# =============================================================================
# sources.py
# =============================================================================
# Scrapes job listings from multiple sources and returns a unified list of
# raw listing dicts for the scorer to process.
#
# SOURCES:
#   - LinkedIn     (via JobSpy) — working, stable
#   - Indeed India (via JobSpy, country_indeed="India") — replaces Naukri
#   - Google Jobs  (via JobSpy) — aggregates Naukri/Glassdoor/TimesJobs/etc.
#
# FILTERS APPLIED BEFORE SCORING:
#   - is_tech_role()       — keeps only tech/security/GRC/risk roles
#   - is_entry_level()     — keeps only 0-1 yr experience listings
#   - is_bangalore()       — keeps only Bangalore/Bengaluru listings
# =============================================================================

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

try:
    from jobspy import scrape_jobs
except ImportError as e:
    raise ImportError(
        "python-jobspy is not installed. Run: pip install python-jobspy"
    ) from e

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

SEARCH_TERM     = "walk in interview"
LOCATION        = "Bangalore, India"
HOURS_OLD       = 24
RESULTS_WANTED  = 50
INTER_SOURCE_DELAY = 3

# ---------------------------------------------------------------------------
# FILTER KEYWORDS
# ---------------------------------------------------------------------------

# Tech + Security + GRC + Risk roles to KEEP
TECH_TITLE_KEYWORDS = [
    # Software / Engineering
    "software engineer", "software developer", "sde", "backend", "frontend",
    "full stack", "fullstack", "full-stack", "python developer", "java developer",
    "node developer", "react developer", "angular developer",

    # Cloud / Infrastructure
    "cloud engineer", "cloud developer", "cloud architect", "aws", "azure", "gcp",
    "devops", "devsecops", "platform engineer", "infrastructure engineer",
    "site reliability", "sre", "kubernetes", "docker", "terraform", "ansible",

    # Security / Cybersecurity
    "security analyst", "security engineer", "information security", "infosec",
    "cybersecurity", "cyber security", "application security", "appsec",
    "network security", "soc analyst", "soc engineer", "threat analyst",
    "penetration tester", "pen tester", "vulnerability analyst", "vapt",
    "security operations", "incident response", "malware analyst",
    "blue team", "red team", "purple team", "security consultant",

    # GRC / Risk / Compliance
    "grc", "governance", "risk and compliance", "risk analyst", "risk engineer",
    "compliance analyst", "compliance engineer", "it audit", "it risk",
    "third party risk", "vendor risk", "tprm", "iso 27001", "pci dss",
    "data privacy", "data protection", "dpo", "gdpr", "privacy analyst",
    "audit analyst", "internal audit", "it compliance",

    # Data / AI
    "data engineer", "data analyst", "data scientist", "ml engineer",
    "machine learning", "ai engineer", "etl developer", "bi developer",
    "business intelligence", "power bi developer", "tableau developer",

    # General Tech
    "it analyst", "systems analyst", "network engineer", "database administrator",
    "dba", "it support engineer", "technical analyst",
]

# Non-tech roles to EXCLUDE even if they sneak past keyword match
EXCLUDE_TITLE_KEYWORDS = [
    "sales", "store manager", "retail", "customer service", "customer care",
    "telecaller", "bpo", "voice process", "non voice", "accounts",
    "accountant", "finance executive", "hr executive", "recruiter",
    "logistics", "supply chain", "warehouse", "delivery", "driver",
    "teacher", "faculty", "trainer", "content writer", "graphic designer",
    "marketing", "seo", "social media", "field executive", "insurance",
    "banking", "loan", "mortgage", "real estate", "civil engineer",
    "mechanical", "electrical engineer", "hardware engineer",
]

# Entry level indicators — job must contain at least one of these OR
# have 0/1 year experience mentioned
ENTRY_LEVEL_TITLE_KEYWORDS = [
    "fresher", "freshers", "entry level", "entry-level", "junior", "jr.",
    "associate", "trainee", "graduate", "intern", "0-1", "0 - 1",
    "0 to 1", "1 year", "1 years", "up to 1", "less than 1",
]

