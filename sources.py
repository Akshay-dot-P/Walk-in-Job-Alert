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
#                                  without hitting them directly; best free
#                                  Naukri alternative available
#
# WHY NAUKRI WAS REMOVED:
#   Naukri returns HTTP 406 + reCAPTCHA for all automated requests. There is
#   no free workaround — rotating proxies or headless browsers require paid
#   services and are fragile. Instead, Google Jobs surfaces the same Naukri
#   listings (and more) without any bot protection, making it the ideal
#   drop-in replacement.
#
# ADDING MORE SOURCES LATER:
#   To add Glassdoor or Bayt (Gulf), just add them to the site_name list in
#   their respective scrape_jobs call below. Both are supported by JobSpy
#   but rate-limit more aggressively, so they are kept separate with their
#   own try/except blocks.
# =============================================================================
import logging
import time
from typing import Any
logger = logging.getLogger(__name__)
# ---------------------------------------------------------------------------
# Import JobSpy. If it's not installed the error surfaces clearly at startup
# rather than silently at scrape time.
# ---------------------------------------------------------------------------
try:
    from jobspy import scrape_jobs
except ImportError as e:
    raise ImportError(
        "python-jobspy is not installed. Run: pip install python-jobspy"
    ) from e
# ---------------------------------------------------------------------------
# CONFIG — edit these to match your walk-in search criteria.
# These are kept here (not in config.py) so each source call can be tuned
# independently without touching the shared config.
# ---------------------------------------------------------------------------
# The search term sent to every job board.
# Tip: keep it broad for walk-ins; AI scoring in Phase 2 filters the noise.
SEARCH_TERM = "walk in interview"
# Location string understood by all boards. For Indeed India this narrows
# results to the subregion; for Google Jobs it biases the geo ranking.
LOCATION = "India"
# How many hours back to look. 24h keeps results fresh for daily runs.
HOURS_OLD = 24
# Max results per source. JobSpy caps at ~1000 per search regardless.
RESULTS_WANTED = 50
# Delay in seconds between source calls. Helps avoid triggering rate limits
# when running multiple scrapes in the same GitHub Actions job.
INTER_SOURCE_DELAY = 3
# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _jobspy_to_listing(row: Any, source_label: str) -> dict:
    """
    Convert a single pandas Series row from JobSpy into the flat dict format
    expected by scorer.py.
    Only fields that are reliably populated across all boards are extracted.
    The scorer's AI prompt will handle missing/None values gracefully.
    """
    def _safe(val):
        """Return None instead of NaN/NaT so JSON serialisation doesn't choke."""
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
        "source":       source_label,
        "title":        _safe(row.get("title")),
        "company":      _safe(row.get("company")),
        "location":     _safe(row.get("location")),
        "job_type":     _safe(row.get("job_type")),
        "date_posted":  str(_safe(row.get("date_posted")) or ""),
        "description":  _safe(row.get("description")) or "",
        "job_url":      _safe(row.get("job_url")),
        "is_remote":    _safe(row.get("is_remote")),
    }
def _scrape_linkedin() -> list[dict]:
    """
    Scrape LinkedIn for walk-in job postings in India.
    LinkedIn is the most reliable source but rate-limits hard after ~10 pages
    (~100 results). RESULTS_WANTED is intentionally kept modest.
    """
    logger.info("JobSpy:LinkedIn — starting scrape")
    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=SEARCH_TERM,
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            linkedin_fetch_description=True,   # richer descriptions for AI scorer
        )
        if df is None or df.empty:
            logger.warning("JobSpy:LinkedIn — returned 0 results")
            return []
        listings = [_jobspy_to_listing(row, "LinkedIn") for _, row in df.iterrows()]
        logger.info(f"JobSpy:LinkedIn — {len(listings)} listings collected")
        return listings
    except Exception as exc:
        logger.error(f"JobSpy:LinkedIn — failed: {exc}")
        return []
def _scrape_indeed_india() -> list[dict]:
    """
    Scrape Indeed India (in.indeed.com) via JobSpy.
    country_indeed="India" tells JobSpy to hit the Indian Indeed domain
    instead of indeed.com, which avoids US-centric results and gives us
    the same listing pool that Naukri users see (many companies cross-post).
    Indeed India has no meaningful bot protection from GitHub Actions IPs
    as of 2025-2026; no proxies needed.
    """
    logger.info("JobSpy:Indeed India — starting scrape")
    try:
        df = scrape_jobs(
            site_name=["indeed"],
            search_term=SEARCH_TERM,
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            country_indeed="India",            # ← key param; hits in.indeed.com
        )
        if df is None or df.empty:
            logger.warning("JobSpy:Indeed India — returned 0 results")
            return []
        listings = [_jobspy_to_listing(row, "Indeed India") for _, row in df.iterrows()]
        logger.info(f"JobSpy:Indeed India — {len(listings)} listings collected")
        return listings
    except Exception as exc:
        logger.error(f"JobSpy:Indeed India — failed: {exc}")
        return []
