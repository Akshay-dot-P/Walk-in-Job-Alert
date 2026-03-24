# =============================================================================
# scorer.py
# =============================================================================
# This module takes raw listing text and asks an AI (Groq's Llama 3) to
# do two things simultaneously:
#   1. Extract structured fields (date, time, address, contact) from messy text
#   2. Score the listing's legitimacy from 1-10
#
# Why use AI here instead of regex?
# Indian job listing text is inconsistent. One listing says:
#   "Walk-In on 15th April 2025 from 10 AM to 5 PM at Prestige Tech Park"
# Another says:
#   "date-16/04/25, timing 10:00, venue-near metro station brigade road"
# Another says:
#   "venue: below-mentioned address contact hr dept"
# Regex would need 50+ patterns to handle all these variations.
# An LLM reads them all correctly with one instruction.
#
# We use Groq because their free tier gives 14,400 requests/day with
# Llama 3 — that's far more than we'll ever use. Claude API would cost
# money. OpenAI would too. Groq is the right choice for a free pipeline.
# =============================================================================

import os
import json
import logging
import re
from groq import Groq
from config import GROQ_MODEL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Initialize the Groq client once at module load time.
# The API key comes from the environment variable set in GitHub Actions.
# Never hardcode API keys in source code — they end up in git history forever.
# ---------------------------------------------------------------------------
client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))


# =============================================================================
# THE PROMPT TEMPLATE
# =============================================================================
# This is the most important piece of the scorer. The quality of AI output
# depends almost entirely on the clarity of your instructions.
#
# Key prompt engineering decisions made here:
#
# 1. "Return ONLY a valid JSON object, no other text" — without this instruction,
#    the model sometimes wraps output in ```json ``` code fences or adds
#    "Here is the extracted data:" before the JSON. That breaks JSON parsing.
#
# 2. Specific scoring criteria with numbers — vague instructions like
#    "score from 1 to 10" produce inconsistent results. Explicit criteria
#    like "9-10: known MNC with full address" anchors the model.
#
# 3. Listing the red flags explicitly — the model knows what to look for
#    and will call them out reliably rather than making up its own criteria.
#
# 4. Truncating to 2000 chars — LLMs have context windows, and Groq's free
#    tier limits tokens per minute. 2000 chars is enough for extraction.
# =============================================================================
SCORING_PROMPT = """You are an expert at analyzing Indian job market listings, 
especially in Bangalore/Bengaluru tech sector. 
Analyze the job listing below and return ONLY a valid JSON object — no markdown, 
no code fences, no preamble, no explanation. Just the raw JSON.
The JSON must have exactly these keys:
{{
  "job_title": "normalized title like 'Cloud Engineer' or 'SDE-2'",
  "company": "company name as written",
  "company_tier": "MNC" or "startup" or "mid-tier" or "unknown",
  "walk_in_date": "YYYY-MM-DD format, or null if not found",
  "walk_in_time": "HH:MM-HH:MM like 10:00-17:00, or null if not found",
  "location_address": "full address string or venue name in Bangalore, or null",
  "contact": "email or phone number, or null",
  "legitimacy_score": integer between 1 and 10,
  "red_flags": ["array", "of", "warning strings"],
  "summary": "one sentence summary of this opportunity"
}}
SCORING RULES for legitimacy_score:
- 9-10: Verified known company (MNC, funded startup), full street address, corporate 
        email or company HR name, specific date and time, clear job description
- 7-8:  Recognizable company name, has a venue address, has contact info, 
        specific date, role description is coherent
- 5-6:  Unknown company but listing has specific venue + contact + date, 
        no suspicious language, could be a small legitimate company
- 3-4:  Missing key details (no address OR no contact), or unknown company,
        but no outright scam signals
- 1-2:  One or more of these: asks for registration fee / certification fee,
        personal Gmail or @yahoo.com contact from unknown company,
        "guaranteed offer letter", "no rounds just direct hire 100%",
        salary claims wildly above market (>40 LPA for freshers),
        urgent language with no verifiable details
KNOWN RED FLAGS to detect and list in red_flags array:
- Registration fee or document fee mentioned
- "Guaranteed placement" or "100% joining" claims  
- Salary promise that is unrealistic (eg: 15-20 LPA for 0-1 year experience)
- Only personal mobile number, no email
- Gmail/Yahoo/Hotmail contact from an unknown company
- "Limited slots, register now" urgency tactics
- No specific venue address (just says "Bangalore" or "near metro")
- Multiple grammar errors suggesting non-professional origin
If red_flags is empty, return an empty array [].
For company_tier: MNC means large multinational (Infosys, Wipro, Accenture, IBM, 
Microsoft, Google, Amazon, Cisco, Oracle, Capgemini, etc). Startup means funded 
tech startup. mid-tier means Indian mid-size IT company.
JOB LISTING TO ANALYZE:
{listing_text}"""


