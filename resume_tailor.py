"""
resume_tailor.py (UPDATED)
───────────────────────────
For every new job in the Google Sheet (status = "New", tailored_resume = ""),
this script:
  1. Calls Groq (llama-3.1-8b-instant, free) to tailor the base resume to the job
  2. Generates a properly formatted PDF with reportlab matching EXACT original layout
  3. Uploads PDF to Google Drive
  4. Stores shareable Drive link in the 'tailored_resume' column

Secrets needed (already in your repo):
  GROQ_API_KEY, GOOGLE_CREDS_JSON

The tailored_resume column is added automatically if missing.
"""

import os
import io
import json
import logging
import sys
import time
import re
import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

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
GOOGLE_CREDS_JSON  = os.environ["GOOGLE_CREDS_JSON"]
SHEET_NAME         = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
WORKSHEET_NAME     = os.environ.get("WORKSHEET_NAME", "Sheet1")
GROQ_MODEL         = "llama-3.1-8b-instant"
MAX_JOBS_PER_RUN   = 10
TAILORED_COL       = "tailored_resume"

# Google Drive folder name where resumes will be stored
DRIVE_FOLDER_NAME  = "Tailored Resumes - Cybersecurity Jobs"

# ─────────────────────────────────────────────────────────
# Your base resume
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
    {"school": "Presidency College", "degree": "Master of Computer Applications (MCA) · CGPA 7.29", "period": "2021–2023", "location": "Bengaluru"},
    {"school": "St. Claret College", "degree": "Bachelor of Computer Applications (BCA) · CGPA 7.21", "period": "2018–2021", "location": "Bengaluru"},
    {"school": "St. Claret PU College", "degree": "Higher Secondary (12th)", "period": "2016–2018", "location": "Bengaluru"}
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
    {"label": "Risk & Investigation", "value": "..."},
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
4. Keep all 6 skill categories (Networking, OS & Scripting, Risk & Investigation, SIEM & Tools, SOC, Frameworks)
5. Return ONLY the JSON object, nothing else"""


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
# reportlab: EXACT formatting to match original PDF
# ─────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGIN_LR      = 0.75 * inch    # Left/Right margins
MARGIN_TB      = 0.75 * inch    # Top/Bottom margins
USABLE_W       = PAGE_W - 2 * MARGIN_LR


def _style(name, **kwargs):
    """Create paragraph style with Times-Roman as base font"""
    defaults = dict(
        fontName="Times-Roman",
        fontSize=11,
        leading=13,
        textColor=colors.black,
        spaceAfter=0,
        spaceBefore=0,
        alignment=TA_LEFT
    )
    defaults.update(kwargs)
    return ParagraphStyle(name, **defaults)


# Style definitions matching your original resume exactly
S = {
    # Name: centered, bold, 18pt, extra space after
    "name": _style("name",
                   fontName="Times-Bold",
                   fontSize=18,
                   leading=22,
                   alignment=TA_CENTER,
                   spaceAfter=6),
    
    # Contact: centered, 10pt, space after
    "contact": _style("contact",
                      fontSize=10,
                      leading=12,
                      alignment=TA_CENTER,
                      spaceAfter=10),
    
    # Section headers: bold, all caps, 11pt, underlined, space before and after
    "section": _style("section",
                      fontName="Times-Bold",
                      fontSize=11,
                      leading=13,
                      spaceBefore=12,
                      spaceAfter=3),
    
    # School/Job title: bold, 11pt
    "title": _style("title",
                    fontName="Times-Bold",
                    fontSize=11,
                    leading=13),
    
    # Degree/Company line: regular, 11pt, small space after
    "subtitle": _style("subtitle",
                       fontSize=11,
                       leading=13,
                       spaceAfter=4),
    
    # Bullets: 11pt with left indent, proper spacing
    "bullet": _style("bullet",
                     fontSize=11,
                     leading=14,
                     leftIndent=18,
                     firstLineIndent=-18,
                     spaceAfter=2),
    
    # Project name: bold, 11pt, space before
    "project": _style("project",
                      fontName="Times-Bold",
                      fontSize=11,
                      leading=13,
                      spaceBefore=4,
                      spaceAfter=3),
    
    # Skills: 11pt, small space after
    "skill": _style("skill",
                    fontSize=11,
                    leading=13,
                    spaceAfter=3),
}


def _section_header(title: str) -> list:
    """Create section header with underline"""
    return [
        Paragraph(f'<b><u>{title.upper()}</u></b>', S["section"]),
        Spacer(1, 2)
    ]


def _bullet_point(text: str) -> Paragraph:
    """Format a single bullet point"""
    clean_text = text.lstrip("•-– ").strip()
    return Paragraph(f"• {clean_text}", S["bullet"])


def build_pdf(data: dict) -> bytes:
    """
    Build PDF matching the EXACT layout of Akshay_P_Resume_april.pdf
    
    Key formatting details:
    - Times New Roman font throughout
    - 11pt base font size
    - 0.75" margins all around
    - Name centered, 18pt bold
    - Section headers bold, underlined, all caps
    - Proper spacing between sections
    - Bullets with hanging indent
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN_LR,
        rightMargin=MARGIN_LR,
        topMargin=MARGIN_TB,
        bottomMargin=MARGIN_TB,
    )
    story = []

    # ══════════════════════════════════════════════════════
    # HEADER (Name + Contact)
    # ══════════════════════════════════════════════════════
    story.append(Paragraph(data.get("name", "AKSHAY P"), S["name"]))
    story.append(Paragraph(data.get("contact", ""), S["contact"]))

    # ══════════════════════════════════════════════════════
    # EDUCATION
    # ══════════════════════════════════════════════════════
    story.extend(_section_header("Education"))
    
    for edu in data.get("education", []):
        school = edu.get("school", "")
        degree = edu.get("degree", "")
        period = edu.get("period", "")
        location = edu.get("location", "")
        
        # School name (bold) | Period (right-aligned via table)
        school_row = Table(
            [[Paragraph(f"<b>{school}</b>", S["title"]),
              Paragraph(f"{period}", S["title"])]],
            colWidths=[4.5*inch, 1.5*inch]
        )
        school_row.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        
        story.append(school_row)
        story.append(Paragraph(f"{degree} | {location}", S["subtitle"]))
        story.append(Spacer(1, 2))

    # ══════════════════════════════════════════════════════
    # WORK EXPERIENCE
    # ══════════════════════════════════════════════════════
    story.extend(_section_header("Work Experience"))
    
    for exp in data.get("experience", []):
        title = exp.get("title", "")
        company = exp.get("company", "")
        period = exp.get("period", "")
        location = exp.get("location", "")
        bullets = exp.get("bullets", [])
        
        # Job title | Period (right-aligned)
        title_row = Table(
            [[Paragraph(f"<b>{title}</b>", S["title"]),
              Paragraph(f"{period}", S["title"])]],
            colWidths=[4.5*inch, 1.5*inch]
        )
        title_row.setStyle(TableStyle([
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        
        story.append(title_row)
        story.append(Paragraph(f"{company} | {location}", S["subtitle"]))
        
        # Bullets
        for bullet in bullets:
            story.append(_bullet_point(bullet))
        
        story.append(Spacer(1, 4))

    # ══════════════════════════════════════════════════════
    # PROJECTS
    # ══════════════════════════════════════════════════════
    story.extend(_section_header("Projects"))
    
    for proj in data.get("projects", []):
        name = proj.get("name", "")
        stack = proj.get("stack", "")
        bullets = proj.get("bullets", [])
        
        story.append(Paragraph(f"<b>{name} | {stack}</b>", S["project"]))
        
        for bullet in bullets:
            story.append(_bullet_point(bullet))
        
        story.append(Spacer(1, 3))

    # ══════════════════════════════════════════════════════
    # TECHNICAL SKILLS
    # ══════════════════════════════════════════════════════
    story.extend(_section_header("Technical Skills"))
    
    for skill in data.get("skills", []):
        label = skill.get("label", "")
        value = skill.get("value", "")
        story.append(Paragraph(f"<b>{label}:</b> {value}", S["skill"]))

    # ══════════════════════════════════════════════════════
    # CERTIFICATIONS
    # ══════════════════════════════════════════════════════
    story.extend(_section_header("Certifications"))
    
    for cert in data.get("certifications", []):
        story.append(_bullet_point(cert))

    # Build PDF
    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────
# Google Drive: Upload PDF and get shareable link
# ─────────────────────────────────────────────────────────
def get_or_create_folder(drive_service, folder_name: str) -> str:
    """
    Get or create a Google Drive folder.
    Returns the folder ID.
    """
    # Search for existing folder
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name)'
    ).execute()
    
    folders = results.get('files', [])
    
    if folders:
        logger.info(f"Found existing folder '{folder_name}': {folders[0]['id']}")
        return folders[0]['id']
    
    # Create new folder
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    folder = drive_service.files().create(
        body=file_metadata,
        fields='id'
    ).execute()
    
    logger.info(f"Created new folder '{folder_name}': {folder['id']}")
    return folder['id']


def upload_to_drive(pdf_bytes: bytes, filename: str, creds) -> str:
    """
    Upload PDF to Google Drive and return shareable link.
    
    Args:
        pdf_bytes: PDF file content
        filename: Name for the file
        creds: Google credentials object
    
    Returns:
        Shareable Google Drive link
    """
    try:
        drive_service = build('drive', 'v3', credentials=creds)
        
        # Get or create folder
        folder_id = get_or_create_folder(drive_service, DRIVE_FOLDER_NAME)
        
        # Upload file
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        media = MediaIoBaseUpload(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            resumable=True
        )
        
        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        # Make file publicly accessible (anyone with link can view)
        drive_service.permissions().create(
            fileId=file['id'],
            body={
                'type': 'anyone',
                'role': 'reader'
            }
        ).execute()
        
        link = file.get('webViewLink', '')
        logger.info(f"  Uploaded to Drive: {filename}")
        logger.info(f"  Link: {link}")
        
        return link
        
    except Exception as e:
        logger.error(f"Drive upload failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────
# Google Sheets: Connect and manage
# ─────────────────────────────────────────────────────────
def get_credentials():
    """Get Google credentials for both Sheets and Drive"""
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_CREDS_JSON is not valid JSON: {e}") from e

    return Credentials.from_service_account_info(
        creds_info,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )


def get_worksheet(creds):
    """Get the worksheet object"""
    gc = gspread.Client(auth=creds)
    
    try:
        sh = gc.open(SHEET_NAME)
    except gspread.exceptions.SpreadsheetNotFound:
        raise RuntimeError(
            f"Spreadsheet '{SHEET_NAME}' not found. "
            "Check SHEET_NAME and ensure the service account has access."
        )
    
    try:
        return sh.worksheet(WORKSHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        available = [w.title for w in sh.worksheets()]
        raise RuntimeError(
            f"Worksheet '{WORKSHEET_NAME}' not found. Available: {available}"
        )


def ensure_tailored_column(ws) -> int:
    """
    Ensure the 'tailored_resume' column exists.
    Returns the 1-based column index.
    """
    headers = ws.row_values(1)
    if TAILORED_COL in headers:
        return headers.index(TAILORED_COL) + 1

    # Add column
    new_col = len(headers) + 1
    ws.update_cell(1, new_col, TAILORED_COL)
    logger.info(f"Added '{TAILORED_COL}' column at position {new_col}")
    return new_col


def get_pending_jobs(ws, tailored_col_idx: int) -> list:
    """
    Return rows where status = "New" and tailored_resume is empty.
    """
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []

    headers = all_rows[0]
    col = {h: i for i, h in enumerate(headers)}

    status_idx = col.get("status", -1)
    tailored_idx = tailored_col_idx - 1

    pending = []
    for row_num, row in enumerate(all_rows[1:], start=2):
        # Pad short rows
        while len(row) <= max(status_idx, tailored_idx):
            row.append("")

        status = row[status_idx].strip().lower() if status_idx >= 0 else ""
        tailored = row[tailored_idx].strip()

        if status == "new" and tailored == "":
            entry = {"_row": row_num}
            for h, i in col.items():
                entry[h] = row[i] if i < len(row) else ""
            pending.append(entry)

    return pending


def update_resume_link(ws, row_num: int, tailored_col_idx: int, link: str):
    """Update the tailored_resume column with the Drive link"""
    ws.update_cell(row_num, tailored_col_idx, link)


# ─────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────
def main():
    logger.info("=" * 60)
    logger.info("Resume Tailor started (Google Drive version)")
    logger.info("=" * 60)

    # Get credentials
    try:
        creds = get_credentials()
        logger.info("✓ Credentials loaded")
    except Exception as e:
        logger.error(f"Cannot load credentials: {e}")
        sys.exit(1)

    # Connect to sheet
    try:
        ws = get_worksheet(creds)
        logger.info(f"✓ Connected to sheet: {SHEET_NAME} / {WORKSHEET_NAME}")
    except Exception as e:
        logger.error(f"Cannot connect to Google Sheets: {e}")
        sys.exit(1)

    tailored_col = ensure_tailored_column(ws)
    pending = get_pending_jobs(ws, tailored_col)

    if not pending:
        logger.info("No pending jobs to tailor. Exiting.")
        sys.exit(0)

    logger.info(f"Found {len(pending)} pending job(s). Processing up to {MAX_JOBS_PER_RUN}.")
    jobs_to_process = pending[:MAX_JOBS_PER_RUN]

    ok = 0
    for job in jobs_to_process:
        job_title = job.get("job_title", "Unknown Role")
        company = job.get("company", "Unknown Company")
        skills = job.get("skills_required", "")
        summary = job.get("summary", "")
        row_num = job["_row"]

        logger.info(f"[{ok+1}/{len(jobs_to_process)}] Tailoring [row {row_num}]: {job_title} @ {company}")

        # 1. Tailor resume via Groq
        try:
            resume_data = call_groq(job_title, company, skills, summary)
            logger.info("  ✓ AI tailoring complete")
        except Exception as e:
            logger.error(f"  ✗ Groq failed: {e} — skipping")
            continue

        # 2. Build PDF
        try:
            pdf_bytes = build_pdf(resume_data)
            logger.info(f"  ✓ PDF generated ({len(pdf_bytes):,} bytes)")
        except Exception as e:
            logger.error(f"  ✗ PDF build failed: {e} — skipping")
            continue

        # 3. Upload to Drive
        safe_title = re.sub(r"[^\w\s-]", "", job_title)[:40].strip().replace(" ", "_")
        safe_company = re.sub(r"[^\w\s-]", "", company)[:20].strip().replace(" ", "_")
        filename = f"Resume_{safe_title}_{safe_company}.pdf"
        
        try:
            drive_link = upload_to_drive(pdf_bytes, filename, creds)
            if not drive_link:
                logger.error("  ✗ No Drive link returned — skipping")
                continue
            logger.info("  ✓ Uploaded to Drive")
        except Exception as e:
            logger.error(f"  ✗ Drive upload failed: {e} — skipping")
            continue

        # 4. Update sheet with link
        try:
            update_resume_link(ws, row_num, tailored_col, drive_link)
            logger.info(f"  ✓ Sheet updated with Drive link")
            ok += 1
        except Exception as e:
            logger.error(f"  ✗ Sheet update failed: {e}")
            continue

        # Rate limiting
        if job != jobs_to_process[-1]:
            time.sleep(6)

    logger.info("=" * 60)
    logger.info(f"Done. Successfully tailored {ok}/{len(jobs_to_process)} job(s).")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
