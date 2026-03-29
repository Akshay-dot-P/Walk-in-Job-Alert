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
    "Analyze the job listing below and return ONLY a valid JSON object.\n\n"
    "Required keys:\n"
    '{\n'
    '  "job_title": "normalized title string",\n'
    '  "company": "company name or empty string",\n'
    '  "company_tier": "MNC or startup or mid-tier or unknown",\n'
    '  "legitimacy_score": 1-10,\n'
    '  "red_flags": [],\n'
    '  "summary": "one sentence",\n'
    '  "is_intern": true or false,\n'
    '  "is_fresher_eligible": true or false,\n'
    '  "experience_required": "e.g. 0-2 years or null",\n'
    '  "work_mode": "remote or hybrid or onsite or unknown",\n'
    '  "skills_required": ["skill1", "skill2"],\n'
    '  "salary_range": "e.g. 4-6 LPA or null",\n'
    '  "apply_url": "direct application URL or null",\n'
    '  "notice_period": "immediate or 30 days or null",\n'
    '  "openings_count": number or null,\n'
    '  "posted_date": "YYYY-MM-DD or null",\n'
    '  "application_deadline": "YYYY-MM-DD or null",\n'
    '  "domain": "SOC or GRC or AppSec or VAPT or CloudSec or IAM or Forensics or General"\n'
    '}\n\n'
    "SCORING RUBRIC:\n"
    "9-10: MNC or well-known company, detailed JD with specific skills, "
    "      realistic salary (3-12 LPA for fresher/0-2yr), direct apply link, "
    "      clear eligibility criteria, no red flags\n"
    "7-8:  Recognizable company, decent JD, apply link present, "
    "      realistic expectations, minor info gaps\n"
    "5-6:  Unknown/startup company but specific role, real skills listed, "
    "      legitimate-looking apply link or source, no scam signals\n"
    "3-4:  Vague JD, no salary info, no company name, "
    "      but no active scam signals detected\n"
    "1-2:  ANY of: registration/training fee required, guaranteed placement/interview, "
    "      unrealistic salary (50k/month fresher), no apply link + no company name, "
    "      obvious fake or spam posting\n\n"
    "IMPORTANT RULES:\n"
    "- Missing salary is NORMAL — do not penalize\n"
    "- Missing physical address is NORMAL for online jobs — do not penalize\n"
    "- No walk-in date expected — this is an online job posting\n"
    "- is_intern=true if: intern, internship, stipend, trainee, apprentice\n"
    "- is_fresher_eligible=true if: fresher, 0 years, 0-2 years, entry level, intern\n"
    "- domain: pick closest from SOC/GRC/AppSec/VAPT/CloudSec/IAM/Forensics/General\n\n"
    "LISTING:\n"
)

# =============================================================================
# SANITIZE
# =============================================================================

def sanitize(text: str) -> str:
    if not text:
        return ""
    result = []
    for ch in text:
        cp = ord(ch)
        if 0x1D400 <= cp <= 0x1D419:   result.append(chr(ord('A') + cp - 0x1D400))
        elif 0x1D41A <= cp <= 0x1D433: result.append(chr(ord('a') + cp - 0x1D41A))
        elif 0x1D434 <= cp <= 0x1D44D: result.append(chr(ord('A') + cp - 0x1D434))
        elif 0x1D44E <= cp <= 0x1D467: result.append(chr(ord('a') + cp - 0x1D44E))
        elif 0x1D468 <= cp <= 0x1D481: result.append(chr(ord('A') + cp - 0x1D468))
        elif 0x1D482 <= cp <= 0x1D49B: result.append(chr(ord('a') + cp - 0x1D482))
        elif 0x1D5D4 <= cp <= 0x1D5ED: result.append(chr(ord('A') + cp - 0x1D5D4))
        elif 0x1D5EE <= cp <= 0x1D607: result.append(chr(ord('a') + cp - 0x1D5EE))
        elif 0x1D63C <= cp <= 0x1D655: result.append(chr(ord('A') + cp - 0x1D63C))
        elif 0x1D656 <= cp <= 0x1D66F: result.append(chr(ord('a') + cp - 0x1D656))
        elif 0x1D7CE <= cp <= 0x1D7D7: result.append(chr(ord('0') + cp - 0x1D7CE))
        elif 0x1D400 <= cp <= 0x1D7FF: result.append('')
        elif cp > 0xFFFF:               result.append(' ')
        else:                           result.append(ch)
    text = ''.join(result)

    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201C': '"', '\u201D': '"',
        '\u2013': '-', '\u2014': '-', '\u2026': '...', '\u00A0': ' ',
        '\u2032': "'", '\u2033': '"', '\u00B4': "'",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)

    text = re.sub(r'[\u2600-\u27FF\uFE00-\uFE0F\u2702-\u27B0]', ' ', text)
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# =============================================================================
# PRE-FILTER
# =============================================================================

