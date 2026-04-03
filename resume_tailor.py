"""
resume_tailor.py
────────────────
For every new job in the Google Sheet (status = "New", tailored_resume = ""),
this script:
  1. Calls Groq (llama-3.1-8b-instant, free) to tailor the base resume to the job
  2. Generates a properly formatted PDF with reportlab (Times New Roman, exact layout)
  3. Sends the PDF to Telegram as a document
  4. Marks the row tailored_resume = "sent" in the sheet

Secrets needed (already in your repo):
  GROQ_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GOOGLE_CREDS_JSON

One new column is added to the sheet automatically: tailored_resume
"""

import os
import io
import json
import logging
import sys
import time
import re
import tempfile
import requests
import gspread
from google.oauth2.service_account import Credentials
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, HRFlowable, KeepTogether
)
from reportlab.lib import colors

# ─────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Config (from environment / GitHub secrets)
# ─────────────────────────────────────────────────────────
GROQ_API_KEY       = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN     = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_CREDS_JSON  = os.environ["GOOGLE_CREDS_JSON"]
SHEET_NAME         = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
WORKSHEET_NAME     = os.environ.get("WORKSHEET_NAME", "Sheet1")
GROQ_MODEL         = "llama-3.1-8b-instant"
MAX_JOBS_PER_RUN   = 10   # stay well inside Groq free tier (14400 RPD)
TAILORED_COL       = "tailored_resume"

# ─────────────────────────────────────────────────────────
# Your base resume (hardcoded — update this if you change your resume)
# ─────────────────────────────────────────────────────────
BASE_RESUME = """
AKSHAY P
+91 7483473945 | akshayp7841@gmail.com | LinkedIn | Portfolio | GitHub

EDUCATION
Presidency College | Master of Computer Applications (MCA) · CGPA 7.29 | 2021–2023 | Bengaluru
St. Claret College | Bachelor of Computer Applications (BCA) · CGPA 7.21 | 2018–2021 | Bengaluru
St. Claret PU College | Higher Secondary (12th) | 2016–2018 | Bengaluru

WORK EXPERIENCE
Support Operations Specialist | Amazon FBA | Jun 2024 – Jan 2026 | Bengaluru, India
- Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring structured alert triage and escalation workflows used in SOC Tier 1 analyst roles
- Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated incidents to senior reviewers, demonstrating investigative instincts central to SOC operations
- Maintained audit-ready case documentation recording all investigation findings, decisions, and corrective actions — establishing the evidence chain-of-custody discipline required for security incident reporting
- Monitored transaction queues for anomalous activity patterns, applying risk-based prioritisation consistent with fraud detection and AML investigation workflows
- Enforced policy compliance across 200+ monthly cases, identifying recurring violation patterns and contributing to process improvement recommendations aligned with GRC audit practices

PROJECTS
Cybersecurity Home Lab | Wireshark, Nmap, Splunk, Burp Suite, Linux, TryHackMe
- Captured and analysed TCP/IP traffic in Wireshark — isolated HTTP, DNS, and handshake traffic, observed plaintext credential exposure on unencrypted sessions, and applied filters to detect SYN scans and DNS tunnelling patterns
- Conducted network reconnaissance with Nmap (host discovery, service-version detection, OS fingerprinting) and performed manual SQL injection on testphp.vulnweb.com; documented parameterised query remediation per OWASP Top 10
- Deployed Splunk, built SPL correlation search for brute-force detection; wrote PICERL incident report with MITRE ATT&CK TTP mapping (T1110, T1078, T1059); completed 9 TryHackMe rooms

Automated Vulnerability Scanning and Reporting Tool | Python, Bash, Nessus, OpenVAS, Git
- Built automated scanning pipeline integrating Nessus and OpenVAS APIs for scheduled network security assessments; generated CVE reports summarising findings by CVSS score with remediation guidance
- Automated scan scheduling via Bash scripting, reducing manual workflow overhead and demonstrating practical security automation ability

TECHNICAL SKILLS
Networking: TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS
OS & Scripting: Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell (basic), Python, Bash
Risk & Investigation: Root cause analysis, policy-based case evaluation, audit documentation
SIEM & Tools: Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap
SOC: Alert triage, log analysis, threat detection, incident escalation, endpoint security, ticketing workflows
Frameworks: MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10

CERTIFICATIONS
- CompTIA Security+ SY0-701 — in progress, exam target Q3 2026
- Cisco Networking Academy — Introduction to Networking · Introduction to Cybersecurity
""".strip()

