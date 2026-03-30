TARGET_ROLES = [
    # SOC / Blue Team
    "soc analyst", "security operations", "blue team", "cyber defense analyst",
    "l1 analyst", "l2 analyst", "tier 1 analyst", "tier 2 analyst",
    # SIEM
    "siem analyst", "siem engineer", "splunk analyst", "qradar", "sentinel analyst",
    "security monitoring analyst", "log analysis",
    # Threat Intelligence
    "threat intelligence", "cti analyst", "threat hunting", "osint analyst",
    "threat research", "dark web analyst",
    # Incident Response / DFIR
    "incident response", "ir analyst", "dfir", "digital forensics",
    "computer forensics", "ediscovery", "forensic analyst", "forensic investigator",
    # VAPT / Pentest
    "vapt", "penetration test", "ethical hacker", "pentest", "red team",
    "bug bounty", "vulnerability researcher", "offensive security",
    "web application pentest", "network pentest", "mobile pentest", "api security",
    # Vulnerability Management
    "vulnerability analyst", "vulnerability management", "va analyst",
    "patch management", "qualys", "tenable", "nessus", "threat assessment",
    # AppSec / DevSecOps
    "application security", "appsec", "devsecops", "dast", "sast",
    "secure code review", "software security", "product security",
    # Network / Infra Security
    "network security", "firewall engineer", "ids ips", "palo alto",
    "fortinet", "cisco security", "endpoint security", "systems security",
    "infrastructure security",
    # Cloud Security
    "cloud security", "aws security", "azure security", "gcp security",
    "cspm", "cloud compliance", "cloud iam", "cloud forensic", "ccsp",
    "cloud security officer", "ccso",
    # IAM / PAM / DLP
    "iam analyst", "identity access management", "pam analyst",
    "privileged access", "dlp analyst", "data loss prevention",
    "sailpoint", "cyberark", "okta analyst", "zero trust", "idam",
    "access governance", "identity governance",
    # GRC
    "grc analyst", "it grc", "cyber grc", "iso 27001", "soc 2 analyst",
    "nist analyst", "third party risk", "tprm", "vendor risk",
    "supply chain risk", "cis controls",
    # IT Audit / IS Audit
    "it audit", "is audit", "it auditor", "itgc", "cisa",
    "technology audit", "cyber audit", "internal audit",
    # Risk
    "risk analyst", "operational risk", "cyber risk", "it risk",
    "enterprise risk", "erm analyst", "rcsa", "basel analyst",
    "business continuity", "bcp analyst", "dr analyst", "loss event",
    # Compliance
    "compliance analyst", "regulatory compliance", "pci dss", "sox compliance",
    "rbi compliance", "sebi compliance", "irdai compliance", "pdpb",
    "data governance", "compliance monitoring",
    # Fraud / AML / KYC
    "fraud analyst", "fraud detection", "aml analyst", "anti-money laundering",
    "kyc analyst", "kyc associate", "transaction monitoring",
    "financial crime", "fcrm", "sanctions analyst", "ubo analyst",
    "cdd analyst", "str analyst", "cft analyst",
    # Privacy
    "data privacy", "privacy analyst", "dpo", "data protection",
    "gdpr analyst", "pdpb analyst", "privacy compliance", "cipp",
    "consent management", "privacy engineer",
    # Malware / Forensics
    "malware analyst", "malware researcher", "sandbox analyst",
    "reverse engineer", "binary analysis", "memory forensics",
    "mobile forensics", "cryptographer",
    # General
    "cybersecurity analyst", "security analyst", "information security",
    "infosec analyst", "cyber analyst", "security engineer",
    "security awareness", "security researcher",
    # Indian market specific titles
    "associate security analyst", "junior security officer",
    "executive information security", "technology risk associate",
    "cyber risk associate", "security management trainee",
    "security graduate trainee", "security officer trainee",
    "security apprentice",
    # INTERN titles — every variant used by Indian recruiters
    "security intern", "cybersecurity intern", "cyber security intern",
    "infosec intern", "soc intern", "grc intern", "it audit intern",
    "risk intern", "compliance intern", "cloud security intern",
    "network security intern", "fraud analyst intern", "kyc intern",
    "aml intern", "threat intelligence intern", "vapt intern",
    "penetration testing intern", "data privacy intern",
    "appsec intern", "devsecops intern", "security research intern",
    "vulnerability assessment intern", "iam intern",
]

WALKIN_KEYWORDS = [
    "walk-in", "walk in", "walkin",
    "walk-in interview", "walk in interview", "walkin interview",
    "direct interview", "direct hiring",
    "mega drive", "hiring drive", "recruitment drive",
    "campus drive", "open house", "spot offer",
]

# Intern-specific detection keywords
INTERN_KEYWORDS = [
    "intern", "internship", "trainee", "apprentice",
    "stipend", "6 month internship", "3 month internship",
    "summer intern", "winter intern", "fellowship",
    "graduate trainee", "management trainee",
    "campus hire", "fresher program", "graduate program",
    "associate program", "entry program",
]

BANGALORE_KEYWORDS = [
    "bangalore", "bengaluru", "blr",
    "koramangala", "whitefield", "electronic city",
    "indiranagar", "hsr layout", "btm layout",
    "marathahalli", "sarjapur", "bellandur",
    "hebbal", "yeshwanthpur", "jp nagar",
    "manyata", "ecospace", "bagmane", "brookefield",
]

# Lowered to 4 to catch intern/fresher postings which are often less formal
MIN_LEGITIMACY_SCORE = 4

GROQ_MODEL = "llama-3.1-8b-instant"

SHEET_COLUMNS = [
    "scraped_at", "job_title", "company", "company_tier", "domain",
    "legitimacy_score", "red_flags", "summary",
    "is_intern", "is_fresher_eligible", "experience_required",
    "work_mode", "skills_required", "salary_range",
    "apply_url", "notice_period", "openings_count",
    "posted_date", "application_deadline",
    "source", "url", "status",
]
