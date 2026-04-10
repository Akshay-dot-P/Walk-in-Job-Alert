"""
resume_tailor.py
================
Triggered for every NEW job in the sheet that has no resume yet.

STORAGE: GitHub repo (resumes/ folder) via GitHub Contents API.
  - GITHUB_TOKEN is auto-available in every Actions run — zero new secrets.
  - Download URL is permanent and public.
  - No Drive quota. No Drive API. No Drive permissions to set up.

TRIGGER: status = "New" AND resume_doc_link is empty.
  (Jobs already marked applied/rejected/not_relevant are skipped.)

FORMAT: resume_template.docx is cloned and placeholders are replaced.
  Formatting (font, size, spacing, borders, bullets) is preserved exactly
  because we only swap text inside existing XML nodes.

ADD TO requirements.txt (if not already there):
  python-docx==1.1.2
  beautifulsoup4==4.12.3
  google-api-python-client==2.108.0   ← still needed for Sheets auth only
"""

import os, sys, re, json, time, io, base64, logging, requests, copy
from pathlib import Path
from docx import Document
import gspread
from google.oauth2.service_account import Credentials
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SHEET_NAME       = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
GROQ_API_KEY     = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL       = "llama-3.1-8b-instant"
GROQ_URL         = "https://api.groq.com/openai/v1/chat/completions"
MAX_JOBS_PER_RUN = 6
TEMPLATE_PATH    = Path(__file__).parent / "resume_template.docx"

