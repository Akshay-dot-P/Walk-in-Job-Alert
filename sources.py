import re
import time
import logging
import requests
import feedparser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
    logger.info("jobspy loaded OK")
except Exception as e:
    JOBSPY_AVAILABLE = False
    logger.warning(f"jobspy not available: {e}")

from config import TARGET_ROLES, WALKIN_KEYWORDS, BANGALORE_KEYWORDS

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def text_contains_any(text, keywords):
    if not text:
        return False
    pattern = "|".join(re.escape(kw) for kw in keywords)
    return bool(re.search(pattern, text, re.IGNORECASE))


def is_tech_in_blr(title, description="", location=""):
    """Check only tech role + Bangalore — used when source already guarantees walk-in context."""
    full = f"{title} {description} {location}"
    return (
        text_contains_any(full, TARGET_ROLES) and
        text_contains_any(full, BANGALORE_KEYWORDS)
    )


def is_full_match(title, description="", location=""):
    """Check tech + Bangalore + walk-in keyword — used for generic sources."""
    full = f"{title} {description} {location}"
    return (
        text_contains_any(full, TARGET_ROLES) and
        text_contains_any(full, BANGALORE_KEYWORDS) and
        text_contains_any(full, WALKIN_KEYWORDS)
    )


# ---------------------------------------------------------------------------
# SOURCE 1: Google News RSS
# We search specifically for "walk in interview bangalore <role>" so every
# result is already about walk-in interviews. We only filter by tech + blr.
# ---------------------------------------------------------------------------
def fetch_google_news():
    logger.info("Fetching Google News RSS...")
    results = []

    queries = [
        "walk+in+interview+bangalore+cloud+engineer+2026",
        "walk+in+interview+bangalore+software+developer+SDE+2026",
        "walkin+drive+bangalore+devops+SRE+security+analyst+2026",
        "walk+in+interview+bangalore+AWS+GCP+Azure+2026",
        "hiring+drive+bangalore+software+engineer+walk+in+2026",
    ]

    for q in queries:
        url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
        try:
            feed = feedparser.parse(url)
            logger.info(f"Google News '{q[:40]}': {len(feed.entries)} entries")

            for entry in feed.entries:
                title = entry.get("title", "")
                desc  = entry.get("summary", "") or ""
                link  = entry.get("link", "")

                # Since the QUERY already contains "walk in interview bangalore",
                # every result is contextually about walk-ins.
                # We only require it mentions a tech role.
                if text_contains_any(f"{title} {desc}", TARGET_ROLES):
                    logger.info(f"  + MATCH: {title[:70]}")
                    results.append({
                        "source": "google_news",
                        "title": title,
                        "company": "",
                        "description": f"Walk-in interview related: {desc[:500]}",
                        "url": link,
                        "location": "Bangalore",
                    })
                else:
                    logger.info(f"  - skip (no tech role): {title[:60]}")

        except Exception as e:
            logger.error(f"Google News error: {e}")

        time.sleep(1)

    logger.info(f"Google News: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# SOURCE 2: Naukri — extract JSON from embedded script tags
# ---------------------------------------------------------------------------
def fetch_naukri():
    logger.info("Fetching Naukri (API)...")
    results = []
    session = requests.Session()
    
    searches = [
        "walk in interview cloud engineer bangalore",
        "walk in interview software developer bangalore",
        "walk in interview devops SRE bangalore",
        "walkin drive security analyst bangalore",
    ]
    
    for keyword in searches:
        try:
            url = "https://www.naukri.com/jobapi/v4/search"
            params = {
                "noOfResults": 20,
                "urlType": "search_by_key_loc",
                "searchType": "adv",
                "keyword": keyword,
                "location": "bangalore",
                "jobAge": 1,
                "src": "jobsearchDesk",
                "pageNo": 1,
                "experience": 0,
            }
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "appid": "109",
                "systemid": "109",
                "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
                "Referer": "https://www.naukri.com/",
            }
            r = session.get(url, params=params, headers=headers, timeout=15)
            logger.info(f"Naukri API status: {r.status_code} for '{keyword}'")
            
            if r.status_code != 200:
                continue
                
            data = r.json()
            jobs = data.get("jobDetails", [])
            logger.info(f"Naukri API: {len(jobs)} jobs returned")
            
            for job in jobs:
                title = job.get("title", "")
                company = job.get("companyName", "")
                location = job.get("placeholders", [{}])
                loc_text = "Bangalore"
                for p in location:
                    if p.get("type") == "location":
                        loc_text = p.get("label", "Bangalore")
                        break
                
                job_url = job.get("jdURL", "") or job.get("jobUrl", "")
                desc = job.get("jobDescription", "")
                
                if is_tech_in_blr(title, desc, loc_text):
                    results.append({
                        "source": "naukri",
                        "title": title,
                        "company": company,
                        "description": desc,
                        "url": f"https://www.naukri.com{job_url}" if job_url.startswith("/") else job_url,
                        "location": loc_text,
                    })
                    
        except Exception as e:
            logger.error(f"Naukri API error for '{keyword}': {e}")
        time.sleep(2)
    
    logger.info(f"Naukri total: {len(results)} relevant listings")
    return results

