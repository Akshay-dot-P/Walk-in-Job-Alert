"""
resume_tailor.py — Research Framework Edition
==============================================
Generates ATS-optimised tailored DOCX+PDF resumes and measures how different
keyword strategies affect ATS scores and recruiter perception.

FEATURES
A. Bug fixes: & → and  |  Fraud-AML project fix  |  soft char limit
B. Feature 1: extract_keywords(jd_text) → {tools, concepts, actions, ranked}
C. Feature 2: SYNONYM_MAP + apply_synonyms() — safe post-generation expansion
D. Feature 3: track_keyword_usage() — 2-3x coverage tracking
E. Feature 4: dynamic_skills_augment() — JD keywords filtered via whitelist
F. Feature 5: compute_metrics() → keyword_coverage, keyword_density, skills_count
G. Feature 6: recruiter_simulate() → credibility, stuffing_suspicion, hireability
H. Single-page: enforce_single_page() — 5-tier STRICT single-page enforcement
   + page-fill via binary-search spacing expansion

AMZ_B4 LOGIC (key fix in this version):
  - Template has [[AMZ_B4]] at paragraph 16.
  - LLM generates AMZ_B4 dynamically based on job domain (14 keys total).
  - enforce_single_page() tries with 4 bullets first.
  - If overflow: AMZ_B4 is the FIRST thing removed (before any project bullet).
  - So 4 bullets when space allows, 3 when it doesn't. Never a raw placeholder.

ADD TO requirements.txt:
  python-docx==1.1.2
  beautifulsoup4==4.12.3
  google-api-python-client==2.108.0
  pikepdf>=8.0
  pdfminer.six>=20221105

WORKFLOW env:
  VALIDATION_MODE: normal   # lenient | normal | strict
"""

import os, sys, re, json, time, io, base64, logging, requests, subprocess, tempfile, copy
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
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
GROQ_GEN_MODEL    = "llama-3.1-8b-instant"
GROQ_VAL_MODEL    = "llama-3.1-8b-instant"
GROQ_URL          = "https://api.groq.com/openai/v1/chat/completions"
MAX_JOBS_PER_RUN  = 10
TEMPLATE_PATH     = Path(__file__).parent / "resume_template.docx"
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_BRANCH     = os.environ.get("GITHUB_REF_NAME", "main")
RESUMES_FOLDER    = "resumes"
VALIDATION_MODE   = os.environ.get("VALIDATION_MODE", "normal").lower().strip()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2: SYNONYM MAP
# Hardcoded, grounded in Akshay's actual projects only. No LLM involved.
# apply_synonyms() appends aliases in parentheses — never replaces originals.
# ─────────────────────────────────────────────────────────────────────────────
SYNONYM_MAP = {
    "ioc enrichment":          ["threat intelligence"],
    "log analysis":            ["SIEM monitoring"],
    "alert triage":            ["incident triage"],
    "threat detection":        ["anomaly detection"],
    "false positive analysis": ["alert tuning"],
    "incident escalation":     ["escalation workflows"],
    "spl correlation":         ["detection engineering"],
    "soar":                    ["security orchestration and automation"],
    "sigma rules":             ["detection-as-code"],
    "mitre att&ck":            ["TTP mapping"],
    "cvss severity":           ["vulnerability prioritisation"],
    "epss scoring":            ["exploit probability scoring"],
    "patch compliance":        ["remediation tracking"],
    "owasp top 10":            ["web application security"],
    "iam":                     ["identity and access management"],
    "cloudtrail":              ["cloud audit logging"],
    "guardduty":               ["cloud threat detection"],
    "cloud misconfiguration":  ["cloud security posture management"],
    "virustotal api":          ["threat intelligence feeds"],
    "osint enrichment":        ["open-source intelligence"],
    "typosquatting":           ["brand impersonation detection"],
    "audit documentation":     ["audit trail"],
    "root cause analysis":     ["investigative analysis"],
    "compliance monitoring":   ["regulatory compliance"],
    "nist csf":                ["cybersecurity framework"],
    "transaction monitoring":  ["financial crime detection"],
}


def apply_synonyms(text: str) -> str:
    if not text:
        return text
    applied = 0
    for term, aliases in SYNONYM_MAP.items():
        if applied >= 2:
            break
        alias = aliases[0]
        if alias.lower() in text.lower():
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        def replacer(match, _applied=None):
            nonlocal applied
            if applied >= 2:
                return match.group(0)
            applied += 1
            return f"{match.group(0)} ({alias})"
        text, count = pattern.subn(replacer, text, count=1)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1: KEYWORD EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_keywords(jd_text: str) -> dict:
    if not jd_text or len(jd_text.strip()) < 30:
        return {"tools": [], "concepts": [], "actions": [], "ranked": []}
    system = "You are an ATS keyword analyst. Return ONLY valid JSON. No markdown."
    user = (
        f"Extract the top 10-15 most important keywords from this job description.\n"
        f"JD: {jd_text[:800]}\n\n"
        "Return raw JSON only:\n"
        '{"tools":["tool1","tool2"],"concepts":["concept1"],"actions":["action1"],'
        '"ranked":["highest_priority",...up_to_15]}'
    )
    try:
        raw  = _call_groq(system, user, GROQ_GEN_MODEL, max_tokens=300)
        data = json.loads(_repair_json(raw))
        logger.info("  Keywords extracted — top 5: %s", data.get("ranked", [])[:5])
        return data
    except Exception as exc:
        logger.warning("  Keyword extraction failed: %s", exc)
        return {"tools": [], "concepts": [], "actions": [], "ranked": []}


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 3: KEYWORD INJECTION CONTROL
# ─────────────────────────────────────────────────────────────────────────────
def track_keyword_usage(content: dict, ranked_keywords: list) -> dict:
    bullet_keys = [
        "AMZ_B1","AMZ_B2","AMZ_B3","AMZ_B4",
        "P1_B1","P1_B2","P1_B3",
        "P2_B1","P2_B2","P2_B3"
    ]
    all_text = " ".join(content.get(k, "") for k in bullet_keys)
    usage = {}
    for kw in ranked_keywords[:10]:
        pattern = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        usage[kw] = len(pattern.findall(all_text))
    under   = [k for k, c in usage.items() if c == 0]
    over    = [k for k, c in usage.items() if c > 3]
    present = sum(1 for c in usage.values() if c > 0)
    logger.info("  Keyword coverage: %d/%d present | under=%s over=%s",
                present, len(usage), under[:3], over[:2])
    return usage


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4: DYNAMIC SKILLS AUGMENTATION
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATE_GROUNDABLE = {
    "splunk","spl","siem","sigma rules","soar","wireshark","nmap",
    "mitre att&ck","ttp","picerl","incident response","brute force detection",
    "lateral movement","privilege escalation","ioc","virustotal","telegram bot",
    "log analysis","alert triage","threat detection",
    "nessus","openvas","cve","cvss","epss","nvd","owasp","sqli",
    "patch management","remediation","bash scripting","cron","api",
    "phishing","osint","abuseipdb","urlscan","whois","dns","typosquatting",
    "threat intelligence","ioc enrichment","domain analysis",
    "iam","cloudtrail","guardduty","boto3","aws","s3","cloud security",
    "cloud misconfiguration","least privilege","cspm",
    "cloud security posture","cloud access controls","zero trust",
    "root cause analysis","audit documentation","escalation","triage",
    "policy enforcement","investigation","chain of custody",
    "nist csf","iso 27001","pci-dss","gdpr","sox","itgc",
    "compliance monitoring","risk assessment","vendor risk",
    "transaction monitoring","aml","kyc","sanctions screening",
    "tcp/ip","http","firewall","ids","ips","endpoint security",
    "windows internals","linux","active directory","python","powershell",
    "cyber kill chain","pcap",
}


