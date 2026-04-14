"""
resume_tailor.py — Research Framework Edition
==============================================
Generates ATS-optimised tailored DOCX+PDF resumes and measures how different
keyword strategies affect ATS scores and recruiter perception.

NEW FEATURES
A. Bug fixes: & → and  |  Fraud-AML project fix  |  soft char limit
B. Feature 1: extract_keywords(jd_text) → {tools, concepts, actions, ranked}
C. Feature 2: SYNONYM_MAP + apply_synonyms() — safe post-generation expansion
D. Feature 3: track_keyword_usage() — 2-3x coverage tracking
E. Feature 4: dynamic_skills_augment() — JD keywords filtered via whitelist
F. Feature 5: compute_metrics() → keyword_coverage, keyword_density, skills_count
G. Feature 6: recruiter_simulate() → credibility, stuffing_suspicion, hireability
H. Single-page: enforce_single_page() — 5-tier STRICT single-page enforcement
   + page-fill: measures bottom gap via pdfminer, distributes spacing to
     fully utilise the page (binary search on section/skill/bullet spacing)
   Tier 0: reduce paragraph spacing (non-destructive formatting)
   Tier 1: shorten long bullets (>200 chars) via LLM
   Tier 1.5: aggressive shortening (>150 chars)
   Tier 2: remove least-relevant project bullet by JD keyword score
   Tier 3: trim excess skills (SK_V5 → SK_V4 → SK_V1)
   Tier 4: shorten longest Amazon bullet (never remove)

CONFLICT NOTES (Feature 2 only — all others conflict-free)
Feature 2 had a partial conflict with "never fabricate" rule.
Resolution: SYNONYM_MAP is hardcoded and manually verified against Akshay's
actual projects. apply_synonyms() APPENDS aliases in parentheses — never replaces.
e.g. "IOC enrichment" → "IOC enrichment (threat intelligence)"
No LLM involved in synonym generation. Zero fabrication risk.

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
GROQ_VAL_MODEL    = "llama-3.1-8b-instant"   # same model, separate call = independent
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
# FEATURE 2: SYNONYM / SEMANTIC EXPANSION MAP
#
# SAFE: every entry is grounded in Akshay's actual project work.
# apply_synonyms() appends aliases in parentheses — never replaces originals.
# This is a static lookup — no LLM involved. Zero fabrication risk.
# ─────────────────────────────────────────────────────────────────────────────
SYNONYM_MAP = {
    # SOC / Detection — grounded in soc_auto project
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

    # VAPT — grounded in vuln_scanner project
    "cvss severity":           ["vulnerability prioritisation"],
    "epss scoring":            ["exploit probability scoring"],
    "patch compliance":        ["remediation tracking"],
    "owasp top 10":            ["web application security"],

    # Cloud/AWS — grounded in cloud project with boto3
    "iam":                     ["identity and access management"],
    "cloudtrail":              ["cloud audit logging"],
    "guardduty":               ["cloud threat detection"],
    "cloud misconfiguration":  ["cloud security posture management"],

    # OSINT / Phishing — grounded in phishing_osint project
    "virustotal api":          ["threat intelligence feeds"],
    "osint enrichment":        ["open-source intelligence"],
    "typosquatting":           ["brand impersonation detection"],

    # GRC / Audit — grounded in Amazon work experience
    "audit documentation":     ["audit trail"],
    "root cause analysis":     ["investigative analysis"],
    "compliance monitoring":   ["regulatory compliance"],
    "nist csf":                ["cybersecurity framework"],
    "transaction monitoring":  ["financial crime detection"],
}


def apply_synonyms(text: str) -> str:
    """
    Append one alias per matched term (max 2 per text).
    - Uses word boundaries to avoid partial matches
    - Preserves original casing
    - Prevents duplicate alias insertion
    """
    if not text:
        return text

    applied = 0

    for term, aliases in SYNONYM_MAP.items():
        if applied >= 2:
            break

        alias = aliases[0]

        # Skip if alias already present anywhere
        if alias.lower() in text.lower():
            continue

        # Regex with word boundaries (safe matching)
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)

        def replacer(match):
            nonlocal applied
            if applied >= 2:
                return match.group(0)

            applied += 1
            return f"{match.group(0)} ({alias})"

        # Replace only first occurrence
        text, count = pattern.subn(replacer, text, count=1)

        if count > 0:
            continue

    return text


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1: KEYWORD EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────
def extract_keywords(jd_text: str) -> dict:
    """
    Extract top 10-15 JD keywords structured by type.
    Returns: {"tools": [...], "concepts": [...], "actions": [...], "ranked": [...]}
    """
    if not jd_text or len(jd_text.strip()) < 30:
        return {"tools": [], "concepts": [], "actions": [], "ranked": []}

    system = "You are an ATS keyword analyst. Return ONLY valid JSON. No markdown."
    user = (
        f"Extract the top 10-15 most important keywords from this job description.\n"
        f"JD: {jd_text[:800]}\n\n"
        "Return raw JSON only:\n"
        '{"tools":["tool1","tool2"],'
        '"concepts":["concept1","concept2"],'
        '"actions":["action1","action2"],'
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
    """
    Count keyword appearances across all bullets using SAFE matching.
    - Uses word boundaries to avoid partial matches
    - Case-insensitive matching
    - Logs under (<1) and over (>3) usage
    """
    bullet_keys = [
        "AMZ_B1","AMZ_B2","AMZ_B3",
        "P1_B1","P1_B2","P1_B3",
        "P2_B1","P2_B2","P2_B3"
    ]

    # Combine all bullet text
    all_text = " ".join(content.get(k, "") for k in bullet_keys)

    usage = {}

    for kw in ranked_keywords[:10]:
        # SAFE regex with word boundaries
        pattern = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        matches = pattern.findall(all_text)
        usage[kw] = len(matches)

    # Analysis
    under = [k for k, c in usage.items() if c == 0]
    over  = [k for k, c in usage.items() if c > 3]
    present = sum(1 for c in usage.values() if c > 0)

    logger.info(
        "  Keyword coverage: %d/%d present | under=%s over=%s",
        present, len(usage), under[:3], over[:2]
    )

    return usage


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4: DYNAMIC SKILLS AUGMENTATION
# Candidate groundable whitelist — only these terms can be added from JD
# ─────────────────────────────────────────────────────────────────────────────
CANDIDATE_GROUNDABLE = {
    # soc_auto project
    "splunk","spl","siem","sigma rules","soar","wireshark","nmap",
    "mitre att&ck","ttp","picerl","incident response","brute force detection",
    "lateral movement","privilege escalation","ioc","virustotal","telegram bot",
    "log analysis","alert triage","threat detection",
    # vuln_scanner project
    "nessus","openvas","cve","cvss","epss","nvd","owasp","sqli",
    "patch management","remediation","bash scripting","cron","api",
    # phishing_osint project
    "phishing","osint","abuseipdb","urlscan","whois","dns","typosquatting",
    "threat intelligence","ioc enrichment","domain analysis",
    # cloud project (boto3 + AWS free tier)
    "iam","cloudtrail","guardduty","boto3","aws","s3","cloud security",
    "cloud misconfiguration","least privilege","cspm",
    "cloud security posture","cloud access controls","zero trust",
    # Amazon work experience
    "root cause analysis","audit documentation","escalation","triage",
    "policy enforcement","investigation","chain of custody",
    # GRC concepts (studied)
    "nist csf","iso 27001","pci-dss","gdpr","sox","itgc",
    "compliance monitoring","risk assessment","vendor risk",
    "transaction monitoring","aml","kyc","sanctions screening",
    # Foundational
    "tcp/ip","dns","http","firewall","ids","ips","endpoint security",
    "windows internals","linux","active directory","python","powershell",
    "cyber kill chain","osint enrichment","pcap",
}


def dynamic_skills_augment(profile_skills: dict, jd_keywords: dict) -> dict:
    """
    Append safe JD keywords to the Automation skill slot (SK_V5).
    Only adds terms present in CANDIDATE_GROUNDABLE and not already in skills.
    """
    ranked = jd_keywords.get("ranked", []) + jd_keywords.get("tools", [])
    if not ranked:
        return profile_skills
    skills = dict(profile_skills)
    safe   = []
    for kw in ranked[:15]:
        kl = kw.lower()
        if any(g in kl or kl in g for g in CANDIDATE_GROUNDABLE):
            if not any(kl in v.lower() for v in skills.values()):
                safe.append(kw)
    if safe:
        existing = skills.get("SK_V5","")
        additions = ", ".join(safe[:3])
        skills["SK_V5"] = f"{existing}, {additions}" if existing else additions
        logger.info("  Dynamic skills +%s", additions)
    return skills


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5: METRICS COLLECTION
# ─────────────────────────────────────────────────────────────────────────────
def compute_metrics(content: dict, jd_keywords: dict, ats_score) -> dict:
    ranked  = jd_keywords.get("ranked", [])
    bullets = [content.get(k,"") for k in
               ["AMZ_B1","AMZ_B2","AMZ_B3","P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]]
    all_text = " ".join(bullets).lower()

    coverage = 0
    if ranked:
        hits = sum(
           1 for kw in ranked[:10]
           if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", all_text, re.IGNORECASE)
        )        
    coverage = round(hits / min(len(ranked),10) * 100)

    nonempty = [b for b in bullets if b.strip()]
    density  = 0.0
    if nonempty and ranked:
        total = sum(sum(1 for kw in ranked[:10] if kw.lower() in b.lower()) for b in nonempty)
        density = round(total / len(nonempty), 2)

    skill_vals   = [content.get(f"SK_V{i}","") for i in range(1,6)]
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
    bullets = "\n".join(f"• {content.get(k,'')}" for k in
              ["AMZ_B1","AMZ_B2","AMZ_B3","P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]
              if content.get(k))
    skills  = " | ".join(content.get(f"SK_V{i}","") for i in range(1,6))
    system  = "You are an experienced India cybersecurity recruiter. Be direct. Return ONLY valid JSON."
    user    = (
        f"Role: {job['job_title']} at {job['company']}\n"
        f"Candidate: MCA grad, 1.5yr Amazon operations, 0 professional security experience.\n"
        f"Resume bullets:\n{bullets[:800]}\nSkills: {skills[:300]}\n\n"
        "Rate honestly:\n"
        '{"credibility":<1-10>,"stuffing_suspicion":<1-10>,"hireability":<1-10>,'
        '"explanation":"<one sentence each dimension, max 200 chars total>"}'
    )
    try:
        raw  = _call_groq(system, user, GROQ_VAL_MODEL, max_tokens=200)
        data = json.loads(_repair_json(raw))
        logger.info("  Recruiter: credibility=%s stuffing=%s hireability=%s",
                    data.get("credibility"), data.get("stuffing_suspicion"), data.get("hireability"))
        return data
    except Exception as exc:
        logger.warning("  Recruiter sim failed: %s", exc)
        return {"credibility":"N/A","stuffing_suspicion":"N/A","hireability":"N/A","explanation":""}


# ─────────────────────────────────────────────────────────────────────────────
# SKILL PROFILES — dynamic labels AND values (10 keys: SK_L1-5 + SK_V1-5)
# ─────────────────────────────────────────────────────────────────────────────
SKILL_PROFILES = {
    "soc_security": {
        "SK_L1":"SOC Operations",      "SK_V1":"Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2":"SIEM & Monitoring",   "SK_V2":"Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3":"Threat Intelligence", "SK_V3":"MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4":"Systems & Networking","SK_V4":"Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, firewall and IDS/IPS concepts",
        "SK_L5":"Automation",          "SK_V5":"Python, Bash (basic), regular expressions",
    },
    "soc_security_cloud": {
        "SK_L1":"SOC Operations",      "SK_V1":"Alert triage, incident investigation, log analysis, threat detection, escalation, false positive analysis",
        "SK_L2":"SIEM & Monitoring",   "SK_V2":"Splunk (SPL), Elastic SIEM (basic), Windows Event Logs, Sysmon, Wireshark",
        "SK_L3":"Threat Intelligence", "SK_V3":"MITRE ATT&CK, IOC analysis, VirusTotal, OSINT enrichment, Cyber Kill Chain",
        "SK_L4":"Systems & Networking","SK_V4":"Windows internals, Linux fundamentals, TCP/IP, DNS, HTTP/S, IDS/IPS, AWS (IAM, CloudTrail, GuardDuty), cloud security posture",
        "SK_L5":"Automation",          "SK_V5":"Python, Bash (basic), boto3, regular expressions",
    },
    "networking_entry": {
        "SK_L1":"Networking",          "SK_V1":"TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
        "SK_L2":"OS & Scripting",      "SK_V2":"Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell, Python, Bash",
        "SK_L3":"SIEM & Tools",        "SK_V3":"Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
        "SK_L4":"Security Operations", "SK_V4":"Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security",
        "SK_L5":"Frameworks",          "SK_V5":"MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",
    },
    "grc_risk_fraud": {
        "SK_L1":"GRC & Compliance",    "SK_V1":"NIST CSF, ISO 27001, PCI-DSS, GDPR/PDPB, SOX/ITGC, compliance monitoring",
        "SK_L2":"Risk & Audit",        "SK_V2":"Risk assessment, control testing, audit documentation, vendor risk, RCSA basics",
        "SK_L3":"Fraud & AML",         "SK_V3":"Transaction monitoring, AML typologies, KYC/CDD, sanctions screening",
        "SK_L4":"Systems & Tools",     "SK_V4":"Windows internals, Linux fundamentals, Python, Excel, SQL (basic), TCP/IP basics",
        "SK_L5":"Frameworks",          "SK_V5":"MITRE ATT&CK, OWASP Top 10, Incident Response (PICERL), audit trail documentation",
    },
}

DOMAIN_SKILL_PROFILE = {
    "SOC":"soc_security","VAPT":"soc_security","AppSec":"soc_security","Forensics":"soc_security",
    "CloudSec":"soc_security_cloud","IAM":"soc_security_cloud",
    "Network":"networking_entry",
    "GRC":"grc_risk_fraud","Risk":"grc_risk_fraud","Fraud-AML":"grc_risk_fraud",
    "General":"soc_security",
}


def compute_skills(domain: str) -> dict:
    return dict(SKILL_PROFILES.get(DOMAIN_SKILL_PROFILE.get(domain,"soc_security"),
                                   SKILL_PROFILES["soc_security"]))


# ─────────────────────────────────────────────────────────────────────────────
# 3 PROJECTS — Bug fix A: Fraud-AML → vuln_scanner not soc_auto
# Bug fix C: full canonical bullets, soft char limit
# ─────────────────────────────────────────────────────────────────────────────
PROJECTS = {
    "soc_auto": {
        "title": "SOC Automation and Threat Detection Lab",
        "github": "https://github.com/Akshay-dot-P/soc-threat-lab",
        "tech_base": ["Python","Splunk","Wireshark","Nmap","MITRE ATT&CK","Sigma rules"],
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
        "title": "Vulnerability Scanner and Patch Prioritization Engine",
        "github": "https://github.com/Akshay-dot-P/vuln-scanner",
        "tech_base": ["Python","Bash","Nessus","OpenVAS","NVD API","CVSS/EPSS scoring"],
        "tech_swappable": {
            r"qualys":                            ["Qualys"],
            r"tenable":                           ["Tenable.io"],
            r"burp suite|burp|owasp|web app":     ["Burp Suite","OWASP ZAP"],
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
        "title": "Phishing and OSINT Threat Intelligence Tool",
        "github": "https://github.com/Akshay-dot-P/phishing-osint-tool",
        "tech_base": ["Python","VirusTotal API","AbuseIPDB","WHOIS","Telegram bot","DNS analysis"],
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

# BUG FIX A: Fraud-AML → (phishing_osint, vuln_scanner) not soc_auto
DOMAIN_TO_PROJECTS = {
    "SOC":        ("soc_auto",       "phishing_osint"),
    "VAPT":       ("vuln_scanner",   "soc_auto"),
    "AppSec":     ("vuln_scanner",   "soc_auto"),
    "GRC":        ("phishing_osint", "vuln_scanner"),
    "Risk":       ("phishing_osint", "vuln_scanner"),
    "Fraud-AML":  ("phishing_osint", "vuln_scanner"),   # FIXED
    "CloudSec":   ("soc_auto",       "vuln_scanner"),
    "IAM":        ("soc_auto",       "phishing_osint"),
    "Forensics":  ("soc_auto",       "phishing_osint"),
    "Network":    ("soc_auto",       "vuln_scanner"),
    "General":    ("soc_auto",       "vuln_scanner"),
}

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2: CONCEPT SWAPPABLE — deterministic domain phrases for LLM prompt
#
# Each phrase describes something the project ACTUALLY does, framed for
# a specific domain. Regex patterns match JD text; matched phrases go
# straight into the Groq prompt as "weave 1-2 of these naturally."
# Zero fabrication — only reframing of real capabilities.
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
            "cross-account activity correlation for cloud-native threat detection",
        ],
        r"forensic|dfir|incident.?response|evidence|chain.?of.?custody": [
            "forensic-grade event timeline reconstruction from SIEM log artifacts",
            "automated evidence packaging with chain-of-custody documentation",
        ],
        r"network|ids|ips|firewall|packet|intrusion": [
            "network intrusion detection via deep packet analysis and IDS alert correlation",
            "protocol-level anomaly detection for network security monitoring",
        ],
    },
    "vuln_scanner": {
        r"grc|compliance|audit|iso\s*27001|nist|pci|sox": [
            "vulnerability risk scoring mapped to compliance framework controls (PCI-DSS, NIST)",
            "audit-ready remediation tracking with SLA compliance evidence",
        ],
        r"devsecops|appsec|ci/?cd|sdlc|secure.?cod|sast|dast": [
            "application security testing integrated with development release cycles",
            "vulnerability-to-remediation workflow for secure development lifecycle",
        ],
        r"cloud|aws|azure|container|docker|kubernetes": [
            "cloud infrastructure vulnerability assessment and misconfiguration detection",
            "continuous security scanning for cloud-deployed services and endpoints",
        ],
        r"fraud|aml|risk|financial": [
            "risk-quantified vulnerability prioritization using exploit probability metrics",
            "remediation deadline enforcement aligned with regulatory compliance windows",
        ],
    },
    "phishing_osint": {
        r"grc|compliance|audit|vendor.?risk|third.?party|due.?diligence": [
            "domain reputation scoring for third-party vendor risk assessment",
            "quantitative risk evidence generation from multi-source OSINT intelligence",
        ],
        r"fraud|aml|kyc|transaction|financial.?crime|sanctions": [
            "KYC domain-verification workflow: WHOIS age, registrar, DNS, and SSL cross-check",
            "suspicious transaction indicator enrichment mapping domains to known fraud typologies",
        ],
        r"cti|threat.?intel|ioc|indicator|feed|hunt": [
            "IOC lifecycle management and multi-source threat intelligence correlation",
            "proactive infrastructure-based threat hunting via domain attribution analysis",
        ],
        r"risk|assessment|scoring": [
            "automated risk indicator enrichment for entity due diligence workflows",
            "domain and IP reputation scoring for risk quantification documentation",
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3: BULLET VARIANTS — pre-framed alternate bullet sets per domain
#
# Each variant rewrites the project's 3 bullets for a specific domain.
# The LLM receives already-framed bullets instead of guessing from JD context.
# ALL content is grounded — same project work, different framing.
# ─────────────────────────────────────────────────────────────────────────────
BULLET_VARIANTS = {
    "soc_auto": {
        "cloud_iam": [
            "Deployed Splunk SIEM with SPL correlation searches to monitor IAM anomalies including unauthorized privilege escalation (T1078) and suspicious cross-account access patterns; mapped cloud-relevant TTPs to MITRE ATT&CK and wrote PICERL incident report.",
            "Built automated cloud security detection pipeline: Python script ingests Splunk alerts for IAM policy violations, performs IOC enrichment via VirusTotal API, and dispatches Telegram notifications with severity classification — enabling rapid response to identity-based threats.",
            "Developed Sigma-compatible detection rules for cloud-specific TTPs including credential abuse and lateral movement; performed network analysis in Wireshark to identify anomalous authentication and DNS traffic patterns in cloud environments.",
        ],
        "dfir_forensics": [
            "Deployed Splunk SIEM with SPL correlation searches for forensic event timeline reconstruction — tracked brute-force attempts (T1110), credential misuse (T1078), and script-based execution (T1059) across host and network logs with full MITRE ATT&CK TTP mapping.",
            "Built automated evidence collection pipeline: Python script ingests Splunk alerts, performs IOC enrichment via VirusTotal API, and generates severity-classified incident packages with chain-of-custody documentation for forensic investigation handoff.",
            "Converted detection logic to Sigma rules for cross-SIEM forensic portability; performed deep packet inspection in Wireshark to reconstruct attack sequences including SYN scans, DNS tunnelling, and credential exposure — documenting artifacts per PICERL framework.",
        ],
        "network_ids": [
            "Deployed Splunk SIEM with SPL correlation searches for network intrusion detection — brute-force detection (index=* failed | stats count by src_ip), lateral movement, and privilege escalation alerts mapped to MITRE ATT&CK (T1110, T1078, T1059) with PICERL reporting.",
            "Built automated network alert triage pipeline: Python script ingests Splunk IDS alerts, performs IOC enrichment via VirusTotal API, and dispatches Telegram notifications with severity classification — reducing mean time to detect network-based threats.",
            "Wrote Sigma rules (vendor-neutral IDS detection format) for enterprise network security; performed TCP/IP deep packet analysis in Wireshark to detect SYN scans, DNS tunnelling, port sweeps, and plaintext credential exposure across network segments.",
        ],
    },
    "vuln_scanner": {
        "devsecops_appsec": [
            "Built automated application security testing pipeline integrating Nessus and OpenVAS APIs in Python; generates vulnerability reports classified by CVSS severity with EPSS exploit probability scoring from FIRST.org API for risk-based prioritization in development workflows.",
            "Developed OWASP Top 10 automated application security checker detecting injection, broken authentication, SSRF, and XSS vulnerabilities; documented SQL injection exploit-to-remediation workflow with parameterised query fixes for secure development guidance.",
            "Automated security scan scheduling via Bash and cron integrated with development cycles; built delta-scan logic to flag newly introduced CVEs per release and enforce remediation SLA deadlines (Critical=24hrs, High=7 days) for secure development lifecycle compliance.",
        ],
        "cloud_security": [
            "Built automated cloud infrastructure vulnerability assessment pipeline using Nessus and OpenVAS APIs in Python; generates CVE reports classified by CVSS severity with EPSS scoring from FIRST.org API to prioritize cloud misconfiguration risks by exploit probability.",
            "Developed automated security checker for cloud-hosted applications testing OWASP Top 10 vulnerabilities including injection, broken authentication, and SSRF; documented remediation workflows for cloud service misconfigurations and exposed endpoints.",
            "Automated vulnerability scan scheduling via Bash and cron for continuous cloud security monitoring; built delta-scan logic to detect newly exposed CVEs and calculate remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) for cloud compliance.",
        ],
        "compliance_audit": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS APIs in Python; generates audit-ready CVE reports classified by CVSS severity with EPSS scoring from FIRST.org API — providing quantitative risk evidence for compliance documentation.",
            "Developed OWASP Top 10 automated compliance checker validating web application security controls against regulatory requirements; documented vulnerability-to-remediation audit trails including SQL injection evidence and parameterised query fixes.",
            "Automated compliance scan scheduling via Bash and cron; built delta-scan logic to track remediation progress against SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days) — generating audit evidence for patch compliance and control effectiveness reporting.",
        ],
    },
    "phishing_osint": {
        "grc_risk_audit": [
            "Built multi-source risk assessment pipeline: submits vendor domains and IPs to VirusTotal, AbuseIPDB, and URLScan.io; cross-references WHOIS registration age, DNS records, and SSL certificate details to produce quantitative risk scores for third-party due diligence.",
            "Implemented domain reputation assessment tool generating typosquatting variants of monitored domains and checking live DNS resolution — provides early warning for brand-impersonation risks in vendor and partner ecosystems.",
            "Deployed automated risk assessment interface via Telegram bot enabling analysts to submit domains for enrichment; supports bulk CSV input/output for vendor risk assessment workflows and includes OSINT enrichment via theHarvester for comprehensive domain profiling.",
        ],
        "fraud_aml": [
            "Built multi-API fraud intelligence pipeline: submits suspicious domains and IPs to VirusTotal, AbuseIPDB, and URLScan.io; cross-references WHOIS registration age, DNS records, and SSL details as part of KYC domain-verification workflow to produce fraud probability scores.",
            "Implemented typosquatting domain detector generating character-substitution variants of legitimate business domains and checking live DNS resolution — identifies brand-impersonation infrastructure used in financial fraud schemes before reaching threat feeds.",
            "Deployed Telegram bot interface for live suspicious entity enrichment supporting bulk CSV input/output for investigation workflows; includes OSINT enrichment via theHarvester for domain profiling to support suspicious transaction report (STR) documentation.",
        ],
        "cti_threat_intel": [
            "Built multi-API cyber threat intelligence pipeline: submits IOCs to VirusTotal, AbuseIPDB, and URLScan.io simultaneously; cross-references WHOIS registration data, DNS records, and SSL certificate details to produce unified threat confidence scores for intelligence products.",
            "Implemented typosquatting domain detector generating character-substitution variants of tracked infrastructure and checking live DNS resolution — provides proactive threat detection capability for infrastructure-based threat hunting.",
            "Deployed Telegram bot interface for real-time IOC enrichment enabling analysts to process indicators at scale; supports bulk CSV input/output for threat intelligence workflows and includes OSINT enrichment via theHarvester for comprehensive domain attribution.",
        ],
    },
}

# Maps domain → {project_key: variant_name}
# Missing project_key or None = use default bullets
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

AMAZON_BASE = [
    "Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring the structured alert triage and escalation workflow used in SOC Tier 1 analyst roles.",
    "Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated findings to senior reviewers, demonstrating investigative instincts central to SOC and fraud analyst operations.",
    "Maintained audit-ready case documentation recording investigation findings, decisions, and corrective actions, establishing the evidence chain-of-custody discipline required for security incident reporting and IT audit.",
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence
# ─────────────────────────────────────────────────────────────────────────────
COMPANY_INTEL = {
    "wipro":         {"framing":"24x7 SOC shifts, SLA discipline, shift documentation.",                       "keywords":["24x7 SOC","SLA adherence","shift documentation"]},
    "tcs":           {"framing":"ISO 27001 ISMS, VAPT, compliance delivery.",                                  "keywords":["ISMS","ISO 27001","compliance audit"]},
    "infosys":       {"framing":"Multi-client delivery, documentation quality.",                               "keywords":["documentation quality","multi-client"]},
    "hcl":           {"framing":"Cloud-native security, AWS, detection engineering.",                          "keywords":["cloud security","AWS security"]},
    "cognizant":     {"framing":"24x7 SOC, BFSI compliance, investigation rigour.",                           "keywords":["SOC operations","BFSI security"]},
    "capgemini":     {"framing":"GRC consulting, cloud security, European clients.",                           "keywords":["GRC","NIST"]},
    "deloitte":      {"framing":"GRC consulting, ITGC/SOX audits, client risk reports.",                      "keywords":["cyber risk advisory","ITGC","SOX"]},
    "kpmg":          {"framing":"ITGC/IS audit. CISA valued. Control testing.",                               "keywords":["IT audit","ITGC","SOX"]},
    "pwc":           {"framing":"Cyber risk advisory. RBI, SEBI, GDPR, PDPB.",                               "keywords":["cyber risk","regulatory compliance","GDPR"]},
    "ey":            {"framing":"EY GDS IT audit and GRC delivery.",                                          "keywords":["GRC","IT audit","ITGC"]},
    "jpmorgan":      {"framing":"Technology risk, Basel III, AML/KYC operations.",                            "keywords":["technology risk","AML","operational risk"]},
    "goldman sachs": {"framing":"Internal tech audit, ITGC, control testing.",                                "keywords":["technology audit","ITGC","SOX"]},
    "deutsche bank": {"framing":"KYC, AML, information security.",                                            "keywords":["KYC","AML","transaction monitoring"]},
    "citi":          {"framing":"Fraud detection, risk analytics, anomaly detection.",                         "keywords":["fraud detection","risk analytics"]},
    "amazon":        {"framing":"LP lens: Dive Deep, Bias for Action, automation mindset.",                   "keywords":["dive deep","automation","AWS"]},
    "google":        {"framing":"Technical depth, automation, systems thinking.",                              "keywords":["security engineering","automation"]},
    "microsoft":     {"framing":"Azure, AD, Sentinel. Growth mindset.",                                       "keywords":["Azure security","Active Directory","Zero Trust"]},
    "hdfc bank":     {"framing":"Fraud detection, AML, RBI compliance.",                                      "keywords":["AML","RBI compliance","fraud analytics"]},
    "bajaj finserv": {"framing":"Fraud/risk operations, NBFC compliance.",                                    "keywords":["fraud operations","IT risk"]},
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
    """
    Scan JD text for domain patterns and return grounded concept phrases.
    These go into the LLM prompt as domain-specific framing signals.
    Every phrase describes something the project actually does — only the
    framing changes. Uses CONCEPT_SWAPPABLE (regex → phrases).
    """
    concept_map = CONCEPT_SWAPPABLE.get(project_key, {})
    jd_lower = jd_text.lower()
    concepts = []
    for pattern, phrases in concept_map.items():
        if re.search(pattern, jd_lower):
            for phrase in phrases:
                if phrase not in concepts:
                    concepts.append(phrase)
    return concepts[:max_concepts]


def get_project_bullets(project_key: str, domain: str) -> list[str]:
    """
    Get domain-specific variant bullets for a project, or fall back to defaults.
    Uses DOMAIN_BULLET_VARIANT mapping + BULLET_VARIANTS data.
    """
    variant_name = DOMAIN_BULLET_VARIANT.get(domain, {}).get(project_key)
    if variant_name:
        variants = BULLET_VARIANTS.get(project_key, {})
        if variant_name in variants:
            logger.debug("  Bullet variant: %s → %s", project_key, variant_name)
            return variants[variant_name]
    return PROJECTS[project_key]["bullets"]
# Company scraping / GitHub research
# ─────────────────────────────────────────────────────────────────────────────
_HDRS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120.0.0.0 Safari/537.36"}


def scrape_company(company_raw: str) -> str:
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip()
    if not name or name.lower() in ("unknown",""):
        return ""
    try:
        q    = requests.utils.quote(f"{name} cybersecurity about mission")
        resp = requests.get(f"https://html.duckduckgo.com/html/?q={q}", headers=_HDRS, timeout=8)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href","")
            if href.startswith("http") and not any(x in href for x in ["linkedin.com","glassdoor.com","indeed.com"]):
                pg  = requests.get(href, headers=_HDRS, timeout=8)
                s2  = BeautifulSoup(pg.text, "html.parser")
                for tag in s2(["script","style","nav","footer","header"]): tag.decompose()
                main = s2.find("main") or s2.find("article") or s2
                text = " ".join(p.get_text(" ",strip=True) for p in main.find_all("p") if len(p.get_text())>40)
                if len(text) > 100:
                    return text[:800]
    except Exception:
        pass
    return ""


def research_github_projects(domain: str, job_title: str) -> str:
    DOMAIN_SEARCH = {
        "SOC":"SOC automation SIEM detection lab","VAPT":"vulnerability scanner CVE CVSS python",
        "GRC":"GRC compliance automation NIST ISO27001 python","Risk":"risk management compliance python",
        "Fraud-AML":"AML transaction monitoring fraud detection python",
        "CloudSec":"cloud security AWS IAM audit python","General":"cybersecurity portfolio entry level",
    }
    query   = DOMAIN_SEARCH.get(domain, "cybersecurity portfolio")
    encoded = requests.utils.quote(f"{query} language:Python stars:>2")
    url     = f"https://api.github.com/search/repositories?q={encoded}&sort=stars&per_page=5"
    headers = {"Accept":"application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp  = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items",[])
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
    raw = re.sub(r"^```(?:json)?\s*","", raw.strip())
    raw = re.sub(r"\s*```$","",          raw.strip())
    raw = raw.replace("\u201c",'"').replace("\u201d",'"')
    raw = raw.replace("\u2018","'").replace("\u2019","'")
    raw = re.sub(r",\s*([\}\]])",r"\1", raw)
    raw = re.sub(r'\\([^"\\/bfnrtu])',r'\1', raw)
    return raw.strip()


def _call_groq(system: str, user: str, model: str, max_tokens: int = 2500, retries: int = 3) -> str:
    payload = {"model":model,"temperature":0.15,"max_tokens":max_tokens,
               "messages":[{"role":"system","content":system},{"role":"user","content":user}]}
    hdrs = {"Authorization":f"Bearer {GROQ_API_KEY}","Content-Type":"application/json"}
    for attempt in range(1, retries+1):
        try:
            r = requests.post(GROQ_URL, json=payload, headers=hdrs, timeout=35)
            if r.status_code == 429:
                wait = 25*attempt
                logger.warning("  Groq 429 — waiting %ds (attempt %d/%d)", wait, attempt, retries)
                time.sleep(wait); continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.RequestException as exc:
            logger.warning("  Groq error attempt %d: %s", attempt, exc)
            time.sleep(5*attempt)
    raise RuntimeError(f"Groq ({model}) failed after retries.")


# ─────────────────────────────────────────────────────────────────────────────
# Resume content generation
# ─────────────────────────────────────────────────────────────────────────────
def generate_content(job: dict, p1_key: str, p2_key: str,
                     intel: dict | None, scraped_ctx: str,
                     p1_tools: list, p2_tools: list,
                     jd_keywords: dict) -> dict:
    p1 = PROJECTS[p1_key]
    p2 = PROJECTS[p2_key]

    co_ctx = ""
    if intel:
        co_ctx = f"\nCOMPANY FRAMING: {intel['framing']}\nPriority keywords: {', '.join(intel['keywords'][:4])}\nDo NOT write 'Eager to contribute to X'.\n"
    elif scraped_ctx:
        co_ctx = f"\nCOMPANY CONTEXT: {scraped_ctx[:400]}\n"

    # FEATURE 3: keyword injection hint
    ranked  = jd_keywords.get("ranked", [])
    kw_hint = ""
    if ranked:
        kw_hint = (f"\nKEYWORD INJECTION: Weave these top JD keywords naturally across bullets "
                   f"(target 2-3x total, max 2 per bullet): {', '.join(ranked[:8])}\n")

    # BUG FIX B: 'and' not '&'
    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "Bullets must be factual — never fabricate tools or experience. "
        "ALWAYS write 'and' not '&' in bullet text (except MITRE ATT&CK which is a proper noun). "
        "Return ONLY a valid JSON object. Internal double-quotes escaped as \\\". "
        "No markdown fences. No comments. No trailing commas."
    )

    # BUG FIX C: soft char limit — keep differentiators
    # Build project-specific differentiator preservation list
    # Only include TTPs/syntax that belong to the actually selected projects
    _PROJ_DIFFERENTIATORS = {
        "soc_auto":       ["SPL query syntax (index=* failed | stats)",
                           "MITRE TTP numbers (T1110/T1078/T1059)",
                           "SOAR pipeline detail"],
        "vuln_scanner":   ["EPSS scoring", "FIRST.org API mention",
                           "CVSS severity classification",
                           "remediation SLA deadlines (Critical=24hrs, High=7 days, Medium=30 days)"],
        "phishing_osint": ["typosquatting detection detail",
                           "multi-API cross-referencing (VirusTotal, AbuseIPDB, URLScan.io)",
                           "WHOIS/DNS/SSL analysis detail"],
    }
    active_diffs = []
    for pk in set([p1_key, p2_key]):
        active_diffs.extend(_PROJ_DIFFERENTIATORS.get(pk, []))

    if active_diffs:
        diff_instruction = (
            f"NEVER drop from project bullets: {', '.join(active_diffs)}.\n"
            "These differentiators are what make a fresher resume stand out — keep them even if longer.\n"
            "IMPORTANT: Only include technical details that belong to each specific project. "
            "Do NOT add MITRE TTP numbers, SPL queries, or SOAR details to projects that don't have them.\n"
        )
    else:
        diff_instruction = ""

    user = f"""JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{co_ctx}{kw_hint}
