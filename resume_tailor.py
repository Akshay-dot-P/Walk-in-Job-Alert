"""
resume_tailor.py
================
Generates tailored DOCX + PDF resumes for every New job in the sheet.

WHAT'S IN THIS VERSION:
  - MAX_JOBS_PER_RUN = 10
  - 3 projects total; 2 selected per resume based on domain
  - New skills layout: SOC Operations / SIEM & Monitoring / Threat Intelligence /
    Systems & Networking / Automation
  - Hyperlink fix: replaces [[PLACEHOLDERS]] inside w:hyperlink elements correctly
  - No Professional Summary section (removed)
  - No Git icon (drawing elements stripped from project title paragraphs)
  - PDF via LibreOffice (free, perfect formatting, no Drive quota)
  - Both DOCX and PDF committed to GitHub resumes/ folder
  - Dynamic tool injection per JD keywords
  - Company intelligence for 18 major Bangalore hirers

PLACEHOLDER MAP (must match resume_template.docx exactly):
  [[AMZ_B1]] [[AMZ_B2]] [[AMZ_B3]]
  [[P1_TITLE]] [[P1_TECH]] [[P1_B1]] [[P1_B2]] [[P1_B3]]
  [[P2_TITLE]] [[P2_TECH]] [[P2_B1]] [[P2_B2]] [[P2_B3]]
  [[SK_SOC]] [[SK_SIEM]] [[SK_TI]] [[SK_SYS]] [[SK_AUTO]]

ADD TO requirements.txt:
  python-docx==1.1.2
  beautifulsoup4==4.12.3
  google-api-python-client==2.108.0

WORKFLOW must have:
  permissions: contents: write
  - run: sudo apt-get install -y libreoffice
"""

import os, sys, re, json, time, io, base64, logging, requests, subprocess, tempfile, copy
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
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

SHEET_NAME        = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
GROQ_API_KEY      = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL        = "llama-3.1-8b-instant"
GROQ_URL          = "https://api.groq.com/openai/v1/chat/completions"
MAX_JOBS_PER_RUN  = 10
TEMPLATE_PATH     = Path(__file__).parent / "resume_template.docx"
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_BRANCH     = os.environ.get("GITHUB_REF_NAME", "main")
RESUMES_FOLDER    = "resumes"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─────────────────────────────────────────────────────────────────────────────
# 3 PROJECTS
#
# PROJECT 1 — soc_auto: SOC Automation & Threat Detection Lab
#   Covers: SOC, SIEM, IR, DFIR, Threat Intel, Malware, Network Security
#   Combines original Cybersecurity Home Lab content with SOAR automation,
#   Sigma rules, and Telegram alert integration.
#
# PROJECT 2 — vuln_scanner: Vulnerability Scanner & Patch Prioritization Engine
#   Covers: VAPT, Vuln Management, AppSec, Network Security
#   Combines original Automated Vulnerability Scanning with OWASP Top 10
#   checker, EPSS scoring from FIRST.org API, and remediation SLA calculator.
#
# PROJECT 3 — phishing_osint: Phishing & OSINT Threat Intelligence Tool
#   Covers: Threat Intel, OSINT, Phishing, IR, Fraud, AML
#   Multi-API enrichment pipeline: VirusTotal, AbuseIPDB, WHOIS, DNS.
#   Telegram bot interface, bulk CSV scanner, typosquatting detector.
# ─────────────────────────────────────────────────────────────────────────────