# GitHub — for storing the generated DOCX files
# GITHUB_TOKEN is injected automatically by Actions; GITHUB_REPOSITORY = "owner/repo"
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")   # e.g. "Akshay-dot-P/Walk-in-Job-Alert"
GITHUB_BRANCH     = os.environ.get("GITHUB_REF_NAME", "main")
RESUMES_FOLDER    = "resumes"   # folder inside the repo where DOCX files are stored

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",   # needed for gspread auth
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence
# Pre-researched framing for major Bangalore hirers. The goal is NOT to add
# a generic "Eager to contribute to X's practice" line. The goal is to make
# the LLM write bullets and the summary using the vocabulary these companies
# actually use in their JDs — based on Reddit, Glassdoor, and their hiring
# patterns. Shapes tone, keyword choice, and skill ordering.
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_INTEL = {
    "wipro": {
        "focus":    "Managed security services for BFSI, manufacturing, and healthcare clients. Wipro CyberDefense runs 24x7 SOC operations.",
        "framing":  "Wipro hires L1 SOC analysts for 24x7 SIEM monitoring shifts. They value process adherence, SLA discipline, and shift documentation rigour. Emphasise structured triage, documentation, and escalation workflows. ITIL awareness is a plus.",
        "keywords": ["SIEM operations", "24x7 SOC", "L1 analyst", "SLA adherence", "shift documentation", "incident escalation"],
        "emphasise_skills": ["Splunk", "SIEM", "alert triage", "incident escalation", "documentation"],
    },
    "tcs": {
        "focus":    "Large enterprise security accounts; TCS Cyber Security practice covers compliance, ISMS, and VA/PT.",
        "framing":  "TCS values structured compliance processes and certification alignment. They frequently hire for ISO 27001-related roles, ISMS auditing, and vulnerability assessments. Emphasise compliance frameworks, audit documentation, and process orientation.",
        "keywords": ["ISMS", "ISO 27001", "compliance audit", "vulnerability assessment", "risk register"],
        "emphasise_skills": ["ISO 27001", "VAPT", "compliance", "audit documentation", "risk assessment"],
    },
    "infosys": {
        "focus":    "Infosys Cyber Security practice serves global clients; InfySecure is their internal security brand.",
        "framing":  "Infosys looks for analysts who can adapt to large multi-client delivery environments. They value learning agility, documentation quality, and structured project teamwork.",
        "keywords": ["multi-client delivery", "security delivery", "documentation quality"],
        "emphasise_skills": ["Python", "Splunk", "documentation", "compliance", "risk assessment"],
    },
    "hcl": {
        "focus":    "HCL SecureCloud focuses on cloud security and managed detection and response.",
        "framing":  "HCL emphasises cloud-native security skills. Highlight AWS security, cloud IAM, and detection engineering.",
        "keywords": ["managed detection", "cloud security", "AWS security", "cloud IAM"],
        "emphasise_skills": ["AWS", "cloud security", "EDR", "Python", "SIEM", "IAM"],
    },
    "cognizant": {
        "focus":    "Cognizant cybersecurity serves BFSI and healthcare clients with SOC and compliance services.",
        "framing":  "Cognizant hires for 24x7 SOC operations and compliance roles. Emphasise structured investigation, documentation discipline, and BFSI security frameworks.",
        "keywords": ["SOC operations", "BFSI security", "compliance monitoring", "threat detection"],
        "emphasise_skills": ["SIEM", "alert triage", "compliance", "documentation"],
    },
    "capgemini": {
        "focus":    "Capgemini cybersecurity focuses on GRC consulting, cloud security, and managed SOC for European multinationals.",
        "framing":  "Capgemini values candidates who bridge GRC consulting and technical security. Emphasise ability to translate technical findings into compliance documentation.",
        "keywords": ["GRC", "cloud security", "security governance", "NIST", "compliance reporting"],
        "emphasise_skills": ["GRC", "NIST CSF", "cloud security", "compliance", "Python"],
    },
    "deloitte": {
        "focus":    "Deloitte Cyber Risk Advisory — BFSI-heavy GRC consulting, ITGC audits, third-party risk, and incident response.",
        "framing":  "Deloitte Cyber Risk hires consultants who communicate risk in business terms. They value GRC framework knowledge, IT audit skills (ITGC, SOX), and client-facing report writing.",
        "keywords": ["cyber risk advisory", "ITGC", "SOX", "GRC consulting", "third-party risk", "client deliverable"],
        "emphasise_skills": ["GRC", "ITGC", "ISO 27001", "NIST CSF", "PCI-DSS", "audit documentation"],
    },
    "kpmg": {
        "focus":    "KPMG IT Advisory and Risk Consulting — IT audit (ITGC, SOX), IS audit, and cybersecurity assurance.",
        "framing":  "KPMG IT Advisory is one of the heaviest ITGC and IS audit practices in India. They value control testing, audit evidence gathering, and SOX/ITGC methodology. CISA (even in progress) is explicitly valued.",
        "keywords": ["IT audit", "ITGC", "SOX", "IS audit", "CISA", "control testing", "audit evidence"],
        "emphasise_skills": ["IT audit", "ITGC", "ISO 27001", "PCI-DSS", "audit documentation", "compliance"],
    },
    "pwc": {
        "focus":    "PwC Cyber Risk and Regulatory — GRC consulting, data privacy, and cyber transformation advisory.",
        "framing":  "PwC Cyber hires for advisory roles requiring strong written communication and framework knowledge. They value regulatory landscape understanding (RBI, SEBI, GDPR, PDPB) and structured risk recommendations.",
        "keywords": ["cyber risk", "regulatory compliance", "data privacy", "GDPR", "PDPB", "RBI compliance"],
        "emphasise_skills": ["GRC", "data privacy", "GDPR", "NIST CSF", "regulatory compliance", "audit documentation"],
    },
    "ey": {
        "focus":    "EY GDS Bangalore — GRC, IT audit, data privacy, and risk assurance delivery centre.",
        "framing":  "EY GDS values process orientation, framework fluency, and executing standardised audit programs at scale. Emphasise structured methodologies, finding documentation, and alignment to international standards.",
        "keywords": ["GRC", "IT audit", "risk assurance", "GDS delivery", "ITGC", "data protection"],
        "emphasise_skills": ["IT audit", "GRC", "ISO 27001", "NIST CSF", "compliance", "documentation"],
    },
    "jpmorgan": {
        "focus":    "JPMorgan Chase Bangalore — technology risk, cybersecurity operations, and compliance for global banking.",
        "framing":  "JPMorgan values candidates who understand both technical controls and regulatory risk. They apply Basel III operational risk frameworks. Emphasise transaction monitoring instincts, audit documentation, and financial services compliance (AML, KYC, operational risk).",
        "keywords": ["technology risk", "operational risk", "cyber controls", "AML", "transaction monitoring"],
        "emphasise_skills": ["operational risk", "AML", "compliance", "audit documentation", "Python"],
    },
    "goldman sachs": {
        "focus":    "Goldman Sachs Bangalore — internal technology audit, SOC operations, and risk management.",
        "framing":  "Goldman Sachs internal audit values rigorous documentation, independence, and control challenge capability. Emphasise structured investigation methodology, audit evidence quality, and control-gap identification.",
        "keywords": ["technology audit", "ITGC", "internal audit", "control testing", "SOX"],
        "emphasise_skills": ["IT audit", "ITGC", "SOX", "audit documentation", "compliance"],
    },
    "deutsche bank": {
        "focus":    "Deutsche Bank Bangalore — information security, KYC/AML operations, and compliance.",
        "framing":  "Deutsche Bank runs significant KYC, AML, and information security operations. Emphasise investigative accuracy, AML/KYC process knowledge, and compliance documentation discipline.",
        "keywords": ["KYC", "AML", "information security", "BFSI compliance", "transaction monitoring"],
        "emphasise_skills": ["KYC", "AML", "compliance", "documentation", "transaction monitoring"],
    },
    "citi": {
        "focus":    "Citi Bangalore — risk analytics, fraud operations, and technology risk management.",
        "framing":  "Citi focuses on fraud detection, risk analytics, and operational risk. Emphasise root-cause analysis from Amazon, anomaly detection instincts, and Python/data skills.",
        "keywords": ["risk analytics", "fraud detection", "transaction monitoring", "operational risk"],
        "emphasise_skills": ["fraud detection", "Python", "risk assessment", "operational risk", "documentation"],
    },
    "amazon": {
        "focus":    "Amazon security engineering — AWS security, application security, and threat detection at scale.",
        "framing":  "Amazon applies leadership principles directly. Frame bullets using LP vocabulary where possible: Dive Deep, Bias for Action, Insist on Highest Standards. Emphasise automation, Python scripting, and measurable outcomes.",
        "keywords": ["dive deep", "bias for action", "automation", "AWS", "security at scale", "builder"],
        "emphasise_skills": ["Python", "AWS", "automation", "Bash", "cloud security", "SIEM"],
    },
    "google": {
        "focus":    "Google — security engineering, threat intelligence, and red team operations.",
        "framing":  "Google values technical depth, automation, and systems thinking. Emphasise hands-on technical work, scripting/automation skills, and systematic repeatable security processes.",
        "keywords": ["security engineering", "automation", "threat analysis", "scripting", "technical depth"],
        "emphasise_skills": ["Python", "Bash", "Linux", "automation", "threat intelligence", "SIEM"],
    },
    "microsoft": {
        "focus":    "Microsoft — security operations, identity security, and compliance engineering.",
        "framing":  "Microsoft values engineers who bridge identity, cloud, and security. Emphasise Azure/cloud security, Active Directory skills, and Microsoft tooling (Sentinel, Defender). Growth mindset language fits their culture.",
        "keywords": ["Azure security", "Microsoft Sentinel", "identity security", "Active Directory", "Zero Trust"],
        "emphasise_skills": ["Azure", "Active Directory", "cloud security", "IAM", "Python", "Splunk"],
    },
    "hdfc bank": {
        "focus":    "HDFC Bank — fraud analytics, information security, and compliance for India's largest private bank.",
        "framing":  "HDFC Bank security roles focus on fraud detection, AML, and RBI compliance. Emphasise transaction monitoring instincts, audit documentation rigour, and RBI IT/Cyber security framework awareness.",
        "keywords": ["fraud analytics", "AML", "RBI compliance", "IS audit", "transaction monitoring"],
        "emphasise_skills": ["fraud detection", "AML", "compliance", "audit documentation", "risk assessment"],
    },
    "bajaj finserv": {
        "focus":    "Bajaj Finserv — cybersecurity, fraud operations, and IT risk for a large NBFC.",
        "framing":  "Bajaj Finserv values fraud and risk domain knowledge. Emphasise investigative approach, policy-based decision making, and structured case documentation.",
        "keywords": ["fraud operations", "IT risk", "NBFC compliance", "RBI", "investigation"],
        "emphasise_skills": ["fraud detection", "risk assessment", "compliance", "documentation"],
    },
}