def dynamic_skills_augment(profile_skills: dict, jd_keywords: dict) -> dict:
    ranked = jd_keywords.get("ranked", []) + jd_keywords.get("tools", [])
    if not ranked:
        return profile_skills
    skills = dict(profile_skills)
    safe = []
    for kw in ranked[:15]:
        kl = kw.lower()
        if any(g in kl or kl in g for g in CANDIDATE_GROUNDABLE):
            if not any(kl in v.lower() for v in skills.values()):
                safe.append(kw)
    if safe:
        existing  = skills.get("SK_V5", "")
        additions = ", ".join(safe[:3])
        skills["SK_V5"] = f"{existing}, {additions}" if existing else additions
        logger.info("  Dynamic skills +%s", additions)
    return skills


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5: METRICS
# ─────────────────────────────────────────────────────────────────────────────
def compute_metrics(content: dict, jd_keywords: dict, ats_score) -> dict:
    ranked  = jd_keywords.get("ranked", [])
    bullets = [content.get(k, "") for k in
               ["AMZ_B1","AMZ_B2","AMZ_B3","AMZ_B4",
                "P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]]
    all_text = " ".join(bullets).lower()

    coverage = 0
    if ranked:
        hits     = sum(1 for kw in ranked[:10]
                       if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", all_text, re.IGNORECASE))
        coverage = round(hits / min(len(ranked), 10) * 100)

    nonempty = [b for b in bullets if b.strip()]
    density  = 0.0
    if nonempty and ranked:
        total   = sum(sum(1 for kw in ranked[:10] if kw.lower() in b.lower()) for b in nonempty)
        density = round(total / len(nonempty), 2)

    skill_vals   = [content.get(f"SK_V{i}", "") for i in range(1, 6)]
    skills_count = sum(len([x for x in v.split(",") if x.strip()]) for v in skill_vals)

    return {
        "ats_score":          ats_score,
        "keyword_coverage":   f"{coverage}%",
        "keyword_density":    str(density),
        "total_skills_count": str(skills_count),
    }


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 6: RECRUITER SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
def recruiter_simulate(content: dict, job: dict) -> dict:
    bullets = "\n".join(
        f"• {content.get(k, '')}"
        for k in ["AMZ_B1","AMZ_B2","AMZ_B3","AMZ_B4",
                  "P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]
        if content.get(k, "").strip()
    )
    skills = " | ".join(content.get(f"SK_V{i}", "") for i in range(1, 6))
    system = "You are an experienced India cybersecurity recruiter. Be direct. Return ONLY valid JSON."
    user   = (
        f"Role: {job['job_title']} at {job['company']}\n"
        f"Candidate: MCA grad, 1.5yr Amazon ops, 0 professional security experience.\n"
        f"Resume bullets:\n{bullets[:800]}\nSkills: {skills[:300]}\n\n"
        'Rate honestly: {"credibility":<1-10>,"stuffing_suspicion":<1-10>,"hireability":<1-10>,'
        '"explanation":"<one sentence each, max 200 chars total>"}'
    )
    try:
        raw  = _call_groq(system, user, GROQ_VAL_MODEL, max_tokens=200)
        data = json.loads(_repair_json(raw))
        logger.info("  Recruiter: credibility=%s stuffing=%s hireability=%s",
                    data.get("credibility"), data.get("stuffing_suspicion"), data.get("hireability"))
        return data
    except Exception as exc:
        logger.warning("  Recruiter sim failed: %s", exc)
        return {"credibility": "N/A", "stuffing_suspicion": "N/A", "hireability": "N/A", "explanation": ""}


# ─────────────────────────────────────────────────────────────────────────────
# SKILL PROFILES — dynamic labels AND values (SK_L1-5 + SK_V1-5)
# ─────────────────────────────────────────────────────────────────────────────
SKILL_PROFILES = {
    "soc_security": {
        "SK_L1": "SOC Operations",       "SK_V1": "Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2": "SIEM & Monitoring",    "SK_V2": "Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3": "Threat Intelligence",  "SK_V3": "MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4": "Systems & Networking", "SK_V4": "Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, firewall and IDS/IPS concepts",
        "SK_L5": "Automation",           "SK_V5": "Python, Bash (basic), regular expressions",
    },
    "soc_security_cloud": {
        "SK_L1": "SOC Operations",       "SK_V1": "Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2": "SIEM & Monitoring",    "SK_V2": "Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3": "Threat Intelligence",  "SK_V3": "MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4": "Systems & Networking", "SK_V4": "Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, IDS/IPS, AWS (IAM, CloudTrail, GuardDuty), cloud security posture",
        "SK_L5": "Automation",           "SK_V5": "Python, Bash (basic), boto3, regular expressions",
    },
    "networking_entry": {
        "SK_L1": "Networking",           "SK_V1": "TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
        "SK_L2": "OS & Scripting",       "SK_V2": "Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell, Python, Bash",
        "SK_L3": "SIEM & Tools",         "SK_V3": "Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
        "SK_L4": "Security Operations",  "SK_V4": "Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security",
        "SK_L5": "Frameworks",           "SK_V5": "MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",
    },
    "grc_risk_fraud": {
        "SK_L1": "GRC & Compliance",     "SK_V1": "NIST CSF, ISO 27001, PCI-DSS, GDPR/PDPB, SOX/ITGC, compliance monitoring",
        "SK_L2": "Risk & Audit",         "SK_V2": "Risk assessment, control testing, audit documentation, vendor risk, RCSA basics",
        "SK_L3": "Fraud & AML",          "SK_V3": "Transaction monitoring, AML typologies, KYC/CDD, sanctions screening",
        "SK_L4": "Systems & Tools",      "SK_V4": "Windows internals, Linux fundamentals, Python, Excel, SQL (basic), TCP/IP basics",
        "SK_L5": "Frameworks",           "SK_V5": "MITRE ATT&CK, OWASP Top 10, Incident Response (PICERL), audit trail documentation",
    },
}

DOMAIN_SKILL_PROFILE = {
    "SOC": "soc_security", "VAPT": "soc_security", "AppSec": "soc_security", "Forensics": "soc_security",
    "CloudSec": "soc_security_cloud", "IAM": "soc_security_cloud",
    "Network": "networking_entry",
    "GRC": "grc_risk_fraud", "Risk": "grc_risk_fraud", "Fraud-AML": "grc_risk_fraud",
    "General": "soc_security",
}


def compute_skills(domain: str) -> dict:
    return dict(SKILL_PROFILES.get(DOMAIN_SKILL_PROFILE.get(domain, "soc_security"),
                                   SKILL_PROFILES["soc_security"]))


# ─────────────────────────────────────────────────────────────────────────────
# 3 PROJECTS
# ─────────────────────────────────────────────────────────────────────────────
PROJECTS = {
    "soc_auto": {
        "title":  "SOC Automation and Threat Detection Lab",
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
        "title":  "Vulnerability Scanner and Patch Prioritization Engine",
        "github": "https://github.com/Akshay-dot-P/vuln-scanner",
        "tech_base": ["Python", "Bash", "Nessus", "OpenVAS", "NVD API", "CVSS/EPSS scoring"],
        "tech_swappable": {
            r"qualys":                            ["Qualys"],
            r"tenable":                           ["Tenable.io"],
            r"burp suite|burp|owasp|web app":     ["Burp Suite", "OWASP ZAP"],
            r"nmap|network scan":                 ["Nmap"],
            r"epss|exploit probability":          ["EPSS API (FIRST.org)"],
            r"sast|bandit|semgrep|secure code":   ["Semgrep SAST"],
            r"container|docker|trivy|kubernetes": ["Trivy container scanner"],
        },
        "bullets": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS REST APIs in Python; generates CVE reports classified by CVSS severity; implemented EPSS scoring from FIRST.org API to prioritise by actual exploit probability — a metric rarely used by freshers.",
            "Developed OWASP Top 10 automated web checker that sends crafted HTTP requests to detect injection, broken auth, and SSRF vulnerabilities; documented SQL injection exploit and parameterised query remediation.",
            "Automated scan scheduling via Bash and cron; built delta-scan logic to flag newly discovered CVEs and calculate remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) for patch compliance tracking.",
        ],
    },
    "phishing_osint": {
        "title":  "Phishing and OSINT Threat Intelligence Tool",
        "github": "https://github.com/Akshay-dot-P/phishing-osint-tool",
        "tech_base": ["Python", "VirusTotal API", "AbuseIPDB", "WHOIS", "Telegram bot", "DNS analysis"],
        "tech_swappable": {
            r"shodan|censys":                      ["Shodan API"],
            r"osint|open source intel|recon":      ["theHarvester"],
            r"phishing|url|domain|malicious link": ["URLScan.io"],
            r"fraud|aml|financial crime":          ["fraud pattern matching"],
            r"threat intel|cti|ioc|indicator":     ["MISP IOC feeds"],
            r"typosquat|brand|impersonat":         ["typosquatting detector"],
            r"email|spf|dkim|dmarc":               ["email header analyser"],
        },
        "bullets": [
            "Built multi-API threat intelligence pipeline: submits suspicious URLs/IPs to VirusTotal, AbuseIPDB, and URLScan.io simultaneously; cross-references WHOIS registration age, DNS records, and SSL details to produce a unified phishing probability score.",
            "Implemented typosquatting domain detector generating character-substitution variants of brand domains and checking live DNS resolution — catches brand-impersonation attacks before they reach threat feeds.",
            "Deployed Telegram bot interface enabling analysts to submit URLs for live IOC enrichment; supports bulk CSV input/output for incident response workflows and includes OSINT enrichment via theHarvester for domain profiling.",
        ],
    },
}

DOMAIN_TO_PROJECTS = {
    "SOC":       ("soc_auto",        "phishing_osint"),
    "VAPT":      ("vuln_scanner",    "soc_auto"),
    "AppSec":    ("vuln_scanner",    "soc_auto"),
    "GRC":       ("phishing_osint",  "vuln_scanner"),
    "Risk":      ("phishing_osint",  "vuln_scanner"),
    "Fraud-AML": ("phishing_osint",  "vuln_scanner"),   # fixed
    "CloudSec":  ("soc_auto",        "vuln_scanner"),
    "IAM":       ("soc_auto",        "phishing_osint"),
    "Forensics": ("soc_auto",        "phishing_osint"),
    "Network":   ("soc_auto",        "vuln_scanner"),
    "General":   ("soc_auto",        "vuln_scanner"),
}

# ─────────────────────────────────────────────────────────────────────────────
# AMAZON BULLETS
#
# B1-B3: Strong fixed base bullets. No apologetic "mirroring SOC" framing.
# B4: Dynamic — domain-specific instruction passed to the LLM.
#     The LLM generates a real bullet; this string is the instruction only.
#
# SINGLE-PAGE CONDITION:
#   enforce_single_page() tries with all 4 bullets first.
#   If overflow: AMZ_B4 is removed first (before any project bullet).
#   Result: 4 bullets when space allows, exactly 3 when it doesn't.
# ─────────────────────────────────────────────────────────────────────────────
AMAZON_BASE = [
    # B1 — structured triage discipline, quantified
    "Triaged 50+ weekly inventory cases by severity and policy eligibility; applied structured investigation logic, documented decisions per case, and escalated edge cases — sustaining systematic triage discipline across 80+ weeks of continuous high-volume operations.",

    # B2 — RCA and anomaly detection, quantified
    "Conducted root cause analysis on 20+ complex seller claims weekly; identified policy violations and anomalous reimbursement patterns; escalated structured findings with supporting evidence — maintaining 95%+ case accuracy under sustained operational pressure.",

    # B3 — audit documentation discipline
    "Maintained structured case documentation across 500+ investigations: capturing investigation findings, policy decisions, corrective actions, and audit trail — enabling retrospective review and evidence retrieval on demand for compliance and quality assurance.",

    # B4 — domain-dynamic instruction (LLM generates the actual bullet)
    # This string is passed as the instruction in the user prompt.
    # Do NOT show this string in any output — only the generated bullet is used.
    (
        "Generate a 4th Amazon work bullet relevant to the job domain. "
        "Choose ONE focus area from: (a) process compliance and regulatory adherence "
        "for GRC/Audit/Fraud roles; (b) cross-team escalation and risk communication "
        "for SOC/Security roles; (c) operational metrics and SLA management "
        "for general/management roles; (d) data pattern analysis and anomaly flagging "
        "for analytics/fraud/cloud roles. "
        "Write as a strong past-tense action bullet. Max 200 chars. "
        "Use 'and' not '&'. Do NOT say 'mirroring SOC' or compare to security roles."
    ),
]

# ─────────────────────────────────────────────────────────────────────────────
# CONCEPT SWAPPABLE
# ─────────────────────────────────────────────────────────────────────────────
CONCEPT_SWAPPABLE = {
    "soc_auto": {
        r"grc|compliance|audit|iso\s*27001|nist|sox|itgc": [
            "SIEM-based compliance monitoring and security audit log retention",
            "automated security control validation via SPL correlation searches",
        ],
        r"fraud|aml|kyc|transaction.?monitor|financial.?crime": [
            "transaction anomaly detection via log correlation and pattern matching",
            "automated suspicious activity alerting with severity-based escalation",
        ],
        r"cloud|aws|azure|gcp|iam|saas": [
            "cloud security event monitoring and IAM access anomaly detection",
        ],
        r"forensic|dfir|incident.?response|evidence": [
            "forensic-grade event timeline reconstruction from SIEM log artifacts",
        ],
        r"network|ids|ips|firewall|packet|intrusion": [
            "network intrusion detection via deep packet analysis and IDS alert correlation",
        ],
    },
    "vuln_scanner": {
        r"grc|compliance|audit|iso\s*27001|nist|pci|sox": [
            "vulnerability risk scoring mapped to compliance framework controls (PCI-DSS, NIST)",
            "audit-ready remediation tracking with SLA compliance evidence",
        ],
        r"devsecops|appsec|ci/?cd|sdlc|secure.?cod|sast|dast": [
            "application security testing integrated with development release cycles",
        ],
        r"cloud|aws|azure|container|docker|kubernetes": [
            "cloud infrastructure vulnerability assessment and misconfiguration detection",
        ],
    },
    "phishing_osint": {
        r"grc|compliance|audit|vendor.?risk|third.?party|due.?diligence": [
            "domain reputation scoring for third-party vendor risk assessment",
            "quantitative risk evidence generation from multi-source OSINT intelligence",
        ],
        r"fraud|aml|kyc|transaction|financial.?crime|sanctions": [
            "KYC domain-verification workflow: WHOIS age, registrar, DNS, and SSL cross-check",
        ],
        r"cti|threat.?intel|ioc|indicator|feed|hunt": [
            "IOC lifecycle management and multi-source threat intelligence correlation",
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# BULLET VARIANTS
# ─────────────────────────────────────────────────────────────────────────────
BULLET_VARIANTS = {
    "soc_auto": {
        "cloud_iam": [
            "Deployed Splunk SIEM with SPL correlation searches to monitor IAM anomalies including unauthorized privilege escalation (T1078) and suspicious cross-account access patterns; mapped cloud-relevant TTPs to MITRE ATT&CK and wrote PICERL incident report.",
            "Built automated cloud security detection pipeline: Python script ingests Splunk alerts for IAM policy violations, performs IOC enrichment via VirusTotal API, and dispatches Telegram notifications with severity classification.",
            "Developed Sigma-compatible detection rules for cloud-specific TTPs including credential abuse and lateral movement; performed network analysis in Wireshark to identify anomalous authentication and DNS traffic patterns.",
        ],
        "dfir_forensics": [
            "Deployed Splunk SIEM with SPL correlation searches for forensic event timeline reconstruction — tracked brute-force (T1110), credential misuse (T1078), and script-based execution (T1059) with full MITRE ATT&CK TTP mapping.",
            "Built automated evidence collection pipeline: Python script ingests Splunk alerts, performs IOC enrichment via VirusTotal API, and generates severity-classified incident packages with chain-of-custody documentation.",
            "Converted detection logic to Sigma rules for cross-SIEM forensic portability; performed deep packet inspection in Wireshark to reconstruct attack sequences including SYN scans, DNS tunnelling, and credential exposure.",
        ],
        "network_ids": [
            "Deployed Splunk SIEM with SPL correlation searches for network intrusion detection — brute-force (index=* failed | stats count by src_ip), lateral movement, and privilege escalation mapped to MITRE ATT&CK (T1110, T1078, T1059).",
            "Built automated network alert triage pipeline: Python script ingests Splunk IDS alerts, performs IOC enrichment via VirusTotal API, and dispatches Telegram notifications with severity classification.",
            "Wrote Sigma rules for enterprise network security; performed TCP/IP deep packet analysis in Wireshark to detect SYN scans, DNS tunnelling, port sweeps, and plaintext credential exposure.",
        ],
    },
    "vuln_scanner": {
        "devsecops_appsec": [
            "Built automated application security testing pipeline integrating Nessus and OpenVAS APIs in Python; generates vulnerability reports classified by CVSS severity with EPSS scoring from FIRST.org API for risk-based prioritization in development workflows.",
            "Developed OWASP Top 10 automated application security checker detecting injection, broken authentication, SSRF, and XSS; documented SQL injection exploit-to-remediation with parameterised query fixes.",
            "Automated security scan scheduling via Bash and cron; built delta-scan logic to flag newly introduced CVEs per release with remediation SLA deadlines (Critical=24hrs, High=7 days) for secure development lifecycle compliance.",
        ],
        "cloud_security": [
            "Built automated cloud infrastructure vulnerability assessment pipeline using Nessus and OpenVAS APIs in Python; generates CVE reports with CVSS severity and EPSS scoring from FIRST.org API to prioritize cloud misconfiguration risks.",
            "Developed automated security checker for cloud-hosted applications testing OWASP Top 10 vulnerabilities including injection, broken authentication, and SSRF; documented remediation workflows for cloud service misconfigurations.",
            "Automated vulnerability scan scheduling via Bash for continuous cloud security monitoring; built delta-scan logic to detect newly exposed CVEs with SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days).",
        ],
        "compliance_audit": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS APIs; generates audit-ready CVE reports classified by CVSS severity with EPSS scoring from FIRST.org API — providing quantitative risk evidence for compliance documentation.",
            "Developed OWASP Top 10 automated compliance checker validating web application security controls; documented vulnerability-to-remediation audit trails including SQL injection evidence and parameterised query fixes.",
            "Automated compliance scan scheduling via Bash; built delta-scan logic tracking remediation progress against SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) — generating audit evidence for patch compliance reporting.",
        ],
    },
    "phishing_osint": {
        "grc_risk_audit": [
            "Built multi-source risk assessment pipeline: submits vendor domains and IPs to VirusTotal, AbuseIPDB, and URLScan.io; cross-references WHOIS registration age, DNS records, and SSL certificate details for quantitative third-party risk scores.",
            "Implemented domain reputation assessment tool generating typosquatting variants of monitored domains and checking live DNS resolution — provides early warning for brand-impersonation risks in vendor and partner ecosystems.",
            "Deployed automated risk assessment interface via Telegram bot enabling analysts to submit domains for enrichment; supports bulk CSV input/output and includes OSINT enrichment via theHarvester for comprehensive domain profiling.",
        ],
        "fraud_aml": [
            "Built multi-API fraud intelligence pipeline: submits suspicious domains and IPs to VirusTotal, AbuseIPDB, and URLScan.io; cross-references WHOIS, DNS, and SSL details as part of KYC domain-verification workflow to produce fraud probability scores.",
            "Implemented typosquatting domain detector generating character-substitution variants of legitimate business domains — identifies brand-impersonation infrastructure used in financial fraud schemes before reaching threat feeds.",
            "Deployed Telegram bot interface for live suspicious entity enrichment; supports bulk CSV input/output and includes OSINT enrichment via theHarvester for domain profiling to support STR documentation.",
        ],
        "cti_threat_intel": [
            "Built multi-API cyber threat intelligence pipeline: submits IOCs to VirusTotal, AbuseIPDB, and URLScan.io; cross-references WHOIS, DNS, and SSL certificate details to produce unified threat confidence scores.",
            "Implemented typosquatting domain detector generating character-substitution variants of tracked infrastructure — provides proactive threat detection capability for infrastructure-based threat hunting.",
            "Deployed Telegram bot interface for real-time IOC enrichment; supports bulk CSV input/output and includes OSINT enrichment via theHarvester for comprehensive domain attribution.",
        ],
    },
}

DOMAIN_BULLET_VARIANT = {
    "SOC":       {},
    "VAPT":      {},
    "AppSec":    {"vuln_scanner": "devsecops_appsec"},
    "GRC":       {"vuln_scanner": "compliance_audit", "phishing_osint": "grc_risk_audit"},
    "Risk":      {"vuln_scanner": "compliance_audit", "phishing_osint": "grc_risk_audit"},
    "Fraud-AML": {"phishing_osint": "fraud_aml", "vuln_scanner": "compliance_audit"},
    "CloudSec":  {"soc_auto": "cloud_iam", "vuln_scanner": "cloud_security"},
    "IAM":       {"soc_auto": "cloud_iam", "phishing_osint": "cti_threat_intel"},
    "Forensics": {"soc_auto": "dfir_forensics", "phishing_osint": "cti_threat_intel"},
    "Network":   {"soc_auto": "network_ids"},
    "General":   {},
}


def get_project_bullets(project_key: str, domain: str) -> list[str]:
    variant_name = DOMAIN_BULLET_VARIANT.get(domain, {}).get(project_key)
    if variant_name:
        variants = BULLET_VARIANTS.get(project_key, {})
        if variant_name in variants:
            return variants[variant_name]
    return PROJECTS[project_key]["bullets"]


# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence
# ─────────────────────────────────────────────────────────────────────────────
COMPANY_INTEL = {
    "wipro":         {"framing": "24x7 SOC shifts, SLA discipline, shift documentation.",     "keywords": ["24x7 SOC", "SLA adherence", "shift documentation"]},
    "tcs":           {"framing": "ISO 27001 ISMS, VAPT, compliance delivery.",                "keywords": ["ISMS", "ISO 27001", "compliance audit"]},
    "infosys":       {"framing": "Multi-client delivery, documentation quality.",             "keywords": ["documentation quality", "multi-client"]},
    "hcl":           {"framing": "Cloud-native security, AWS, detection engineering.",        "keywords": ["cloud security", "AWS security"]},
    "cognizant":     {"framing": "24x7 SOC, BFSI compliance, investigation rigour.",          "keywords": ["SOC operations", "BFSI security"]},
    "capgemini":     {"framing": "GRC consulting, cloud security, European clients.",         "keywords": ["GRC", "NIST"]},
    "deloitte":      {"framing": "GRC consulting, ITGC/SOX audits, client risk reports.",    "keywords": ["cyber risk advisory", "ITGC", "SOX"]},
    "kpmg":          {"framing": "ITGC/IS audit. CISA valued. Control testing.",             "keywords": ["IT audit", "ITGC", "SOX"]},
    "pwc":           {"framing": "Cyber risk advisory. RBI, SEBI, GDPR, PDPB.",             "keywords": ["cyber risk", "regulatory compliance", "GDPR"]},
    "ey":            {"framing": "EY GDS IT audit and GRC delivery.",                        "keywords": ["GRC", "IT audit", "ITGC"]},
    "jpmorgan":      {"framing": "Technology risk, Basel III, AML/KYC operations.",          "keywords": ["technology risk", "AML", "operational risk"]},
    "goldman sachs": {"framing": "Internal tech audit, ITGC, control testing.",             "keywords": ["technology audit", "ITGC", "SOX"]},
    "deutsche bank": {"framing": "KYC, AML, information security.",                         "keywords": ["KYC", "AML", "transaction monitoring"]},
    "citi":          {"framing": "Fraud detection, risk analytics, anomaly detection.",      "keywords": ["fraud detection", "risk analytics"]},
    "amazon":        {"framing": "LP lens: Dive Deep, Bias for Action, automation mindset.", "keywords": ["dive deep", "automation", "AWS"]},
    "google":        {"framing": "Technical depth, automation, systems thinking.",          "keywords": ["security engineering", "automation"]},
    "microsoft":     {"framing": "Azure, AD, Sentinel. Growth mindset.",                    "keywords": ["Azure security", "Active Directory", "Zero Trust"]},
    "hdfc bank":     {"framing": "Fraud detection, AML, RBI compliance.",                   "keywords": ["AML", "RBI compliance", "fraud analytics"]},
    "bajaj finserv": {"framing": "Fraud/risk operations, NBFC compliance.",                 "keywords": ["fraud operations", "IT risk"]},
}


def get_company_intel(company_raw: str) -> dict | None:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip().lower()
    for key, intel in COMPANY_INTEL.items():
        if key in name or name in key:
            logger.info("  Company intel: %s", key)
            return intel
    return None


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


def select_concepts(project_key: str, jd_text: str, max_concepts: int = 3) -> list[str]:
    concept_map = CONCEPT_SWAPPABLE.get(project_key, {})
    jd_lower    = jd_text.lower()
    concepts    = []
    for pattern, phrases in concept_map.items():
        if re.search(pattern, jd_lower):
            for phrase in phrases:
                if phrase not in concepts:
                    concepts.append(phrase)
    return concepts[:max_concepts]


# ─────────────────────────────────────────────────────────────────────────────
# Company scraping / GitHub research
# ─────────────────────────────────────────────────────────────────────────────
_HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0 Safari/537.36"}


def scrape_company(company_raw: str) -> str:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip()
    if not name or name.lower() in ("unknown", ""):
        return ""
    try:
        q    = requests.utils.quote(f"{name} cybersecurity about mission")
        resp = requests.get(f"https://html.duckduckgo.com/html/?q={q}", headers=_HDRS, timeout=8)
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


def research_github_projects(domain: str, job_title: str) -> str:
    DOMAIN_SEARCH = {
        "SOC": "SOC automation SIEM detection lab",
        "VAPT": "vulnerability scanner CVE CVSS python",
        "GRC": "GRC compliance automation NIST ISO27001 python",
        "Risk": "risk management compliance python",
        "Fraud-AML": "AML transaction monitoring fraud detection python",
        "CloudSec": "cloud security AWS IAM audit python",
        "General": "cybersecurity portfolio entry level",
    }
    query   = DOMAIN_SEARCH.get(domain, "cybersecurity portfolio")
    encoded = requests.utils.quote(f"{query} language:Python stars:>2")
    url     = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&per_page=5"
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp  = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return "\n".join(
            f"{i.get('full_name','')} (⭐{i.get('stargazers_count',0)}): "
            f"{(i.get('description','') or '')[:80]} | topics: {', '.join(i.get('topics',[])[:5])}"
            for i in items[:4]
        )
    except Exception as exc:
        logger.debug("GitHub research failed: %s", exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# JSON repair + Groq
# ─────────────────────────────────────────────────────────────────────────────
def _repair_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "",          raw.strip())
    raw = raw.replace("\u201c", '"').replace("\u201d", '"')
    raw = raw.replace("\u2018", "'").replace("\u2019", "'")
    raw = re.sub(r",\s*([\}\]])", r"\1",  raw)
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
# Resume content generation
# Generates 14 keys including AMZ_B4 (domain-dynamic)
# ─────────────────────────────────────────────────────────────────────────────
def generate_content(job: dict, p1_key: str, p2_key: str,
                     intel: dict | None, scraped_ctx: str,
                     p1_tools: list, p2_tools: list,
                     jd_keywords: dict) -> dict:

    p1       = PROJECTS[p1_key]
    p2       = PROJECTS[p2_key]
    p1_bulls = get_project_bullets(p1_key, job["domain"])
    p2_bulls = get_project_bullets(p2_key, job["domain"])

    co_ctx = ""
    if intel:
        co_ctx = (f"\nCOMPANY FRAMING: {intel['framing']}\n"
                  f"Priority keywords: {', '.join(intel['keywords'][:4])}\n"
                  "Do NOT write 'Eager to contribute to X'.\n")
    elif scraped_ctx:
        co_ctx = f"\nCOMPANY CONTEXT: {scraped_ctx[:400]}\n"

    ranked  = jd_keywords.get("ranked", [])
    kw_hint = ""
    if ranked:
        kw_hint = (f"\nKEYWORD INJECTION: Weave these JD keywords naturally across bullets "
                   f"(target 2-3x total, max 2 per bullet): {', '.join(ranked[:8])}\n")

    _PROJ_DIFFERENTIATORS = {
        "soc_auto":       ["SPL query syntax (index=* failed | stats)", "MITRE TTP numbers (T1110/T1078/T1059)", "SOAR pipeline detail"],
        "vuln_scanner":   ["EPSS scoring", "FIRST.org API mention", "CVSS severity", "remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days)"],
        "phishing_osint": ["typosquatting detection detail", "multi-API cross-referencing (VirusTotal, AbuseIPDB, URLScan.io)", "WHOIS/DNS/SSL analysis detail"],
    }
    active_diffs = []
    for pk in set([p1_key, p2_key]):
        active_diffs.extend(_PROJ_DIFFERENTIATORS.get(pk, []))
    diff_instruction = (
        f"NEVER drop from project bullets: {', '.join(active_diffs)}.\n"
        "Only include technical details that belong to each specific project.\n"
    ) if active_diffs else ""

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "Bullets must be factual — never fabricate tools or experience. "
        "ALWAYS write 'and' not '&' in bullet text (except MITRE ATT&CK). "
        "Return ONLY a valid JSON object. Internal double-quotes escaped as \\\". "
        "No markdown fences. No comments. No trailing commas."
    )

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{co_ctx}{kw_hint}
SINGLE-PAGE PREFERENCE: Keep bullets concise (prefer under 200 chars).
{diff_instruction}
Return JSON with EXACTLY 14 keys:
{{
  "AMZ_B1": "Rewrite with 1-2 domain keywords, action verb, 'and' not '&'. Do NOT say 'mirroring SOC': {AMAZON_BASE[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords, action verb, 'and' not '&'. Do NOT compare to security roles: {AMAZON_BASE[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords, action verb, 'and' not '&'. Do NOT compare to security roles: {AMAZON_BASE[2]}",
  "AMZ_B4": "{AMAZON_BASE[3]}",
  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{', '.join(p1_tools)}",
  "P1_B1": "Rewrite using ONLY P1_TECH tools, preserve technical detail, 'and' not '&': {p1_bulls[0]}",
  "P1_B2": "Rewrite using ONLY P1_TECH tools, preserve technical detail, 'and' not '&': {p1_bulls[1]}",
  "P1_B3": "Rewrite using ONLY P1_TECH tools, preserve technical detail, 'and' not '&': {p1_bulls[2]}",
  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{', '.join(p2_tools)}",
  "P2_B1": "Rewrite using ONLY P2_TECH tools, preserve technical detail, 'and' not '&': {p2_bulls[0]}",
  "P2_B2": "Rewrite using ONLY P2_TECH tools, preserve technical detail, 'and' not '&': {p2_bulls[1]}",
  "P2_B3": "Rewrite using ONLY P2_TECH tools, preserve technical detail, 'and' not '&': {p2_bulls[2]}"
}}
Rules: action verb start | 'and' not '&' | escape internal quotes | each project uses ONLY its own technical details"""

    raw = _call_groq(system, user, GROQ_GEN_MODEL)
    raw = _repair_json(raw)
    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("  JSON parse failed (%s) — repairing...", exc)
        fixed = re.sub(
            r'("(?:AMZ_B\d|P[12]_(?:TITLE|TECH|B\d))":\s*)"(.*?)"(?=\s*[,}])',
            lambda m: m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"',
            raw, flags=re.DOTALL
        )
        content = json.loads(fixed)

    # All 14 keys required
    expected = [
        "AMZ_B1", "AMZ_B2", "AMZ_B3", "AMZ_B4",
        "P1_TITLE", "P1_TECH", "P1_B1", "P1_B2", "P1_B3",
        "P2_TITLE", "P2_TECH", "P2_B1", "P2_B2", "P2_B3",
    ]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM missing keys: {missing}")

    # Sanity check: AMZ_B4 must be a real generated bullet, not the instruction string
    b4 = content.get("AMZ_B4", "")
    if "Generate a 4th Amazon" in b4 or len(b4.strip()) < 20:
        logger.warning("  AMZ_B4 looks like instruction echo — regenerating...")
        content["AMZ_B4"] = content["AMZ_B3"]  # fallback: use B3 text

    # Skill profile + dynamic augmentation
    base_skills = compute_skills(job["domain"])
    content.update(dynamic_skills_augment(base_skills, jd_keywords))

    # Synonym expansion on project bullets only
    for k in ["P1_B1", "P1_B2", "P1_B3", "P2_B1", "P2_B2", "P2_B3"]:
        if content.get(k):
            content[k] = apply_synonyms(content[k])

    return content


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_validation_output(data: dict) -> dict:
    return {
        "ats_score":        str(data.get("ats_score", "N/A")),
        "missing_keywords": str(data.get("missing_keywords", "")),
        "improvements":     str(data.get("improvements", "")),
        "github_insight":   str(data.get("github_insight", "")),
    }


def validate_resume(content: dict, job: dict, github_notes: str, mode: str) -> dict:
    EMPTY = {"ats_score": "skipped", "missing_keywords": "", "improvements": "", "github_insight": ""}
    if mode == "lenient":
        logger.info("  Validation: lenient — skipped")
        return EMPTY

    bullets = " | ".join(filter(None, [
        content.get("AMZ_B1", ""), content.get("AMZ_B2", ""),
        content.get("AMZ_B3", ""), content.get("AMZ_B4", ""),
        content.get("P1_B1", ""), content.get("P1_B2", ""),
        content.get("P2_B1", ""), content.get("P2_B2", ""),
    ]))

    if mode == "normal":
        prompt = (f"Job: {job.get('job_title','')} | JD keywords: {job.get('skills','')[:200]}\n"
                  f"Bullets: {bullets[:500]}\nATS review for 0-2yr cybersecurity candidate.\n"
                  "Return raw JSON: {\"ats_score\":<1-10>,\"missing_keywords\":\"<max 6>\"}")
        try:
            raw  = _call_groq("Return only valid JSON, no markdown.", prompt, GROQ_VAL_MODEL, max_tokens=150)
            data = _normalize_validation_output(json.loads(_repair_json(raw)))
            logger.info("  ATS=%s missing=%s", data["ats_score"], data["missing_keywords"][:50])
            return data
        except Exception as exc:
            logger.warning("  Validation failed: %s", exc)
            return EMPTY

    gh_sec = (f"\nSimilar GitHub projects:\n{github_notes[:500]}\n" if github_notes else "")
    prompt = (f"Job: {job.get('job_title','')} | Domain: {job.get('domain','')}\n"
              f"JD: {job.get('skills','')[:250]}\nBullets: {bullets[:600]}\n{gh_sec}"
              "Return raw JSON: {\"ats_score\":<1-10>,\"missing_keywords\":\"<max 8>\","
              "\"improvements\":\"<2 fixes>\",\"github_insight\":\"<1 thing>\"}")
    try:
        raw  = _call_groq("Strict ATS reviewer. Return only valid JSON.", prompt, GROQ_VAL_MODEL, max_tokens=300)
        data = _normalize_validation_output(json.loads(_repair_json(raw)))
        logger.info("  ATS=%s", data["ats_score"])
        return data
    except Exception as exc:
        logger.warning("  Validation failed: %s", exc)
        return EMPTY


# ─────────────────────────────────────────────────────────────────────────────
# DOCX fill — replaces placeholder in the specific w:t that holds it
# ─────────────────────────────────────────────────────────────────────────────
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    all_t = para._p.findall(f".//{{{W_NS}}}t")
    for t in all_t:
        if t.text and placeholder in t.text:
            t.text = t.text.replace(placeholder, replacement)
            if t.text and (t.text[0] == " " or t.text[-1] == " "):
                t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            return True
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
        raise FileNotFoundError("resume_template.docx not found.")
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
# PDF generation
# ─────────────────────────────────────────────────────────────────────────────
def generate_pdf(docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        docx_path = os.path.join(tmpdir, "resume.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf:writer_pdf_Export",
             "--outdir", tmpdir, docx_path],
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
# FEATURE H: Single-page enforcement — 5-tier relevancy-aware trimming
#
# TRIM ORDER for AMZ_B4:
#   AMZ_B4 is the first thing removed if the resume overflows.
#   This gives 4 bullets when space allows, exactly 3 when it doesn't.
#   Project bullets are only touched after AMZ_B4 is gone and still overflowing.
# ─────────────────────────────────────────────────────────────────────────────

def _count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        import pikepdf
        return len(pikepdf.open(io.BytesIO(pdf_bytes)).pages)
    except Exception as e:
        logger.warning("Page count failed: %s", e)
        return 999


def _score_bullet_relevancy(bullet_text: str, ranked_keywords: list) -> int:
    if not bullet_text or not ranked_keywords:
        return 0
    score = 0
    lower = bullet_text.lower()
    for kw in ranked_keywords[:10]:
        if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", lower, re.IGNORECASE):
            score += 1
    return score


def _shorten_bullet_llm(bullet_text: str, target_chars: int = 160) -> str:
    if not bullet_text or len(bullet_text) <= target_chars:
        return bullet_text
    system = (
        f"Shorten the bullet to under {target_chars} characters. "
        "PRESERVE: EPSS scoring, SPL query syntax, MITRE TTP numbers, SOAR detail, FIRST.org mention. "
        "Use 'and' not '&'. Return ONLY the shortened bullet, no quotes, no explanation."
    )
    user = f"Shorten: {bullet_text}"
    try:
        result = _call_groq(system, user, GROQ_GEN_MODEL, max_tokens=250)
        result = result.strip().strip('"')
        if len(result) > 20:
            logger.info("    Shortened %d→%d chars", len(bullet_text), len(result))
            return result
    except Exception as exc:
        logger.warning("    Bullet shortening failed: %s", exc)
    return bullet_text


def _trim_skills_line(skills_value: str, max_items: int = 4) -> str:
    if not skills_value:
        return skills_value
    items = [x.strip() for x in skills_value.split(",") if x.strip()]
    if len(items) <= max_items:
        return skills_value
    logger.info("    Skills trimmed: %d→%d items", len(items), max_items)
    return ", ".join(items[:max_items])


def _reduce_paragraph_spacing(docx_bytes: bytes) -> bytes:
    from docx.shared import Pt
    doc = Document(io.BytesIO(docx_bytes))
    for para in doc.paragraphs:
        pf   = para.paragraph_format
        text = para.text.strip()
        if not text:
            pf.space_before = Pt(0)
            pf.space_after  = Pt(0)
        elif para.style and para.style.name and 'Heading' in para.style.name:
            pf.space_before = Pt(2)
            pf.space_after  = Pt(0)
        else:
            if pf.space_before is None or pf.space_before > Pt(2):
                pf.space_before = Pt(1)
            if pf.space_after is None or pf.space_after > Pt(2):
                pf.space_after = Pt(0)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


_SECTION_TITLES = {"education", "work experience", "projects", "technical skills", "certifications"}


def _is_section_header(para) -> bool:
    return para.text.strip().lower() in _SECTION_TITLES


def _is_skill_row(para) -> bool:
    text = para.text.strip()
    if not text or len(text) < 5 or ":" not in text:
        return False
    label = text.split(":")[0].strip()
    return 3 <= len(label) <= 30


def _expand_spacing_to_fill_page(docx_bytes: bytes, extra_pts: float) -> bytes:
    from docx.shared import Pt
    doc = Document(io.BytesIO(docx_bytes))
    section_headers, skill_rows, bullet_rows, separator_rows = [], [], [], []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            separator_rows.append(para)
        elif _is_section_header(para):
            section_headers.append(para)
        elif _is_skill_row(para):
            skill_rows.append(para)
        elif para.style and para.style.name == "List Paragraph":
            bullet_rows.append(para)

    def pts(v):
        return 0.0 if not v else v / 12700.0

    h_share = extra_pts * 0.40 / max(len(section_headers), 1)
    s_share = extra_pts * 0.20 / max(len(skill_rows), 1)
    b_share = extra_pts * 0.20 / max(len(bullet_rows), 1)
    sep_sh  = extra_pts * 0.20 / max(len(separator_rows), 1)

    for p in section_headers:
        p.paragraph_format.space_before = Pt(pts(p.paragraph_format.space_before) + h_share)
    for p in skill_rows:
        p.paragraph_format.space_before = Pt(pts(p.paragraph_format.space_before) + s_share * 0.5)
        p.paragraph_format.space_after  = Pt(pts(p.paragraph_format.space_after)  + s_share * 0.5)
    for p in bullet_rows:
        p.paragraph_format.space_after  = Pt(pts(p.paragraph_format.space_after)  + b_share)
    for p in separator_rows:
        p.paragraph_format.space_before = Pt(pts(p.paragraph_format.space_before) + sep_sh * 0.5)
        p.paragraph_format.space_after  = Pt(pts(p.paragraph_format.space_after)  + sep_sh * 0.5)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _fill_page(docx_bytes: bytes) -> tuple[bytes, bytes]:
    pdf_bytes = generate_pdf(docx_bytes)
    if _count_pdf_pages(pdf_bytes) != 1:
        return docx_bytes, pdf_bytes
    trial_docx = _expand_spacing_to_fill_page(docx_bytes, 5.0)
    if _count_pdf_pages(generate_pdf(trial_docx)) > 1:
        logger.info("  Page fill: already near-full, no expansion")
        return docx_bytes, pdf_bytes
    lo, hi    = 0.0, 200.0
    best_docx = docx_bytes
    best_pdf  = pdf_bytes
    trial_docx = _expand_spacing_to_fill_page(docx_bytes, hi)
    if _count_pdf_pages(generate_pdf(trial_docx)) <= 1:
        logger.info("  Page fill: distributed 200pt, still fits")
        return trial_docx, generate_pdf(trial_docx)
    logger.info("  Page fill: binary searching (0-200pt)...")
    for _ in range(10):
        mid        = (lo + hi) / 2
        trial_docx = _expand_spacing_to_fill_page(docx_bytes, mid)
        trial_pdf  = generate_pdf(trial_docx)
        if _count_pdf_pages(trial_pdf) <= 1:
            lo, best_docx, best_pdf = mid, trial_docx, trial_pdf
        else:
            hi = mid
    logger.info("  Page fill complete: distributed %.1fpt", lo)
    return best_docx, best_pdf


def _generate_and_check(working: dict, reduce_spacing: bool = False) -> tuple[bytes, bytes, int]:
    docx_bytes = fill_template(working)
    if reduce_spacing:
        docx_bytes = _reduce_paragraph_spacing(docx_bytes)
    pdf_bytes = generate_pdf(docx_bytes)
    pages     = _count_pdf_pages(pdf_bytes)
    return docx_bytes, pdf_bytes, pages


def enforce_single_page(content: dict, job: dict,
                        jd_keywords: dict | None = None) -> tuple[bytes, bytes, str]:
    """
    Try with 4 Amazon bullets. If overflow, remove AMZ_B4 first (before any
    project bullet), then proceed through tiers. This guarantees 4 bullets
    when the page fits, exactly 3 when it doesn't, and never a raw placeholder.
    """
    ranked   = (jd_keywords or {}).get("ranked", [])
    trim_log = []
    working  = dict(content)

    # ── Initial check with all 4 Amazon bullets ───────────────────────────
    docx_bytes, pdf_bytes, pages = _generate_and_check(working)
    if pages <= 1:
        logger.info("  4 Amazon bullets fit — single page OK")
        docx_bytes, pdf_bytes = _fill_page(docx_bytes)
        return docx_bytes, pdf_bytes, "page-filled"

    logger.info("  %d pages — enforcing single page", pages)

    # ── Tier 0: Reduce spacing ────────────────────────────────────────────
    logger.info("  Tier 0: reducing spacing")
    docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
    trim_log.append("reduced-spacing")
    if pages <= 1:
        docx_bytes, pdf_bytes = _fill_page(docx_bytes)
        return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 0.5: Remove AMZ_B4 FIRST (before any project bullet) ────────
    # AMZ_B4 is the bonus bullet — it exists only when there's room.
    if working.get("AMZ_B4", "").strip():
        working["AMZ_B4"] = " "
        trim_log.append("removed AMZ_B4")
        logger.info("  Tier 0.5: removed AMZ_B4 (bonus bullet)")
        docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
        if pages <= 1:
            logger.info("  Single page achieved: 3 Amazon bullets. %s", trim_log)
            docx_bytes, pdf_bytes = _fill_page(docx_bytes)
            return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 1: Shorten long bullets (>200 chars) ─────────────────────────
    all_bullet_keys = ["AMZ_B1", "AMZ_B2", "AMZ_B3",
                       "P1_B1", "P1_B2", "P1_B3", "P2_B1", "P2_B2", "P2_B3"]
    long_bullets = sorted(
        [(k, len(working.get(k, ""))) for k in all_bullet_keys if len(working.get(k, "")) > 200],
        key=lambda x: x[1], reverse=True
    )
    if long_bullets:
        logger.info("  Tier 1: shortening %d long bullets", len(long_bullets))
        for key, length in long_bullets:
            working[key] = _shorten_bullet_llm(working[key], target_chars=150)
            trim_log.append(f"shortened {key} ({length}→{len(working[key])})")
        docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
        if pages <= 1:
            docx_bytes, pdf_bytes = _fill_page(docx_bytes)
            return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 1.5: Aggressive shortening (>150 chars) ──────────────────────
    still_long = sorted(
        [(k, len(working.get(k, ""))) for k in all_bullet_keys if len(working.get(k, "")) > 150],
        key=lambda x: x[1], reverse=True
    )
    if still_long:
        logger.info("  Tier 1.5: aggressive shortening %d bullets", len(still_long))
        for key, length in still_long:
            working[key] = _shorten_bullet_llm(working[key], target_chars=130)
            trim_log.append(f"agg-shortened {key}")
        docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
        if pages <= 1:
            docx_bytes, pdf_bytes = _fill_page(docx_bytes)
            return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 2: Remove least-relevant project bullet ──────────────────────
    # Note: AMZ_B4 already removed above. Only project bullets from here.
    PROJECT_KEYS = ["P1_B1", "P1_B2", "P1_B3", "P2_B1", "P2_B2", "P2_B3"]
    removable    = [k for k in PROJECT_KEYS if working.get(k, "").strip() and working[k].strip() != " "]
    if removable:
        scored = sorted(
            [(k, _score_bullet_relevancy(working[k], ranked)) for k in removable],
            key=lambda x: x[1]
        )
        for key, score in scored:
            working[key] = " "
            trim_log.append(f"removed {key} (score={score})")
            logger.info("  Tier 2: removed %s (score=%d)", key, score)
            docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
            if pages <= 1:
                docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 3: Trim excess skills ────────────────────────────────────────
    for sk_key in ["SK_V5", "SK_V4", "SK_V3", "SK_V2", "SK_V1"]:
        original = working.get(sk_key, "")
        if original and len(original.split(",")) > 3:
            working[sk_key] = _trim_skills_line(original, max_items=3)
            trim_log.append(f"trimmed {sk_key}")
            docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
            if pages <= 1:
                docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 4: Shorten Amazon bullets (last resort) ──────────────────────
    amz_sorted = sorted(
        [(k, len(working.get(k, ""))) for k in ["AMZ_B1", "AMZ_B2", "AMZ_B3"]
         if working.get(k, "").strip() and working[k].strip() != " "],
        key=lambda x: x[1], reverse=True
    )
    for key, length in amz_sorted:
        if length > 80:
            working[key] = _shorten_bullet_llm(working[key], target_chars=100)
            trim_log.append(f"shortened {key}")
            docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
            if pages <= 1:
                docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    logger.warning("  All tiers exhausted — keeping best result")
    docx_bytes, pdf_bytes, _ = _generate_and_check(working, reduce_spacing=True)
    return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; overflow-unresolved"


# ─────────────────────────────────────────────────────────────────────────────
# GitHub storage + URL shortening
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
    sha      = None
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


def shorten_url(long_url: str) -> str:
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
# Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_creds() -> Credentials:
    j = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not j:
        raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(json.loads(j), scopes=SCOPES)


def ensure_column(ws, name: str) -> int:
    headers = ws.row_values(1)
    if name not in headers:
        idx = len(headers) + 1
        ws.update_cell(1, idx, name)
        headers.append(name)
        logger.info("Added column '%s' at %d.", name, idx)
        return idx
    return headers.index(name) + 1


def get_pending_jobs(ws, doc_col: int) -> list[dict]:
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    headers = rows[0]
    col     = {h: i for i, h in enumerate(headers)}

    def _get(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    pending = []
    for row_num, row in enumerate(rows[1:], start=2):
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
    logger.info("Resume Tailor — Research Framework Edition (validation=%s)", VALIDATION_MODE)
    logger.info("=" * 60)

    for name, val in [("GROQ_API_KEY", GROQ_API_KEY),
                      ("GITHUB_TOKEN", GITHUB_TOKEN),
                      ("GITHUB_REPOSITORY", GITHUB_REPOSITORY)]:
        if not val:
            logger.error("%s not set.", name)
            sys.exit(1)

    if not TEMPLATE_PATH.exists():
        logger.error("resume_template.docx not found.")
        sys.exit(1)

    creds = _get_creds()
    gc    = gspread.authorize(creds)
    ws    = gc.open(SHEET_NAME).sheet1
    logger.info("Connected to Sheets.")

    doc_col   = ensure_column(ws, "resume_doc_link")
    pdf_col   = ensure_column(ws, "resume_pdf_link")
    val_col   = ensure_column(ws, "validation_notes")
    cov_col   = ensure_column(ws, "keyword_coverage")
    den_col   = ensure_column(ws, "keyword_density")
    sk_col    = ensure_column(ws, "total_skills_count")
    cred_col  = ensure_column(ws, "credibility")
    stuff_col = ensure_column(ws, "stuffing_suspicion")
    hire_col  = ensure_column(ws, "hireability")

    pending = get_pending_jobs(ws, doc_col)
    if not pending:
        logger.info("No New jobs with empty resume_doc_link.")
        sys.exit(0)

    logger.info("Found %d pending. Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-" * 50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)",
                    i, len(pending), job["job_title"], job["company"], job["domain"])
        try:
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_auto", "vuln_scanner"))
            jd_text  = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools = select_tools(p1_key, jd_text)
            p2_tools = select_tools(p2_key, jd_text)
            logger.info("  Projects: %s + %s | P1 tools: %s", p1_key, p2_key, p1_tools[:3])

            logger.info("  Extracting JD keywords...")
            jd_keywords = extract_keywords(jd_text)

            github_notes = ""
            if VALIDATION_MODE == "strict":
                github_notes = research_github_projects(job["domain"], job["job_title"])

            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company(job["company"])

            logger.info("  Generating content (14 keys inc. AMZ_B4)...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                       p1_tools, p2_tools, jd_keywords)

            track_keyword_usage(content, jd_keywords.get("ranked", []))

            if VALIDATION_MODE != "lenient":
                time.sleep(3)
            val_result = validate_resume(content, job, github_notes, VALIDATION_MODE)
            ats_score  = val_result.get("ats_score", "N/A")
            val_note   = (
                f"[{VALIDATION_MODE.upper()}] ATS:{ats_score}"
                + (f" | Missing:{val_result.get('missing_keywords','')}" if val_result.get("missing_keywords") else "")
                + (f" | Fix:{val_result.get('improvements','')}" if val_result.get("improvements") else "")
                + (f" | GitHub:{val_result.get('github_insight','')}" if val_result.get("github_insight") else "")
            )
            logger.info("  %s", val_note)

            metrics = compute_metrics(content, jd_keywords, ats_score)

            if VALIDATION_MODE != "lenient":
                time.sleep(2)
                rec_sim = recruiter_simulate(content, job)
            else:
                rec_sim = {"credibility": "skipped", "stuffing_suspicion": "skipped", "hireability": "skipped"}

            logger.info("  Enforcing single page...")
            docx_bytes, pdf_bytes, trim_log = enforce_single_page(content, job, jd_keywords)
            if trim_log and "page-filled" not in trim_log:
                val_note += f" | Trimmed:{trim_log}"
            logger.info("  DOCX: %d bytes  PDF: %d bytes", len(docx_bytes), len(pdf_bytes))

            doc_raw, pdf_raw = upload_to_github(docx_bytes, pdf_bytes, job)
            doc_url = shorten_url(doc_raw)
            pdf_url = shorten_url(pdf_raw)
            logger.info("  Doc: %s", doc_url)
            logger.info("  PDF: %s", pdf_url)

            ws.update_cell(job["row_num"], doc_col,   doc_url)
            ws.update_cell(job["row_num"], pdf_col,   pdf_url)
            ws.update_cell(job["row_num"], val_col,   val_note)
            ws.update_cell(job["row_num"], cov_col,   metrics["keyword_coverage"])
            ws.update_cell(job["row_num"], den_col,   metrics["keyword_density"])
            ws.update_cell(job["row_num"], sk_col,    metrics["total_skills_count"])
            ws.update_cell(job["row_num"], cred_col,  str(rec_sim.get("credibility", "")))
            ws.update_cell(job["row_num"], stuff_col, str(rec_sim.get("stuffing_suspicion", "")))
            ws.update_cell(job["row_num"], hire_col,  str(rec_sim.get("hireability", "")))
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
