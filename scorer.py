import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS, KNOWN_MNCS
from listing_filters import entry_level_gate_enabled, passes_entry_level_gate

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
    "- Unknown or startup company is NOT a red flag — score based on role relevance, not company fame\n"
    "- is_intern=true if: intern, internship, stipend, trainee, apprentice\n"
    "- skills_required must be a plain comma-separated STRING, not a list/array\n"
    "- domain: pick closest from SOC/GRC/AppSec/VAPT/CloudSec/IAM/Forensics/Risk/Fraud-AML/General\n\n"
    "TARGET ENTRY BAND (freshers / interns / 0–2y — prefer HIGH scores 7–10 only when JD fits THIS band):\n"
    "- Internship, apprentice, trainee, graduate hire, campus program, entry-level, fresher,\n"
    "  junior, 0–2 years, Analyst I / Engineer I / L1 / tier-1 style entry grades.\n\n"
    "OUTSIDE TARGET BAND — set legitimacy_score to 1–3 (still return valid JSON; not a scam unless other rules say so):\n"
    "- Title has Senior (except Big-4-style 'Senior Associate' / 'Sr. Associate' only),\n"
    "  Principal, Staff Engineer/Architect, Distinguished, Director, VP, CISO, Chief, Head of,\n"
    "  Lead Engineer/Developer/Architect (lead IC), Group Manager, Engineering Manager.\n"
    "- Title says mid-level / middle level / midlevel.\n"
    "- Title uses Roman II or higher after a role word (e.g. Analyst II, Risk Analyst III) — Roman I alone can stay in band.\n"
    "- Title uses mid/senior tiers like Analyst L3/L4/L5+ or Engineer L3+ (numeric L tiers).\n"
    "- Title or JD requires ~5+ years minimum, 6+ years, or clearly mid-career/senior expectations.\n"
    "- Supervisor or Manager in title unless it is clearly an intern/trainee posting.\n\n"
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

    if entry_level_gate_enabled():
        if not passes_entry_level_gate(
            sanitize(listing.get("title", "")),
            sanitize(listing.get("description", "")),
        ):
            return False
    else:
        SENIOR_REJECTS = [
            "lead soc", "lead security", "lead analyst", "lead engineer",
            "lead service", "lead siem", "lead consultant",
            " sr. engineer", " sr engineer", "senior engineer",
            "senior security analyst", "senior soc analyst",
            "senior operations", "senior specialist",
            "manager ", "supervisor", "director",
            "head of", "vice president", " vp ",
            "ciso", "principal engineer", "principal analyst", "staff engineer",
        ]
        SENIOR_EXCEPTIONS = [
            "senior associate",
            "sr associate", "sr. associate",
        ]
        has_senior = any(p in title for p in SENIOR_REJECTS)
        has_exception = any(p in title for p in SENIOR_EXCEPTIONS)
        if has_senior and not has_exception:
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


def _parse_date_yyyy_mm_dd(value: str) -> datetime | None:
    s = (value or "").strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T", 1)[0]
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def freshness_filter(listings: list[dict], max_age_days: int) -> list[dict]:
    if not max_age_days or max_age_days <= 0:
        return listings

    now = datetime.now(timezone.utc)
    kept: list[dict] = []
    dropped = 0
    for l in listings:
        raw = str(l.get("date_posted") or l.get("posted_date") or "").strip()
        dt = _parse_date_yyyy_mm_dd(raw)
        if not dt:
            kept.append(l)
            continue
        age_days = (now - dt).days
        if age_days > max_age_days:
            dropped += 1
            continue
        kept.append(l)

    logger.info("Freshness filter (max_age_days=%d): %d/%d kept, %d dropped",
                max_age_days, len(kept), len(listings), dropped)
    return kept


# =============================================================================
# AGGRESSIVE DEDUPLICATION - NEW
# =============================================================================

