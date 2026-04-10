"""
resume_tailor.py — Automated ATS-optimised resume tailoring.

WHAT CHANGED IN THIS VERSION:
  - MAX_JOBS_PER_RUN: 6 → 10
  - 3 project pool (down from 4): grc_risk, soc_lab, cloud_intel
    Two are selected per job; the third stays on GitHub only.
  - Dynamic tool injection: LLM reads JD skills and swaps in matching tools
    from each project's swappable pool, keeps most relevant, drops least relevant.
  - PDF generation via LibreOffice (no Drive quota, no API, runs on ubuntu-latest).
  - resume_pdf_link column added to sheet alongside resume_doc_link.
  - Single-page enforcement: 3 bullets per project, 3 Amazon bullets, 2-line max per bullet.
  - Status trigger: "New" (not "applied") — generate before you apply, not after.
  - Storage: GitHub repo resumes/ folder via Contents API (no Drive quota issues).
  - JSON repair: handles unescaped quotes from Groq, increased max_tokens to 2500.

REQUIRES in requirements.txt (add if missing):
  python-docx==1.1.2
  beautifulsoup4==4.12.3
  google-api-python-client==2.108.0

WORKFLOW must install LibreOffice:
  - run: sudo apt-get install -y libreoffice
  - permissions: contents: write   (for GitHub push)
"""

import os, sys, re, json, time, io, base64, logging, requests, subprocess, tempfile
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
# 3 PROJECT POOL
#
# Only 2 appear on the resume (selected by domain).
# All 3 exist on GitHub.
#
# PROJECT 1 — grc_risk
#   GRC, risk management, compliance, IT audit, vendor risk, AML, KYC, fraud,
#   data privacy. Covers white-collar security and BFSI-adjacent roles.
#   Dynamic: swaps in specific compliance tools, regulations, AML platforms.
#
# PROJECT 2 — soc_lab  
#   SOC, SIEM, network security, VAPT, vulnerability management, endpoint.
#   The "chameleon" project: tool line changes per JD (Splunk→QRadar, etc.)
#   Dynamic: swaps in whatever SIEM/scanner/EDR the JD mentions.
#
# PROJECT 3 — cloud_intel
#   Cloud security, threat intelligence, DFIR, AppSec/DevSecOps, automation.
#   Covers modern security engineering roles.
#   Dynamic: swaps in Azure/GCP/specific cloud tools per JD.
# ─────────────────────────────────────────────────────────────────────────────

