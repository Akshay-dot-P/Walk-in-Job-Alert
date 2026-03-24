# =============================================================================
# sources.py
# =============================================================================
# This module handles all data collection — pulling raw job listings from
# every source and returning them in a unified dictionary format.
#
# The golden rule here is: every function returns a LIST OF DICTS, where
# each dict has the same keys. This means the main scanner doesn't care
# WHERE a listing came from — it processes them all identically. This is
# called a "normalized" data format and is one of the most useful patterns
# in pipeline programming.
#
# The unified format for every listing dict:
# {
#   "source":      str   — where we found it ("naukri", "linkedin", etc.)
#   "title":       str   — raw job title text
#   "company":     str   — company name
#   "description": str   — full listing text (or as much as we can get)
#   "url":         str   — link to the original listing
#   "location":    str   — location text as written in the listing
# }
# =============================================================================

import re
import time
import logging
import requests
import feedparser
from datetime import datetime

# JobSpy is the third-party library that handles LinkedIn, Indeed, and
# Google Jobs scraping. We imported it here — not in every function — so
# Python only loads it once when this module is first imported.
try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
except ImportError:
    # If jobspy fails to install (can happen on some systems), we log a
    # warning and continue — the other sources still work.
    JOBSPY_AVAILABLE = False
    logging.warning("jobspy not available — LinkedIn/Indeed/Google sources disabled")

from config import (
    TARGET_ROLES,
    WALKIN_KEYWORDS,
    BANGALORE_KEYWORDS,
    NAUKRI_API_URL,
    NAUKRI_HEADERS,
    RSS_FEEDS,
)

# Set up logging. The format string means: show the time, the severity level
# (INFO / WARNING / ERROR), and the message. This is far more useful than
# plain print() statements because you can filter by severity.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# HELPER: Text contains any keyword
# =============================================================================
# This function checks whether any keyword from a list appears in a piece
# of text. We use it in two ways:
#   1. To check if a listing is a walk-in (check for WALKIN_KEYWORDS)
#   2. To check if a listing is for a target role (check for TARGET_ROLES)
#
# re.escape() is important — it escapes special regex characters. For example,
# "c++" would break a regex without escaping, because + is a quantifier.
# re.IGNORECASE makes the match case-insensitive ("Walk-In" == "walk-in").
# =============================================================================
def text_contains_any(text: str, keywords: list) -> bool:
    if not text:
        return False
    # Build one big pattern like "(keyword1|keyword2|keyword3)"
    # This is faster than calling re.search() in a loop because the regex
    # engine only scans the text once.
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


# =============================================================================
# HELPER: Check if a listing is relevant to our criteria
# =============================================================================
# A listing passes our filter if ALL THREE conditions are true:
#   1. It contains at least one walk-in keyword
#   2. It mentions at least one of our target roles
#   3. It mentions Bangalore / Bengaluru somewhere
#
# We combine the title and description into one string before checking.
# This way "Cloud SRE - Walk-in Drive" in the title and "Bangalore" in the
# description both count, even though they're in different fields.
# =============================================================================
def is_relevant_listing(title: str, description: str, location: str) -> bool:
    # Merge all text fields into one searchable blob
    full_text = f"{title} {description} {location}".lower()

    is_walkin   = text_contains_any(full_text, WALKIN_KEYWORDS)
    is_tech     = text_contains_any(full_text, TARGET_ROLES)
    is_blr      = text_contains_any(full_text, BANGALORE_KEYWORDS)

    return is_walkin and is_tech and is_blr


