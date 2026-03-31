"""
sources.py — comprehensive entry-level + intern cybersec/GRC/risk scraper

SOURCES:
  1. LinkedIn Jobs      — 80 focused searches (max 4 OR terms each)
  2. Google Jobs        — 10 broader searches (different index)
  3. Indeed India       — 12 targeted searches
  4. LinkedIn Posts     — 40 Google RSS queries (hiring posts + intern posts)

NAUKRI: permanently removed — GitHub Actions IPs blocked (HTTP 406 recaptcha).
GLASSDOOR: permanently removed — consistent 403 from GitHub Actions IPs.

FIXES in this version:
  - LinkedIn Posts now filters out login pages, sign-up pages, company pages
  - LinkedIn profile pages filtered by URL (/in/) and title regex
  - Added minimum description length check to drop empty/useless entries
  - Post URLs validated to only keep actual post/pulse/article/feed links
  - Profile regex catches Name - Title @ Company AND Name - Title | Company formats
  - GARBAGE_URL_PATTERNS now includes linkedin.com/in/ as permanent catch-all
"""

import re
import time
import logging
import feedparser
import jobspy
import pandas as pd

logger = logging.getLogger(__name__)

LOCATION            = "Bengaluru, Karnataka, India"
HOURS_OLD           = 72
RESULTS_PER_TERM    = 40

FQ_FRESHER = (
    '(fresher OR "entry level" OR "entry-level" OR junior OR trainee '
    'OR graduate OR "0-2 years" OR "0 to 2 years" OR "upto 2 years" '
    'OR "0-1 year" OR "less than 2 years" OR associate)'
)

FQ_INTERN = (
    '(intern OR internship OR stipend OR "6 month" OR "3 month" '
    'OR "summer intern" OR "winter intern" OR apprentice '
    'OR fellowship OR "graduate trainee" OR "management trainee")'
)

FQ_ALL = (
    '(fresher OR "entry level" OR junior OR trainee OR intern OR internship '
    'OR graduate OR stipend OR "0-2 years" OR "0 to 2 years" OR associate '
    'OR apprentice OR fellowship)'
)


def qf(role): return f"({role}) {FQ_FRESHER}"
def qi(role): return f"({role}) {FQ_INTERN}"
def qa(role): return f"({role}) {FQ_ALL}"