# ─────────────────────────────────────────────────────────
# Groq: tailor resume to job
# ─────────────────────────────────────────────────────────
TAILOR_SYSTEM = """You are an ATS resume writer for entry-level cybersecurity roles. Given a base resume and a job, return ONLY valid JSON — no markdown, no explanation:

{
  "name": "Akshay P",
  "contact": "+91 7483473945 | akshayp7841@gmail.com | LinkedIn | Portfolio | GitHub",
  "education": [
    {"school": "Presidency College", "degree": "Master of Computer Applications (MCA) · CGPA 7.29", "period": "2021–2023", "location": "Bengaluru, India"},
    {"school": "St. Claret College", "degree": "Bachelor of Computer Applications (BCA) · CGPA 7.21", "period": "2018–2021", "location": "Bengaluru, India"},
    {"school": "St. Claret PU College", "degree": "Higher Secondary (12th)", "period": "2016–2018", "location": "Bengaluru, India"}
  ],
  "experience": [
    {
      "title": "Support Operations Specialist",
      "company": "Amazon FBA",
      "period": "Jun 2024 – Jan 2026",
      "location": "Bengaluru, India",
      "bullets": ["reworded bullet with JD keywords", "...up to 5 bullets"]
    }
  ],
  "projects": [
    {"name": "...", "stack": "...", "bullets": ["...", "..."]},
    {"name": "...", "stack": "...", "bullets": ["...", "..."]}
  ],
  "skills": [
    {"label": "Networking", "value": "..."},
    {"label": "OS & Scripting", "value": "..."},
    {"label": "SIEM & Tools", "value": "..."},
    {"label": "SOC", "value": "..."},
    {"label": "Frameworks", "value": "..."}
  ],
  "certifications": [
    "CompTIA Security+ SY0-701 — in progress, exam target Q3 2026",
    "Cisco Networking Academy — Introduction to Networking · Introduction to Cybersecurity"
  ]
}

Rules:
1. Reword Amazon bullets to front-load JD keywords — never fabricate tools or experience
2. Put the most JD-relevant project first (keep exactly 2 projects)
3. Match skills section to exact JD terminology (acronyms, tool names, framework names)
4. Return ONLY the JSON object, nothing else"""


def call_groq(job_title: str, company: str, skills: str, summary: str) -> dict:
    """Call Groq with exponential backoff. Returns parsed JSON resume dict."""
    prompt = (
        f"BASE RESUME:\n{BASE_RESUME}\n\n"
        f"JOB TITLE: {job_title}\n"
        f"COMPANY: {company}\n"
        f"REQUIRED SKILLS: {skills}\n"
        f"JOB SUMMARY: {summary}\n\n"
        "Tailor the resume for this exact job."
    )
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": TAILOR_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    for attempt in range(1, 5):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers, json=payload, timeout=30,
            )
            if resp.status_code == 429:
                wait = 2 ** attempt * 10
                logger.warning("Groq 429 — waiting %ds (attempt %d)", wait, attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
            raw = re.sub(r"```\s*$", "", raw).strip()
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("JSON parse error on attempt %d: %s", attempt, e)
            if attempt == 4:
                raise
            time.sleep(5)
        except requests.RequestException as e:
            logger.error("Request error on attempt %d: %s", attempt, e)
            if attempt == 4:
                raise
            time.sleep(5 * attempt)
    raise RuntimeError("Groq failed after 4 attempts")


# ─────────────────────────────────────────────────────────
# reportlab: generate PDF matching your resume format
# ─────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4            # 595 x 842 pt
MARGIN_LR      = 0.65 * inch
MARGIN_TB      = 0.55 * inch
USABLE_W       = PAGE_W - 2 * MARGIN_LR


def _style(name, **kwargs):
    defaults = dict(fontName="Times-Roman", fontSize=10.5, leading=14,
                    textColor=colors.black, spaceAfter=0, spaceBefore=0)
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)


# Style library — mirrors your original PDF exactly
S = {
    "name":    _style("name",    fontName="Times-Bold", fontSize=18,
                      alignment=TA_CENTER, leading=22, spaceAfter=3),
    "contact": _style("contact", fontSize=9.5, alignment=TA_CENTER,
                      leading=13, spaceAfter=4),
    "sec":     _style("sec",     fontName="Times-Bold", fontSize=10.5,
                      leading=13, spaceBefore=8, spaceAfter=2),
    "jobtitle":_style("jobtitle",fontName="Times-Bold", fontSize=10.5, leading=13),
    "jobmeta": _style("jobmeta", fontSize=10, leading=12, spaceAfter=2,
                      textColor=colors.HexColor("#333333")),
    "bullet":  _style("bullet",  fontSize=10, leading=13.5, leftIndent=10,
                      firstLineIndent=0, spaceAfter=1),
    "projname":_style("projname",fontName="Times-Bold", fontSize=10.5,
                      leading=13, spaceBefore=5, spaceAfter=2),
    "skill":   _style("skill",   fontSize=10, leading=13),
    "cert":    _style("cert",    fontSize=10, leading=13),
}