# =============================================================================
# SOURCE 1: Naukri.com (custom HTTP request)
# =============================================================================
# Naukri is the richest source for Indian job listings. Their site uses
# an internal JSON API that we can call directly.
#
# How we discovered this URL: Open Chrome, go to naukri.com, search for
# "walk in interview cloud bangalore". Then open DevTools → Network tab →
# filter to "Fetch/XHR" requests → find the request named something like
# "search?noOfResults=..." → right-click → Copy as cURL. That gives you
# the exact URL and headers the browser is using.
#
# The API returns a JSON object with a "jobDetails" array. Each item in
# that array is one job listing with fields like title, companyName, etc.
# =============================================================================
def fetch_naukri() -> list:
    logger.info("Fetching from Naukri...")
    results = []

    try:
        response = requests.get(
            NAUKRI_API_URL,
            headers=NAUKRI_HEADERS,
            timeout=15,  # don't wait more than 15 seconds for a response
        )

        # raise_for_status() raises an exception if the HTTP status code
        # indicates an error (4xx client error or 5xx server error).
        # Without this, a 403 or 404 response would silently return empty data.
        response.raise_for_status()

        data = response.json()

        # The API returns a nested dict. We use .get() with defaults throughout
        # to avoid KeyError crashes if Naukri changes their response format.
        jobs = data.get("jobDetails", [])
        logger.info(f"Naukri returned {len(jobs)} total listings")

        for job in jobs:
            # Extract the job title from nested structure.
            # Naukri's API stores some fields in a "placeholders" array,
            # each with a "type" and "label". We find the one where type=="title".
            title = job.get("title", "")

            # Company name
            company = job.get("companyName", "Unknown")

            # Location is also in placeholders under type=="location"
            placeholders = job.get("placeholders", [])
            location = ""
            for ph in placeholders:
                if ph.get("type") == "location":
                    location = ph.get("label", "")
                    break  # found what we needed, stop searching

            # Description is in a separate "jobDescription" field
            description = job.get("jobDescription", "")

            # The full URL is constructed from their base URL + the job's path
            job_path = job.get("jdURL", "")
            url = f"https://www.naukri.com{job_path}" if job_path else ""

            # Only add listings that pass our relevance filter
            if is_relevant_listing(title, description, location):
                results.append({
                    "source":      "naukri",
                    "title":       title,
                    "company":     company,
                    "description": description[:2000],  # cap at 2000 chars to save on AI tokens
                    "url":         url,
                    "location":    location,
                })

    except requests.exceptions.Timeout:
        logger.error("Naukri request timed out after 15 seconds")
    except requests.exceptions.HTTPError as e:
        # HTTPError contains the status code. 403 = blocked, 429 = rate limited
        logger.error(f"Naukri HTTP error: {e}")
    except Exception as e:
        # Catch-all for JSON decode errors, unexpected structure changes, etc.
        logger.error(f"Naukri unexpected error: {e}")

    logger.info(f"Naukri yielded {len(results)} relevant listings")
    return results


# =============================================================================
# SOURCE 2: JobSpy (LinkedIn + Indeed + Google Jobs)
# =============================================================================
# JobSpy wraps multiple job sites into one function call. It uses Playwright
# (a headless browser) internally to get past JavaScript-rendered content.
#
# Important: JobSpy is not 100% reliable — LinkedIn in particular actively
# fights scrapers. If it fails, we log the error and return an empty list
# so the rest of the pipeline continues unaffected.
# =============================================================================
def fetch_jobspy() -> list:
    if not JOBSPY_AVAILABLE:
        return []

    logger.info("Fetching from LinkedIn/Indeed/Google via JobSpy...")
    results = []

    # We try each site separately so that one failing doesn't kill the others.
    # JobSpy accepts a list in site_name but then one error fails all.
    for site in ["linkedin", "indeed", "google"]:
        try:
            # scrape_jobs returns a pandas DataFrame
            df = scrape_jobs(
                site_name=[site],
                # We search specifically for walk-in terms combined with
                # our target roles. The OR logic here means ANY of these
                # combinations will show up in results.
                search_term="walk in interview cloud SDE SRE security analyst bangalore",
                location="Bangalore, Karnataka, India",
                results_wanted=30,      # fetch up to 30 listings per site
                hours_old=8,            # only listings posted in last 8 hours
                country_indeed="india", # only applies when site="indeed"
            )

            # df.iterrows() yields (index, row) pairs. We ignore the index
            # with the underscore convention. row is a pandas Series.
            for _, row in df.iterrows():
                title       = str(row.get("title", "") or "")
                company     = str(row.get("company", "") or "")
                description = str(row.get("description", "") or "")
                location    = str(row.get("location", "") or "")
                url         = str(row.get("job_url", "") or "")

                if is_relevant_listing(title, description, location):
                    results.append({
                        "source":      site,
                        "title":       title,
                        "company":     company,
                        "description": description[:2000],
                        "url":         url,
                        "location":    location,
                    })

            logger.info(f"  {site}: found relevant listings so far")

            # Be a good citizen — don't hammer sites with rapid-fire requests
            time.sleep(3)

        except Exception as e:
            logger.error(f"JobSpy error on {site}: {e}")
            continue  # move to the next site even if this one failed

    logger.info(f"JobSpy total: {len(results)} relevant listings")
    return results


# =============================================================================
# SOURCE 3: RSS Feeds (Shine, TimesJobs, Freshersworld)
# =============================================================================
# RSS is an old, simple XML standard that many job portals still support.
# The feedparser library converts the XML into a Python dict for us.
#
# Each RSS item (called an "entry") has title, summary/description, link,
# and published fields. The structure is more consistent than scraped HTML.
#
# Why RSS is valuable: it's extremely lightweight (no JavaScript rendering),
# very rarely blocked, and updates in near real-time as new listings are added.
# =============================================================================
def fetch_rss() -> list:
    logger.info("Fetching from RSS feeds...")
    results = []

    for feed_url in RSS_FEEDS:
        try:
            # feedparser.parse() downloads the RSS URL and parses the XML.
            # It returns a FeedParserDict with a .entries list.
            feed = feedparser.parse(feed_url)

            # feed.bozo is True if the RSS XML was malformed.
            # We log it but still try to process entries — most parsers
            # recover gracefully from minor XML errors.
            if feed.bozo:
                logger.warning(f"RSS feed may be malformed: {feed_url}")

            for entry in feed.entries:
                title       = entry.get("title", "")
                # RSS uses "summary" OR "description" depending on the feed
                description = entry.get("summary", "") or entry.get("description", "")
                url         = entry.get("link", "")

                # RSS feeds often don't have a separate location field —
                # the location is usually mentioned inside the description
                location    = ""  # let is_relevant_listing scan description instead

                if is_relevant_listing(title, description, location):
                    results.append({
                        "source":      "rss",
                        "title":       title,
                        "company":     "",  # RSS feeds often don't include company separately
                        "description": description[:2000],
                        "url":         url,
                        "location":    "",
                    })

        except Exception as e:
            logger.error(f"RSS feed error ({feed_url}): {e}")
            continue

    logger.info(f"RSS total: {len(results)} relevant listings")
    return results


