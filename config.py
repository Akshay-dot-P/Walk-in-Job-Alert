# =============================================================================
# config.py
# =============================================================================
# This file is the single source of truth for all settings.
# Instead of scattering magic strings and numbers throughout the codebase,
# we collect them here. This makes tuning easy — if you want to add "data
# engineer" as a target role, you add it in ONE place, and every module that
# imports this file will automatically pick up the change.
# =============================================================================

# ---------------------------------------------------------------------------
# TARGET ROLES
# We cast a wide net with synonyms. Naukri and LinkedIn both use different
# terminology — one says "SRE", another says "site reliability engineer",
# another says "platform engineer". We check if ANY of these substrings
# appear anywhere in the job title or description (case-insensitive).
# ---------------------------------------------------------------------------
TARGET_ROLES = [
    "cloud engineer",
    "cloud architect",
    "cloud developer",
    "aws",
    "gcp",
    "azure",
    "sde",
    "software developer",
    "software engineer",
    "backend engineer",
    "backend developer",
    "sre",
    "site reliability",
    "platform engineer",
    "devops",
    "security analyst",
    "infosec",
    "cybersecurity",
    "information security",
    "application security",
]

# ---------------------------------------------------------------------------
# WALK-IN DETECTION KEYWORDS
# These are phrases that, when found in a job title OR description, strongly
# suggest the listing is for a walk-in event rather than a regular apply-online
# job. The pipe character is used later in regex (keyword1|keyword2|...).
# ---------------------------------------------------------------------------
WALKIN_KEYWORDS = [
    "walk-in",
    "walk in",
    "walkin",
    "walk-in interview",
    "walk in interview",
    "walkin interview",
    "direct interview",
    "direct hiring",
    "no appointment",
    "mega drive",
    "hiring drive",
    "recruitment drive",
    "campus drive",
    "open house",
    "spot offer",
    "direct walk",
    "fresher drive",
    "lateral drive",
]

# ---------------------------------------------------------------------------
# BANGALORE LOCATION KEYWORDS
# Listings sometimes say "Bengaluru", sometimes "Bangalore", sometimes just
# a neighbourhood like "Whitefield" or "Koramangala". We check that at least
# one of these appears somewhere in the listing to confirm it is in Bangalore.
# ---------------------------------------------------------------------------
BANGALORE_KEYWORDS = [
    "bangalore",
    "bengaluru",
    "blr",
    "koramangala",
    "whitefield",
    "electronic city",
    "indiranagar",
    "hsr layout",
    "btm layout",
    "marathahalli",
    "sarjapur",
    "bellandur",
    "hebbal",
    "yeshwanthpur",
    "jayanagar",
    "jp nagar",
    "manyata",
    "ecospace",
    "bagmane",
    "brookefield",
]

# ---------------------------------------------------------------------------
# COMPANY TIER DETECTION
# We detect MNCs by checking if the company name contains any known MNC name.
# This list isn't exhaustive — the AI scorer also does this independently.
# Having it here gives us a quick pre-filter before we even call the AI.
# ---------------------------------------------------------------------------
KNOWN_MNCS = [
    "infosys", "wipro", "tcs", "hcl", "tech mahindra", "cognizant",
    "accenture", "ibm", "capgemini", "oracle", "microsoft", "google",
    "amazon", "aws", "deloitte", "ey", "kpmg", "pwc", "pwc",
    "cisco", "hp", "dell", "sap", "salesforce", "servicenow",
    "dxc", "ntt", "atos", "unisys", "mindtree", "mphasis",
    "hexaware", "ltimindtree", "persistent", "birlasoft",
]

# ---------------------------------------------------------------------------
# FILTERING THRESHOLDS
# Any listing that the AI scores below MIN_LEGITIMACY_SCORE is silently
# dropped — we don't store it and we don't notify. You can raise this to 7
# if you want only high-confidence legitimate listings, or lower it to 4
# if you're okay with doing more manual verification.
# ---------------------------------------------------------------------------
MIN_LEGITIMACY_SCORE = 5

# ---------------------------------------------------------------------------
# GROQ MODEL
# We use llama3-8b-8192 because it is fast (under 2 seconds per call) and
# free. The 8b model handles structured JSON extraction tasks very reliably.
# If you ever need richer reasoning, swap to "llama3-70b-8192" (same free
# tier but uses more of your daily request quota).
# ---------------------------------------------------------------------------
GROQ_MODEL = "llama3-8b-8192"

# ---------------------------------------------------------------------------
# GOOGLE SHEETS
# These are the column names used in the sheet. The order here defines the
# column order in the spreadsheet. Changing names here will NOT rename
# existing sheet columns — you would need to update the sheet manually.
# ---------------------------------------------------------------------------
SHEET_COLUMNS = [
    "scraped_at",       # ISO timestamp of when we found this listing
    "job_title",        # e.g. "SRE Engineer"
    "company",          # e.g. "Accenture"
    "company_tier",     # "MNC", "startup", or "mid-tier"
    "walk_in_date",     # e.g. "2025-04-15"
    "walk_in_time",     # e.g. "10:00-16:00"
    "location_address", # e.g. "Prestige Tech Park, Whitefield"
    "contact",          # email or phone
    "legitimacy_score", # 1-10 integer
    "red_flags",        # comma-separated warning strings
    "source",           # "naukri", "linkedin", "indeed", "rss", etc.
    "url",              # original listing URL
    "status",           # you manually update this: "pending", "attended", "fake"
]

# ---------------------------------------------------------------------------
# RSS FEED SOURCES
# These are job portal RSS feeds that include walk-in listings. Shine and
# TimesJobs both provide keyword-based RSS endpoints. The URL format is
# stable and doesn't require authentication. feedparser handles the parsing.
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    # Shine.com: walk-in jobs in Bangalore for IT
    "https://www.shine.com/rss/job-search/?q=walk-in+interview+bangalore&cat=it-software",
    # TimesJobs: walk-in listings in Bangalore
    "https://www.timesjobs.com/candidate/jobs-in-india.html?searchType=personalizedSearch&from=submit&txtKeywords=walk+in+interview&txtLocation=bangalore&rss=1",
    # Freshersworld: walk-in drives (good for 0-3 year experience)
    "https://www.freshersworld.com/jobs/rss?q=walk-in-interview&l=Bangalore",
]

# ---------------------------------------------------------------------------
# NAUKRI SEARCH URL
# Naukri's internal search API returns JSON. We discovered this URL by opening
# DevTools in Chrome (F12 → Network tab → filter "XHR" → search on Naukri →
# find the request that returns job data). The key parameters are:
#   keyword     = search terms
#   location    = city
#   jobAge      = maximum age of listing in days (1 = posted today)
#   jobTypeId   = 5 means "Walk-in" job type in Naukri's taxonomy
# ---------------------------------------------------------------------------
NAUKRI_API_URL = (
    "https://www.naukri.com/jobapi/v3/search"
    "?noOfResults=30"
    "&urlType=search_by_keyword"
    "&searchType=adv"
    "&keyword=walk+in+interview+cloud+sde+sre+security"
    "&location=bangalore"
    "&jobAge=1"
)

# ---------------------------------------------------------------------------
# REQUEST HEADERS
# Naukri and most job portals block requests that don't look like a real
# browser. We provide a realistic User-Agent and headers to avoid 403 errors.
# The appid and systemid headers are required by Naukri's API specifically —
# they are public values found by inspecting any real browser request to Naukri.
# ---------------------------------------------------------------------------
NAUKRI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "appid": "109",
    "systemid": "109",
    "referer": "https://www.naukri.com/",
}
