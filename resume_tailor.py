"""
resume_tailor.py  ── Complete version with validation, smart skills, URL shortening.

WHAT'S NEW IN THIS VERSION:
  • Smart skill profiles — 3 sets selected by role type (SOC/Security, Networking/Entry,
    GRC/Risk/Fraud), combining both skill sets you provided
  • 4 Amazon bullets (restored, stronger)
  • Validation model: gemma2-9b-it (Groq free) — separate model = independent review
  • VALIDATION_MODE: lenient (0 calls) / normal (1 call, ATS score) /
    strict (2 calls: ATS review + GitHub similar-projects research)
  • URL shortening via TinyURL API (free, no key) — clean links in sheet
  • validation_notes column added to sheet

GROQ MODELS USED:
  Generator  : llama-3.1-8b-instant  (fast, free)
  Validator  : gemma2-9b-it           (different architecture = independent judgement)

SET IN resume_tailor.yml env section:
  VALIDATION_MODE: normal   # lenient | normal | strict

ADD TO requirements.txt:
  python-docx==1.1.2
  beautifulsoup4==4.12.3
  google-api-python-client==2.108.0
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
GROQ_GEN_MODEL    = "llama-3.1-8b-instant"       # resume generation — fast free model
GROQ_VAL_MODEL    = "llama-3.1-8b-instant"   # same model, separate call = still independent
# gemma2-9b-it removed: returns 400 on Groq free tier (context limit issues).
# llama-3.3-70b-versatile is on Groq free tier and gives better quality validation.
GROQ_URL          = "https://api.groq.com/openai/v1/chat/completions"
MAX_JOBS_PER_RUN  = 10
TEMPLATE_PATH     = Path(__file__).parent / "resume_template.docx"
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_BRANCH     = os.environ.get("GITHUB_REF_NAME", "main")
RESUMES_FOLDER    = "resumes"
# lenient=0 Groq calls  |  normal=1 call (ATS score)  |  strict=2 calls (full review + GitHub)
VALIDATION_MODE   = os.environ.get("VALIDATION_MODE", "normal").lower().strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─────────────────────────────────────────────────────────────────────────────
# SMART SKILL PROFILES
#
# Three sets selected by role type. Combines BOTH skill sets you provided.
# Selector logic is in compute_skills() below.
#
# PROFILE A — SOC / Security / Threat / DFIR / Network
#   Uses the "monitoring-forward" labels from your screenshot
#
# PROFILE B — Entry-level / Networking / Systems / Generalist
#   Uses the original "Networking / OS & Scripting / SIEM" format
#   Better for roles that explicitly ask for networking or infra background
#
# PROFILE C — GRC / Risk / Fraud / Compliance / Audit
#   Based on what LinkedIn/Reddit/actual resumes show for these roles
#   (Big4 consulting, BFSI compliance, IT audit freshers)
# ─────────────────────────────────────────────────────────────────────────────

# Each profile is a dict of 10 keys: SK_L1..SK_L5 (bold labels) + SK_V1..SK_V5 (plain values).
# Template now uses [[SK_L1]]: [[SK_V1]] etc. so labels are dynamic too.
SKILL_PROFILES = {
    # ── A: SOC / Security / Threat / VAPT / AppSec / Forensics ───────────────
    "soc_security": {
        "SK_L1": "SOC Operations",     "SK_V1": "Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2": "SIEM & Monitoring",  "SK_V2": "Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3": "Threat Intelligence","SK_V3": "MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4": "Systems & Networking","SK_V4": "Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, firewall and IDS/IPS concepts",
        "SK_L5": "Automation",         "SK_V5": "Python, Bash (basic), regular expressions",
    },
    # ── B: CloudSec / IAM — SOC profile + AWS in systems row ─────────────────
    "soc_security_cloud": {
        "SK_L1": "SOC Operations",     "SK_V1": "Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2": "SIEM & Monitoring",  "SK_V2": "Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3": "Threat Intelligence","SK_V3": "MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4": "Systems & Networking","SK_V4": "Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, IDS/IPS, AWS (IAM, CloudTrail, GuardDuty basics)",
        "SK_L5": "Automation",         "SK_V5": "Python, Bash (basic), boto3, regular expressions",
    },
    # ── C: Networking / Entry-level / Systems-forward ─────────────────────────
    # Labels and values both shift to networking-first ordering
    "networking_entry": {
        "SK_L1": "Networking",         "SK_V1": "TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
        "SK_L2": "OS & Scripting",     "SK_V2": "Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell, Python, Bash",
        "SK_L3": "SIEM & Tools",       "SK_V3": "Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
        "SK_L4": "Security Operations","SK_V4": "Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security",
        "SK_L5": "Frameworks",         "SK_V5": "MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",
    },
    # ── D: GRC / Risk / Compliance / Audit / Fraud / AML ─────────────────────
    # Labels and values both use GRC terminology.
    # Drawn from: Big4 JDs, LinkedIn GRC fresher posts, Reddit r/cybersecurity,
    # BFSI compliance JDs, CISA-track graduate resumes on GitHub.
    "grc_risk_fraud": {
        "SK_L1": "GRC & Compliance",   "SK_V1": "NIST CSF, ISO 27001, PCI-DSS, GDPR/PDPB, SOX/ITGC, compliance monitoring",
        "SK_L2": "Risk & Audit",       "SK_V2": "Risk assessment, control testing, audit documentation, vendor risk, RCSA basics",
        "SK_L3": "Fraud & AML",        "SK_V3": "Transaction monitoring, AML typologies, KYC/CDD, sanctions screening",
        "SK_L4": "Systems & Tools",    "SK_V4": "Windows internals, Linux fundamentals, Python, Excel, SQL (basic), TCP/IP basics",
        "SK_L5": "Frameworks",         "SK_V5": "MITRE ATT&CK, OWASP Top 10, Incident Response (PICERL), audit trail documentation",
    },
}

# Domain → profile mapping
DOMAIN_SKILL_PROFILE = {
    "SOC":        "soc_security",
    "VAPT":       "soc_security",
    "AppSec":     "soc_security",
    "Forensics":  "soc_security",
    "CloudSec":   "soc_security_cloud",
    "IAM":        "soc_security_cloud",
    "Network":    "networking_entry",
    "GRC":        "grc_risk_fraud",
    "Risk":       "grc_risk_fraud",
    "Fraud-AML":  "grc_risk_fraud",
    "General":    "soc_security",      # default to SOC for unknown roles
}


def compute_skills(domain: str) -> dict:
    """Return 10 skill keys (SK_L1..SK_L5 + SK_V1..SK_V5) for this domain.
    Both labels and values are dynamic — no hardcoded labels in template."""
    profile_key = DOMAIN_SKILL_PROFILE.get(domain, "soc_security")
    return dict(SKILL_PROFILES[profile_key])


# ─────────────────────────────────────────────────────────────────────────────
# 3 PROJECTS
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
            r"burp suite|burp|appsec|web app":             ["Burp Suite"],
            r"metasploit|exploit|pentest|vapt":            ["Metasploit"],
            r"volatility|memory forensics|dfir":           ["Volatility"],
            r"soar|playbook|automation":                   ["SOAR playbook"],
            r"grafana|dashboard":                          ["Grafana"],
            r"sysmon|evtx":                                ["Sysmon"],
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
            r"qualys":                               ["Qualys"],
            r"tenable":                             ["Tenable.io"],
            r"burp suite|burp|owasp|web app":       ["Burp Suite", "OWASP ZAP"],
            r"nmap|network scan":                   ["Nmap"],
            r"epss|exploit probability":            ["EPSS API (FIRST.org)"],
            r"sast|bandit|semgrep|secure code":     ["Semgrep SAST"],
            r"container|docker|trivy|kubernetes":   ["Trivy container scanner"],
        },
        "bullets": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS REST APIs in Python; generates CVE reports classified by CVSS severity; implemented EPSS scoring from FIRST.org API to prioritise by actual exploit probability — a metric rarely used by freshers.",
            "Developed OWASP Top 10 automated web checker that sends crafted HTTP requests to detect injection, broken auth, and SSRF vulnerabilities; documented SQL injection exploit and parameterised query remediation.",
            "Automated scan scheduling via Bash and cron; built delta-scan logic to flag newly discovered CVEs and calculate remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) for patch compliance tracking.",
        ],
    },

    "phishing_osint": {
        "title":  "Phishing & OSINT Threat Intelligence Tool",
        "github": "https://github.com/Akshay-dot-P/phishing-osint-tool",
        "tech_base": ["Python", "VirusTotal API", "AbuseIPDB", "WHOIS", "Telegram bot", "DNS analysis"],
        "tech_swappable": {
            r"shodan|censys":                        ["Shodan API"],
            r"osint|open source intel|recon":        ["theHarvester"],
            r"phishing|url|domain|malicious link":   ["URLScan.io"],
            r"fraud|aml|financial crime":            ["fraud pattern matching"],
            r"threat intel|cti|ioc|indicator":       ["MISP IOC feeds"],
            r"typosquat|brand|impersonat":           ["typosquatting detector"],
            r"email|spf|dkim|dmarc":                 ["email header analyser"],
            r"ip|geolocation|asn|reputation":        ["IPInfo API"],
        },
        "bullets": [
            "Built multi-API threat intelligence pipeline: submits suspicious URLs/IPs to VirusTotal, AbuseIPDB, and URLScan.io simultaneously; cross-references WHOIS registration age, DNS records, and SSL details to produce a unified phishing probability score.",
            "Implemented typosquatting domain detector generating character-substitution variants of brand domains and checking live DNS resolution — catches brand-impersonation attacks before they reach threat feeds.",
            "Deployed Telegram bot interface enabling analysts to submit URLs for live IOC enrichment; supports bulk CSV input/output for incident response workflows and includes OSINT enrichment via theHarvester for domain profiling.",
        ],
    },
}

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
    "Collaborated with compliance and risk teams to enforce regulatory policies, identify process control gaps, and implement corrective actions — building operational instincts directly applicable to GRC analyst, risk management, and compliance monitoring roles.",
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_INTEL = {
    "wipro":         {"framing": "24x7 SOC shifts. Value: SLA discipline, shift documentation, SIEM operations.",
                      "keywords": ["24x7 SOC", "SLA adherence", "shift documentation"],
                      "skills_first": ["Splunk", "SIEM", "alert triage"]},
    "tcs":           {"framing": "ISO 27001 ISMS, VAPT, compliance delivery.",
                      "keywords": ["ISMS", "ISO 27001", "compliance audit"],
                      "skills_first": ["ISO 27001", "VAPT", "compliance"]},
    "infosys":       {"framing": "Multi-client delivery, documentation quality.",
                      "keywords": ["documentation quality", "multi-client"],
                      "skills_first": ["Python", "Splunk", "documentation"]},
    "hcl":           {"framing": "Cloud-native security, AWS, detection engineering.",
                      "keywords": ["cloud security", "AWS security"],
                      "skills_first": ["AWS", "cloud security", "Python"]},
    "cognizant":     {"framing": "24x7 SOC, BFSI compliance, investigation rigour.",
                      "keywords": ["SOC operations", "BFSI security"],
                      "skills_first": ["SIEM", "alert triage", "compliance"]},
    "capgemini":     {"framing": "GRC consulting, cloud security, European clients.",
                      "keywords": ["GRC", "NIST", "compliance reporting"],
                      "skills_first": ["GRC", "NIST CSF", "cloud security"]},
    "deloitte":      {"framing": "GRC consulting, ITGC/SOX audits, client risk reports.",
                      "keywords": ["cyber risk advisory", "ITGC", "SOX"],
                      "skills_first": ["GRC", "ITGC", "ISO 27001", "audit documentation"]},
    "kpmg":          {"framing": "ITGC/IS audit practice. CISA valued. Control testing.",
                      "keywords": ["IT audit", "ITGC", "SOX", "CISA"],
                      "skills_first": ["IT audit", "ITGC", "audit documentation"]},
    "pwc":           {"framing": "Cyber risk advisory. Regulatory compliance (RBI, SEBI, GDPR, PDPB).",
                      "keywords": ["cyber risk", "regulatory compliance", "GDPR"],
                      "skills_first": ["GRC", "data privacy", "GDPR", "NIST CSF"]},
    "ey":            {"framing": "EY GDS IT audit and GRC delivery. Structured audit methodology.",
                      "keywords": ["GRC", "IT audit", "ITGC"],
                      "skills_first": ["IT audit", "GRC", "ISO 27001", "compliance"]},
    "jpmorgan":      {"framing": "Technology risk, Basel III, AML/KYC operations.",
                      "keywords": ["technology risk", "AML", "operational risk"],
                      "skills_first": ["operational risk", "AML", "compliance"]},
    "goldman sachs": {"framing": "Internal tech audit, ITGC, control testing.",
                      "keywords": ["technology audit", "ITGC", "SOX"],
                      "skills_first": ["IT audit", "ITGC", "SOX"]},
    "deutsche bank": {"framing": "KYC, AML, information security.",
                      "keywords": ["KYC", "AML", "transaction monitoring"],
                      "skills_first": ["KYC", "AML", "compliance"]},
    "citi":          {"framing": "Fraud detection, risk analytics, anomaly detection.",
                      "keywords": ["fraud detection", "risk analytics"],
                      "skills_first": ["fraud detection", "Python", "risk assessment"]},
    "amazon":        {"framing": "LP lens: Dive Deep, Bias for Action, automation mindset.",
                      "keywords": ["dive deep", "automation", "AWS"],
                      "skills_first": ["Python", "AWS", "automation", "Bash"]},
    "google":        {"framing": "Technical depth, automation, systems thinking.",
                      "keywords": ["security engineering", "automation"],
                      "skills_first": ["Python", "Bash", "Linux", "automation"]},
    "microsoft":     {"framing": "Azure, AD, Sentinel. Growth mindset.",
                      "keywords": ["Azure security", "Active Directory", "Zero Trust"],
                      "skills_first": ["Azure", "Active Directory", "cloud security"]},
    "hdfc bank":     {"framing": "Fraud detection, AML, RBI compliance.",
                      "keywords": ["AML", "RBI compliance", "fraud analytics"],
                      "skills_first": ["fraud detection", "AML", "compliance"]},
    "bajaj finserv": {"framing": "Fraud/risk operations, NBFC compliance.",
                      "keywords": ["fraud operations", "IT risk"],
                      "skills_first": ["fraud detection", "risk assessment"]},
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

_HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0 Safari/537.36"}


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
# GitHub research (strict mode only)
# Searches GitHub for cybersec projects from 0-2yr experience candidates,
# summarises what similar projects do well.
# ─────────────────────────────────────────────────────────────────────────────

def research_github_projects(domain: str, job_title: str) -> str:
    """
    Search GitHub for similar projects from entry-level / fresher candidates.
    Returns a short summary of patterns found.
    Only called in strict validation mode.
    """
    # Map domain to useful GitHub search terms
    DOMAIN_SEARCH = {
        "SOC":       "SOC automation SIEM detection lab",
        "VAPT":      "vulnerability scanner CVE CVSS python",
        "GRC":       "GRC compliance automation NIST ISO27001 python",
        "Risk":      "risk management compliance framework python",
        "Fraud-AML": "AML transaction monitoring fraud detection python",
        "CloudSec":  "cloud security AWS IAM audit python",
        "General":   "cybersecurity portfolio entry level",
    }
    query = DOMAIN_SEARCH.get(domain, "cybersecurity portfolio")
    encoded = requests.utils.quote(f"{query} language:Python stars:>2")
    url = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&per_page=5"

    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return ""

        notes = []
        for item in items[:4]:
            name    = item.get("full_name", "")
            desc    = item.get("description", "") or ""
            stars   = item.get("stargazers_count", 0)
            topics  = ", ".join(item.get("topics", [])[:5])
            notes.append(f"{name} (⭐{stars}): {desc[:100]} | topics: {topics}")

        return "\n".join(notes)
    except Exception as exc:
        logger.debug("GitHub research failed: %s", exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Groq helpers — two models, same interface
# ─────────────────────────────────────────────────────────────────────────────

def _repair_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "",          raw.strip())
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")
    raw = re.sub(r",\s*([\}\]])", r"\1",  raw)
    # Fix invalid JSON escapes: \& \# etc.
    raw = re.sub(r'\\([^"\\/bfnrtu])', r'\1', raw)
    return raw.strip()


def _call_groq(system: str, user: str, model: str,
               max_tokens: int = 2500, retries: int = 3) -> str:
    payload = {
        "model": model, "temperature": 0.15, "max_tokens": max_tokens,
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
    raise RuntimeError(f"Groq ({model}) failed after retries.")


# ─────────────────────────────────────────────────────────────────────────────
# Resume content generation (llama-3.1-8b-instant)
# ─────────────────────────────────────────────────────────────────────────────

def generate_content(job: dict, p1_key: str, p2_key: str,
                     intel: dict | None, scraped_ctx: str,
                     p1_tools: list, p2_tools: list) -> dict:
    p1 = PROJECTS[p1_key]
    p2 = PROJECTS[p2_key]

    co_ctx = ""
    if intel:
        co_ctx = (
            f"\nCOMPANY FRAMING: {intel['framing']}\n"
            f"Priority keywords: {', '.join(intel['keywords'][:4])}\n"
            f"Do NOT write 'Eager to contribute to X' — let keywords appear naturally.\n"
        )
    elif scraped_ctx:
        co_ctx = f"\nCOMPANY CONTEXT: {scraped_ctx[:400]}\n"

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "You write ATS-optimised, factual, concise bullets. "
        "Never fabricate tools or experience not in source material. "
        "Return ONLY a valid JSON object. "
        "Internal double-quotes MUST be escaped as \\\". "
        "The & character stays as & — never write \\&. "
        "No markdown fences. No comments. No trailing commas."
    )

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{co_ctx}
SINGLE-PAGE RULE: Each bullet max 160 characters (2 lines at 9.5pt Source Sans Pro).

Return JSON with EXACTLY these 13 keys:

{{
  "AMZ_B1": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords (factual, action verb, max 160 chars): {AMAZON_BASE[2]}",

  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{', '.join(p1_tools)}",
  "P1_B1": "Rewrite using tools in P1_TECH (factual, max 160 chars): {p1['bullets'][0]}",
  "P1_B2": "Rewrite using tools in P1_TECH (factual, max 160 chars): {p1['bullets'][1]}",
  "P1_B3": "Rewrite using tools in P1_TECH (factual, max 160 chars): {p1['bullets'][2]}",

  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{', '.join(p2_tools)}",
  "P2_B1": "Rewrite using tools in P2_TECH (factual, max 160 chars): {p2['bullets'][0]}",
  "P2_B2": "Rewrite using tools in P2_TECH (factual, max 160 chars): {p2['bullets'][1]}",
  "P2_B3": "Rewrite using tools in P2_TECH (factual, max 160 chars): {p2['bullets'][2]}"
}}

Rules:
- Every bullet opens with past-tense action verb (Built, Deployed, Triaged, Conducted)
- & stays as & — never \\&
- Escape internal double-quotes with backslash
- Max 160 chars per bullet"""

    raw = _call_groq(system, user, GROQ_GEN_MODEL)
    raw = _repair_json(raw)

    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("  JSON parse failed (%s) — attempting repair...", exc)
        fixed = re.sub(
            r'("(?:AMZ_B\d|P[12]_(?:TITLE|TECH|B\d))":\s*)"(.*?)"(?=\s*[,}])',
            lambda m: m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"',
            raw, flags=re.DOTALL
        )
        content = json.loads(fixed)
        logger.info("  JSON repair succeeded.")

    expected = [
        "AMZ_B1", "AMZ_B2", "AMZ_B3",
        "P1_TITLE", "P1_TECH", "P1_B1", "P1_B2", "P1_B3",
        "P2_TITLE", "P2_TECH", "P2_B1", "P2_B2", "P2_B3",
    ]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM response missing keys: {missing}")

    # Merge computed (fixed) skill values — never from LLM
    content.update(compute_skills(job["domain"]))
    return content


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION (gemma2-9b-it — different model = independent judgement)
#
# Three modes matching the VALIDATION_MODE env var:
#
#   lenient : 0 Groq calls — skip entirely, just log
#   normal  : 1 Groq call  — ATS score + missing keywords (~150 tokens)
#   strict  : 1 Groq call  — full review: ATS + missing kw + improvements
#             + GitHub project comparison (what similar repos did better)
#             Uses GitHub API research done before this call
#
# Why gemma2-9b-it?
#   Different training data and architecture than llama-3.1-8b.
#   A second model catching what the first one missed is closer to a real
#   independent ATS check. Both are on Groq free tier.
# ─────────────────────────────────────────────────────────────────────────────