PROJECTS = {
    "soc_auto": {
        "title":  "SOC Automation & Threat Detection Lab",
        "github": "https://github.com/Akshay-dot-P/soc-threat-lab",
        "tech_base": ["Python", "Splunk", "Wireshark", "Nmap", "MITRE ATT&CK", "Sigma rules"],
        "tech_swappable": {
            r"qradar|ibm qradar":                          ["QRadar"],
            r"elastic|kibana|elk":                         ["Elastic SIEM"],
            r"sentinel|azure sentinel|microsoft sentinel": ["Azure Sentinel"],
            r"crowdstrike|falcon|edr|xdr":                 ["CrowdStrike Falcon"],
            r"defender|microsoft defender|mde":            ["Microsoft Defender"],
            r"suricata|snort|zeek|ids\b|ips\b":            ["Suricata IDS"],
            r"burp suite|burp|owasp|appsec|web app":       ["Burp Suite"],
            r"metasploit|exploit|pentest|vapt":            ["Metasploit"],
            r"volatility|memory forensics|dfir":           ["Volatility"],
            r"soar|playbook|automation|orchestration":     ["SOAR playbook"],
            r"grafana|dashboard|visuali":                  ["Grafana"],
            r"sysmon|windows event|evtx":                  ["Sysmon"],
        },
        "bullets": [
            "Deployed Splunk SIEM with SPL correlation searches for brute-force detection (index=* failed | stats count by src_ip), lateral movement, and privilege escalation; mapped TTPs to MITRE ATT&CK (T1110, T1078, T1059) and wrote PICERL incident report.",
            "Built automated SOAR-style detection pipeline: Python script ingests Splunk alerts, runs IOC enrichment via VirusTotal API, and dispatches Telegram notifications with severity classification — reducing mean time to triage by automating repetitive L1 tasks.",
            "Converted detection logic to Sigma rules (vendor-neutral format used by enterprise SOCs); performed TCP/IP analysis in Wireshark to detect SYN scans, DNS tunnelling, and plaintext credential exposure on unencrypted sessions.",
        ],
    },

    "vuln_scanner": {
        "title":  "Vulnerability Scanner & Patch Prioritization Engine",
        "github": "https://github.com/Akshay-dot-P/vuln-scanner",
        "tech_base": ["Python", "Bash", "Nessus", "OpenVAS", "NVD API", "CVSS/EPSS scoring"],
        "tech_swappable": {
            r"qualys":                                    ["Qualys"],
            r"tenable|nessus":                           ["Nessus"],
            r"openvas":                                  ["OpenVAS"],
            r"burp suite|burp|owasp|web app|appsec":    ["Burp Suite", "OWASP ZAP"],
            r"nmap|network scan|port scan":              ["Nmap"],
            r"cve|nvd|cvss":                             ["NVD API"],
            r"epss|exploit probability|first\.org":     ["EPSS API (FIRST.org)"],
            r"patch|remediation|sla|prioriti":          ["remediation SLA calculator"],
            r"sast|bandit|semgrep|secure code":         ["Semgrep SAST"],
            r"container|docker|trivy|kubernetes":       ["Trivy container scanner"],
        },
        "bullets": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS REST APIs in Python; generates CVE reports classified by CVSS severity with remediation guidance; implemented EPSS scoring from FIRST.org API to prioritise by actual exploit probability — a metric rarely used by freshers.",
            "Developed OWASP Top 10 automated web checker that sends crafted HTTP requests to test targets and detects injection, broken auth, and SSRF vulnerabilities; documented query-level SQL injection exploit and parameterised query remediation.",
            "Automated scan scheduling via Bash and cron; built delta-scan logic comparing consecutive runs to flag newly discovered CVEs and calculate remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) for patch compliance tracking.",
        ],
    },

    "phishing_osint": {
        "title":  "Phishing & OSINT Threat Intelligence Tool",
        "github": "https://github.com/Akshay-dot-P/phishing-osint-tool",
        "tech_base": ["Python", "VirusTotal API", "AbuseIPDB", "WHOIS", "Telegram bot", "DNS analysis"],
        "tech_swappable": {
            r"shodan|censys|internet scan":              ["Shodan API"],
            r"maltego|graph|relationship|link analysis": ["graph visualisation"],
            r"osint|open source intel|reconnaissance":  ["theHarvester", "OSINT framework"],
            r"phishing|url|domain|malicious link":      ["URLScan.io"],
            r"fraud|aml|financial crime|transaction":   ["fraud pattern matching"],
            r"threat intel|cti|ioc|indicator":          ["MISP IOC feeds"],
            r"typosquat|brand|impersonat":              ["typosquatting detector"],
            r"email|header|spf|dkim|dmarc":             ["email header analyser"],
            r"ip|geolocation|asn|reputation":           ["IPInfo API"],
            r"telegram|bot|alert|notification":         ["Telegram bot"],
        },
        "bullets": [
            "Built multi-API threat intelligence pipeline: submits suspicious URLs/IPs to VirusTotal, AbuseIPDB, and URLScan.io simultaneously; cross-references WHOIS registration age, DNS records, and SSL certificate details to produce a unified phishing probability score.",
            "Implemented typosquatting domain detector that generates character-substitution variants of brand domains and checks live DNS resolution — catches brand-impersonation attacks before they are reported to threat feeds.",
            "Deployed Telegram bot interface enabling analysts to submit URLs for live IOC enrichment and receive structured threat reports in-chat; supports bulk CSV input/output for incident response workflows where analysts triage multiple URLs simultaneously.",
        ],
    },
}

