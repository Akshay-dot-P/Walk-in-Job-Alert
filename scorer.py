import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timezone
from config import GROQ_MODEL, TARGET_ROLES, INTERN_KEYWORDS, KNOWN_MNCS, MAX_AGE_DAYS, EXP_MIN_ALLOWED, EXP_MAX_ALLOWED, location_ok
from listing_filters import entry_level_gate_enabled, passes_entry_level_gate
from collections import Counter
from storage import normalize_url, load_known_urls, log_scored_urls
import time
import csv
import html as _html

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
    "- exp_ok: the stated experience range has a lower end of 0, 1 or 2 and an upper end of 5 or less, "
    "or no experience is stated.\n"
    "- role_keyword: title has a security word (cyber, security, soc, siem, vapt, cloud, iam, grc, threat, vulnerability, network, monitoring) "
    "OR an ops/risk word (fraud, aml, risk, compliance, audit, privacy, investigation, operations, noc, identity). "
    "Both kinds count equally.\n\n"
    "SCORING: apply the rules IN ORDER. The FIRST rule that matches decides the score. "
    "Put the matching id in score_rule.\n"
    "S2: training or registration fee, guaranteed placement, fake or spam listing, salary above 25 LPA for a fresher.\n"
    "S1: title has Director, Head, VP, Chief, CISO, Principal or Manager; OR exp_ok is false.\n"
    "R10a: company_tier is startup. Score 10.\n"
    "R10b: intern, trainee, apprentice, fixed term or contract role. Score 10.\n"
    "R9: stated range with lower end 0 to 2, or fresher or entry level. Score 9.\n"
    "R8: experience not stated AND role_keyword in title AND company_tier is MNC. Score 8.\n"
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






# ============================================================================
# JOB RULES
# ============================================================================
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

INCLUDE_AI_ROLES = True
SKIP_KYC_ONLY = True          # KYC only roles are skipped, "KYC AML" style titles are kept


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
            return 999 if "+" in low and n >= 30 else n      # "30+ Days Ago" is unbounded
        m = re.search(r"(\d+)\s*weeks?", low)
        if m:
            return int(m.group(1)) * 7
        return None
    if s.isdigit() and len(s) in (10, 13):                    # epoch seconds / millis (Lever)
        ts = int(s) / (1000 if len(s) == 13 else 1)
        return max(0, (now - datetime.fromtimestamp(ts, timezone.utc)).days)
    try:
        dt = datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except ValueError:
        pass
    try:                                                      # "Fri, 30 Oct 2026 20:29:01 GMT"
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, (now - dt).days)
    except (TypeError, ValueError):
        return None


# ---------- experience
_SLUG = _rx(r"-(\d{1,2})-to-(\d{1,2})-years")                 # naukri url slug
_RANGE = _rx(r"(?<![\d.])(\d{1,2})\s*(?:\+\s*)?(?:-|to)\s*(\d{1,2})\s*(?:\+\s*)?(?:years?|yrs?)\b")
_PLUS = _rx(r"(?<![\d.-])(\d{1,2})\s*\+\s*(?:years?|yrs?)\b")
_MINIMUM = _rx(r"\b(?:minimum|min\.?|at\s*least)\s*(?:of\s*)?(\d{1,2})\s*(?:years?|yrs?)\b")
_EXACT = _rx(r"(?<![\d.-])(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s*)?(?:relevant\s+|work\s+|professional\s+|total\s+|"
             r"hands[- ]on\s+|industry\s+|prior\s+)?(?:experience|exp)\b")
_FRESHER = _rx(r"\b(?:freshers?|entry[- ]level|no\s+experience|0\s+years?)\b")


def parse_experience(title: str = "", description: str = "", url: str = "") -> tuple[int, int | None] | None:
    """(min_years, max_years_or_None). None when the listing states nothing."""
    for text in (_clean(title),):
        m = _RANGE.search(text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = _PLUS.search(text) or _MINIMUM.search(text)
        if m:
            return int(m.group(1)), None
    m = _SLUG.search(_clean(url).lower())
    if m:
        return int(m.group(1)), int(m.group(2))
    d = _clean(description)[:3000]
    m = _RANGE.search(d)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _MINIMUM.search(d) or _PLUS.search(d) or _EXACT.search(d)
    if m:
        n = int(m.group(1))
        return n, (n if _EXACT.search(d) and not (_PLUS.search(d) or _MINIMUM.search(d)) else None)
    if _FRESHER.search(_clean(title) + " " + d[:600]):
        return 0, 0
    return None


def experience_ok(exp: tuple[int, int | None] | None) -> bool:
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
FRAUD = (r"fraud|aml|anti[\s-]?money|sanctions?|financial\s+crimes?|fincrime|investigat\w*|"
         r"trust\s*(?:&|and)\s*safety|transaction\s+monitoring|cdd|edd|screening")
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
    r"human\s+resources?|hr|recruit\w*|talent|marketing|sales|business\s+development|"
    r"customer\s+(?:success|support|service|experience)|learning|training|trainers?|instructor|facilitator|"
    r"tutor|coach|legal|counsel|paralegal|company\s+secretary|articleship|article(?:d)?|chartered|ca|"
    r"pharma\w*|clinical|patient|medical|drug|virology|hotel|resort|chef|restaurant|housekeeping|driver|"
    r"ground\s+staff|procurement|suppliers?|vendor\s+management|logistics|customs|trade\s+compliance|"
    r"product\s+compliance|material\s+compliance|regulatory\s+affairs|environmental|safety\s+officer|"
    r"supply\s+chain|p&c|litigation|capital\s+markets?|regulatory\s+reporting|wealth|equity|investment|"
    r"audit\s+assistant|middle\s+office|securities|security\s+(?:master|valuations?)|slp|loss\s+prevention|"
    r"physical\s+security|guard|watchman|facility|facilities|patrol|bouncer|armed|"
    r"cpu|gpu|asic|vlsi|rtl|fpga|silicon|semiconductor|firmware|embedded|hardware)\b")