def validate_resume(content: dict, job: dict,
                    github_notes: str, mode: str) -> dict:
    """
    Validate the generated resume content.

    mode=lenient → instant return, 0 Groq calls
    mode=normal  → 1 Groq call, ATS score + missing keywords
    mode=strict  → 1 Groq call, full ATS review + GitHub comparison
    """
    EMPTY = {"ats_score": "skipped", "missing_keywords": "", "improvements": "", "github_insight": ""}

    if mode == "lenient":
        logger.info("  Validation: lenient — skipped")
        return EMPTY

    # Build bullet text for the validator
    bullets = " | ".join(filter(None, [
        content.get("AMZ_B1",""), content.get("AMZ_B2",""),
        content.get("AMZ_B3",""),
        content.get("P1_B1",""), content.get("P1_B2",""),
        content.get("P2_B1",""), content.get("P2_B2",""),
    ]))
    title      = job.get("job_title", "")
    skills_req = job.get("skills", "")

    # ── NORMAL mode ───────────────────────────────────────────────────────────
    if mode == "normal":
        prompt = (
            f"Job title: {title}\n"
            f"JD keywords: {skills_req[:200]}\n"
            f"Resume bullets: {bullets[:500]}\n\n"
            "You are an ATS system. Score the keyword match and find gaps.\n"
            "Return raw JSON only — no markdown:\n"
            '{"ats_score":<1-10>,"missing_keywords":"<max 6 keywords comma-separated>"}'
        )
        try:
            raw  = _call_groq(
                "You are a strict ATS system. Return only valid JSON, no markdown.",
                prompt, GROQ_VAL_MODEL, max_tokens=150
            )
            data = json.loads(_repair_json(raw))
            data.setdefault("improvements", "")
            data.setdefault("github_insight", "")
            logger.info("  Validation (normal) ATS=%s missing=%s",
                        data.get("ats_score"), data.get("missing_keywords", "")[:60])
            return data
        except Exception as exc:
            logger.warning("  Validation failed: %s", exc)
            return EMPTY

    # ── STRICT mode ───────────────────────────────────────────────────────────
    github_section = ""
    if github_notes:
        github_section = (
            f"\nSimilar GitHub projects from entry-level developers:\n{github_notes[:600]}\n"
            "Based on these, note ONE thing they did better or differently.\n"
        )

    prompt = (
        f"Job: {title} | Domain: {job.get('domain','')}\n"
        f"JD keywords: {skills_req[:250]}\n"
        f"Resume bullets: {bullets[:600]}\n"
        f"{github_section}\n"
        "You are a strict ATS reviewer checking a fresher resume targeting 0-2yr roles.\n"
        "Return raw JSON only — no markdown:\n"
        "{\n"
        '  "ats_score": <1-10>,\n'
        '  "missing_keywords": "<max 8 comma-separated>",\n'
        '  "improvements": "<2 specific fixes max 200 chars>",\n'
        '  "github_insight": "<1 thing similar GitHub projects did better max 150 chars>"\n'
        "}"
    )
    try:
        raw  = _call_groq(
            "You are a strict ATS reviewer. Return only valid JSON, no markdown.",
            prompt, GROQ_VAL_MODEL, max_tokens=300
        )
        data = json.loads(_repair_json(raw))
        logger.info("  Validation (strict) ATS=%s missing=%s",
                    data.get("ats_score"), data.get("missing_keywords", "")[:60])
        return data
    except Exception as exc:
        logger.warning("  Validation failed: %s", exc)
        return EMPTY


