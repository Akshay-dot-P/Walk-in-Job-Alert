import re
import time
import logging
import requests
import feedparser
from datetime import datetime

try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
except (ImportError, Exception):
    JOBSPY_AVAILABLE = False
    logging.warning("jobspy not available — LinkedIn/Indeed/Google sources disabled")

from config import (
    TARGET_ROLES,
    WALKIN_KEYWORDS,
    BANGALORE_KEYWORDS,
    RSS_FEEDS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Updated headers that Naukri accepts — mimics a real Chrome browser exactly
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


def text_contains_any(text: str, keywords: list) -> bool:
    if not text:
        return False
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


def is_relevant_listing(title: str, description: str, location: str) -> bool:
    full_text = f"{title} {description} {location}".lower()
    return (
        text_contains_any(full_text, WALKIN_KEYWORDS)
        and text_contains_any(full_text, TARGET_ROLES)
        and text_contains_any(full_text, BANGALORE_KEYWORDS)
    )


def fetch_naukri() -> list:
    logger.info("Fetching from Naukri...")
    results = []

    # Use a session so cookies carry over — Naukri needs this
    session = requests.Session()
    session.headers.update(NAUKRI_HEADERS)

    # First visit the homepage to get cookies (Naukri checks for them)
    try:
        session.get("https://www.naukri.com/", timeout=10)
        time.sleep(1)
    except Exception:
        pass

    url = (
        "https://www.naukri.com/jobapi/v3/search"
        "?noOfResults=30"
        "&urlType=search_by_keyword"
        "&searchType=adv"
        "&keyword=walk+in+interview+cloud+sde+sre+security+analyst"
        "&location=bangalore"
        "&jobAge=1"
    )

    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobDetails", [])
        logger.info(f"Naukri returned {len(jobs)} total listings")

        for job in jobs:
            title = job.get("title", "")
            company = job.get("companyName", "Unknown")
            placeholders = job.get("placeholders", [])
            location = ""
            for ph in placeholders:
                if ph.get("type") == "location":
                    location = ph.get("label", "")
                    break
            description = job.get("jobDescription", "")
            job_path = job.get("jdURL", "")
            url_full = f"https://www.naukri.com{job_path}" if job_path else ""

            if is_relevant_listing(title, description, location):
                results.append({
                    "source": "naukri",
                    "title": title,
                    "company": company,
                    "description": description[:2000],
                    "url": url_full,
                    "location": location,
                })

    except requests.exceptions.HTTPError as e:
        logger.error(f"Naukri HTTP error: {e}")
    except Exception as e:
        logger.error(f"Naukri error: {e}")

    logger.info(f"Naukri: {len(results)} relevant listings")
    return results


def fetch_naukri_walkin_type() -> list:
    logger.info("Fetching Naukri walk-in type listings...")
    results = []

    session = requests.Session()
    session.headers.update(NAUKRI_HEADERS)

    try:
        session.get("https://www.naukri.com/", timeout=10)
        time.sleep(1)
    except Exception:
        pass

    url = (
        "https://www.naukri.com/jobapi/v3/search"
        "?noOfResults=30"
        "&urlType=search_by_keyword"
        "&searchType=adv"
        "&keyword=cloud+aws+gcp+sde+developer+engineer+sre+security"
        "&location=bangalore"
        "&jobTypeId=5"
        "&jobAge=1"
    )

    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobDetails", [])

        for job in jobs:
            title = job.get("title", "")
            company = job.get("companyName", "Unknown")
            description = job.get("jobDescription", "")
            job_path = job.get("jdURL", "")
            url_full = f"https://www.naukri.com{job_path}" if job_path else ""
            placeholders = job.get("placeholders", [])
            location = ""
            for ph in placeholders:
                if ph.get("type") == "location":
                    location = ph.get("label", "")
                    break

            full_text = f"{title} {description} {location}".lower()
            if (text_contains_any(full_text, TARGET_ROLES)
                    and text_contains_any(full_text, BANGALORE_KEYWORDS)):
                results.append({
                    "source": "naukri_walkin",
                    "title": title,
                    "company": company,
                    "description": description[:2000],
                    "url": url_full,
                    "location": location,
                })

    except Exception as e:
        logger.error(f"Naukri walk-in type error: {e}")

    logger.info(f"Naukri walk-in type: {len(results)} listings")
    return results


def fetch_rss() -> list:
    logger.info("Fetching from RSS feeds...")
    results = []

    # Updated working RSS feed URLs for Indian job portals
    working_feeds = [
        # Indeed India walk-in search
        "https://in.indeed.com/rss?q=walk+in+interview+cloud+SDE+SRE&l=Bangalore&sort=date",
        # Naukri RSS (different endpoint)
        "https://www.naukri.com/jobapi/v3/search?noOfResults=10&keyword=walk+in+interview+cloud+bangalore&location=bangalore&jobAge=1&format=rss",
        # Shine.com
        "https://www.shine.com/rss/jobs/?q=walk+in+interview&l=Bangalore",
    ]

    for feed_url in working_feeds:
        try:
            # feedparser handles malformed RSS gracefully
            feed = feedparser.parse(feed_url)
            entry_count = len(feed.entries)
            logger.info(f"  RSS {feed_url[:50]}... → {entry_count} entries")

            for entry in feed.entries:
                title = entry.get("title", "")
                description = entry.get("summary", "") or entry.get("description", "")
                url = entry.get("link", "")

                if is_relevant_listing(title, description, "bangalore"):
                    results.append({
                        "source": "rss",
                        "title": title,
                        "company": "",
                        "description": description[:2000],
                        "url": url,
                        "location": "Bangalore",
                    })

        except Exception as e:
            logger.error(f"RSS error ({feed_url[:50]}): {e}")
            continue

    logger.info(f"RSS total: {len(results)} relevant listings")
    return results


def fetch_indeed_direct() -> list:
    """Scrape Indeed India directly as a fallback source."""
    logger.info("Fetching from Indeed India directly...")
    results = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    searches = [
        "walk+in+interview+cloud+engineer+bangalore",
        "walk+in+interview+software+developer+bangalore",
        "walk+in+interview+SRE+security+analyst+bangalore",
    ]

    for query in searches:
        url = f"https://in.indeed.com/jobs?q={query}&sort=date&fromage=1"
        try:
            r = requests.get(url, headers=headers, timeout=15)
            # Parse job cards from HTML using simple string matching
            # Indeed embeds job data in the page as JSON
            content = r.text

            # Extract job titles and companies from page content
            # Look for structured data patterns
            import json as _json
            # Find all jobTitle occurrences in page
            titles = re.findall(r'"jobTitle":\s*"([^"]+)"', content)
            companies = re.findall(r'"companyName":\s*"([^"]+)"', content)
            job_keys = re.findall(r'"jobKey":\s*"([^"]+)"', content)

            for i, title in enumerate(titles):
                company = companies[i] if i < len(companies) else "Unknown"
                job_key = job_keys[i] if i < len(job_keys) else ""
                job_url = f"https://in.indeed.com/viewjob?jk={job_key}" if job_key else url

                full_text = f"{title} bangalore walk in interview".lower()
                if text_contains_any(full_text, TARGET_ROLES):
                    results.append({
                        "source": "indeed",
                        "title": title,
                        "company": company,
                        "description": f"Walk-in interview for {title} at {company} in Bangalore",
                        "url": job_url,
                        "location": "Bangalore",
                    })

            time.sleep(2)

        except Exception as e:
            logger.error(f"Indeed direct error: {e}")

    logger.info(f"Indeed direct: {len(results)} listings")
    return results


def gather_all_listings() -> list:
    all_listings = []

    all_listings.extend(fetch_naukri())
    all_listings.extend(fetch_naukri_walkin_type())
    all_listings.extend(fetch_rss())
    all_listings.extend(fetch_indeed_direct())

    if JOBSPY_AVAILABLE:
        all_listings.extend(fetch_jobspy())

    # Deduplicate by URL
    seen = {}
    for listing in all_listings:
        url = listing.get("url", "")
        key = url if url else f"{listing['title']}|{listing['company']}"
        if key not in seen:
            seen[key] = listing

    unique = list(seen.values())
    logger.info(f"Total unique relevant listings: {len(unique)}")
    return unique


def fetch_jobspy() -> list:
    results = []
    for site in ["linkedin", "indeed", "google"]:
        try:
            df = scrape_jobs(
                site_name=[site],
                search_term="walk in interview cloud SDE SRE security analyst bangalore",
                location="Bangalore, Karnataka, India",
                results_wanted=20,
                hours_old=8,
                country_indeed="india",
            )
            for _, row in df.iterrows():
                title = str(row.get("title", "") or "")
                company = str(row.get("company", "") or "")
                description = str(row.get("description", "") or "")
                location = str(row.get("location", "") or "")
                url = str(row.get("job_url", "") or "")
                if is_relevant_listing(title, description, location):
                    results.append({
                        "source": site,
                        "title": title,
                        "company": company,
                        "description": description[:2000],
                        "url": url,
                        "location": location,
                    })
            time.sleep(3)
        except Exception as e:
            logger.error(f"JobSpy {site} error: {e}")
    return results
