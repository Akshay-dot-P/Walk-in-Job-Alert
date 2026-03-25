"""
sources.py — Job scraping via python-jobspy (speedyapply fork).

Source status after investigation:
  LinkedIn  ✅  Works reliably from GitHub Actions
  Indeed    ✅  Works intermittently; 0-result runs are handled gracefully
  Glassdoor ❌  REMOVED — GitHub Actions IPs are blocked by Glassdoor's
                Cloudflare WAF (HTTP 403 on every single call, every term).
                This is a deliberate IP block, not a code bug. There is no
                fix without a residential proxy or self-hosted runner.
  Naukri    ❌  REMOVED — Returns 406 "recaptcha required" for all CI IP
                ranges. Same root cause as Glassdoor.
"""

import time
import logging

import pandas as pd
import jobspy

logger = logging.getLogger(__name__)

# ── Location ──────────────────────────────────────────────────────────────────
LOCATION         = "Bengaluru, Karnataka, India"
HOURS_OLD        = 9     # 3×/day runs ≈ 8h apart → only pull fresh listings
RESULTS_PER_TERM = 40    # per (search term, source) pair

# ── Search terms — 10 entry-level cybersecurity buckets ──────────────────────
# Each term targets one role family with explicit fresher/entry-level qualifiers
# so the portals surface trainee and 0-exp postings alongside junior ones.
SEARCH_TERMS = [
    # 1. SOC / Blue Team
    '("SOC analyst" OR "security operations" OR "L1 SOC" OR "L2 SOC" OR "SIEM analyst") (fresher OR "entry level" OR junior OR trainee OR graduate OR "0-2 years")',
    # 2. AppSec / DevSecOps
    '("application security" OR "appsec" OR "DAST" OR "SAST" OR "security engineer" OR devsecops) (fresher OR "entry level" OR junior OR trainee)',
    # 3. VAPT / Pentest
    '("VAPT" OR "penetration test" OR "ethical hacker" OR "red team" OR "bug bounty") (fresher OR "entry level" OR junior OR "0-2 years")',
    # 4. Vulnerability Management
    '("vulnerability assessment" OR "vulnerability management" OR "patch management" OR "security assessment") (fresher OR "entry level" OR junior)',
    # 5. GRC / Compliance
    '("GRC analyst" OR "governance risk compliance" OR "ISO 27001" OR "compliance analyst" OR "regulatory compliance" OR "data privacy") (fresher OR "entry level" OR junior OR trainee)',
    # 6. IT / IS Audit
    '("IT audit" OR "IS audit" OR "information systems audit" OR "internal audit" OR "ITGC") (fresher OR "entry level" OR junior OR "0-2 years")',
    # 7. Risk Analyst (BFSI)
    '("risk analyst" OR "operational risk" OR "credit risk" OR "market risk" OR "enterprise risk" OR "RCSA") (fresher OR "entry level" OR junior OR trainee)',
    # 8. Fraud / AML / KYC
    '("fraud analyst" OR "AML analyst" OR "KYC analyst" OR "anti-money laundering" OR "transaction monitoring" OR "financial crime") (fresher OR "entry level" OR junior)',
    # 9. Network / Cloud / IAM / DLP
    '("network security" OR "cloud security" OR "IAM analyst" OR "identity access management" OR "DLP analyst" OR "zero trust") (fresher OR "entry level" OR junior)',
    # 10. General Cybersecurity / InfoSec / Intern
    '("cybersecurity" OR "cyber security" OR "information security" OR "infosec") (fresher OR intern OR trainee OR "entry level" OR graduate OR "0-2 years") Bangalore',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a jobspy DataFrame to normalised dicts. Returns [] for empty input."""
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        d = row.to_dict()
        records.append({
            "title":       str(d.get("title")       or ""),
            "company":     str(d.get("company")      or ""),
            "location":    str(d.get("location")     or ""),
            "job_url":     str(d.get("job_url")      or ""),
            "description": str(d.get("description")  or ""),
            "date_posted": str(d.get("date_posted")  or ""),
            "source":      str(d.get("site")         or ""),
        })
    return records


def _run_scrape(site: list[str], term: str, extra_kwargs: dict | None = None) -> list[dict]:
    """
    One jobspy call with up to 3 retries on transient failures.
    extra_kwargs overrides any default parameter (e.g. location for Glassdoor).
    """
    kwargs = dict(
        site_name=site,
        search_term=term,
        location=LOCATION,
        results_wanted=RESULTS_PER_TERM,
        hours_old=HOURS_OLD,
        country_indeed="India",   # required for both Indeed AND Glassdoor routing
    )
    if extra_kwargs:
        kwargs.update(extra_kwargs)

    for attempt in range(1, 4):
        try:
            df = jobspy.scrape_jobs(**kwargs)
            return _to_records(df)
        except Exception as exc:
            logger.warning("  scrape attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(4 * attempt)
    return []


# ── Per-source scrapers ────────────────────────────────────────────────────────

def _scrape_linkedin() -> list[dict]:
    logger.info("=== LinkedIn: starting ===")
    seen: set[str] = set()
    results: list[dict] = []

    for term in SEARCH_TERMS:
        batch = _run_scrape(["linkedin"], term)
        new = [r for r in batch if r["job_url"] not in seen]
        for r in new:
            seen.add(r["job_url"])
        results.extend(new)
        logger.info("  LinkedIn [%s…] → %d", term[:55], len(new))
        time.sleep(5)   # polite delay between terms

    logger.info("LinkedIn total: %d unique", len(results))
    return results


def _scrape_indeed() -> list[dict]:
    logger.info("=== Indeed India: starting ===")
    seen: set[str] = set()
    results: list[dict] = []

    for term in SEARCH_TERMS:
        batch = _run_scrape(["indeed"], term)
        new = [r for r in batch if r["job_url"] not in seen]
        for r in new:
            seen.add(r["job_url"])
        results.extend(new)
        logger.info("  Indeed [%s…] → %d", term[:55], len(new))
        time.sleep(5)

    logger.info("Indeed total: %d unique", len(results))
    return results


# ── GLASSDOOR — PERMANENTLY REMOVED ──────────────────────────────────────────
# Glassdoor's Cloudflare WAF blocks all GitHub Actions IP ranges with HTTP 403.
# This is not a bug — it is a deliberate anti-scraping measure.
# Even with correct location ("Bengaluru") and country_indeed="India" the block
# persists because the IP range itself is blacklisted, not the request params.
#
# To re-enable Glassdoor: use a residential proxy and pass proxy_url= to
# jobspy.scrape_jobs(), or switch to a self-hosted GitHub Actions runner.
# ─────────────────────────────────────────────────────────────────────────────

# ── NAUKRI — PERMANENTLY REMOVED ─────────────────────────────────────────────
# Returns HTTP 406 "recaptcha required" for all requests from cloud CI IPs.
# Same fix applies: residential proxy or self-hosted runner.
# ─────────────────────────────────────────────────────────────────────────────


# ── Main entry point ──────────────────────────────────────────────────────────

def gather_all_listings() -> list[dict]:
    """
    Scrape all working sources and return a single deduplicated list.
    Sources: LinkedIn → Indeed  (Glassdoor and Naukri removed, see above).
    Never raises — source failures are caught internally.
    """
    all_results: list[dict] = []
    seen_urls:   set[str]   = set()
    source_counts: dict[str, int] = {}

    sources = [
        ("LinkedIn", _scrape_linkedin),
        ("Indeed",   _scrape_indeed),
    ]

    for name, fn in sources:
        try:
            batch = fn()
        except Exception as exc:
            logger.error("%s scraper crashed: %s", name, exc)
            batch = []

        before = len(all_results)
        for r in batch:
            if r["job_url"] and r["job_url"] not in seen_urls:
                seen_urls.add(r["job_url"])
                all_results.append(r)

        source_counts[name] = len(all_results) - before
        logger.info("%s added %d unique listings", name, source_counts[name])
        time.sleep(8)   # pause between sources

    logger.info(
        "gather_all_listings complete: %d unique total | %s",
        len(all_results),
        " ".join(f"{k}={v}" for k, v in source_counts.items()),
    )
    return all_results