REJECT_PATTERNS = [
    # LinkedIn job count pages — "X,000+ Role jobs in Country"
    "jobs in united states", "jobs in india", "jobs in united kingdom",
    "jobs in canada", "jobs in australia", "jobs in singapore",
    "jobs in germany", "jobs in europe", "+ jobs",
    "new jobs", "(1,713 new)", "(244 new)",
    # Profile/person pages
    "| ceh certified", "| cissp", "| gcfa", "| ejpt",
    "penetration tester| python", "cyber security professional",
    "helping organizations secure", "satish kumar", "deepak pokhrel",
    "ramavath rakesh",
    # Personal achievement / experience posts
    "excited to share that i", "thrilled to share that i",
    "excited to announce that i", "i have been selected",
    "i have kicked off my", "officially completed my",
    "i am starting a new position", "im starting a new position",
    "i'm starting a new position", "i have started",
    "my internship at", "my 6-month internship", "my internship journey",
    "left bangalore to pursue", "kickstart your cybersecurity career!",
    "roadmap to become", "read this be",
    # Garbage pages
    "log in or sign up", "sign up", "join now", "linkedin india",
    "linkedin: log in", "page not found", "404", "jobs at ", "careers at ",
    # Course posts
    "free cybersecurity online", "with certificate for everyone",
    "per month (source:", "leetcode/glassdoor",
    # Irrelevant roles
    "food experience", "chef", "restaurant",
    "accounts receivable", "accounts payable", "legal entity controller",
    "head of global finance", "monetization operation",
    "marketing operations", "lead generation", "payroll",
    "global people support", "talent acquisition",
    "content writer", "seo specialist", "social media manager",
    "graphic designer", "ux designer",
    "mechanical engineer", "civil engineer", "electrical engineer",
    "customer support", "customer service", "call center", "bpo",
    "supply chain", "logistics", "warehouse",
    "teacher", "professor", "lecturer",
    "medical officer", "nurse", "doctor",
    "chartered accountant",
]

# Titles matching these regex patterns are rejected
REJECT_REGEX = [
    r'^\d[\d,]+\+?\s+\w',        # "2,000+ Information Security..." or "48,000+ Malware..."
    r'^\d+\s+\w+.*jobs in',      # "768 Information Security Advisor jobs in..."
]


def is_relevant(listing: dict) -> bool:
    title    = sanitize(listing.get("title", "")).lower()
    desc     = sanitize(listing.get("description", "")).lower()
    combined = title + " " + desc

    # Regex-based rejects (job count pages)
    for pattern in REJECT_REGEX:
        if re.match(pattern, title, re.IGNORECASE):
            return False

    # Keyword-based rejects
    if any(p in title for p in REJECT_PATTERNS):
        return False

    # Must have a target role or intern+security combo
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
# GROQ CALL — with exponential backoff retry on 429
# =============================================================================

def call_groq(listing_text: str, retries: int = 4) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")

    safe = sanitize(listing_text)[:2500]

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_PROMPT_PREFIX + safe},
        ],
        "temperature": 0.1,
        "max_tokens":  800,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    for attempt in range(1, retries + 1):
        try:
            r = requests.post(GROQ_API_URL, headers=headers,
                              json=payload, timeout=30)

            if r.status_code == 429:
                # Read retry-after header if present, else use exponential backoff
                retry_after = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset-requests")
                if retry_after:
                    wait = float(retry_after) + 1
                else:
                    wait = 10 * (2 ** (attempt - 1))  # 10s, 20s, 40s, 80s
                logger.warning(
                    "Groq 429 rate limit (attempt %d/%d) — waiting %.0fs",
                    attempt, retries, wait
                )
                time.sleep(wait)
                continue

            if r.status_code == 400:
                try:
                    err_msg = r.json().get("error", {}).get("message", r.text[:200])
                except Exception:
                    err_msg = r.text[:200]
                logger.error("Groq 400: %s", err_msg)

            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.warning("Groq timeout attempt %d/%d", attempt, retries)
            if attempt < retries:
                time.sleep(5 * attempt)

    raise RuntimeError(f"Groq failed after {retries} attempts")


def score_listing(listing: dict) -> dict | None:
    listing_text = (
        "SOURCE: "       + sanitize(listing.get("source", ""))                        + "\n"
        "TITLE: "        + sanitize(listing.get("title", ""))                         + "\n"
        "COMPANY: "      + sanitize(listing.get("company", ""))                       + "\n"
        "LOCATION: "     + sanitize(listing.get("location", ""))                      + "\n"
        "DATE POSTED: "  + sanitize(listing.get("date_posted", ""))                   + "\n"
        "URL: "          + sanitize(listing.get("job_url") or listing.get("url",""))  + "\n"
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
            "domain":               d.get("domain", "General"),
            "legitimacy_score":     int(d.get("legitimacy_score", 1)),
            "red_flags":            d.get("red_flags", []),
            "summary":              d.get("summary", ""),
            "is_intern":            bool(d.get("is_intern", False)),
            "is_fresher_eligible":  bool(d.get("is_fresher_eligible", False)),
            "experience_required":  d.get("experience_required"),
            "work_mode":            d.get("work_mode", "unknown"),
            "skills_required":      d.get("skills_required", []),
            "salary_range":         d.get("salary_range"),
            "apply_url":            d.get("apply_url") or listing.get("job_url", ""),
            "notice_period":        d.get("notice_period"),
            "openings_count":       d.get("openings_count"),
            "posted_date":          d.get("posted_date"),
            "application_deadline": d.get("application_deadline"),
            "status":               "pending",
        }

        tags = []
        if result["is_intern"]:           tags.append("INTERN")
        if result["is_fresher_eligible"]: tags.append("FRESHER-OK")
        logger.info("  [%s] %s @ %s | score=%d | %s",
                    "/".join(tags) or "regular",
                    result["job_title"][:35], result["company"][:20],
                    result["legitimacy_score"],
                    result["experience_required"] or "?")
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON error '%s': %s", listing.get("title","?")[:40], e)
        return None
    except Exception as e:
        logger.error("Error '%s': %s", listing.get("title","?")[:40], e)
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

        # Groq free tier: 30 req/min = 2s minimum between calls
        # Use 3s to give headroom and avoid hitting the limit
        time.sleep(3)

    logger.info("Done: %d/%d passed", len(scored), len(relevant))
    return scored
