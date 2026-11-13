import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS, KNOWN_MNCS, MAX_AGE_DAYS, EXP_MIN_ALLOWED, EXP_MAX_ALLOWED, location_ok, title_location_ok
from listing_filters import entry_level_gate_enabled, passes_entry_level_gate
from collections import Counter
from storage import normalize_url, load_known_urls, log_scored_urls
import time
import csv
import html as _html
import KNOWN_COMPANY_TITLES, _ct_key 


logger = logging.getLogger(__name__)


AUDIT_FILE = os.environ.get("SKIP_AUDIT_FILE", "skipped_audit.csv")
_AUDIT: list[dict] = []

def _audit(stage: str, reason: str, listing: dict) -> None:
    _AUDIT.append({
        "stage":       stage,
        "reason":      reason,
        "source":      listing.get("source", ""),
        "title":       sanitize(listing.get("title", ""))[:120],
        "company":     sanitize(listing.get("company", ""))[:60],
        "date_posted": listing.get("date_posted") or listing.get("posted_date") or "",
        "url":         listing.get("job_url") or listing.get("url") or "",
    })

def flush_audit() -> None:
    if not _AUDIT:
        return
    by_stage = Counter(r["stage"] for r in _AUDIT)
    logger.info("SKIP AUDIT: %s", " | ".join(f"{s}={n}" for s, n in by_stage.most_common()))
    try:
        with open(AUDIT_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(_AUDIT[0].keys()))
            w.writeheader()
            w.writerows(_AUDIT)
        logger.info("Skip audit written to %s (%d rows)", AUDIT_FILE, len(_AUDIT))
    except OSError as exc:
        logger.warning("Could not write skip audit: %s", exc)
    _AUDIT.clear()
    
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You output only raw valid JSON. "
    "No markdown. No explanation. No preamble. No code fences."
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






# ============================================================================
# JOB RULES
# ============================================================================

# ============================================================================
# JOB RULES (v2)  - replaces everything from the old "JOB RULES" header down to is_relevant()
# ============================================================================
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

INCLUDE_AI_ROLES = True


def _clean(text) -> str:
    if text is None:
        return ""
    s = str(text)
    if s.lower() in ("nan", "none", "nat"):
        return ""
    return s.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-").strip()


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


# ---------- age
def posted_age_days(value, now: datetime | None = None) -> int | None:
    """Age in days from any date string the scrapers produce. None means unknown (keep)."""
    now = now or datetime.now(timezone.utc)
    s = _clean(value)
    if not s:
        return None
    low = s.lower()
    if low.startswith("posted"):
        if "today" in low or "just" in low:
            return 0
        if "yesterday" in low:
            return 1
        m = re.search(r"(\d+)\s*\+?\s*days?", low)
        if m:
            n = int(m.group(1))
            return 999 if "+" in low and n >= 30 else n
        m = re.search(r"(\d+)\s*weeks?", low)
        if m:
            return int(m.group(1)) * 7
        return None
    if s.isdigit() and len(s) in (10, 13):
        ts = int(s) / (1000 if len(s) == 13 else 1)
        return max(0, (now - datetime.fromtimestamp(ts, timezone.utc)).days)
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except (TypeError, ValueError):
        return None


# ---------- experience
_SLUG = _rx(r"-(\d{1,2})-to-(\d{1,2})-years")
_RANGE = _rx(r"(?<![\d.])(\d{1,2})\s*(?:\+\s*)?(?:-|to)\s*(\d{1,2})\s*(?:\+\s*)?(?:years?|yrs?)\b")
_PLUS = _rx(r"(?<![\d.-])(\d{1,2})\s*\+\s*(?:years?|yrs?)\b")
_MINIMUM = _rx(r"\b(?:minimum|min\.?|at\s*least)\s*(?:of\s*)?(\d{1,2})\s*(?:years?|yrs?)\b")
_EXACT = _rx(r"(?<![\d.-])(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s+|work\s+|professional\s+|total\s+|"
             r"hands[- ]on\s+|industry\s+|prior\s+)?(?:experience|exp)\b")
_FRESHER = _rx(r"\b(?:freshers?|entry[- ]level|no\s+experience|0\s+years?)\b")
_PREF_BEFORE = _rx(r"(?:prefer\w*|nice[\s-]to[\s-]have|good[\s-]to[\s-]have|bonus|desired|optional|an?\s+(?:plus|advantage))[^.;]{0,50}$")
_PREF_AFTER = _rx(r"\b(?:preferred|desired|is\s+a\s+plus|a\s+plus|nice\s+to\s+have|bonus)\b")


def _exp_candidates(text: str) -> list:
    """Every experience requirement in the text, in document order. Skips preferred-only lines and company boilerplate."""
    found = []
    for kind, rx in (("range", _RANGE), ("plus", _PLUS), ("min", _MINIMUM), ("exact", _EXACT)):
        for m in rx.finditer(text):
            lo = int(m.group(1))
            hi = int(m.group(2)) if kind == "range" else (lo if kind == "exact" else None)
            if lo >= 15 or (hi is not None and (hi < lo or hi > 40)):
                continue                                   # "25+ years in business" style boilerplate
            if _PREF_BEFORE.search(text[max(0, m.start() - 70):m.start()]) or _PREF_AFTER.search(text[m.end():m.end() + 35]):
                continue
            found.append((m.start(), lo, hi, m.group(0).strip()))
    found.sort()
    return found


