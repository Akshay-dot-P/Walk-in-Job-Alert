"""
resume_tailor.py
================
Reads resume_template.docx from the repo, tailors it per job, exports PDF.

TRIGGER: Set any job's status to "applied" in the sheet.
The script runs every 30 min, finds jobs with status=applied + empty resume,
generates a tailored DOCX + PDF, stores both links in the sheet.

HOW FORMAT IS PRESERVED:
  resume_template.docx contains your exact layout with [[PLACEHOLDERS]].
  The script clones this file and replaces only the placeholder text strings.
  Every character of formatting (font, size, spacing, borders, bullets) is
  preserved because we never touch the XML structure — only the text nodes.

HOW COMPANY INTELLIGENCE WORKS (this is the actual smart part):
  For 20 major Bangalore hirers (Wipro, TCS, Goldman, Deloitte etc.), we have
  pre-baked knowledge of what they actually look for, what language their JDs
  use, and what Reddit/Glassdoor says about their interviews.
  This shapes HOW the bullets are written and which skills are foregrounded —
  not just a cringe "Eager to contribute to X's Y practice" line.
  For unknown companies, we scrape their about page and use that context.

NEW FILES NEEDED IN REPO ROOT:
  resume_template.docx  ← the template file (provided separately)

ADD TO requirements.txt:
  python-docx==1.1.2
  google-api-python-client==2.108.0
  beautifulsoup4==4.12.3
"""

import os, sys, re, json, time, copy, io, logging, requests
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
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

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence — pre-baked knowledge for major Bangalore hirers.
#
# The goal is NOT to add a generic "Eager to contribute..." line.
# The goal is to make the LLM write bullets and the summary using the
# vocabulary that these specific companies actually use in their JDs and
# interviews — based on what's known from Reddit, Glassdoor, and their
# public hiring patterns.
# ─────────────────────────────────────────────────────────────────────────────

