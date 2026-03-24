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

# Try importing jobspy — log the actual error so we can debug it
try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
    logger.info("jobspy loaded OK")
except Exception as e:
    JOBSPY_AVAILABLE = False
    logger.warning(f"jobspy not available: {e}")

from config import (
    TARGET_ROLES, WALKIN_KEYWORDS, BANGALORE_KEYWORDS,
    RSS_FEEDS, NAUKRI_HEADERS,
)


def text_contains_any(text, keywords):
    if not text:
        return False
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


def is_relevant(title, description, location=""):
    full = f"{title} {description} {location}".lower()
    return (
        text_contains_any(full, WALKIN_KEYWORDS)
        and text_contains_any(full, TARGET_ROLES)
        and text_contains_any(full, BANGALORE_KEYWORDS)
    )


def fetch_naukri():
    logger.info("Fetching Naukri...")
    results = []
    session = requests.Session()
    session.headers.update(NAUKRI_HEADERS)

    # Visit homepage first to get session cookies
    try:
        session.get("https://www.naukri.com/", timeout=10)
        time.sleep(2)
    except Exception as e:
        logger.warning(f"Naukri homepage warmup failed: {e}")

    for url in [
        (
            "https://www.naukri.com/jobapi/v3/search"
            "?noOfResults=30&urlType=search_by_keyword&searchType=adv"
            "&keyword=walk+in+interview+cloud+sde+sre+security+analyst"
            "&location=bangalore&jobAge=1"
        ),
        (
            "https://www.naukri.com/jobapi/v3/search"
            "?noOfResults=30&urlType=search_by_keyword&searchType=adv"
            "&keyword=cloud+aws+gcp+sde+developer+engineer+sre+security"
            "&location=bangalore&jobTypeId=5&jobAge=1"
        ),
    ]:
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            jobs = r.json().get("jobDetails", [])
            logger.info(f"Naukri returned {len(jobs)} jobs from {url[50:90]}...")

            for job in jobs:
                title = job.get("title", "")
                company = job.get("companyName", "Unknown")
                desc = job.get("jobDescription", "")
                loc = next(
                    (p.get("label", "") for p in job.get("placeholders", []) if p.get("type") == "location"),
                    ""
                )
                jurl = "https://www.naukri.com" + job.get("jdURL", "")
                if is_relevant(title, desc, loc):
                    results.append({
                        "source": "naukri", "title": title, "company": company,
                        "description": desc[:2000], "url": jurl, "location": loc,
                    })
        except Exception as e:
            logger.error(f"Naukri error: {e}")

    logger.info(f"Naukri: {len(results)} relevant listings")
    return results


def fetch_rss():
    logger.info("Fetching RSS feeds...")
    results = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            logger.info(f"RSS {feed_url[:60]}... → {len(feed.entries)} entries")

            for entry in feed.entries:
                title = entry.get("title", "")
                desc = entry.get("summary", "") or entry.get("description", "")
                url = entry.get("link", "")
                if is_relevant(title, desc, "bangalore"):
                    results.append({
                        "source": "rss", "title": title, "company": "",
                        "description": desc[:2000], "url": url, "location": "Bangalore",
                    })
        except Exception as e:
            logger.error(f"RSS error {feed_url[:50]}: {e}")

    logger.info(f"RSS: {len(results)} relevant listings")
    return results


def fetch_jobspy():
    if not JOBSPY_AVAILABLE:
        return []
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
                if is_relevant(title, description, location):
                    results.append({
                        "source": site, "title": title, "company": company,
                        "description": description[:2000], "url": url, "location": location,
                    })
            time.sleep(3)
        except Exception as e:
            logger.error(f"JobSpy {site}: {e}")
    return results


def gather_all_listings():
    all_listings = (
        fetch_naukri()
        + fetch_rss()
        + fetch_jobspy()
    )

    # Deduplicate by URL or title+company
    seen = {}
    for l in all_listings:
        key = l.get("url") or f"{l['title']}|{l['company']}"
        if key not in seen:
            seen[key] = l

    unique = list(seen.values())
    logger.info(f"Total unique relevant listings: {len(unique)}")
    return unique
