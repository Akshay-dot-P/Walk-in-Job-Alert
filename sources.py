# =============================================================================
# sources.py
# =============================================================================
# Scrapes walk-in job listings via python-jobspy (speedyapply fork).
#
# SOURCE STATUS — based on official JobSpy docs + confirmed open issues:
#
#   ✅ LinkedIn     — works; rate-limits ~10 pages/IP, manageable for our volume
#   ✅ Indeed India — works with correct country_indeed + precise query syntax
#                     (issue #342: intermittent from IN IPs; handled with retry)
#   ✅ Glassdoor    — FIXED: needs country_indeed="India" for correct routing
#                     (without it you get 403 — this was the bug in the old code)
#   ✅ Naukri       — India-native, works great from GitHub Actions IPs
#   ❌ ZipRecruiter — US/CANADA ONLY by design. CF-WAF 403 on India IPs is
#                     permanent and intentional. Cannot be fixed. (issue #302)
#   ❌ Google Jobs  — Needs exact browser-session query syntax. Returns 0 from
#                     CI IPs without a live browser. (issue #302, open, no fix)
#
# GLASSDOOR FIX (root cause of the 403s in your logs):
#   The country_indeed param controls routing for BOTH Indeed AND Glassdoor.
#   Without country_indeed="India", Glassdoor hits a wrong endpoint → 403.
#
# INDEED QUERY SYNTAX (from official README FAQ):
#   Indeed searches full description, not just title. Quoted phrases + negative
#   keywords (-senior -manager) dramatically cut noise vs plain search_term.
#
# NAUKRI NOTE (issue #301):
#   Naukri descriptions sometimes return null. The AI scorer handles this.
#
# TIMEOUT PATCH:
#   tls_client hardcodes 10s read timeout. GitHub Actions → in.indeed.com is
#   slow. Patched to 45s at module import time before jobspy loads tls_client.
# =============================================================================

import logging
import time

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monkey-patch tls_client timeout BEFORE importing jobspy
# ---------------------------------------------------------------------------
try:
    import tls_client

    _orig = tls_client.Session.execute_request

    def _patched(self, method, url, **kwargs):
        kwargs["timeout_seconds"] = 45
        return _orig(self, method, url, **kwargs)

    tls_client.Session.execute_request = _patched
    logger.debug("tls_client timeout patched → 45s")
except Exception as e:
    logger.warning(f"tls_client patch skipped ({e})")

try:
    from jobspy import scrape_jobs
except ImportError as e:
    raise ImportError("python-jobspy not installed. Run: pip install python-jobspy") from e

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

HOURS_OLD = 9           # 3x/day runs are ~8h apart; 9h catches everything since last run
LOCATION = "Bangalore, India"   # More precise → fewer off-topic non-Bangalore results
RESULTS_WANTED = 25     # Per (source × term) call — keeps Groq scoring load manageable

INTER_SOURCE_DELAY = 8  # seconds between sources
INTER_TERM_DELAY = 4    # seconds between search terms within a source

