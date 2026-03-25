"""
config.py — Central configuration for the Walk-In Job Alert scanner.
"""

# ── AI Scoring ────────────────────────────────────────────────────────────────
GROQ_MODEL = "llama-3.1-8b-instant"       # Current free-tier fast model (llama3-8b-8192 deprecated)
SCORE_THRESHOLD = 6                        # Listings below this score are dropped

# ── Role Targeting ────────────────────────────────────────────────────────────
# All keywords are matched case-insensitively against title + description.
# Organised by bucket — add/remove freely.
TARGET_ROLES = [
    # ── SOC / Blue Team ──────────────────────────────────────────────────────
    "soc analyst", "soc l1", "soc l2", "security operations",
    "security monitoring", "siem analyst", "splunk analyst",
    "incident response", "threat detection",

    # ── AppSec / Secure Development ──────────────────────────────────────────
    "application security", "appsec", "appsec engineer",
    "secure code review", "sast", "dast", "security engineer",
    "devsecops",

    # ── VAPT / Offensive Security ─────────────────────────────────────────────
    "vapt", "penetration test", "penetration tester", "pentest",
    "ethical hacker", "ethical hacking", "red team", "bug bounty",
    "offensive security",

    # ── Vulnerability Management ─────────────────────────────────────────────
    "vulnerability assessment", "vulnerability management",
    "patch management", "threat assessment", "security assessment",
    "cvss", "cve analyst",

    # ── GRC / Compliance / Policy ─────────────────────────────────────────────
    "grc analyst", "grc", "governance risk compliance",
    "compliance analyst", "regulatory compliance",
    "iso 27001", "iso27001", "nist", "pci dss", "data privacy",
    "gdpr", "dpo", "privacy analyst",

    # ── IT Audit / IS Audit ───────────────────────────────────────────────────
    "it audit", "is audit", "information systems audit",
    "internal audit", "it risk", "itgc", "cisa",

    # ── Risk Analyst (BFSI) ───────────────────────────────────────────────────
    "risk analyst", "operational risk", "credit risk",
    "market risk", "enterprise risk", "basel", "rcsa",
    "risk management", "risk associate",

    # ── Fraud / AML / KYC / FinCrime ─────────────────────────────────────────
    "fraud analyst", "aml analyst", "kyc analyst",
    "anti-money laundering", "transaction monitoring",
    "financial crime", "fincrime", "sanctions analyst",
    "orc analyst", "loss prevention",

    # ── Network / Cloud / IAM / DLP ──────────────────────────────────────────
    "network security", "cloud security", "iam analyst",
    "identity access management", "dlp analyst", "pam",
    "zero trust", "firewall analyst", "nac",

    # ── General InfoSec / Entry Level ────────────────────────────────────────
    "cybersecurity", "cyber security", "information security",
    "infosec", "security analyst", "security intern",
    "cybersecurity intern", "security trainee", "security fresher",
    "cyber analyst",
]

# ── Known MNCs / Top Employers ────────────────────────────────────────────────
# Listings from these companies get a +1 score bonus.
KNOWN_MNCS = [
    # Big Tech / IT Services
    "accenture", "ibm", "deloitte", "kpmg", "pwc", "ey", "ernst",
    "wipro", "infosys", "tcs", "hcl", "cognizant", "capgemini",
    "tech mahindra", "mphasis", "hexaware", "niit technologies",
    "lt infotech", "l&t technology",

    # Cybersecurity Specialists
    "palo alto", "crowdstrike", "mandiant", "google cloud",
    "microsoft", "cisco", "symantec", "mcafee", "fortinet",
    "secureworks", "qualys", "rapid7", "tenable",
    "check point", "cyberark", "sailpoint",

    # BFSI — Banks & Financial Services
    "hdfc bank", "icici bank", "axis bank", "kotak mahindra",
    "sbi", "state bank", "yes bank", "indusind",
    "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
    "citi", "citibank", "barclays", "deutsche bank", "bnp paribas",
    "hsbc", "standard chartered", "rbs", "credit suisse",
    "societe generale", "ubs",

    # BFSI — Insurance & Fintech
    "bajaj finserv", "bajaj allianz", "hdfc life",
    "icici prudential", "max life", "aditya birla",
    "razorpay", "paytm", "phonepe", "policybazaar",

    # Big 4 Consulting (GRC heavy)
    "deloitte", "pwc", "kpmg", "ey", "bdo", "grant thornton",

    # Global Capability Centres (GCCs)
    "wells fargo", "american express", "amex", "mastercard",
    "visa", "paypal", "stripe", "fidelity", "blackrock", "state street",
]