COMPANY_INTEL = {
    # ── IT Services / MSSPs ──────────────────────────────────────────────────
    "wipro": {
        "focus":    "Managed security services for BFSI, manufacturing, healthcare clients. Wipro CyberDefense runs 24x7 SOC operations.",
        "framing":  "Wipro hires L1 SOC analysts primarily for 24x7 SIEM monitoring shifts. They value process adherence, SLA discipline, and documentation rigour over creative problem-solving. Emphasise structured triage, shift-ready documentation, and escalation workflows. ITIL awareness is a plus.",
        "keywords": ["SIEM operations", "24x7 SOC", "L1 analyst", "SLA adherence", "client delivery", "shift documentation", "incident escalation"],
        "emphasise_skills": ["Splunk", "SIEM", "alert triage", "incident escalation", "documentation", "SLA"],
    },
    "tcs": {
        "focus":    "Large enterprise security accounts; TCS Cyber Security practice covers compliance, ISMS, and VA/PT.",
        "framing":  "TCS values structured compliance processes and certification alignment. They frequently hire for ISO 27001-related roles, ISMS auditing, and vulnerability assessments. Emphasise compliance frameworks, audit documentation, and process orientation. CISA is valued.",
        "keywords": ["ISMS", "ISO 27001", "compliance audit", "vulnerability assessment", "CISA", "process adherence", "risk register"],
        "emphasise_skills": ["ISO 27001", "VAPT", "compliance", "audit documentation", "Python", "risk assessment"],
    },
    "infosys": {
        "focus":    "Infosys Cyber Security practice serves global clients; InfySecure is their internal security brand.",
        "framing":  "Infosys looks for analysts who can adapt to large, multi-client delivery environments. They value learning agility, documentation quality, and the ability to work in structured project teams. Emphasise cross-functional collaboration, client-facing communication, and certification progress.",
        "keywords": ["multi-client delivery", "security delivery", "structured analysis", "documentation quality", "InfySecure"],
        "emphasise_skills": ["Python", "Splunk", "documentation", "compliance", "risk assessment"],
    },
    "hcl": {
        "focus":    "HCL Technologies' cybersecurity practice (HCL SecureCloud) focuses on cloud security and managed detection & response.",
        "framing":  "HCL emphasises cloud-native security skills. Highlight AWS security experience, cloud IAM, and detection engineering. They value hands-on technical skills over pure theory.",
        "keywords": ["managed detection", "cloud security", "EDR/XDR", "AWS security", "cloud IAM", "SecureCloud"],
        "emphasise_skills": ["AWS", "cloud security", "EDR", "Python", "SIEM", "IAM"],
    },
    "cognizant": {
        "focus":    "Cognizant's cybersecurity practice serves BFSI and healthcare clients with SOC and compliance services.",
        "framing":  "Cognizant hires for 24x7 SOC operations and compliance roles. Emphasise your structured investigation approach, documentation discipline, and knowledge of security frameworks relevant to BFSI.",
        "keywords": ["SOC operations", "BFSI security", "compliance monitoring", "threat detection", "incident documentation"],
        "emphasise_skills": ["SIEM", "alert triage", "compliance", "documentation", "frameworks"],
    },
    "capgemini": {
        "focus":    "Capgemini cybersecurity services focus on GRC, cloud security, and managed SOC for European multinationals.",
        "framing":  "Capgemini values candidates who understand both technical controls and governance frameworks. They often bridge GRC consulting and technical security. Emphasise your ability to translate technical findings into compliance-relevant documentation.",
        "keywords": ["GRC", "cloud security", "security governance", "NIST", "compliance reporting"],
        "emphasise_skills": ["GRC", "NIST CSF", "cloud security", "compliance", "Python"],
    },

    # ── Big 4 consulting ─────────────────────────────────────────────────────
    "deloitte": {
        "focus":    "Deloitte Cyber Risk Advisory — BFSI-heavy GRC consulting, ITGC audits, third-party risk, and incident response.",
        "framing":  "Deloitte Cyber Risk hires consultants who can communicate risk in business terms. They value GRC framework knowledge (NIST, ISO, PCI-DSS), IT audit skills (ITGC, SOX), and the ability to write client-facing reports. Emphasise compliance gap analysis, risk quantification, and structured advisory thinking.",
        "keywords": ["cyber risk advisory", "ITGC", "SOX", "GRC consulting", "third-party risk", "risk advisory", "client deliverable"],
        "emphasise_skills": ["GRC", "ITGC", "ISO 27001", "NIST CSF", "PCI-DSS", "risk assessment", "audit documentation"],
    },
    "kpmg": {
        "focus":    "KPMG IT Advisory & Risk Consulting — IT audit (ITGC, SOX), IS audit, and cybersecurity assurance.",
        "framing":  "KPMG IT Advisory is one of the heaviest ITGC and IS audit practices in India. They hire associate consultants who understand control frameworks, audit evidence gathering, and SOX/ITGC testing. CISA (even in progress) is explicitly valued. Emphasise audit trail documentation, control testing, and regulatory compliance knowledge.",
        "keywords": ["IT audit", "ITGC", "SOX", "IS audit", "CISA", "control testing", "audit evidence", "assurance"],
        "emphasise_skills": ["IT audit", "ITGC", "ISO 27001", "PCI-DSS", "audit documentation", "compliance", "risk assessment"],
    },
    "pwc": {
        "focus":    "PwC Cyber Risk & Regulatory — GRC consulting, data privacy, and cyber transformation advisory.",
        "framing":  "PwC Cyber hires for advisory roles requiring strong written communication and framework knowledge. They value candidates who understand regulatory landscapes (RBI, SEBI, GDPR, PDPB) and can structure findings as risk recommendations. Emphasise regulatory compliance knowledge, privacy frameworks, and structured report writing.",
        "keywords": ["cyber risk", "regulatory compliance", "data privacy", "GDPR", "PDPB", "RBI compliance", "risk advisory"],
        "emphasise_skills": ["GRC", "data privacy", "GDPR", "NIST CSF", "regulatory compliance", "audit documentation"],
    },
    "ey": {
        "focus":    "EY GDS (Global Delivery Services) Bangalore — GRC, IT audit, data privacy, and risk assurance.",
        "framing":  "EY GDS Bangalore is a major IT audit and GRC delivery centre. They value process orientation, framework fluency, and the ability to execute standardised audit programs at scale. Emphasise your ability to follow structured methodologies, document findings per a defined format, and align to international standards.",
        "keywords": ["GRC", "IT audit", "risk assurance", "GDS delivery", "ITGC", "data protection", "ISO 27001"],
        "emphasise_skills": ["IT audit", "GRC", "ISO 27001", "NIST CSF", "compliance", "documentation", "data privacy"],
    },

    # ── Financial institutions ─────────────────────────────────────────────
    "jpmorgan": {
        "focus":    "JPMorgan Chase Bangalore — technology risk, cybersecurity operations, and compliance for global banking.",
        "framing":  "JPMorgan values candidates who understand both technical controls and regulatory risk. They apply Basel III operational risk frameworks and run internal cyber risk programs. Emphasise transaction monitoring instincts from Amazon experience, audit documentation discipline, and any knowledge of financial services compliance (AML, KYC, operational risk).",
        "keywords": ["technology risk", "operational risk", "cyber controls", "Basel", "AML", "transaction monitoring", "control assessment"],
        "emphasise_skills": ["operational risk", "AML", "compliance", "audit documentation", "Python", "risk assessment"],
    },
    "goldman sachs": {
        "focus":    "Goldman Sachs Bangalore — internal technology audit, SOC operations, and risk management.",
        "framing":  "Goldman Sachs internal audit team values rigorous documentation, independence, and the ability to challenge control owners. Their tech audit practice focuses on ITGC, cloud security controls, and application-level risks. Emphasise structured investigation methodology, audit evidence quality, and control-gap identification.",
        "keywords": ["technology audit", "ITGC", "internal audit", "control testing", "audit independence", "SOX"],
        "emphasise_skills": ["IT audit", "ITGC", "SOX", "audit documentation", "compliance", "Python"],
    },
    "deutsche bank": {
        "focus":    "Deutsche Bank Bangalore — information security, KYC/AML operations, and compliance for global banking.",
        "framing":  "Deutsche Bank Bangalore runs significant KYC, AML, and information security operations. For security roles, they value understanding of BFSI regulatory requirements and structured documentation. Emphasise investigative accuracy, AML/KYC process knowledge, and compliance documentation discipline.",
        "keywords": ["KYC", "AML", "information security", "BFSI compliance", "transaction monitoring", "regulatory"],
        "emphasise_skills": ["KYC", "AML", "compliance", "documentation", "transaction monitoring", "audit trail"],
    },
    "citi": {
        "focus":    "Citi Bangalore — risk analytics, fraud operations, and technology risk management.",
        "framing":  "Citi focuses heavily on risk analytics, fraud detection, and operational risk management. They look for structured analytical thinkers who can identify patterns in data. Emphasise your root-cause analysis skills from Amazon, anomaly detection instincts, and any Python/data skills relevant to fraud analytics.",
        "keywords": ["risk analytics", "fraud detection", "transaction monitoring", "operational risk", "pattern analysis"],
        "emphasise_skills": ["fraud detection", "Python", "risk assessment", "operational risk", "documentation", "anomaly detection"],
    },
    "amazon": {
        "focus":    "Amazon security engineering — AWS security, application security, and threat detection at scale.",
        "framing":  "Amazon values builder mentality, automation, and measurable outcomes. For security roles, they apply leadership principles directly: Customer Obsession, Dive Deep, Bias for Action, Insist on Highest Standards. Frame every bullet using LP vocabulary where possible. Emphasise automation, Python scripting, and the scale/impact of your work.",
        "keywords": ["customer obsession", "dive deep", "bias for action", "automation", "security at scale", "AWS", "builder"],
        "emphasise_skills": ["Python", "AWS", "automation", "Bash", "cloud security", "SIEM", "scripting"],
    },
    "google": {
        "focus":    "Google — security engineering, threat intelligence, and red team operations.",
        "framing":  "Google values technical depth, automation, and systems thinking. Emphasise your hands-on technical work, scripting/automation skills, and any evidence of systematic, repeatable security processes you built. Show curiosity and depth over breadth.",
        "keywords": ["security engineering", "automation", "threat analysis", "systems security", "scripting", "technical depth"],
        "emphasise_skills": ["Python", "Bash", "Linux", "automation", "threat intelligence", "SIEM", "AWS"],
    },
    "microsoft": {
        "focus":    "Microsoft — security operations, identity security, and compliance engineering.",
        "framing":  "Microsoft values engineers who can bridge identity, cloud, and security. Emphasise Azure/cloud security knowledge, Active Directory skills, and any experience with Microsoft tooling (Sentinel, Defender). Growth mindset language aligns with their culture.",
        "keywords": ["Azure security", "Microsoft Sentinel", "identity security", "cloud security", "Active Directory", "Zero Trust"],
        "emphasise_skills": ["Azure", "Active Directory", "cloud security", "IAM", "Python", "Splunk"],
    },

    # ── Insurance / Fintech ──────────────────────────────────────────────────
    "hdfc bank": {
        "focus":    "HDFC Bank — fraud analytics, information security, and compliance for India's largest private bank.",
        "framing":  "HDFC Bank security roles focus on fraud detection, AML, and RBI compliance. Emphasise transaction monitoring instincts, audit documentation rigour, and any familiarity with RBI IT/Cyber security framework or IS audit.",
        "keywords": ["fraud analytics", "AML", "RBI compliance", "IS audit", "transaction monitoring", "BFSI"],
        "emphasise_skills": ["fraud detection", "AML", "compliance", "audit documentation", "Python", "risk assessment"],
    },
    "bajaj finserv": {
        "focus":    "Bajaj Finserv — cybersecurity, fraud operations, and IT risk for a large NBFC.",
        "framing":  "Bajaj Finserv values candidates with fraud and risk domain knowledge for their large lending/insurance operations. Emphasise your investigative approach, policy-based decision making, and structured case documentation.",
        "keywords": ["fraud operations", "IT risk", "cyber security", "NBFC compliance", "RBI", "investigation"],
        "emphasise_skills": ["fraud detection", "risk assessment", "compliance", "documentation", "Python"],
    },
}