def parse_experience_full(title: str = "", description: str = "", url: str = ""):
    """((min_years, max_years_or_None) or None, matched text)."""
    t = _clean(title)
    m = _RANGE.search(t)
    if m:
        return (int(m.group(1)), int(m.group(2))), m.group(0)
    m = _PLUS.search(t) or _MINIMUM.search(t)
    if m:
        return (int(m.group(1)), None), m.group(0)
    m = _SLUG.search(_clean(url).lower())
    if m:
        return (int(m.group(1)), int(m.group(2))), m.group(0).strip("-")
    d = _clean(description)[:30000]               # the requirement is often past the first 5000 characters
    found = _exp_candidates(d)
    if found:
        _, lo, hi, src = found[0]
        return (lo, hi), src
    if _FRESHER.search(t + " " + d[:600]):
        return (0, 0), "fresher"
    return None, ""


def parse_experience(title: str = "", description: str = "", url: str = ""):
    return parse_experience_full(title, description, url)[0]


def experience_ok(exp) -> bool:
    if exp is None:
        return True
    lo, hi = exp
    if lo > EXP_MIN_ALLOWED:
        return False
    return hi is None or hi <= EXP_MAX_ALLOWED


# ---------- vocabulary
SEC = (r"cirt|csirt|cert|cyber\w*|infosec|information\s+security|security|secops|soc|siem|xsiam|edr|mdr|xdr|soar|threats?|"
       r"vulnerabilit\w*|vapt|pentest\w*|penetration|red\s*team\w*|blue\s*team|malware|forensic\w*|dfir|"
       r"incident\s+(?:response|management)|intrusion|detection|appsec|devsecops|cryptograph\w*|pki|dlp|purview|"
       r"encryption|sase|ztna|waf|e-?discovery")
IAM = (r"iam|idam|iga|pam|identity|access\s+management|sailpoint|saviynt|cyberark|okta|delinea|beyondtrust|"
       r"entra(?:\s+id)?|active\s+directory|ping\s*(?:identity|federate|one)|sod|segregation\s+of\s+duties|"
       r"fusion\s+security|sso|mfa|privileged\s+access|ciam")
GRC = (r"grc|governance|compliance|risk|audit\w*|(?<!quality\s)assurance|itgc|sox|privacy|dpdp|gdpr|"
       r"data\s+protection|tprm|due\s+diligence|resilience|business\s+continuity|bcm|bcp|disaster\s+recovery|"
       r"iso\s*2\d{3}")
FRAUD = (r"fraud|investigat\w*|trust\s*(?:&|and)\s*safety|screening")
NET = r"network\w*|noc|wlan|wi-?fi|wireless|firewall|sd-?wan|l2\s+l3|vpn"
ITOPS = (r"it\s+(?:support|operations?|consulting|consultant|helpdesk|service\s*desk)|information\s+technology\s+analyst|"
         r"(?:technical|tech)\s+support|help\s*desk|service\s*desk|m365|microsoft\s+365|intune")
AI = (r"ai\s+(?:red\s*team\w*|security|governance|safety|operations?|implementation|risk|trust|engineer|analyst)|"
      r"(?:gen|generative)\s*ai\s+(?:security|governance)|llm\s+(?:security|red\s*team)|responsible\s+ai")
ROLE_WORD = (r"engineer|analyst|administrator|consultant|specialist|operations?|support|associate|executive|"
             r"technician|operator|researcher|tester|hunter|developer|officer|professional|coordinator|"
             r"automation|intern|trainee|apprentice")

CORE = _rx(rf"\b(?:{SEC}|{IAM}|{GRC}|{FRAUD}|{ITOPS}|noc|l2\s+l3)\b" + (rf"|\b(?:{AI})\b" if INCLUDE_AI_ROLES else ""))
NETWORK_ROLE = _rx(rf"\b(?:{NET})\b")
HAS_ROLE_WORD = _rx(rf"\b(?:{ROLE_WORD})\b")

HARD_JUNK = _rx(
    r"\b(?:tax(?:ation)?|payroll|accountants?|accounts?\s+(?:payable|receivable)|fp&a|treasury|valuation|"
    r"investment\s+banking|private\s+equity|finance|financial\s+(?:analyst|data|controller|reporting|planning)|"
    r"human\s+resources?|hr|recruit\w*|(?<!early\s)talent|marketing|sales|business\s+development|"
    r"customer\s+(?:success|support|service|experience)|learning|training|trainers?|instructor|facilitator|"
    r"tutor|coach|legal|counsel|paralegal|company\s+secretary|articleship|article(?:d)?|chartered|ca|"
    r"pharma\w*|clinical|patient|medical|drug|virology|hotel|resort|chef|restaurant|housekeeping|driver|"
    r"ground\s+staff|procurement|suppliers?|vendor\s+management|logistics|customs|trade\s+compliance|"
    r"product\s+compliance|material\s+compliance|regulatory\s+affairs|environmental|safety\s+officer|"
    r"supply\s+chain|p&c|litigation|capital\s+markets?|regulatory\s+reporting|wealth|equity|investment|"
    r"audit\s+assistant|middle\s+office|securities|security\s+(?:master|valuations?)|slp|loss\s+prevention|"
    r"physical\s+security|guard|watchman|facility|facilities|patrol|bouncer|armed|"
    r"account\s+executives?|pre[\s-]?sales|insurance|financial\s+(?:security\s+)?advis\w+|bids?\s*(?:&|and)\s*proposals?|"
    r"cpu|gpu|hpc|asic|vlsi|rtl|fpga|silicon|firmware|hardware|network\s+transport)\b")
SOFT_JUNK = _rx(
    r"\b(?:software|developer|sde|sdet|full[\s-]?stack|front[\s-]?end|back[\s-]?end|web|android|ios|java|"
    r"data\s+(?:scientist|engineer|science)|machine\s+learning|ml|applied\s+scientist|research\s+scientist|"
    r"quant\w*|actuar\w*|devops|sre|site\s+reliability|platform\s+engineer|cloud\s+(?:engineer|developer|"
    r"consultant|architect|operations)|solutions?\s+architect|qa|quality|test\s+engineer|production\s+support|"
    r"business\s+intelligence|\bbi\b)\b")