DOMAIN_TO_PROJECTS = {
    "SOC":        ("soc",   "cloud"),
    "VAPT":       ("vapt",  "soc"),
    "AppSec":     ("vapt",  "cloud"),
    "GRC":        ("grc",   "soc"),
    "Risk":       ("grc",   "cloud"),
    "Fraud-AML":  ("grc",   "vapt"),
    "CloudSec":   ("cloud", "soc"),
    "IAM":        ("cloud", "grc"),
    "Forensics":  ("soc",   "vapt"),
    "Network":    ("soc",   "cloud"),
    "General":    ("soc",   "grc"),
}

PROJECTS = {
    "soc": {
        "title": "Cybersecurity SOC & Threat Detection Home Lab",
        "tech":  "Splunk (SPL), Wireshark, Nmap, Burp Suite, Linux, MITRE ATT&CK, TryHackMe",
        "bullets": [
            "Deployed Splunk SIEM and authored SPL correlation searches for brute-force detection, lateral movement, and privilege escalation; mapped TTPs to MITRE ATT&CK (T1110, T1078, T1059) and wrote PICERL incident report.",
            "Analysed TCP/IP traffic in Wireshark — isolated HTTP/DNS flows, detected plaintext credential exposure, identified SYN scan patterns and DNS tunnelling anomalies indicative of C2 communication.",
            "Conducted network reconnaissance with Nmap (host discovery, OS fingerprinting, service-version detection) and SQL injection testing; documented OWASP Top 10 parameterised query remediation.",
            "Completed 9 TryHackMe rooms: Windows Event Logs, Active Directory Basics, Phishing Analysis, SIEM fundamentals, and threat hunting — building hands-on SOC Tier 1 analyst skills.",
        ],
    },
    "vapt": {
        "title": "Automated Vulnerability Scanning & CVE Reporting Tool",
        "tech":  "Python, Bash, Nessus, OpenVAS, Git, CVSS scoring",
        "bullets": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS REST APIs in Python; scheduled scans and generated CVE reports classified by CVSS severity with remediation guidance.",
            "Automated scan scheduling via Bash and cron; parsed JSON scan results to extract CVE IDs, affected assets, and remediation timelines into structured reports for audit review.",
            "Implemented delta-scan logic comparing consecutive scan runs to highlight newly discovered vulnerabilities and track patch compliance status.",
            "Documented SQL injection exploit at query level and applied OWASP Top 10 remediation, demonstrating practical DAST and AppSec assessment skills.",
        ],
    },
    "grc": {
        "title": "GRC Compliance Automation & Risk Intelligence Framework",
        "tech":  "Python, NIST CSF, ISO 27001, PCI-DSS, GDPR/PDPB, Excel, Git",
        "bullets": [
            "Developed Python GRC compliance tool mapping controls to NIST CSF, ISO 27001, and PCI-DSS; auto-generates gap analysis reports scoring each domain and flagging non-compliant controls.",
            "Built automated third-party vendor risk module computing quantitative risk scores across 12 domains aligned with enterprise TPRM frameworks.",
            "Implemented transaction anomaly detection engine flagging structuring, rapid velocity, and dormant account reactivation patterns applicable to fraud and AML roles.",
            "Developed GDPR/PDPB audit checklist evaluating consent management, data minimisation, and breach notification compliance with scored outputs.",
        ],
    },
    "cloud": {
        "title": "Cloud Security Posture & Threat Intelligence Monitoring Platform",
        "tech":  "Python, AWS (IAM, CloudTrail, GuardDuty, Security Hub), Elastic SIEM, VirusTotal API, Volatility, MITRE ATT&CK",
        "bullets": [
            "Built CSPM tool using boto3 auditing IAM configurations for over-privileged roles, unrotated access keys, public S3 misconfigurations, and disabled MFA; maps findings to CIS AWS Benchmark.",
            "Developed threat intelligence pipeline ingesting IOC feeds (VirusTotal, AbuseIPDB, MalwareBazaar APIs) and enriching SIEM alerts with malware classification and ATT&CK technique mapping.",
            "Automated DFIR triage using Volatility to extract running processes, network connections, and injected shellcode; correlated with Windows Event Logs to reconstruct attacker timelines.",
            "Configured AWS CloudTrail and Elastic SIEM detection rules for IAM privilege escalation and CloudTrail log tampering; audited IAM policies against least-privilege principles.",
        ],
    },
}