# Domain-to-project mapping — picks the 2 most relevant project templates per domain
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

# The four project templates. Two are selected per job.
PROJECTS = {
    "soc": {
        "title": "Cybersecurity SOC & Threat Detection Home Lab",
        "tech":  "Splunk (SPL), Wireshark, Nmap, Burp Suite, Linux, MITRE ATT&CK, TryHackMe",
        "bullets": [
            "Deployed Splunk SIEM and authored SPL correlation searches for brute-force detection (index=* | stats count by src_ip), lateral movement, and privilege escalation; mapped TTPs to MITRE ATT&CK (T1110, T1078, T1059) and wrote PICERL incident report.",
            "Analysed TCP/IP traffic in Wireshark — isolated HTTP/DNS flows, detected plaintext credential exposure, identified SYN scan patterns and DNS tunnelling anomalies indicative of C2 communication.",
            "Conducted network reconnaissance with Nmap (host discovery, OS fingerprinting, service-version detection) and manual SQL injection testing; documented OWASP Top 10 parameterised query remediation.",
            "Completed 9 TryHackMe rooms: Windows Event Logs, Active Directory Basics, Phishing Analysis, SIEM fundamentals, and threat hunting — building hands-on SOC Tier 1 analyst skills.",
        ],
    },
    "vapt": {
        "title": "Automated Vulnerability Scanning & CVE Reporting Tool",
        "tech":  "Python, Bash, Nessus, OpenVAS, Git, CVSS scoring",
        "bullets": [
            "Built automated vulnerability assessment pipeline integrating Nessus and OpenVAS REST APIs in Python; scheduled scans and generated CVE reports classified by CVSS severity with remediation guidance per NIST advisories.",
            "Automated scan scheduling via Bash and cron; parsed JSON scan results to extract CVE IDs, affected assets, and remediation timelines into structured reports for audit review.",
            "Implemented delta-scan logic comparing consecutive scan runs to highlight newly discovered vulnerabilities and track patch compliance status — simulating enterprise vulnerability management workflows.",
            "Documented SQL injection exploit at query level on a test target and applied OWASP Top 10 remediation, demonstrating practical DAST and AppSec assessment skills.",
        ],
    },
    "grc": {
        "title": "GRC Compliance Automation & Risk Intelligence Framework",
        "tech":  "Python, NIST CSF, ISO 27001, PCI-DSS, GDPR/PDPB, Excel, Git",
        "bullets": [
            "Developed Python GRC compliance tool mapping controls to NIST CSF, ISO 27001, and PCI-DSS; auto-generates gap analysis reports scoring each framework domain and flagging non-compliant controls for remediation.",
            "Built automated third-party vendor risk module computing quantitative risk scores across 12 domains (data access, security posture, regulatory compliance, business continuity) aligned with enterprise TPRM frameworks.",
            "Implemented transaction anomaly detection engine flagging structuring, rapid velocity, and dormant account reactivation patterns — applicable to fraud analyst and AML transaction monitoring roles.",
            "Developed GDPR/PDPB audit checklist evaluating consent management, data minimisation, and breach notification compliance; produced scored reports applicable to privacy analyst and DPO support roles.",
        ],
    },
    "cloud": {
        "title": "Cloud Security Posture & Threat Intelligence Monitoring Platform",
        "tech":  "Python, AWS (IAM, CloudTrail, GuardDuty, Security Hub), Elastic SIEM, VirusTotal API, Volatility, MITRE ATT&CK",
        "bullets": [
            "Built CSPM tool using boto3 auditing IAM configurations for over-privileged roles, unrotated access keys, public S3 misconfigurations, and disabled MFA; maps findings to CIS AWS Benchmark with remediation reports.",
            "Developed threat intelligence pipeline ingesting IOC feeds (VirusTotal, AbuseIPDB, MalwareBazaar APIs) and enriching SIEM alerts with malware classification, geolocation, and ATT&CK technique mapping.",
            "Automated DFIR triage using Volatility (memory forensics) to extract running processes, network connections, and injected shellcode; correlated with Windows Event Logs to reconstruct attacker timelines.",
            "Configured AWS CloudTrail + Elastic SIEM detection rules for IAM privilege escalation, unusual cross-region API calls, and CloudTrail log tampering; audited IAM policies against least-privilege principles.",
        ],
    },
}