SOFT_JUNK_EXEMPT = _rx(r"appsec|pentest\w*|penetration|vapt|ethical\s+hack\w*|red\s*team\w*|"
                       r"security\s+(?:automation|test(?:ing)?|engineer|analyst|tester)\b")
HOSPITALITY = _rx(r"marriott|hilton|hyatt|ritz|radisson|sheraton|taj\b|itc\s+hotels|accor|oberoi|leela|lemon\s+tree")

# your exclusions (each returns "junk:<name>" so the audit shows which one fired)
LANG_JUNK = _rx(r"\b(?:bilingual|french|spanish|german|italian|dutch|portuguese|japanese|mandarin|cantonese|chinese|korean|arabic|"
                r"hebrew|russian|polish|turkish|swedish|danish|norwegian|finnish|thai|vietnamese|bahasa|indonesian|nordic)\b"
                r"|\b(?!english\b|kannada\b|hindi\b)[a-z]+\s+speaking\b")
HW_SOC = _rx(r"\bsoc\b.*\b(?:logic|design|verification|validation|functional|silicon|rtl|dft|bring[\s-]?up|physical|hardware|firmware|asic|fpga)\b"
             r"|\b(?:logic|design|verification|validation|silicon|rtl|dft|asic|fpga)\b.*\bsoc\b|\bsystem[\s-]on[\s-]chip\b")
FIN_RISK = _rx(r"\b(?:financial|market|credit|liquidity|counterparty|interest[\s-]rate|investment|treasury|trading|fx)\s+risk\b"
               r"|\brisk\s+model\w*|\bmodel\s+risk\b")
FIN_RISK_DESC = _rx(r"\b(?:credit|market|liquidity|counterparty|financial|interest[\s-]rate)\s+risk\b")
AML_KYC = _rx(r"\b(?:aml|kyc|cdd|edd|odd|ubo|fcrm)\b|anti[\s-]?money|money\s+laundering|\bsanctions?\b|financial\s+crimes?|"
              r"\bfincrime\b|transaction\s+monitoring|customer\s+due\s+diligence")
HW_WORDS = _rx(r"\b(?:semiconductor|embedded)\b")
HW_KEEP = _rx(r"\b(?:cyber\w*|security|infosec|vulnerabilit\w*|risk|compliance|audit\w*)\b")
EXTRA_JUNK = [
    ("field", _rx(r"\bfield\b")),
    ("researcher", _rx(r"\bresearchers?\b")),
    ("hw_soc", HW_SOC),
    ("language", LANG_JUNK),
    ("fin_risk", FIN_RISK),
    ("aml_kyc", AML_KYC),
]
DEVSECOPS = _rx(r"devsecops")                  # kept for internships only
SEC_WORDS = _rx(r"\b(?:cyber\w*|security|infosec|information|technology|tech|it)\b")
INDIA_EVIDENCE = _rx(r"\b(?:india|bengaluru|bangalore|karnataka|blr|remote|work\s+from\s+home|wfh|worldwide|anywhere|global|apac)\b")
CLEARANCE = _rx(r"security\s+clear(?:ed|ance)|\bsecret\s+clear|\bsc[\s-]cleared\b|\bdv[\s-]cleared\b|uk\s+(?:address|residen)|"
                r"\bbpss\b|eligible\s+for\s+(?:uk\s+)?(?:sc|dv)\b|(?:us|u\.s\.)\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|only)|"
                r"must\s+be\s+(?:a\s+)?(?:us|u\.s\.)\s+citizen")

# fraud style roles must not be finance, accounting or CA roles
FRAUD_FAMILY = _rx(rf"\b(?:{FRAUD})\b")
FINANCE_CA_TITLE = _rx(r"\b(?:ca|cpa|cma|acca|chartered|accountants?|accounting|accounts\s+(?:payable|receivable|executive)|"
                       r"articled?|articleship|statutory|gst|tds|bookkeep\w*|ledger|reconciliations?|treasury|valuation|claims?|"
                       r"financial\s+(?:reporting|statements|accounting|audit)|finance|forensic\s+accounting)\b")
FINANCE_CA_DESC = _rx(r"\b(?:chartered\s+accountants?|icai|cpa|acca|cma|articleship|statutory\s+audit|financial\s+statements|"
                      r"general\s+ledger|bookkeeping|accounts\s+(?:payable|receivable)|"
                      r"ca\s+(?:inter|final|qualified|freshers?|preferred|required)|qualified\s+ca)\b")

# Naukri only: generic risk titles need a cyber/IT word
NAUKRI_RISK = _rx(r"\brisk\b")
NAUKRI_RISK_KEEP = _rx(r"\b(?:cyber\w*|security|infosec|information|it|technology|tech|grc|isms)\b")

PROFILE_OR_PAGE = _rx(r"^\d[\d,]*\+?\s+\w.*\bjobs?\b|\bjobs\s+in\s+|log in or sign up|\bsign up\b|join now|"
                      r"\bcompany profile\b|\bjobs at\b|\bcareers at\b|\s-\s*linkedin$|iimjobs|"
                      r"^\d[\d,]*\+?\s+.*\binternships\b|^\W*(?:the\s+)?numbers to watch")
BPO_VOICE = _rx(r"\bbpo\b|voice\s+process|inbound|outbound|international\s+voice")

# seniority
ALWAYS_OUT = _rx(r"\b(?:director|vice\s+president|vp|avp|assistant\s+vice\s+president|head\s+of|head|chief|ciso|"
                 r"principal|distinguished|president|manager|mgr|supervisor|deputy|officer)\b")