# ─────────────────────────────────────────────────────────────────────────────
# DOCX template fill — replaces placeholder in the specific w:t that holds it
# ─────────────────────────────────────────────────────────────────────────────

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    """
    Replace placeholder in the specific w:t element containing it.
    Does NOT merge into the first w:t (which would cause bold-bleed into
    label runs). Works for runs nested inside w:hyperlink elements too.
    """
    all_t = para._p.findall(f".//{{{W_NS}}}t")

    # First pass: find the specific w:t with the full placeholder
    for t in all_t:
        if t.text and placeholder in t.text:
            t.text = t.text.replace(placeholder, replacement)
            if t.text and (t.text[0] == " " or t.text[-1] == " "):
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            return True

    # Fallback: placeholder split across multiple w:t (rare)
    full = "".join(t.text or "" for t in all_t)
    if placeholder not in full:
        return False
    new_text = full.replace(placeholder, replacement)
    if all_t:
        all_t[0].text = new_text
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
        full = "".join(t.text or "" for t in para._p.findall(f".//{{{W_NS}}}t"))
        for ph, val in replacements.items():
            if ph in full:
                _replace_in_para(para, ph, val)
                full = full.replace(ph, val)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# PDF via LibreOffice
# ─────────────────────────────────────────────────────────────────────────────