PROJECTS = {
    "grc_risk": {
        "title":   "Unified GRC & Financial Crime Risk Platform",
        "github":  "https://github.com/Akshay-dot-P/grc-risk-platform",
        # Base tools always shown. LLM picks from swappable to replace/augment.
        "tech_base": ["Python", "NIST CSF", "ISO 27001", "PCI-DSS", "GDPR/PDPB"],
        # If JD mentions these keywords, swap in the corresponding tools
        "tech_swappable": {
            "aml|anti-money laundering|transaction monitoring|fincrime|fraud":
                ["AML simulation", "transaction monitoring"],
            "kyc|customer due diligence|cdd|onboarding":
                ["KYC automation", "CDD workflows"],
            "vendor|tprm|third.party|supply chain":
                ["vendor risk scoring", "TPRM"],
            "privacy|gdpr|pdpb|dpo|consent":
                ["GDPR/PDPB", "consent management"],
            "sox|itgc|audit|cisa|cobit":
                ["ITGC controls", "audit automation"],
            "sebi|rbi|irdai|nbfc|bfsi":
                ["RBI compliance", "BFSI regulatory mapping"],
        },
        "bullets": [
            "Developed Python GRC compliance tool mapping controls to NIST CSF, ISO 27001, and PCI-DSS; auto-generates gap analysis reports scoring each framework domain and flagging non-compliant controls for remediation.",
            "Built automated vendor risk scoring module computing quantitative scores across 12 domains (data access, security posture, regulatory compliance) aligned with enterprise TPRM frameworks.",
            "Implemented statistical anomaly detection on financial datasets to flag AML indicators including structuring, velocity abuse, and dormant account reactivation — simulating transaction monitoring analyst workflows.",
        ],
    },

    "soc_lab": {
        "title":   "Adaptive Security Operations & Vulnerability Intelligence Lab",
        "github":  "https://github.com/Akshay-dot-P/soc-vuln-lab",
        "tech_base": ["Splunk", "Wireshark", "Nmap", "Python", "MITRE ATT&CK"],
        "tech_swappable": {
            "qradar|ibm qradar":                    ["QRadar"],
            "elastic|kibana|elk":                   ["Elastic SIEM"],
            "sentinel|azure sentinel|microsoft sentinel": ["Azure Sentinel"],
            "chronicle|google siem":                ["Chronicle SIEM"],
            "crowdstrike|falcon":                   ["CrowdStrike Falcon"],
            "carbon black|vmware carbon":           ["Carbon Black"],
            "defender|microsoft defender|mde":      ["Microsoft Defender"],
            "nessus|tenable":                       ["Nessus"],
            "qualys":                               ["Qualys"],
            "openvas":                              ["OpenVAS"],
            "suricata|snort|zeek|ids|ips":          ["Suricata IDS"],
            "burp suite|burp|owasp|appsec|web app": ["Burp Suite"],
            "metasploit|exploit|pentest|vapt":      ["Metasploit"],
        },
        "bullets": [
            "Deployed Splunk SIEM with SPL correlation searches for brute-force detection, lateral movement, and privilege escalation; mapped TTPs to MITRE ATT&CK (T1110, T1078, T1059) and wrote structured PICERL incident report.",
            "Conducted TCP/IP traffic analysis with Wireshark — isolated HTTP/DNS flows, detected plaintext credential exposure, and identified SYN scan and DNS tunnelling patterns indicative of C2 activity.",
            "Integrated Nessus and OpenVAS APIs in Python to automate CVE scanning, classify findings by CVSS severity, and generate remediation reports; implemented delta-scan logic to track patch compliance over time.",
        ],
    },

    "cloud_intel": {
        "title":   "Cloud-Native Threat Intelligence & Security Automation Framework",
        "github":  "https://github.com/Akshay-dot-P/cloud-threat-intel",
        "tech_base": ["Python", "AWS", "boto3", "VirusTotal API", "MITRE ATT&CK"],
        "tech_swappable": {
            "azure|microsoft azure|azure security":      ["Azure Security Center", "Azure"],
            "gcp|google cloud|gcp security":             ["GCP Security Command Center"],
            "prisma|wiz|cspm":                           ["Prisma Cloud"],
            "elastic|kibana|elasticsearch":              ["Elastic SIEM"],
            "sentinel|azure sentinel":                   ["Azure Sentinel"],
            "volatility|memory forensics|dfir|forensics":["Volatility"],
            "autopsy|kape|disk forensics":               ["Autopsy"],
            "devsecops|ci.cd|pipeline|jenkins|github actions": ["DevSecOps pipeline"],
            "zap|owasp zap|dast":                        ["OWASP ZAP"],
            "bandit|semgrep|sast|secure code":           ["Semgrep SAST"],
            "trivy|container|docker|kubernetes|k8s":     ["Trivy container scanner"],
            "guardduty|cloudtrail|cloudwatch|aws security": ["GuardDuty", "CloudTrail"],
        },
        "bullets": [
            "Built AWS CSPM tool using boto3 auditing IAM over-privilege, unrotated access keys, public S3 misconfigurations, and disabled MFA; maps findings to CIS AWS Benchmark with prioritised remediation reports.",
            "Developed threat intelligence pipeline ingesting IOC feeds (VirusTotal, AbuseIPDB, MalwareBazaar) to auto-enrich SIEM alerts with malware classification, geolocation, and MITRE ATT&CK technique mapping.",
            "Automated DFIR triage using Volatility to extract running processes, network connections, and injected shellcode from memory dumps; correlated with Windows Event Logs to reconstruct attacker kill chain.",
        ],
    },
}

# Which 2 projects to show on the resume for each domain
DOMAIN_TO_PROJECTS = {
    "SOC":        ("soc_lab",    "cloud_intel"),
    "VAPT":       ("soc_lab",    "cloud_intel"),
    "AppSec":     ("cloud_intel","soc_lab"),
    "GRC":        ("grc_risk",   "soc_lab"),
    "Risk":       ("grc_risk",   "cloud_intel"),
    "Fraud-AML":  ("grc_risk",   "soc_lab"),
    "CloudSec":   ("cloud_intel","soc_lab"),
    "IAM":        ("cloud_intel","grc_risk"),
    "Forensics":  ("soc_lab",    "cloud_intel"),
    "Network":    ("soc_lab",    "cloud_intel"),
    "General":    ("soc_lab",    "grc_risk"),
}