LEAD_ARCH = _rx(r"\b(?:lead|architect|archt)\b")
STAFF_ROLE = _rx(r"\bstaff\s+(?:\w+\s+){0,3}(?:engineer|developer|scientist|architect|analyst|researcher)\b")
SENIOR_WORD = _rx(r"\b(?:senior|sr)\b\.?")
SENIOR_ASSOC = _rx(r"\b(?:senior|sr)\.?\s+associate\b")
SENIOR_OK_DOMAIN = _rx(r"\b(?:risk|forensic\w*|investigat\w*|audit\w*|compliance|governance|fraud|privacy)\b")
# level 2 and above is out ("Engineer II", "Analyst-3", "Specialist 2", "L2"); "Associate 2" is a PwC entry grade and stays
_LV_ROLE = r"(?:engineer|analyst|specialist|consultant|researcher|architect|administrator|developer|scientist|tester|operator|technician|investigator|executive|designer)s?"
LEVEL_HIGH = _rx(rf"\b{_LV_ROLE}\s*[-:(]?\s*(?:ii|iii|iv|v|vi|vii|viii|[2-9])\b(?!\s*(?:\+|[-]\s*\d|to\s+\d|years?|yrs?|months?))"
                 r"|\b(?:grade|level|tier)\s*[-:]?\s*[2-9]\b|\bl[2-9]\b")

TYPE_WALKIN = _rx(r"walk[\s-]?ins?\b")
TYPE_INTERN = _rx(r"\bintern(?:s|ship|ships)?\b")
TYPE_APPRENTICE = _rx(r"\bapprentice\w*")
TYPE_TRAINEE = _rx(r"\b(?:trainee|graduate\s+(?:program|programme|trainee)|early\s+career|new\s+grad)\b")
TYPE_FTC = _rx(r"\b(?:ftc|fixed[\s-]?term|temporary)\b")
TYPE_CONTRACT = _rx(r"\b(?:contract|contractor|contractual|c2h|freelance)\b")
CONTRACT_FALSE = _rx(r"contract\s+(?:management|manager|administrat\w*|services?|specialist|analyst|lifecycle|"
                     r"compliance|negotiat\w*|law|lawyer|counsel|review\w*)|vendor\s*/\s*contract")
CONTRACT_DESC = _rx(r"contract\s+(?:role|position|basis|job|duration|to\s+hire)|contractual\s+(?:role|position|basis)|"
                    r"\d+\s*months?\s+contract")


def classify_job_type(title: str, description: str = "") -> str:
    t, d = _clean(title), _clean(description)[:1500]
    if TYPE_INTERN.search(t):
        return "Internship"
    if TYPE_APPRENTICE.search(t):
        return "Apprenticeship"
    if TYPE_TRAINEE.search(t):
        return "Trainee"
    if TYPE_FTC.search(t) or _rx(r"\bfixed[\s-]?term\b|\bftc\b").search(d):
        return "FTC"
    if (TYPE_CONTRACT.search(t) and not CONTRACT_FALSE.search(t)) or CONTRACT_DESC.search(d):
        return "Contract"
    if TYPE_WALKIN.search(t) or _rx(r"walk[\s-]?in\s+(?:drive|interview)").search(d):
        return "Walk-in"
    return "Job"


@dataclass
class Verdict:
    keep: bool
    reason: str = ""
    job_type: str = "Job"
    exp: tuple | None = None
    exp_src: str = ""


def _relevant(title: str, job_type: str) -> bool:
    if CORE.search(title):
        return True
    if NETWORK_ROLE.search(title) and HAS_ROLE_WORD.search(title):
        return True
    return job_type == "Apprenticeship"


def _business_junk(t: str) -> bool:
    if re.search(r"\bbusiness\s+analyst", t):
        return True
    return bool(re.search(r"\bbusiness\b", t) and not re.search(r"\bbusiness\s+continuity\b", t) and not SEC_WORDS.search(t))