AMAZON_BASE = [
    "Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring the structured alert triage and escalation workflow used in SOC Tier 1 analyst roles.",
    "Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated findings to senior reviewers, demonstrating investigative instincts central to SOC and fraud analyst operations.",
    "Maintained audit-ready case documentation recording investigation findings, decisions, and corrective actions, establishing the evidence chain-of-custody discipline required for security incident reporting and IT audit.",
    "Collaborated with compliance and risk teams to enforce regulatory policies, identify process vulnerabilities, and implement corrective controls, building operational instincts in risk management and compliance monitoring.",
]


# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence lookup
# ─────────────────────────────────────────────────────────────────────────────

def get_company_intel(company_raw: str) -> dict | None:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip().lower()
    for key, intel in COMPANY_INTEL.items():
        if key in name or name in key:
            logger.info("  Company intel found: %s", key)
            return intel
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Company scraping (fallback for unknown companies)
# ─────────────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

def scrape_company_context(company_raw: str) -> str:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip()
    if not name or name.lower() in ("unknown", ""):
        return ""
    try:
        q    = requests.utils.quote(f"{name} company cybersecurity about mission")
        resp = requests.get(f"https://html.duckduckgo.com/html/?q={q}",
                            headers=_HEADERS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if (href.startswith("http") and
                    not any(x in href for x in ["linkedin.com", "glassdoor.com",
                                                "indeed.com", "naukri.com"])):
                pg   = requests.get(href, headers=_HEADERS, timeout=8)
                s2   = BeautifulSoup(pg.text, "html.parser")
                for tag in s2(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                main = s2.find("main") or s2.find("article") or s2
                text = " ".join(p.get_text(" ", strip=True)
                                for p in main.find_all("p")
                                if len(p.get_text()) > 40)
                if len(text) > 100:
                    logger.info("  Scraped context (%d chars)", len(text))
                    return text[:1200]
    except Exception as exc:
        logger.debug("Scrape failed: %s", exc)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# JSON repair — handles unescaped quotes inside string values
# ─────────────────────────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    """
    Attempt to fix common LLM JSON errors:
    1. Unescaped double-quotes inside string values  e.g.  "text with "quotes" here"
    2. Trailing commas before closing braces
    3. Smart quotes from the model
    """
    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    # Smart quotes → straight quotes
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")

    # Remove trailing commas before } or ]
    raw = re.sub(r",\s*([\}\]])", r"\1", raw)

    return raw.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Groq LLM
# ─────────────────────────────────────────────────────────────────────────────

def _call_groq(system: str, user: str, retries: int = 3) -> str:
    payload = {
        "model":       GROQ_MODEL,
        "temperature": 0.15,     # very low = consistent output, less hallucination
        "max_tokens":  2500,     # enough room for the model to finish all 22 keys
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    hdrs = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(GROQ_URL, json=payload, headers=hdrs, timeout=35)
            if r.status_code == 429:
                wait = 25 * attempt
                logger.warning("  Groq 429 — waiting %ds (attempt %d/%d)", wait, attempt, retries)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.RequestException as exc:
            logger.warning("  Groq error attempt %d: %s", attempt, exc)
            time.sleep(5 * attempt)
    raise RuntimeError("Groq API failed after retries.")


def generate_content(job: dict, p1_key: str, p2_key: str,
                     intel: dict | None, scraped_ctx: str) -> dict:
    p1  = PROJECTS[p1_key]
    p2  = PROJECTS[p2_key]
    amz = AMAZON_BASE

    if intel:
        company_section = (
            f"\nCOMPANY CONTEXT:\n"
            f"  Focus: {intel['focus']}\n"
            f"  Framing: {intel['framing']}\n"
            f"  Keywords to weave in naturally: {', '.join(intel['keywords'][:5])}\n"
            f"  Skills to foreground: {', '.join(intel['emphasise_skills'])}\n"
            f"Use this context to influence vocabulary and emphasis across ALL sections.\n"
            f"Do NOT write a generic sentence like 'Eager to contribute to X practice'.\n"
        )
    elif scraped_ctx:
        company_section = (
            f"\nCOMPANY CONTEXT (scraped):\n{scraped_ctx[:500]}\n"
            f"Use relevant signals to inform vocabulary. No generic sentences.\n"
        )
    else:
        company_section = ""

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "You write ATS-optimised, concise, factual bullets. "
        "Never fabricate tools, certifications, or experience not in source material. "
        "CRITICAL: Return ONLY a valid JSON object. "
        "Every string value must use double-quotes with internal double-quotes escaped as \\\" — "
        "for example: the SPL search was \\\"index=* failed\\\" not \"index=* failed\". "
        "No markdown fences. No comments. No trailing commas."
    )

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{company_section}
Return a JSON object with EXACTLY these 22 keys. All values must be strings.
Internal double-quotes must be escaped with a backslash.

{{
  "SUMMARY": "3-4 sentences. Start with Entry-level cybersecurity professional with hands-on experience in. Weave in top 3-4 keywords from job title and domain. Use only real skills: Python, Bash, Linux, Splunk, Wireshark, Nmap, Burp Suite, Nessus, OpenVAS, MITRE ATT&CK, OWASP Top 10, NIST CSF, ISO 27001 concepts, PCI-DSS concepts, GDPR concepts, TCP/IP, DNS, AWS basics, root cause analysis, audit documentation.",

  "SK_NET":  "Reorder for job relevance. Source only: TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
  "SK_OS":   "Reorder for job relevance. Source only: Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell (basic), Python, Bash",
  "SK_RISK": "Reorder for job relevance. Source only: Root cause analysis, policy-based case evaluation, audit documentation",
  "SK_SIEM": "Reorder for job relevance. Source only: Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
  "SK_SOC":  "Reorder for job relevance. Source only: Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security, ticketing system workflows",
  "SK_FW":   "Reorder for job relevance. Source only: MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",

  "AMZ_B1": "Rewrite with 1-2 domain keywords embedded (keep factual, start with action verb): {amz[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords embedded (keep factual, start with action verb): {amz[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords embedded (keep factual, start with action verb): {amz[2]}",
  "AMZ_B4": "Rewrite with 1-2 domain keywords embedded (keep factual, start with action verb): {amz[3]}",

  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{p1['tech']}",
  "P1_B1": "Rewrite for this job domain (factual, action verb, under 2 lines): {p1['bullets'][0]}",
  "P1_B2": "Rewrite for this job domain (factual, action verb, under 2 lines): {p1['bullets'][1]}",
  "P1_B3": "Rewrite for this job domain (factual, action verb, under 2 lines): {p1['bullets'][2]}",
  "P1_B4": "Rewrite for this job domain (factual, action verb, under 2 lines): {p1['bullets'][3]}",

  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{p2['tech']}",
  "P2_B1": "Rewrite for this job domain (factual, action verb, under 2 lines): {p2['bullets'][0]}",
  "P2_B2": "Rewrite for this job domain (factual, action verb, under 2 lines): {p2['bullets'][1]}",
  "P2_B3": "Rewrite for this job domain (factual, action verb, under 2 lines): {p2['bullets'][2]}",
  "P2_B4": "Rewrite for this job domain (factual, action verb, under 2 lines): {p2['bullets'][3]}"
}}"""

    raw = _call_groq(system, user)
    raw = _repair_json(raw)

    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Second attempt: try json5-style repair by removing problematic characters
        logger.warning("  First JSON parse failed (%s) — attempting repair...", exc)
        # Replace unescaped double-quotes inside string values aggressively
        # Strategy: find all "key": "value" pairs and escape inner quotes
        fixed = re.sub(
            r'("(?:SUMMARY|SK_\w+|AMZ_B\d|P[12]_(?:TITLE|TECH|B\d))":\s*)"(.*?)"(?=\s*[,}])',
            lambda m: m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"',
            raw, flags=re.DOTALL
        )
        try:
            content = json.loads(fixed)
            logger.info("  JSON repair succeeded.")
        except json.JSONDecodeError as exc2:
            logger.error("JSON repair also failed: %s\nRaw (first 500): %s", exc2, raw[:500])
            raise exc2

    expected = [
        "SUMMARY",
        "SK_NET", "SK_OS", "SK_RISK", "SK_SIEM", "SK_SOC", "SK_FW",
        "AMZ_B1", "AMZ_B2", "AMZ_B3", "AMZ_B4",
        "P1_TITLE", "P1_TECH", "P1_B1", "P1_B2", "P1_B3", "P1_B4",
        "P2_TITLE", "P2_TECH", "P2_B1", "P2_B2", "P2_B3", "P2_B4",
    ]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    return content


# ─────────────────────────────────────────────────────────────────────────────
# DOCX template fill
# ─────────────────────────────────────────────────────────────────────────────

def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    full = "".join(r.text for r in para.runs)
    if placeholder not in full:
        return False
    new_text = full.replace(placeholder, replacement)
    if para.runs:
        para.runs[0].text = new_text
        for r in para.runs[1:]:
            r.text = ""
    return True


def fill_template(content: dict) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"resume_template.docx not found. Upload it to the repo root."
        )
    doc = Document(str(TEMPLATE_PATH))
    replacements = {f"[[{k}]]": v for k, v in content.items()}
    for para in doc.paragraphs:
        full_text = "".join(r.text for r in para.runs)
        for ph, val in replacements.items():
            if ph in full_text:
                _replace_in_para(para, ph, val)
                full_text = full_text.replace(ph, val)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# GitHub storage — commits the DOCX into resumes/ folder in the repo
# Returns the raw download URL (permanent, public, no quota issues)
# GITHUB_TOKEN is automatically available in every Actions workflow run
# ─────────────────────────────────────────────────────────────────────────────

def _safe(s: str, n: int = 35) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:n]