def _hr():
    """Thin black line under section headers."""
    return HRFlowable(width="100%", thickness=0.75, color=colors.black,
                      spaceAfter=3, spaceBefore=0)


def _section(title: str) -> list:
    return [Paragraph(title.upper(), S["sec"]), _hr()]


def _bullets(items: list) -> list:
    out = []
    for b in items:
        text = b.lstrip("•-– ").strip()
        out.append(Paragraph(f"• {text}", S["bullet"]))
    return out


def build_pdf(data: dict) -> bytes:
    """
    Build a PDF from the tailored resume JSON and return raw bytes.
    Layout matches the original Akshay_P_Resume_april.pdf exactly.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN_LR, rightMargin=MARGIN_LR,
        topMargin=MARGIN_TB,  bottomMargin=MARGIN_TB,
    )
    story = []

    # ── Header ──────────────────────────────────────────
    story.append(Paragraph(data.get("name", "Akshay P"), S["name"]))
    story.append(Paragraph(data.get("contact", ""), S["contact"]))
    story.append(Spacer(1, 2))

    # ── Education ───────────────────────────────────────
    story += _section("Education")
    for edu in data.get("education", []):
        school  = edu.get("school", "")
        degree  = edu.get("degree", "")
        period  = edu.get("period", "")
        loc     = edu.get("location", "")
        # Bold school name with date on same line (right-aligned via tab trick)
        story.append(Paragraph(
            f'<b>{school}</b><font size="10">&nbsp;&nbsp;&nbsp;{period}</font>',
            S["jobtitle"]
        ))
        story.append(Paragraph(f"{degree} · {loc}", S["jobmeta"]))

    # ── Work Experience ─────────────────────────────────
    story += _section("Work Experience")
    for exp in data.get("experience", []):
        block = []
        block.append(Paragraph(
            f'<b>{exp.get("title","")}</b>'
            f'<font size="10">  {exp.get("period","")}</font>',
            S["jobtitle"]
        ))
        block.append(Paragraph(
            f'{exp.get("company","")} · {exp.get("location","")}',
            S["jobmeta"]
        ))
        block += _bullets(exp.get("bullets", []))
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3))

    # ── Projects ────────────────────────────────────────
    story += _section("Projects")
    for proj in data.get("projects", []):
        block = []
        block.append(Paragraph(
            f'<b>{proj.get("name","")} | {proj.get("stack","")}</b>',
            S["projname"]
        ))
        block += _bullets(proj.get("bullets", []))
        story.append(KeepTogether(block))
        story.append(Spacer(1, 3))

    # ── Technical Skills ────────────────────────────────
    story += _section("Technical Skills")
    for sk in data.get("skills", []):
        story.append(Paragraph(
            f'<b>{sk.get("label","")}:</b> {sk.get("value","")}',
            S["skill"]
        ))

    # ── Certifications ──────────────────────────────────
    story += _section("Certifications")
    for cert in data.get("certifications", []):
        story.append(Paragraph(f"• {cert}", S["cert"]))

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────
# Telegram: send PDF document
# ─────────────────────────────────────────────────────────
def send_pdf_telegram(pdf_bytes: bytes, filename: str, caption: str) -> bool:
    """Send a PDF file to Telegram. Returns True on success."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
            files={"document": (filename, pdf_bytes, "application/pdf")},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error("Telegram send failed: %s", e)
        return False


# ─────────────────────────────────────────────────────────
# Google Sheets: connect and manage tailored_resume column
# ─────────────────────────────────────────────────────────
def get_worksheet():
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_CREDS_JSON is not valid JSON: {e}") from e

    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    gc = gspread.Client(auth=creds)
    try:
        sh = gc.open(SHEET_NAME)
    except gspread.exceptions.SpreadsheetNotFound:
        raise RuntimeError(
            f"Spreadsheet '{SHEET_NAME}' not found. "
            "Check SHEET_NAME secret matches the exact title of your Google Sheet, "
            "and that the service account email has been shared on the sheet."
        )
    try:
        return sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        available = [w.title for w in sh.worksheets()]
        raise RuntimeError(
            f"Worksheet '{WORKSHEET_NAME}' not found in '{SHEET_NAME}'. "
            f"Available tabs: {available}"
        )


