"""
config.py — Single source of truth for all constants.
"""
import os

# ── Google Sheets ─────────────────────────────────────────────────────────────
SHEET_NAME     = "WalkIn Jobs Bangalore"
WORKSHEET_NAME = "Jobs"

# Matches your actual sheet header row exactly.
# walk_in_date / walk_in_time REMOVED — project now scrapes online jobs, not walk-ins.
# fit_for_fresher + reasoning ADDED — AI verdicts surfaced directly in the sheet
# so you can filter and sort without reading every Telegram message.
SHEET_COLUMNS = [
    "scraped_at",
    "job_title",
    "company",
    "company_tier",
    "location_address",
    "contact",
    "legitimacy_score",
    "fit_for_fresher",   # "Yes" / "No" — can a 0-exp Sec+ holder realistically apply?
    "reasoning",         # one-sentence AI explanation of the score
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
    # ── Internship / Entry-Level / Fresher ────────────────────────────────────
    # These are the titles explicitly welcoming 0-experience candidates.
    # Indian job portals use all of these phrasings — every variant is needed
    # because portals like Naukri, LinkedIn India, and Indeed India each use
    # different vocabulary for the same type of role.
    "security intern", "infosec intern",
    "soc intern", "it security intern", "network security intern",
    "cloud security intern", "security operations intern",
    "cyber fresher", "it security fresher",
    "soc fresher", "security graduate trainee", "graduate security analyst",
    "security associate trainee", "junior security analyst",
    "junior soc analyst", "junior infosec analyst",
    "junior cybersecurity analyst", "entry level security",
    "entry level soc", "entry level analyst",
    "security apprentice",
    # Tier-1 SOC / helpdesk-adjacent titles reachable at 0 exp — important
    # stepping-stone roles that Sec+ holders are specifically targeted for
    "soc tier 1", "tier 1 soc", "l1 soc analyst", "soc analyst l1",
    "security analyst l1", "security analyst level 1",
    "it security support", "security helpdesk", "security support analyst",
    # Common phrasing on Indian portals for no-experience roles
    "fresher security analyst", "security analyst fresher",
    "0-1 year security", "0-2 year security",
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

# ── Entry-Level / Fresher Targeting ──────────────────────────────────────────
# These three lists are injected directly into the Groq AI scoring prompt,
# giving the model an explicit rubric instead of asking it to guess.
# Think of them as the AI's marking scheme — the more specific you make them,
# the more consistent and useful the scores will be.

# Jobs mentioning any of these phrases score HIGHER — they are reachable for
# a Sec+ holder with 0 experience. The AI will also set fit_for_fresher=true.
ENTRY_LEVEL_BOOST_KEYWORDS = [
    "fresher", "freshers welcome", "freshers can apply",
    "0 experience", "no experience required", "entry level", "entry-level",
    "0-1 year", "0-2 years", "0 to 1 year", "0 to 2 years",
    "recent graduate", "fresh graduate", "campus hire", "campus recruitment",
    "trainee", "apprentice", "graduate program", "graduate trainee",
    "security+", "sec+", "comptia security+", "comptia",
    "certification preferred", "cert holders welcome",
    "will train", "training provided", "on the job training",
]

# Jobs mentioning any of these are OUT OF REACH right now — the AI will lower
# the score and set fit_for_fresher=false so these don't flood your alerts.
# A job can be 100% legitimate and still be wrong for a 0-exp candidate.
EXPERIENCE_MISMATCH_KEYWORDS = [
    "5+ years", "6+ years", "7+ years", "8+ years", "10+ years",
    "minimum 3 years", "minimum 4 years", "minimum 5 years",
    "senior", "lead", "principal", "staff engineer",
    "manager", "head of", "director", "vp ", "ciso",
    "must have led", "proven track record of managing",
]

# Each red flag found lowers the score. Centralising these here means you only
# need to update this list — the AI prompt picks them up automatically.
RED_FLAG_KEYWORDS = [
    "commission only", "own laptop required", "security deposit",
    "registration fee", "processing fee", "pay to join", "pay to train",
    "multi-level", "mlm", "network marketing",
    "no salary mentioned", "earn from home", "unlimited income",
    "whatsapp interview only", "contact on whatsapp to apply",
    "no experience needed for senior",   # internal contradiction = red flag
    "immediate joiner only",             # pressure tactic
]

# ── Scraper Behaviour ─────────────────────────────────────────────────────────
MAX_JOB_AGE_DAYS  = 7    # ignore postings older than this many days
MAX_JOBS_PER_RUN  = 50   # cap alerts per run to avoid Telegram flooding
DEDUP_WINDOW_DAYS = 14   # skip a URL already seen in the last N days
