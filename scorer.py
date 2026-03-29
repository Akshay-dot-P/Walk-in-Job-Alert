import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You output only raw valid JSON. "
    "No markdown. No explanation. No preamble. No code fences."
)

USER_PROMPT_PREFIX = (
    "You are an expert analyst of the Indian job market, "
    "specializing in Bangalore cybersecurity hiring for freshers and interns.\n\n"
    "Analyze the listing below and return ONLY a valid JSON object.\n\n"
    "Required keys:\n"
    '{\n'
    '  "job_title": "normalized title",\n'
    '  "company": "company name or empty string",\n'
    '  "company_tier": "MNC or startup or mid-tier or unknown",\n'
    '  "walk_in_date": "YYYY-MM-DD or null",\n'
    '  "walk_in_time": "HH:MM-HH:MM or null",\n'
    '  "location_address": "specific Bangalore address or null",\n'
    '  "contact": "email or phone or null",\n'
    '  "legitimacy_score": 1-10,\n'
    '  "red_flags": [],\n'
    '  "summary": "one sentence",\n'
    '  "is_walk_in": true or false,\n'
    '  "is_intern": true or false,\n'
    '  "is_fresher_eligible": true or false,\n'
    '  "experience_required": "e.g. 0-2 years or null",\n'
    '  "work_mode": "remote or hybrid or onsite or unknown",\n'
    '  "stipend_or_salary": "e.g. 15000/month or null",\n'
    '  "application_deadline": "YYYY-MM-DD or null"\n'
    '}\n\n'
    "SCORING:\n"
    "9-10: Known company, full address, corporate contact, specific date+time\n"
    "7-8: Recognizable company, has venue+contact+date\n"
    "5-6: Unknown company but specific venue+contact+date, no scam signals\n"
    "3-4: Missing address or contact, no scam signals\n"
    "1-2: Registration fee, guaranteed placement, fake salary, no address,\n"
    "     OR news article/personal achievement post/profile page\n\n"
    "is_intern=true if: intern, internship, stipend, trainee program, apprentice\n"
    "is_fresher_eligible=true if: fresher, 0 years, 0-2 years, entry level, intern\n\n"
    "LISTING:\n"
)