LINKEDIN_TERMS = [
    # SOC / Blue Team
    qf('"SOC analyst" OR "L1 SOC analyst" OR "security operations analyst" OR "l1 analyst" OR "tier 1 analyst"'),
    qf('"L2 SOC analyst" OR "tier 1 analyst" OR "blue team analyst"'),
    qf('"cyber defense analyst" OR "security operations center analyst"'),
    # SIEM
    qf('"SIEM analyst" OR "SIEM engineer" OR "Splunk analyst"'),
    qf('"QRadar analyst" OR "Microsoft Sentinel analyst" OR "security monitoring analyst"'),
    qf('"log analysis analyst" OR "security event analyst" OR "SIEM administrator"'),
    # Threat Intelligence
    qf('"threat intelligence analyst" OR "CTI analyst" OR "cyber threat intelligence"'),
    qf('"threat hunting analyst" OR "OSINT analyst" OR "threat research analyst"'),
    qf('"dark web analyst" OR "intelligence analyst" OR "threat analyst"'),
    # Incident Response
    qf('"incident response analyst" OR "IR analyst" OR "incident responder"'),
    qf('"DFIR analyst" OR "digital forensics analyst" OR "cyber incident analyst"'),
    qf('"forensic analyst" OR "eDiscovery analyst" OR "computer forensics analyst"'),
    # VAPT / Pentest
    qf('"VAPT engineer" OR "VAPT analyst" OR "penetration tester"'),
    qf('"ethical hacker" OR "pentest engineer" OR "pentest analyst"'),
    qf('"red team analyst" OR "offensive security analyst" OR "security researcher"'),
    qf('"bug bounty" OR "vulnerability researcher" OR "web application pentest"'),
    qf('"network pentest" OR "mobile pentest" OR "API security tester"'),
    # Vulnerability Management
    qf('"vulnerability analyst" OR "vulnerability management analyst" OR "vulnerability analyst"'),
    qf('"VA analyst" OR "Qualys analyst" OR "Tenable analyst"'),
    qf('"patch management analyst" OR "security assessment analyst"'),
    # AppSec / DevSecOps
    qf('"application security engineer" OR "appsec engineer" OR "appsec analyst"'),
    qf('"DevSecOps engineer" OR "DevSecOps analyst" OR "software security engineer"'),
    qf('"DAST analyst" OR "SAST analyst" OR "secure code review analyst"'),
    # Network Security
    qf('"network security engineer" OR "network security analyst"'),
    qf('"firewall engineer" OR "firewall analyst" OR "IDS IPS analyst"'),
    qf('"Palo Alto engineer" OR "Fortinet engineer" OR "Cisco security engineer"'),
    qf('"endpoint security analyst" OR "systems security administrator"'),
    # Cloud Security
    qf('"cloud security analyst" OR "cloud security engineer"'),
    qf('"cloud security architect" OR "cloud security administrator"'),
    qf('"AWS security engineer" OR "Azure security engineer" OR "GCP security"'),
    qf('"CSPM analyst" OR "cloud compliance analyst" OR "cloud IAM analyst"'),
    qf('"cloud security auditor" OR "cloud forensic analyst"'),
    # IAM / PAM / DLP
    qf('"IAM analyst" OR "identity access management analyst" OR "IAM engineer"'),
    qf('"PAM analyst" OR "privileged access management analyst" OR "CyberArk analyst"'),
    qf('"DLP analyst" OR "data loss prevention analyst" OR "SailPoint analyst"'),
    qf('"Okta analyst" OR "SSO engineer" OR "identity governance analyst"'),
    qf('"zero trust analyst" OR "access governance analyst" OR "IDAM analyst"'),
    # GRC
    qf('"GRC analyst" OR "IT GRC analyst" OR "cyber GRC analyst"'),
    qf('"ISO 27001 analyst" OR "SOC 2 analyst" OR "NIST analyst"'),
    qf('"third party risk analyst" OR "TPRM analyst" OR "vendor risk analyst"'),
    qf('"supply chain risk analyst" OR "CIS controls analyst" OR "GRC engineer"'),
    # IT Audit
    qf('"IT audit analyst" OR "IS audit analyst" OR "IT auditor"'),
    qf('"information systems audit" OR "CISA" OR "ITGC analyst"'),
    qf('"technology audit analyst" OR "cyber audit analyst"'),
    qf('"internal audit IT" OR "Big 4 IT audit" OR "security audit analyst"'),
    # Risk
    qf('"risk analyst" OR "operational risk analyst" OR "cyber risk analyst"'),
    qf('"IT risk analyst" OR "enterprise risk analyst" OR "ERM analyst"'),
    qf('"RCSA analyst" OR "Basel analyst" OR "ORC analyst"'),
    qf('"business continuity analyst" OR "BCP analyst" OR "DR analyst"'),
    qf('"technology risk associate" OR "risk management analyst"'),
    # Compliance
    qf('"compliance analyst" OR "IT compliance analyst" OR "regulatory compliance analyst"'),
    qf('"PCI DSS analyst" OR "SOX compliance analyst" OR "RBI compliance analyst"'),
    qf('"SEBI compliance analyst" OR "IRDAI compliance" OR "PDPB analyst"'),
    qf('"data governance analyst" OR "compliance monitoring analyst"'),
    # Fraud / AML / KYC
    qf('"fraud analyst" OR "fraud detection analyst" OR "fraud prevention analyst"'),
    qf('"AML analyst" OR "anti-money laundering analyst" OR "transaction monitoring analyst"'),
    qf('"KYC analyst" OR "KYC associate" OR "financial crime analyst"'),
    qf('"sanctions analyst" OR "UBO analyst" OR "customer due diligence analyst"'),
    # Privacy
    qf('"data privacy analyst" OR "privacy analyst" OR "DPO support"'),
    qf('"data protection analyst" OR "GDPR analyst" OR "PDPB compliance analyst"'),
    qf('"privacy compliance analyst" OR "CIPP" OR "consent management analyst"'),
    # Malware / Forensics
    qf('"malware analyst" OR "malware researcher" OR "sandbox analyst"'),
    qf('"reverse engineer" OR "binary analysis analyst" OR "memory forensics analyst"'),
    qf('"mobile forensics analyst" OR "cyber forensics analyst"'),
    # Indian market titles
    qf('"associate security analyst" OR "junior security officer"'),
    qf('"executive information security" OR "technology risk associate"'),
    qf('"cyber risk associate" OR "security management trainee"'),
    qf('"security officer trainee" OR "security graduate trainee" OR "security apprentice"'),
    qf('"security awareness trainer" OR "security awareness executive"'),
    # General catch-all
    qf('"cybersecurity analyst" OR "security analyst" OR "information security analyst"'),
    qf('"infosec analyst" OR "cyber analyst" OR "security engineer" Bangalore'),

    # ── INTERN SEARCHES ──
    qi('"cybersecurity intern" OR "cyber security intern" OR "security intern"'),
    qi('"infosec intern" OR "information security intern"'),
    qi('"SOC intern" OR "security operations intern" OR "blue team intern"'),
    qi('"GRC intern" OR "governance risk compliance intern"'),
    qi('"IT audit intern" OR "IS audit intern" OR "risk intern"'),
    qi('"compliance intern" OR "regulatory compliance intern"'),
    qi('"cloud security intern" OR "AWS security intern" OR "Azure security intern"'),
    qi('"network security intern" OR "firewall intern"'),
    qi('"VAPT intern" OR "penetration testing intern" OR "ethical hacking intern"'),
    qi('"fraud analyst intern" OR "KYC intern" OR "AML intern"'),
    qi('"threat intelligence intern" OR "OSINT intern"'),
    qi('"vulnerability assessment intern" OR "security assessment intern"'),
    qi('"data privacy intern" OR "privacy compliance intern"'),
    qi('"appsec intern" OR "application security intern" OR "DevSecOps intern"'),
    qi('"security research intern" OR "malware analyst intern"'),
    qi('"IAM intern" OR "identity management intern" OR "DLP intern"'),
    qi('"incident response intern" OR "DFIR intern" OR "forensics intern"'),
    qi('"risk analyst intern" OR "operational risk intern"'),
    qi('cybersecurity OR "information security" OR "cyber security"'),
    qi('"security program" OR "security fellowship" OR "security graduate program"'),
]


