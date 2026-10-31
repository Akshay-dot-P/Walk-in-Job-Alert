import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS, KNOWN_MNCS
from listing_filters import entry_level_gate_enabled, passes_entry_level_gate
from collections import Counter
from storage import normalize_url, load_known_urls, log_scored_urls

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
    '  "score_rule": "rule id such as R10a",\n'
    '  "red_flags": [],\n'
    '  "summary": "one sentence",\n'
    '  "is_intern": true or false,\n'
    '  "experience_required": "e.g. 0-2 years or freshers or null",\n'
    '  "skills_required": "comma-separated skills e.g. SIEM, Python, ISO 27001 or empty string",\n'
    '  "salary_range": "e.g. 4-6 LPA or null",\n'
    '  "apply_url": "direct application URL or null",\n'
    '  "posted_date": "YYYY-MM-DD or null"\n'
    '}\n\n'
    "FIRST decide these facts from the listing text only. Never guess.\n"
    "- fresher_proof: the text literally says fresher, freshers, entry level or graduate trainee, "
    "OR states a maximum of 2 years or less (e.g. 0-2 years).\n"
    "- entry_title: title has intern, trainee, apprentice, graduate, junior, associate, L1, Analyst I or Engineer 1.\n"
    "- senior_level_title: title has Senior, Sr, Grade 2 or higher, L2 to L5, II, III, IV, or Analyst/Engineer followed by 2 to 5.\n"
    "- role_keyword: title has a security word (cyber, security, soc, siem, vapt, cloud, iam, grc, threat, vulnerability, network, monitoring) "
    "OR an ops/risk word (fraud, kyc, aml, risk, compliance, audit, privacy, investigation, case, operations). "
    "Both kinds count equally.\n\n"
    "SCORING: apply the rules IN ORDER. The FIRST rule that matches decides the score. "
    "Put the matching id in score_rule.\n"
    "S2: training or registration fee, guaranteed placement, fake or spam listing, salary above 25 LPA for a fresher.\n"
    "S1: title has Staff, Principal, Lead, Head, Director, Manager, VP, Chief, CISO or Architect; "
    "OR the text requires 3 or more years; "
    "OR senior_level_title WITHOUT fresher_proof; "
    "OR posted date is confirmed older than 30 days (a missing date is NOT a reason to reject).\n"
    "R10a: company_tier is startup. Score 10.\n"
    "R9c: senior_level_title WITH fresher_proof. Score 9.\n"
    "R10b: intern, trainee or apprentice role. Score 10.\n"
    "R10c: fresher_proof AND (company_tier is MNC OR entry_title). Score 10.\n"
    "R9a: fresher_proof only. Score 9.\n"
    "R9b: stated range with minimum 0 or 1 and maximum 3 or less, or 1+ years. Score 9.\n"
    "R8a: experience not stated AND role_keyword in title AND (entry_title OR company_tier is MNC). Score 8.\n"
    "R8b: stated minimum of 2 years AND role_keyword in title AND entry_title. Score 8.\n"
    "R7: role_keyword in title, none of the rules above. Score 7.\n"
    "R6: role keywords appear only in the description. Score 6.\n"
    "R5: no role keywords anywhere. Score 5.\n"
    "Use only these scores: 1, 2, 5, 6, 7, 8, 9, 10.\n\n"
    "OTHER RULES:\n"
    "- If the listing has an EXPERIENCE MENTIONS line, use it as the source for fresher_proof and experience_required.\n"
    "- Missing experience is NOT a reason to reject, but it never scores above 8.\n"
    "- Missing salary or missing posted_date is never a reason to reject.\n"
    "- experience_required: copy the wording exactly as written, or null. Never guess.\n"
    "- is_intern=true if: intern, internship, stipend, trainee, apprentice\n"
    "- skills_required must be a STRING, not array\n"
    "- domain: SOC/GRC/AppSec/VAPT/CloudSec/IAM/Forensics/Risk/Fraud-AML/General\n\n"
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
    kept, dropped = [], []
    for l in listings:
        (kept if is_relevant(l) else dropped).append(l)

    total_by_src = Counter(l.get("source", "?") for l in listings)
    kept_by_src = Counter(l.get("source", "?") for l in kept)
    logger.info("Pre-filter kept/total by source: %s",
                " | ".join(f"{s} {kept_by_src[s]}/{n}" for s, n in total_by_src.most_common()))

    loose = re.compile(r"secur|cyber|\bsoc\b|siem|grc|risk|compliance|fraud|kyc|aml|audit|privacy", re.I)
    suspects = [l for l in dropped if loose.search(l.get("title", ""))]
    logger.info("Pre-filter: %d/%d kept, %d dropped (%d dropped titles look relevant)",
                len(kept), len(listings), len(dropped), len(suspects))
    for l in suspects[:40]:
        logger.info("  dropped?: [%s] %s @ %s", l.get("source", "?"),
                    sanitize(l.get("title", ""))[:60], sanitize(l.get("company", ""))[:25])
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
    ULTRA-AGGRESSIVE title normalization to catch ALL fuzzy duplicates:
    'SOC L1 Analyst' → 'analyst center operations security'
    'USI-FY26-Cyber-Detect & Respond-SSA-SIEM' → 'cyber detect engineer respond siem'
    'Security Operations Center Analyst - L1' → 'analyst center operations security'
    'Cyber Operate-Detect & Respond-SA-SIEM Engineer' → 'cyber detect engineer respond siem'
    """
    if not title:
        return ""
    
    t = sanitize(title).lower().strip()
    
    # Remove year/FY tags FIRST (before other processing)
    t = re.sub(r'\b(fy|year|yr)\s*-?\s*\d{2,4}\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\b20\d{2}\b', '', t)
    
    # Remove location mentions
    t = re.sub(r'\b(bangalore|bengaluru|india|karnataka|blr|aus|australia|usa|uk)\b', '', t, flags=re.IGNORECASE)
    
    # Remove level/tier indicators AGGRESSIVELY
    t = re.sub(r'\b(l\d+|l-\d+|tier\s*\d+|level\s*\d+)\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\b(i|ii|iii|iv|v)\b', '', t)  # Roman numerals
    
    # Remove grade/band/seniority indicators AGGRESSIVELY
    # This is the KEY fix for Deloitte jobs
    t = re.sub(r'\b(ssa|lsa|sa|tsa|asa|msa)\b', '', t, flags=re.IGNORECASE)  # All "SA" variants
    t = re.sub(r'\b(associate|sr\.?|senior|junior|jr\.?|lead|principal|staff)\b', '', t, flags=re.IGNORECASE)
    
    # Remove company-specific prefixes
    t = re.sub(r'\busi\b', '', t, flags=re.IGNORECASE)
    t = re.sub(r'\bin_\b', '', t, flags=re.IGNORECASE)  # IN_Associate style
    
    # Remove duplicate words that appear due to formatting
    # "Cyber-Cyber Operate" → "Cyber Operate"
    # "CyberOperate" → "Cyber Operate" (add space before capital letters)
    t = re.sub(r'([a-z])([A-Z])', r'\1 \2', t)
    
    # Expand common abbreviations to match full forms
    EXPANSIONS = {
        r'\bsoc\b': 'security operations center',
        r'\bgrc\b': 'governance risk compliance',
        r'\biam\b': 'identity access management',
        r'\bpam\b': 'privileged access management',
        r'\bvapt\b': 'vulnerability assessment penetration testing',
        r'\bdfir\b': 'digital forensics incident response',
        r'\bappsec\b': 'application security',
        r'\bdevsecops\b': 'development security operations',
    }
    for abbr, full in EXPANSIONS.items():
        t = re.sub(abbr, full, t, flags=re.IGNORECASE)
    
    # Remove ALL punctuation and special characters
    t = re.sub(r'[^\w\s]', ' ', t)
    
    # Collapse multiple spaces
    t = re.sub(r'\s+', ' ', t).strip()
    
    # Remove common noise words BEFORE sorting
    NOISE_WORDS = {
        'the', 'a', 'an', 'and', 'or', 'for', 'in', 'at', 'to', 'of', 'with',
        'position', 'role', 'job', 'opening', 'opportunity', 'hiring', 'seeking',
        'new', 'urgent', 'immediate', 'apply', 'now'
    }
    words = [w for w in t.split() if w not in NOISE_WORDS and len(w) > 1]
    
    # Remove duplicate words (e.g., "cyber cyber" → "cyber")
    seen = set()
    unique_words = []
    for w in words:
        if w not in seen:
            seen.add(w)
            unique_words.append(w)
    
    # Sort alphabetically (so "Analyst SOC" matches "SOC Analyst")
    unique_words.sort()
    
    return ' '.join(unique_words)


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

GROQ_MODELS = [GROQ_MODEL, "openai/gpt-oss-120b"]
_EXHAUSTED: set[str] = set()

def call_groq(listing_text: str, retries: int = 3) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    safe = sanitize(listing_text)[:2000]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for model in GROQ_MODELS:
        if model in _EXHAUSTED:
            continue
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": USER_PROMPT_PREFIX + safe},
            ],
            "temperature": 0.1, "max_tokens": 1200,
            "reasoning_effort": "low",
            "response_format": {"type": "json_object"},
        }
        for attempt in range(1, retries + 1):
            try:
                r = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
                if r.status_code == 429:
                    logger.warning("Groq 429 on %s: %s", model, r.text[:200])
                    low = r.text.lower()
                    if "per day" in low or "(tpd)" in low or "(rpd)" in low:
                        logger.warning("Daily limit on %s — skipping it for the rest of this run", model)
                        _EXHAUSTED.add(model)
                        break
                    try:
                        retry_after = float(r.headers.get("retry-after", 0) or 0)
                    except ValueError:
                        retry_after = 0
                    time.sleep(min(max(retry_after + 1, 8 * attempt), 60))
                    continue
                r.raise_for_status()
                choice  = r.json()["choices"][0]
                content = (choice["message"].get("content") or "").strip()
                if not content:
                    logger.warning("Empty content on %s (finish_reason=%s)", model, choice.get("finish_reason"))
                    continue
                return content
            except requests.exceptions.Timeout:
                time.sleep(5 * attempt)
    raise RuntimeError("Groq failed on all models (rate limit or empty replies)")


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
_EXP_HINT_RE = re.compile(
    r"[^\n.]{0,50}\b(?:\d{1,2}\s*(?:-|to)\s*\d{1,2}|\d{1,2}\s*\+|\d{1,2})\s*(?:years?|yrs?)\b[^\n.]{0,50}"
    r"|[^\n.]{0,40}\b(?:freshers?|entry[- ]level|graduate trainee)\b[^\n.]{0,40}",
    re.IGNORECASE)


def experience_hint(description: str) -> str:
    hits = [m.group(0).strip() for m in _EXP_HINT_RE.finditer(sanitize(description))]
    return " | ".join(hits[:3])[:300]

ROLE_KW_RE = re.compile(
    r"\b(cyber\w*|security|soc|siem|grc|iam|vapt|pentest\w*|penetration|threat|vulnerabilit\w*|infosec|"
    r"appsec|devsecops|forensic\w*|malware|fraud|kyc|aml|sanctions?|compliance|risk|audit\w*|privacy|"
    r"data protection)\b", re.IGNORECASE)
SENIOR_HARD_RE = re.compile(
    r"\b(staff|principal|lead|head|director|manager|vp|vice president|chief|ciso|architect|supervisor)\b",
    re.IGNORECASE)
SENIOR_SOFT_RE = re.compile(
    r"\b(senior|sr)\b|\bgrade\s*[2-9]\b|\bl[2-5]\b"
    r"|\b(analyst|engineer|associate|specialist|consultant)\s+(ii|iii|iv|[2-5])\b", re.IGNORECASE)
FRESHER_PROOF_RE = re.compile(
    r"\b(freshers?|entry[- ]level|graduate trainee)\b|\b0\s*(?:-|to)\s*[12]\s*(?:years?|yrs?)",
    re.IGNORECASE)


def code_gate(listing: dict) -> str | None:
    title = sanitize(listing.get("title", ""))
    text = title + " " + sanitize(listing.get("description", ""))
    if SENIOR_HARD_RE.search(title):
        return "senior title"
    if SENIOR_SOFT_RE.search(title) and not FRESHER_PROOF_RE.search(text):
        return "senior level title, no fresher proof"
    return None


def score_listing(listing: dict) -> dict | None:
    listing_text = (
        "SOURCE: "       + sanitize(listing.get("source", ""))                        + "\n"
        "TITLE: "        + sanitize(listing.get("title", ""))                         + "\n"
        "COMPANY: "      + sanitize(listing.get("company", ""))                       + "\n"
        "LOCATION: "     + sanitize(listing.get("location", ""))                      + "\n"
        "DATE POSTED: "  + sanitize(listing.get("date_posted", ""))                   + "\n"
        "EXPERIENCE MENTIONS: " + experience_hint(listing.get("description", "")) + "\n"
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
        score = int(d.get("legitimacy_score", 1))
        rule = d.get("score_rule", "?")
        title_raw = sanitize(listing.get("title", ""))
        if score > 2 and not ROLE_KW_RE.search(title_raw):
            score, rule = min(score, 6), "capped: no role keyword in title"
        elif score > 2 and ai_tier == "startup":
            score, rule = 10, "R10a (override)"
        elif score > 2 and bool(d.get("is_intern")):
            score, rule = 10, "R10b (override)"

        result = {
            "scraped_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "job_title":            d.get("job_title")  or listing.get("title", ""),
            "company":              _merge_company(ai_name, ai_tier),
            "domain":               d.get("domain", "General"),
            "legitimacy_score":     score,
            "red_flags":            d.get("red_flags", []),
            "summary":              d.get("summary", ""),
            "is_intern":            bool(d.get("is_intern", False)),
            "experience_required":  d.get("experience_required") or "",
            "skills_required":      _skills_to_str(d.get("skills_required", "")),
            "salary_range":         d.get("salary_range") or "",
            "apply_url":            listing.get("job_url") or listing.get("url") or d.get("apply_url") or "",
            "score_rule":           rule,
            "posted_date":          d.get("posted_date") or "",
            "source":               listing.get("source", ""),
            "url":                  listing.get("job_url") or listing.get("url", ""),
            "job_id":               listing.get("job_id", ""),
            "source_company":       listing.get("company", ""),
            "status":               "New",
        }

        tag = "INTERN" if result["is_intern"] else "regular"
        logger.info("  [%s] %s @ %s | score=%d | %s | exp=%s",
                    tag,
                    result["job_title"][:35],
                    result["company"][:25],
                    result["legitimacy_score"],
                    rule,
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
def score_all(listings: list, min_score: int = 4, known_urls: set | None = None) -> list:
    relevant = pre_filter(listings)
    if not relevant:
        logger.info("Nothing relevant after pre-filter")
        return []

    max_age_days = int(os.environ.get("MAX_POST_AGE_DAYS", "21"))
    relevant = freshness_filter(relevant, max_age_days=max_age_days)
    if not relevant:
        logger.info("Nothing left after freshness filter")
        return []

    deduped = aggressive_deduplicate(relevant)

    # Cross-check against the sheet (saved rows) and the scored_log tab (rejected and duplicates)
    if known_urls is None:
        try:
            known_urls = load_known_urls()
        except Exception as exc:
            logger.warning("Could not load known URLs: %s", exc)
            known_urls = set()
    if known_urls:
        before = len(deduped)
        deduped = [l for l in deduped
                   if normalize_url(l.get("job_url") or l.get("url") or "") not in known_urls]
        logger.info("Skipped %d listings already in the sheet or scored_log (%d left)",
                    before - len(deduped), len(deduped))
    if not deduped:
        logger.info("Nothing left after deduplication")
        return []

    scored, rejected = [], []
    delay = float(os.environ.get("GROQ_SCORE_DELAY_SEC", "8"))
    logger.info("Scoring %d unique listings via Groq...", len(deduped))

    for i, listing in enumerate(deduped):
        if all(m in _EXHAUSTED for m in GROQ_MODELS):
            logger.warning("All models are at the daily limit, %d listings left unscored", len(deduped) - i)
            break
        url = listing.get("job_url") or listing.get("url") or ""
        title = sanitize(listing.get("title", ""))

        gate = code_gate(listing)
        if gate:
            logger.info("  GATE reject (%s): %s", gate, title[:55])
            rejected.append((url, 1, title))
            continue

        logger.info("Scoring %d/%d: %s", i + 1, len(deduped), title[:55])
        result = score_listing(listing)
        time.sleep(delay)          # after EVERY call: dropped listings use tokens too

        if result is None:
            continue
        if result["legitimacy_score"] < min_score:
            logger.info("  -> Dropped score=%d", result["legitimacy_score"])
            rejected.append((url, result["legitimacy_score"], title))
            continue
        scored.append(result)

    if rejected:
        try:
            log_scored_urls(rejected)
        except Exception as exc:
            logger.warning("Could not write scored_log: %s", exc)

    logger.info("Done: %d/%d passed", len(scored), len(deduped))
    return scored
