import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS, KNOWN_MNCS

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
    '  "domain": "SOC or GRC or AppSec or VAPT or CloudSec or IAM or Forensics or Risk or Fraud-AML or General",\n'
    '  "legitimacy_score": 1-10,\n'
    '  "red_flags": [],\n'
    '  "summary": "one sentence",\n'
    '  "is_intern": true or false,\n'
    '  "experience_required": "e.g. 0-2 years or freshers or null",\n'
    '  "skills_required": "comma-separated skills e.g. SIEM, Python, ISO 27001 or empty string",\n'
    '  "salary_range": "e.g. 4-6 LPA or null",\n'
    '  "apply_url": "direct application URL or null",\n'
    '  "posted_date": "YYYY-MM-DD or null"\n'
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
    "- skills_required must be a plain comma-separated STRING, not a list/array\n"
    "- domain: pick closest from SOC/GRC/AppSec/VAPT/CloudSec/IAM/Forensics/Risk/Fraud-AML/General\n\n"
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
# PRE-FILTER  (unchanged from original)
# =============================================================================

REJECT_PATTERNS = [
    "jobs in united states", "jobs in india", "jobs in united kingdom",
    "jobs in canada", "jobs in australia", "jobs in singapore",
    "jobs in germany", "jobs in europe", "+ jobs",
    "new jobs", "(1,713 new)", "(244 new)",
    "| ceh certified", "| cissp", "| gcfa", "| ejpt",
    "penetration tester| python", "cyber security professional",
    "helping organizations secure", "satish kumar", "deepak pokhrel",
    "ramavath rakesh",
    "excited to share that i", "thrilled to share that i",
    "excited to announce that i", "i have been selected",
    "i have kicked off my", "officially completed my",
    "i am starting a new position", "im starting a new position",
    "i'm starting a new position", "i have started",
    "my internship at", "my 6-month internship", "my internship journey",
    "left bangalore to pursue", "kickstart your cybersecurity career!",
    "roadmap to become", "read this be",
    "log in or sign up", "sign up", "join now", "linkedin india",
    "linkedin: log in", "page not found", "404", "jobs at ", "careers at ",
    "free cybersecurity online", "with certificate for everyone",
    "per month (source:", "leetcode/glassdoor",
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

REJECT_REGEX = [
    r'^\d[\d,]+\+?\s+\w',
    r'^\d+\s+\w+.*jobs in',
]


def is_relevant(listing: dict) -> bool:
    title    = sanitize(listing.get("title", "")).lower()
    desc     = sanitize(listing.get("description", "")).lower()
    combined = title + " " + desc
    url      = (listing.get("url") or listing.get("job_url") or "").lower()

    if "linkedin.com/in/" in url:
        return False
    for pattern in REJECT_REGEX:
        if re.match(pattern, title, re.IGNORECASE):
            return False
    if any(p in title for p in REJECT_PATTERNS):
        return False
    if re.match(r'^[a-z]+ [a-z]+(,? [a-z]+)? - .{5,} @', title):
        return False

    CONTENT_REJECTS = [
        "offers free", "free cyber security virtual", "free online cyber",
        "free cybersecurity online", "virtual internship for college",
        "with certificate for everyone",
        "meet our interns", "my internship at", "my internship journey",
        "officially completed my", "i have completed",
        "excited to share that i", "thrilled to announce",
        "cheat sheet", "roadmap to become", "where to find",
        "how to get into", "tips for", "guide to",
        "rise of fake internships", "beware of internship",
        "reality check", "fake internship",
        "interview experience", "interview process at",
        "is this enough for", "what salary can freshers expect",
        "per month (source:", "leetcode/glassdoor",
    ]
    if any(p in combined for p in CONTENT_REJECTS):
        return False

    WALKIN_REJECTS = [
        "walk-in", "walk in interview", "walkin interview",
        "walk-in drive", "walkin drive", "direct interview",
        "mega drive", "hiring drive",
    ]
    if any(p in combined for p in WALKIN_REJECTS):
        return False

    DOMAIN_REJECTS = [
        "vlsi", "embedded systems", "mechanical engineer",
        "civil engineer", "electrical engineer",
        "accounts receivable", "accounts payable",
        "content writer", "graphic designer", "ux designer",
        "customer support", "customer service", "call center",
        "supply chain", "logistics", "teacher", "professor",
        "medical officer", "nurse", "chartered accountant",
    ]
    if any(p in title for p in DOMAIN_REJECTS):
        return False

    if re.search(r'\b(1[0-9]|20)\+?\s*years?\b', title):
        return False

    has_role_in_title   = any(r in title for r in TARGET_ROLES)
    has_intern_in_title = any(k in title for k in INTERN_KEYWORDS)
    has_sec_in_combined = any(k in combined for k in [
        "security", "cyber", "risk", "compliance", "grc", "audit",
        "fraud", "kyc", "aml", "privacy", "cloud", "network",
        "forensic", "malware", "threat", "vulnerability",
    ])

    return has_role_in_title or (has_intern_in_title and has_sec_in_combined)


def pre_filter(listings: list) -> list:
    kept    = [l for l in listings if is_relevant(l)]
    dropped = len(listings) - len(kept)
    logger.info("Pre-filter: %d/%d kept, %d dropped", len(kept), len(listings), dropped)
    return kept


# =============================================================================
# GROQ CALL  (unchanged from original — uses retry-after header)
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
        "max_tokens":  600,
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
                retry_after = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset-requests")
                wait = float(retry_after) + 1 if retry_after else 10 * (2 ** (attempt - 1))
                logger.warning("Groq 429 rate limit (attempt %d/%d) — waiting %.0fs",
                               attempt, retries, wait)
                time.sleep(wait)
                continue

            if r.status_code == 400:
                try:    err_msg = r.json().get("error", {}).get("message", r.text[:200])
                except: err_msg = r.text[:200]
                logger.error("Groq 400: %s", err_msg)

            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.Timeout:
            logger.warning("Groq timeout attempt %d/%d", attempt, retries)
            if attempt < retries:
                time.sleep(5 * attempt)

    raise RuntimeError(f"Groq failed after {retries} attempts")


# =============================================================================
# HELPERS
# =============================================================================

def _resolve_tier(company: str, ai_tier: str) -> str:
    """Override to MNC if company is in known list."""
    if any(mnc in (company or "").lower() for mnc in KNOWN_MNCS):
        return "MNC"
    return ai_tier if ai_tier in ("MNC", "mid-tier", "startup") else "unknown"


def _merge_company(name: str, tier: str) -> str:
    """'Accenture' + 'MNC' → 'Accenture (MNC)'"""
    name = (name or "").strip() or "Unknown"
    return f"{name} ({tier})"


def _skills_to_str(skills) -> str:
    """Normalise skills — model may return list or string."""
    if isinstance(skills, list):
        return ", ".join(str(s).strip() for s in skills if s)
    return str(skills or "").strip()


# =============================================================================
# SCORE ONE LISTING
# =============================================================================

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

        ai_name = d.get("company") or listing.get("company", "")
        ai_tier = _resolve_tier(ai_name, d.get("company_tier", "unknown"))

        # ── Build result dict — keys match SHEET_COLUMNS exactly ──────────────
        result = {
            "scraped_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "job_title":            d.get("job_title")  or listing.get("title", ""),
            "company":              _merge_company(ai_name, ai_tier),
            "domain":               d.get("domain", "General"),
            "legitimacy_score":     int(d.get("legitimacy_score", 1)),
            "red_flags":            d.get("red_flags", []),   # stored as list; _save_listing converts
            "summary":              d.get("summary", ""),
            "is_intern":            bool(d.get("is_intern", False)),
            "experience_required":  d.get("experience_required") or "",
            "skills_required":      _skills_to_str(d.get("skills_required", "")),
            "salary_range":         d.get("salary_range") or "",
            "apply_url":            d.get("apply_url") or listing.get("job_url", ""),
            "posted_date":          d.get("posted_date") or "",
            "source":               listing.get("source", ""),
            "url":                  listing.get("job_url") or listing.get("url", ""),
            "status":               "New",
        }

        tag = "INTERN" if result["is_intern"] else "regular"
        logger.info("  [%s] %s @ %s | score=%d | %s",
                    tag,
                    result["job_title"][:35],
                    result["company"][:25],
                    result["legitimacy_score"],
                    result["experience_required"] or "?")
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON error '%s': %s", listing.get("title","?")[:40], e)
        return None
    except Exception as e:
        logger.error("Error '%s': %s", listing.get("title","?")[:40], e)
        return None


# =============================================================================
# SCORE ALL
# =============================================================================

def score_all(listings: list, min_score: int = 4) -> list:
    relevant = pre_filter(listings)
    if not relevant:
        logger.info("Nothing relevant after pre-filter")
        return []

    # Dedup by company+title before scoring (prevents scoring same post N times)
    seen_key: set[str] = set()
    deduped = []
    for l in relevant:
        title   = sanitize(l.get("title", "")).lower().strip()
        company = sanitize(l.get("company", "")).lower().strip()
        key = f"{company}|{title}" if company else title
        if key in seen_key:
            continue
        seen_key.add(key)
        deduped.append(l)

    logger.info("Pre-score dedup: %d → %d (removed %d duplicates)",
                len(relevant), len(deduped), len(relevant) - len(deduped))

    scored = []
    logger.info("Scoring %d listings via Groq...", len(deduped))

    for i, listing in enumerate(deduped):
        logger.info("Scoring %d/%d: %s",
                    i + 1, len(deduped),
                    sanitize(listing.get("title", "?"))[:55])
        result = score_listing(listing)

        if result is None:
            continue
        if result["legitimacy_score"] < min_score:
            logger.info("  -> Dropped score=%d", result["legitimacy_score"])
            continue

        scored.append(result)
        time.sleep(3)   # Groq free tier: 30 req/min

    logger.info("Done: %d/%d passed", len(scored), len(deduped))
    return scored