SINGLE-PAGE PREFERENCE: Keep bullets concise (prefer under 200 chars).
{diff_instruction}
Return JSON with EXACTLY 13 keys:
{{
  "AMZ_B1": "Rewrite with 1-2 domain keywords, action verb start, 'and' not '&'. Do NOT say 'mirroring SOC' or 'similar to SOC' — let the skills speak for themselves: {AMAZON_BASE[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords, action verb start, 'and' not '&'. Do NOT explicitly compare to security roles: {AMAZON_BASE[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords, action verb start, 'and' not '&'. Do NOT explicitly compare to security roles: {AMAZON_BASE[2]}",
  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{', '.join(p1_tools)}",
  "P1_B1": "Rewrite using ONLY P1_TECH tools and details from P1 project, preserve technical detail, use 'and' not '&': {p1['bullets'][0]}",
  "P1_B2": "Rewrite using ONLY P1_TECH tools and details from P1 project, preserve technical detail, use 'and' not '&': {p1['bullets'][1]}",
  "P1_B3": "Rewrite using ONLY P1_TECH tools and details from P1 project, preserve technical detail, use 'and' not '&': {p1['bullets'][2]}",
  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{', '.join(p2_tools)}",
  "P2_B1": "Rewrite using ONLY P2_TECH tools and details from P2 project, preserve technical detail, use 'and' not '&': {p2['bullets'][0]}",
  "P2_B2": "Rewrite using ONLY P2_TECH tools and details from P2 project, preserve technical detail, use 'and' not '&': {p2['bullets'][1]}",
  "P2_B3": "Rewrite using ONLY P2_TECH tools and details from P2 project, preserve technical detail, use 'and' not '&': {p2['bullets'][2]}"
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
            lambda m: m.group(1)+'"'+m.group(2).replace('"','\\"')+'"',
            raw, flags=re.DOTALL
        )
        content = json.loads(fixed)

    expected = ["AMZ_B1","AMZ_B2","AMZ_B3",
                "P1_TITLE","P1_TECH","P1_B1","P1_B2","P1_B3",
                "P2_TITLE","P2_TECH","P2_B1","P2_B2","P2_B3"]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM missing keys: {missing}")

    # Merge skill profile + dynamic augmentation (FEATURE 4)
    base_skills = compute_skills(job["domain"])
    content.update(dynamic_skills_augment(base_skills, jd_keywords))

    # FEATURE 2: Apply synonym expansion to project bullets
    for k in ["P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]:
        if content.get(k):
            content[k] = apply_synonyms(content[k])

    return content


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_validation_output(data: dict) -> dict:
    return {
        "ats_score": str(data.get("ats_score", "N/A")),
        "missing_keywords": str(data.get("missing_keywords", "")),
        "improvements": str(data.get("improvements", "")),
        "github_insight": str(data.get("github_insight", "")),
    }


def validate_resume(content: dict, job: dict, github_notes: str, mode: str) -> dict:
    EMPTY = {"ats_score":"skipped","missing_keywords":"","improvements":"","github_insight":""}

    if mode == "lenient":
        logger.info("  Validation: lenient — skipped")
        return EMPTY

    bullets = " | ".join(filter(None,[
        content.get("AMZ_B1",""),content.get("AMZ_B2",""),content.get("AMZ_B3",""),
        content.get("P1_B1",""),content.get("P1_B2",""),
        content.get("P2_B1",""),content.get("P2_B2",""),
    ]))

    if mode == "normal":
        prompt = (f"Job: {job.get('job_title','')} | JD keywords: {job.get('skills','')[:200]}\n"
                  f"Bullets: {bullets[:500]}\nATS review for 0-2yr cybersecurity candidate.\n"
                  "Return raw JSON: {\"ats_score\":<1-10>,\"missing_keywords\":\"<max 6>\"}")
        try:
            raw  = _call_groq("Return only valid JSON, no markdown.", prompt, GROQ_VAL_MODEL, max_tokens=150)
            data = json.loads(_repair_json(raw))
            data = _normalize_validation_output(data)

            logger.info(
                "  ATS=%s missing=%s",
                data.get("ats_score"),
                str(data.get("missing_keywords",""))[:50]
            )
            return data

        except Exception as exc:
            logger.warning("  Validation failed: %s | raw=%s", exc, raw[:200] if 'raw' in locals() else "")
            return EMPTY

    gh_sec = (f"\nSimilar GitHub projects:\n{github_notes[:500]}\n" if github_notes else "")

    prompt = (f"Job: {job.get('job_title','')} | Domain: {job.get('domain','')}\n"
              f"JD: {job.get('skills','')[:250]}\nBullets: {bullets[:600]}\n{gh_sec}"
              "Return raw JSON: {\"ats_score\":<1-10>,\"missing_keywords\":\"<max 8>\","
              "\"improvements\":\"<2 fixes>\",\"github_insight\":\"<1 thing>\"}")

    try:
        raw  = _call_groq("Strict ATS reviewer. Return only valid JSON.", prompt, GROQ_VAL_MODEL, max_tokens=300)
        data = json.loads(_repair_json(raw))
        data = _normalize_validation_output(data)

        logger.info("  ATS=%s", data.get("ats_score"))
        return data

    except Exception as exc:
        logger.warning("  Validation failed: %s | raw=%s", exc, raw[:200] if 'raw' in locals() else "")
        return EMPTY


# ─────────────────────────────────────────────────────────────────────────────
# DOCX fill
# ─────────────────────────────────────────────────────────────────────────────
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    all_t = para._p.findall(f".//{{{W_NS}}}t")
    for t in all_t:
        if t.text and placeholder in t.text:
            t.text = t.text.replace(placeholder, replacement)
            if t.text and (t.text[0]==" " or t.text[-1]==" "):
                t.set("{http://www.w3.org/XML/1998/namespace}space","preserve")
            return True
    full = "".join(t.text or "" for t in all_t)
    if placeholder not in full:
        return False
    new_text = full.replace(placeholder, replacement)
    if all_t:
        all_t[0].text = new_text
        if new_text and (new_text[0]==" " or new_text[-1]==" "):
            all_t[0].set("{http://www.w3.org/XML/1998/namespace}space","preserve")
        for t in all_t[1:]: t.text = ""
    return True


def fill_template(content: dict) -> bytes:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError("resume_template.docx not found.")
    doc = Document(str(TEMPLATE_PATH))
    replacements = {f"[[{k}]]": v for k,v in content.items()}
    for para in doc.paragraphs:
        full = "".join(t.text or "" for t in para._p.findall(f".//{{{W_NS}}}t"))
        for ph,val in replacements.items():
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
        with open(docx_path,"wb") as f: f.write(docx_bytes)
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf:writer_pdf_Export", "--outdir", tmpdir, docx_path],
            capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice: {result.stderr[:200]}")
        pdf_path = os.path.join(tmpdir, "resume.pdf")
        if not os.path.exists(pdf_path):
            raise FileNotFoundError("LibreOffice did not produce resume.pdf")
        with open(pdf_path,"rb") as f: return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE H: Single-page enforcement — 5-tier relevancy-aware trimming
#
# Tier 0: Reduce paragraph spacing in DOCX (non-destructive formatting)
# Tier 1: Shorten long bullets (>200 chars) via LLM — non-destructive
# Tier 2: Remove least-relevant project bullet by JD keyword score
# Tier 3: Trim excess skills (SK_V5 → SK_V4 → SK_V1)
# Tier 4: Shorten longest Amazon bullet (never remove)
#
# STRICT: Always enforces single page. No exceptions for certs on page 2.
# ─────────────────────────────────────────────────────────────────────────────

def _count_pdf_pages(pdf_bytes: bytes) -> int:
    try:
        import pikepdf
        return len(pikepdf.open(io.BytesIO(pdf_bytes)).pages)
    except Exception as e:
        logger.warning("Page count failed: %s", e)
        return 999   # force trimming instead of skipping


def _score_bullet_relevancy(bullet_text: str, ranked_keywords: list) -> int:
    """
    Score a bullet's relevancy to the JD based on keyword overlap.
    Returns count of ranked JD keywords found in the bullet (0–10).
    Uses word-boundary regex — no LLM call.
    """
    if not bullet_text or not ranked_keywords:
        return 0
    score = 0
    lower = bullet_text.lower()
    for kw in ranked_keywords[:10]:
        if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", lower, re.IGNORECASE):
            score += 1
    return score


def _shorten_bullet_llm(bullet_text: str, target_chars: int = 160) -> str:
    """
    Use Groq to compress a bullet to ~target_chars while preserving
    differentiators (EPSS, SPL syntax, MITRE TTPs, FIRST.org, SOAR).
    Falls back to original text on failure.
    """
    if not bullet_text or len(bullet_text) <= target_chars:
        return bullet_text
    system = (
        "You are a resume bullet editor. Shorten the bullet to under "
        f"{target_chars} characters. PRESERVE: EPSS scoring, SPL query syntax, "
        "MITRE TTP numbers (T1110/T1078/T1059), SOAR detail, FIRST.org mention. "
        "Use 'and' not '&'. Return ONLY the shortened bullet, no quotes, no explanation."
    )
    user = f"Shorten this resume bullet to ~{target_chars} chars:\n{bullet_text}"
    try:
        result = _call_groq(system, user, GROQ_GEN_MODEL, max_tokens=250)
        result = result.strip().strip('"')
        if len(result) > 20:  # sanity check
            logger.info("    Shortened %d→%d chars", len(bullet_text), len(result))
            return result
    except Exception as exc:
        logger.warning("    Bullet shortening failed: %s", exc)
    return bullet_text


def _trim_skills_line(skills_value: str, max_items: int = 4) -> str:
    """
    Trim a comma-separated skills value to at most max_items.
    Keeps the first max_items entries (most important ones listed first).
    """
    if not skills_value:
        return skills_value
    items = [x.strip() for x in skills_value.split(",") if x.strip()]
    if len(items) <= max_items:
        return skills_value
    trimmed = ", ".join(items[:max_items])
    logger.info("    Skills trimmed: %d→%d items", len(items), max_items)
    return trimmed


def _reduce_paragraph_spacing(docx_bytes: bytes) -> bytes:
    """
    Reduce paragraph before/after spacing in the DOCX to squeeze content.
    This is non-destructive — no content is removed, only formatting changes.
    Targets: section headings get 2pt before/0pt after, bullet paras get 0pt/0pt.
    """
    doc = Document(io.BytesIO(docx_bytes))
    from docx.shared import Pt
    for para in doc.paragraphs:
        pf = para.paragraph_format
        text = para.text.strip()
        if not text:
            # Remove empty paragraphs' spacing entirely
            pf.space_before = Pt(0)
            pf.space_after  = Pt(0)
            continue
        # Section headings (bold, short text) — minimal spacing
        if para.style and para.style.name and 'Heading' in para.style.name:
            pf.space_before = Pt(2)
            pf.space_after  = Pt(0)
        else:
            # All other paragraphs — reduce spacing
            if pf.space_before is None or pf.space_before > Pt(2):
                pf.space_before = Pt(1)
            if pf.space_after is None or pf.space_after > Pt(2):
                pf.space_after = Pt(0)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# Section titles in the template to identify section-header paragraphs
_SECTION_TITLES = {"education", "work experience", "projects", "technical skills", "certifications"}


def _is_section_header(para) -> bool:
    """Check if a paragraph is a section header (Education, Projects, etc.)."""
    text = para.text.strip().lower()
    return text in _SECTION_TITLES


def _is_skill_row(para) -> bool:
    """Check if a paragraph is a filled-in skill row (e.g. 'SOC Operations: ...')."""
    text = para.text.strip()
    if not text or len(text) < 5:
        return False
    # Skill rows are "Label: value1, value2, ..." — short label with colon
    if ":" in text:
        label = text.split(":")[0].strip()
        if 3 <= len(label) <= 30:
            return True
    return False


def _expand_spacing_to_fill_page(docx_bytes: bytes, extra_pts: float) -> bytes:
    """
    Distribute extra vertical space across section headers, skill rows,
    bullet paragraphs, and empty separator paragraphs to fill the page.

    Distribution ratios:
    - 40% to section headers (space_before) — ~5 headers, biggest visual impact
    - 20% to skill rows (space_before + space_after)
    - 20% to bullet list paragraphs (space_after)
    - 20% to empty separator paragraphs (space_before + space_after)
    """
    from docx.shared import Pt, Emu
    doc = Document(io.BytesIO(docx_bytes))

    section_headers = []
    skill_rows = []
    bullet_rows = []
    separator_rows = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            separator_rows.append(para)
            continue
        if _is_section_header(para):
            section_headers.append(para)
        elif _is_skill_row(para):
            skill_rows.append(para)
        elif para.style and para.style.name == 'List Paragraph':
            bullet_rows.append(para)

    n_headers    = max(len(section_headers), 1)
    n_skills     = max(len(skill_rows), 1)
    n_bullets    = max(len(bullet_rows), 1)
    n_separators = max(len(separator_rows), 1)

    header_share    = extra_pts * 0.40 / n_headers
    skill_share     = extra_pts * 0.20 / n_skills
    bullet_share    = extra_pts * 0.20 / n_bullets
    separator_share = extra_pts * 0.20 / n_separators

    def _get_pts(val):
        """Convert a spacing value to float points."""
        if val is None or val == 0:
            return 0.0
        # val is in EMU; 1pt = 12700 EMU
        return val / 12700.0

    for para in section_headers:
        pf = para.paragraph_format
        current_pts = _get_pts(pf.space_before)
        pf.space_before = Pt(current_pts + header_share)

    for para in skill_rows:
        pf = para.paragraph_format
        cb = _get_pts(pf.space_before)
        ca = _get_pts(pf.space_after)
        pf.space_before = Pt(cb + skill_share * 0.5)
        pf.space_after  = Pt(ca + skill_share * 0.5)

    for para in bullet_rows:
        pf = para.paragraph_format
        ca = _get_pts(pf.space_after)
        pf.space_after = Pt(ca + bullet_share)

    for para in separator_rows:
        pf = para.paragraph_format
        cb = _get_pts(pf.space_before)
        ca = _get_pts(pf.space_after)
        pf.space_before = Pt(cb + separator_share * 0.5)
        pf.space_after  = Pt(ca + separator_share * 0.5)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


def _fill_page(docx_bytes: bytes) -> tuple[bytes, bytes]:
    """
    Expand spacing to fill the single page fully.
    Does NOT try to measure PDF content position (unreliable with lines/rules).
    Instead, binary searches extra spacing (0–200pt) and checks page count
    each iteration. Finds the maximum spacing that still fits on one page.

    Returns (final_docx_bytes, final_pdf_bytes).
    """
    pdf_bytes = generate_pdf(docx_bytes)
    pages = _count_pdf_pages(pdf_bytes)
    if pages != 1:
        return docx_bytes, pdf_bytes  # safety — don't fill if not single page

    # Quick check: can we add ANY spacing? Try 5pt first.
    trial_docx = _expand_spacing_to_fill_page(docx_bytes, 5.0)
    trial_pdf  = generate_pdf(trial_docx)
    if _count_pdf_pages(trial_pdf) > 1:
        # Even 5pt overflows — page is already very full, no room to expand
        logger.info("  Page fill: page already near-full, no expansion possible")
        return docx_bytes, pdf_bytes

    # Binary search: find max extra_pts in [0, 200] that still fits 1 page
    # 200pt ≈ 2.78 inches — more than enough for any realistic gap
    lo, hi = 0.0, 200.0
    best_docx, best_pdf = docx_bytes, pdf_bytes

    # First, find an upper bound that actually overflows
    # (start at 200, if it fits, use it directly)
    trial_docx = _expand_spacing_to_fill_page(docx_bytes, hi)
    trial_pdf  = generate_pdf(trial_docx)
    if _count_pdf_pages(trial_pdf) <= 1:
        # Even 200pt fits — use it (this means the page was very empty)
        logger.info("  Page fill: distributed 200.0pt (maximum), still fits")
        return trial_docx, trial_pdf

    logger.info("  Page fill: binary searching optimal spacing (0-200pt)...")

    for iteration in range(10):  # 10 iterations → ~0.2pt precision
        mid = (lo + hi) / 2
        trial_docx = _expand_spacing_to_fill_page(docx_bytes, mid)
        trial_pdf  = generate_pdf(trial_docx)
        trial_pages = _count_pdf_pages(trial_pdf)

        if trial_pages <= 1:
            lo = mid
            best_docx = trial_docx
            best_pdf  = trial_pdf
        else:
            hi = mid

    logger.info("  Page fill complete: distributed %.1fpt of spacing", lo)
    return best_docx, best_pdf


def _generate_and_check(working: dict, reduce_spacing: bool = False,
                        fill_page: bool = False) -> tuple[bytes, bytes, int]:
    """Fill template, generate PDF, count pages. Returns (docx, pdf, pages)."""
    docx_bytes = fill_template(working)
    if reduce_spacing:
        docx_bytes = _reduce_paragraph_spacing(docx_bytes)
    pdf_bytes  = generate_pdf(docx_bytes)
    pages      = _count_pdf_pages(pdf_bytes)
    if fill_page and pages == 1:
        docx_bytes, pdf_bytes = _fill_page(docx_bytes)
        pages = _count_pdf_pages(pdf_bytes)
    return docx_bytes, pdf_bytes, pages


def enforce_single_page(content: dict, job: dict,
                        jd_keywords: dict | None = None) -> tuple[bytes, bytes, str]:
    """
    Generate DOCX+PDF and STRICTLY enforce single-page output.
    Applies up to 5 tiers of trimming, then fills remaining space:

    Tier 0: Reduce paragraph spacing (non-destructive formatting)
    Tier 1: Shorten bullets > 200 chars via LLM (non-destructive)
    Tier 2: Remove least-relevant project bullet (scored by JD keyword overlap)
    Tier 3: Trim excess skills (SK_V5 → SK_V4 → SK_V1)
    Tier 4: Shorten longest Amazon bullet (never fully remove)

    Page Fill: After achieving single page, measures bottom white space using
    pdfminer and distributes extra spacing across section headers, skill rows,
    and bullet paragraphs via binary search to fully utilize the page.
    """
    ranked = (jd_keywords or {}).get("ranked", [])
    trim_log = []
    working  = dict(content)

    # ── Initial check (no spacing reduction yet) ─────────────────────────
    docx_bytes, pdf_bytes, pages = _generate_and_check(working)

    if pages <= 1:
        # Page fits — now fill it to avoid white space at bottom
        logger.info("  Single page OK — filling page to reduce white space")
        docx_bytes, pdf_bytes = _fill_page(docx_bytes)
        return docx_bytes, pdf_bytes, "page-filled"

    logger.info("  %d pages detected — enforcing single page", pages)

    # ── Tier 0: Reduce paragraph spacing ─────────────────────────────────
    logger.info("  Tier 0: reducing paragraph spacing")
    docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
    trim_log.append("reduced-spacing")

    if pages <= 1:
        logger.info("  Single page achieved via Tier 0 (spacing). %s", trim_log)
        docx_bytes, pdf_bytes = _fill_page(docx_bytes)
        return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 1: Shorten long bullets (>200 chars) ────────────────────────
    LONG_THRESHOLD = 200
    all_bullet_keys = ["AMZ_B1","AMZ_B2","AMZ_B3",
                       "P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]
    long_bullets = [(k, len(working.get(k,""))) for k in all_bullet_keys
                    if len(working.get(k,"")) > LONG_THRESHOLD]
    # Sort by length descending — shorten longest first
    long_bullets.sort(key=lambda x: x[1], reverse=True)

    if long_bullets:
        logger.info("  Tier 1: %d bullets > %d chars — shortening", len(long_bullets), LONG_THRESHOLD)
        for key, length in long_bullets:
            working[key] = _shorten_bullet_llm(working[key], target_chars=150)
            trim_log.append(f"shortened {key} ({length}→{len(working[key])})")

        docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
        if pages <= 1:
            logger.info("  Single page achieved via Tier 1 (shortening). %s", trim_log)
            docx_bytes, pdf_bytes = _fill_page(docx_bytes)
            return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 1.5: Shorten ALL bullets > 150 chars (more aggressive) ──────
    AGGRESSIVE_THRESHOLD = 150
    still_long = [(k, len(working.get(k,""))) for k in all_bullet_keys
                  if len(working.get(k,"")) > AGGRESSIVE_THRESHOLD]
    still_long.sort(key=lambda x: x[1], reverse=True)

    if still_long:
        logger.info("  Tier 1.5: %d bullets > %d chars — aggressive shortening",
                    len(still_long), AGGRESSIVE_THRESHOLD)
        for key, length in still_long:
            working[key] = _shorten_bullet_llm(working[key], target_chars=130)
            trim_log.append(f"aggressively shortened {key} ({length}→{len(working[key])})")

        docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
        if pages <= 1:
            logger.info("  Single page achieved via Tier 1.5. %s", trim_log)
            docx_bytes, pdf_bytes = _fill_page(docx_bytes)
            return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 2: Remove least-relevant project bullets ────────────────────
    PROJECT_BULLET_KEYS = ["P1_B1","P1_B2","P1_B3","P2_B1","P2_B2","P2_B3"]
    removable = [k for k in PROJECT_BULLET_KEYS
                 if working.get(k,"").strip() and working[k].strip() != " "]

    if removable:
        logger.info("  Tier 2: scoring %d project bullets by JD relevancy", len(removable))
        # Score each bullet; remove lowest-scoring first
        scored = [(k, _score_bullet_relevancy(working[k], ranked)) for k in removable]
        scored.sort(key=lambda x: x[1])  # ascending — least relevant first

        for key, score in scored:
            working[key] = " "
            trim_log.append(f"removed {key} (score={score})")
            logger.info("  Tier 2: removed %s (relevancy score=%d)", key, score)

            docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
            if pages <= 1:
                logger.info("  Single page achieved via Tier 2. %s", trim_log)
                docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 3: Trim excess skills ───────────────────────────────────────
    SKILL_TRIM_ORDER = ["SK_V5", "SK_V4", "SK_V3", "SK_V2", "SK_V1"]
    logger.info("  Tier 3: trimming skills")
    for sk_key in SKILL_TRIM_ORDER:
        original = working.get(sk_key, "")
        if original and len(original.split(",")) > 3:
            working[sk_key] = _trim_skills_line(original, max_items=3)
            trim_log.append(f"trimmed {sk_key}")

            docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
            if pages <= 1:
                logger.info("  Single page achieved via Tier 3 (skills). %s", trim_log)
                docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Tier 4: Shorten Amazon bullets (last resort, never remove) ───────
    AMZ_KEYS = ["AMZ_B1", "AMZ_B2", "AMZ_B3"]
    amz_bullets = [(k, len(working.get(k,""))) for k in AMZ_KEYS
                   if working.get(k,"").strip() and working[k].strip() != " "]
    amz_bullets.sort(key=lambda x: x[1], reverse=True)  # longest first

    if amz_bullets:
        logger.info("  Tier 4: shortening Amazon bullets (last resort)")
        for key, length in amz_bullets:
            if length > 80:  # only shorten if meaningfully long
                working[key] = _shorten_bullet_llm(working[key], target_chars=100)
                trim_log.append(f"shortened {key} ({length}→{len(working[key])})")

                docx_bytes, pdf_bytes, pages = _generate_and_check(working, reduce_spacing=True)
                if pages <= 1:
                    logger.info("  Single page achieved via Tier 4 (AMZ shorten). %s", trim_log)
                    docx_bytes, pdf_bytes = _fill_page(docx_bytes)
                    return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; page-filled"

    # ── Fallback: all tiers exhausted ────────────────────────────────────
    logger.warning("  All tiers exhausted — could not achieve single page")
    docx_bytes, pdf_bytes, _ = _generate_and_check(working, reduce_spacing=True)
    return docx_bytes, pdf_bytes, "; ".join(trim_log) + "; overflow-unresolved"


# ─────────────────────────────────────────────────────────────────────────────
# GitHub storage + URL shortening
# ─────────────────────────────────────────────────────────────────────────────
def _safe(s: str, n: int = 35) -> str:
    return re.sub(r"[^A-Za-z0-9_-]","_",s)[:n]


def _github_commit(filename: str, file_bytes: bytes, message: str) -> str:
    path    = f"{RESUMES_FOLDER}/{filename}"
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    headers = {"Authorization":f"Bearer {GITHUB_TOKEN}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
    sha     = None
    existing = requests.get(api_url, headers=headers, timeout=10)
    if existing.status_code == 200: sha = existing.json().get("sha")
    payload = {"message":message,"content":base64.b64encode(file_bytes).decode(),"branch":GITHUB_BRANCH}
    if sha: payload["sha"] = sha
    resp = requests.put(api_url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/{GITHUB_BRANCH}/{path}"


def upload_to_github(docx_bytes: bytes, pdf_bytes: bytes, job: dict) -> tuple[str,str]:
    base = f"Resume_{_safe(job['job_title'])}_{_safe(job['company'])}"
    msg  = f"Resume: {job['job_title']} @ {job['company']}"
    return (_github_commit(f"{base}.docx", docx_bytes, msg),
            _github_commit(f"{base}.pdf",  pdf_bytes,  msg))


def shorten_url(long_url: str) -> str:
    try:
        resp = requests.get(f"https://tinyurl.com/api-create.php?url={requests.utils.quote(long_url)}", timeout=8)
        if resp.status_code == 200 and resp.text.startswith("https://tinyurl.com"):
            return resp.text.strip()
    except Exception:
        pass
    return long_url


# ─────────────────────────────────────────────────────────────────────────────
# Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_creds() -> Credentials:
    j = os.environ.get("GOOGLE_CREDS_JSON","")
    if not j: raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(json.loads(j), scopes=SCOPES)


def ensure_column(ws, name: str) -> int:
    headers = ws.row_values(1)
    if name not in headers:
        idx = len(headers)+1
        ws.update_cell(1, idx, name)
        headers.append(name)
        logger.info("Added column '%s' at %d.", name, idx)
        return idx
    return headers.index(name)+1


def get_pending_jobs(ws, doc_col: int) -> list[dict]:
    rows = ws.get_all_values()
    if len(rows) < 2: return []
    headers = rows[0]
    col = {h:i for i,h in enumerate(headers)}
    def _get(row,key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""
    pending = []
    for row_num, row in enumerate(rows[1:], start=2):
        if _get(row,"status").lower() == "new" and not (row[doc_col-1].strip() if doc_col-1 < len(row) else ""):
            pending.append({
                "row_num": row_num,
                "job_title": _get(row,"job_title") or "Cybersecurity Role",
                "company":   _get(row,"company")   or "Unknown",
                "domain":    _get(row,"domain")     or "General",
                "summary":   _get(row,"summary"),
                "skills":    _get(row,"skills_required"),
            })
    return pending


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    logger.info("="*60)
    logger.info("Resume Tailor — Research Framework Edition (validation=%s)", VALIDATION_MODE)
    logger.info("="*60)

    for name, val in [("GROQ_API_KEY",GROQ_API_KEY),("GITHUB_TOKEN",GITHUB_TOKEN),("GITHUB_REPOSITORY",GITHUB_REPOSITORY)]:
        if not val: logger.error("%s not set.", name); sys.exit(1)
    if not TEMPLATE_PATH.exists():
        logger.error("resume_template.docx not found."); sys.exit(1)

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
        logger.info("No New jobs with empty resume_doc_link."); sys.exit(0)

    logger.info("Found %d pending. Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-"*50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)", i, len(pending),
                    job["job_title"], job["company"], job["domain"])
        try:
            # Projects + tools
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_auto","vuln_scanner"))
            jd_text  = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools = select_tools(p1_key, jd_text)
            p2_tools = select_tools(p2_key, jd_text)
            logger.info("  Projects: %s + %s | P1 tools: %s", p1_key, p2_key, p1_tools[:3])

            # FEATURE 1: Extract keywords
            logger.info("  Extracting JD keywords...")
            jd_keywords = extract_keywords(jd_text)

            # GitHub research (strict only)
            github_notes = ""
            if VALIDATION_MODE == "strict":
                github_notes = research_github_projects(job["domain"], job["job_title"])

            # Company intel
            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company(job["company"])

            # Generate content (includes Features 2, 3, 4)
            logger.info("  Generating content...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                       p1_tools, p2_tools, jd_keywords)

            # FEATURE 3: Track keyword usage
            track_keyword_usage(content, jd_keywords.get("ranked",[]))

            # Validate
            if VALIDATION_MODE != "lenient": time.sleep(3)
            val_result = validate_resume(content, job, github_notes, VALIDATION_MODE)
            ats_score  = val_result.get("ats_score","N/A")
            val_note   = (
                f"[{VALIDATION_MODE.upper()}] ATS:{ats_score}"
                + (f" | Missing:{val_result.get('missing_keywords','')}" if val_result.get("missing_keywords") else "")
                + (f" | Fix:{val_result.get('improvements','')}" if val_result.get("improvements") else "")
                + (f" | GitHub:{val_result.get('github_insight','')}" if val_result.get("github_insight") else "")
            )
            logger.info("  %s", val_note)

            # FEATURE 5: Metrics
            metrics = compute_metrics(content, jd_keywords, ats_score)

            # FEATURE 6: Recruiter simulation
            if VALIDATION_MODE != "lenient":
                time.sleep(2)
                rec_sim = recruiter_simulate(content, job)
            else:
                rec_sim = {"credibility":"skipped","stuffing_suspicion":"skipped","hireability":"skipped"}

            # FEATURE H: Single-page enforcement + PDF generation
            logger.info("  Generating DOCX+PDF (single-page enforcement)...")
            docx_bytes, pdf_bytes, trim_log = enforce_single_page(content, job, jd_keywords)
            if trim_log and trim_log not in ("certs-p2-ok",""):
                val_note += f" | Trimmed:{trim_log}"
            logger.info("  DOCX: %d bytes  PDF: %d bytes", len(docx_bytes), len(pdf_bytes))

            # Upload + shorten
            doc_raw, pdf_raw = upload_to_github(docx_bytes, pdf_bytes, job)
            doc_url = shorten_url(doc_raw)
            pdf_url = shorten_url(pdf_raw)
            logger.info("  Doc: %s", doc_url)
            logger.info("  PDF: %s", pdf_url)

            # Write all columns to sheet
            ws.update_cell(job["row_num"], doc_col,   doc_url)
            ws.update_cell(job["row_num"], pdf_col,   pdf_url)
            ws.update_cell(job["row_num"], val_col,   val_note)
            ws.update_cell(job["row_num"], cov_col,   metrics["keyword_coverage"])
            ws.update_cell(job["row_num"], den_col,   metrics["keyword_density"])
            ws.update_cell(job["row_num"], sk_col,    metrics["total_skills_count"])
            ws.update_cell(job["row_num"], cred_col,  str(rec_sim.get("credibility","")))
            ws.update_cell(job["row_num"], stuff_col, str(rec_sim.get("stuffing_suspicion","")))
            ws.update_cell(job["row_num"], hire_col,  str(rec_sim.get("hireability","")))
            logger.info("  ✓ Sheet updated.")

            success += 1
            time.sleep(4)

        except Exception as exc:
            logger.error("  ✗ Failed: %s", exc); continue

    logger.info("="*60)
    logger.info("Done: %d/%d succeeded.", success, len(pending))
    logger.info("="*60)


if __name__ == "__main__":
    main()