def _normalize_company_aggressive(company: str) -> str:
    """
    Aggressively normalize company name to catch variations:
    'Verint Financial Compliance' → 'verint'
    'Deloitte USI' → 'deloitte'
    'Accenture (India)' → 'accenture'
    """
    if not company:
        return ""
    
    c = sanitize(company).lower().strip()
    
    # Remove common suffixes
    COMPANY_NOISE = [
        r'\s*\(.*?\)',          # Remove anything in parentheses: (India), (MNC), (mid-tier)
        r'\s+india.*$',         # 'Accenture India Pvt Ltd' → 'accenture'
        r'\s+pvt\.?\s*ltd.*$',  # 'XYZ Pvt Ltd' → 'xyz'
        r'\s+limited.*$',       # 'ABC Limited' → 'abc'
        r'\s+inc\.?$',          # 'Microsoft Inc' → 'microsoft'
        r'\s+corp\.?$',         # 'Oracle Corp' → 'oracle'
        r'\s+technologies.*$',  # 'Wipro Technologies' → 'wipro'
        r'\s+solutions.*$',     # 'TCS Solutions' → 'tcs'
        r'\s+services.*$',      # 'Infosys Services' → 'infosys'
        r'\s+consulting.*$',    # 'Deloitte Consulting' → 'deloitte'
        r'\s+usi$',             # 'Deloitte USI' → 'deloitte'
        r'\s+acceleration.*$',  # 'PwC Acceleration Center' → 'pwc'
        r'\s+financial.*$',     # 'Verint Financial Compliance' → 'verint'
    ]
    
    for pattern in COMPANY_NOISE:
        c = re.sub(pattern, '', c).strip()
    
    # Remove all punctuation and extra spaces
    c = re.sub(r'[^\w\s]', ' ', c)
    c = re.sub(r'\s+', ' ', c).strip()
    
    # Take first significant word if multi-word (helps catch "Big 4" variations)
    # 'pwc acceleration center india' → 'pwc'
    words = c.split()
    if words:
        # Common case: take first word unless it's generic
        if words[0] not in ('the', 'a', 'an'):
            return words[0]
    
    return c


def _normalize_title_aggressive(title: str) -> str:
    """
    Aggressively normalize title to catch fuzzy duplicates:
    'SOC L1 Analyst' → 'soc analyst'
    'USI-FY26-Cyber-Detect & Respond-SSA-SIEM' → 'cyber detect respond siem'
    'Security Operations Center Analyst' → 'soc analyst'
    """
    if not title:
        return ""
    
    t = sanitize(title).lower().strip()
    
    # Remove year/FY tags
    t = re.sub(r'\b(fy|year|yr)\s*-?\s*\d{2,4}\b', '', t)  # FY26, FY2026, year2024
    t = re.sub(r'\b20\d{2}\b', '', t)  # 2024, 2025, 2026
    
    # Remove location mentions
    t = re.sub(r'\b(bangalore|bengaluru|india|karnataka|blr)\b', '', t)
    
    # Remove level/tier indicators (these make same role look different)
    t = re.sub(r'\b(l1|l2|l3|l4|l-1|l-2|tier\s*[1-4]|level\s*[1-4])\b', '', t)
    
    # Remove grade/band indicators
    t = re.sub(r'\b(ssa|lsa|sa|associate|sr\.|sr|senior|junior|jr)\b', '', t)
    
    # Remove common acronyms that add noise
    t = re.sub(r'\busi\b', '', t)  # USI-FY26 style prefixes
    
    # Expand common abbreviations to match full forms
    EXPANSIONS = {
        r'\bsoc\b': 'security operations center',
        r'\bgrc\b': 'governance risk compliance',
        r'\biam\b': 'identity access management',
        r'\bpam\b': 'privileged access management',
        r'\bvapt\b': 'vulnerability assessment penetration testing',
        r'\bdfir\b': 'digital forensics incident response',
    }
    for abbr, full in EXPANSIONS.items():
        t = re.sub(abbr, full, t)
    
    # Remove all punctuation
    t = re.sub(r'[^\w\s]', ' ', t)
    
    # Remove extra whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    
    # Sort words alphabetically (so "Analyst SOC" matches "SOC Analyst")
    words = sorted(t.split())
    
    # Remove duplicates and common noise words
    NOISE_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'for', 'in', 'at', 'to', 'of', 'with',
        'position', 'role', 'job', 'opening', 'opportunity', 'hiring', 'seeking'
    }
    words = [w for w in words if w not in NOISE_WORDS and len(w) > 1]
    
    return ' '.join(words)