def generate_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "resume.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, docx_path],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice: {result.stderr[:200]}")
        pdf_path = os.path.join(tmpdir, "resume.pdf")
        if not os.path.exists(pdf_path):
            raise FileNotFoundError("LibreOffice did not produce resume.pdf")
        with open(pdf_path, "rb") as f:
            return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# URL shortening via TinyURL (free, no API key required)
# ─────────────────────────────────────────────────────────────────────────────

def shorten_url(long_url: str) -> str:
    """
    Shorten a URL using TinyURL's free API (no key, no signup).
    Falls back to original URL on any error.
    """
    try:
        resp = requests.get(
            f"https://tinyurl.com/api-create.php?url={requests.utils.quote(long_url)}",
            timeout=8
        )
        if resp.status_code == 200 and resp.text.startswith("https://tinyurl.com"):
            return resp.text.strip()
    except Exception:
        pass
    return long_url


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
    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        return []
    headers = all_rows[0]
    col     = {h: i for i, h in enumerate(headers)}

    def _get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

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
    logger.info("Resume Tailor started  (validation=%s)", VALIDATION_MODE)
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

    doc_col   = ensure_column(ws, "resume_doc_link")
    pdf_col   = ensure_column(ws, "resume_pdf_link")
    val_col   = ensure_column(ws, "validation_notes")

    pending = get_pending_jobs(ws, doc_col)
    if not pending:
        logger.info("No New jobs with empty resume_doc_link. Nothing to do.")
        sys.exit(0)

    logger.info("Found %d pending. Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-" * 50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)",
                    i, len(pending), job["job_title"], job["company"], job["domain"])
        try:
            # 1. Projects + tools
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_auto", "vuln_scanner"))
            jd_text  = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools = select_tools(p1_key, jd_text)
            p2_tools = select_tools(p2_key, jd_text)
            logger.info("  Projects: %s (%s) + %s (%s)",
                        p1_key, ", ".join(p1_tools[:3]),
                        p2_key, ", ".join(p2_tools[:3]))

            # 2. GitHub research — strict mode only (saves time + API calls)
            github_notes = ""
            if VALIDATION_MODE == "strict":
                logger.info("  Researching GitHub projects (strict mode)...")
                github_notes = research_github_projects(job["domain"], job["job_title"])
                if github_notes:
                    logger.info("  Found %d chars of GitHub context.", len(github_notes))

            # 3. Company intelligence
            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company(job["company"])

            # 4. Generate resume content
            logger.info("  Generating content via Groq (llama-3.1-8b-instant)...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                       p1_tools, p2_tools)
            logger.info("  Content generated.")

            # 5. Validate — using gemma2-9b-it (different model = independent review)
            if VALIDATION_MODE != "lenient":
                time.sleep(3)   # brief pause between Groq calls
            val_result = validate_resume(content, job, github_notes, VALIDATION_MODE)
            ats_score  = val_result.get("ats_score", "N/A")
            missing_kw = val_result.get("missing_keywords", "")
            improvs    = val_result.get("improvements", "")
            gh_insight = val_result.get("github_insight", "")

            val_note = (
                f"[{VALIDATION_MODE.upper()}] ATS:{ats_score}"
                + (f" | Missing:{missing_kw}" if missing_kw else "")
                + (f" | Fix:{improvs}" if improvs else "")
                + (f" | GitHub:{gh_insight}" if gh_insight else "")
            )
            logger.info("  %s", val_note)

            # 6. Fill template
            docx_bytes = fill_template(content)
            logger.info("  Template filled (%d bytes).", len(docx_bytes))

            # 7. Generate PDF
            logger.info("  Generating PDF via LibreOffice...")
            pdf_bytes = generate_pdf(docx_bytes)
            logger.info("  PDF generated (%d bytes).", len(pdf_bytes))

            # 8. Upload to GitHub
            doc_url_raw, pdf_url_raw = upload_to_github(docx_bytes, pdf_bytes, job)

            # 9. Shorten URLs for clean sheet display
            logger.info("  Shortening URLs...")
            doc_url = shorten_url(doc_url_raw)
            pdf_url = shorten_url(pdf_url_raw)
            logger.info("  Doc: %s", doc_url)
            logger.info("  PDF: %s", pdf_url)

            # 10. Write all to sheet
            ws.update_cell(job["row_num"], doc_col, doc_url)
            ws.update_cell(job["row_num"], pdf_col, pdf_url)
            ws.update_cell(job["row_num"], val_col, val_note)
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