def ensure_tailored_column(ws) -> int:
    """
    Ensure the 'tailored_resume' column exists.
    Returns the 1-based column index of that column.
    """
    headers = ws.row_values(1)
    if TAILORED_COL in headers:
        return headers.index(TAILORED_COL) + 1  # 1-based

    # Not found — append it
    new_col = len(headers) + 1
    ws.update_cell(1, new_col, TAILORED_COL)
    logger.info("Added '%s' column at position %d", TAILORED_COL, new_col)
    return new_col


def get_pending_jobs(ws, tailored_col_idx: int) -> list[dict]:
    """
    Return rows where status = "New" and tailored_resume is empty.
    Each dict includes the row number (1-based) and all column values.
    """
    all_rows  = ws.get_all_values()
    if len(all_rows) < 2:
        return []

    headers = all_rows[0]
    col = {h: i for i, h in enumerate(headers)}

    status_idx   = col.get("status", -1)
    tailored_idx = tailored_col_idx - 1  # 0-based

    pending = []
    for row_num, row in enumerate(all_rows[1:], start=2):
        # Pad short rows
        while len(row) <= max(status_idx, tailored_idx):
            row.append("")

        status   = row[status_idx].strip().lower()  if status_idx >= 0 else ""
        tailored = row[tailored_idx].strip().lower()

        if status == "new" and tailored == "":
            entry = {"_row": row_num}
            for h, i in col.items():
                entry[h] = row[i] if i < len(row) else ""
            pending.append(entry)

    return pending


def mark_sent(ws, row_num: int, tailored_col_idx: int):
    ws.update_cell(row_num, tailored_col_idx, "sent")


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("Resume Tailor started")
    logger.info("=" * 60)

    # Connect to sheet
    try:
        ws = get_worksheet()
        logger.info("Connected to sheet: %s / %s", SHEET_NAME, WORKSHEET_NAME)
    except Exception as e:
        logger.error("Cannot connect to Google Sheets: %s", e)
        sys.exit(1)

    tailored_col = ensure_tailored_column(ws)
    pending      = get_pending_jobs(ws, tailored_col)

    if not pending:
        logger.info("No pending jobs to tailor. Exiting.")
        sys.exit(0)

    logger.info("Found %d pending job(s). Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    jobs_to_process = pending[:MAX_JOBS_PER_RUN]

    ok = 0
    for job in jobs_to_process:
        job_title = job.get("job_title", "Unknown Role")
        company   = job.get("company",   "Unknown Company")
        skills    = job.get("skills_required", "")
        summary   = job.get("summary", "")
        url       = job.get("apply_url") or job.get("url", "")
        row_num   = job["_row"]

        logger.info("Tailoring [row %d]: %s @ %s", row_num, job_title, company)

        # 1. Tailor resume via Groq
        try:
            resume_data = call_groq(job_title, company, skills, summary)
        except Exception as e:
            logger.error("  Groq failed for '%s': %s — skipping", job_title, e)
            continue

        # 2. Build PDF
        try:
            pdf_bytes = build_pdf(resume_data)
            logger.info("  PDF generated (%d bytes)", len(pdf_bytes))
        except Exception as e:
            logger.error("  PDF build failed: %s — skipping", e)
            continue

        # 3. Send to Telegram
        safe_title   = re.sub(r"[^\w\s-]", "", job_title)[:40].strip().replace(" ", "_")
        safe_company = re.sub(r"[^\w\s-]", "", company)[:20].strip().replace(" ", "_")
        filename     = f"Resume_{safe_title}_{safe_company}.pdf"
        caption = (
            f"📄 <b>Tailored resume ready</b>\n"
            f"Role: <b>{job_title}</b>\n"
            f"Company: {company}\n"
            + (f'<a href="{url}">View job listing</a>' if url else "")
        )
        sent = send_pdf_telegram(pdf_bytes, filename, caption)
        if sent:
            logger.info("  Sent to Telegram: %s", filename)
        else:
            logger.warning("  Telegram send failed — still marking as processed")

        # 4. Mark row as done
        mark_sent(ws, row_num, tailored_col)
        ok += 1

        # Respect Groq rate limits — 6 seconds between calls
        if job != jobs_to_process[-1]:
            time.sleep(6)

    logger.info("=" * 60)
    logger.info("Done. Tailored %d / %d job(s).", ok, len(jobs_to_process))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