SOFT_JUNK = _rx(
    r"\b(?:software|developer|sde|sdet|full[\s-]?stack|front[\s-]?end|back[\s-]?end|web|android|ios|java|"
    r"data\s+(?:scientist|engineer|science)|machine\s+learning|ml|applied\s+scientist|research\s+scientist|"
    r"quant\w*|actuar\w*|devops|sre|site\s+reliability|platform\s+engineer|cloud\s+(?:engineer|developer|"
    r"consultant|architect|operations)|solutions?\s+architect|qa|quality|test\s+engineer|production\s+support|"
    r"business\s+intelligence|\bbi\b)\b")
SOFT_JUNK_EXEMPT = _rx(r"devsecops|appsec|security\s+(?:automation|testing|engineer|analyst)\b")
HOSPITALITY = _rx(r"marriott|hilton|hyatt|ritz|radisson|sheraton|taj\b|itc\s+hotels|accor|oberoi|leela|lemon\s+tree")
SKIP_KYC = _rx(r"\bkyc\b")
KYC_ALSO_OK = _rx(r"\b(?:aml|fraud|sanctions?|investigat\w*|financial\s+crimes?)\b")
# fraud / AML / KYC style roles must not be finance, accounting or CA roles
FRAUD_FAMILY = _rx(rf"\b(?:{FRAUD}|kyc)\b")
FINANCE_CA_TITLE = _rx(r"\b(?:ca|cpa|cma|acca|chartered|accountants?|accounting|accounts?|articled?|articleship|"
                       r"statutory|gst|tds|bookkeep\w*|ledger|reconciliations?|treasury|valuation|claims?|"
                       r"financial\s+(?:reporting|statements|accounting|audit)|finance|forensic\s+accounting)\b")
FINANCE_CA_DESC = _rx(r"\b(?:chartered\s+accountants?|icai|cpa|acca|cma|articleship|statutory\s+audit|financial\s+statements|"
                      r"general\s+ledger|bookkeeping|accounts\s+(?:payable|receivable)|"
                      r"ca\s+(?:inter|final|qualified|freshers?|preferred|required)|qualified\s+ca)\b")
PROFILE_OR_PAGE = _rx(r"^\d[\d,]*\+?\s+\w.*\bjobs?\b|\bjobs?\s+in\s+|log in or sign up|\bsign up\b|join now|"
                      r"\bcompany profile\b|\bjobs at\b|\bcareers at\b|\s-\s*linkedin$|iimjobs|"
                      r"^\W*(?:the\s+)?numbers to watch")
BPO_VOICE = _rx(r"\bbpo\b|voice\s+process|inbound|outbound|international\s+voice")

# seniority
ALWAYS_OUT = _rx(r"\b(?:director|vice\s+president|vp|avp|assistant\s+vice\s+president|head\s+of|head|chief|ciso|"
                 r"principal|distinguished|president|manager|mgr|supervisor|deputy)\b")
LEAD_ARCH = _rx(r"\b(?:lead|architect|archt)\b")
STAFF_ROLE = _rx(r"\bstaff\s+(?:\w+\s+){0,3}(?:engineer|developer|scientist|architect|analyst|researcher)\b")
SENIOR_WORD = _rx(r"\b(?:senior|sr)\b\.?")
SENIOR_ASSOC = _rx(r"\b(?:senior|sr)\.?\s+associate\b")
SENIOR_OK_DOMAIN = _rx(r"\b(?:risk|forensic\w*|investigat\w*|audit\w*|compliance|governance|fraud|aml|privacy)\b")
ROMAN_HIGH = _rx(r"\b(?:iv|v|vi)\s*$|\b(?:engineer|analyst|specialist|consultant)\s+(?:iv|v|vi|[4-9])\b|\bl[4-9]\b")

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
    exp: tuple[int, int | None] | None = None


