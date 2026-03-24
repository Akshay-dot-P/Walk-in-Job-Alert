# =============================================================================
# scorer.py — uses direct HTTP requests instead of groq SDK
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


def call_groq(prompt: str) -> str:
    """Call Groq API directly via HTTP — no SDK needed."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a structured data extractor. "
                    "You ONLY output valid JSON. No markdown. No explanation. "
                    "No preamble. Just raw JSON."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }

    response = requests.post(
        GROQ_API_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def score_listing(listing: dict) -> dict | None:
    listing_text = (
        f"SOURCE: {listing.get('source', '')}\n"
        f"JOB TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"URL: {listing.get('url', '')}\n"
        f"DESCRIPTION:\n{listing.get('description', '')[:2000]}"
    )

    prompt = SCORING_PROMPT.format(listing_text=listing_text)

    try:
        raw_text = call_groq(prompt)

        # Strip markdown fences if model added them
        raw_text = re.sub(r"```json\s*", "", raw_text)
        raw_text = re.sub(r"```\s*", "", raw_text)

        # Find JSON boundaries
        first_brace = raw_text.find("{")
        if first_brace > 0:
            raw_text = raw_text[first_brace:]
        last_brace = raw_text.rfind("}")
        if last_brace >= 0:
            raw_text = raw_text[: last_brace + 1]

        scored_data = json.loads(raw_text)

        result = {
            "scraped_at":        datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
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
        logger.error(f"JSON parse error for '{listing.get('title')}': {e}")
        return None
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        return None


def score_all(listings: list, min_score: int = 5) -> list:
    scored = []
    logger.info(f"Sending {len(listings)} listings to Groq for scoring...")

    for i, listing in enumerate(listings):
        logger.info(f"Scoring {i+1}/{len(listings)}: {listing.get('title', '?')[:50]}")
        result = score_listing(listing)

        if result is None:
            continue

        if result["legitimacy_score"] < min_score:
            logger.info(f"  -> Dropped (score {result['legitimacy_score']} < {min_score})")
            continue

        scored.append(result)
        time.sleep(2)  # stay within Groq free tier rate limit

    logger.info(f"Scoring complete: {len(scored)} listings passed threshold")
    return scored