# =============================================================================
# FUNCTION: Score a single listing
# =============================================================================
# Takes a raw listing dict (from sources.py) and returns an enriched dict
# with all the AI-extracted fields added to it.
# =============================================================================
def score_listing(listing: dict) -> dict | None:
    # Build the text we send to the AI by combining all fields we have.
    # More context = better extraction accuracy.
    listing_text = (
        f"SOURCE: {listing.get('source', '')}\n"
        f"JOB TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {listing.get('url', '')}\n"
        f"DESCRIPTION:\n{listing.get('description', '')[:2000]}"
    )

    # Format the prompt with the actual listing text.
    # We use .format() here because the prompt template uses {listing_text}.
    # The double braces {{ }} in the template become single braces { } after
    # .format() is called — that's Python's way to include literal braces
    # in a format string.
    prompt = SCORING_PROMPT.format(listing_text=listing_text)

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    # The system message sets the AI's overall behavior for the
                    # entire conversation. We reinforce the JSON-only instruction
                    # here too because models sometimes ignore it if only in the user message.
                    "content": (
                        "You are a structured data extractor. You ONLY output valid JSON. "
                        "No markdown. No explanation. No preamble. Just raw JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.1,
            # temperature controls randomness. 0.0 = fully deterministic (same
            # input always gives same output). We use 0.1 to allow slight
            # variation while keeping outputs consistent and structured.
            max_tokens=600,
            # 600 tokens is enough for our JSON response (~450 words).
            # Limiting tokens prevents the model from rambling and keeps
            # our Groq usage within the free tier limits.
        )

        # The response object has this structure:
        # response.choices[0].message.content = the text the AI generated
        raw_text = response.choices[0].message.content.strip()

        # -----------------------------------------------------------------------
        # JSON CLEANUP
        # Despite our instructions, the model occasionally wraps output in
        # ```json ... ``` markdown code fences. We strip those defensively.
        # We also handle the case where it adds a brief sentence before the JSON.
        # -----------------------------------------------------------------------

        # Remove ```json and ``` if present
        raw_text = re.sub(r"```json\s*", "", raw_text)
        raw_text = re.sub(r"```\s*", "", raw_text)

        # If the model added text before the JSON, find the first { and start there
        first_brace = raw_text.find("{")
        if first_brace > 0:
            raw_text = raw_text[first_brace:]

        # Similarly, find the last } and cut anything after it
        last_brace = raw_text.rfind("}")
        if last_brace >= 0:
            raw_text = raw_text[: last_brace + 1]

        # Parse the cleaned JSON string into a Python dict
        scored_data = json.loads(raw_text)

        # Merge the original listing fields with the AI-extracted fields.
        # If the AI extracted a better title, it overwrites the original.
        # We keep "url" and "source" from the original since the AI
        # doesn't generate these.
        result = {
            "scraped_at":        _now_str(),
            "source":            listing.get("source", ""),
            "url":               listing.get("url", ""),
            "job_title":         scored_data.get("job_title", listing.get("title", "")),
            "company":           scored_data.get("company", listing.get("company", "")),
            "company_tier":      scored_data.get("company_tier", "unknown"),
            "walk_in_date":      scored_data.get("walk_in_date"),
            "walk_in_time":      scored_data.get("walk_in_time"),
            "location_address":  scored_data.get("location_address"),
            "contact":           scored_data.get("contact"),
            "legitimacy_score":  int(scored_data.get("legitimacy_score", 1)),
            "red_flags":         scored_data.get("red_flags", []),
            "summary":           scored_data.get("summary", ""),
            "status":            "pending",
        }

        logger.info(
            f"  Scored: {result['company']} | {result['job_title']} "
            f"| score={result['legitimacy_score']} | date={result['walk_in_date']}"
        )
        return result

    except json.JSONDecodeError as e:
        # This happens when the AI returned text that isn't valid JSON.
        # We log the raw text so you can inspect it and improve the prompt.
        logger.error(f"JSON parse error for listing '{listing.get('title')}': {e}")
        logger.error(f"Raw AI response was: {raw_text[:300]}")
        return None

    except Exception as e:
        # Covers: Groq API errors (rate limit, network error, auth failure)
        logger.error(f"Groq API error: {e}")
        return None


# =============================================================================
# FUNCTION: Score all listings in a batch
# =============================================================================
# Scores every listing that passes the minimum threshold and returns
# only the ones with legitimacy_score >= min_score.
# =============================================================================
def score_all(listings: list, min_score: int = 5) -> list:
    scored = []

    logger.info(f"Sending {len(listings)} listings to Groq for scoring...")

    for i, listing in enumerate(listings):
        logger.info(f"Scoring {i+1}/{len(listings)}: {listing.get('title', '?')[:50]}")

        result = score_listing(listing)

        if result is None:
            continue  # AI failed on this one — skip it

        if result["legitimacy_score"] < min_score:
            logger.info(
                f"  -> Dropped (score {result['legitimacy_score']} < threshold {min_score})"
            )
            continue

        scored.append(result)

        # Rate limiting: Groq's free tier allows 30 requests per minute.
        # 2 seconds between calls = max 30/minute. We stay just within the limit.
        # Without this sleep, a batch of 20 listings could trigger rate limiting.
        import time
        time.sleep(2)

    logger.info(f"Scoring complete: {len(scored)} listings passed the threshold")
    return scored


# =============================================================================
# HELPER: Current UTC time as ISO string
# =============================================================================
def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# Import datetime here since we used it in _now_str above
from datetime import datetime