def _scrape_google_jobs() -> list[dict]:
    """
    Scrape Google Jobs for walk-in listings in India.
    Google Jobs is the single best replacement for Naukri because:
      - It aggregates listings FROM Naukri, TimesJobs, Glassdoor India,
        Shine, Freshersworld, and company career pages simultaneously.
      - It has no meaningful CAPTCHA barrier for programmatic access via
        JobSpy's implementation.
      - The google_search_term lets us be very specific: we ask for recent
        walk-in drives in India, which is the exact query a job seeker would
        type into Google Jobs.
    NOTE: google_search_term is the ONLY filtering param for Google Jobs.
    All other JobSpy params (hours_old, location, job_type, etc.) are
    ignored for this source — filtering happens through the query string.
    """
    logger.info("JobSpy:Google Jobs — starting scrape")
    # Build a query that mimics what a real user types when searching for
    # walk-in drives on Google. Specific phrasing gets much better results
    # than a generic keyword.
    google_query = (
        "walk in interview OR walk-in drive OR walk in drive jobs India today"
    )
    try:
        df = scrape_jobs(
            site_name=["google"],
            search_term=SEARCH_TERM,           # fallback term (not used by google)
            google_search_term=google_query,   # ← this is what Google Jobs uses
            location=LOCATION,
            results_wanted=RESULTS_WANTED,
        )
        if df is None or df.empty:
            logger.warning("JobSpy:Google Jobs — returned 0 results")
            return []
        listings = [_jobspy_to_listing(row, "Google Jobs") for _, row in df.iterrows()]
        logger.info(f"JobSpy:Google Jobs — {len(listings)} listings collected")
        return listings
    except Exception as exc:
        logger.error(f"JobSpy:Google Jobs — failed: {exc}")
        return []
# ---------------------------------------------------------------------------
# Optional future sources (commented out — uncomment to enable)
# ---------------------------------------------------------------------------
# def _scrape_glassdoor() -> list[dict]:
#     """
#     Glassdoor India. Works via JobSpy but requires a valid Glassdoor login
#     session cookie (GLASSDOOR_SESSION env var) on some IPs. Rate limits
#     aggressively. Enable once you have a session cookie configured.
#     """
#     logger.info("JobSpy:Glassdoor — starting scrape")
#     try:
#         df = scrape_jobs(
#             site_name=["glassdoor"],
#             search_term=SEARCH_TERM,
#             location=LOCATION,
#             results_wanted=RESULTS_WANTED,
#             hours_old=HOURS_OLD,
#         )
#         if df is None or df.empty:
#             logger.warning("JobSpy:Glassdoor — returned 0 results")
#             return []
#         listings = [_jobspy_to_listing(row, "Glassdoor") for _, row in df.iterrows()]
#         logger.info(f"JobSpy:Glassdoor — {len(listings)} listings collected")
#         return listings
#     except Exception as exc:
#         logger.error(f"JobSpy:Glassdoor — failed: {exc}")
#         return []
# ---------------------------------------------------------------------------
# Public API — called by scanner.py
# ---------------------------------------------------------------------------
def gather_all_listings() -> list[dict]:
    """
    Run all active scrapers in sequence and return a combined, de-duped list
    of raw listing dicts.
    Each scraper is isolated in its own try/except inside its function, so
    a failure in one source never prevents the others from running.
    De-duplication here is URL-based (cheap and fast). The storage layer in
    Phase 3 does a more thorough dedup against the Sheets history.
    """
    all_listings: list[dict] = []
    # --- LinkedIn ---
    linkedin_listings = _scrape_linkedin()
    all_listings.extend(linkedin_listings)
    time.sleep(INTER_SOURCE_DELAY)
    # --- Indeed India (primary Naukri replacement) ---
    indeed_listings = _scrape_indeed_india()
    all_listings.extend(indeed_listings)
    time.sleep(INTER_SOURCE_DELAY)
    # --- Google Jobs (secondary Naukri replacement; aggregates many boards) ---
    google_listings = _scrape_google_jobs()
    all_listings.extend(google_listings)
    # URL-based dedup: keep the first occurrence of each URL.
    seen_urls: set[str] = set()
    unique_listings: list[dict] = []
    for listing in all_listings:
        url = listing.get("job_url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique_listings.append(listing)
    logger.info(
        f"gather_all_listings complete: {len(all_listings)} raw → "
        f"{len(unique_listings)} after URL dedup "
        f"(LinkedIn={len(linkedin_listings)}, "
        f"IndeedIndia={len(indeed_listings)}, "
        f"GoogleJobs={len(google_listings)})"
    )
    return unique_listings
