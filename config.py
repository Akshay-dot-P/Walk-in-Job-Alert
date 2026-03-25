"""
config.py — Single source of truth for all constants.
"""
import os

# ── Google Sheets ─────────────────────────────────────────────────────────────
SHEET_NAME     = "WalkIn Jobs Bangalore"
WORKSHEET_NAME = "Jobs"

# Must match your actual Google Sheet header row exactly (case-sensitive).
# walk_in_date / walk_in_time removed — project now scrapes online postings.
# fit_for_fresher / reasoning removed — not in the actual sheet.
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
# Why llama-3.1-8b-instant:
#   - Free tier: 14,400 RPD and 131,072 TPM
#   - Our usage: 9 batch calls × 3 runs/day = 27 calls/day (0.2% of limit)
#   - Tokens/run: ~20,000 (well under 131,072 TPM)
#   - llama-3.3-70b only has 1,000 RPD → retry storms blow it instantly
#   - gemma2-9b has only 15,000 TPM → one run exceeds it
GROQ_MODEL           = "llama-3.1-8b-instant"
SCORE_THRESHOLD      = 6
MIN_LEGITIMACY_SCORE = SCORE_THRESHOLD   # alias used by scanner.py

# ── Secrets (injected via GitHub Actions secrets / local .env) ────────────────
TELEGRAM_BOT_TOKEN      = os.environ.get("TELEGRAM_TOKEN", "")       # matches workflow secret name
TELEGRAM_CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "")
GROQ_API_KEY            = os.environ.get("GROQ_API_KEY", "")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")    # matches workflow secret name

# ── Role Targeting ────────────────────────────────────────────────────────────
# Matched case-insensitively against title + description.
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
    # Threat Intelligence / DFIR
    "threat intelligence", "cyber threat intelligence", "cti analyst",
    "threat hunter", "dfir", "digital forensics", "forensic analyst",
    "malware analyst", "incident handler",
    # Endpoint / EDR
    "endpoint security", "edr analyst", "xdr analyst", "endpoint detection",
    # Vendor / Third-Party Risk
    "vendor risk", "third party risk", "tprm", "supply chain risk",
    # Cloud-native security
    "aws security", "azure security", "gcp security",
    "cloud compliance", "cloud governance", "casb analyst",
    # General InfoSec / Intern / Fresher
    "cybersecurity", "cyber security", "information security",
    "infosec", "security analyst", "security intern",
    "cybersecurity intern", "security trainee", "security fresher",
    "cyber analyst", "junior soc analyst", "l1 soc analyst",
    "entry level security", "entry level soc", "security apprentice",
]

# ── Known MNCs (give a score bonus in scorer.py) ──────────────────────────────
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

# ── Scraper Behaviour ─────────────────────────────────────────────────────────
MAX_JOB_AGE_DAYS  = 7
MAX_JOBS_PER_RUN  = 50
DEDUP_WINDOW_DAYS = 14