def _relevant(title: str, job_type: str) -> bool:
    if CORE.search(title):
        return True
    if NETWORK_ROLE.search(title) and HAS_ROLE_WORD.search(title):
        return True
    return job_type == "Apprenticeship"            # a variety of apprenticeship roles


def evaluate(listing: dict) -> Verdict:
    title = _clean(listing.get("title"))
    desc = _clean(listing.get("description"))
    company = _clean(listing.get("company"))
    url = _clean(listing.get("job_url") or listing.get("url"))
    t = re.sub(r"[_/]+", " ", title.lower())     # "TDR_SOC/SIEM" -> "tdr soc siem"

    jt = classify_job_type(title, desc)
    exp = parse_experience(title, desc, url)

    def out(reason: str) -> Verdict:
        return Verdict(False, reason, jt, exp)

    if "linkedin.com/in/" in url.lower():
        return out("url:linkedin_profile")
    if len(t) < 4 or PROFILE_OR_PAGE.search(t):
        return out("title:page_or_headline")
    if BPO_VOICE.search(t):
        return out("title:bpo_voice")
    if not location_ok(listing.get("location")):
        return out("location")
    age = posted_age_days(listing.get("date_posted") or listing.get("posted_date"))
    if age is not None and age > MAX_AGE_DAYS:
        return out(f"age>{MAX_AGE_DAYS}d")

    m = HARD_JUNK.search(t)
    if m:
        word = re.sub(r"\s+", " ", m.group(0).lower())
        if not (word == "supply chain" and re.search(r"\b(?:risk|security|cyber)\b", t)):   # "Supply Chain Risk Analyst" is a target
            return out("junk:" + word)
    if HOSPITALITY.search(company.lower()):
        return out("junk:hospitality")
    if SOFT_JUNK.search(t) and not SOFT_JUNK_EXEMPT.search(t):
        return out("junk:" + SOFT_JUNK.search(t).group(0).lower())
    if SKIP_KYC_ONLY and SKIP_KYC.search(t) and not KYC_ALSO_OK.search(t):
        return out("user_skip:kyc")
    if FRAUD_FAMILY.search(t) and (FINANCE_CA_TITLE.search(t) or FINANCE_CA_DESC.search(desc.lower())):
        return out("finance_ca")
    if not _relevant(t, jt):
        return out("no_relevant_keyword")

    # seniority
    if ALWAYS_OUT.search(t):
        return out("senior:" + ALWAYS_OUT.search(t).group(0).lower())
    ehr = exp is not None and experience_ok(exp)                 # explicit range that passes
    if (LEAD_ARCH.search(t) or STAFF_ROLE.search(t)) and not ehr:
        return out("senior:lead_architect_staff")
    if ROMAN_HIGH.search(t) and not ehr:
        return out("senior:level_IV_plus")
    if SENIOR_WORD.search(t) and not (SENIOR_ASSOC.search(t) or ehr or SENIOR_OK_DOMAIN.search(t)):
        return out("senior:no_range")

    # experience
    if not experience_ok(exp):
        lo, hi = exp
        return out(f"experience:{lo}-{hi if hi is not None else ''}")

    return Verdict(True, "", jt, exp)


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
    if str(listing.get("source", "")).lower() == "linkedin_post":     # post phrases only apply to LinkedIn posts
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
        if score > 2 and not (CORE.search(title_raw) or NETWORK_ROLE.search(title_raw)):
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
            "job_type":             listing.get("job_type") or "Job",
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

    scored, rejected = [], []
    delay = float(os.environ.get("GROQ_SCORE_DELAY_SEC", "8"))
    logger.info("Scoring %d unique listings via Groq...", len(deduped))

    for i, listing in enumerate(deduped):
        if all(m in _EXHAUSTED for m in GROQ_MODELS):
            logger.warning("All models are at the daily limit, %d listings left unscored", len(deduped) - i)
            for rest in deduped[i:]:
                _audit("unscored", "all Groq models at daily limit", rest)
            break
        url = listing.get("job_url") or listing.get("url") or ""
        title = sanitize(listing.get("title", ""))

        # LinkedIn search results carry no description, so fetch it for NEW listings only
        if str(listing.get("source", "")).lower() == "linkedin" and not listing.get("description"):
            desc = fetch_linkedin_description(url)
            if desc:
                listing["description"] = desc
                time.sleep(2)
        verdict = evaluate(listing)
        listing["job_type"] = verdict.job_type
        if not verdict.keep:
            logger.info("  RULES reject (%s): %s", verdict.reason, title[:55])
            rejected.append((url, 1, title))
            continue

        logger.info("Scoring %d/%d: %s", i + 1, len(deduped), title[:55])
        result = score_listing(listing)
        time.sleep(delay)          # after EVERY call: dropped listings use tokens too

        if result is None:
            _audit("score_error", "AI call/JSON failed (see error log)", listing)
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
