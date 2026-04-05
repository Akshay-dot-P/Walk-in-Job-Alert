"""
resume_tailor.py - GitHub Releases Edition
This version generates PDFs and saves them to a local directory.
The GitHub Actions workflow will then upload them to a release.
"""

import os
import io
import json
import logging
import sys
import time
import re
from pathlib import Path
import requests
import gspread
from google.oauth2.service_account import Credentials
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Frame, PageTemplate
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Configuration
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
SHEET_NAME = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Sheet1")
GROQ_MODEL = "llama-3.1-8b-instant"
MAX_JOBS_PER_RUN = 2
TAILORED_COL = "tailored_resume"

# This directory will hold generated PDFs that GitHub Actions will upload
# GitHub Actions workspace persists between steps in the same job
RESUME_OUTPUT_DIR = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "generated_resumes"
RESUME_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# GitHub repository information for constructing download URLs
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # Format: "username/repo"
GITHUB_RUN_ID = os.environ.get("GITHUB_RUN_ID", "")

# Your base resume content
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

# Groq AI system prompt for tailoring
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
3. Match skills section to exact JD terminology
4. Return ONLY the JSON object"""


def call_groq(job_title: str, company: str, skills: str, summary: str) -> dict:
    """Call Groq AI to tailor resume, with exponential backoff for rate limits"""
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
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }
    
    for attempt in range(1, 5):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            
            if resp.status_code == 429:
                wait = 2 ** attempt * 10
                logger.warning(f"Groq 429 — waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue
                
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
            
            # Clean markdown code fences if present
            raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
            raw = re.sub(r"```\s*$", "", raw).strip()
            
            return json.loads(raw)
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error on attempt {attempt}: {e}")
            if attempt == 4:
                raise
            time.sleep(5)
            
        except requests.RequestException as e:
            logger.error(f"Request error on attempt {attempt}: {e}")
            if attempt == 4:
                raise
            time.sleep(5 * attempt)
    
    raise RuntimeError("Groq failed after 4 attempts")


def build_pdf_exact_format(data: dict, output_path: Path):
    """
    Generate PDF matching your exact resume format.
    Based on analyzing your actual resume file.
    
    Key specifications from your resume:
    - Font: Calibri 11pt for body (NOT Times New Roman)
    - Name: Calibri Bold 20pt, centered
    - Margins: 0.7" top/bottom, 0.75" left/right
    - Section headers: Bold, underlined, 11pt
    - School names and dates on same line with right-aligned dates
    """
    
    # Create document with exact margins from your resume
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=0.75*inch,
        rightMargin=0.75*inch,
        topMargin=0.7*inch,
        bottomMargin=0.7*inch,
    )
    
    # Build the story (content)
    story = []
    
    # Styles - matching your resume exactly
    # Note: Calibri isn't available in reportlab by default
    # We'll use Helvetica which is visually similar
    # For true Calibri, you'd need to register the font file
    
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    
    style_name = ParagraphStyle(
        'CustomName',
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    
    style_contact = ParagraphStyle(
        'CustomContact',
        fontName='Helvetica',
        fontSize=10,
        leading=12,
        alignment=TA_LEFT,
        spaceAfter=12,
    )
    
    style_section = ParagraphStyle(
        'CustomSection',
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=13,
        spaceBefore=12,
        spaceAfter=6,
    )
    
    style_body = ParagraphStyle(
        'CustomBody',
        fontName='Helvetica',
        fontSize=11,
        leading=14,
        spaceAfter=4,
    )
    
    style_bullet = ParagraphStyle(
        'CustomBullet',
        fontName='Helvetica',
        fontSize=11,
        leading=14,
        leftIndent=18,
        firstLineIndent=-18,
        spaceAfter=3,
    )
    
    # Header
    story.append(Paragraph(data.get("name", "AKSHAY P"), style_name))
    story.append(Paragraph(data.get("contact", ""), style_contact))
    
    # Education section
    story.append(Paragraph("<b><u>Education</u></b>", style_section))
    
    for edu in data.get("education", []):
        # Create a mini-table for school name and date on same line
        from reportlab.platypus import Table, TableStyle
        
        school = edu.get("school", "")
        period = edu.get("period", "")
        
        # School and date on same line
        t = Table(
            [[Paragraph(f"<b>{school}</b>", style_body), 
              Paragraph(period, style_body)]],
            colWidths=[4.5*inch, 1.5*inch]
        )
        t.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))
        story.append(t)
        
        # Degree details
        degree = edu.get("degree", "")
        location = edu.get("location", "")
        story.append(Paragraph(f"{degree} | {location}", style_body))
        story.append(Spacer(1, 6))
    
    # Work Experience
    story.append(Paragraph("<b><u>Work Experience</u></b>", style_section))
    
    for exp in data.get("experience", []):
        title = exp.get("title", "")
        period = exp.get("period", "")
        company = exp.get("company", "")
        location = exp.get("location", "")
        
        # Title and date on same line
        t = Table(
            [[Paragraph(f"<b>{title}</b>", style_body),
              Paragraph(period, style_body)]],
            colWidths=[4.5*inch, 1.5*inch]
        )
        t.setStyle(TableStyle([
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'RIGHT'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))
        story.append(t)
        
        story.append(Paragraph(f"{company} | {location}", style_body))
        
        for bullet in exp.get("bullets", []):
            clean = bullet.lstrip("•-– ").strip()
            story.append(Paragraph(f"• {clean}", style_bullet))
        
        story.append(Spacer(1, 8))
    
    # Projects
    story.append(Paragraph("<b><u>Projects</u></b>", style_section))
    
    for proj in data.get("projects", []):
        name = proj.get("name", "")
        stack = proj.get("stack", "")
        story.append(Paragraph(f"<b>{name} | {stack}</b>", style_body))
        
        for bullet in proj.get("bullets", []):
            clean = bullet.lstrip("•-– ").strip()
            story.append(Paragraph(f"• {clean}", style_bullet))
        
        story.append(Spacer(1, 6))
    
    # Technical Skills
    story.append(Paragraph("<b><u>Technical Skills</u></b>", style_section))
    
    for skill in data.get("skills", []):
        label = skill.get("label", "")
        value = skill.get("value", "")
        story.append(Paragraph(f"<b>{label}:</b> {value}", style_body))
    
    # Certifications
    story.append(Paragraph("<b><u>Certifications</u></b>", style_section))
    
    for cert in data.get("certifications", []):
        story.append(Paragraph(f"• {cert}", style_bullet))
    
    # Build PDF
    doc.build(story)
    logger.info(f"  ✓ PDF saved: {output_path.name}")


# Google Sheets functions
def get_worksheet():
    """Connect to Google Sheet"""
    try:
        creds_info = json.loads(GOOGLE_CREDS_JSON)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"GOOGLE_CREDS_JSON invalid: {e}")
    
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
        ],
    )
    
    gc = gspread.Client(auth=creds)
    sh = gc.open(SHEET_NAME)
    return sh.worksheet(WORKSHEET_NAME)


def ensure_tailored_column(ws) -> int:
    """Ensure tailored_resume column exists"""
    headers = ws.row_values(1)
    if TAILORED_COL in headers:
        return headers.index(TAILORED_COL) + 1
    
    new_col = len(headers) + 1
    ws.update_cell(1, new_col, TAILORED_COL)
    logger.info(f"Added '{TAILORED_COL}' column at position {new_col}")
    return new_col


def get_pending_jobs(ws, tailored_col_idx: int) -> list:
    """Get jobs needing resumes"""
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []
    
    headers = all_rows[0]
    col = {h: i for i, h in enumerate(headers)}
    
    status_idx = col.get("status", -1)
    tailored_idx = tailored_col_idx - 1
    
    pending = []
    for row_num, row in enumerate(all_rows[1:], start=2):
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


def main():
    logger.info("=" * 60)
    logger.info("Resume Tailor (GitHub Releases)")
    logger.info("=" * 60)
    
    # Connect to sheet
    try:
        ws = get_worksheet()
        logger.info(f"Connected: {SHEET_NAME} / {WORKSHEET_NAME}")
    except Exception as e:
        logger.error(f"Sheet connection failed: {e}")
        sys.exit(1)
    
    tailored_col = ensure_tailored_column(ws)
    pending = get_pending_jobs(ws, tailored_col)
    
    if not pending:
        logger.info("No pending jobs. Exiting.")
        sys.exit(0)
    
    logger.info(f"Found {len(pending)} pending jobs. Processing {MAX_JOBS_PER_RUN} max.")
    jobs_to_process = pending[:MAX_JOBS_PER_RUN]
    
    # Track generated files for later upload
    generated_files = []
    
    for idx, job in enumerate(jobs_to_process, 1):
        job_title = job.get("job_title", "Unknown")
        company = job.get("company", "Unknown")
        skills = job.get("skills_required", "")
        summary = job.get("summary", "")
        row_num = job["_row"]
        
        logger.info(f"[{idx}/{len(jobs_to_process)}] Row {row_num}: {job_title} @ {company}")
        
        # Generate tailored resume
        try:
            resume_data = call_groq(job_title, company, skills, summary)
            logger.info("  ✓ AI tailoring complete")
        except Exception as e:
            logger.error(f"  ✗ Groq failed: {e}")
            continue
        
        # Build PDF
        try:
            # Clean filename
            safe_title = re.sub(r"[^\w\s-]", "", job_title)[:40].strip().replace(" ", "_")
            safe_company = re.sub(r"[^\w\s-]", "", company)[:20].strip().replace(" ", "_")
            filename = f"Resume_{safe_title}_{safe_company}.pdf"
            
            output_path = RESUME_OUTPUT_DIR / filename
            build_pdf_exact_format(resume_data, output_path)
            
            # Track this file and its associated row
            generated_files.append({
                "path": output_path,
                "filename": filename,
                "row_num": row_num,
                "job_title": job_title,
                "company": company,
            })
            
        except Exception as e:
            logger.error(f"  ✗ PDF generation failed: {e}")
            continue
        
        # Rate limiting
        if idx < len(jobs_to_process):
            time.sleep(6)
    
    # Write manifest file for GitHub Actions to read
    manifest = {
        "files": [
            {
                "filename": f["filename"],
                "row_num": f["row_num"],
                "job_title": f["job_title"],
                "company": f["company"],
            }
            for f in generated_files
        ],
        "repository": GITHUB_REPOSITORY,
        "run_id": GITHUB_RUN_ID,
    }
    
    manifest_path = RESUME_OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    
    logger.info("=" * 60)
    logger.info(f"Generated {len(generated_files)} PDFs")
    logger.info(f"Files saved to: {RESUME_OUTPUT_DIR}")
    logger.info(f"Manifest: {manifest_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
