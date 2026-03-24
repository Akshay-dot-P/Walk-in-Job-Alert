# =============================================================================
# sources.py
# =============================================================================
# Scrapes walk-in job listings from multiple sources.
#
# ACTIVE SOURCES:
#   1. LinkedIn     — stable, confirmed working
#   2. Indeed India — working; needs runtime timeout patch (see below)
#   3. Glassdoor    — re-enabled after JobSpy fixed 403s (user-agent rotation)
#
# DROPPED SOURCES:
#   - Naukri     — HTTP 406 + reCAPTCHA, no free workaround
#   - Google Jobs — returns 0 results reliably from GitHub Actions IPs;
#                   open issue #302 on JobSpy with no fix as of Mar 2026.
#                   google_search_term must exactly match Google's own search
#                   box output — impossible to construct reliably at runtime
#                   without a live browser session.
#
# INDEED TIMEOUT PATCH:
#   JobSpy uses `tls_client` internally and hardcodes a 10-second read
#   timeout. in.indeed.com is slow from GitHub Actions and reliably hits
#   this limit. Since scrape_jobs() doesn't expose a timeout param, we
#   monkey-patch tls_client.Session.execute_request at import time to
#   inject a 45s timeout. This is the only reliable fix short of forking
#   JobSpy itself.
# =============================================================================

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monkey-patch tls_client timeout BEFORE importing jobspy.
# Must happen here, at module load time, before jobspy imports tls_client.
# ---------------------------------------------------------------------------
try:
    import tls_client

    _original_execute = tls_client.Session.execute_request

    def _patched_execute(self, method, url, **kwargs):
        kwargs["timeout_seconds"] = 45   # up from hardcoded 10s
        return _original_execute(self, method, url, **kwargs)

    tls_client.Session.execute_request = _patched_execute
    logger.debug("tls_client timeout patched to 45s")

except Exception as patch_err:
    # Non-fatal: if tls_client API changes, log a warning and proceed.
    # Indeed may still timeout occasionally, but other sources will work.
    logger.warning(f"Could not patch tls_client timeout: {patch_err}")

# ---------------------------------------------------------------------------
# Now safe to import JobSpy (which imports tls_client internally)
# ---------------------------------------------------------------------------
try:
    from jobspy import scrape_jobs
except ImportError as e:
    raise ImportError(
        "python-jobspy is not installed. Run: pip install python-jobspy"
    ) from e


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
SEARCH_TERM    = "walk in interview"
LOCATION       = "India"
HOURS_OLD      = 24
RESULTS_WANTED = 50

# Sleep between source calls — prevents per-IP rate limits when scrapers
# run back-to-back in the same GitHub Actions job.
INTER_SOURCE_DELAY = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _jobspy_to_listing(row: Any, source_label: str) -> dict:
    """
    Convert a single JobSpy pandas Series row into the flat dict format
    expected by scorer.py. Only reliably-populated fields are extracted.
    The AI scorer handles None values gracefully.
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


def _run_scrape(source_label: str, **kwargs) -> list[dict]:
    """
    Shared scrape wrapper with one retry on failure.
    Returns [] on total failure so one bad source never kills the pipeline.
    """
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            df = scrape_jobs(**kwargs)
            if df is None or df.empty:
                logger.warning(f"JobSpy:{source_label} — returned 0 results")
                return []
            listings = [_jobspy_to_listing(row, source_label) for _, row in df.iterrows()]
            logger.info(f"JobSpy:{source_label} — {len(listings)} listings collected")
            return listings
        except Exception as exc:
            if attempt < max_attempts:
                logger.warning(
                    f"JobSpy:{source_label} — attempt {attempt} failed ({exc}), "
                    f"retrying in 15s..."
                )
                time.sleep(15)
            else:
                logger.error(
                    f"JobSpy:{source_label} — failed after {max_attempts} attempts: {exc}"
                )
                return []
    return []


# ---------------------------------------------------------------------------
# Individual source scrapers
# ---------------------------------------------------------------------------

def _scrape_linkedin() -> list[dict]:
    """
    LinkedIn walk-in listings in India.
    linkedin_fetch_description=True gives richer text for the AI scorer.
    LinkedIn rate-limits at ~10 pages; RESULTS_WANTED=50 is safely under that.
    """
    logger.info("JobSpy:LinkedIn — starting scrape")
    return _run_scrape(
        "LinkedIn",
        site_name=["linkedin"],
        search_term=SEARCH_TERM,
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        linkedin_fetch_description=True,
    )


def _scrape_indeed_india() -> list[dict]:
    """
    Indeed India (in.indeed.com) via JobSpy.
    country_indeed="India" routes to the Indian domain.
    The tls_client 45s timeout patch at the top of this file prevents the
    ReadTimeout that the default hardcoded 10s causes from GitHub Actions.
    """
    logger.info("JobSpy:Indeed India — starting scrape")
    return _run_scrape(
        "Indeed India",
        site_name=["indeed"],
        search_term=SEARCH_TERM,
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed="India",
    )


def _scrape_glassdoor() -> list[dict]:
    """
    Glassdoor India listings.
    JobSpy fixed Glassdoor 403 errors in late 2024 via user-agent rotation
    (issue #270 fix). Re-enabled here as a third reliable source.
    No country filter available for Glassdoor — location param narrows it;
    non-India results are caught and dropped by the AI scorer at Phase 2.
    """
    logger.info("JobSpy:Glassdoor — starting scrape")
    return _run_scrape(
        "Glassdoor",
        site_name=["glassdoor"],
        search_term=SEARCH_TERM,
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
    )


# ---------------------------------------------------------------------------
# Public API — called by scanner.py
# ---------------------------------------------------------------------------

def gather_all_listings() -> list[dict]:
    """
    Run all active scrapers sequentially and return a combined, URL-deduped
    list of raw listing dicts.

    Each scraper handles its own failures internally and always returns a list,
    so a failure in one source never blocks the others.

    URL-based dedup here is cheap and fast. Phase 3 (storage.py) does a
    more thorough dedup against the full Google Sheets history.
    """
    all_listings: list[dict] = []

    # 1. LinkedIn
    linkedin_listings = _scrape_linkedin()
    all_listings.extend(linkedin_listings)
    time.sleep(INTER_SOURCE_DELAY)

    # 2. Indeed India
    indeed_listings = _scrape_indeed_india()
    all_listings.extend(indeed_listings)
    time.sleep(INTER_SOURCE_DELAY)

    # 3. Glassdoor
    glassdoor_listings = _scrape_glassdoor()
    all_listings.extend(glassdoor_listings)

    # URL-based dedup — keep first occurrence of each job URL
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for listing in all_listings:
        url = listing.get("job_url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(listing)

    logger.info(
        f"gather_all_listings complete: {len(all_listings)} raw → "
        f"{len(unique)} after URL dedup "
        f"(LinkedIn={len(linkedin_listings)}, "
        f"IndeedIndia={len(indeed_listings)}, "
        f"Glassdoor={len(glassdoor_listings)})"
    )
    return unique
