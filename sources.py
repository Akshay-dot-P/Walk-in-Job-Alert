import jobspy
import pandas as pd







logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# CXS list URL must be /wday/cxs/{tenant}/{site_id}/jobs (see ApplyPilot employers.yaml).
# PayPal uses site_id "jobs" → path ends with .../jobs/jobs (verified).
# Disable strict entry/intern filtering: WORKDAY_EXPERIENCE_STRICT=0
WORKDAY_COMPANIES = [
    ("BMO", "https://bmo.wd3.myworkdayjobs.com/wday/cxs/bmo/External/jobs"),
    ("Salesforce", "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce/External_Career_Site/jobs"),
    ("Cisco", "https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/jobs"),
    ("PayPal", "https://paypal.wd1.myworkdayjobs.com/wday/cxs/paypal/jobs/jobs"),
    ("Adobe", "https://adobe.wd5.myworkdayjobs.com/wday/cxs/adobe/external_experienced/jobs"),
    ("Intel", "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs"),
    ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs"),
    ("Mastercard", "https://mastercard.wd1.myworkdayjobs.com/wday/cxs/mastercard/CorporateCareers/jobs"),
    ("PwC", "https://pwc.wd3.myworkdayjobs.com/wday/cxs/pwc/Global_Experienced_Careers/jobs"),
    ("FIS", "https://fis.wd5.myworkdayjobs.com/wday/cxs/fis/SearchJobs/jobs"),
    ("Thomson Reuters", "https://thomsonreuters.wd5.myworkdayjobs.com/wday/cxs/thomsonreuters/External_Career_Site/jobs"),
    ("Motorola Solutions", "https://motorolasolutions.wd5.myworkdayjobs.com/wday/cxs/motorolasolutions/Careers/jobs"),
    ("Ciena", "https://ciena.wd5.myworkdayjobs.com/wday/cxs/ciena/Careers/jobs"),
    ("BlackBerry", "https://bb.wd3.myworkdayjobs.com/wday/cxs/bb/BlackBerry/jobs"),
    ("Workday", "https://workday.wd5.myworkdayjobs.com/wday/cxs/workday/Workday/jobs"),
    ("TELUS International", "https://telusinternational.wd3.myworkdayjobs.com/wday/cxs/telusinternational/External/jobs"),
    ("Magna", "https://magna.wd3.myworkdayjobs.com/wday/cxs/magna/Magna/jobs"),
    ("TD Bank", "https://td.wd3.myworkdayjobs.com/wday/cxs/td/TD_Bank_Careers/jobs"),
    ("RBC", "https://rbc.wd3.myworkdayjobs.com/wday/cxs/rbc/RBCGLOBAL1/jobs"),

]
# Intern / entry / junior-oriented queries only — avoid "Senior …" strings that pull mid-career roles.
WORKDAY_SEARCH_QUERIES = [
@@ -94,112 +101,7 @@
    "security", "cyber", "soc", "risk", "compliance", "grc", "iam", "appsec", "cloud"
)
WORKDAY_ALLOWED_LOCATIONS = ("india", "bengaluru", "bangalore")

# Title-only: mid/senior IC and leadership (Workday gate before AI scoring).
_WORKDAY_SENIOR_TITLE_RE = re.compile(
    r"(?i)\b(senior|\bsr\.?\b|principal\b|staff\s+engineer|staff\s+architect|distinguished|"
    r"director\b|vice\s+president|\bvp\b|chief\s+|head\s+of|executive\s+vice|"
    r"lead\s+engineer|lead\s+developer|lead\s+architect|group\s+manager|"
    r"people\s+manager)\b",
)
_WORKDAY_TITLE_ROMAN_SENIOR_RE = re.compile(r"(?i)\s(iii|iv|v|vi)\s*$")
_WORKDAY_TITLE_YEARS_PLUS_RE = re.compile(
    r"(?i)\(?\s*\d{1,2}\s*\+\s*(?:years?|yrs?)|\b\d{1,2}\s*\+\s*years?\b",
)
# Description: explicit minimum experience (conservative).
_WORKDAY_DESC_MIN_YEARS_RE = re.compile(
    r"(?is)(?:minimum|min\.?|at\s+least|requires?\s+(?:a\s+)?(?:minimum\s+)?(?:of\s+)?)"
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
)
_WORKDAY_DESC_HIGH_RANGE_RE = re.compile(
    r"(?i)\b(\d{1,2})\s*[\+\-–]\s*(\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)",
)
_WORKDAY_ENTRY_SIGNAL_RE = re.compile(
    r"(?is)\b(intern|internship|apprentice|apprenticeship|trainee|co[\s-]?op\b|"
    r"entry[\s-]level|entry\s+level|fresher|new\s+grad|graduate\s+program|"
    r"campus\s+(hire|recruiting|program)|early\s+career|university\s+graduate|"
    r"0[\s\-–]*2\s*yrs?|0\s+to\s+2|upto\s+2|up\s+to\s+2|less\s+than\s+3\s+years?|"
    r"1[\s\-–]*2\s*year|associate\s+level|junior\b|graduate\s+hire|"
    r"analyst\s+i\b|engineer\s+i\b|engineer\s+1\b|l1\b|level\s+1\b|tier\s+1\b)\b",
)


