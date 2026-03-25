# =============================================================================
# config.py
# =============================================================================

TARGET_ROLES = [
    # Security
    "application security", "appsec", "app sec",
    "security analyst", "sec analyst", "security engineer",
    "information security", "infosec", "cybersecurity", "cyber security",
    "social engineer", "social engineering",
    "penetration tester", "pentester", "vapt",
    "soc analyst", "soc engineer", "threat analyst",
    "vulnerability analyst", "security operations",
    "network security", "cloud security",
    # GRC / Compliance
    "grc", "governance risk compliance",
    "compliance analyst", "compliance officer", "compliance executive",
    "regulatory compliance", "it compliance",
    "audit analyst", "internal audit", "it audit",
    "policy analyst",
    # Risk
    "risk analyst", "risk associate", "risk officer",
    "credit risk", "operational risk", "market risk", "enterprise risk",
    "risk management",
    # Fraud / ORC
    "fraud analyst", "fraud investigator", "fraud detection",
    "anti-fraud", "anti money laundering", "aml analyst",
    "orc analyst", "organized retail crime", "loss prevention",
    "financial crimes", "transaction monitoring",
    # Intern / Entry-level
    "intern", "internship", "trainee", "fresher", "graduate trainee",
    "junior analyst", "associate analyst", "entry level",
]

WALKIN_KEYWORDS = [
    "walk-in", "walk in", "walkin",
    "walk-in interview", "walk in interview", "walkin interview",
    "direct interview", "direct hiring", "no appointment",
    "mega drive", "hiring drive", "recruitment drive",
    "campus drive", "open house", "spot offer", "fresher drive",
]

BANGALORE_KEYWORDS = [
    "bangalore", "bengaluru", "blr",
    "koramangala", "whitefield", "electronic city",
    "indiranagar", "hsr layout", "btm layout",
    "marathahalli", "sarjapur", "bellandur",
    "hebbal", "yeshwanthpur", "jayanagar",
    "jp nagar", "manyata", "ecospace",
    "bagmane", "brookefield",
]

KNOWN_MNCS = [
    "infosys", "wipro", "tcs", "hcl", "tech mahindra", "cognizant",
    "accenture", "ibm", "capgemini", "oracle", "microsoft", "google",
    "amazon", "aws", "deloitte", "ey", "kpmg", "pwc",
    "cisco", "hp", "dell", "sap", "salesforce", "servicenow",
    "dxc", "ntt", "atos", "unisys", "mindtree", "mphasis",
    "hexaware", "ltimindtree", "persistent", "birlasoft",
    # BFSI — relevant for fraud/risk/compliance roles
    "hdfc", "icici", "axis bank", "kotak", "sbi", "rbi",
    "jpmorgan", "jp morgan", "goldman sachs", "morgan stanley",
    "citibank", "hsbc", "barclays", "standard chartered",
    "bajaj finserv", "paytm", "phonepe", "razorpay",
]

MIN_LEGITIMACY_SCORE = 5

# llama-3.1-8b-instant: current recommended free-tier fast model
# (llama3-8b-8192 is deprecated as of mid-2025)
GROQ_MODEL = "llama-3.1-8b-instant"

SHEET_COLUMNS = [
    "scraped_at",
    "job_title",
    "company",
    "company_tier",
    "walk_in_date",
    "walk_in_time",
    "location_address",
    "contact",
    "legitimacy_score",
    "red_flags",
    "source",
    "url",
    "status",
]

# Active sources (LinkedIn, Indeed, Glassdoor, Naukri)
# ZipRecruiter dropped: US/Canada only by design
# Google Jobs dropped: needs browser-session query syntax
ACTIVE_SOURCES = ["linkedin", "indeed", "glassdoor", "naukri"]
