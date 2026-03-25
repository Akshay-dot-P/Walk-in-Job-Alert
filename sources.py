# =============================================================================
# sources.py
# =============================================================================
# Scrapes walk-in job listings from multiple sources via JobSpy.
#
# ACTIVE SOURCES:
# 1. LinkedIn      — stable, confirmed working
# 2. Indeed India  — working; needs runtime timeout patch (see below)
# 3. Glassdoor     — re-enabled after JobSpy fixed 403s (user-agent rotation)
# 4. ZipRecruiter  — added; good supplementary source for India postings
#
# DROPPED SOURCES:
# - Naukri       — HTTP 406 + reCAPTCHA, no free workaround
# - Google Jobs  — returns 0 results reliably from GitHub Actions IPs
#
# INDEED TIMEOUT PATCH:
# JobSpy uses `tls_client` internally and hardcodes a 10-second read
# timeout. in.indeed.com is slow from GitHub Actions and reliably hits
# this limit. Since scrape_jobs() doesn't expose a timeout param, we
# monkey-patch tls_client.Session.execute_request at import time to
# inject a 45s timeout. This is the only reliable fix short of forking
# JobSpy itself.
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
        kwargs["timeout_seconds"] = 45  # up from hardcoded 10s
        return _original_execute(self, method, url, **kwargs)

    tls_client.Session.execute_request = _patched_execute
    logger.debug("tls_client timeout patched to 45s")
except Exception as patch_err:
    # Non-fatal: if tls_client API changes, log a warning and proceed.
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

# Since the scanner runs 3x/day with 8h between runs, HOURS_OLD=9 ensures
# we catch everything posted since the last run without missing anything.
HOURS_OLD = 9

LOCATION = "India"
RESULTS_WANTED = 30  # per search term per source (keeps Groq scoring manageable)

# Multiple search terms — each gets scraped separately and merged.
# Covering: security, GRC, risk, compliance, fraud, ORC, intern walk-ins.
SEARCH_TERMS = [
    "walk in interview security analyst",
    "walk in interview application security",
    "walk in interview risk analyst",
    "walk in interview compliance analyst",
    "walk in interview fraud analyst",
    "walk in interview intern bangalore",
    "walk in interview GRC analyst",
    "walk in interview AML analyst",
    "walkin interview cybersecurity fresher",
]

# Sleep between source calls — prevents per-IP rate limits when scrapers
# run back-to-back in the same GitHub Actions job.
INTER_SOURCE_DELAY = 8


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _jobspy_to_listing(row: Any, source_label: str) -> dict:
    """
    Convert a single JobSpy pandas Series row into the flat dict format
    expected by scorer.py.

    IMPORTANT: The canonical URL field in our pipeline is "job_url".
    scorer.py and storage.py both expect "job_url". Do NOT use "url" here.
    """
    import pandas as pd

    def _safe(val):
        if val is None:
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        return val

    return {
        "source": source_label,
        "title": _safe(row.get("title")),
        "company": _safe(row.get("company")),
        "location": _safe(row.get("location")),
        "job_type": _safe(row.get("job_type")),
        "date_posted": str(_safe(row.get("date_posted")) or ""),
        "description": _safe(row.get("description")) or "",
        # "job_url" is the canonical field name used throughout the pipeline.
        # scorer.py reads listing["job_url"], storage.py writes it as "url" col.
        "job_url": _safe(row.get("job_url")),
        "is_remote": _safe(row.get("is_remote")),
    }


def _run_scrape_one_term(source_label: str, search_term: str, **kwargs) -> list[dict]:
    """
    Scrape a single (source, search_term) combination with one retry on failure.
    Returns [] on total failure so one bad call never kills the pipeline.
    """
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            df = scrape_jobs(search_term=search_term, **kwargs)
            if df is None or df.empty:
                logger.debug(f"JobSpy:{source_label} [{search_term!r}] — 0 results")
                return []
            listings = [_jobspy_to_listing(row, source_label) for _, row in df.iterrows()]
            logger.info(
                f"JobSpy:{source_label} [{search_term!r}] — {len(listings)} listings"
            )
            return listings
        except Exception as exc:
            if attempt < max_attempts:
                logger.warning(
                    f"JobSpy:{source_label} [{search_term!r}] — attempt {attempt} "
                    f"failed ({exc}), retrying in 15s..."
                )
                time.sleep(15)
            else:
                logger.error(
                    f"JobSpy:{source_label} [{search_term!r}] — "
                    f"failed after {max_attempts} attempts: {exc}"
                )
                return []
    return []


def _scrape_all_terms(source_label: str, inter_term_delay: int = 3, **base_kwargs) -> list[dict]:
    """
    Run _run_scrape_one_term for every search term in SEARCH_TERMS, merge
    results, and return a URL-deduped list for this source.
    """
    all_for_source: list[dict] = []
    for i, term in enumerate(SEARCH_TERMS):
        results = _run_scrape_one_term(source_label, term, **base_kwargs)
        all_for_source.extend(results)
        # Short pause between search terms to avoid hammering the same source
        if i < len(SEARCH_TERMS) - 1:
            time.sleep(inter_term_delay)

    # URL-dedup within this source
    seen: set[str] = set()
    unique = []
    for listing in all_for_source:
        url = listing.get("job_url") or ""
        if url and url in seen:
            continue
        seen.add(url)
        unique.append(listing)

    logger.info(
        f"JobSpy:{source_label} — {len(all_for_source)} raw across all terms "
        f"→ {len(unique)} after URL dedup"
    )
    return unique


# ---------------------------------------------------------------------------
# Individual source scrapers
# ---------------------------------------------------------------------------

def _scrape_linkedin() -> list[dict]:
    """LinkedIn walk-in listings in India — all search terms."""
    logger.info("JobSpy:LinkedIn — starting multi-term scrape")
    return _scrape_all_terms(
        "LinkedIn",
        site_name=["linkedin"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        linkedin_fetch_description=True,
    )


def _scrape_indeed_india() -> list[dict]:
    """
    Indeed India (in.indeed.com) — all search terms.
    The tls_client 45s timeout patch at module load time prevents ReadTimeout.
    """
    logger.info("JobSpy:Indeed India — starting multi-term scrape")
    return _scrape_all_terms(
        "Indeed India",
        site_name=["indeed"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed="India",
    )


def _scrape_glassdoor() -> list[dict]:
    """Glassdoor India listings — all search terms."""
    logger.info("JobSpy:Glassdoor — starting multi-term scrape")
    return _scrape_all_terms(
        "Glassdoor",
        site_name=["glassdoor"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
    )


def _scrape_ziprecruiter() -> list[dict]:
    """ZipRecruiter India listings — all search terms."""
    logger.info("JobSpy:ZipRecruiter — starting multi-term scrape")
    return _scrape_all_terms(
        "ZipRecruiter",
        site_name=["zip_recruiter"],
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
    time.sleep(INTER_SOURCE_DELAY)

    # 4. ZipRecruiter
    ziprecruiter_listings = _scrape_ziprecruiter()
    all_listings.extend(ziprecruiter_listings)

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
        f"Glassdoor={len(glassdoor_listings)}, "
        f"ZipRecruiter={len(ziprecruiter_listings)}, "
        f"SearchTerms={len(SEARCH_TERMS)})"
    )
    return unique
