import logging
import time
import random
import pandas as pd
from typing import Any
from jobspy import scrape_jobs

logger = logging.getLogger(__name__)

# --- REVISED CONFIG ---
SEARCH_TERM = "walk in interview"
LOCATION = "India"
HOURS_OLD = 72  # Increased slightly to ensure we don't miss weekend posts
RESULTS_WANTED = 40 # Lowered per source to stay under Groq limits more easily
INTER_SOURCE_DELAY = 5 # Increased to be safer against IP blocks

def _jobspy_to_listing(row: Any, source_label: str) -> dict:
    """Helper to convert JobSpy row to dict with NaN protection."""
    def _safe(val):
        if pd.isna(val) or val is None:
            return None
        return val

    return {
        "source": source_label,
        "title": _safe(row.get("title")),
        "company": _safe(row.get("company")),
        "location": _safe(row.get("location")),
        "job_type": _safe(row.get("job_type")),
        "date_posted": str(_safe(row.get("date_posted")) or ""),
        "description": _safe(row.get("description")) or "",
        "job_url": _safe(row.get("job_url")),
        "is_remote": _safe(row.get("is_remote")),
    }

def _scrape_indeed_india() -> list[dict]:
    """
    FIX: Indeed India often requires a more specific location 
    or just 'India' with the country_indeed param.
    """
    logger.info("JobSpy:Indeed India — starting")
    try:
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=SEARCH_TERM,
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            country_indeed="india", # Ensure lowercase
        )
        if df is None or df.empty:
            return []
        return [_jobspy_to_listing(row, "Indeed India") for _, row in df.iterrows()]
    except Exception as exc:
        logger.error(f"Indeed India failed: {exc}")
        return []

def _scrape_google_jobs() -> list[dict]:
    """
    FIX: Google Jobs is query-driven. Using a more 'Google-friendly' 
    string improves visibility of Naukri/Glassdoor aggregates.
    """
    logger.info("JobSpy:Google Jobs — starting")
    # Using specific 'site:' operators via Google Jobs search is often blocked, 
    # so we use natural language that triggers the 'Walk-in' widget.
    google_query = f"{SEARCH_TERM} drive in {LOCATION} jobs"
    
    try:
        df = scrape_jobs(
            site_name=["google"],
            google_search_term=google_query,
            results_wanted=RESULTS_WANTED,
            # Note: Google Jobs doesn't support 'hours_old' in JobSpy 
            # as it's a direct scraper of the search results page.
        )
        if df is None or df.empty:
            return []
        return [_jobspy_to_listing(row, "Google Jobs") for _, row in df.iterrows()]
    except Exception as exc:
        logger.error(f"Google Jobs failed: {exc}")
        return []

def gather_all_listings() -> list[dict]:
    """Sequential execution with delay to prevent IP flagging."""
    all_results = []
    
    # Run scrapers
    scrapers = [
        ("LinkedIn", _scrape_linkedin),
        ("Indeed", _scrape_indeed_india),
        ("Google", _scrape_google_jobs)
    ]
    
    for name, func in scrapers:
        results = func()
        all_results.extend(results)
        logger.info(f"Collected {len(results)} from {name}")
        time.sleep(INTER_SOURCE_DELAY)

    # URL Dedup
    seen = set()
    unique = []
    for item in all_results:
        url = item.get("job_url")
        if url not in seen:
            unique.append(item)
            seen.add(url)
            
    return unique