def upload_to_github(docx_bytes: bytes, job: dict) -> str:
    """
    Commit the DOCX file to the resumes/ folder in this repo using the
    GitHub Contents API. Returns a direct raw download URL.

    Why this instead of Drive:
    - GITHUB_TOKEN is always available in Actions — zero setup
    - No storage quota (repo limit is 1GB total, DOCX files are ~20KB each)
    - Permanent URLs — never expire
    - Files are readable by anyone with the repo link
    """
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not available.")
    if not GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY not set.")

    filename = f"Resume_{_safe(job['job_title'])}_{_safe(job['company'])}.docx"
    path     = f"{RESUMES_FOLDER}/{filename}"
    api_url  = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"

    encoded  = base64.b64encode(docx_bytes).decode()
    headers  = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Check if file already exists (need sha to update)
    sha = None
    existing = requests.get(api_url, headers=headers, timeout=10)
    if existing.status_code == 200:
        sha = existing.json().get("sha")

    payload = {
        "message": f"Resume: {job['job_title']} @ {job['company']}",
        "content": encoded,
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    # Construct raw download URL
    raw_url = (f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}"
               f"/{GITHUB_BRANCH}/{path}")
    logger.info("  Committed to GitHub: %s", filename)
    return raw_url


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_creds() -> Credentials:
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )


