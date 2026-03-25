# =============================================================================
# scorer.py
# =============================================================================
# Sends each raw listing to Groq (free Llama 3) for AI scoring/extraction.
# =============================================================================

import os
import json
import logging
import re
import time
import requests
from datetime import datetime, timezone

# Import the keyword lists from config so the AI prompt stays in sync with
# whatever you add to config.py — no need to ever edit this file for that.
from config import (
    GROQ_MODEL,
    ENTRY_LEVEL_BOOST_KEYWORDS,
    EXPERIENCE_MISMATCH_KEYWORDS,
    RED_FLAG_KEYWORDS,
    KNOWN_MNCS,
)

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = "Return only raw JSON. No markdown fences, no preamble, no explanation."


def _build_user_prompt() -> str:
    """
    Build the scoring prompt dynamically from config keyword lists.

    Why dynamic and not a static string? Because every time you add a keyword
    to ENTRY_LEVEL_BOOST_KEYWORDS or RED_FLAG_KEYWORDS in config.py, the AI
    automatically learns about it on the next run — no need to come back here.
    The config is the single source of truth; this function just formats it
    into natural language the model can follow.
    """
    boost_kws    = ", ".join(ENTRY_LEVEL_BOOST_KEYWORDS)
    mismatch_kws = ", ".join(EXPERIENCE_MISMATCH_KEYWORDS)
    red_flag_kws = ", ".join(RED_FLAG_KEYWORDS)
    mncs         = ", ".join(KNOWN_MNCS)

    return f"""\
You are scoring an Indian cybersecurity job listing for a CompTIA Sec+ certified
candidate with 0 years of experience based in Bangalore. Score BOTH legitimacy
AND whether this role is realistically reachable for that profile.

Return ONLY valid JSON with these exact keys:

{{
  "job_title": "string",
  "company": "string",
  "company_tier": "MNC or startup or mid-tier or unknown",
  "location_address": "full venue address or null",
  "contact": "email or phone or null",
  "legitimacy_score": <integer 1-10>,
  "fit_for_fresher": <true or false>,
  "reasoning": "one sentence explaining the score and fit decision",
  "red_flags": ["list", "of", "strings"]
}}

--- KNOWN MNCs (presence of these boosts company trust) ---
{mncs}

--- BOOST legitimacy_score AND set fit_for_fresher=true when listing contains ---
{boost_kws}

--- LOWER legitimacy_score AND set fit_for_fresher=false when listing contains ---
{mismatch_kws}

--- RED FLAGS — each one found lowers score by 1-2 points ---
{red_flag_kws}

Scoring guide:
  9-10: Known MNC, full address, corporate email, fresher/entry-level explicitly welcomed
  7-8 : Recognizable company, has contact + location, no experience mismatch
  5-6 : Unknown company, has venue + contact, no obvious scam signals
  1-4 : Missing address OR contact, any registration/processing fee, guaranteed offer,
        fake salary, OR requires 3+ years experience (not reachable for 0-exp candidate)

Listing:
{{listing_text}}"""


# Build once at module load. Every call to score_listing() reuses this string,
# which avoids re-joining the keyword lists on every single Groq request.
USER_PROMPT = _build_user_prompt()


def _call_groq(prompt: str, max_retries: int = 3) -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise ValueError("GROQ_API_KEY not set")

    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        # Raised from 600 to 800 to give the model room for the longer prompt
        # and the new reasoning field without truncating mid-JSON.
        "max_tokens": 800,
    }

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
            if r.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"Groq 429 rate-limit — waiting {wait}s (attempt {attempt})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.Timeout:
            logger.warning(f"Groq timeout (attempt {attempt})")
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
        except requests.exceptions.HTTPError as e:
            logger.error(f"Groq HTTP error: {e}")
            raise

    raise RuntimeError(f"Groq failed after {max_retries} attempts")


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in response: {raw[:200]}")
    return json.loads(cleaned[start:end + 1])


def score_listing(listing: dict) -> dict | None:
    job_url = listing.get("job_url") or ""
    text = (
        f"TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {job_url}\n"
        f"DESCRIPTION: {listing.get('description', '')[:2000]}"
    )
    try:
        raw = _call_groq(USER_PROMPT.format(listing_text=text))
        d = _parse_json(raw)
        return {
            "scraped_at":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source":           listing.get("source", ""),
            "url":              job_url,
            "job_title":        d.get("job_title") or listing.get("title", ""),
            "company":          d.get("company") or listing.get("company", ""),
            "company_tier":     d.get("company_tier", "unknown"),
            "location_address": d.get("location_address"),
            "contact":          d.get("contact"),
            "legitimacy_score": int(d.get("legitimacy_score", 1)),
            # Stored as "Yes"/"No" string so Google Sheets can filter on it
            # directly without any formula — just use the dropdown filter.
            "fit_for_fresher":  "Yes" if d.get("fit_for_fresher") else "No",
            # One sentence from the AI — shown in Telegram so you know why it
            # scored the way it did without having to open the sheet.
            "reasoning":        d.get("reasoning", ""),
            "red_flags":        d.get("red_flags", []),
            "status":           "pending",
        }
    except Exception as exc:
        logger.error(f"Scoring failed for '{listing.get('title', '?')}': {exc}")
        return None


def score_all(listings: list[dict], min_score: int = 5) -> list[dict]:
    scored = []
    total = len(listings)
    for i, listing in enumerate(listings, 1):
        logger.info(f"Scoring {i}/{total}: {(listing.get('title') or '?')[:50]}")
        result = score_listing(listing)
        if result is None:
            time.sleep(2)
            continue
        score = result["legitimacy_score"]
        fit   = result["fit_for_fresher"]
        if score < min_score:
            logger.info(f"  → Dropped (score {score} < {min_score})")
        else:
            logger.info(f"  → Kept (score {score}, fresher fit: {fit})")
            scored.append(result)
        time.sleep(2)   # 2s between calls = max 30/min (Groq free tier)
    logger.info(f"Scoring done: {len(scored)}/{total} passed")
    return scored