AMAZON_BASE = [
    "Triaged 50+ weekly inventory reimbursement cases by severity and policy eligibility, mirroring the structured alert triage and escalation workflow used in SOC Tier 1 analyst roles.",
    "Performed root cause analysis on seller claims to identify policy violations and anomalous patterns; escalated findings to senior reviewers, demonstrating investigative instincts central to SOC and fraud analyst operations.",
    "Maintained audit-ready case documentation recording investigation findings, decisions, and corrective actions — establishing the evidence chain-of-custody discipline required for security incident reporting and IT audit.",
    "Collaborated with compliance and risk teams to enforce regulatory policies, identify process vulnerabilities, and implement corrective controls — building operational instincts in risk management and compliance monitoring.",
]


# ─────────────────────────────────────────────────────────────────────────────
# Company intelligence lookup
# ─────────────────────────────────────────────────────────────────────────────

def _clean_company_name(raw: str) -> str:
    """Strip tier annotations like '(MNC)', '(startup)' from the company name."""
    return re.sub(r"\s*\(.*?\)\s*$", "", raw).strip().lower()


def get_company_intel(company_raw: str) -> dict | None:
    """
    Return pre-baked company intelligence dict or None for unknown companies.
    Does partial matching so 'JPMorganChase' matches 'jpmorgan'.
    """
    name = _clean_company_name(company_raw)
    for key, intel in COMPANY_INTEL.items():
        if key in name or name in key:
            logger.info("  Company intel found: %s", key)
            return intel
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Company web scraping (fallback for unknown companies)
# ─────────────────────────────────────────────────────────────────────────────

