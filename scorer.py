# =============================================================================
# scorer.py
# =============================================================================
# Sends each raw listing to Groq (Llama 3) for AI scoring and structured
# data extraction.
#
# CHANGES FROM v1:
#   - Retry logic with exponential backoff on Groq 429 (rate limit)
#   - sleep(3) between calls to stay under 30 req/min free tier limit
#   - Better error logging (prints response body on failure)
#   - min_score filter now also checks for entry-level in AI response
# =============================================================================

import os
import json
import logging
import re
import time
import requests

from datetime import datetime
from config import GROQ_MODEL

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Groq free tier limits:
#   - 30 requests/minute
#   - 6000 tokens/minute
# With sleep(3) between calls we send max 20 req/min — safely under limit.
SLEEP_BETWEEN_CALLS = 3

# Retry settings for 429 rate limit responses
MAX_RETRIES    = 4
RETRY_WAIT_BASE = 10   # seconds — will be multiplied by attempt number

PROMPT = """Analyze this Indian job listing and return ONLY valid JSON, no other text.

Keys required:
{{"job_title":"string","company":"string","company_tier":"MNC or startup or mid-tier or unknown","walk_in_date":"YYYY-MM-DD or null","walk_in_time":"HH:MM-HH:MM or null","location_address":"string or null","contact":"string or null","legitimacy_score":1-10,"red_flags":[],"summary":"string","experience_required":"string or null","is_entry_level":true or false}}

Scoring guide:
  Score 9-10: known MNC, full address, corporate email, specific date/time
  Score 7-8:  recognizable company, has address + contact + date
  Score 5-6:  unknown company but has specific venue + contact + date, no scam signals
  Score 1-4:  missing address or contact, registration fee, guaranteed offer, fake salary claims

is_entry_level: set true if experience required is 0-1 years, fresher, junior, trainee, or not mentioned.
Set false if experience required is 2+ years or title has Senior/Lead/Manager/Principal.

Listing:
{listing_text}"""


def call_groq(prompt: str) -> str:
    """
    Call the Groq API with automatic retry on 429 rate limit.
    Raises on non-retryable errors or after all retries exhausted.
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Return only raw JSON, no markdown, no explanation."},
            {"role": "user",   "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens":  600,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization":  f"Bearer {key}",
                    "Content-Type":   "application/json",
                },
                json=payload,
                timeout=30,
            )

            # Rate limited — wait and retry
            if r.status_code == 429:
                wait = RETRY_WAIT_BASE * attempt
                logger.warning(
                    f"Groq 429 rate limit — waiting {wait}s "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            # Any other non-2xx — log body for debugging and raise
            if not r.ok:
                logger.error(
                    f"Groq error {r.status_code}: {r.text[:300]}"
                )
                r.raise_for_status()

            return r.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.warning(f"Groq timeout on attempt {attempt}/{MAX_RETRIES}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(5 * attempt)

        except requests.exceptions.RequestException as e:
            # 429 retries handled above; anything else re-raises immediately
            if attempt == MAX_RETRIES:
                raise
            logger.warning(f"Groq request error attempt {attempt}: {e}")
            time.sleep(5)

    raise Exception(f"Groq failed after {MAX_RETRIES} retries")


def score_listing(listing: dict) -> dict | None:
    """
    Score a single listing. Returns a scored dict or None if scoring fails
    or the response can't be parsed.
    """
    text = (
        f"TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {listing.get('job_url', '')}\n"
        f"DESCRIPTION: {listing.get('description', '')[:2000]}"
    )

    try:
        raw = call_groq(PROMPT.format(listing_text=text))

        # Strip markdown fences if model adds them despite instructions
        raw = re.sub(r"```json|```", "", raw).strip()

        # Extract the JSON object — find outermost { }
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            logger.error(f"No JSON object found in Groq response for '{listing.get('title')}'")
            return None

        d = json.loads(raw[start:end])

        return {
            "scraped_at":        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "source":            listing.get("source", ""),
            "url":               listing.get("job_url", ""),
            "job_title":         d.get("job_title",   listing.get("title", "")),
            "company":           d.get("company",     listing.get("company", "")),
            "company_tier":      d.get("company_tier", "unknown"),
            "walk_in_date":      d.get("walk_in_date"),
            "walk_in_time":      d.get("walk_in_time"),
            "location_address":  d.get("location_address"),
            "contact":           d.get("contact"),
            "legitimacy_score":  int(d.get("legitimacy_score", 1)),
            "experience_required": d.get("experience_required"),
            "is_entry_level":    bool(d.get("is_entry_level", True)),
            "red_flags":         d.get("red_flags", []),
            "summary":           d.get("summary", ""),
            "status":            "pending",
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for '{listing.get('title')}': {e}")
        return None
    except Exception as e:
        logger.error(f"Scoring error for '{listing.get('title')}': {e}")
        return None


def score_all(listings: list, min_score: int = 5) -> list:
    """
    Score all listings. Drops any that:
      - fail to parse
      - score below min_score
      - AI says is_entry_level = False
    """
    scored = []
    total  = len(listings)

    for i, listing in enumerate(listings, start=1):
        title = (listing.get("title") or "?")[:50]
        logger.info(f"Scoring {i}/{total}: {title}")

        result = score_listing(listing)

        if result is None:
            logger.info(f"  -> Skipped (parse/API error)")
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        if result["legitimacy_score"] < min_score:
            logger.info(
                f"  -> Dropped (legitimacy score {result['legitimacy_score']} "
                f"< min {min_score})"
            )
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        if not result.get("is_entry_level", True):
            logger.info(
                f"  -> Dropped (not entry level — "
                f"exp: {result.get('experience_required', 'unknown')})"
            )
            time.sleep(SLEEP_BETWEEN_CALLS)
            continue

        logger.info(
            f"  -> PASSED (score={result['legitimacy_score']}, "
            f"entry_level={result['is_entry_level']}, "
            f"exp={result.get('experience_required', 'not stated')})"
        )
        scored.append(result)
        time.sleep(SLEEP_BETWEEN_CALLS)

    logger.info(f"Scoring complete: {len(scored)}/{total} passed")
    return scored
