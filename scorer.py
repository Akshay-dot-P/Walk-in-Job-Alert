# =============================================================================
# scorer.py
# =============================================================================
# Sends listings to Groq (Llama 3) for AI scoring and structured extraction.
#
# KEY FIX — WHY THE ORIGINAL FAILED:
#   The old code called Groq once per listing with no pre-call delay.
#   75 listings × ~500 tokens = ~37,500 tokens fired in ~5 seconds.
#   Groq free tier caps at 6,000 tokens/minute AND 30 requests/minute.
#   Both limits were hit simultaneously on listing #2, causing 74/75 failures.
#
#   The sleep(3) in the old code only ran AFTER the result was evaluated,
#   not BEFORE the next Groq call — so it never protected the burst.
#
# THE FIX — BATCH SCORING:
#   Instead of 1 Groq call per listing, we send BATCH_SIZE listings per call.
#   With BATCH_SIZE=5:
#     - 75 listings → 15 calls  (well under 30 RPM)
#     - ~2,500 tokens per call  (well under 6,000 TPM)
#     - SLEEP_BETWEEN_BATCHES=12s → max 5 calls/min → max 3,000 TPM
#   This is the most token-efficient approach for Groq's free tier.
#
# GROQ FREE TIER LIMITS (as of 2025-2026):
#   - 30 requests/minute
#   - 6,000 tokens/minute
#   - 500 requests/day (on some models)
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

# ---------------------------------------------------------------------------
# Batching + rate limit config
#
# BATCH_SIZE: how many listings to score in one Groq call.
#   - 5 is the sweet spot: enough context per call, short enough to stay
#     well under 6,000 TPM even with verbose descriptions.
#   - Reduce to 3 if you see token limit errors.
#
# SLEEP_BETWEEN_BATCHES: seconds to wait between batch calls.
#   - 15s → max 4 calls/min → ~2,000 TPM at 5 listings/batch.
#   - Safe headroom below both the 30 RPM and 6,000 TPM limits.
#
# MAX_RETRIES / RETRY_WAIT_BASE: exponential backoff on 429.
#   - Wait = RETRY_WAIT_BASE × attempt_number (10s, 20s, 30s, 40s).
# ---------------------------------------------------------------------------
BATCH_SIZE            = 5
SLEEP_BETWEEN_BATCHES = 15   # seconds
MAX_RETRIES           = 4
RETRY_WAIT_BASE       = 10   # seconds

# ---------------------------------------------------------------------------
# Prompt — asks the model to score a numbered list of listings in one shot.
# The model returns a JSON array, one object per listing, in the same order.
# ---------------------------------------------------------------------------
BATCH_PROMPT = """You are a job listing analyser for Indian walk-in interview alerts.

Analyse the {n} job listings below and return ONLY a JSON array with exactly {n} objects — one per listing, in the same order.

Each object must have these keys:
{{
  "job_title": "string",
  "company": "string",
  "company_tier": "MNC or startup or mid-tier or unknown",
  "walk_in_date": "YYYY-MM-DD or null",
  "walk_in_time": "HH:MM-HH:MM or null",
  "location_address": "string or null",
  "contact": "string or null",
  "legitimacy_score": <integer 1-10>,
  "red_flags": [],
  "summary": "string",
  "experience_required": "string or null",
  "is_entry_level": <true or false>
}}

Scoring guide:
  9-10: known MNC, full address, corporate email, specific date+time
  7-8 : recognizable company, has address + contact + date
  5-6 : unknown company but specific venue + contact + date, no scam signals
  1-4 : missing address or contact, registration fee, guaranteed offer, fake salary

is_entry_level: true if 0-1 years experience, fresher, junior, trainee, or not mentioned.
                false if 2+ years required, or title has Senior/Lead/Manager/Principal.

Return ONLY the raw JSON array. No markdown, no explanation, no extra keys.

LISTINGS:
{listings_block}"""


def _format_listing(index: int, listing: dict) -> str:
    """Format one listing as a numbered text block for the batch prompt."""
    return (
        f"[{index}]\n"
        f"TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {listing.get('job_url', '')}\n"
        f"DESCRIPTION: {(listing.get('description') or '')[:800]}\n"
        # 800 chars/listing × 5 listings = 4,000 chars ≈ ~1,000 tokens of content
        # plus prompt overhead ≈ ~1,500 tokens total per batch — well under 6k TPM
    )


