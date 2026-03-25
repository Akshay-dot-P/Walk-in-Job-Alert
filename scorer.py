# =============================================================================
# scorer.py
# =============================================================================
# Sends each raw listing to Groq (free Llama 3 API) for AI scoring.
# The AI extracts structured walk-in details and assigns a legitimacy score.
#
# KEY FIX: sources.py stores the URL under "job_url". This file previously
# used listing.get("url") which always returned None. Fixed to "job_url".
#
# Rate limiting: Groq free tier allows 30 req/min and 6000 tokens/min.
# We sleep 2s between calls (30 req/min max = one every 2s). For large
# batches, exponential backoff handles 429s gracefully.
# =============================================================================

import os
import json
import logging
import re
import time
import requests
from datetime import datetime, timezone

from config import GROQ_MODEL

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = "Return only raw JSON. No markdown fences, no preamble, no explanation."

USER_PROMPT = """\
Analyze this Indian job listing and return ONLY valid JSON with these exact keys:

{{
  "job_title": "string",
  "company": "string",
  "company_tier": "MNC or startup or mid-tier or unknown",
  "walk_in_date": "YYYY-MM-DD or null",
  "walk_in_time": "HH:MM-HH:MM or null",
  "location_address": "full venue address string or null",
  "contact": "email or phone or null",
  "legitimacy_score": <integer 1-10>,
  "red_flags": ["list", "of", "strings"],
  "summary": "2-sentence plain-text summary"
}}

Target roles (flag as relevant if listing matches any):
  Security: application security, appsec, security analyst, SOC analyst, infosec,
            cybersecurity, social engineering, VAPT, threat analyst, vulnerability analyst
  GRC: GRC analyst, compliance analyst, IT audit, regulatory compliance, policy analyst
  Risk: risk analyst, operational risk, credit risk, market risk, enterprise risk
  Fraud / ORC: fraud analyst, AML analyst, anti-money laundering, transaction monitoring,
               organized retail crime, loss prevention, financial crimes
  Intern / Entry-level: intern, internship, trainee, fresher, graduate trainee,
                        junior analyst, entry level

Scoring guide:
  9-10: Known MNC, full street address, corporate email, specific date+time
  7-8 : Recognizable company, has address + contact + date
  5-6 : Unknown company but has specific venue + contact + date, no scam signals
  1-4 : Missing address OR contact, registration fee, guaranteed offer, fake salary

Listing:
{listing_text}"""


def _call_groq(prompt: str, max_retries: int = 3) -> str:
    """
    Call the Groq API with exponential backoff on rate-limit (429) errors.
    Raises on unrecoverable errors.
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set in environment")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                GROQ_API_URL, headers=headers, json=payload, timeout=30
            )
            if response.status_code == 429:
                wait = 2 ** attempt  # 2s, 4s, 8s
                logger.warning(f"Groq rate-limited (429). Waiting {wait}s before retry {attempt}/{max_retries}")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.Timeout:
            logger.warning(f"Groq request timed out (attempt {attempt}/{max_retries})")
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            logger.error(f"Groq HTTP error: {e} — {response.text[:300]}")
            raise

    raise RuntimeError(f"Groq call failed after {max_retries} attempts")


def _parse_groq_response(raw: str) -> dict:
    """
    Robustly parse a JSON response from Groq.
    Strips markdown fences if the model misbehaved, then extracts the first
    complete JSON object.
    """
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()

    # Find the outermost { ... } block
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in Groq response: {raw[:200]}")

    return json.loads(cleaned[start : end + 1])


def score_listing(listing: dict) -> dict | None:
    """
    Score a single raw listing dict. Returns an enriched dict on success,
    or None if scoring fails or the response is unparseable.

    BUG FIX: sources.py stores URL as "job_url", not "url".
    All internal references now use "job_url" consistently.
    """
    job_url = listing.get("job_url") or ""  # FIXED: was listing.get("url")

    listing_text = (
        f"TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {job_url}\n"
        f"DESCRIPTION: {listing.get('description', '')[:2000]}"
    )

    try:
        raw = _call_groq(USER_PROMPT.format(listing_text=listing_text))
        d = _parse_groq_response(raw)

        return {
            # Use timezone-aware UTC (datetime.utcnow() is deprecated in Python 3.12+)
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": listing.get("source", ""),
            # Store as "url" in the output dict to match SHEET_COLUMNS in config.py
            "url": job_url,
            "job_title": d.get("job_title") or listing.get("title", ""),
            "company": d.get("company") or listing.get("company", ""),
            "company_tier": d.get("company_tier", "unknown"),
            "walk_in_date": d.get("walk_in_date"),
            "walk_in_time": d.get("walk_in_time"),
            "location_address": d.get("location_address"),
            "contact": d.get("contact"),
            "legitimacy_score": int(d.get("legitimacy_score", 1)),
            "red_flags": d.get("red_flags", []),
            "summary": d.get("summary", ""),
            "status": "pending",
        }

    except Exception as exc:
        logger.error(
            f"Scoring failed for '{listing.get('title', '?')}' "
            f"({listing.get('company', '?')}): {exc}"
        )
        return None


def score_all(listings: list[dict], min_score: int = 5) -> list[dict]:
    """
    Score all listings sequentially, drop those below min_score, and return
    the passing list.

    Sleeps 2s between each Groq call to stay within the free tier's
    30 requests/minute rate limit.
    """
    scored = []
    total = len(listings)

    for i, listing in enumerate(listings, start=1):
        title_preview = (listing.get("title") or "?")[:50]
        logger.info(f"Scoring {i}/{total}: {title_preview}")

        result = score_listing(listing)

        if result is None:
            logger.warning(f"  → Skipped (scoring error)")
            time.sleep(2)
            continue

        score = result["legitimacy_score"]
        if score < min_score:
            logger.info(f"  → Dropped (score {score} < threshold {min_score})")
        else:
            logger.info(f"  → Kept (score {score})")
            scored.append(result)

        # Rate-limit guard: 2s between calls = max 30/min (Groq free tier limit)
        time.sleep(2)

    logger.info(f"Scoring complete: {len(scored)}/{total} passed min_score={min_score}")
    return scored
