TARGET_ROLES = [
    "cloud engineer", "cloud architect", "cloud developer",
    "aws", "gcp", "azure",
    "sde", "software developer", "software engineer",
    "backend engineer", "backend developer",
    "sre", "site reliability", "platform engineer", "devops",
    "security analyst", "infosec", "cybersecurity", "information security",
    "application security",
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
]

MIN_LEGITIMACY_SCORE = 5

GROQ_MODEL = "llama3-8b-8192"

SHEET_COLUMNS = [
    "scraped_at", "job_title", "company", "company_tier",
    "walk_in_date", "walk_in_time", "location_address",
    "contact", "legitimacy_score", "red_flags",
    "source", "url", "status",
]

# Working RSS feeds as of 2025 — verified active
RSS_FEEDS = [
    "https://in.indeed.com/rss?q=walk+in+interview+cloud+SDE+SRE+security&l=Bangalore&sort=date&fromage=2",
    "https://in.indeed.com/rss?q=walkin+drive+bangalore+software+engineer&sort=date&fromage=2",
]

NAUKRI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "appid": "109",
    "systemid": "109",
    "referer": "https://www.naukri.com/",
    "origin": "https://www.naukri.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