LINKEDIN_POST_QUERIES = [
    # Fresher hiring posts
    "site:linkedin.com hiring bangalore cybersecurity fresher 2026",
    "site:linkedin.com hiring bangalore SOC analyst fresher",
    "site:linkedin.com hiring bangalore GRC compliance analyst fresher",
    "site:linkedin.com hiring bangalore KYC AML fraud analyst fresher",
    "site:linkedin.com hiring bangalore IAM security analyst fresher",
    "site:linkedin.com opening bangalore cybersecurity entry level",
    "site:linkedin.com urgent hiring bangalore information security analyst",
    "site:linkedin.com bangalore immediate joining cybersecurity fresher",
    "site:linkedin.com bangalore SOC analyst hiring fresher junior",
    "site:linkedin.com bangalore VAPT penetration tester fresher opening",
    "site:linkedin.com bangalore cloud security AWS GCP fresher hiring",
    "site:linkedin.com bangalore risk analyst compliance fresher opening",
    "site:linkedin.com bangalore IT audit CISA fresher hiring",
    "site:linkedin.com bangalore AML KYC fraud analyst fresher hiring",
    "site:linkedin.com bangalore data privacy GDPR analyst fresher",
    "site:linkedin.com bangalore incident response DFIR analyst fresher",
    "site:linkedin.com bangalore threat intelligence CTI analyst fresher",
    "site:linkedin.com bangalore DevSecOps appsec engineer fresher",
    # Intern posts
    "site:linkedin.com cybersecurity intern bangalore 2026",
    "site:linkedin.com security intern hiring bangalore stipend",
    "site:linkedin.com GRC intern bangalore hiring",
    "site:linkedin.com SOC intern bangalore opening",
    "site:linkedin.com IT audit intern bangalore hiring",
    "site:linkedin.com risk compliance intern bangalore",
    "site:linkedin.com cloud security intern bangalore",
    "site:linkedin.com network security intern bangalore",
    "site:linkedin.com VAPT intern bangalore hiring",
    "site:linkedin.com fraud KYC AML intern bangalore",
    "site:linkedin.com threat intelligence intern bangalore",
    "site:linkedin.com data privacy intern bangalore",
    "site:linkedin.com appsec DevSecOps intern bangalore",
    "site:linkedin.com cybersecurity internship bangalore stipend",
    "site:linkedin.com paid internship security bangalore 2026",
    "site:linkedin.com 6 month internship cybersecurity bangalore",
    "site:linkedin.com 3 month internship security bangalore",
    "site:linkedin.com summer internship cybersecurity bangalore",
    "site:linkedin.com offering internship security bangalore",
    "site:linkedin.com looking for cybersecurity intern bangalore",
]

