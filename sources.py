"""
sources.py — job scraping via python-jobspy (speedyapply fork)

Changes from previous version:
- Removed all "walk-in" requirements from search terms (now scrapes online jobs too)
- Added 10 broad entry-level cybersecurity / GRC / risk role search term buckets
- Glassdoor: location changed to "Bengaluru" (official name; "Bangalore, India" fails geocoding → 400)
- Glassdoor: added linkedin_fetch_recipient_url=False to suppress extra network calls
- Naukri: REMOVED — GitHub Actions IPs are permanently blocked by Naukri's recaptcha (406).
           No fix exists without a residential proxy. See README for workaround options.
- Indeed: kept with graceful 0-result handling (intermittent from CI IPs)
"""

import time
import logging
from datetime import datetime, timezone

import pandas as pd
import jobspy

logger = logging.getLogger(__name__)

# ── Location ──────────────────────────────────────────────────────────────────
LOCATION = "Bengaluru, Karnataka, India"   # official spelling fixes Glassdoor geocoding
HOURS_OLD = 9                              # 3×/day runs ≈ 8h apart
RESULTS_PER_TERM = 40                      # per search term per source

# ── Search terms (walk-in removed — covers online + offline postings) ─────────
#
# 10 buckets covering every entry-level cybersecurity / GRC / risk role:
#
#  1. SOC / Blue Team         — SOC Analyst L1/L2, Security Operations, SIEM
#  2. AppSec / DAST / SAST    — Application Security, AppSec Engineer, Code Review
#  3. VAPT / Pentest          — Penetration Tester, Ethical Hacker, VAPT Engineer
#  4. Vulnerability Mgmt      — VA Analyst, Patch Management, Threat Assessment
#  5. GRC / Compliance        — GRC Analyst, ISO 27001, Compliance Analyst, DPO support
#  6. IT / IS Audit           — IT Audit, IS Audit, Internal Audit, Big 4 GRC
#  7. Risk Analyst            — Operational Risk, Credit Risk, Market Risk, Basel
#  8. Fraud / AML / KYC       — Fraud Analyst, AML, KYC, Anti-Money Laundering
#  9. Network / Cloud / IAM   — Network Security, Cloud Security, IAM, DLP, PAM
# 10. General InfoSec / Intern — Cybersecurity fresher, InfoSec intern, trainee, graduate
#
SEARCH_TERMS = [
    # 1. SOC / Blue Team
    '("SOC analyst" OR "security operations" OR "L1 SOC" OR "L2 SOC" OR "SIEM analyst" OR "security monitoring") (fresher OR "entry level" OR junior OR trainee OR graduate OR "0-2 years" OR "0 to 2")',

    # 2. AppSec / DAST / SAST
    '("application security" OR "appsec" OR "DAST" OR "SAST" OR "secure code review" OR "security engineer") (fresher OR "entry level" OR junior OR trainee OR graduate)',

    # 3. VAPT / Penetration Testing
    '("VAPT" OR "penetration test" OR "ethical hacker" OR "offensive security" OR "red team" OR "bug bounty") (fresher OR "entry level" OR junior OR trainee OR "0-2 years")',

    # 4. Vulnerability Management
    '("vulnerability assessment" OR "vulnerability management" OR "patch management" OR "threat assessment" OR "security assessment") (fresher OR "entry level" OR junior)',

    # 5. GRC / Compliance
    '("GRC analyst" OR "governance risk compliance" OR "ISO 27001" OR "compliance analyst" OR "regulatory compliance" OR "data privacy" OR "GDPR") (fresher OR "entry level" OR junior OR trainee)',

    # 6. IT Audit / IS Audit
    '("IT audit" OR "IS audit" OR "information systems audit" OR "internal audit" OR "CISA" OR "IT risk") (fresher OR "entry level" OR junior OR "0-2 years")',

    # 7. Risk Analyst (BFSI focus)
    '("risk analyst" OR "operational risk" OR "credit risk" OR "market risk" OR "Basel" OR "enterprise risk" OR "ORC" OR "loss prevention") (fresher OR "entry level" OR junior OR trainee)',

    # 8. Fraud / AML / KYC
    '("fraud analyst" OR "AML analyst" OR "KYC analyst" OR "anti-money laundering" OR "transaction monitoring" OR "financial crime") (fresher OR "entry level" OR junior OR trainee)',

    # 9. Network / Cloud / IAM / DLP
    '("network security" OR "cloud security" OR "IAM analyst" OR "identity access management" OR "DLP analyst" OR "PAM" OR "zero trust") (fresher OR "entry level" OR junior)',

    # 10. General Cybersecurity / InfoSec / Intern
    '("cybersecurity" OR "cyber security" OR "information security" OR "infosec") (fresher OR intern OR trainee OR "entry level" OR graduate OR "0-2 years") Bangalore',
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a jobspy DataFrame to a list of plain dicts with normalised keys."""
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        d = row.to_dict()
        records.append({
            "title":        str(d.get("title") or ""),
            "company":      str(d.get("company") or ""),
            "location":     str(d.get("location") or ""),
            "job_url":      str(d.get("job_url") or ""),
            "description":  str(d.get("description") or ""),
            "date_posted":  str(d.get("date_posted") or ""),
            "source":       str(d.get("site") or ""),
        })
    return records


def _run_scrape(site: list[str], term: str, extra_kwargs: dict | None = None) -> list[dict]:
    """Single jobspy call with retry on transient errors."""
    kwargs = dict(
        site_name=site,
        search_term=term,
        location=LOCATION,
        results_wanted=RESULTS_PER_TERM,
        hours_old=HOURS_OLD,
        country_indeed="India",
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
    seen_urls: set[str] = set()
    results: list[dict] = []

    for term in SEARCH_TERMS:
        batch = _run_scrape(["linkedin"], term)
        new = [r for r in batch if r["job_url"] not in seen_urls]
        for r in new:
            seen_urls.add(r["job_url"])
        results.extend(new)
        logger.info("  LinkedIn [%s…] → %d", term[:50], len(new))
        time.sleep(5)

    logger.info("LinkedIn total: %d unique", len(results))
    return results


def _scrape_indeed() -> list[dict]:
    logger.info("=== Indeed India: starting ===")
    seen_urls: set[str] = set()
    results: list[dict] = []

    for term in SEARCH_TERMS:
        batch = _run_scrape(["indeed"], term)
        new = [r for r in batch if r["job_url"] not in seen_urls]
        for r in new:
            seen_urls.add(r["job_url"])
        results.extend(new)
        logger.info("  Indeed [%s…] → %d", term[:50], len(new))
        time.sleep(5)

    logger.info("Indeed total: %d unique", len(results))
    return results


def _scrape_glassdoor() -> list[dict]:
    """
    Glassdoor fix notes:
    - Must use country_indeed="India" (already in _run_scrape defaults)
    - Location MUST be "Bengaluru" NOT "Bangalore, India" — the Glassdoor
      geocoding autocomplete API rejects the latter with HTTP 400.
    - linkedin_fetch_recipient_url=False suppresses an extra outbound call
      that sometimes triggers rate-limits from CI IPs.
    """
    logger.info("=== Glassdoor India: starting ===")
    seen_urls: set[str] = set()
    results: list[dict] = []

    glassdoor_kwargs = {
        "location": "Bengaluru",          # ← KEY FIX: official name, not "Bangalore, India"
        "linkedin_fetch_recipient_url": False,
    }

    for term in SEARCH_TERMS:
        batch = _run_scrape(
            ["glassdoor"], term,
            extra_kwargs=glassdoor_kwargs,
        )
        new = [r for r in batch if r["job_url"] not in seen_urls]
        for r in new:
            seen_urls.add(r["job_url"])
        results.extend(new)
        logger.info("  Glassdoor [%s…] → %d", term[:50], len(new))
        time.sleep(6)

    logger.info("Glassdoor total: %d unique", len(results))
    return results


# ── NAUKRI IS INTENTIONALLY REMOVED ──────────────────────────────────────────
#
# Naukri returns HTTP 406 "recaptcha required" for ALL requests from GitHub
# Actions IP ranges. This is a permanent block — Naukri actively detects
# and challenges automated requests from cloud CI IP pools.
#
# WORKAROUNDS (pick one):
#   A) Self-hosted GitHub Actions runner on your home machine / VPS
#   B) Add a residential proxy (e.g. Webshare, Smartproxy) and pass
#      proxy_url=... to jobspy.scrape_jobs()
#   C) Manually check Naukri and paste good listings into the sheet
#
# ─────────────────────────────────────────────────────────────────────────────


# ── Main entry point ──────────────────────────────────────────────────────────

def gather_all_listings() -> list[dict]:
    """
    Scrape all working sources and return a single deduplicated list.
    Source order: LinkedIn → Indeed → Glassdoor
    (Naukri removed — recaptcha blocked from GitHub Actions)
    """
    all_results: list[dict] = []
    seen_urls: set[str] = set()

    sources = [
        ("LinkedIn",       _scrape_linkedin),
        ("Indeed",         _scrape_indeed),
        ("Glassdoor",      _scrape_glassdoor),
    ]

    source_counts: dict[str, int] = {}

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

        added = len(all_results) - before
        source_counts[name] = added
        logger.info("%s added %d unique listings", name, added)

        # Pause between sources to avoid simultaneous rate-limit hits
        time.sleep(8)

    logger.info(
        "gather_all_listings complete: %d unique total | %s",
        len(all_results),
        " ".join(f"{k}={v}" for k, v in source_counts.items()),
    )
    return all_results