def _workday_experience_strict_enabled() -> bool:
    return os.environ.get("WORKDAY_EXPERIENCE_STRICT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _workday_loose_plain_text(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    t = re.sub(r"<[^>]+>", " ", html_or_text)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _workday_passes_entry_experience_gate(title: str, description: str) -> bool:
    """
    Keep intern / apprenticeship / entry / 0–2y-style roles; drop obvious senior
    titles and postings that state high minimum years of experience.
    """
    t_raw = (title or "").strip()
    t = t_raw.lower()
    d_plain = _workday_loose_plain_text(description or "")
    blob = f"{t}\n{d_plain}"

    if _WORKDAY_TITLE_YEARS_PLUS_RE.search(t_raw):
        return False
    if _WORKDAY_SENIOR_TITLE_RE.search(t_raw):
        return False
    if _WORKDAY_TITLE_ROMAN_SENIOR_RE.search(t_raw) and "intern" not in t and "trainee" not in t:
        return False
    if re.search(r"\bmanager\b", t) and "intern" not in t and "trainee" not in t:
        return False

    for m in _WORKDAY_DESC_MIN_YEARS_RE.finditer(blob):
        try:
            if int(m.group(1)) >= 5:
                return False
        except ValueError:
            pass
    for m in _WORKDAY_DESC_HIGH_RANGE_RE.finditer(blob):
        try:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo >= 5 or hi >= 8:
                return False
        except ValueError:
            pass
    if re.search(r"(?i)\b([6-9]|\d{2})\s*\+\s*years?\b", blob):
        return False

    if _WORKDAY_ENTRY_SIGNAL_RE.search(blob):
        return True
    if re.search(r"(?i)\b(fresher|graduate|stipend|stipendiary)\b", blob):
        return True

    # Analyst / SOC-style titles without senior markers (already excluded above).
    if re.search(
        r"(?i)\b(soc|security|cyber|grc|risk|compliance|fraud|iam|appsec|"
        r"vulnerability|incident|threat|network\s+security)\b",
        t,
    ) and re.search(r"(?i)\b(analyst|specialist|consultant|engineer|administrator)\b", t):
        return True

    return False


def _workday_experience_hint(title: str, description: str) -> str:
    """Short label for listing dicts (pre-AI); aligns with scorer experience_required."""
    blob = _workday_loose_plain_text(f"{title}\n{description}")
    if re.search(r"(?i)\b(intern|internship)\b", blob):
        return "intern"
    if re.search(r"(?i)\b(apprentice|apprenticeship)\b", blob):
        return "apprenticeship"
    if _WORKDAY_ENTRY_SIGNAL_RE.search(blob) or re.search(r"(?i)\b(fresher|graduate\s+hire)\b", blob):
        return "0-2 years"
    return ""


FQ_FRESHER = (
    '(fresher OR "entry level" OR "entry-level" OR junior OR trainee '
        location_ok = True if not allowed_locations else any(c in location for c in allowed_locations)
        if not title_ok or not location_ok:
            continue
        if strict_xp and not _workday_passes_entry_experience_gate(
            str(job.get("title") or ""),
            str(job.get("description") or ""),
        ):
            company_name=company_name,
            posting=posting,
        )
        xp_hint = _workday_experience_hint(title, description)
        records.append({
            "title": title,
            "company": company_name,
            "location": location,
            "job_url": external_url,
            "description": description,
            "date_posted": _extract_posted_date(posting),
            "source": "workday",
            "job_id": job_id,
            "experience_required": xp_hint or "",
        })

    filtered = filter_workday_jobs(
        records,
        title_keywords=title_keywords,
        allowed_locations=allowed_locations,
    )
    logger.info("%s: %d jobs found (filtered)", company_name, len(filtered))
    return filtered


async def scrape_workday_jobs(
    companies: list[tuple[str, str]],
    search_queries: list[str],
    worker_count: int = WORKDAY_WORKERS,
    title_keywords: tuple[str, ...] | list[str] | None = None,
    allowed_locations: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    if not companies:
        return []

    workers = max(1, min(int(worker_count or 1), 8))
    sem = asyncio.Semaphore(workers)
    timeout = httpx.Timeout(WORKDAY_TIMEOUT_S)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        async def run_company(company_name: str, jobs_url: str) -> list[dict]:
            async with sem:
                return await _scrape_workday_company(
                    client=client,
                    company_name=company_name,
                    jobs_url=jobs_url,
                    search_queries=search_queries,
                    title_keywords=title_keywords,
                    allowed_locations=allowed_locations,
                )

        tasks = [run_company(name, url) for name, url in companies]
        company_results = await asyncio.gather(*tasks, return_exceptions=True)

    combined: list[dict] = []
    for idx, res in enumerate(company_results):
        company_name = companies[idx][0]
        if isinstance(res, Exception):
            logger.error("%s: Workday scrape crashed: %s", company_name, res)
            continue
        combined.extend(res)
    return combined


def _scrape_workday() -> list[dict]:
    companies = _load_workday_companies()
    logger.info("=== Workday API: %d tenants | %d queries ===",
                len(companies), len(WORKDAY_SEARCH_QUERIES))
    try:
        return asyncio.run(
            scrape_workday_jobs(
                companies=companies,
                search_queries=WORKDAY_SEARCH_QUERIES,
                worker_count=WORKDAY_WORKERS,
                title_keywords=WORKDAY_TITLE_KEYWORDS,
                allowed_locations=WORKDAY_ALLOWED_LOCATIONS,
            )
        )
    except Exception as exc:
        logger.error("Workday source failed: %s", exc)
        return []


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
        # Log BEFORE scrape so a slow/hung term doesn't look like a freeze.
        logger.info("  [%d/%d] scraping | %s…", i + 1, len(LINKEDIN_TERMS), term[:55])
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
                    if age_days > 40:
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
        ("Workday",        _scrape_workday),
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