# ---------------------------------------------------------------------------
# SOURCE 3: JobSpy with broader terms (no "walk in" in query)
# Walk-in listings on LinkedIn/Google don't use that phrase in metadata.
# We search broadly for tech roles in Bangalore, then score them via AI.
# The AI legitimacy scorer will identify walk-in ones from the description.
# ---------------------------------------------------------------------------
def fetch_jobspy():
    if not JOBSPY_AVAILABLE:
        return []

    results = []

    searches = [
        ("cloud engineer bangalore", ["linkedin", "google"]),
        ("software developer SDE bangalore", ["linkedin", "google"]),
        ("site reliability engineer SRE bangalore", ["linkedin"]),
        ("security analyst bangalore", ["linkedin", "google"]),
        ("devops engineer bangalore", ["linkedin", "google"]),
    ]

    for term, sites in searches:
        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=term,
                location="Bangalore, Karnataka, India",
                results_wanted=15,
                hours_old=24,
                country_indeed="india",
            )

            logger.info(f"JobSpy '{term}' via {sites}: {len(df)} raw results")

            if len(df) > 0:
                for _, row in df.head(2).iterrows():
                    logger.info(f"  Sample: '{row.get('title','?')}' @ {row.get('company','?')}")

            for _, row in df.iterrows():
                title    = str(row.get("title", "") or "")
                company  = str(row.get("company", "") or "")
                desc     = str(row.get("description", "") or "")
                location = str(row.get("location", "") or "")
                url      = str(row.get("job_url", "") or "")

                # For JobSpy: only require tech role in Bangalore.
                # The AI scorer will check if it's a walk-in and score it.
                if is_tech_in_blr(title, desc, location):
                    results.append({
                        "source": sites[0],
                        "title": title,
                        "company": company,
                        "description": desc[:2000] or f"{title} at {company} in Bangalore",
                        "url": url,
                        "location": location,
                    })

            time.sleep(3)

        except Exception as e:
            logger.error(f"JobSpy '{term}': {e}")

    logger.info(f"JobSpy total: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# SOURCE 4: Foundit (formerly Monster India) — has a working RSS feed
# ---------------------------------------------------------------------------
def fetch_foundit():
    logger.info("Fetching Foundit (Monster India)...")
    results = []

    urls = [
        "https://www.foundit.in/srp/results?query=walk+in+interview+cloud+engineer&location=Bangalore&experienceRanges=0~3,3~6",
        "https://www.foundit.in/srp/results?query=walk+in+interview+software+engineer&location=Bangalore",
    ]

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)

    for url in urls:
        try:
            r = session.get(url, timeout=15)
            logger.info(f"Foundit status: {r.status_code}")

            if r.status_code != 200:
                continue

            # Extract job data from JSON embedded in page
            # Foundit uses __NEXT_DATA__ pattern (Next.js)
            match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
            if match:
                import json
                try:
                    data = json.loads(match.group(1))
                    # Navigate to job listings in Next.js data structure
                    jobs = (
                        data.get("props", {})
                            .get("pageProps", {})
                            .get("jobSearchResult", {})
                            .get("jobDetails", [])
                    )
                    logger.info(f"Foundit: found {len(jobs)} jobs in __NEXT_DATA__")

                    for job in jobs:
                        title   = job.get("designation", "") or job.get("jobTitle", "")
                        company = job.get("companyName", "")
                        desc    = job.get("jobDescription", "")
                        jid     = job.get("jobId", "")
                        jurl    = f"https://www.foundit.in/job/{jid}" if jid else url

                        if is_full_match(title, desc, "Bangalore"):
                            results.append({
                                "source": "foundit",
                                "title": title,
                                "company": company,
                                "description": desc[:2000],
                                "url": jurl,
                                "location": "Bangalore",
                            })
                except Exception as e:
                    logger.error(f"Foundit JSON parse error: {e}")

        except Exception as e:
            logger.error(f"Foundit error: {e}")

        time.sleep(2)

    logger.info(f"Foundit: {len(results)} relevant listings")
    return results


# ---------------------------------------------------------------------------
# MASTER FUNCTION
# ---------------------------------------------------------------------------
def gather_all_listings():
    all_listings = (
        fetch_google_news()   # Most reliable — Google News RSS always works
        + fetch_naukri()      # India's #1 job portal
        + fetch_foundit()     # Monster India equivalent
        + fetch_jobspy()      # LinkedIn + Google Jobs
    )

    # Deduplicate by URL or title+company
    seen = {}
    for listing in all_listings:
        key = listing.get("url") or f"{listing['title']}|{listing['company']}"
        if key and key not in seen:
            seen[key] = listing

    unique = list(seen.values())
    logger.info(f"Total unique listings across all sources: {len(unique)}")
    return unique