_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def scrape_company_context(company_raw: str) -> str:
    """
    For companies not in COMPANY_INTEL, try to scrape their about page.
    Returns plain text context or empty string if scraping fails.
    This text is passed to the LLM as additional context only.
    """
    name = re.sub(r"\s*\(.*?\)\s*$", "", company_raw).strip()
    if not name or name.lower() in ("unknown", ""):
        return ""

    try:
        query = f"{name} company cybersecurity about mission"
        url   = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        resp  = requests.get(url, headers=_SCRAPE_HEADERS, timeout=8)
        soup  = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            if (href.startswith("http") and
                    not any(x in href for x in ["linkedin.com", "glassdoor.com",
                                                "indeed.com", "naukri.com"])):
                page = requests.get(href, headers=_SCRAPE_HEADERS, timeout=8)
                s2   = BeautifulSoup(page.text, "html.parser")
                for tag in s2(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                main = s2.find("main") or s2.find("article") or s2
                text = " ".join(
                    p.get_text(" ", strip=True)
                    for p in main.find_all("p") if len(p.get_text()) > 40
                )
                if len(text) > 100:
                    logger.info("  Scraped company context (%d chars)", len(text))
                    return text[:1200]
    except Exception as exc:
        logger.debug("Company scrape failed: %s", exc)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Groq LLM call
# ─────────────────────────────────────────────────────────────────────────────

def _call_groq(system: str, user: str, retries: int = 3) -> str:
    payload = {
        "model":       GROQ_MODEL,
        "temperature": 0.2,
        "max_tokens":  2200,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    }
    hdrs = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(GROQ_URL, json=payload, headers=hdrs, timeout=30)
            if r.status_code == 429:
                wait = 20 * attempt
                logger.warning("  Groq 429 — waiting %ds", wait)
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
    """
    Ask the LLM to generate all 18 placeholder values.
    The company intelligence shapes HOW the bullets are written and which
    skills are foregrounded — not just a surface-level summary line.
    """
    p1  = PROJECTS[p1_key]
    p2  = PROJECTS[p2_key]
    amz = AMAZON_BASE

    # Build company context section for the prompt
    if intel:
        company_section = f"""
COMPANY CONTEXT (pre-researched — use this to shape the tone and vocabulary):
  What they do: {intel['focus']}
  What to emphasise: {intel['framing']}
  Priority keywords to weave in naturally: {', '.join(intel['keywords'][:6])}
  Skills to foreground in this resume: {', '.join(intel['emphasise_skills'])}

These keywords and emphases should influence EVERY section — the summary, the
Amazon bullets, and the project bullets. Do NOT add a generic "Eager to contribute
to X's Y practice" line. Instead, let the vocabulary reflect the company naturally."""
    elif scraped_ctx:
        company_section = f"""
COMPANY CONTEXT (scraped from their website):
{scraped_ctx[:600]}

Use any relevant signals from this context to inform vocabulary choices.
Do NOT write a sentence like "Eager to contribute to X's Y" — that sounds generic.
Let the company context influence vocabulary and emphasis naturally."""
    else:
        company_section = ""

    system = (
        "You are a senior cybersecurity resume writer for the Indian job market. "
        "You write ATS-optimised, concise, honest bullets. "
        "You never fabricate tools, certifications, or experience that is not in the source material. "
        "Return ONLY a valid JSON object with exactly the keys listed — no markdown fences, "
        "no explanation, no extra keys, no commentary."
    )

    user = f"""
JOB:
  Title:   {job['job_title']}
  Company: {job['company']}
  Domain:  {job['domain']}
  Summary: {job['summary']}
  Skills:  {job['skills']}
{company_section}

Return a JSON object with EXACTLY these 18 keys:

{{
  "SUMMARY": "3-4 sentences. Open with 'Entry-level cybersecurity professional with hands-on experience in'. Weave in the top 3-4 keywords from the job title and domain naturally. Base only on skills that actually exist: Python, Bash, Linux, Splunk, Wireshark, Nmap, Burp Suite, Nessus, OpenVAS, MITRE ATT&CK, OWASP Top 10, NIST CSF, ISO 27001 (concepts), PCI-DSS (concepts), GDPR (concepts), TCP/IP, DNS, AWS basics, root cause analysis, audit documentation. No fabrications.",

  "SK_NET":  "Reorder so most job-relevant networking skills appear first. Source: TCP/IP, OSI model, DNS, HTTP/S, firewall concepts, IDS/IPS concepts",
  "SK_OS":   "Reorder so most job-relevant OS/scripting skills appear first. Source: Linux (grep, netstat, log analysis), Windows internals, Active Directory (basics), PowerShell (basic), Python, Bash",
  "SK_RISK": "Reorder so most job-relevant risk/investigation skills appear first. Source: Root cause analysis, policy-based case evaluation, audit documentation",
  "SK_SIEM": "Reorder so most job-relevant SIEM/tools appear first. Source: Splunk (SPL), Wireshark, PCAP analysis, Windows Event Logs, Nmap",
  "SK_SOC":  "Reorder so most job-relevant SOC skills appear first. Source: Alert triage, log analysis, security monitoring, threat detection, incident escalation, endpoint security, ticketing system workflows",
  "SK_FW":   "Reorder so most job-relevant frameworks appear first. Source: MITRE ATT&CK, Incident Response (PICERL), OWASP Top 10",

  "AMZ_B1": "Rewrite with 1-2 domain keywords naturally embedded (keep factual): {amz[0]}",
  "AMZ_B2": "Rewrite with 1-2 domain keywords naturally embedded (keep factual): {amz[1]}",
  "AMZ_B3": "Rewrite with 1-2 domain keywords naturally embedded (keep factual): {amz[2]}",
  "AMZ_B4": "Rewrite with 1-2 domain keywords naturally embedded (keep factual): {amz[3]}",

  "P1_TITLE": "{p1['title']}",
  "P1_TECH":  "{p1['tech']}",
  "P1_B1": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p1['bullets'][0]}",
  "P1_B2": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p1['bullets'][1]}",
  "P1_B3": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p1['bullets'][2]}",
  "P1_B4": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p1['bullets'][3]}",

  "P2_TITLE": "{p2['title']}",
  "P2_TECH":  "{p2['tech']}",
  "P2_B1": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p2['bullets'][0]}",
  "P2_B2": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p2['bullets'][1]}",
  "P2_B3": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p2['bullets'][2]}",
  "P2_B4": "Rewrite to emphasise relevance to this job domain (keep 100% factual): {p2['bullets'][3]}"
}}

Rules:
- Every bullet opens with a strong past-tense action verb (Built, Developed, Implemented, Conducted, Configured, Automated)
- Bullets must stay under 2 lines when rendered in 9.5pt Source Sans Pro
- For SK_* keys: output only the skill values after the colon — no category label
- Return raw JSON only — absolutely no ```json wrapper, no comments
"""

    raw = _call_groq(system, user)
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    try:
        content = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed: %s\nFirst 400: %s", exc, raw[:400])
        raise

    expected = [
        "SUMMARY",
        "SK_NET", "SK_OS", "SK_RISK", "SK_SIEM", "SK_SOC", "SK_FW",
        "AMZ_B1", "AMZ_B2", "AMZ_B3", "AMZ_B4",
        "P1_TITLE", "P1_TECH", "P1_B1", "P1_B2", "P1_B3", "P1_B4",
        "P2_TITLE", "P2_TECH", "P2_B1", "P2_B2", "P2_B3", "P2_B4",
    ]
    missing = [k for k in expected if k not in content]
    if missing:
        raise ValueError(f"LLM missing keys: {missing}")

    return content


# ─────────────────────────────────────────────────────────────────────────────
# DOCX placeholder replacement
# ─────────────────────────────────────────────────────────────────────────────

def _replace_in_para(para, placeholder: str, replacement: str) -> bool:
    """
    Replace a [[PLACEHOLDER]] that may be split across multiple runs.
    Merges all run text, replaces, puts result in run[0], clears the rest.
    Preserves run[0]'s formatting (font, size, bold etc).
    """
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
    """
    Open resume_template.docx, replace all [[PLACEHOLDERS]], return DOCX bytes.
    """
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"resume_template.docx not found at {TEMPLATE_PATH}. "
            "Upload it to your repo root. See RESUME_SETUP.md."
        )

    doc = Document(str(TEMPLATE_PATH))

    # Build full replacement map: [[KEY]] -> value
    replacements = {f"[[{k}]]": v for k, v in content.items()}

    for para in doc.paragraphs:
        for placeholder, replacement in replacements.items():
            if placeholder in "".join(r.text for r in para.runs):
                _replace_in_para(para, placeholder, replacement)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