# Which 2 projects to show on the resume for each domain
DOMAIN_TO_PROJECTS = {
    "SOC":        ("soc_auto",       "phishing_osint"),
    "VAPT":       ("vuln_scanner",   "soc_auto"),
    "AppSec":     ("vuln_scanner",   "soc_auto"),
    "GRC":        ("phishing_osint", "vuln_scanner"),
    "Risk":       ("phishing_osint", "vuln_scanner"),
    "Fraud-AML":  ("phishing_osint", "soc_auto"),
    "CloudSec":   ("soc_auto",       "vuln_scanner"),
    "IAM":        ("soc_auto",       "phishing_osint"),
    "Forensics":  ("soc_auto",       "phishing_osint"),
    "Network":    ("soc_auto",       "vuln_scanner"),
    "General":    ("soc_auto",       "vuln_scanner"),
}

AMAZON_BASE = [
    "Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring the structured alert triage and escalation workflow used in SOC Tier 1 analyst roles.",
    "Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated findings to senior reviewers, demonstrating investigative instincts central to SOC and fraud analyst operations.",
    "Maintained audit-ready case documentation recording investigation findings, decisions, and corrective actions, establishing the evidence chain-of-custody discipline required for security incident reporting and IT audit.",
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_INTEL = {
    "wipro": {
        "framing":   "Wipro hires L1 SOC analysts for 24x7 SIEM monitoring shifts. Value: process adherence, SLA discipline, shift documentation, escalation workflows.",
        "keywords":  ["24x7 SOC", "SLA adherence", "shift documentation", "SIEM operations", "incident escalation"],
        "skills_first": ["Splunk", "SIEM", "alert triage", "incident escalation", "documentation"],
    },
    "tcs": {
        "framing":   "TCS values structured compliance and certification alignment. Hires for ISO 27001 ISMS, VAPT, process-oriented security delivery.",
        "keywords":  ["ISMS", "ISO 27001", "compliance audit", "vulnerability assessment"],
        "skills_first": ["ISO 27001", "VAPT", "compliance", "audit documentation"],
    },
    "infosys": {
        "framing":   "Infosys values learning agility, documentation quality, multi-client delivery adaptability.",
        "keywords":  ["multi-client delivery", "documentation quality"],
        "skills_first": ["Python", "Splunk", "documentation", "compliance"],
    },
    "hcl": {
        "framing":   "HCL SecureCloud emphasises cloud-native security. Highlight AWS, cloud IAM, detection engineering.",
        "keywords":  ["cloud security", "AWS security", "cloud IAM"],
        "skills_first": ["AWS", "cloud security", "Python", "SIEM"],
    },
    "cognizant": {
        "framing":   "Cognizant hires for 24x7 SOC and BFSI compliance. Value: investigation rigour, BFSI frameworks.",
        "keywords":  ["SOC operations", "BFSI security", "compliance monitoring"],
        "skills_first": ["SIEM", "alert triage", "compliance", "documentation"],
    },
    "capgemini": {
        "framing":   "Capgemini bridges GRC consulting and technical security for European clients.",
        "keywords":  ["GRC", "NIST", "compliance reporting"],
        "skills_first": ["GRC", "NIST CSF", "cloud security", "compliance"],
    },
    "deloitte": {
        "framing":   "Deloitte Cyber Risk Advisory: ITGC audits, GRC, BFSI. Value: communicating risk in business terms, ITGC/SOX, client reports.",
        "keywords":  ["cyber risk advisory", "ITGC", "SOX", "GRC consulting"],
        "skills_first": ["GRC", "ITGC", "ISO 27001", "NIST CSF", "audit documentation"],
    },
    "kpmg": {
        "framing":   "KPMG IT Advisory: ITGC/IS audit. Value: control testing, audit evidence, SOX/ITGC methodology. CISA valued.",
        "keywords":  ["IT audit", "ITGC", "SOX", "CISA", "control testing"],
        "skills_first": ["IT audit", "ITGC", "ISO 27001", "audit documentation"],
    },
    "pwc": {
        "framing":   "PwC Cyber Risk: regulatory landscape knowledge (RBI, SEBI, GDPR, PDPB), structured risk recommendations.",
        "keywords":  ["cyber risk", "regulatory compliance", "data privacy", "GDPR"],
        "skills_first": ["GRC", "data privacy", "GDPR", "NIST CSF"],
    },
    "ey": {
        "framing":   "EY GDS Bangalore: GRC and IT audit delivery. Value: structured audit execution, international standards.",
        "keywords":  ["GRC", "IT audit", "risk assurance", "ITGC"],
        "skills_first": ["IT audit", "GRC", "ISO 27001", "compliance"],
    },
    "jpmorgan": {
        "framing":   "JPMorgan: technology risk, Basel III operational risk, AML/KYC operations.",
        "keywords":  ["technology risk", "operational risk", "AML", "transaction monitoring"],
        "skills_first": ["operational risk", "AML", "compliance", "Python"],
    },
    "goldman sachs": {
        "framing":   "Goldman Sachs: ITGC, control testing, audit independence. Value: documentation rigour.",
        "keywords":  ["technology audit", "ITGC", "internal audit", "SOX"],
        "skills_first": ["IT audit", "ITGC", "SOX", "audit documentation"],
    },
    "deutsche bank": {
        "framing":   "Deutsche Bank: KYC, AML, information security. Value: investigative accuracy, AML/KYC process knowledge.",
        "keywords":  ["KYC", "AML", "information security", "transaction monitoring"],
        "skills_first": ["KYC", "AML", "compliance", "documentation"],
    },
    "citi": {
        "framing":   "Citi: fraud detection, risk analytics. Value: pattern recognition, Python/data skills, anomaly detection.",
        "keywords":  ["risk analytics", "fraud detection", "transaction monitoring"],
        "skills_first": ["fraud detection", "Python", "risk assessment"],
    },
    "amazon": {
        "framing":   "Amazon: LP lens — Dive Deep, Bias for Action, Insist on Highest Standards. Automation mindset.",
        "keywords":  ["dive deep", "automation", "AWS", "security at scale"],
        "skills_first": ["Python", "AWS", "automation", "Bash", "SIEM"],
    },
    "google": {
        "framing":   "Google: technical depth, automation, systems thinking.",
        "keywords":  ["security engineering", "automation", "threat analysis"],
        "skills_first": ["Python", "Bash", "Linux", "automation", "threat intelligence"],
    },
    "microsoft": {
        "framing":   "Microsoft: bridge identity, cloud, and security. Azure/AD/Sentinel.",
        "keywords":  ["Azure security", "Microsoft Sentinel", "Active Directory", "Zero Trust"],
        "skills_first": ["Azure", "Active Directory", "cloud security", "IAM"],
    },
    "hdfc bank": {
        "framing":   "HDFC Bank: fraud detection, AML, RBI compliance.",
        "keywords":  ["fraud analytics", "AML", "RBI compliance", "transaction monitoring"],
        "skills_first": ["fraud detection", "AML", "compliance", "audit documentation"],
    },
    "bajaj finserv": {
        "framing":   "Bajaj Finserv: fraud/risk operations for large NBFC.",
        "keywords":  ["fraud operations", "IT risk", "NBFC compliance"],
        "skills_first": ["fraud detection", "risk assessment", "compliance"],
    },
}


def get_company_intel(company_raw: str) -> dict | None:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip().lower()
    for key, intel in COMPANY_INTEL.items():
        if key in name or name in key:
            logger.info("  Company intel: %s", key)
            return intel
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic tool selection per JD
# ─────────────────────────────────────────────────────────────────────────────

def select_tools(project_key: str, jd_text: str, max_tools: int = 5) -> list[str]:
    proj     = PROJECTS[project_key]
    jd_lower = jd_text.lower()
    base     = list(proj["tech_base"])
    extra    = []
    for pattern, tools in proj["tech_swappable"].items():
        if re.search(pattern, jd_lower):
            for t in tools:
                if t not in base and t not in extra:
                    extra.append(t)
    return (base + extra)[:max_tools]


# ─────────────────────────────────────────────────────────────────────────────
# Company web scraping (fallback)
# ─────────────────────────────────────────────────────────────────────────────

_HDRS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def scrape_company(company_raw: str) -> str:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip()
    if not name or name.lower() in ("unknown", ""):
        return ""
    try:
        q    = requests.utils.quote(f"{name} cybersecurity about mission")
        resp = requests.get(f"https://html.duckduckgo.com/html/?q={q}",
                            headers=_HDRS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if href.startswith("http") and not any(
                    x in href for x in ["linkedin.com", "glassdoor.com", "indeed.com"]):
                pg   = requests.get(href, headers=_HDRS, timeout=8)
                s2   = BeautifulSoup(pg.text, "html.parser")
                for tag in s2(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                main = s2.find("main") or s2.find("article") or s2
                text = " ".join(p.get_text(" ", strip=True)
                                for p in main.find_all("p") if len(p.get_text()) > 40)
                if len(text) > 100:
                    return text[:800]
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# JSON repair
# ─────────────────────────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "",          raw.strip())
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")
    raw = re.sub(r",\s*([\}\]])", r"\1",  raw)
    return raw.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Groq LLM
# ─────────────────────────────────────────────────────────────────────────────

def _call_groq(system: str, user: str, retries: int = 3) -> str:
    payload = {
        "model":       GROQ_MODEL,
        "temperature": 0.15,
        "max_tokens":  2500,
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
                     intel: dict | None, scraped_ctx: str,
                     p1_tools: list, p2_tools: list) -> dict:

    p1 = PROJECTS[p1_key]
    p2 = PROJECTS[p2_key]

    if intel:
        co_ctx = (
            f"\nCOMPANY FRAMING: {intel['framing']}\n"
            f"Priority keywords: {', '.join(intel['keywords'][:5])}\n"
            f"Skills to foreground: {', '.join(intel['skills_first'])}\n"
            f"Do NOT write 'Eager to contribute to X' — let keywords appear naturally.\n"
        )
    elif scraped_ctx:
        co_ctx = f"\nCOMPANY CONTEXT: {scraped_ctx[:400]}\nUse vocabulary signals naturally.\n"
    else:
        co_ctx = ""

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "You write ATS-optimised, factual, concise bullets that fit on one page. "
        "Never fabricate tools, certifications, or experience not in the source material. "
        "CRITICAL: Return ONLY a valid JSON object. "
        "Internal double-quotes MUST be escaped as \\\". "
        "No markdown fences. No comments. No trailing commas."
    )

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{co_ctx}
SINGLE-PAGE RULE: Each bullet must be max 160 characters (fits 2 lines at 9.5pt).

Return a JSON object with EXACTLY these 19 keys:

{{
  "SK_SOC":  "Most relevant first. Source: Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
  "SK_SIEM": "Most relevant first. Source: Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
  "SK_TI":   "Most relevant first. Source: MITRE ATT\\&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
  "SK_SYS":  "Most relevant first. Source: Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, firewall and IDS/IPS concepts",
  "SK_AUTO": "Most relevant first. Source: Python, Bash (basic), regular expressions",

  "AMZ_B1": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[2]}",

  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{', '.join(p1_tools)}",
  "P1_B1": "Rewrite using tools in P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][0]}",
  "P1_B2": "Rewrite using tools in P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][1]}",
  "P1_B3": "Rewrite using tools in P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][2]}",

  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{', '.join(p2_tools)}",
  "P2_B1": "Rewrite using tools in P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][0]}",
  "P2_B2": "Rewrite using tools in P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][1]}",
  "P2_B3": "Rewrite using tools in P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][2]}"
}}

Rules:
- SK_* values: skill values ONLY — no category label, no colon
- Every bullet opens with a past-tense action verb
- Escape all internal double-quotes with backslash
- & in skill names must be written as & (not &amp;)"""

    raw = _call_groq(system, user)
    raw = _repair_json(raw)

    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("  JSON parse failed (%s) — attempting repair...", exc)
        fixed = re.sub(
            r'("(?:SK_\w+|AMZ_B\d|P[12]_(?:TITLE|TECH|B\d))":\s*)"(.*?)"(?=\s*[,}])',
            lambda m: m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"',
            raw, flags=re.DOTALL
        )
        try:
            content = json.loads(fixed)
            logger.info("  JSON repair succeeded.")
        except json.JSONDecodeError as exc2:
            logger.error("JSON repair failed: %s\nFirst 500: %s", exc2, raw[:500])
            raise exc2

    expected = [
        "SK_SOC", "SK_SIEM", "SK_TI", "SK_SYS", "SK_AUTO",
        "AMZ_B1", "AMZ_B2", "AMZ_B3",
        "P1_TITLE", "P1_TECH", "P1_B1", "P1_B2", "P1_B3",
        "P2_TITLE", "P2_TECH", "P2_B1", "P2_B2", "P2_B3",
    ]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    return content


# ─────────────────────────────────────────────────────────────────────────────
# DOCX template fill
# KEY FIX: replaces placeholders inside w:hyperlink elements too, by iterating
# all w:t elements in the paragraph rather than just para.runs
# ─────────────────────────────────────────────────────────────────────────────

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    """
    Replace [[PLACEHOLDER]] anywhere in the paragraph's text nodes,
    including inside w:hyperlink elements (which para.runs does NOT cover).

    Strategy: collect all w:t elements in order, concatenate their text,
    find the placeholder, rebuild the text across runs by putting the full
    replaced text in the first w:t and clearing the rest.
    """
    # Collect all w:t elements in document order
    all_t = para._p.findall(f".//{{{W_NS}}}t")
    full  = "".join(t.text or "" for t in all_t)

    if placeholder not in full:
        return False

    new_text = full.replace(placeholder, replacement)

    # Put full text in first w:t, blank the rest
    if all_t:
        all_t[0].text = new_text
        # Preserve xml:space if there are spaces
        if new_text and (new_text[0] == " " or new_text[-1] == " "):
            all_t[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for t in all_t[1:]:
            t.text = ""
    return True


def fill_template(content: dict) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError("resume_template.docx not found in repo root.")

    doc = Document(str(TEMPLATE_PATH))
    replacements = {f"[[{k}]]": v for k, v in content.items()}

    for para in doc.paragraphs:
        full = "".join(
            (t.text or "")
            for t in para._p.findall(f".//{{{W_NS}}}t")
        )
        for ph, val in replacements.items():
            if ph in full:
                _replace_in_para(para, ph, val)
                full = full.replace(ph, val)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# PDF via LibreOffice (zero quota, perfect formatting)
# Workflow must have: sudo apt-get install -y libreoffice
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "resume.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf",
             "--outdir", tmpdir, docx_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice failed: {result.stderr[:300]}")

        pdf_path = os.path.join(tmpdir, "resume.pdf")
        if not os.path.exists(pdf_path):
            raise FileNotFoundError("LibreOffice did not produce resume.pdf")

        with open(pdf_path, "rb") as f:
            return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# GitHub storage
# ─────────────────────────────────────────────────────────────────────────────

def _safe(s: str, n: int = 35) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:n]


def _github_commit(filename: str, file_bytes: bytes, message: str) -> str:
    path    = f"{RESUMES_FOLDER}/{filename}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    headers = {
        "Authorization":        f"Bearer {GITHUB_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    sha     = None
    existing = requests.get(api_url, headers=headers, timeout=10)
    if existing.status_code == 200:
        sha = existing.json().get("sha")

    payload = {
        "message": message,
        "content": base64.b64encode(file_bytes).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{GITHUB_BRANCH}/{path}"


def upload_to_github(docx_bytes: bytes, pdf_bytes: bytes, job: dict) -> tuple[str, str]:
    base = f"Resume_{_safe(job['job_title'])}_{_safe(job['company'])}"
    msg  = f"Resume: {job['job_title']} @ {job['company']}"
    return (
        _github_commit(f"{base}.docx", docx_bytes, msg),
        _github_commit(f"{base}.pdf",  pdf_bytes,  msg),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_creds() -> Credentials:
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)


def ensure_column(ws, name: str) -> int:
    headers = ws.row_values(1)
    if name not in headers:
        idx = len(headers) + 1
        ws.update_cell(1, idx, name)
        headers.append(name)
        logger.info("Added column '%s' at position %d.", name, idx)
        return idx
    return headers.index(name) + 1


def get_pending_jobs(ws, doc_col: int) -> list[dict]:
    """
    Jobs where status=New and resume_doc_link is empty.
    Generate resume BEFORE applying — not after.
    """
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []

    headers = all_rows[0]
    col     = {h: i for i, h in enumerate(headers)}

    def _get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    SKIP = {"applied", "rejected", "not_relevant", "offer", "interview"}
    pending = []
    for row_num, row in enumerate(all_rows[1:], start=2):
        status   = _get(row, "status").lower()
        doc_link = row[doc_col - 1].strip() if (doc_col - 1) < len(row) else ""
        if status == "new" and not doc_link:
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

    for name, val in [("GROQ_API_KEY", GROQ_API_KEY),
                      ("GITHUB_TOKEN", GITHUB_TOKEN),
                      ("GITHUB_REPOSITORY", GITHUB_REPOSITORY)]:
        if not val:
            logger.error("%s is not set.", name)
            sys.exit(1)

    if not TEMPLATE_PATH.exists():
        logger.error("resume_template.docx not found in repo root.")
        sys.exit(1)

    creds   = _get_creds()
    gc      = gspread.authorize(creds)
    ws      = gc.open(SHEET_NAME).sheet1
    logger.info("Connected to Google Sheets.")

    doc_col = ensure_column(ws, "resume_doc_link")
    pdf_col = ensure_column(ws, "resume_pdf_link")

    pending = get_pending_jobs(ws, doc_col)
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
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_auto", "vuln_scanner"))
            logger.info("  Projects: %s + %s", p1_key, p2_key)

            jd_text  = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools = select_tools(p1_key, jd_text)
            p2_tools = select_tools(p2_key, jd_text)
            logger.info("  P1 tools: %s", ", ".join(p1_tools))
            logger.info("  P2 tools: %s", ", ".join(p2_tools))

            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company(job["company"])

            logger.info("  Generating content via Groq...")
            content    = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                          p1_tools, p2_tools)
            logger.info("  Content generated.")

            docx_bytes = fill_template(content)
            logger.info("  Template filled (%d bytes).", len(docx_bytes))

            logger.info("  Generating PDF via LibreOffice...")
            pdf_bytes  = generate_pdf(docx_bytes)
            logger.info("  PDF generated (%d bytes).", len(pdf_bytes))

            doc_url, pdf_url = upload_to_github(docx_bytes, pdf_bytes, job)

            ws.update_cell(job["row_num"], doc_col, doc_url)
            ws.update_cell(job["row_num"], pdf_col, pdf_url)
            logger.info("  ✓ Done — Doc: %s", doc_url)

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
