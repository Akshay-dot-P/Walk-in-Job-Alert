"""
config.py — Single source of truth for all constants.
"""
import os

# ── Google Sheets ─────────────────────────────────────────────────────────────
SHEET_NAME     = "WalkIn Jobs Bangalore"
WORKSHEET_NAME = "Jobs"

# Matches your actual sheet header row exactly.
# walk_in_date / walk_in_time REMOVED per user request (online jobs, not walk-ins).
SHEET_COLUMNS = [
    "scraped_at",
    "job_title",
    "company",
    "company_tier",
    "location_address",
    "contact",
    "legitimacy_score",
    "red_flags",
    "source",
    "url",
    "status",
]

# ── AI Scoring ────────────────────────────────────────────────────────────────
GROQ_MODEL           = "llama-3.1-8b-instant"
SCORE_THRESHOLD      = 6
MIN_LEGITIMACY_SCORE = SCORE_THRESHOLD   # alias used by scanner.py

# ── Secrets ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN      = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY            = os.environ.get("GROQ_API_KEY", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

# ── Role Targeting ────────────────────────────────────────────────────────────
TARGET_ROLES = [
    # SOC / Blue Team
    "soc analyst", "soc l1", "soc l2", "security operations",
    "security monitoring", "siem analyst", "splunk analyst",
    "incident response", "threat detection",
    # AppSec / DevSecOps
    "application security", "appsec", "appsec engineer",
    "secure code review", "sast", "dast", "security engineer", "devsecops",
    # VAPT / Pentest
    "vapt", "penetration test", "penetration tester", "pentest",
    "ethical hacker", "ethical hacking", "red team", "bug bounty",
    "offensive security",
    # Vulnerability Management
    "vulnerability assessment", "vulnerability management",
    "patch management", "threat assessment", "security assessment",
    "cvss", "cve analyst",
    # GRC / Compliance
    "grc analyst", "grc", "governance risk compliance",
    "compliance analyst", "regulatory compliance",
    "iso 27001", "iso27001", "nist", "pci dss", "data privacy",
    "gdpr", "dpo", "privacy analyst",
    # IT / IS Audit
    "it audit", "is audit", "information systems audit",
    "internal audit", "it risk", "itgc", "cisa",
    # Risk Analyst (BFSI)
    "risk analyst", "operational risk", "credit risk",
    "market risk", "enterprise risk", "basel", "rcsa",
    "risk management", "risk associate",
    # Fraud / AML / KYC
    "fraud analyst", "aml analyst", "kyc analyst",
    "anti-money laundering", "transaction monitoring",
    "financial crime", "fincrime", "sanctions analyst",
    "orc analyst", "loss prevention",
    # Network / Cloud / IAM / DLP
    "network security", "cloud security", "iam analyst",
    "identity access management", "dlp analyst", "pam",
    "zero trust", "firewall analyst",
    # General InfoSec / Intern / Fresher
    "cybersecurity", "cyber security", "information security",
    "infosec", "security analyst", "security intern",
    "cybersecurity intern", "security trainee", "security fresher",
    "cyber analyst",
    # Threat Intelligence
    "threat intelligence", "cyber threat intelligence", "cti analyst",
    "threat hunter", "threat hunting", "ioc analyst",
    # DFIR / Malware Analysis
    "dfir", "digital forensics", "forensic analyst", "malware analyst",
    "malware reverse engineering", "reverse engineer", "incident handler",
    "memory forensics",
    # Security Architecture
    "security architect", "cloud security architect",
    "enterprise security architect", "solution security architect",
    # Endpoint / EDR
    "endpoint security", "edr analyst", "xdr analyst",
    "endpoint detection", "carbon black", "crowdstrike analyst",
    # Third-Party / Vendor Risk
    "vendor risk", "third party risk", "tprm",
    "supply chain risk", "vendor assessment",
    # OT / ICS / SCADA (niche but growing)
    "ot security", "ics security", "scada security",
    "operational technology security", "industrial cybersecurity",
    # Cloud-native security (AWS/Azure/GCP specific)
    "aws security", "azure security", "gcp security",
    "cloud compliance", "cloud governance", "cnapp",
    "casb analyst", "cloud posture",
]

# ── Known MNCs (score bonus) ──────────────────────────────────────────────────
KNOWN_MNCS = [
    "accenture", "ibm", "deloitte", "kpmg", "pwc", "ey", "ernst",
    "wipro", "infosys", "tcs", "hcl", "cognizant", "capgemini",
    "tech mahindra", "mphasis", "hexaware",
    "palo alto", "crowdstrike", "mandiant", "microsoft", "cisco",
    "symantec", "mcafee", "fortinet", "secureworks", "qualys",
    "rapid7", "tenable", "check point", "cyberark", "sailpoint",
    "hdfc bank", "icici bank", "axis bank", "kotak mahindra",
    "sbi", "state bank", "yes bank", "indusind",
    "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
    "citi", "citibank", "barclays", "deutsche bank", "bnp paribas",
    "hsbc", "standard chartered", "societe generale", "ubs",
    "bajaj finserv", "bajaj allianz", "hdfc life", "icici prudential",
    "max life", "aditya birla", "razorpay", "paytm", "phonepe",
    "wells fargo", "american express", "amex", "mastercard",
    "visa", "paypal", "fidelity", "blackrock", "state street",
    "bdo", "grant thornton",
]