# Google Drive upload + PDF export
# ─────────────────────────────────────────────────────────────────────────────

def _safe_name(s: str, max_len: int = 35) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "", s)[:max_len]


def upload_docx_and_export_pdf(drive_svc, docx_bytes: bytes, job: dict) -> tuple[str, str]:
    """
    1. Upload the tailored DOCX to Drive as a Google Doc (auto-convert).
       This gives us a Google Doc link (editable, formatted).
    2. Export the Google Doc as PDF using Drive's server-side rendering.
       This gives us a PDF link with 100% identical formatting.
    Returns (doc_link, pdf_link).
    """
    name = f"Resume_{_safe_name(job['job_title'])}_{_safe_name(job['company'])}".replace(" ", "_")

    # ── Step 1: Upload DOCX as Google Doc ────────────────────────────────────
    # mimeType in the body tells Drive to convert the uploaded DOCX to a Google Doc
    file_metadata = {
        "name":     name,
        "mimeType": "application/vnd.google-apps.document",
    }
    media = MediaIoBaseUpload(
        io.BytesIO(docx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        resumable=False,
    )
    uploaded = drive_svc.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    doc_id   = uploaded["id"]
    doc_link = uploaded["webViewLink"]
    logger.info("  Uploaded as Google Doc → %s", name)

    # ── Step 2: Make it publicly readable ────────────────────────────────────
    drive_svc.permissions().create(
        fileId=doc_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()

    # ── Step 3: Export as PDF ─────────────────────────────────────────────────
    # Google renders the Doc as PDF using its own engine — perfect formatting.
    pdf_bytes = drive_svc.files().export(
        fileId=doc_id,
        mimeType="application/pdf",
    ).execute()

    # ── Step 4: Upload PDF as a separate file ─────────────────────────────────
    pdf_uploaded = drive_svc.files().create(
        body={"name": name + ".pdf", "mimeType": "application/pdf"},
        media_body=MediaIoBaseUpload(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            resumable=False,
        ),
        fields="id, webViewLink",
    ).execute()

    pdf_id   = pdf_uploaded["id"]
    pdf_link = pdf_uploaded["webViewLink"]

    drive_svc.permissions().create(
        fileId=pdf_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()

    logger.info("  PDF exported and uploaded.")
    return doc_link, pdf_link


# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_creds() -> Credentials:
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)


def ensure_columns(ws) -> tuple[int, int]:
    """Ensure resume_doc_link and resume_pdf_link columns exist. Return 1-based indices."""
    headers = ws.row_values(1)

    def _ensure(name: str) -> int:
        if name not in headers:
            idx = len(headers) + 1
            ws.update_cell(1, idx, name)
            headers.append(name)
            logger.info("Added column '%s' at position %d.", name, idx)
        return headers.index(name) + 1

    return _ensure("resume_doc_link"), _ensure("resume_pdf_link")


def get_pending_jobs(ws, doc_col: int) -> list[dict]:
    """Return rows where status=applied and resume_doc_link is empty."""
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
        if status == "applied" and not doc_link:
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
        logger.error("resume_template.docx not found in repo root. See RESUME_SETUP.md.")
        sys.exit(1)

    creds     = _get_creds()
    gc        = gspread.authorize(creds)
    ws        = gc.open(SHEET_NAME).sheet1
    drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    logger.info("Connected to Google Sheets and Drive.")

    doc_col, pdf_col = ensure_columns(ws)

    pending = get_pending_jobs(ws, doc_col)
    if not pending:
        logger.info("No jobs with status='applied' and empty resume_doc_link.")
        logger.info("Mark a job as 'applied' in the sheet to trigger resume generation.")
        sys.exit(0)

    logger.info("Found %d pending job(s). Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-" * 50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)",
                    i, len(pending), job["job_title"], job["company"], job["domain"])
        try:
            # 1. Select the two most relevant projects
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc", "grc"))
            logger.info("  Projects: %s + %s", p1_key, p2_key)

            # 2. Get company intelligence (pre-baked or scraped)
            intel = get_company_intel(job["company"])
            scraped_ctx = ""
            if intel is None:
                logger.info("  No pre-baked intel — trying scrape...")
                scraped_ctx = scrape_company_context(job["company"])

            # 3. Generate tailored text content via Groq
            logger.info("  Generating content via Groq...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx)
            logger.info("  Content generated.")

            # 4. Fill the template DOCX with the content
            docx_bytes = fill_template(content)
            logger.info("  Template filled (%d bytes).", len(docx_bytes))

            # 5. Upload DOCX → Google Doc, export as PDF
            doc_link, pdf_link = upload_docx_and_export_pdf(drive_svc, docx_bytes, job)

            # 6. Store links in sheet
            ws.update_cell(job["row_num"], doc_col, doc_link)
            ws.update_cell(job["row_num"], pdf_col, pdf_link)
            logger.info("  ✓ Sheet updated.")
            logger.info("  Doc: %s", doc_link)
            logger.info("  PDF: %s", pdf_link)

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