# Experience patterns that indicate senior roles — used to EXCLUDE
SENIOR_EXPERIENCE_KEYWORDS = [
    "3+ years", "3 years", "4 years", "5 years", "6 years", "7 years",
    "8 years", "10 years", "senior", "lead ", "principal", "staff ",
    "manager", "director", "head of", "vp ", "vice president",
    "3-5 years", "4-6 years", "5-7 years", "5-8 years", "5+ years",
    "minimum 3", "minimum 4", "minimum 5", "at least 3", "at least 5",
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

# ---------------------------------------------------------------------------
# FILTER FUNCTIONS
# ---------------------------------------------------------------------------

def is_tech_role(listing: dict) -> bool:
    """
    Returns True if the listing is a tech/security/GRC/risk role.
    Checks title first (high signal), then first 300 chars of description.
    Also excludes clearly non-tech roles even if they pass keyword match.
    """
    title = (listing.get("title") or "").lower()
    desc  = (listing.get("description") or "").lower()[:300]
    combined = title + " " + desc

    # Hard exclude non-tech roles by title
    if any(kw in title for kw in EXCLUDE_TITLE_KEYWORDS):
        return False

    # Must match at least one tech keyword in title or description
    return any(kw in combined for kw in TECH_TITLE_KEYWORDS)


def is_entry_level(listing: dict) -> bool:
    """
    Returns True if the listing appears to be entry level (0-1 years exp).
    Strategy:
      - If title contains a senior/lead/manager keyword → exclude
      - If description mentions 3+ years experience → exclude
      - If title or description contains fresher/junior/associate → include
      - Otherwise → include by default (benefit of the doubt for walk-ins,
        which are often open to freshers even when not stated)
    """
    title = (listing.get("title") or "").lower()
    desc  = (listing.get("description") or "").lower()[:500]

    # Hard exclude senior roles by title
    for kw in ["senior", "lead ", "principal", "staff ", "manager",
               "director", "head of", "vp ", "vice president"]:
        if kw in title:
            return False

    # Exclude if description mentions clearly senior experience
    for kw in SENIOR_EXPERIENCE_KEYWORDS:
        if kw in desc:
            return False

    return True


def is_bangalore(listing: dict) -> bool:
    """
    Returns True if the listing is in Bangalore/Bengaluru.
    Checks location field first, then title and description.
    """
    location = (listing.get("location") or "").lower()
    title    = (listing.get("title") or "").lower()
    desc     = (listing.get("description") or "").lower()[:300]
    combined = location + " " + title + " " + desc

    return any(kw in combined for kw in BANGALORE_KEYWORDS)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _jobspy_to_listing(row: Any, source_label: str) -> dict:
    """
    Convert a single pandas Series row from JobSpy into the flat dict format
    expected by scorer.py.
    """
    def _safe(val):
        import pandas as pd
        if val is None:
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        return val

    return {
        "source":      source_label,
        "title":       _safe(row.get("title")),
        "company":     _safe(row.get("company")),
        "location":    _safe(row.get("location")),
        "job_type":    _safe(row.get("job_type")),
        "date_posted": str(_safe(row.get("date_posted")) or ""),
        "description": _safe(row.get("description")) or "",
        "job_url":     _safe(row.get("job_url")),
        "is_remote":   _safe(row.get("is_remote")),
    }


def _apply_filters(listings: list[dict], source_label: str) -> list[dict]:
    """
    Apply all three filters and log a breakdown of what was dropped and why.
    """
    total = len(listings)
    after_tech     = [l for l in listings if is_tech_role(l)]
    after_entry    = [l for l in after_tech if is_entry_level(l)]
    after_location = [l for l in after_entry if is_bangalore(l)]

    logger.info(
        f"{source_label} filter: {total} raw "
        f"→ {len(after_tech)} tech "
        f"→ {len(after_entry)} entry-level "
        f"→ {len(after_location)} bangalore"
    )
    return after_location


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def _scrape_linkedin() -> list[dict]:
    logger.info("JobSpy:LinkedIn — starting scrape")
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=SEARCH_TERM,
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            linkedin_fetch_description=True,
        )
        if df is None or df.empty:
            logger.warning("JobSpy:LinkedIn — returned 0 results")
            return []
        raw = [_jobspy_to_listing(row, "LinkedIn") for _, row in df.iterrows()]
        logger.info(f"JobSpy:LinkedIn — {len(raw)} raw listings")
        return _apply_filters(raw, "LinkedIn")
    except Exception as exc:
        logger.error(f"JobSpy:LinkedIn — failed: {exc}")
        return []


