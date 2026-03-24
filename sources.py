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
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def text_contains_any(text, keywords):
    if not text:
        return False
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


# ---------------------------------------------------------------------------
# RELAXED FILTER
# The strict filter (walkin AND tech AND bangalore) dropped everything because
# LinkedIn descriptions are often empty. Now we use a tiered approach:
#   - STRONG match: all three conditions → include
#   - MEDIUM match: tech + bangalore in title/location (no description needed)
#     AND the SEARCH TERM already contained walk-in → include
# ---------------------------------------------------------------------------
def is_relevant(title, description, location="", require_walkin_keyword=True):
    full = f"{title} {description} {location}"
    title_loc = f"{title} {location}"

    has_tech    = text_contains_any(full, TARGET_ROLES)
    has_blr     = text_contains_any(full, BANGALORE_KEYWORDS)
    has_walkin  = text_contains_any(full, WALKIN_KEYWORDS)

    if not has_tech or not has_blr:
        return False

    if require_walkin_keyword:
        return has_walkin
    else:
        # For sources where we searched "walk in interview" already,
        # just needing tech + bangalore is enough
        return True


# ---------------------------------------------------------------------------
# SOURCE 1: Naukri HTML scrape (avoids the blocked JSON API)
# ---------------------------------------------------------------------------
def fetch_naukri():
    logger.info("Fetching Naukri (HTML)...")
    results = []

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    searches = [
        "walk-in-interview-jobs-in-bangalore?k=walk+in+interview+cloud+sde+sre+security&l=bangalore",
        "walk-in-interview-jobs-in-bangalore?k=walk+in+interview+software+engineer&l=bangalore",
    ]

    for path in searches:
        url = f"https://www.naukri.com/{path}"
        try:
            r = session.get(url, timeout=15)
            logger.info(f"Naukri HTML status: {r.status_code} for {path[:50]}")

            if r.status_code != 200:
                continue

            content = r.text

            # Naukri embeds job data as JSON in the page source
            # Look for the jobDetails array in the embedded script
            matches = re.findall(
                r'"title"\s*:\s*"([^"]+)"[^}]*"companyName"\s*:\s*"([^"]+)"',
                content
            )
            logger.info(f"Naukri HTML: found {len(matches)} job title/company pairs")

            # Also try to find jdURL for each job
            job_blocks = re.findall(
                r'\{[^{}]*"title"\s*:\s*"[^"]*walk[^"]*"[^{}]*\}',
                content, re.IGNORECASE
            )

            for title, company in matches:
                if is_relevant(title, "", "bangalore", require_walkin_keyword=True):
                    results.append({
                        "source": "naukri",
                        "title": title,
                        "company": company,
                        "description": f"Walk-in interview for {title} at {company} in Bangalore",
                        "url": f"https://www.naukri.com/job-listings-{title.lower().replace(' ','-')}-{company.lower().replace(' ','-')}-bangalore",
                        "location": "Bangalore",
                    })

        except Exception as e:
            logger.error(f"Naukri HTML error: {e}")

        time.sleep(2)

    logger.info(f"Naukri HTML: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# SOURCE 2: JobSpy — with debug logging to see raw results
# ---------------------------------------------------------------------------
def fetch_jobspy():
    if not JOBSPY_AVAILABLE:
        return []

    results = []

    # Search terms specifically crafted to surface walk-in listings
    searches = [
        {
            "term": "walk in interview cloud engineer bangalore",
            "sites": ["linkedin", "google"],
        },
        {
            "term": "walkin drive software developer SDE bangalore",
            "sites": ["linkedin", "google"],
        },
        {
            "term": "walk in interview SRE devops security analyst bangalore",
            "sites": ["linkedin", "google"],
        },
        {
            "term": "walk in interview bangalore cloud AWS GCP",
            "sites": ["indeed"],
            "country": "india",
        },
    ]

    for search in searches:
        sites = search["sites"]
        term  = search["term"]

        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=term,
                location="Bangalore, Karnataka, India",
                results_wanted=25,
                hours_old=24,          # look back 24 hours
                country_indeed=search.get("country", "india"),
            )

            logger.info(f"JobSpy '{term[:40]}': raw {len(df)} results from {sites}")

            # Log first few titles so we can see what's coming back
            if len(df) > 0:
                for _, row in df.head(3).iterrows():
                    logger.info(f"  Sample: {row.get('title','?')} @ {row.get('company','?')} | {row.get('location','?')}")

            for _, row in df.iterrows():
                title       = str(row.get("title", "") or "")
                company     = str(row.get("company", "") or "")
                description = str(row.get("description", "") or "")
                location    = str(row.get("location", "") or "")
                url         = str(row.get("job_url", "") or "")

                # For JobSpy results: we already searched for "walk in interview"
                # so just require tech role + bangalore location
                if is_relevant(title, description, location, require_walkin_keyword=False):
                    results.append({
                        "source": sites[0],
                        "title": title,
                        "company": company,
                        "description": description[:2000] if description else f"Walk-in for {title} at {company} in Bangalore",
                        "url": url,
                        "location": location,
                    })

            time.sleep(3)

        except Exception as e:
            logger.error(f"JobSpy error ({term[:30]}): {e}")

    logger.info(f"JobSpy total: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3: Google Jobs via SerpAPI-style URL (free, no auth needed)
# ---------------------------------------------------------------------------
def fetch_google_jobs_rss():
    logger.info("Fetching via Google News RSS (job announcements)...")
    results = []

    # Google News RSS for job-related news — catches company hiring announcements
    google_rss_urls = [
        "https://news.google.com/rss/search?q=walk+in+interview+bangalore+cloud+engineer&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=walkin+drive+bangalore+software+engineer+2025&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=walk+in+interview+bangalore+SRE+devops+security+2025&hl=en-IN&gl=IN&ceid=IN:en",
    ]

    for feed_url in google_rss_urls:
        try:
            feed = feedparser.parse(feed_url)
            logger.info(f"Google News RSS: {len(feed.entries)} entries from {feed_url[50:80]}...")

            for entry in feed.entries:
                title = entry.get("title", "")
                desc  = entry.get("summary", "") or entry.get("description", "")
                url   = entry.get("link", "")

                if is_relevant(title, desc, "bangalore", require_walkin_keyword=True):
                    results.append({
                        "source": "google_news",
                        "title": title,
                        "company": "",
                        "description": desc[:2000],
                        "url": url,
                        "location": "Bangalore",
                    })

        except Exception as e:
            logger.error(f"Google News RSS error: {e}")

    logger.info(f"Google News RSS: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# SOURCE 4: Instahyre (startup-focused, good for Bangalore tech)
# ---------------------------------------------------------------------------
def fetch_instahyre():
    logger.info("Fetching Instahyre...")
    results = []

    url = "https://www.instahyre.com/api/v1/opportunity/?format=json&location=Bangalore&role_type=Technology"
    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        logger.info(f"Instahyre status: {r.status_code}")

        if r.status_code == 200:
            data = r.json()
            jobs = data.get("results", data if isinstance(data, list) else [])
            logger.info(f"Instahyre: {len(jobs)} total jobs")

            for job in jobs:
                title   = job.get("designation", "") or job.get("title", "")
                company = job.get("company", {}).get("name", "") if isinstance(job.get("company"), dict) else job.get("company", "")
                desc    = job.get("description", "")
                jurl    = f"https://www.instahyre.com/job/{job.get('id', '')}"

                if is_relevant(title, desc, "Bangalore", require_walkin_keyword=False):
                    results.append({
                        "source": "instahyre",
                        "title": title,
                        "company": company,
                        "description": desc[:2000],
                        "url": jurl,
                        "location": "Bangalore",
                    })

    except Exception as e:
        logger.error(f"Instahyre error: {e}")

    logger.info(f"Instahyre: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# MASTER FUNCTION
# ---------------------------------------------------------------------------
def gather_all_listings():
    all_listings = (
        fetch_naukri()
        + fetch_jobspy()
        + fetch_google_news_rss()
        + fetch_instahyre()
    )

    # Deduplicate
    seen = {}
    for l in all_listings:
        key = l.get("url") or f"{l['title']}|{l['company']}"
        if key not in seen:
            seen[key] = l

    unique = list(seen.values())
    logger.info(f"Total unique relevant listings across all sources: {len(unique)}")
    return unique


# Fix function name typo
def fetch_google_news_rss():
    return fetch_google_jobs_rss()
