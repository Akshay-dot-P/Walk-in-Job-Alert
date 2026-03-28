import os
import json
import re
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

PROMPT_TEMPLATE = """You are an expert analyst of the Indian job market,
specializing in Bangalore tech and cybersecurity hiring for freshers and interns.

Analyze the listing below and return ONLY a valid JSON object.
No markdown, no code fences, no explanation. Just the raw JSON.

Required JSON keys:
{{
  "job_title": "normalized title e.g. SOC Analyst, GRC Intern, Security Analyst",
  "company": "company name as written, or empty string",
  "company_tier": "MNC" or "startup" or "mid-tier" or "unknown",
  "walk_in_date": "YYYY-MM-DD or null",
  "walk_in_time": "HH:MM-HH:MM or null",
  "location_address": "specific Bangalore address or venue or null",
  "contact": "email or phone or null",
  "legitimacy_score": integer 1-10,
  "red_flags": ["list of warning strings, empty array if none"],
  "summary": "one sentence summary",
  "is_walk_in": true or false,
  "is_intern": true or false,
  "is_fresher_eligible": true or false,
  "experience_required": "e.g. 0-1 years, fresher, 0-2 years, or null",
  "work_mode": "remote" or "hybrid" or "onsite" or "unknown",
  "stipend_or_salary": "e.g. 15000/month, 3-5 LPA, negotiable, or null",
  "application_deadline": "YYYY-MM-DD or null"
}}

LEGITIMACY SCORING:
9-10: Known MNC/startup, full street address, corporate email, specific date+time, clear JD
7-8:  Recognizable company, has venue + contact + date, coherent description
5-6:  Unknown company but specific venue + contact + date, no scam signals
3-4:  Missing address OR contact, unknown company, but no outright scam
1-2:  Registration/document fee, "guaranteed placement", fake salary, personal Gmail
      from unknown company, no address, OR this is a news article/blog not a job post

RED FLAGS to detect:
- Any registration, document, or training fee required
- "Guaranteed offer letter" or "100% placement" claims
- Salary/stipend wildly above market for fresher (e.g. 20 LPA for 0 years)
- Only personal mobile number provided, no email
- Gmail/Yahoo/Hotmail from unknown company
- No specific venue ("just Bangalore" with no address)
- "Limited slots, register now" pressure tactics
- This is a news article, not a job listing (score 1-2)

INTERN DETECTION:
Set is_intern=true if any of these appear: intern, internship, stipend, trainee program,
apprentice, fellowship, graduate trainee, summer intern, 6-month program.

FRESHER DETECTION:
Set is_fresher_eligible=true if: fresher, 0 years, 0-1 year, 0-2 years, entry level,
graduate, no experience required, or intern role.

LISTING:
{text}"""


def call_groq(text: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    r = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role":    "system",
                    "content": "You output only raw valid JSON. No markdown. No explanation. No preamble.",
                },
                {
                    "role":    "user",
                    "content": PROMPT_TEMPLATE.format(text=text[:2500]),
                },
            ],
            "temperature": 0.1,
            "max_tokens":  800,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def score_listing(listing: dict) -> dict | None:
    text = (
        f"SOURCE: {listing.get('source', '')}\n"
        f"TITLE: {listing.get('title', '')}\n"
        f"COMPANY: {listing.get('company', '')}\n"
        f"LOCATION: {listing.get('location', '')}\n"
        f"DATE POSTED: {listing.get('date_posted', '')}\n"
        f"URL: {listing.get('job_url', '') or listing.get('url', '')}\n"
        f"DESCRIPTION:\n{listing.get('description', '')[:1800]}"
    )

    try:
        raw = call_groq(text)

        # Strip any accidental markdown fences
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)

        # Trim to valid JSON boundaries
        start = raw.find("{")
        end   = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON found in response")
        raw = raw[start : end + 1]

        d = json.loads(raw)

        result = {
            "scraped_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source":               listing.get("source", ""),
            "url":                  listing.get("job_url") or listing.get("url", ""),
            "job_title":            d.get("job_title")  or listing.get("title", ""),
            "company":              d.get("company")    or listing.get("company", ""),
            "company_tier":         d.get("company_tier", "unknown"),
            "walk_in_date":         d.get("walk_in_date"),
            "walk_in_time":         d.get("walk_in_time"),
            "location_address":     d.get("location_address"),
            "contact":              d.get("contact"),
            "legitimacy_score":     int(d.get("legitimacy_score", 1)),
            "red_flags":            d.get("red_flags", []),
            "summary":              d.get("summary", ""),
            "is_walk_in":           bool(d.get("is_walk_in", False)),
            "is_intern":            bool(d.get("is_intern", False)),
            "is_fresher_eligible":  bool(d.get("is_fresher_eligible", False)),
            "experience_required":  d.get("experience_required"),
            "work_mode":            d.get("work_mode", "unknown"),
            "stipend_or_salary":    d.get("stipend_or_salary"),
            "application_deadline": d.get("application_deadline"),
            "status":               "pending",
        }

        # Build a readable log tag
        tags = []
        if result["is_intern"]:          tags.append("INTERN")
        if result["is_walk_in"]:         tags.append("WALK-IN")
        if result["is_fresher_eligible"]: tags.append("FRESHER-OK")
        tag_str = " ".join(tags) or "regular"

        logger.info(
            "  [%s] %s @ %s | score=%d | %s | mode=%s",
            tag_str,
            result["job_title"][:35],
            result["company"][:25],
            result["legitimacy_score"],
            result["experience_required"] or "exp?",
            result["work_mode"],
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON parse error for '%s': %s", listing.get("title", "?"), e)
        return None
    except Exception as e:
        logger.error("Scoring error for '%s': %s", listing.get("title", "?"), e)
        return None


def score_all(listings: list, min_score: int = 4) -> list:
    scored = []
    logger.info("Scoring %d listings via Groq...", len(listings))

    for i, listing in enumerate(listings):
        logger.info(
            "Scoring %d/%d: %s",
            i + 1, len(listings), listing.get("title", "?")[:55]
        )
        result = score_listing(listing)

        if result is None:
            continue

        if result["legitimacy_score"] < min_score:
            logger.info(
                "  -> Dropped: score %d < %d",
                result["legitimacy_score"], min_score
            )
            continue

        scored.append(result)
        time.sleep(2)  # Groq free tier: 30 req/min

    logger.info("Scoring complete: %d/%d passed", len(scored), len(listings))
    return scored
