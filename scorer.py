# =============================================================================
# scorer.py
# =============================================================================
# Sends listings to Groq (llama-3.1-8b-instant) for AI scoring.
#
# KEY CHANGE from original: BATCH scoring (5 listings per API call) instead
# of 1-per-call. This reduces API calls from ~45 to ~9 per run, eliminating
# the 429 rate-limit errors entirely.
#
# Rate-limit math (why this model, why this batch size):
#   Model limits  : 30 RPM, 14,400 RPD, 131,072 TPM (free tier)
#   Calls per run : 45 listings ÷ 5 per batch = 9 calls
#   Calls per day : 9 × 3 runs = 27  →  14,400 limit  (530× headroom)
#   Tokens per run: 9 × ~2,200 = ~20,000  →  131,072 limit  (6× headroom)
#   RPM at 6s gap : 9 calls over ~54s = ~10 RPM  →  30 RPM limit  (3× headroom)
#
# Output fields match SHEET_COLUMNS exactly:
#   scraped_at, job_title, company, company_tier, location_address,
#   contact, legitimacy_score, red_flags, source, url, status
# =============================================================================

import os
import json
import logging
import re
import time
import requests
from datetime import datetime, timezone

from config import GROQ_MODEL, KNOWN_MNCS, TARGET_ROLES

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── Batching config ───────────────────────────────────────────────────────────
BATCH_SIZE  = 5     # listings per API call
CALL_SLEEP  = 6     # seconds between batch calls → ~10 RPM (limit is 30)
MAX_RETRIES = 4
BACKOFF     = [10, 20, 45, 90]   # longer than original 2/4/8 → prevents cascade 429s

# ── Prompts ───────────────────────────────────────────────────────────────────
# The system prompt is minimal — just tells the model output format.
# All scoring logic lives in the user prompt so it is easy to update.
SYSTEM_PROMPT = "Return only raw JSON. No markdown fences, no preamble, no explanation."

# STRICT user prompt — the original was too lenient (AI/ML, SFMC, Design Engineer
# all scored 7-9). The irrelevant roles list forces the model to score those 1-4.
BATCH_USER_PROMPT = """\
Score these {n} Indian job listings for an entry-level cybersecurity job alert \
targeting Bangalore, India. Return a JSON array of exactly {n} objects in the \
SAME ORDER as the input. Each object must have EXACTLY these keys:

  job_title        : string  (cleaned title, fix typos)
  company          : string
  company_tier     : "MNC" | "mid-tier" | "startup" | "unknown"
  location_address : full office/venue address from description, or ""
  contact          : phone or email from description, or ""
  legitimacy_score : integer 1-10  (see rubric below)
  red_flags        : list of strings, empty list [] if none

SCORING RUBRIC — read carefully before scoring:

RELEVANT roles → score 6-10 ONLY for these families:
  • SOC / Security Operations / SIEM / Incident Response
  • AppSec / SAST / DAST / DevSecOps / Secure Code Review
  • VAPT / Penetration Testing / Ethical Hacking / Red Team / Bug Bounty
  • Vulnerability Management / Threat Assessment / CVE Analysis
  • GRC / Compliance / ISO 27001 / NIST / PCI-DSS / Data Privacy / DPO
  • IT Audit / IS Audit / Internal Audit / ITGC
  • Risk Analyst (Operational / Credit / Market / Enterprise / BFSI)
  • Fraud Analyst / AML / KYC / Anti-Money Laundering / Financial Crime
  • Network Security / Cloud Security / IAM / DLP / Zero Trust / Firewall
  • Threat Intelligence / CTI / Threat Hunting / DFIR / Forensics
  • Cybersecurity Intern / InfoSec Trainee / Security Fresher / SOC L1

Within those families:
  9-10 : Entry-level (<2yr exp), Bangalore, clear company, good contact/address info
  7-8  : Right role, Bangalore, but experience slightly higher or info partially missing
  6    : Right role, but outside Bangalore OR experience requirement borderline

IRRELEVANT roles → always score 1-4, no exceptions:
  • Software Engineer / Developer / SDE / Programmer / Full-Stack
  • Data Scientist / ML Engineer / AI Researcher / AI/ML anything
  • Marketing / Sales / Business Development / HR / Finance / Accounts
  • CRM / ERP / Salesforce / SAP / ServiceNow Consultant
  • Regulatory Affairs (pharma / medical device — NOT cybersecurity)
  • Design Engineer / Manufacturing / Supply Chain / Logistics
  • Delivery Operations / Customer Success / Account Management
  • Senior roles requiring 3+ years OR "lead" / "manager" / "director" / "head of"

RED FLAG signals (add to red_flags list when found):
  registration fee, security deposit, pay to join, commission only,
  guaranteed offer, unlimited income, whatsapp interview only,
  immediate joiner pressure, no salary mentioned, MLM / network marketing

Listings (JSON array):
{listings_json}"""


# ── Groq API call (single batch) ─────────────────────────────────────────────