def evaluate(listing: dict) -> Verdict:
    title = _clean(listing.get("title"))
    desc = _clean(listing.get("description"))
    company = _clean(listing.get("company"))
    url = _clean(listing.get("job_url") or listing.get("url"))
    t = re.sub(r"[_/]+", " ", title.lower())
    if str(listing.get("source", "")).lower() == "linkedin_post" or "internshala" in t:
        t = re.sub(r"\s+-\s+internshala.*$", "", t)
        t = re.split(r"\s+at\s+", t, maxsplit=1)[0]    

    jt = classify_job_type(title, desc)
    exp, exp_src = parse_experience_full(title, desc, url)

    def out(reason: str) -> Verdict:
        return Verdict(False, reason, jt, exp, exp_src)

    if "linkedin.com/in/" in url.lower():
        return out("url:linkedin_profile")
    if len(t) < 4 or PROFILE_OR_PAGE.search(t):
        return out("title:page_or_headline")
    if BPO_VOICE.search(t):
        return out("title:bpo_voice")
    if not title_location_ok(title):
        return out("location:title")
    loc = _clean(listing.get("location")) + (" remote" if listing.get("is_remote") else "")
    if not location_ok(loc):
        return out("location")
    if loc.strip() and not INDIA_EVIDENCE.search(loc):
        return out("location:no_india")        
    age = posted_age_days(listing.get("date_posted") or listing.get("posted_date"))
    if age is not None and age > MAX_AGE_DAYS:
        return out(f"age>{MAX_AGE_DAYS}d")
    if CLEARANCE.search(title + " " + desc):
        return out("clearance")

    m = HARD_JUNK.search(t)
    if m:
        word = re.sub(r"\s+", " ", m.group(0).lower())
        if not (word == "supply chain" and re.search(r"\b(?:risk|security|cyber)\b", t)):
            return out("junk:" + word)
    if HOSPITALITY.search(company.lower()):
        return out("junk:hospitality")
    for name, rx in EXTRA_JUNK:
        if rx.search(t):
            return out("junk:" + name)
    if HW_WORDS.search(t) and not HW_KEEP.search(t):
        return out("junk:hardware")        
    if _business_junk(t):
        return out("junk:business")
    if DEVSECOPS.search(t) and jt != "Internship":
        return out("junk:devsecops")
    if SOFT_JUNK.search(t) and not SOFT_JUNK_EXEMPT.search(t):
        return out("junk:" + SOFT_JUNK.search(t).group(0).lower())
    if FRAUD_FAMILY.search(t) and (FINANCE_CA_TITLE.search(t) or FINANCE_CA_DESC.search(desc.lower())):
        return out("finance_ca")
    if re.search(r"\brisk\b", t) and not SEC_WORDS.search(t) and len(FIN_RISK_DESC.findall(desc[:6000])) >= 2:
        return out("junk:fin_risk_desc")
    if str(listing.get("source", "")).lower() == "naukri":
        if NAUKRI_RISK.search(t) and not NAUKRI_RISK_KEEP.search(t):
            return out("naukri_skip:risk")
    if not _relevant(t, jt):
        return out("no_relevant_keyword")

    # seniority
    if ALWAYS_OUT.search(t):
        return out("senior:" + ALWAYS_OUT.search(t).group(0).lower())
    ehr = exp is not None and experience_ok(exp)
    if (LEAD_ARCH.search(t) or STAFF_ROLE.search(t)) and not ehr:
        return out("senior:lead_architect_staff")
    if SENIOR_WORD.search(t) and not (SENIOR_ASSOC.search(t) or ehr or SENIOR_OK_DOMAIN.search(t)):
        return out("senior:no_range")
    if LEVEL_HIGH.search(t) and not re.search(r"\bl1\b", t):
        return out("senior:level_2_plus")

    # experience
    if not experience_ok(exp):
        lo, hi = exp
        return out(f"experience:{lo}-{hi if hi is not None else ''}")

    return Verdict(True, "", jt, exp, exp_src)


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


def drop_reason(listing: dict) -> str | None:
    """None means keep, otherwise a short code saying which rule dropped it."""
    title = sanitize(listing.get("title", ""))
    desc = sanitize(listing.get("description", ""))
    if str(listing.get("source", "")).lower() == "linkedin_post":
        text = (title + " " + desc).lower()
        for p in CONTENT_REJECTS:
            if p in text:
                return f"content_reject:{p}"
    v = evaluate({**listing, "title": title, "description": desc})
    return None if v.keep else v.reason



def is_relevant(listing: dict) -> bool:
    return drop_reason(listing) is None

# =============================================================================
# PRE-FILTER
# =============================================================================





def reject_reason(listing: dict) -> str | None:
    """Pre-filter now uses the rules engine (evaluate/drop_reason)."""
    return drop_reason(listing)

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
    hit = next((p for p in CONTENT_REJECTS if p in combined), None)
    if hit:
        return f"CONTENT_REJECTS: {hit!r}"

    DOMAIN_REJECTS = [
        "vlsi", "embedded systems", "mechanical engineer",
        "civil engineer", "electrical engineer",
        "accounts receivable", "accounts payable",
        "content writer", "graphic designer", "ux designer",
        "customer support", "customer service", "call center",
        "supply chain", "logistics", "teacher", "professor",
        "medical officer", "nurse", "chartered accountant",
    ]
    hit = next((p for p in DOMAIN_REJECTS if p in title), None)
    if hit:
        return f"DOMAIN_REJECTS: {hit!r}"

    if re.search(r'\b(1[0-9]|20)\+?\s*years?\b', title):
        return "10+ years in title"

    if entry_level_gate_enabled():
        if not passes_entry_level_gate(
            sanitize(listing.get("title", "")),
            sanitize(listing.get("description", "")),
        ):
            return "entry_level_gate failed"
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
        hit = next((p for p in SENIOR_REJECTS if p in title), None)
        if hit and not any(p in title for p in SENIOR_EXCEPTIONS):
            return f"SENIOR_REJECTS: {hit!r}"

    has_role_in_title   = any(r in title for r in TARGET_ROLES)
    has_intern_in_title = any(k in title for k in INTERN_KEYWORDS)
    has_sec_in_combined = any(k in combined for k in [
        "security", "cyber", "risk", "compliance", "grc", "audit",
        "fraud", "kyc", "aml", "privacy", "cloud", "network",
        "forensic", "malware", "threat", "vulnerability",
    ])
    if has_role_in_title or (has_intern_in_title and has_sec_in_combined):
        return None
    return "no TARGET_ROLE in title (and no intern+security combo)"


def pre_filter(listings: list) -> list:
    kept, dropped = [], []
    for l in listings:
        reason = reject_reason(l)
        if reason is None:
            kept.append(l)
        else:
            dropped.append(l)
            _audit("pre_filter", reason, l)

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

