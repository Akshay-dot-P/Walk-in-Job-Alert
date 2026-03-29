import os
import json
import re
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# NOTE: No {text} placeholder — we build the prompt by concatenation, NOT .format()
# .format() breaks when job descriptions contain literal { } characters → 400 error
SYSTEM_PROMPT = "You output only raw valid JSON. No markdown. No explanation. No preamble."

USER_PROMPT_PREFIX = """You are an expert analyst of the Indian job market,
specializing in Bangalore tech and cybersecurity hiring for freshers and interns.

Analyze the listing below and return ONLY a valid JSON object. No markdown, no code fences.

Required JSON keys:
{
  "job_title": "normalized title e.g. SOC Analyst, GRC Intern, Security Analyst",
  "company": "company name or empty string",
  "company_tier": "MNC" or "startup" or "mid-tier" or "unknown",
  "walk_in_date": "YYYY-MM-DD or null",
  "walk_in_time": "HH:MM-HH:MM or null",
  "location_address": "specific Bangalore address/venue or null",
  "contact": "email or phone or null",
  "legitimacy_score": integer 1-10,
  "red_flags": [],
  "summary": "one sentence",
  "is_walk_in": true or false,
  "is_intern": true or false,
  "is_fresher_eligible": true or false,
  "experience_required": "e.g. 0-1 years, fresher, or null",
  "work_mode": "remote" or "hybrid" or "onsite" or "unknown",
  "stipend_or_salary": "e.g. 15000/month, 3-5 LPA, or null",
  "application_deadline": "YYYY-MM-DD or null"
}

SCORING:
9-10: Known company, full address, corporate contact, specific date+time, clear JD
7-8:  Recognizable company, has venue+contact+date, coherent description
5-6:  Unknown company but specific venue+contact+date, no scam signals
3-4:  Missing address OR contact, no scam signals
1-2:  Registration fee, guaranteed placement, fake salary, Gmail from unknown company,
      no address, OR this is a news article/login page/not a real job post

RED FLAGS: registration/document fee, guaranteed placement, unrealistic salary,
personal Gmail from unknown company, no specific venue, urgency pressure tactics.

Set is_intern=true if: intern, internship, stipend, trainee program, apprentice, fellowship.
Set is_fresher_eligible=true if: fresher, 0 years, 0-2 years, entry level, graduate, intern.

LISTING:
"""


# =============================================================================
# PRE-FILTER — runs BEFORE calling Groq
# Drops obviously irrelevant listings so we don't waste API quota.
# 2674 listings × 2sec = 89 minutes. With pre-filter → ~200 listings × 2sec = 7 min.
# =============================================================================

# Titles to hard-reject — clearly not cybersec/GRC/risk roles
REJECT_TITLE_KEYWORDS = [
    "food", "chef", "cook", "restaurant", "hospitality",
    "marketing operations", "lead generation", "accounts receivable",
    "accounts payable", "payroll", "legal entity", "finance controller",
    "head of finance", "global finance", "monetization", "shipping",
    "log in or sign up", "sign up", "linkedin india",
    "global people support", "hr operations", "talent acquisition",
    "recruiter", "recruitment consultant", "staffing",
    "sales executive", "business development", "bdm",
    "content writer", "seo specialist", "social media",
    "graphic designer", "ui designer", "ux designer",
    "mechanical engineer", "civil engineer", "electrical engineer",
    "chemical engineer", "biomedical", "pharmaceutical",
    "customer support", "customer service", "call center",
    "voice process", "bpo", "telecaller",
    "supply chain", "logistics", "warehouse",
    "teacher", "professor", "lecturer", "education",
    "medical", "nurse", "doctor", "healthcare",
    "accountant", "ca ", "chartered accountant", "finance analyst",
    "financial analyst", "banking", "loan officer",
]

def text_contains_any(text: str, keywords: list) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def is_relevant_listing(listing: dict) -> bool:
    """
    Pre-filter: returns True only if listing is plausibly a cybersec/GRC/risk/intern role.
    This runs BEFORE Groq to avoid wasting API calls on irrelevant listings.
    """
    title = listing.get("title", "").lower()
    desc  = listing.get("description", "").lower()
    combined = f"{title} {desc}"

    # Hard reject obvious garbage
    if text_contains_any(title, REJECT_TITLE_KEYWORDS):
        return False

    # Must contain at least one target role keyword
    if text_contains_any(combined, TARGET_ROLES):
        return True

    # OR must be an intern post in a relevant field
    if text_contains_any(combined, INTERN_KEYWORDS):
        if text_contains_any(combined, ["security", "cyber", "risk", "compliance",
                                         "grc", "audit", "fraud", "kyc", "aml",
                                         "privacy", "cloud", "network"]):
            return True

    return False


def pre_filter(listings: list) -> list:
    """Drop irrelevant listings before scoring. Logs what was kept vs dropped."""
    kept    = [l for l in listings if is_relevant_listing(l)]
    dropped = len(listings) - len(kept)
    logger.info(
        "Pre-filter: %d/%d kept, %d dropped as irrelevant",
        len(kept), len(listings), dropped
    )
    return kept


# =============================================================================
# GROQ CALL — uses string concatenation, NOT .format()
# This is the fix for the 400 Bad Request error.
# .format() breaks when descriptions contain { or } characters.
# =============================================================================

def call_groq(listing_text: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    # Build prompt by concatenation — safe against { } in descriptions
    full_prompt = USER_PROMPT_PREFIX + listing_text

    r = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": full_prompt},
            ],
            "temperature": 0.1,
            "max_tokens":  800,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def score_listing(listing: dict) -> dict | None:
    # Build the listing text safely — no .format(), just concatenation
    listing_text = (
        "SOURCE: "      + listing.get("source", "")                         + "\n"
        "TITLE: "       + listing.get("title", "")                          + "\n"
        "COMPANY: "     + listing.get("company", "")                        + "\n"
        "LOCATION: "    + listing.get("location", "")                       + "\n"
        "DATE POSTED: " + listing.get("date_posted", "")                    + "\n"
        "URL: "         + (listing.get("job_url") or listing.get("url","")) + "\n"
        "DESCRIPTION:\n"+ listing.get("description", "")[:1800]
    )

    try:
        raw = call_groq(listing_text)

        # Strip accidental markdown fences
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*",     "", raw)

        # Trim to valid JSON object
        start = raw.find("{")
        end   = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object in response")
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

        tags = []
        if result["is_intern"]:           tags.append("INTERN")
        if result["is_walk_in"]:          tags.append("WALK-IN")
        if result["is_fresher_eligible"]: tags.append("FRESHER-OK")

        logger.info(
            "  [%s] %s @ %s | score=%d | %s",
            "/".join(tags) or "regular",
            result["job_title"][:35],
            result["company"][:20],
            result["legitimacy_score"],
            result["experience_required"] or "exp?",
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON parse error for '%s': %s", listing.get("title", "?"), e)
        return None
    except Exception as e:
        logger.error("Scoring error for '%s': %s", listing.get("title", "?"), e)
        return None


def score_all(listings: list, min_score: int = 4) -> list:
    # PRE-FILTER first — drop irrelevant listings before hitting Groq
    relevant = pre_filter(listings)

    if not relevant:
        logger.info("No relevant listings after pre-filter")
        return []

    scored = []
    logger.info("Scoring %d relevant listings via Groq...", len(relevant))

    for i, listing in enumerate(relevant):
        logger.info(
            "Scoring %d/%d: %s",
            i + 1, len(relevant), listing.get("title", "?")[:55]
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

    logger.info("Scoring complete: %d/%d passed", len(scored), len(relevant))
    return scored