def _call_groq_batch(batch: list[dict]) -> list[dict] | None:
    """
    Send one batch of listings to Groq and return parsed results.
    Returns None on unrecoverable failure — caller handles fallback.
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set in environment")

    # Build a compact representation of each listing to minimise tokens.
    # We truncate descriptions to 500 chars — enough for the model to score
    # the role family but avoids blowing the token budget on verbose JDs.
    listings_input = [
        {
            "index":       i,
            "title":       l.get("title", ""),
            "company":     l.get("company", ""),
            "location":    l.get("location", ""),
            "description": (l.get("description", "") or "")[:500],
        }
        for i, l in enumerate(batch)
    ]

    user_content = BATCH_USER_PROMPT.format(
        n=len(batch),
        listings_json=json.dumps(listings_input, ensure_ascii=False),
    )

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model":       GROQ_MODEL,
        "temperature": 0,           # deterministic scoring
        "max_tokens":  900,         # ~180 tokens per listing × 5 = 900 max
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)

            if r.status_code == 429:
                wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                logger.warning("Groq 429 — waiting %ds (attempt %d/%d)", wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
                continue

            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()

            # Strip accidental markdown fences the model sometimes adds
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

            parsed = json.loads(raw)

            # Model should return a list; sometimes wraps it in an object
            if isinstance(parsed, list):
                return parsed
            for v in parsed.values():
                if isinstance(v, list):
                    return v

            logger.error("Groq returned unexpected JSON shape: %s", raw[:200])
            return None

        except json.JSONDecodeError as exc:
            logger.warning("Groq JSON parse error (attempt %d): %s", attempt + 1, exc)
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
        except requests.exceptions.Timeout:
            logger.warning("Groq timeout (attempt %d)", attempt + 1)
            time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
        except requests.exceptions.HTTPError as exc:
            logger.error("Groq HTTP error: %s", exc)
            return None

    logger.error("Groq batch failed after %d attempts", MAX_RETRIES)
    return None


# ── Keyword fallback (when Groq is completely unreachable) ────────────────────

def _keyword_fallback_score(listing: dict) -> int:
    """
    Emergency fallback used only when Groq fails all retries.
    Counts TARGET_ROLES keyword hits in the title only.
    Capped at 7 (no AI confirmation → can't be fully trusted).
    """
    title = (listing.get("title", "") or "").lower()
    hits  = sum(1 for role in TARGET_ROLES if role in title)
    return min(5 + hits, 7)


def _resolve_tier(company: str, ai_tier: str) -> str:
    """Override AI tier to MNC if company name appears in our known list."""
    c = (company or "").lower()
    if any(mnc in c for mnc in KNOWN_MNCS):
        return "MNC"
    # Normalise AI output capitalisation
    tier_map = {"mnc": "MNC", "mid-tier": "mid-tier", "startup": "startup"}
    return tier_map.get((ai_tier or "").lower(), "unknown")


# ── Main exported function (called by scanner.py) ─────────────────────────────

def score_all(listings: list[dict], min_score: int = 5) -> list[dict]:
    """
    Score all listings in batches of BATCH_SIZE.
    Returns only those that pass min_score, with all SHEET_COLUMNS fields filled.
    Function signature unchanged from original — scanner.py calls it the same way.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    passed  = []
    total   = len(listings)

    for batch_start in range(0, total, BATCH_SIZE):
        batch     = listings[batch_start : batch_start + BATCH_SIZE]
        batch_end = batch_start + len(batch)

        logger.info("Scoring %d–%d / %d …", batch_start + 1, batch_end, total)
        ai_results = _call_groq_batch(batch)

        for i, listing in enumerate(batch):
            global_idx = batch_start + i + 1
            title      = listing.get("title", "Untitled")
            company    = listing.get("company", "")

            if ai_results and i < len(ai_results):
                # ── AI path (normal) ────────────────────────────────────────
                r     = ai_results[i]
                score = int(r.get("legitimacy_score", 0))
                tier  = _resolve_tier(company, r.get("company_tier", "unknown"))
                addr  = r.get("location_address", "") or ""
                cont  = r.get("contact", "") or ""
                flags = r.get("red_flags", [])
                if isinstance(flags, list):
                    flags = ", ".join(str(f) for f in flags)
            else:
                # ── Keyword fallback (Groq completely unreachable) ───────────
                score = _keyword_fallback_score(listing)
                tier  = _resolve_tier(company, "unknown")
                addr  = listing.get("location", "")
                cont  = ""
                flags = "AI scoring unavailable"
                logger.warning("  [%d/%d] fallback score=%d  %s", global_idx, total, score, title[:60])

            icon = "✓" if score >= min_score else "✗"
            logger.info("  [%d/%d] %s score=%-2d  %s", global_idx, total, icon, score, title[:60])

            if score < min_score:
                continue

            # Build output dict with exactly the keys in SHEET_COLUMNS
            passed.append({
                "scraped_at":       now_str,
                "job_title":        title,
                "company":          company,
                "company_tier":     tier,
                "location_address": addr or listing.get("location", ""),
                "contact":          cont,
                "legitimacy_score": score,
                "red_flags":        flags,
                "source":           listing.get("source", ""),
                "url":              listing.get("job_url", ""),   # note: raw field is job_url
                "status":           "New",
            })

        # Sleep between batch calls to stay safely under 30 RPM
        if batch_end < total:
            time.sleep(CALL_SLEEP)

    logger.info("Scoring done: %d / %d passed (score ≥ %d)", len(passed), total, min_score)
    return passed