# =============================================================================
# SOURCE 4: Naukri Additional Walk-In Search
# =============================================================================
# Naukri has a dedicated "Walk-In" job type filter (jobTypeId=5 in their API).
# This is a second Naukri call specifically targeting that filter, which catches
# listings that didn't use walk-in keywords but were tagged as walk-in by the poster.
# =============================================================================
def fetch_naukri_walkin_type() -> list:
    logger.info("Fetching Naukri walk-in type listings...")
    results = []

    # This URL targets the walk-in job type specifically (jobTypeId=5)
    # without keyword restriction — so we'll see ALL walk-ins in Bangalore
    # and filter down to tech roles ourselves.
    url = (
        "https://www.naukri.com/jobapi/v3/search"
        "?noOfResults=30"
        "&urlType=search_by_keyword"
        "&searchType=adv"
        "&keyword=cloud+aws+gcp+sde+developer+engineer+sre+security"
        "&location=bangalore"
        "&jobTypeId=5"   # 5 = Walk-In in Naukri's system
        "&jobAge=1"
    )

    try:
        response = requests.get(url, headers=NAUKRI_HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
        jobs = data.get("jobDetails", [])

        for job in jobs:
            title       = job.get("title", "")
            company     = job.get("companyName", "Unknown")
            description = job.get("jobDescription", "")
            job_path    = job.get("jdURL", "")
            url_full    = f"https://www.naukri.com{job_path}" if job_path else ""

            placeholders = job.get("placeholders", [])
            location = ""
            for ph in placeholders:
                if ph.get("type") == "location":
                    location = ph.get("label", "")
                    break

            # For this function we only check tech role relevance and Bangalore,
            # NOT the walk-in keyword check, because jobTypeId=5 already guarantees
            # these are walk-in listings.
            full_text = f"{title} {description} {location}".lower()
            is_tech = text_contains_any(full_text, TARGET_ROLES)
            is_blr  = text_contains_any(full_text, BANGALORE_KEYWORDS)

            if is_tech and is_blr:
                results.append({
                    "source":      "naukri_walkin_type",
                    "title":       title,
                    "company":     company,
                    "description": description[:2000],
                    "url":         url_full,
                    "location":    location,
                })

    except Exception as e:
        logger.error(f"Naukri walk-in type fetch error: {e}")

    logger.info(f"Naukri walk-in type: {len(results)} relevant listings")
    return results


# =============================================================================
# MASTER FUNCTION: Gather from all sources
# =============================================================================
# This is the only function that scanner.py calls. It runs all four sources,
# merges their results into one list, and deduplicates based on URL.
#
# Why deduplicate here? The same listing sometimes appears on multiple
# sources — a company posts on Naukri AND LinkedIn. Without dedup, we'd
# send the same Telegram alert twice and store it twice in Sheets.
#
# The dedup strategy: use the URL as a unique key. If two listings have
# the same URL, keep only the first one encountered.
# =============================================================================
def gather_all_listings() -> list:
    all_listings = []

    # Collect from all sources, extending the master list each time
    all_listings.extend(fetch_naukri())
    all_listings.extend(fetch_naukri_walkin_type())
    all_listings.extend(fetch_rss())
    all_listings.extend(fetch_jobspy())   # slowest, do last

    # Deduplicate by URL using a dict (Python dicts preserve insertion order).
    # The dict key is the URL; the value is the listing dict.
    # If the same URL appears twice, the second one simply overwrites the first
    # — but since they're the same listing, it doesn't matter.
    seen_urls = {}
    for listing in all_listings:
        url = listing.get("url", "")
        if url and url not in seen_urls:
            seen_urls[url] = listing
        elif not url:
            # Listings with no URL (some RSS entries) can't be deduped by URL.
            # We give them a fake unique key based on title+company.
            fake_key = f"{listing['title']}|{listing['company']}"
            if fake_key not in seen_urls:
                seen_urls[fake_key] = listing

    unique_listings = list(seen_urls.values())
    logger.info(f"Total unique relevant listings across all sources: {len(unique_listings)}")
    return unique_listings