def freshness_filter(listings: list[dict], max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    kept: list[dict] = []
    for l in listings:
        age = posted_age_days(l.get("date_posted") or l.get("posted_date"))
        if age is not None and age > max_age_days:
            continue
        kept.append(l)
    logger.info("Freshness filter (max_age_days=%d): %d/%d kept, %d dropped",
                max_age_days, len(kept), len(listings), len(listings) - len(kept))
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
    seen_keys: dict[str, dict] = {}
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
        if job_id and company_norm:
            candidate_keys.append(f"id:{company_norm}:{job_id}")
        
        # Key 2: Exact URL (catches reposts at same link)
        if url:
            candidate_keys.append(f"url:{url}")
        
        # Key 3: Company + normalized title (FUZZY - main dedup logic)
        if company_norm and title_norm:
            candidate_keys.append(f"ct:{company_norm}:{title_norm}")
        
        # Key 4: Just normalized title if company unknown (risky but catches more)
        # Only use if title is specific enough (>= 3 words after normalization)
        elif title_norm and len(title_norm.split()) >= 3:
            candidate_keys.append(f"t:{title_norm}")
        
        hit = next((k for k in candidate_keys if k in seen_keys), None)
        if hit:
            first = seen_keys[hit]
            _audit("dedup",
                   f"key={hit[:70]} | KEPT: {sanitize(first.get('title',''))[:50]}"
                   f" @ {sanitize(first.get('company',''))[:25]}", l)
            continue

        for k in candidate_keys:
            seen_keys[k] = l
        deduped.append(l)
    
    removed = len(listings) - len(deduped)
    logger.info("AGGRESSIVE dedup: %d → %d (removed %d duplicates)",
                len(listings), len(deduped), removed)
    
    return deduped


# =============================================================================
# GROQ CALL
# =============================================================================

GROQ_MODELS = list(dict.fromkeys([GROQ_MODEL, "openai/gpt-oss-120b"]))
_EXHAUSTED: set[str] = set()
_TOKEN_CAP: dict[str, int] = {}      # smaller max_tokens once a model's daily budget is nearly gone
_USAGE: dict[str, dict] = {}         # what each model actually used this run
DEFAULT_MAX_TOKENS = 1200
MIN_USABLE_TOKENS = 450              # below this the JSON reply can get cut off
_TPD_RE = re.compile(r"Limit\s+(\d+),\s*Used\s+(\d+),\s*Requested\s+(\d+)", re.I)


def _note_usage(model: str, usage: dict) -> None:
    u = _USAGE.setdefault(model, {"calls": 0, "prompt": 0, "completion": 0, "cached": 0})
    u["calls"] += 1
    u["prompt"] += int(usage.get("prompt_tokens") or 0)
    u["completion"] += int(usage.get("completion_tokens") or 0)
    u["cached"] += int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)


def call_groq(listing_text: str, retries: int = 3) -> str:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    safe = sanitize(listing_text)[:2000]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    for model in GROQ_MODELS:
        if model in _EXHAUSTED:
            continue
        squeezed = False
        for attempt in range(1, retries + 1):
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": USER_PROMPT_PREFIX + safe},
                ],
                "temperature": 0.1,
                "max_tokens": _TOKEN_CAP.get(model, DEFAULT_MAX_TOKENS),
                "response_format": {"type": "json_object"},
            }
            if model.startswith("openai/gpt-oss"):
                payload["reasoning_effort"] = "low"          # llama models reject this parameter
            try:
                r = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=45)
            except requests.exceptions.RequestException:
                time.sleep(5 * attempt)
                continue

            if r.status_code == 429:
                logger.warning("Groq 429 on %s: %s", model, r.text[:200])
                low = r.text.lower()
                if "per day" in low or "(tpd)" in low or "(rpd)" in low:
                    m = _TPD_RE.search(r.text)
                    if m and "(tpd)" in low and not squeezed:
                        limit, used, requested = map(int, m.groups())
                        prompt_tokens = max(requested - payload["max_tokens"], 0)
                        room = limit - used - prompt_tokens - 50
                        if MIN_USABLE_TOKENS <= room < payload["max_tokens"]:
                            squeezed = True
                            _TOKEN_CAP[model] = room
                            logger.info("%s: %d tokens left today, retrying with max_tokens=%d",
                                        model, limit - used, room)
                            continue
                    logger.warning("Daily limit on %s, skipping it for the rest of this run", model)
                    _EXHAUSTED.add(model)
                    break
                try:
                    retry_after = float(r.headers.get("retry-after", 0) or 0)
                except ValueError:
                    retry_after = 0
                time.sleep(min(max(retry_after + 1, 8 * attempt), 60))
                continue

            if r.status_code >= 400:
                logger.warning("Groq HTTP %s on %s: %s", r.status_code, model, r.text[:160])
                if r.status_code == 404:                     # model missing or no access: skip it for this run
                    _EXHAUSTED.add(model)
                    break
                if r.status_code < 500:
                    break                                    # this model rejected the request: try the next one
                time.sleep(3 * attempt)
                continue

            data = r.json()
            choice = data["choices"][0]
            content = (choice["message"].get("content") or "").strip()
            _note_usage(model, data.get("usage") or {})
            if not content:
                logger.warning("Empty content on %s (finish_reason=%s)", model, choice.get("finish_reason"))
                continue
            return content
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


# ============================================================================
# SCORING (v2): the score is computed in code, the LLM only extracts facts
# ============================================================================
INTERN_TYPES = ("Internship", "Apprenticeship", "Trainee")
NON_FULLTIME = INTERN_TYPES + ("FTC", "Contract")
SCAM_RE = _rx(r"(?:training|registration|joining|security|refundable)\s+(?:fee|deposit)|pay\s+(?:rs|inr|₹)|guaranteed\s+(?:job|placement)|"
              r"send\s+money|processing\s+fee|whatsapp\s+(?:us|me|number)|earn\s+(?:up\s+to\s+)?(?:rs|inr|₹)")