def sanitize(text: str) -> str:
    """
    Clean text before sending to Groq.
    Fixes HTTP 400 errors caused by:
      1. Unicode math bold chars (LinkedIn bold text styling like boldCYBERSECURITY)
      2. Emojis
      3. Curly/smart quotes and apostrophes
      4. Control characters
    """
    if not text:
        return ""

    # Step 1: Convert Unicode math bold/italic/sans-serif chars to ASCII
    result = []
    for ch in text:
        cp = ord(ch)
        # Mathematical Bold Capital A-Z (U+1D400-U+1D419)
        if 0x1D400 <= cp <= 0x1D419:
            result.append(chr(ord('A') + cp - 0x1D400))
        # Mathematical Bold Small a-z (U+1D41A-U+1D433)
        elif 0x1D41A <= cp <= 0x1D433:
            result.append(chr(ord('a') + cp - 0x1D41A))
        # Mathematical Italic Capital (U+1D434-U+1D44D)
        elif 0x1D434 <= cp <= 0x1D44D:
            result.append(chr(ord('A') + cp - 0x1D434))
        # Mathematical Italic Small (U+1D44E-U+1D467)
        elif 0x1D44E <= cp <= 0x1D467:
            result.append(chr(ord('a') + cp - 0x1D44E))
        # Bold Italic Capital (U+1D468-U+1D481)
        elif 0x1D468 <= cp <= 0x1D481:
            result.append(chr(ord('A') + cp - 0x1D468))
        # Bold Italic Small (U+1D482-U+1D49B)
        elif 0x1D482 <= cp <= 0x1D49B:
            result.append(chr(ord('a') + cp - 0x1D482))
        # Sans-serif Bold Capital (U+1D5D4-U+1D5ED)
        elif 0x1D5D4 <= cp <= 0x1D5ED:
            result.append(chr(ord('A') + cp - 0x1D5D4))
        # Sans-serif Bold Small (U+1D5EE-U+1D607)
        elif 0x1D5EE <= cp <= 0x1D607:
            result.append(chr(ord('a') + cp - 0x1D5EE))
        # Sans-serif Bold Italic Capital (U+1D63C-U+1D655)
        elif 0x1D63C <= cp <= 0x1D655:
            result.append(chr(ord('A') + cp - 0x1D63C))
        # Sans-serif Bold Italic Small (U+1D656-U+1D66F)
        elif 0x1D656 <= cp <= 0x1D66F:
            result.append(chr(ord('a') + cp - 0x1D656))
        # Mathematical Bold Digits (U+1D7CE-U+1D7D7)
        elif 0x1D7CE <= cp <= 0x1D7D7:
            result.append(chr(ord('0') + cp - 0x1D7CE))
        # All other math alphanumeric (drop)
        elif 0x1D400 <= cp <= 0x1D7FF:
            result.append('')
        # Non-BMP characters beyond U+FFFF (includes most emojis) — remove
        elif cp > 0xFFFF:
            result.append(' ')
        else:
            result.append(ch)

    text = ''.join(result)

    # Step 2: Curly/smart quotes and dashes -> plain ASCII
    replacements = {
        '\u2018': "'", '\u2019': "'",  # left/right single quote
        '\u201C': '"', '\u201D': '"',  # left/right double quote
        '\u2013': '-', '\u2014': '-',  # en dash, em dash
        '\u2032': "'", '\u2033': '"',  # prime, double prime
        '\u00B4': "'", '\u0060': "'",  # acute accent, grave accent
        '\u2026': '...',               # ellipsis
        '\u00A0': ' ',                 # non-breaking space
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    # Step 3: Remove remaining emoji ranges in BMP
    text = re.sub(r'[\u2600-\u27FF\uFE00-\uFE0F\u2702-\u27B0]', ' ', text)

    # Step 4: Remove control characters (keep \n \t)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)

    # Step 5: Collapse excessive whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


# =============================================================================
# PRE-FILTER
# =============================================================================

REJECT_PATTERNS = [
    # Garbage pages
    "log in or sign up", "sign up", "join now", "linkedin india",
    "linkedin: log in", "page not found", "404", "jobs at ", "careers at ",
    # Personal achievement posts (not job openings)
    "excited to share that i", "thrilled to share that i",
    "excited to announce that i", "i have been selected",
    "i have kicked off my", "officially completed my",
    "i am starting a new position", "im starting a new position",
    "i'm starting a new position",
    "my internship at", "my 6-month internship", "my internship journey",
    "left bangalore to pursue",
    # Profile headlines (not jobs)
    "helping organizations secure", "cissp, gcfa",
    "per month (source:", "leetcode/glassdoor",
    # Course/certification posts
    "free cybersecurity online", "with certificate for everyone",
    # Irrelevant roles
    "food experience", "chef", "restaurant", "hospitality",
    "accounts receivable", "accounts payable", "legal entity controller",
    "head of global finance", "monetization operation", "financial data analyst",
    "marketing operations", "lead generation", "payroll",
    "global people support", "talent acquisition",
    "recruiter", "staffing consultant",
    "content writer", "seo specialist", "social media manager",
    "graphic designer", "ux designer",
    "mechanical engineer", "civil engineer", "electrical engineer",
    "customer support", "customer service", "call center", "bpo", "telecaller",
    "supply chain", "logistics", "warehouse",
    "teacher", "professor", "lecturer",
    "medical officer", "nurse", "doctor",
    "chartered accountant", "ca fresher",
]


def is_relevant(listing: dict) -> bool:
    title    = sanitize(listing.get("title", "")).lower()
    desc     = sanitize(listing.get("description", "")).lower()
    combined = title + " " + desc

    if any(p in title for p in REJECT_PATTERNS):
        return False

    has_role   = any(r in combined for r in TARGET_ROLES)
    has_intern = any(k in combined for k in INTERN_KEYWORDS)
    has_sec    = any(k in combined for k in [
        "security", "cyber", "risk", "compliance", "grc", "audit",
        "fraud", "kyc", "aml", "privacy", "cloud", "network",
        "forensic", "malware", "threat", "vulnerability",
    ])

    return has_role or (has_intern and has_sec)


def pre_filter(listings: list) -> list:
    kept    = [l for l in listings if is_relevant(l)]
    dropped = len(listings) - len(kept)
    logger.info("Pre-filter: %d/%d kept, %d dropped", len(kept), len(listings), dropped)
    return kept


# =============================================================================
# GROQ CALL
# =============================================================================

def call_groq(listing_text: str) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    safe = sanitize(listing_text)[:2500]

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
                {"role": "user",   "content": USER_PROMPT_PREFIX + safe},
            ],
            "temperature": 0.1,
            "max_tokens":  800,
        },
        timeout=30,
    )

    if r.status_code == 400:
        try:
            err_msg = r.json().get("error", {}).get("message", r.text[:300])
        except Exception:
            err_msg = r.text[:300]
        logger.error("Groq 400: %s", err_msg)

    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def score_listing(listing: dict) -> dict | None:
    listing_text = (
        "SOURCE: "       + sanitize(listing.get("source", ""))                         + "\n"
        "TITLE: "        + sanitize(listing.get("title", ""))                          + "\n"
        "COMPANY: "      + sanitize(listing.get("company", ""))                        + "\n"
        "LOCATION: "     + sanitize(listing.get("location", ""))                       + "\n"
        "DATE POSTED: "  + sanitize(listing.get("date_posted", ""))                    + "\n"
        "URL: "          + sanitize(listing.get("job_url") or listing.get("url", ""))  + "\n"
        "DESCRIPTION:\n" + sanitize(listing.get("description", ""))[:1600]
    )

    try:
        raw = call_groq(listing_text)
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*",     "", raw)
        start = raw.find("{")
        end   = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON in response")
        d = json.loads(raw[start : end + 1])

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
            result["experience_required"] or "?",
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON parse '%s': %s", listing.get("title", "?")[:40], e)
        return None
    except Exception as e:
        logger.error("Error '%s': %s", listing.get("title", "?")[:40], e)
        return None


def score_all(listings: list, min_score: int = 4) -> list:
    relevant = pre_filter(listings)
    if not relevant:
        logger.info("Nothing relevant after pre-filter")
        return []

    scored = []
    logger.info("Scoring %d listings via Groq...", len(relevant))

    for i, listing in enumerate(relevant):
        logger.info("Scoring %d/%d: %s",
                    i + 1, len(relevant),
                    sanitize(listing.get("title", "?"))[:55])
        result = score_listing(listing)

        if result is None:
            continue
        if result["legitimacy_score"] < min_score:
            logger.info("  -> Dropped score=%d", result["legitimacy_score"])
            continue

        scored.append(result)
        time.sleep(2)

    logger.info("Done: %d/%d passed", len(scored), len(relevant))
    return scored