def call_groq(prompt: str) -> str:
    """
    Call the Groq API with automatic exponential-backoff retry on 429.
    Raises on non-retryable errors or after all retries are exhausted.
    """
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Return only raw JSON with no markdown fences and no explanation.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,  # enough for 5 objects × ~200 tokens each
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(
                GROQ_API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type":  "application/json",
                },
                json=payload,
                timeout=60,
            )

            if r.status_code == 429:
                wait = RETRY_WAIT_BASE * attempt
                logger.warning(
                    f"Groq 429 rate limit — waiting {wait}s "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            if not r.ok:
                logger.error(f"Groq error {r.status_code}: {r.text[:300]}")
                r.raise_for_status()

            return r.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.warning(f"Groq timeout on attempt {attempt}/{MAX_RETRIES}")
            if attempt == MAX_RETRIES:
                raise
            time.sleep(5 * attempt)

        except requests.exceptions.RequestException as e:
            if attempt == MAX_RETRIES:
                raise
            logger.warning(f"Groq request error attempt {attempt}: {e}")
            time.sleep(5)

    raise Exception(f"Groq failed after {MAX_RETRIES} retries")


def _parse_batch_response(raw: str, batch: list[dict]) -> list[dict | None]:
    """
    Parse the JSON array returned by a batch Groq call.
    Returns a list of the same length as `batch`, with None for any entry
    that couldn't be parsed. Never raises — bad JSON becomes None entries.
    """
    # Strip markdown fences if the model adds them despite instructions
    raw = re.sub(r"```json|```", "", raw).strip()

    # Extract the outermost JSON array
    start = raw.find("[")
    end   = raw.rfind("]") + 1
    if start == -1 or end == 0:
        logger.error("Batch response: no JSON array found")
        return [None] * len(batch)

    try:
        items = json.loads(raw[start:end])
    except json.JSONDecodeError as e:
        logger.error(f"Batch JSON parse error: {e}")
        return [None] * len(batch)

    if not isinstance(items, list):
        logger.error(f"Batch response is not a list: {type(items)}")
        return [None] * len(batch)

    # Pad or trim to match batch length (model occasionally hallucinates count)
    while len(items) < len(batch):
        items.append(None)
    items = items[: len(batch)]

    return items


def _build_result(listing: dict, d: dict) -> dict:
    """Merge a raw listing with the AI-scored dict into the canonical format."""
    return {
        "scraped_at":          datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "source":              listing.get("source", ""),
        "url":                 listing.get("job_url", ""),
        "job_title":           d.get("job_title",   listing.get("title", "")),
        "company":             d.get("company",     listing.get("company", "")),
        "company_tier":        d.get("company_tier", "unknown"),
        "walk_in_date":        d.get("walk_in_date"),
        "walk_in_time":        d.get("walk_in_time"),
        "location_address":    d.get("location_address"),
        "contact":             d.get("contact"),
        "legitimacy_score":    int(d.get("legitimacy_score", 1)),
        "experience_required": d.get("experience_required"),
        "is_entry_level":      bool(d.get("is_entry_level", True)),
        "red_flags":           d.get("red_flags", []),
        "summary":             d.get("summary", ""),
        "status":              "pending",
    }


def score_all(listings: list, min_score: int = 5) -> list:
    """
    Score all listings using batch Groq calls.

    Batches of BATCH_SIZE listings are sent per call with SLEEP_BETWEEN_BATCHES
    seconds between calls to stay under Groq's free tier limits.

    Drops listings that:
      - fail to parse (API or JSON error)
      - score below min_score
      - AI flags as is_entry_level = False (senior/managerial roles)
    """
    scored = []
    total  = len(listings)

    # Split into batches
    batches = [
        listings[i : i + BATCH_SIZE]
        for i in range(0, total, BATCH_SIZE)
    ]

    logger.info(
        f"Scoring {total} listings in {len(batches)} batches "
        f"of up to {BATCH_SIZE} (sleep {SLEEP_BETWEEN_BATCHES}s between batches)"
    )

    for batch_num, batch in enumerate(batches, start=1):
        batch_indices_str = f"{(batch_num-1)*BATCH_SIZE+1}–{min(batch_num*BATCH_SIZE, total)}"
        logger.info(f"Batch {batch_num}/{len(batches)}: listings {batch_indices_str}")

        # Build the numbered listing block for the prompt
        listings_block = "\n\n".join(
            _format_listing(i + 1, listing)
            for i, listing in enumerate(batch)
        )
        prompt = BATCH_PROMPT.format(n=len(batch), listings_block=listings_block)

        # Call Groq with retry
        try:
            raw = call_groq(prompt)
        except Exception as exc:
            logger.error(f"Batch {batch_num} failed entirely: {exc}")
            # Sleep before next batch even on failure to avoid hammering on error
            if batch_num < len(batches):
                time.sleep(SLEEP_BETWEEN_BATCHES)
            continue

        # Parse the array response
        parsed_items = _parse_batch_response(raw, batch)

        # Evaluate each item in the batch
        for listing, item in zip(batch, parsed_items):
            title = (listing.get("title") or "?")[:50]

            if item is None:
                logger.info(f"  [{title}] → Skipped (parse error)")
                continue

            result = _build_result(listing, item)

            if result["legitimacy_score"] < min_score:
                logger.info(
                    f"  [{title}] → Dropped "
                    f"(score {result['legitimacy_score']} < min {min_score})"
                )
                continue

            if not result.get("is_entry_level", True):
                logger.info(
                    f"  [{title}] → Dropped "
                    f"(not entry level, exp: {result.get('experience_required', 'unknown')})"
                )
                continue

            logger.info(
                f"  [{title}] → PASSED "
                f"(score={result['legitimacy_score']}, "
                f"exp={result.get('experience_required', 'not stated')})"
            )
            scored.append(result)

        # Respect rate limits between batches
        if batch_num < len(batches):
            logger.info(f"Sleeping {SLEEP_BETWEEN_BATCHES}s before next batch...")
            time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(f"Scoring complete: {len(scored)}/{total} passed")
    return scored