def ensure_column(ws, col_name: str) -> int:
    """Ensure column exists. Returns its 1-based index."""
    headers = ws.row_values(1)
    if col_name not in headers:
        idx = len(headers) + 1
        ws.update_cell(1, idx, col_name)
        logger.info("Added column '%s' at position %d.", col_name, idx)
        return idx
    return headers.index(col_name) + 1


def get_pending_jobs(ws, resume_col: int) -> list[dict]:
    """
    Return rows where:
      - status = "New"  (generate resume for new jobs BEFORE applying)
      - resume_doc_link is empty  (not already generated)

    Applied/rejected/not_relevant jobs are excluded — no need to regenerate.
    """
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []

    headers = all_rows[0]
    col     = {h: i for i, h in enumerate(headers)}

    def _get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    # Statuses that mean the job is already handled — skip these
    SKIP_STATUSES = {"applied", "rejected", "not_relevant", "offer", "interview"}

    pending = []
    for row_num, row in enumerate(all_rows[1:], start=2):
        status     = _get(row, "status").lower()
        resume_val = row[resume_col - 1].strip() if (resume_col - 1) < len(row) else ""

        # Only generate for "New" jobs with no resume yet
        if status == "new" and not resume_val:
            pending.append({
                "row_num":   row_num,
                "job_title": _get(row, "job_title") or "Cybersecurity Role",
                "company":   _get(row, "company")   or "Unknown",
                "domain":    _get(row, "domain")     or "General",
                "summary":   _get(row, "summary"),
                "skills":    _get(row, "skills_required"),
            })
    return pending


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("Resume Tailor started")
    logger.info("=" * 60)

    if not GROQ_API_KEY:
        logger.error("GROQ_API_KEY is not set.")
        sys.exit(1)
    if not TEMPLATE_PATH.exists():
        logger.error("resume_template.docx not found in repo root.")
        sys.exit(1)
    if not GITHUB_TOKEN:
        logger.error("GITHUB_TOKEN not available — is this running in GitHub Actions?")
        sys.exit(1)

    creds = _get_creds()
    gc    = gspread.authorize(creds)
    ws    = gc.open(SHEET_NAME).sheet1
    logger.info("Connected to Google Sheets.")

    resume_col = ensure_column(ws, "resume_doc_link")

    pending = get_pending_jobs(ws, resume_col)
    if not pending:
        logger.info("No New jobs with empty resume_doc_link. Nothing to do.")
        sys.exit(0)

    logger.info("Found %d pending job(s). Processing up to %d.",
                len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-" * 50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)",
                    i, len(pending), job["job_title"], job["company"], job["domain"])
        try:
            # 1. Select projects
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc", "grc"))
            logger.info("  Projects: %s + %s", p1_key, p2_key)

            # 2. Company intelligence
            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company_context(job["company"])

            # 3. Generate content via Groq
            logger.info("  Generating content via Groq...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx)
            logger.info("  Content generated.")

            # 4. Fill the DOCX template
            docx_bytes = fill_template(content)
            logger.info("  Template filled (%d bytes).", len(docx_bytes))

            # 5. Commit to GitHub repo (no Drive, no quota issues)
            download_url = upload_to_github(docx_bytes, job)

            # 6. Write link to sheet
            ws.update_cell(job["row_num"], resume_col, download_url)
            logger.info("  ✓ Sheet updated: %s", download_url)

            success += 1
            time.sleep(4)

        except Exception as exc:
            logger.error("  ✗ Failed: %s", exc)
            continue

    logger.info("=" * 60)
    logger.info("Done: %d/%d succeeded.", success, len(pending))
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