AMAZON_BASE = [
    "Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring the structured alert triage and escalation workflow used in SOC Tier 1 analyst roles.",
    "Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated findings to senior reviewers demonstrating investigative instincts central to SOC and fraud analyst operations.",
    "Maintained audit-ready case documentation recording investigation findings, decisions, and corrective actions, establishing the evidence chain-of-custody discipline required for security incident reporting and IT audit.",
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence (shapes vocabulary, not just adds a line)
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_INTEL = {
    "wipro": {
        "framing":  "Wipro hires L1 SOC analysts for 24x7 SIEM monitoring shifts. Value: process adherence, SLA discipline, shift documentation rigour, escalation workflows.",
        "keywords": ["SIEM operations", "24x7 SOC", "SLA adherence", "shift documentation", "incident escalation"],
        "skills_first": ["Splunk", "SIEM", "alert triage", "incident escalation", "documentation"],
    },
    "tcs": {
        "framing":  "TCS values structured compliance and certification alignment. Hires for ISO 27001 ISMS, VAPT, and process-oriented security delivery.",
        "keywords": ["ISMS", "ISO 27001", "compliance audit", "vulnerability assessment", "risk register"],
        "skills_first": ["ISO 27001", "VAPT", "compliance", "audit documentation"],
    },
    "infosys": {
        "framing":  "Infosys values learning agility, documentation quality, and multi-client delivery adaptability.",
        "keywords": ["multi-client delivery", "documentation quality", "security delivery"],
        "skills_first": ["Python", "Splunk", "documentation", "compliance"],
    },
    "hcl": {
        "framing":  "HCL SecureCloud emphasises cloud-native security. Highlight AWS, cloud IAM, detection engineering.",
        "keywords": ["cloud security", "AWS security", "cloud IAM", "managed detection"],
        "skills_first": ["AWS", "cloud security", "Python", "SIEM", "IAM"],
    },
    "cognizant": {
        "framing":  "Cognizant hires for 24x7 SOC and BFSI compliance. Value: investigation rigour, documentation, BFSI frameworks.",
        "keywords": ["SOC operations", "BFSI security", "compliance monitoring"],
        "skills_first": ["SIEM", "alert triage", "compliance", "documentation"],
    },
    "capgemini": {
        "framing":  "Capgemini bridges GRC consulting and technical security for European clients. Value: translating technical findings into compliance documentation.",
        "keywords": ["GRC", "cloud security", "NIST", "compliance reporting"],
        "skills_first": ["GRC", "NIST CSF", "cloud security", "compliance"],
    },
    "deloitte": {
        "framing":  "Deloitte Cyber Risk Advisory: GRC consulting, ITGC audits, BFSI. Value: risk-in-business-terms communication, ITGC/SOX, client reports.",
        "keywords": ["cyber risk advisory", "ITGC", "SOX", "GRC consulting", "third-party risk"],
        "skills_first": ["GRC", "ITGC", "ISO 27001", "NIST CSF", "PCI-DSS", "audit documentation"],
    },
    "kpmg": {
        "framing":  "KPMG IT Advisory: heaviest ITGC/IS audit practice in India. Value: control testing, audit evidence, SOX/ITGC methodology. CISA valued.",
        "keywords": ["IT audit", "ITGC", "SOX", "IS audit", "CISA", "control testing"],
        "skills_first": ["IT audit", "ITGC", "ISO 27001", "PCI-DSS", "audit documentation"],
    },
    "pwc": {
        "framing":  "PwC Cyber Risk: regulatory landscape knowledge (RBI, SEBI, GDPR, PDPB), structured risk recommendations, advisory writing.",
        "keywords": ["cyber risk", "regulatory compliance", "data privacy", "GDPR", "PDPB", "RBI"],
        "skills_first": ["GRC", "data privacy", "GDPR", "NIST CSF", "regulatory compliance"],
    },
    "ey": {
        "framing":  "EY GDS Bangalore: GRC and IT audit delivery centre. Value: structured methodologies, standardised audit execution, international standards alignment.",
        "keywords": ["GRC", "IT audit", "risk assurance", "ITGC", "data protection"],
        "skills_first": ["IT audit", "GRC", "ISO 27001", "NIST CSF", "compliance"],
    },
    "jpmorgan": {
        "framing":  "JPMorgan Chase: technology risk, Basel III operational risk, AML/KYC operations. Value: financial services compliance, transaction monitoring instincts.",
        "keywords": ["technology risk", "operational risk", "AML", "transaction monitoring"],
        "skills_first": ["operational risk", "AML", "compliance", "audit documentation", "Python"],
    },
    "goldman sachs": {
        "framing":  "Goldman Sachs internal audit: ITGC, control testing, audit independence. Value: rigorous documentation, control-gap identification.",
        "keywords": ["technology audit", "ITGC", "internal audit", "control testing", "SOX"],
        "skills_first": ["IT audit", "ITGC", "SOX", "audit documentation", "compliance"],
    },
    "deutsche bank": {
        "framing":  "Deutsche Bank Bangalore: KYC, AML, information security for global banking. Value: investigative accuracy, AML/KYC process knowledge.",
        "keywords": ["KYC", "AML", "information security", "BFSI compliance", "transaction monitoring"],
        "skills_first": ["KYC", "AML", "compliance", "documentation", "transaction monitoring"],
    },
    "citi": {
        "framing":  "Citi: fraud detection, risk analytics, operational risk. Value: pattern recognition, Python/data skills, anomaly detection.",
        "keywords": ["risk analytics", "fraud detection", "transaction monitoring", "operational risk"],
        "skills_first": ["fraud detection", "Python", "risk assessment", "operational risk"],
    },
    "amazon": {
        "framing":  "Amazon: frame bullets through LP lens — Dive Deep, Bias for Action, Insist on Highest Standards, automation mindset, measurable outcomes.",
        "keywords": ["dive deep", "bias for action", "automation", "AWS", "security at scale"],
        "skills_first": ["Python", "AWS", "automation", "Bash", "cloud security", "SIEM"],
    },
    "google": {
        "framing":  "Google: technical depth, automation, systems thinking. Emphasise scripting, systematic repeatable processes, hands-on technical work.",
        "keywords": ["security engineering", "automation", "threat analysis", "scripting"],
        "skills_first": ["Python", "Bash", "Linux", "automation", "threat intelligence", "SIEM"],
    },
    "microsoft": {
        "framing":  "Microsoft: bridge identity, cloud, and security. Emphasise Azure, Active Directory, Sentinel/Defender. Growth mindset fits.",
        "keywords": ["Azure security", "Microsoft Sentinel", "identity security", "Active Directory"],
        "skills_first": ["Azure", "Active Directory", "cloud security", "IAM", "Python"],
    },
    "hdfc bank": {
        "framing":  "HDFC Bank: fraud detection, AML, RBI compliance. Value: transaction monitoring, audit rigour, RBI IT/Cyber framework awareness.",
        "keywords": ["fraud analytics", "AML", "RBI compliance", "IS audit", "transaction monitoring"],
        "skills_first": ["fraud detection", "AML", "compliance", "audit documentation"],
    },
    "bajaj finserv": {
        "framing":  "Bajaj Finserv: fraud/risk operations for large NBFC. Value: investigative approach, policy-based decisions, structured documentation.",
        "keywords": ["fraud operations", "IT risk", "NBFC compliance", "RBI", "investigation"],
        "skills_first": ["fraud detection", "risk assessment", "compliance", "documentation"],
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
# Dynamic tool selection
# Looks at JD skills and picks the most relevant swappable tools per project
# ─────────────────────────────────────────────────────────────────────────────

def select_project_tools(project_key: str, jd_skills: str, max_tools: int = 5) -> list[str]:
    """
    Given the JD skills text, figure out which swappable tools are relevant
    and return a merged tool list (base + relevant swappables) under max_tools.
    """
    proj         = PROJECTS[project_key]
    jd_lower     = jd_skills.lower()
    base         = list(proj["tech_base"])
    extra        = []

    for pattern, tools in proj["tech_swappable"].items():
        if re.search(pattern, jd_lower):
            for t in tools:
                if t not in base and t not in extra:
                    extra.append(t)

    # Merge: base first, then extras, truncate to max_tools
    combined = base + extra
    return combined[:max_tools]


# ─────────────────────────────────────────────────────────────────────────────
# Company web scraping (fallback)
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
            if href.startswith("http") and not any(
                    x in href for x in ["linkedin.com", "glassdoor.com", "indeed.com"]):
                pg   = requests.get(href, headers=_HEADERS, timeout=8)
                s2   = BeautifulSoup(pg.text, "html.parser")
                for tag in s2(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                main = s2.find("main") or s2.find("article") or s2
                text = " ".join(p.get_text(" ", strip=True)
                                for p in main.find_all("p") if len(p.get_text()) > 40)
                if len(text) > 100:
                    logger.info("  Scraped context (%d chars)", len(text))
                    return text[:900]
    except Exception as exc:
        logger.debug("Scrape failed: %s", exc)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# JSON repair
# ─────────────────────────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "",          raw.strip())
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")
    raw = re.sub(r",\s*([\}\]])", r"\1", raw)
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
    """
    Generate all placeholder values. The tools lists are pre-computed from
    JD keyword matching so the LLM gets concrete tool choices, not vague guidance.
    """
    p1     = PROJECTS[p1_key]
    p2     = PROJECTS[p2_key]
    p1_tl  = ", ".join(p1_tools)
    p2_tl  = ", ".join(p2_tools)

    if intel:
        co_ctx = (
            f"\nCOMPANY FRAMING: {intel['framing']}\n"
            f"Priority keywords to weave in naturally: {', '.join(intel['keywords'][:5])}\n"
            f"Skills to foreground: {', '.join(intel['skills_first'])}\n"
            f"Do NOT write a generic 'Eager to contribute to X' sentence.\n"
        )
    elif scraped_ctx:
        co_ctx = f"\nCOMPANY CONTEXT: {scraped_ctx[:500]}\nUse vocabulary signals naturally.\n"
    else:
        co_ctx = ""

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "You write ATS-optimised, factual, concise bullets that fit on one page. "
        "Never fabricate tools, certifications, or experience not in the source material. "
        "CRITICAL: Return ONLY a valid JSON object. "
        "All string values use double-quotes. Internal double-quotes MUST be escaped as \\\". "
        "No markdown fences. No comments. No trailing commas."
    )

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{co_ctx}
SINGLE-PAGE RULE: Every bullet must be 1-2 lines at 9.5pt. Max ~160 characters per bullet.

Return a JSON object with EXACTLY these 20 keys. All values are strings.

{{
  "SUMMARY": "2-3 sentences max. Start: Entry-level cybersecurity professional with hands-on experience in. Include top 3 job keywords. Use only real skills: Python, Bash, Linux, Splunk, Wireshark, Nmap, Burp Suite, Nessus, OpenVAS, MITRE ATT&CK, OWASP Top 10, NIST CSF, ISO 27001 (concepts), PCI-DSS (concepts), GDPR (concepts), TCP/IP, DNS, AWS basics, root cause analysis, audit documentation.",

  "SK_NET":  "Most job-relevant first. Source: TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
  "SK_OS":   "Most job-relevant first. Source: Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell (basic), Python, Bash",
  "SK_SIEM": "Most job-relevant first. Source: Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
  "SK_SOC":  "Most job-relevant first. Source: Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security, ticketing system workflows",
  "SK_FW":   "Most job-relevant first. Source: MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",

  "AMZ_B1": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[2]}",

  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{p1_tl}",
  "P1_B1": "Rewrite for this job domain using tools from P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][0]}",
  "P1_B2": "Rewrite for this job domain using tools from P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][1]}",
  "P1_B3": "Rewrite for this job domain using tools from P1_TECH where relevant (factual, max 160 chars): {p1['bullets'][2]}",

  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{p2_tl}",
  "P2_B1": "Rewrite for this job domain using tools from P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][0]}",
  "P2_B2": "Rewrite for this job domain using tools from P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][1]}",
  "P2_B3": "Rewrite for this job domain using tools from P2_TECH where relevant (factual, max 160 chars): {p2['bullets'][2]}"
}}

Rules:
- Every bullet opens with a past-tense action verb (Built, Developed, Conducted, Configured, Automated, Implemented)
- SK_* values: output skill VALUES only — no category label, no colon
- All strings: escape internal double-quotes with backslash
- Max 160 characters per bullet for single-page fit"""

    raw = _call_groq(system, user)
    raw = _repair_json(raw)

    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("  JSON parse failed (%s) — attempting repair...", exc)
        fixed = re.sub(
            r'("(?:SUMMARY|SK_\w+|AMZ_B\d|P[12]_(?:TITLE|TECH|B\d))":\s*)"(.*?)"(?=\s*[,}])',
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
        "SUMMARY", "SK_NET", "SK_OS", "SK_SIEM", "SK_SOC", "SK_FW",
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
# ─────────────────────────────────────────────────────────────────────────────

def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    """Replace placeholder that may be split across multiple runs."""
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
        raise FileNotFoundError("resume_template.docx not found in repo root.")
    doc = Document(str(TEMPLATE_PATH))
    replacements = {f"[[{k}]]": v for k, v in content.items()}
    for para in doc.paragraphs:
        full = "".join(r.text for r in para.runs)
        for ph, val in replacements.items():
            if ph in full:
                _replace_in_para(para, ph, val)
                full = full.replace(ph, val)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# PDF generation via LibreOffice (zero quota, no Drive, perfect formatting)
# LibreOffice must be installed in the workflow:
#   - run: sudo apt-get install -y libreoffice
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(docx_bytes: bytes) -> bytes:
    """
    Convert DOCX bytes to PDF using LibreOffice headless mode.
    LibreOffice renders the DOCX using the same engine as opening it in Writer,
    so formatting, fonts, and spacing are preserved exactly.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "resume.docx")
        pdf_path  = os.path.join(tmpdir, "resume.pdf")

        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf",
             "--outdir", tmpdir, docx_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice failed: {result.stderr[:300]}")

        if not os.path.exists(pdf_path):
            raise FileNotFoundError("LibreOffice did not produce resume.pdf")

        with open(pdf_path, "rb") as f:
            return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# GitHub storage — commits files to resumes/ folder, returns raw URLs
# ─────────────────────────────────────────────────────────────────────────────

def _safe(s: str, n: int = 35) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)[:n]