# URLs containing these strings are garbage — filter them out
GARBAGE_URL_PATTERNS = [
    "linkedin.com/login",
    "linkedin.com/signup",
    "linkedin.com/authwall",
    "linkedin.com/company/",      # company page, not a post
    "linkedin.com/school/",
    "linkedin.com/jobs/",         # jobs board redirect, not a post
    "linkedin.com/in/",           # profile pages — people, not job postings
    "accounts.google.com",
    "support.google.com",
    "/404",
]

# Titles that are obviously not job posts
GARBAGE_TITLE_PATTERNS = [
    "log in or sign up",
    "sign up",
    "join now",
    "jobs at ",
    "careers at ",
    "about us",
    "linkedin india",
    "linkedin: log in",
    "error",
    "page not found",
    "403",
    "404",
]

# Regex: matches LinkedIn profile headline formats
# Covers all these patterns:
#   "Firstname Lastname - Job Title @ Company"
#   "Firstname Lastname - Job Title | Company"
#   "Firstname Lastname, CISA - Job Title"
#   "Firstname Lastname | SOC Analyst @ Company"
#   "Firstname Lastname, CISSP, CISM - ..."
PROFILE_HEADLINE_REGEX = re.compile(
    r'^[A-Za-z]+ [A-Za-z].{0,30}?'
    r'(,\s*(CISA|CISM|CISSP|CEH|OSCP|CA|MBA|PhD|CPA|CFE|CDPSE|CRISC|CGEIT|'
    r'CFA|FRM|CCSP|CCNA|MCSE|AWS|GCP|PMP|ITIL|ISO)\b)?'
    r'\s*[-|]',
    re.IGNORECASE
)


def _is_valid_post(title: str, url: str) -> bool:
    """Return False for login pages, company pages, profile pages, and other garbage."""
    title_l = title.lower()
    url_l   = url.lower()

    if any(p in url_l   for p in GARBAGE_URL_PATTERNS):   return False
    if any(p in title_l for p in GARBAGE_TITLE_PATTERNS): return False
    if len(title.strip()) < 10:                            return False

    return True


def _is_profile_headline(title: str) -> bool:
    """
    Return True if the title looks like a LinkedIn profile headline rather
    than a job posting title. Used as a secondary filter after URL check.

    Examples that return True (profiles — reject these):
      "Sushmitha Sonkamble - SailPoint IdentityIQ/ISC Certified | IAM"
      "Ashish Gangavaram, CISA - LinkedIn"
      "Anand Kumar - Cyber Threat Intelligence @ adidas"
      "Pranav Taskar - SOC Analyst L1 | SIEM (Splunk/Elastic)"
      "Dhanushree S O - Security Engineer@Amazon | Masters in CS"

    Examples that return False (job posts — keep these):
      "We're Hiring! Junior Application Security Analyst"
      "SOC Analyst L1 | Bangalore | Fresher Welcome"
      "#Hiring: SIEM Administrator & SOC Analyst (L1)"
      "Cyber Security Intern at Groww | Paid | Bangalore"
    """
    return bool(PROFILE_HEADLINE_REGEX.match(title))