_DOMAIN_RULES = [
    ("IAM",       r"\b(?:iam|idam|iga|pam|identity|sailpoint|saviynt|cyberark|okta|delinea|entra|active\s+directory|privileged\s+access|ciam|access\s+management|sod)\b"),
    ("Fraud-AML", r"\b(?:fraud|investigat\w*|trust\s*(?:&|and)\s*safety)\b"),
    ("Forensics", r"\b(?:forensic\w*|dfir|e-?discovery)\b"),
    ("VAPT",      r"\b(?:vapt|pentest\w*|penetration|red\s*team\w*|ethical\s+hack\w*|vulnerabilit\w*)\b"),
    ("AppSec",    r"\b(?:appsec|application\s+security|product\s+security|secure\s+code|devsecops|api\s+security)\b"),
    ("CloudSec",  r"\bcloud\b"),
    ("GRC",       r"\b(?:grc|governance|compliance|audit\w*|privacy|itgc|sox|assurance|tprm|third\s+party|vendor\s+risk|isms|iso\s*2\d{3}|resilience|bcm|bcp|data\s+protection|gdpr|dpdp|due\s+diligence)\b"),
    ("Risk",      r"\brisk\b"),
    ("SOC",       r"\b(?:soc|siem|threat\w*|detection|incident|mdr|xdr|edr|secops|monitoring|noc|network\w*|firewall|wlan|security|cyber\w*|infosec)\b"),
]
_DOMAIN_RX = [(d, _rx(p)) for d, p in _DOMAIN_RULES]


def domain_of(title: str) -> str:
    t = re.sub(r"[_/]+", " ", _clean(title))
    for name, rx in _DOMAIN_RX:
        if rx.search(t):
            return name
    return "General"


_REMOTE_TXT = _rx(r"\b(?:remote|work\s+from\s+home|wfh)\b")
_HYBRID_TXT = _rx(r"\bhybrid\b")


def work_mode_of(listing: dict) -> str:
    head = " ".join([_clean(listing.get("title")), _clean(listing.get("location"))])
    if listing.get("is_remote") or _REMOTE_TXT.search(head):
        return "Remote"
    if _HYBRID_TXT.search(head + " " + _clean(listing.get("description"))[:600]):
        return "Hybrid"
    return "On-site"


def fmt_experience(exp) -> str:
    if exp is None:
        return ""
    lo, hi = exp
    if (lo, hi) == (0, 0):
        return "Fresher"
    if hi is None:
        return f"{lo}+ years"
    return f"{lo} years" if lo == hi else f"{lo}-{hi} years"


def rule_score(listing: dict, v: Verdict, tier: str, scam: bool) -> tuple[int, str]:
    """The rubric that used to live in the prompt, evaluated in code so it is identical on every run."""
    if scam:
        return 2, "S2"
    title = sanitize(listing.get("title", ""))
    title_hit = bool(CORE.search(title) or NETWORK_ROLE.search(title))
    desc_hit = bool(CORE.search(sanitize(listing.get("description", ""))[:1500]))
    exp = v.exp
    if tier == "startup":
        return 10, "R10a"
    if v.job_type in NON_FULLTIME:
        return 10, "R10b"
    if exp is not None and exp[0] <= 2:
        return 9, "R9"
    if exp is None and title_hit and tier == "MNC":
        return 8, "R8"
    if title_hit:
        return 7, "R7"
    if desc_hit:
        return 6, "R6"
    return 5, "R5"


def _iso_date(value) -> str:
    s = _clean(value)
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:
        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


USER_PROMPT_PREFIX = (
    "You extract facts from ONE Indian job listing. Return ONLY a JSON object, nothing else.\n"
    "Keys: "
    '{"job_title": "clean title", "company": "company name or empty", '
    '"company_tier": "MNC or startup or mid-tier or unknown", "summary": "one sentence", '
    '"skills_required": "comma separated string or empty", "salary_range": "e.g. 4-6 LPA or empty", '
    '"posted_date": "YYYY-MM-DD or empty", "is_scam": false, "red_flags": []}\n'
    "Never guess. Use an empty string when the listing does not say. "
    "company_tier is startup only for small funded startups. "
    "is_scam is true ONLY for explicit training or registration fees, guaranteed placement, or a fake listing.\n\n"
    "LISTING:\n"
)


def score_listing(listing: dict, verdict: Verdict | None = None, use_llm: bool = True) -> dict | None:
    verdict = verdict or evaluate(listing)
    title = sanitize(listing.get("title", ""))
    d: dict = {}
    if use_llm:
        listing_text = (
            "TITLE: "       + title                                              + "\n"
            "COMPANY: "     + sanitize(listing.get("company", ""))               + "\n"
            "LOCATION: "    + sanitize(listing.get("location", ""))              + "\n"
            "DATE POSTED: " + sanitize(listing.get("date_posted", ""))           + "\n"
            "DESCRIPTION:\n" + sanitize(listing.get("description", ""))[:1200]
        )
        try:
            raw = call_groq(listing_text)
            raw = re.sub(r"```json\s*|```\s*", "", raw)
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1:
                raise ValueError("No JSON in response")
            d = json.loads(raw[start:end + 1])
        except Exception as e:
            logger.warning("LLM extraction failed for '%s' (%s), saving with code only fields", title[:40], e)
            d = {}

    company_raw = _clean(listing.get("company"))
    ai_name = _clean(d.get("company")) or company_raw
    ai_tier = _resolve_tier(ai_name, d.get("company_tier", "unknown"))
    text_for_scam = title + " " + sanitize(listing.get("description", ""))
    scam = bool(d.get("is_scam")) and bool(SCAM_RE.search(text_for_scam))
    score, rule = rule_score(listing, verdict, ai_tier, scam)
    is_intern = verdict.job_type in INTERN_TYPES
    flags = d.get("red_flags", []) if isinstance(d.get("red_flags"), list) else []
    if use_llm and not d:
        flags = flags + ["llm_unavailable"]

    result = {
        "scraped_at":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "job_title":            _clean(d.get("job_title")) or title,
        "company":              _merge_company(ai_name, ai_tier),
        "domain":               domain_of(title),
        "legitimacy_score":     score,
        "red_flags":            flags,
        "summary":              _clean(d.get("summary")) or f"{title} at {ai_name or 'an unnamed company'}.",
        "is_intern":            is_intern,
        "experience_required":  fmt_experience(verdict.exp),
        "skills_required":      _skills_to_str(d.get("skills_required", "")),
        "salary_range":         _clean(d.get("salary_range")),
        "apply_url":            listing.get("job_url") or listing.get("url") or "",
        "score_rule":           rule,
        "posted_date":          _iso_date(listing.get("date_posted")) or _iso_date(d.get("posted_date")),
        "source":               listing.get("source", ""),
        "url":                  listing.get("job_url") or listing.get("url", ""),
        "job_id":               listing.get("job_id", ""),
        "source_company":       company_raw,
        "status":               "New",
        "job_type":             verdict.job_type,
        "location":             _clean(listing.get("location")),
        "work_mode":            work_mode_of(listing),
    }
    logger.info("  [%s] %s @ %s | score=%d | %s | exp=%s | %s",
                "INTERN" if is_intern else "regular", result["job_title"][:35], result["company"][:25],
                score, rule, result["experience_required"] or "?", result["domain"])
    return result
 








