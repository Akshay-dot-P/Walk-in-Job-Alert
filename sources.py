# =============================================================================
# Walk-In Job Scanner - FIXED & OPTIMIZED (March 2026)
# =============================================================================
import re
import time
import logging
import requests
import feedparser
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# JobSpy import with graceful fallback
try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
    logger.info("jobspy loaded OK")
except Exception as e:
    JOBSPY_AVAILABLE = False
    logger.warning(f"jobspy not available: {e}")

from config import TARGET_ROLES, WALKIN_KEYWORDS, BANGALORE_KEYWORDS

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# FILTER (unchanged but made slightly more readable)
# ---------------------------------------------------------------------------
def text_contains_any(text: str, keywords: list) -> bool:
    if not text:
        return False
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


def is_relevant(title: str, description: str = "", location: str = "", require_walkin_keyword: bool = True) -> bool:
    full_text = f"{title} {description} {location}".lower()
    title_loc = f"{title} {location}".lower()

    has_tech = text_contains_any(full_text, TARGET_ROLES)
    has_blr = text_contains_any(title_loc, BANGALORE_KEYWORDS)
    has_walkin = text_contains_any(full_text, WALKIN_KEYWORDS)

    if not (has_tech and has_blr):
        return False

    return has_walkin if require_walkin_keyword else True


# ---------------------------------------------------------------------------
# MAIN JOB FETCHER — now uses JobSpy for almost everything
# ---------------------------------------------------------------------------
def fetch_all_jobs() -> list:
    if not JOBSPY_AVAILABLE:
        logger.error("JobSpy is not installed. Cannot fetch jobs.")
        return []

    logger.info("=== Starting JobSpy multi-source scrape ===")
    results = []

    # One powerful search that covers walk-ins across all major sites
    search_configs = [
        {
            "term": "walk in OR walk-in OR walkin (cloud OR devops OR sre OR security OR SDE OR software engineer OR developer)",
            "sites": ["naukri", "linkedin", "google", "indeed"],
            "hours_old": 48,
        },
        {
            "term": "walk in interview bangalore cloud OR aws OR azure OR devops OR sre",
            "sites": ["linkedin", "google"],
            "hours_old": 24,
        },
    ]

    for cfg in search_configs:
        try:
            df = scrape_jobs(
                site_name=cfg["sites"],
                search_term=cfg["term"],
                location="Bangalore, Karnataka, India",
                results_wanted=50,           # you can increase up to 1000
                hours_old=cfg["hours_old"],
                country_indeed="india",
            )

            logger.info(f"JobSpy → {cfg['term'][:50]}... | Raw results: {len(df)}")

            if df.empty:
                continue

            for _, row in df.iterrows():
                title = str(row.get("title", "") or "").strip()
                company = str(row.get("company", "") or "").strip()
                description = str(row.get("description", "") or "")
                location = str(row.get("location", "") or "Bangalore")
                url = str(row.get("job_url", "") or "")

                # For JobSpy we already searched for "walk in", so relax the filter
                if is_relevant(title, description, location, require_walkin_keyword=False):
                    results.append({
                        "source": row.get("site", "unknown"),
                        "title": title,
                        "company": company,
                        "description": description[:2000] if description else f"Walk-in for {title} at {company}",
                        "url": url,
                        "location": location,
                        "date_posted": row.get("date_posted"),
                    })

            time.sleep(3)  # be nice to the servers

        except Exception as e:
            logger.error(f"JobSpy error on '{cfg['term'][:40]}': {e}")

    logger.info(f"JobSpy finished — {len(results)} relevant listings collected")
    return results


# ---------------------------------------------------------------------------
# Google News RSS (still works well for announcements)
# ---------------------------------------------------------------------------
def fetch_google_news():
    logger.info("Fetching Google News RSS for walk-in announcements...")
    results = []
    rss_urls = [
        "https://news.google.com/rss/search?q=walk+in+interview+bangalore+cloud+OR+devops+OR+sre+OR+security&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=walkin+drive+bangalore+software+engineer+2026&hl=en-IN&gl=IN&ceid=IN:en",
    ]

    for url in rss_urls:
        try:
            feed = feedparser.parse(url)
            logger.info(f"Google News: {len(feed.entries)} entries")
            for entry in feed.entries:
                title = entry.get("title", "")
                desc = entry.get("summary") or entry.get("description", "")
                link = entry.get("link", "")

                if is_relevant(title, desc, "bangalore", require_walkin_keyword=True):
                    results.append({
                        "source": "google_news",
                        "title": title,
                        "company": "",
                        "description": desc[:2000],
                        "url": link,
                        "location": "Bangalore",
                    })
        except Exception as e:
            logger.error(f"Google News RSS error: {e}")

    logger.info(f"Google News: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# MASTER GATHER FUNCTION
# ---------------------------------------------------------------------------
def gather_all_listings() -> list:
    start_time = datetime.now()

    all_listings = (
        fetch_all_jobs() +          # ← now the main powerhouse
        fetch_google_news()         # ← keeps the news announcements
    )

    # Deduplication (improved)
    seen = {}
    for job in all_listings:
        key = job.get("url") or f"{job['title']}|{job['company']}"
        if key not in seen:
            seen[key] = job

    unique_listings = list(seen.values())

    logger.info(f"Total unique relevant listings after deduplication: {len(unique_listings)}")
    logger.info(f"Full Phase 1 completed in {datetime.now() - start_time}")

    return unique_listings


# ---------------------------------------------------------------------------
# For easy testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("=== Manual test run of scanner ===")
    jobs = gather_all_listings()
    for i, j in enumerate(jobs[:10], 1):   # show first 10
        logger.info(f"{i:2d}. {j['title']} @ {j['company']} → {j['url']}")
    logger.info(f"Total jobs found: {len(jobs)}")