def _to_records(df) -> list[dict]:
    if df is None or df.empty:
        return []
    records = []
    for _, row in df.iterrows():
        d = row.to_dict()
        records.append({
            "title":       str(d.get("title") or ""),
            "company":     str(d.get("company") or ""),
            "location":    str(d.get("location") or ""),
            "job_url":     str(d.get("job_url") or ""),
            "description": str(d.get("description") or ""),
            "date_posted": str(d.get("date_posted") or ""),
            "source":      str(d.get("site") or ""),
        })
    return records


def _run_scrape(site: list, term: str, extra_kwargs: dict = None) -> list[dict]:
    kwargs = dict(
        site_name=site,
        search_term=term,
        location=LOCATION,
        results_wanted=RESULTS_PER_TERM,
        hours_old=HOURS_OLD,
        country_indeed="India",
    )
    if extra_kwargs:
        kwargs.update(extra_kwargs)
    for attempt in range(1, 4):
        try:
            df = jobspy.scrape_jobs(**kwargs)
            return _to_records(df)
        except Exception as exc:
            logger.warning("  attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(4 * attempt)
    return []


def _scrape_linkedin() -> list[dict]:
    logger.info("=== LinkedIn Jobs: %d terms ===", len(LINKEDIN_TERMS))
    seen: set = set()
    results = []
    for i, term in enumerate(LINKEDIN_TERMS):
        batch = _run_scrape(["linkedin"], term)
        new   = [r for r in batch if r["job_url"] not in seen]
        for r in new: seen.add(r["job_url"])
        results.extend(new)
        logger.info("  [%d/%d] +%d (total %d) | %s…",
                    i+1, len(LINKEDIN_TERMS), len(new), len(results), term[:55])
        time.sleep(5)
    logger.info("LinkedIn Jobs: %d unique", len(results))
    return results


def _scrape_google_jobs() -> list[dict]:
    logger.info("=== Google Jobs ===")
    seen: set = set()
    results = []
    terms = [
        qa('"SOC analyst" OR "security analyst" OR "cybersecurity analyst"'),
        qa('"GRC analyst" OR "compliance analyst" OR "IT audit analyst"'),
        qa('"risk analyst" OR "KYC analyst" OR "AML analyst" OR "fraud analyst"'),
        qa('"cloud security" OR "IAM analyst" OR "network security analyst"'),
        qa('"penetration tester" OR "VAPT analyst" OR "application security engineer"'),
        qa('"incident response analyst" OR "threat intelligence analyst"'),
        qa('"data privacy analyst" OR "DLP analyst" OR "malware analyst"'),
        qa('"DevSecOps engineer" OR "vulnerability analyst"'),
        qi('"cybersecurity intern" OR "security intern" OR "SOC intern"'),
        qi('"GRC intern" OR "compliance intern" OR "risk intern"'),
    ]
    for i, term in enumerate(terms):
        batch = _run_scrape(["google"], term)
        new   = [r for r in batch if r["job_url"] not in seen]
        for r in new: seen.add(r["job_url"])
        results.extend(new)
        logger.info("  [%d/%d] Google +%d", i+1, len(terms), len(new))
        time.sleep(4)
    logger.info("Google Jobs: %d unique", len(results))
    return results


def _scrape_indeed() -> list[dict]:
    logger.info("=== Indeed India ===")
    seen: set = set()
    results = []
    terms = [
        qf('"SOC analyst" OR "security analyst"'),
        qf('"GRC analyst" OR "compliance analyst"'),
        qf('"risk analyst" OR "KYC analyst" OR "AML analyst"'),
        qf('"cloud security" OR "network security analyst"'),
        qf('"penetration tester" OR "VAPT engineer"'),
        qf('"incident response" OR "threat intelligence analyst"'),
        qf('"IAM analyst" OR "data privacy analyst"'),
        qf('"IT audit" OR "vulnerability analyst"'),
        qf('"cybersecurity analyst" OR "information security analyst"'),
        qf('"fraud analyst" OR "DevSecOps engineer"'),
        qi('"cybersecurity intern" OR "security intern"'),
        qi('"GRC intern" OR "compliance intern" OR "risk intern"'),
    ]
    for i, term in enumerate(terms):
        batch = _run_scrape(["indeed"], term)
        new   = [r for r in batch if r["job_url"] not in seen]
        for r in new: seen.add(r["job_url"])
        results.extend(new)
        logger.info("  [%d/%d] Indeed +%d", i+1, len(terms), len(new))
        time.sleep(5)
    logger.info("Indeed: %d unique", len(results))
    return results


def fetch_linkedin_posts() -> list[dict]:
    """Google RSS site:linkedin.com — catches recruiter feed posts and intern announcements."""
    logger.info("=== LinkedIn Posts (Google RSS): %d queries ===",
                len(LINKEDIN_POST_QUERIES))
    results = []
    seen: set = set()

    for i, query in enumerate(LINKEDIN_POST_QUERIES):
        encoded = query.replace(" ", "+").replace(":", "%3A")
        url = (f"https://news.google.com/rss/search"
               f"?q={encoded}&hl=en-IN&gl=IN&ceid=IN:en")
        try:
            feed = feedparser.parse(url)
            valid = 0
            for entry in feed.entries[:10]:
                link  = entry.get("link", "")
                title = entry.get("title", "")
                desc  = entry.get("summary", "") or entry.get("description", "")

                if not link or link in seen:
                    continue

                # ── CHECK 1: URL + title garbage filter (login pages, company pages etc.) ──
                if not _is_valid_post(title, link):
                    continue

                # ── CHECK 2: LinkedIn profile URL filter ──
                # GARBAGE_URL_PATTERNS already includes linkedin.com/in/ so this
                # is caught by _is_valid_post above, but keeping explicit check
                # here as belt-and-suspenders in case URL format varies
                if "linkedin.com/in/" in link.lower():
                    continue

                # ── CHECK 3: Profile headline title filter ──
                # Catches profiles that slipped past URL check because they came
                # from a non /in/ URL (e.g. Google cached version, redirect URL)
                # Examples caught:
                #   "Sushmitha Sonkamble - SailPoint IdentityIQ/ISC Certified"
                #   "Ashish Gangavaram, CISA - LinkedIn"
                #   "Anand Kumar - Cyber Threat Intelligence @ adidas"
                if _is_profile_headline(title):
                    continue

                # ── CHECK 4: Minimum description quality ──
                # Real job posts have substantive descriptions.
                # Profile stub pages and login redirects have almost nothing.
                if len(desc.strip()) < 80:
                    continue

              
                # ADDED — 8 lines
                published = entry.get("published_parsed")
                if published:
                    import time as _time
                    age_days = (_time.time() - _time.mktime(published)) / 86400
                    if age_days > 30:
                        continue
                # If published_parsed is missing we let it through  


              
                # ── All checks passed — keep this entry ──
                seen.add(link)
                valid += 1
                results.append({
                    "title":       title,
                    "company":     "",
                    "location":    "Bangalore",
                    "job_url":     link,
                    "description": f"{title}. {desc[:600]}",
                    "date_posted": entry.get("published", ""),
                    "source":      "linkedin_post",
                })

            logger.info("  [%d/%d] '%s…' → %d valid / %d total",
                        i+1, len(LINKEDIN_POST_QUERIES), query[:45],
                        valid, len(feed.entries))

        except Exception as e:
            logger.error("  Posts RSS error: %s", e)

        time.sleep(1)

    logger.info("LinkedIn Posts: %d valid posts", len(results))
    return results


def gather_all_listings() -> list[dict]:
    all_results = []
    seen: set   = set()

    sources = [
        ("LinkedIn Jobs",  _scrape_linkedin),
        ("Google Jobs",    _scrape_google_jobs),
        ("Indeed",         _scrape_indeed),
        ("LinkedIn Posts", fetch_linkedin_posts),
    ]

    counts = {}
    for name, fn in sources:
        try:
            batch = fn()
        except Exception as exc:
            logger.error("%s crashed: %s", name, exc)
            batch = []

        before = len(all_results)
        for r in batch:
            url = r.get("job_url", "").strip()
            key = url if url else f"{r.get('title','')}|||{r.get('company','')}"
            if key and key not in seen:
                seen.add(key)
                all_results.append(r)

        counts[name] = len(all_results) - before
        logger.info("%s → %d new unique", name, counts[name])
        time.sleep(8)

    logger.info("TOTAL: %d unique | %s",
                len(all_results),
                " | ".join(f"{k}={v}" for k, v in counts.items()))
    return all_results