def _github_commit(filename: str, file_bytes: bytes, commit_message: str) -> str:
    """Commit a file to resumes/ folder and return its raw download URL."""
    path    = f"{RESUMES_FOLDER}/{filename}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    headers = {
        "Authorization":        f"Bearer {GITHUB_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Check if file already exists (need sha to update)
    sha     = None
    existing = requests.get(api_url, headers=headers, timeout=10)
    if existing.status_code == 200:
        sha = existing.json().get("sha")

    payload = {
        "message": commit_message,
        "content": base64.b64encode(file_bytes).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{GITHUB_BRANCH}/{path}"


def upload_to_github(docx_bytes: bytes, pdf_bytes: bytes,
                     job: dict) -> tuple[str, str]:
    """Upload both DOCX and PDF. Returns (docx_url, pdf_url)."""
    base    = f"Resume_{_safe(job['job_title'])}_{_safe(job['company'])}"
    msg     = f"Resume: {job['job_title']} @ {job['company']}"
    doc_url = _github_commit(f"{base}.docx", docx_bytes, msg)
    pdf_url = _github_commit(f"{base}.pdf",  pdf_bytes,  msg)
    logger.info("  GitHub: DOCX → %s.docx", base)
    logger.info("  GitHub: PDF  → %s.pdf",  base)
    return doc_url, pdf_url


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
    Returns jobs where:
    - status = "New"  → generate resume BEFORE applying (not after)
    - resume_doc_link is empty  → not already generated
    Skips: applied, rejected, not_relevant, offer, interview
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

    creds     = _get_creds()
    gc        = gspread.authorize(creds)
    ws        = gc.open(SHEET_NAME).sheet1
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
            # 1. Select 2 projects based on domain
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_lab", "grc_risk"))
            logger.info("  Projects: %s + %s", p1_key, p2_key)

            # 2. Compute relevant tools from JD skills for each project
            jd_skills   = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools    = select_project_tools(p1_key, jd_skills)
            p2_tools    = select_project_tools(p2_key, jd_skills)
            logger.info("  P1 tools: %s", ", ".join(p1_tools))
            logger.info("  P2 tools: %s", ", ".join(p2_tools))

            # 3. Company intelligence
            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company_context(job["company"])

            # 4. Generate all placeholder content via Groq
            logger.info("  Generating content via Groq...")
            content  = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                        p1_tools, p2_tools)
            logger.info("  Content generated.")

            # 5. Fill the DOCX template
            docx_bytes = fill_template(content)
            logger.info("  Template filled (%d bytes).", len(docx_bytes))

            # 6. Generate PDF via LibreOffice
            logger.info("  Generating PDF via LibreOffice...")
            pdf_bytes  = generate_pdf(docx_bytes)
            logger.info("  PDF generated (%d bytes).", len(pdf_bytes))

            # 7. Commit both to GitHub resumes/ folder
            doc_url, pdf_url = upload_to_github(docx_bytes, pdf_bytes, job)

            # 8. Write both links to sheet
            ws.update_cell(job["row_num"], doc_col, doc_url)
            ws.update_cell(job["row_num"], pdf_col, pdf_url)
            logger.info("  ✓ Sheet updated.")

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