_LINKEDIN_BLOCKED = False


def fetch_linkedin_description(job_url: str) -> str:
    """One request per NEW listing against LinkedIn's public guest endpoint. '' on any failure."""
    global _LINKEDIN_BLOCKED
    if _LINKEDIN_BLOCKED:
        return ""
    m = re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", job_url or "")
    if not m:
        return ""
    try:
        r = requests.get(
            f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=15)
    except requests.RequestException:
        return ""
    if r.status_code in (429, 999):
        _LINKEDIN_BLOCKED = True
        logger.warning("LinkedIn description fetch blocked (HTTP %s), skipping it for the rest of this run",
                       r.status_code)
        return ""
    if r.status_code != 200:
        return ""
    block = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", r.text, re.S)
    body = block.group(1) if block else r.text
    text = _html.unescape(re.sub(r"<[^>]+>", " ", body))
    return re.sub(r"\s+", " ", text).strip()[:3000]




def _interleave_by_source(listings: list) -> list:
    """Round-robin across sources so LinkedIn can't use the whole daily token budget first."""
    buckets: dict[str, list] = {}
    for l in listings:
        key = str(l.get("source") or "?").lower() + ("/remote" if l.get("is_remote") else "")
        buckets.setdefault(key, []).append(l)
    n = len(buckets)
    ordered: list = []
    while buckets:
        for key in list(buckets):
            ordered.append(buckets[key].pop(0))
            if not buckets[key]:
                del buckets[key]
    logger.info("Scoring order: round-robin across %d source buckets", n)
    return ordered



# =============================================================================











# SCORE ALL - WITH AGGRESSIVE DEDUP
# =============================================================================
def _score_all_impl(listings: list, min_score: int = 4, known_urls: set | None = None) -> list:
    relevant = pre_filter(listings)
    if not relevant:
        logger.info("Nothing relevant after pre-filter")
        return []

    max_age_days = int(os.environ.get("MAX_POST_AGE_DAYS", str(MAX_AGE_DAYS)))
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
        fresh = []
        for l in deduped:
            if normalize_url(l.get("job_url") or l.get("url") or "") in known_urls:
                _audit("known_url", "already in sheet or scored_log", l)
            else:
                fresh.append(l)
        deduped = fresh
        logger.info("Skipped %d listings already in the sheet or scored_log (%d left)",
                    before - len(deduped), len(deduped))
    if not deduped:
        logger.info("Nothing left after deduplication")
        return []
    deduped = _interleave_by_source(deduped)
    scored, rejected = [], []
    delay = float(os.environ.get("GROQ_SCORE_DELAY_SEC", "8"))
    logger.info("Scoring %d unique listings via Groq...", len(deduped))

    for i, listing in enumerate(deduped):
        url = listing.get("job_url") or listing.get("url") or ""
        title = sanitize(listing.get("title", ""))
        ck = _ct_key(str(listing.get("company", "")), str(listing.get("title", "")))
        if ck and ck in KNOWN_COMPANY_TITLES:
            _audit("known_company_title", "same company and title already in the sheet", listing)
            rejected.append((url, 0, title))
            continue        
        # LinkedIn search results carry no description, so fetch it for NEW listings only
        if str(listing.get("source", "")).lower() == "linkedin" and not listing.get("description"):
            desc = fetch_linkedin_description(url)
            if desc:
                listing["description"] = desc
                time.sleep(2)
        verdict = evaluate(listing)
        listing["job_type"] = verdict.job_type
        if not verdict.keep:
            snippet = f" [{verdict.exp_src}]" if verdict.reason.startswith("experience") and verdict.exp_src else ""
            logger.info("  RULES reject (%s)%s: %s", verdict.reason, snippet, title[:55])
            _audit("rules_after_fetch", verdict.reason + snippet, listing)
            rejected.append((url, 1, title))
            continue

        llm_ok = not all(m in _EXHAUSTED for m in GROQ_MODELS)
        logger.info("Scoring %d/%d: %s%s", i + 1, len(deduped), title[:55],
                    "" if llm_ok else " (rules only, Groq quota used up)")
        result = score_listing(listing, verdict, use_llm=llm_ok)
        if llm_ok:
            time.sleep(delay)

        if result is None:
            _audit("score_error", "scoring failed (see error log)", listing)
            continue
        if result["legitimacy_score"] < min_score:
            logger.info("  -> Dropped score=%d", result["legitimacy_score"])
            _audit("scored_low", f"score={result['legitimacy_score']} rule={result['score_rule']}", listing)
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



def score_all(listings: list, min_score: int = 4, known_urls: set | None = None) -> list:
    try:
        return _score_all_impl(listings, min_score, known_urls)
    finally:
        flush_audit()
        for m, u in _USAGE.items():
            logger.info("Groq usage %s: %s", m, u)