# ---------------------------------------------------------------------------
# SEARCH TERMS — using Indeed's advanced query syntax
#
# Rules (from official JobSpy README FAQ):
#   "term"       = exact phrase required anywhere in title or description
#   (A OR B)     = any of these words
#   -word        = exclude this word from results
#
# Negative keywords (-senior -lead -manager) are essential because Indeed
# searches the full description — without them you drown in senior-level noise.
# ---------------------------------------------------------------------------
SEARCH_TERMS = [
    # Security
    '"walk-in" ("security analyst" OR "appsec" OR "application security") -senior -lead -manager -director',
    '"walk-in" (cybersecurity OR "infosec" OR "information security" OR VAPT OR SOC) fresher -senior -manager',
    '"walkin" ("security analyst" OR "sec analyst" OR "security engineer") Bangalore',
    # Fraud / AML / ORC
    '"walk-in" ("fraud analyst" OR "AML analyst" OR "anti money laundering" OR "transaction monitoring") -senior',
    '"walk-in" ("loss prevention" OR ORC OR "organized retail crime" OR "financial crimes")',
    # GRC / Risk / Compliance
    '"walk-in" (GRC OR compliance OR "risk analyst" OR "operational risk" OR "credit risk") -senior -manager',
    '"walk-in" ("internal audit" OR "IT audit" OR "regulatory compliance" OR "policy analyst") -manager',
    # Intern / Fresher / Entry-level
    '"walk-in" (intern OR internship OR fresher OR trainee) Bangalore (security OR risk OR compliance OR fraud)',
    '"walkin" (intern OR fresher OR "entry level" OR "graduate trainee") Bangalore',
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def _row_to_listing(row, source_label: str) -> dict:
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


def _scrape_one(source_label: str, search_term: str, **kwargs) -> list[dict]:
    """Single (source, term) scrape with one retry. Always returns a list."""
    for attempt in range(1, 3):
        try:
            df = scrape_jobs(search_term=search_term, **kwargs)
            if df is None or df.empty:
                logger.debug(f"{source_label} [{search_term[:45]!r}] → 0 results")
                return []
            listings = [_row_to_listing(row, source_label) for _, row in df.iterrows()]
            logger.info(f"{source_label} [{search_term[:45]!r}] → {len(listings)}")
            return listings
        except Exception as exc:
            if attempt == 1:
                logger.warning(f"{source_label} attempt 1 failed: {exc} — retrying in 15s")
                time.sleep(15)
            else:
                logger.error(f"{source_label} failed after 2 attempts: {exc}")
                return []
    return []


def _scrape_source(source_label: str, **base_kwargs) -> list[dict]:
    """Run all SEARCH_TERMS against one source, return URL-deduped results."""
    all_results: list[dict] = []
    for i, term in enumerate(SEARCH_TERMS):
        results = _scrape_one(source_label, term, **base_kwargs)
        all_results.extend(results)
        if i < len(SEARCH_TERMS) - 1:
            time.sleep(INTER_TERM_DELAY)

    seen: set[str] = set()
    unique = []
    for listing in all_results:
        url = listing.get("job_url") or ""
        if url and url in seen:
            continue
        seen.add(url)
        unique.append(listing)

    logger.info(f"{source_label} total: {len(all_results)} raw → {len(unique)} unique")
    return unique


# ---------------------------------------------------------------------------
# Source scrapers
# ---------------------------------------------------------------------------

def _scrape_linkedin() -> list[dict]:
    """
    LinkedIn — most reliable source from GitHub Actions IPs.
    Full description fetch enabled for better AI scoring quality.
    Rate-limit: ~10 pages/IP. RESULTS_WANTED=25 keeps us safely under.
    """
    logger.info("=== LinkedIn: starting ===")
    return _scrape_source(
        "LinkedIn",
        site_name=["linkedin"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        linkedin_fetch_description=True,
        verbose=0,
    )


def _scrape_indeed() -> list[dict]:
    """
    Indeed India — country_indeed="India" routes to in.indeed.com.
    tls_client 45s patch handles the ReadTimeout from GitHub Actions → India servers.
    Returns 0 gracefully if Indeed is having a bad day (issue #342).
    """
    logger.info("=== Indeed India: starting ===")
    return _scrape_source(
        "Indeed",
        site_name=["indeed"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed="India",
        verbose=0,
    )


def _scrape_glassdoor() -> list[dict]:
    """
    Glassdoor India.
    
    ROOT CAUSE OF YOUR 403s: country_indeed="India" is required for Glassdoor
    too — this single param controls country routing for BOTH Indeed and Glassdoor
    in JobSpy. The old code called Glassdoor WITHOUT this param, which sent
    requests to the wrong endpoint → 403 every single time.
    """
    logger.info("=== Glassdoor India: starting ===")
    return _scrape_source(
        "Glassdoor",
        site_name=["glassdoor"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        country_indeed="India",  # ← THE FIX: without this = always 403
        verbose=0,
    )


def _scrape_naukri() -> list[dict]:
    """
    Naukri — India's #1 job board. Best walk-in coverage for Bangalore.
    Available in speedyapply/JobSpy fork (needs python-jobspy >= 1.1.80).
    Works reliably from GitHub Actions IPs — no geo-blocking, no CF-WAF.
    Description sometimes null (issue #301) — AI scorer handles gracefully.
    """
    logger.info("=== Naukri: starting ===")
    return _scrape_source(
        "Naukri",
        site_name=["naukri"],
        location=LOCATION,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        verbose=0,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gather_all_listings() -> list[dict]:
    """
    Run all active scrapers sequentially. Returns cross-source URL-deduped list.

    Active: LinkedIn, Indeed, Glassdoor (fixed), Naukri (new)
    Dropped: ZipRecruiter (US/CA only), Google Jobs (needs browser session)
    """
    all_listings: list[dict] = []
    counts: dict[str, int] = {}

    for scraper, label in [
        (_scrape_linkedin,  "LinkedIn"),
        (_scrape_indeed,    "Indeed"),
        (_scrape_glassdoor, "Glassdoor"),
        (_scrape_naukri,    "Naukri"),
    ]:
        results = scraper()
        all_listings.extend(results)
        counts[label] = len(results)
        if label != "Naukri":   # no sleep needed after last source
            time.sleep(INTER_SOURCE_DELAY)

    # Cross-source URL dedup
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for listing in all_listings:
        url = listing.get("job_url") or ""
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(listing)

    logger.info(
        f"gather_all_listings complete: {len(all_listings)} raw → {len(unique)} unique  "
        f"| LinkedIn={counts['LinkedIn']} Indeed={counts['Indeed']} "
        f"Glassdoor={counts['Glassdoor']} Naukri={counts['Naukri']}"
    )
    return unique