def aggressive_deduplicate(listings: list[dict]) -> list[dict]:
    """
    AGGRESSIVE deduplication BEFORE scoring.
    Catches:
    - Same job from multiple sources (different URLs)
    - Company name variations ('Verint' vs 'Verint Financial Compliance')
    - Title variations ('SOC L1 Analyst' vs 'Security Operations Center Analyst - L1')
    - Year/FY variations ('USI-FY26-Cyber-SSA' vs 'USI-FY25-Cyber-LSA' for same underlying role)
    """
    seen_keys: set[str] = set()
    deduped = []
    
    for l in listings:
        # Extract and normalize
        job_id_raw = str(l.get("job_id", "")).strip()
        url_raw = str(l.get("job_url") or l.get("url") or "").strip()
        title_raw = str(l.get("title", "")).strip()
        company_raw = str(l.get("company", "")).strip()
        
        job_id = sanitize(job_id_raw).lower()
        url = sanitize(url_raw).lower()
        title_norm = _normalize_title_aggressive(title_raw)
        company_norm = _normalize_company_aggressive(company_raw)
        
        # Build candidate keys
        candidate_keys: list[str] = []
        
        # Key 1: Exact job_id (if present - Workday etc)
        if job_id:
            candidate_keys.append(f"id:{job_id}")
        
        # Key 2: Exact URL (catches reposts at same link)
        if url:
            candidate_keys.append(f"url:{url}")
        
        # Key 3: Company + normalized title (FUZZY - main dedup logic)
        if company_norm and title_norm:
            candidate_keys.append(f"ct:{company_norm}:{title_norm}")
        
        # Key 4: Just normalized title if company unknown (risky but catches more)
        # Only use if title is specific enough (>= 3 words after normalization)
        if title_norm and len(title_norm.split()) >= 3:
            candidate_keys.append(f"t:{title_norm}")
        
        # Check if any key was seen before
        if any(k in seen_keys for k in candidate_keys):
            continue  # Duplicate - skip
        
        # Register all keys and keep this listing
        seen_keys.update(candidate_keys)
        deduped.append(l)
    
    removed = len(listings) - len(deduped)
    logger.info("AGGRESSIVE dedup: %d → %d (removed %d duplicates)",
                len(listings), len(deduped), removed)
    
    return deduped


# =============================================================================
# GROQ CALL
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
                wait = 8.0 * (2 ** (attempt - 1))
                if retry_after:
                    try:
                        w = float(retry_after)
                        if w <= 120:
                            wait = max(wait, w + 1.0)
                    except (TypeError, ValueError):
                        pass
                wait = min(wait, 90.0)
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

        result = {
            "scraped_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "job_title":            d.get("job_title")  or listing.get("title", ""),
            "company":              _merge_company(ai_name, ai_tier),
            "domain":               d.get("domain", "General"),
            "legitimacy_score":     int(d.get("legitimacy_score", 1)),
            "red_flags":            d.get("red_flags", []),
            "summary":              d.get("summary", ""),
            "is_intern":            bool(d.get("is_intern", False)),
            "experience_required":  d.get("experience_required") or "",
            "skills_required":      _skills_to_str(d.get("skills_required", "")),
            "salary_range":         d.get("salary_range") or "",
            "apply_url":            d.get("apply_url") or listing.get("job_url", ""),
            "posted_date":          d.get("posted_date") or "",
            "source":               listing.get("source", ""),
            "url":                  listing.get("job_url") or listing.get("url", ""),
            "job_id":               listing.get("job_id", ""),
            "source_company":       listing.get("company", ""),
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
# SCORE ALL - WITH AGGRESSIVE DEDUP
# =============================================================================

def score_all(listings: list, min_score: int = 4) -> list:
    """
    Main entry point. Scoring pipeline:
    1. Pre-filter (relevance check)
    2. Freshness filter (age check)
    3. AGGRESSIVE deduplication ← NEW - BEFORE scoring
    4. Score each unique listing
    5. Filter by min_score
    """
    # Step 1: Pre-filter
    relevant = pre_filter(listings)
    if not relevant:
        logger.info("Nothing relevant after pre-filter")
        return []

    # Step 2: Freshness filter
    max_age_days = int(os.environ.get("MAX_POST_AGE_DAYS", "21"))
    relevant = freshness_filter(relevant, max_age_days=max_age_days)
    if not relevant:
        logger.info("Nothing left after freshness filter")
        return []

    # Step 3: AGGRESSIVE DEDUPLICATION - NEW
    # This is the key fix - happens BEFORE scoring to avoid wasting API calls
    deduped = aggressive_deduplicate(relevant)
    if not deduped:
        logger.info("Nothing left after deduplication")
        return []

    # Step 4: Score each unique listing
    scored = []
    logger.info("Scoring %d unique listings via Groq...", len(deduped))

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
        time.sleep(float(os.environ.get("GROQ_SCORE_DELAY_SEC", "5")))

    logger.info("Done: %d/%d passed", len(scored), len(deduped))
    return scored