def _scrape_indeed_india() -> list[dict]:
    logger.info("JobSpy:Indeed India — starting scrape")
    try:
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=SEARCH_TERM,
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            country_indeed="India",
        )
        if df is None or df.empty:
            logger.warning("JobSpy:Indeed India — returned 0 results")
            return []
        raw = [_jobspy_to_listing(row, "Indeed India") for _, row in df.iterrows()]
        logger.info(f"JobSpy:Indeed India — {len(raw)} raw listings")
        return _apply_filters(raw, "Indeed India")
    except Exception as exc:
        logger.error(f"JobSpy:Indeed India — failed: {exc}")
        return []


def _scrape_google_jobs() -> list[dict]:
    logger.info("JobSpy:Google Jobs — starting scrape")

    # Multiple targeted queries — Google Jobs needs specific phrasing
    google_queries = [
        "walk in interview software engineer cloud devops Bangalore today 2026",
        "walkin drive fresher junior software engineer Bengaluru 2026",
        "walk in interview security analyst GRC risk compliance Bangalore 2026",
        "walk in interview SRE devops cloud engineer entry level Bangalore 2026",
    ]

    all_raw = []
    for query in google_queries:
        try:
            df = scrape_jobs(
                site_name=["google"],
                search_term=SEARCH_TERM,
                google_search_term=query,
                location=LOCATION,
                results_wanted=RESULTS_WANTED,
            )
            if df is None or df.empty:
                logger.warning(f"JobSpy:Google Jobs — 0 results for: {query[:60]}")
                continue
            raw = [_jobspy_to_listing(row, "Google Jobs") for _, row in df.iterrows()]
            logger.info(f"JobSpy:Google Jobs — {len(raw)} raw for: {query[:60]}")
            all_raw.extend(raw)
            time.sleep(2)
        except Exception as exc:
            logger.error(f"JobSpy:Google Jobs — failed for query '{query[:60]}': {exc}")

    logger.info(f"JobSpy:Google Jobs — {len(all_raw)} total raw listings")
    return _apply_filters(all_raw, "Google Jobs")


# ---------------------------------------------------------------------------
# Public API — called by scanner.py
# ---------------------------------------------------------------------------

def gather_all_listings() -> list[dict]:
    """
    Run all scrapers, apply filters, deduplicate by URL, and return.
    """
    all_listings: list[dict] = []

    linkedin_listings = _scrape_linkedin()
    all_listings.extend(linkedin_listings)
    time.sleep(INTER_SOURCE_DELAY)

    indeed_listings = _scrape_indeed_india()
    all_listings.extend(indeed_listings)
    time.sleep(INTER_SOURCE_DELAY)

    google_listings = _scrape_google_jobs()
    all_listings.extend(google_listings)

    # URL-based dedup
    seen_urls: set[str] = set()
    unique_listings: list[dict] = []
    for listing in all_listings:
        url = listing.get("job_url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique_listings.append(listing)

    logger.info(
        f"gather_all_listings complete: "
        f"{len(all_listings)} filtered → {len(unique_listings)} after URL dedup "
        f"(LinkedIn={len(linkedin_listings)}, "
        f"IndeedIndia={len(indeed_listings)}, "
        f"GoogleJobs={len(google_listings)})"
    )
    return unique_listings
