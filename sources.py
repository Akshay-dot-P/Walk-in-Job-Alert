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

from __future__ import annotations

import re
import time
import logging
import asyncio
import os
import json
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse
import random
from config import MAX_AGE_DAYS, location_ok
import feedparser
import httpx
import jobspy
import pandas as pd
from collections import Counter
from urllib.parse import urlparse, quote
import html as _html

from jobboards import fetch_company   # add to the imports at top

# board types: "oracle", "atlassian" (the new ones), plus greenhouse/lever/ashby/workable
JOBRADAR_COMPANIES = [
    {"name": "Atlassian", "board": "atlassian", "slug": "atlassian"},
    {"name": "Oracle", "board": "oracle", "slug": "eeho.fa.us2.oraclecloud.com/CX_1"},
]

WORKDAY_TITLE_KEYWORDS = (
    "security", "cyber", "soc", "risk", "compliance", "grc", "iam", "appsec", "cloud", "identity", "desk",
    "network", "audit", "privacy", "fraud", "aml", "threat", "detection", "noc", "siem",
)

JOBRADAR_TITLE_KEYWORDS = WORKDAY_TITLE_KEYWORDS

_INDIA_RE = re.compile(r"\b(?:india|bengaluru|bangalore|karnataka|blr|remote)\b", re.I)
_MULTI_LOC_RE = re.compile(r"^\s*(?:\d+\s+locations?|multiple\s+locations?)?\s*$", re.I)
_REMOTE_RE = re.compile(r"\b(?:remote|work\s+from\s+home|wfh)\b", re.I)

def _workday_loc_state(loc: str) -> str:
    """yes: India is named and allowed. maybe: the list hides the places ('3 Locations' or empty). no: everything else."""
    s = str(loc or "").strip()
    if _MULTI_LOC_RE.match(s):
        return "maybe"
    return "yes" if _INDIA_RE.search(s) and location_ok(s) else "no"


def _detail_locations(payload: dict) -> list[str]:
    info = payload.get("jobPostingInfo", {}) if isinstance(payload, dict) else {}
    country = info.get("country")
    country = str(country.get("descriptor") if isinstance(country, dict) else country or "").strip()
    out, primary = [], info.get("location")
    for v in [primary] + list(info.get("additionalLocations") or []):
        if isinstance(v, str) and v.strip():
            v = v.strip()
            out.append(f"{v}, {country}" if (v == primary and country and country.lower() not in v.lower()) else v)
    return out


async def _fetch_workday_detail(client, jobs_url, company_name, posting):
    raw_ext = str(posting.get("externalPath") or "").strip()
    fallback = str(posting.get("description") or "")
    detail_url = _workday_detail_json_url(jobs_url, raw_ext) if raw_ext else ""
    if not detail_url:
        return fallback, []
    try:
        r = await client.get(detail_url)
        if r.status_code == 200 and (r.text or "").strip():
            payload = json.loads(r.text)
            return (_extract_detail_description(payload) or fallback), _detail_locations(payload)
    except Exception:
        pass
    return fallback, []

def _jr_to_record(j: dict) -> dict:
    """jobradar schema -> your schema (job_url, date_posted as YYYY-MM-DD)."""
    ts = j.get("posted_at")
    date_posted = (
        datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else ""
    )
    return {
        "title":       j["title"],
        "company":     j["company"],
        "location":    j["location"] or "India",
        "job_url":     j["url"] or "",
        "description": (j["description"] or "")[:1500],
        "date_posted": date_posted,
        "source":      j["source"],
    }


def _scrape_jobradar() -> list[dict]:
    logger.info("=== JobRadar boards: %d companies ===", len(JOBRADAR_COMPANIES))
    results: list[dict] = []
    for entry in JOBRADAR_COMPANIES:
        jobs, err = fetch_company(entry)
        if err:
            logger.warning("  %s: %s", entry["name"], err)
            continue
        kept = 0
        for j in jobs:
            title = (j["title"] or "").lower()
            if not any(k in title for k in JOBRADAR_TITLE_KEYWORDS):
                continue
            if not location_ok(j["location"]):
                continue
            ts = j.get("posted_at")
            if ts and (time.time() - ts) / 86400 > MAX_AGE_DAYS:
                continue   # posted_at None (e.g. Workday) is kept
            if not j["url"]:
                continue
            results.append(_jr_to_record(j))
            kept += 1
        logger.info("  %s: %d/%d kept", entry["name"], kept, len(jobs))
        time.sleep(1)
    logger.info("JobRadar boards: %d jobs", len(results))
    return results


logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOCATION            = "Bengaluru/Bangalore, Karnataka, India"
HOURS_OLD           = 24 * MAX_AGE_DAYS 
RESULTS_PER_TERM    = 40
WORKDAY_PAGE_SIZE   = 20
WORKDAY_MAX_PAGES   = 25
WORKDAY_MAX_RESULTS = WORKDAY_PAGE_SIZE * WORKDAY_MAX_PAGES
WORKDAY_TIMEOUT_S   = 30.0
WORKDAY_WORKERS     = 5

# CXS list URL must be /wday/cxs/{tenant}/{site_id}/jobs (see ApplyPilot employers.yaml).

WORKDAY_COMPANIES = [
        # unverified rows from the WFH list
    ("Lowe's", "https://lowes.wd5.myworkdayjobs.com/wday/cxs/lowes/LWS_External_CS/jobs"),
    ("Elevance Health", "https://elevancehealth.wd1.myworkdayjobs.com/wday/cxs/elevancehealth/ANT/jobs"),
    ("IQVIA", "https://iqvia.wd1.myworkdayjobs.com/wday/cxs/iqvia/IQVIA/jobs"),
    ("Fiserv", "https://fiserv.wd5.myworkdayjobs.com/wday/cxs/fiserv/EXT/jobs"),
    ("Concentrix", "https://concentrix.wd3.myworkdayjobs.com/wday/cxs/concentrix/Concentrix_Careers/jobs"),
    ("Lumen", "https://lumen.wd1.myworkdayjobs.com/wday/cxs/lumen/Lumen/jobs"),
    ("Verizon", "https://verizon.wd12.myworkdayjobs.com/wday/cxs/verizon/verizon-careers/jobs"),
    ("Sun Life", "https://sunlife.wd3.myworkdayjobs.com/wday/cxs/sunlife/Experienced-Jobs/jobs"),
    ("Conduent", "https://conduent.wd1.myworkdayjobs.com/wday/cxs/conduent/Careers/jobs"),
    ("Yahoo", "https://ouryahoo.wd5.myworkdayjobs.com/wday/cxs/ouryahoo/careers/jobs"),
    # ── Original companies (working) ──
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
    ("RBC", "https://rbc.wd3.myworkdayjobs.com/wday/cxs/rbc/RBCGLOBAL1/jobs"),
    
    # ── Big Tech & Cloud ──
    #("Microsoft", "https://careers.microsoft.com/wday/cxs/microsoft/Global/jobs"),
    #("Amazon", "https://amazon.wd5.myworkdayjobs.com/wday/cxs/amazon/Hire/jobs"),
    #("Google", "https://google.wd1.myworkdayjobs.com/wday/cxs/google/GoogleCareers/jobs"),
    #("Meta", "https://meta.wd1.myworkdayjobs.com/wday/cxs/meta/Careers/jobs"),
    #("Apple", "https://jobs.apple.com/wday/cxs/apple/ExternalCareers/jobs"),
    #("Oracle", "https://oracle.wd1.myworkdayjobs.com/wday/cxs/oracle/OracleCareers/jobs"),
    #("SAP", "https://sap.wd3.myworkdayjobs.com/wday/cxs/sap/External/jobs"),
    #("ServiceNow", "https://servicenow.wd5.myworkdayjobs.com/wday/cxs/servicenow/External/jobs"),
    #("VMware", "https://vmware.wd1.myworkdayjobs.com/wday/cxs/vmware/VMware/jobs"),
    
    # ── Cybersecurity Vendors ──
    #("Palo Alto Networks", "https://paloaltonetworks.wd1.myworkdayjobs.com/wday/cxs/paloaltonetworks/PaloAltoNetworksJobs/jobs"),
    ("CrowdStrike", "https://crowdstrike.wd5.myworkdayjobs.com/wday/cxs/crowdstrike/crowdstrikecareers/jobs"),
    #("Fortinet", "https://fortinet.wd3.myworkdayjobs.com/wday/cxs/fortinet/careers/jobs"),
    #("Okta", "https://okta.wd1.myworkdayjobs.com/wday/cxs/okta/OktaJobs/jobs"),
    #("Zscaler", "https://zscaler.wd5.myworkdayjobs.com/wday/cxs/zscaler/Careers/jobs"),
    #("SentinelOne", "https://sentinelone.wd1.myworkdayjobs.com/wday/cxs/sentinelone/SentinelOne_Careers/jobs"),
    #("Rapid7", "https://rapid7.wd1.myworkdayjobs.com/wday/cxs/rapid7/External/jobs"),
    #("Qualys", "https://qualys.wd5.myworkdayjobs.com/wday/cxs/qualys/External/jobs"),
    #("Tenable", "https://tenable.wd5.myworkdayjobs.com/wday/cxs/tenable/Careers/jobs"),
    #("Check Point", "https://checkpoint.wd3.myworkdayjobs.com/wday/cxs/checkpoint/CheckPoint/jobs"),
    #("F5 Networks", "https://f5.wd5.myworkdayjobs.com/wday/cxs/f5/f5jobs/jobs"),
    #("Proofpoint", "https://proofpoint.wd5.myworkdayjobs.com/wday/cxs/proofpoint/External/jobs"),
    
    # ── Financial Services ──
    #("JPMorgan Chase", "https://jpmc.wd1.myworkdayjobs.com/wday/cxs/jpmc/JPMorganCareers/jobs"),
    #("Goldman Sachs", "https://gs.wd5.myworkdayjobs.com/wday/cxs/gs/GoldmanSachs/jobs"),
    #("Morgan Stanley", "https://morganstanley.wd5.myworkdayjobs.com/wday/cxs/morganstanley/External/jobs"),
   # ("Deutsche Bank", "https://db.wd3.myworkdayjobs.com/wday/cxs/db/DB_External_Career_Site/jobs"),
    #("Barclays", "https://barclays.wd3.myworkdayjobs.com/wday/cxs/barclays/External_Careers/jobs"),
    #("HSBC", "https://hsbc.wd3.myworkdayjobs.com/wday/cxs/hsbc/External/jobs"),
   # ("Standard Chartered", "https://sc.wd1.myworkdayjobs.com/wday/cxs/sc/SCB/jobs"),
    ("Citi", "https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/2/jobs"),
    #("Wells Fargo", "https://wellsfargo.wd1.myworkdayjobs.com/wday/cxs/wellsfargo/External/jobs"),
    #("Bank of America", "https://bofa.wd1.myworkdayjobs.com/wday/cxs/bofa/External/jobs"),
    #("American Express", "https://aexp.wd5.myworkdayjobs.com/wday/cxs/aexp/jobs/jobs"),
    #("Visa", "https://visa.wd5.myworkdayjobs.com/wday/cxs/visa/careers/jobs"),
    #("BlackRock", "https://blackrock.wd1.myworkdayjobs.com/wday/cxs/blackrock/BlackRock/jobs"),
    ("State Street", "https://statestreet.wd1.myworkdayjobs.com/wday/cxs/statestreet/Global/jobs"),
    
    # ── Consulting & Advisory ──
    #("Deloitte", "https://deloitte.wd5.myworkdayjobs.com/wday/cxs/deloitte/DeloitteCareer/jobs"),
    #("KPMG", "https://kpmg.wd3.myworkdayjobs.com/wday/cxs/kpmg/Careers/jobs"),
    #("EY", "https://ey.wd5.myworkdayjobs.com/wday/cxs/ey/EY_External_Careers/jobs"),
    #("Accenture", "https://accenture.wd3.myworkdayjobs.com/wday/cxs/accenture/AccentureExternalCareers/jobs"),
    #("Capgemini", "https://capgemini.wd3.myworkdayjobs.com/wday/cxs/capgemini/Careers/jobs"),
    #("BCG", "https://bcg.wd1.myworkdayjobs.com/wday/cxs/bcg/BCGCareers/jobs"),
    #("McKinsey", "https://mckinsey.wd3.myworkdayjobs.com/wday/cxs/mckinsey/McKinseyExternalCareers/jobs"),
    #("Bain", "https://bain.wd1.myworkdayjobs.com/wday/cxs/bain/External/jobs"),
    
    # ── Enterprise Software & SaaS ──
    #("Atlassian", "https://atlassian.wd5.myworkdayjobs.com/wday/cxs/atlassian/AtlassianCareers/jobs"),
    #("Splunk", "https://splunk.wd5.myworkdayjobs.com/wday/cxs/splunk/External/jobs"),
    #("Elastic", "https://elastic.wd5.myworkdayjobs.com/wday/cxs/elastic/External/jobs"),
    #("Datadog", "https://datadog.wd5.myworkdayjobs.com/wday/cxs/datadog/Careers/jobs"),
    #("Snowflake", "https://snowflake.wd5.myworkdayjobs.com/wday/cxs/snowflake/External/jobs"),
    #("Databricks", "https://databricks.wd1.myworkdayjobs.com/wday/cxs/databricks/External/jobs"),
    #("MongoDB", "https://mongodb.wd1.myworkdayjobs.com/wday/cxs/mongodb/External/jobs"),
    #("HashiCorp", "https://hashicorp.wd5.myworkdayjobs.com/wday/cxs/hashicorp/External/jobs"),
    
    # ── Networking & Infrastructure ──
    #("Juniper Networks", "https://juniper.wd1.myworkdayjobs.com/wday/cxs/juniper/External/jobs"),
    #("Arista Networks", "https://arista.wd1.myworkdayjobs.com/wday/cxs/arista/External/jobs"),
    #("Akamai", "https://akamai.wd5.myworkdayjobs.com/wday/cxs/akamai/Akamai/jobs"),
    #("Cloudflare", "https://cloudflare.wd1.myworkdayjobs.com/wday/cxs/cloudflare/External/jobs"),
    #("NetApp", "https://netapp.wd1.myworkdayjobs.com/wday/cxs/netapp/netapp_external_career_site/jobs"),
    #("Pure Storage", "https://purestorage.wd5.myworkdayjobs.com/wday/cxs/purestorage/External/jobs"),
    #("Nutanix", "https://nutanix.wd5.myworkdayjobs.com/wday/cxs/nutanix/External/jobs"),
    
    # ── Semiconductor & Hardware ──
    #("Qualcomm", "https://qualcomm.wd5.myworkdayjobs.com/wday/cxs/qualcomm/External/jobs"),
    #("Broadcom", "https://broadcom.wd1.myworkdayjobs.com/wday/cxs/broadcom/External/jobs"),
    ("Marvell", "https://marvell.wd1.myworkdayjobs.com/wday/cxs/marvell/MarvellCareers/jobs"),
    ("Micron", "https://micron.wd1.myworkdayjobs.com/wday/cxs/micron/External/jobs"),
    #("Western Digital", "https://wd.wd1.myworkdayjobs.com/wday/cxs/wd/WesternDigital/jobs"),
    
    # ── Industrial & Manufacturing ──
    #("Honeywell", "https://honeywell.wd5.myworkdayjobs.com/wday/cxs/honeywell/HoneywellCareers/jobs"),
    #("Siemens", "https://siemens.wd3.myworkdayjobs.com/wday/cxs/siemens/External/jobs"),
    #("GE Digital", "https://ge.wd1.myworkdayjobs.com/wday/cxs/ge/GE_External_Career_Site/jobs"),
    #("Schneider Electric", "https://schneider.wd3.myworkdayjobs.com/wday/cxs/schneider/External/jobs"),


    ("Morgan Stanley", "https://ms.wd5.myworkdayjobs.com/wday/cxs/ms/External/jobs"),
    ("Barclays", "https://barclays.wd3.myworkdayjobs.com/wday/cxs/barclays/External_Career_Site_Barclays/jobs"),
    ("Wells Fargo", "https://wf.wd1.myworkdayjobs.com/wday/cxs/wf/WellsFargoJobs/jobs"),
    ("Bank of America", "https://ghr.wd1.myworkdayjobs.com/wday/cxs/ghr/Lateral-US/jobs"),
    ("Accenture", "https://accenture.wd103.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs"),
    ("HPE", "https://hpe.wd5.myworkdayjobs.com/wday/cxs/hpe/Jobsathpe/jobs"),
]

GREENHOUSE_COMPANIES = [
    # ── Cybersecurity (original) ──
    "netskope",           # CASB / cloud security
    "tanium",             # endpoint security
    "transmitsecurity",   # identity fraud
    "cybereason",         # EDR
    "axonius",            # asset management
    "cloudsek",       # Bengaluru security company, confirmed on job-boards.greenhouse.io/cloudsek
    "quince-careers",    # SOC Engineer role in Bengaluru, confirmed on job-boards.greenhouse.io/quince-careers
    # ── Cybersecurity (new additions) ──
    "hackerone",          # bug bounty / vuln disclosure — confirmed India security analyst role
    "bugcrowd",           # bug bounty platform
    "synack",             # crowdsourced security
    "netspi",             # offensive security / pentest
    "secureworks",        # MDR / SOC-as-a-service
    "deepwatch",          # MDR
    "huntress",           # SMB security platform
    "lumu",               # network detection
    "vectra",             # AI threat detection (vectra-ai slug)
    "exabeam",            # SIEM / UEBA
    "cyware",             # threat intel / SOAR — India-founded
    "sequretek",          # India cybersec company
    "seclore",            # data-centric security — India-founded
 
    # ── Fintech / Crypto (original + new) ──
    "stripe",             # payments
    "coinbase",           # crypto
    "gemini",             # crypto exchange
    "fireblocks",         # crypto infra
    "plaid",              # fintech data
    "brex",               # fintech
    "mercury",            # fintech banking
 
    # ── Tech / SaaS (original + new) ──
    "gitlab",             # DevSecOps
    "mongodb",            # database
    "vercel",             # cloud
    "planetscale",        # database
    "1password",          # password manager / IAM
    "beyond-identity",    # passwordless IAM
 
    # ── Cloud / Infra (original + new) ──
    "fastly",             # edge cloud
    "algolia",            # search
    "hashicorp",          # infra security / secrets
    "teleport",           # infrastructure access
 
    # ── Enterprise SaaS (original) ──
    "airtable",
    "figma",
    "calendly",
 
    # ── GRC / Compliance (new) ──
    "drata",              # compliance automation
    "hyperproof",         # GRC platform
    "secureframe",        # compliance automation
    "anecdotes",          # GRC automation
    "oneleet",            # compliance + security
    "sprinto",            # India-founded GRC / compliance SaaS
    "scrut",              # India-founded GRC automation

    "razorpay",       # razorpay.com/jobs → boards.greenhouse.io/razorpay
    "browserstack",   # browserstack.com/careers → boards.greenhouse.io/browserstack
    "chargebee",      # chargebee.com/careers → boards.greenhouse.io/chargebee
    "postman",        # postman.com/company/careers
    "moengage",       # moengage.com/careers
    "clevertap",      # clevertap.com/company/careers
    "hasura",         # hasura.io/careers
    "meesho",         # meesho.io/careers (large Bangalore tech company)
    "sprinklr",       # sprinklr.com/careers (large Bangalore office)
]

# ═══════════════════════════════════════════════════════════════════════
# LEVER COMPANIES (Public API — no auth required)
# Only includes slugs verified to return data from api.lever.co
# ═══════════════════════════════════════════════════════════════════════
LEVER_COMPANIES = [
    # original
    "secureframe",        # compliance automation
    "logrocket",          # session replay / observability
 
    # new cybersecurity additions
    "detectify",          # web app security scanner
    "intigriti",          # bug bounty / ethical hacking
    "hadrian",            # attack surface mgmt
    "nagomi-security",    # exposure management
    "torq",               # security automation / SOAR
    "anvilogic",          # SOC modernization / SIEM
    "panther",            # cloud SIEM (panther-labs)
    "hunter-io",          # OSINT / email intelligence (hunter)
    "recorded-future",    # threat intelligence
 
    # India-relevant fintech / GRC
    "razorpay",           # India payments — may use Lever
    "m2p",                # India fintech
    "signzy",             # India digital KYC
    "bureau",             # India fraud/identity platform
    "sift",               # fraud detection
    "unit21",             # fraud/AML
    "hummingbird",        # AML / compliance
    "flagright",          # AML compliance — India presence
    "complyadvantage",    # financial crime intelligence
    "behaviosec",         # behavioral biometrics
    
    "cred",           # jobs.lever.co/cred — large security team (fraud, GRC, AppSec)
    "fi",             # jobs.lever.co/fi — Fi Money neobank, security/compliance roles
    "smallcase",      # jobs.lever.co/smallcase — fintech, compliance roles
    "zepto",          # jobs.lever.co/zepto — q-commerce, growing security practice
    "zetwerk",        # jobs.lever.co/zetwerk — manufacturing tech, infosec roles
    "meesho",         # jobs.lever.co/meesho (also on Greenhouse)
    "juspay",         # jobs.lever.co/juspay — payments infra, strong security team
]



# ═══════════════════════════════════════════════════════════════════════
# ③ ADD — ASHBY_COMPANIES  (new source)
# Public API: GET https://api.ashbyhq.com/posting-api/job-board/{slug}
# No auth needed. Returns {"jobs": [{title, location, jobUrl, publishedAt, ...}]}
# Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
# Confirmed via: github.com/outscal/OpenJobs (multi-ATS harvester)
# ═══════════════════════════════════════════════════════════════════════
 
ASHBY_COMPANIES = [
    # ── Confirmed India / security roles on Ashby ──
    "hackerone",            # bug bounty — confirmed India senior security analyst role
    "socure",               # identity fraud detection — confirmed India presence
 
    # ── Cloud Security ──
    "wiz",                  # cloud security (Google acquisition, still posts separately)
    "orca-security",        # cloud security CNAPP
    "lacework",             # cloud security posture
    "sysdig",               # cloud/container security
    "chainguard",           # software supply chain security
    "cycode",               # application security posture
 
    # ── AppSec / DevSecOps ──
    "snyk",                 # DevSecOps / AppSec — India engineering hub
    "semgrep",              # SAST / code security
    "checkmarx",            # SAST/DAST
    "endor-labs",           # software supply chain
    "apiiro",               # code risk platform
    "armorcode",            # AppSec posture mgmt — India co-founded
 
    # ── GRC / Compliance ──
    "vanta",                # GRC / compliance automation — large Ashby customer
    "drata",                # SOC2 / ISO 27001 automation
    "thoropass",            # compliance (formerly laika)
 
    # ── Identity / IAM / Zero Trust ──
    "opal",                 # IAM access mgmt
    "strata-identity",      # identity orchestration
    "saviynt",              # cloud IGA / PAM — India HQ (Bengaluru)
    "delinea",              # PAM (formerly Centrify + Thycotic)
    "hypr",                 # passwordless MFA
 
    # ── Threat Detection / SIEM / SOAR ──
    "abnormal-security",    # AI email security — India engineering
    "elevate-security",     # human risk mgmt
    "stellar-cyber",        # open XDR
 
    # ── Fraud / AML / FinCrime ──
    "sardine",              # fraud / AML — India team confirmed
    "inscribe",             # fraud document detection
    "alloy",                # identity / fraud decisioning
 
    # ── Fintech with strong India GCC / engineering hubs ──
    "brex",                 # fintech — Bengaluru engineering hub
    "rippling",             # HR/IAM platform — India engineering (Bengaluru)
    "deel",                 # global payroll / compliance — India remote roles
    "remote",               # global HR / compliance
    "plaid",                # open banking / fintech
    "payhawk",              # spend management
 
    # ── Data Privacy / DLP ──
    "mine",                 # data privacy automation
    "osano",                # data privacy mgmt
    "securiti",             # data security (India office)
    "kreditbee",      # kreditbee.in — lending fintech, compliance/risk roles

]
 
 
# ═══════════════════════════════════════════════════════════════════════
# ④ ADD — WORKABLE_COMPANIES  (new source)
# Public API: GET https://www.workable.com/api/accounts/{subdomain}?details=true
# No auth needed. Returns {"jobs": [{title, location, url, description, created_at}]}
# Docs: https://help.workable.com/hc/en-us/articles/4903195036183
# ═══════════════════════════════════════════════════════════════════════
 
WORKABLE_COMPANIES = [
    # Format: (display_name, subdomain)
    # subdomain = the first part of <subdomain>.workable.com
 
    # ── Cybersecurity / Pentest firms that use Workable ──
    ("Appknox", "appknox"),           # India mobile app security testing — verified Workable user
    ("Coda Security", "codasecurity"), # cloud security services
    ("Encode", "encode"),             # cybersecurity consultancy
    ("Hadrian", "hadrian"),           # attack surface mgmt
    ("Outpost24", "outpost24"),       # external attack surface / EASM
    ("Pentera", "pentera"),           # automated pentest — India team
    ("RiskRecon", "riskrecon"),       # third-party risk / TPRM
    ("CyberSmart", "cybersmart"),     # cyber risk rating
 
    # ── GRC / Compliance tools ──
    ("Splashtop", "splashtop"),       # remote access / security
    ("Protera", "protera"),           # SAP cloud security — confirmed Workable
    ("CimTrak", "cimtrak"),          # file integrity / compliance
 
    # ── Fraud / AML / FinCrime ──
    ("Featurespace", "featurespace"), # ML fraud detection — India team
    ("Hawk", "hawk-ai"),             # AML / transaction monitoring
    ("Salv", "salv"),                # AML platform
    ("Napier", "napier"),            # AML compliance
 
    # ── Identity / IAM ──
    ("IS Decisions", "isdecisions"),  # access control / MFA
    ("Strivacity", "strivacity"),     # CIAM
 
    # ── India cybersec / IT companies ──
    ("Securonix", "securonix"),       # cloud SIEM — India team (Hyderabad GCC)
    ("Seclore", "seclore"),          # data-centric security — India-founded
    ("CrowdSec", "crowdsec"),         # collaborative security
    ("Darktrace", "darktrace"),       # AI cybersecurity — India office
]
 

# ═══════════════════════════════════════════════════════════════════════
# SMARTRECRUITERS COMPANIES (Public API — no auth required)
# All 20 verified working via api.smartrecruiters.com
# ═══════════════════════════════════════════════════════════════════════


SMARTRECRUITERS_COMPANIES = [
    ("Visa", "visa"),
    ("Bosch", "bosch"),
    ("IKEA", "IKEA"),
    ("Skechers", "skechers"),
    ("Logitech", "logitech"),
    ("Autodesk", "autodesk"),
    ("LinkedIn", "linkedin"),
    ("Equinix", "equinix"),
    ("Twilio", "twilio"),
    ("Atlassian", "atlassian"),
    ("Zendesk", "zendesk"),
    ("DocuSign", "docusign"),
    ("SoFi", "sofi"),
    ("Square", "square"),
    ("Toast", "toast"),
    ("ServiceTitan", "servicetitan"),
    ("Instacart", "instacart"),
    ("DoorDash", "doordash"),
    ("Lyft", "lyft"),
    ("Robinhood", "robinhood"),
]  

SR_API = "https://api.smartrecruiters.com/v1/companies/{cid}/postings"


def _sr_get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=_JSON_HEADERS, timeout=20)
    except requests.RequestException as exc:
        logger.warning("SmartRecruiters %s: %s", url, type(exc).__name__)
        return None
    if r.status_code != 200:
        logger.warning("SmartRecruiters HTTP %s for %s", r.status_code, url)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _scrape_smartrecruiters_company(name, cid, get=_sr_get) -> list[dict]:
    out, offset = [], 0
    while offset < 300:
        data = get(SR_API.format(cid=cid), {"country": "in", "limit": 100, "offset": offset}) or {}
        items = data.get("content") or []
        for p in items:
            title = str(p.get("name") or "").strip()
            if not title or not _title_ok(title):
                continue
            loc = p.get("location") or {}
            place = ", ".join(x for x in (loc.get("city"), loc.get("region"), str(loc.get("country") or "").upper()) if x)
            if place and not location_ok(place):
                continue
            pid = str(p.get("id") or "")
            detail = get(f"{SR_API.format(cid=cid)}/{pid}", None) or {}
            sections = (detail.get("jobAd") or {}).get("sections") or {}
            desc = " ".join(_html_to_text((sections.get(k) or {}).get("text"))
                            for k in ("jobDescription", "qualifications", "additionalInformation")).strip()
            out.append({
                "title": title, "company": name, "location": place or "India", "is_remote": bool(loc.get("remote")),
                "job_url": str(detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{cid}/{pid}"),
                "description": desc[:4000], "date_posted": str(p.get("releasedDate") or "")[:10],
                "source": "smartrecruiters",
            })
        offset += len(items)
        if not items or offset >= int(data.get("totalFound") or 0):
            break
    return out


def _scrape_smartrecruiters() -> list[dict]:
    logger.info("=== SmartRecruiters: %d companies (country=in) ===", len(SMARTRECRUITERS_COMPANIES))
    results: list[dict] = []
    for name, cid in SMARTRECRUITERS_COMPANIES:
        results.extend(_scrape_smartrecruiters_company(name, cid))
        time.sleep(1)
    logger.info("SmartRecruiters: %d jobs", len(results))
    return results

################################]


WORKDAY_SEARCH_QUERIES = [
    "VAPT Intern",
    "Cybersecurity Intern",
    "security Intern",
    "Networking Security Intern",
    "Cloud Security Engineer",
    "SOC Analyst",
    "Information Security Analyst",
    "Cyber Security Analyst",
    "Cyber Security Engineer",
    "Cloud Security Engineer",
    "Data Security Engineer",
    "Risk Analyst",
    "Compliance Analyst",
    "Fraud Risk Analyst",
    "IAM Analyst", 
    "Network Engineer" ,
    "IT Audit",
    "Desk Support",
    "GRC analyst",
]


WORKDAY_ALLOWED_LOCATIONS = ("india", "bengaluru", "bangalore")
GREENHOUSE_TITLE_KEYWORDS = WORKDAY_TITLE_KEYWORDS

FQ_FRESHER = (
    '(fresher OR "entry level" OR "entry-level" OR junior OR trainee OR associate '
    'OR graduate OR "0-2 years" OR "0 to 2 years" OR "upto 2 years" OR "0-1 years" OR "1-2 years" OR "0-2 yrs" OR "0 to 2 yrs" OR "upto 2 yrs" OR "0-1 yrs" OR "1-2 yrs" '
    'OR "0-1 year" OR "less than 2 years" OR associate)'
)

FQ_INTERN = (
    '(intern OR internship OR stipend OR "6 month" OR "3 month" '
    'OR "summer intern" OR "winter intern" OR apprentice '
    'OR fellowship OR "graduate trainee" OR "trainee")'
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

SCRAPE_EXTRA_LOCATIONS = os.environ.get("SCRAPE_EXTRA_LOCATIONS", "1") != "0"

LINKEDIN_CORE_TERMS = [
    qf('"SOC analyst" OR "security analyst" OR "cyber security analyst"'),
    qf('"SIEM analyst" OR "SIEM engineer" OR "threat detection" OR "MDR analyst"'),
    qf('"threat intelligence analyst" OR "incident response analyst" OR "DFIR analyst"'),
    qf('"VAPT" OR "penetration tester" OR "application security engineer" OR "DevSecOps engineer"'),
    qf('"vulnerability management" OR "cloud security engineer" OR "network security engineer"'),
    qf('"IAM analyst" OR "IAM engineer" OR "SailPoint" OR "CyberArk" OR "Okta" OR "PAM analyst"'),
    qf('"GRC analyst" OR "IT audit" OR "IT auditor" OR "ITGC" OR "SOX"'),
    qf('"risk analyst" OR "technology risk" OR "operational risk" OR "third party risk"'),
    qf('"compliance analyst"'),
    qf('"network engineer" OR "NOC engineer" OR "network operations" OR "network administrator"'),
    qi('"cybersecurity intern" OR "security intern" OR "SOC intern" OR "GRC intern"'),
]

# roles the old terms never asked for: network and IT support, AI security, and the non full time types
LINKEDIN_EXTRA_TERMS = [
    qf('"network engineer" OR "NOC engineer" OR "WLAN engineer" OR "network operations analyst"'),
    qf('"IT support" OR "technical support engineer" OR "service desk" OR "IT operations analyst"'),
    qf('"AI red team" OR "AI security" OR "AI governance" OR "AI risk" OR "AI operations analyst"'),
    '("fixed term" OR FTC OR "contract role" OR contractual) (security OR cyber OR risk OR compliance OR audit OR IAM)',
    '(apprentice OR apprenticeship OR "graduate trainee") (security OR cyber OR IT OR operations OR risk OR compliance)',
    '("walk-in" OR "walk in drive") (fraud OR AML OR security OR risk OR compliance OR investigation)',
]

LINKEDIN_PLAN = [
    # (jobspy location, remote only, terms)
    ("Bengaluru/Bangalore, Karnataka, India", False, LINKEDIN_TERMS + LINKEDIN_EXTRA_TERMS),
    ("India", True, [f'{t} (remote OR "work from home")' for t in LINKEDIN_CORE_TERMS]),
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
    "site:linkedin.com hiring bengaluru cybersecurity fresher 2026",
    "site:linkedin.com hiring bengaluru SOC analyst fresher",
    "site:linkedin.com hiring bengaluru GRC compliance analyst fresher",
    "site:linkedin.com hiring bengaluru KYC AML fraud analyst fresher",
    "site:linkedin.com hiring bengaluru IAM security analyst fresher",
    "site:linkedin.com opening bengaluru cybersecurity entry level",
    "site:linkedin.com urgent hiring bengaluru information security analyst",
    "site:linkedin.com bangalore immediate joining cybersecurity fresher",
    "site:linkedin.com bangalore SOC analyst hiring fresher junior",
    "site:linkedin.com bangalore VAPT penetration tester fresher opening",
    "site:linkedin.com bangalore cloud security AWS GCP fresher hiring",
    "site:linkedin.com bangalore risk analyst compliance fresher opening",
    "site:linkedin.com bangalore IT audit CISA fresher hiring",
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
  


   "site:internshala.com cybersecurity internship bangalore",
   "site:internshala.com information security internship bangalore",
   "site:internshala.com SOC intern bangalore",
   "site:internshala.com GRC compliance internship bangalore",
   "site:internshala.com cloud security internship bangalore",
   "site:internshala.com ethical hacking VAPT internship bangalore",
   "site:internshala.com network security internship bangalore",
   "site:internshala.com risk compliance internship bangalore",
   "site:naukri.com cybersecurity analyst bangalore fresher",
   "site:naukri.com SOC analyst bangalore 0-2 years",
   "site:naukri.com GRC compliance analyst bangalore entry level",
   "site:naukri.com information security analyst bangalore junior",
   "site:naukri.com cloud security engineer bangalore fresher",
   "site:naukri.com VAPT penetration tester bangalore",
   "site:naukri.com IT audit risk analyst bangalore",
   "site:naukri.com cybersecurity intern bangalore stipend",
   "site:naukri.com security engineer bangalore 0-3 years",

  # ── Indian startup hiring posts ──
   "site:linkedin.com hiring bangalore razorpay OR groww OR browserstack security",
    "site:linkedin.com hiring bangalore fintech security fraud risk analyst",
    "site:linkedin.com hiring bangalore startup security engineer fresher",
    "site:linkedin.com security engineer bangalore YC startup hiring",
    "site:linkedin.com GRC compliance analyst bangalore fintech startup",
    "site:linkedin.com fraud analyst bangalore neobank fintech hiring",
]






############

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

# RSS frequently returns SEO/directory pages instead of concrete openings.
# Example:
#   "Cyber Security Internships in Purnia: Find 40 Best Cyber Security Intern Jobs in Purnia - Internshala"
GARBAGE_TITLE_REGEXES = [
    re.compile(
        r"\binternships?\s+in\s+[^:]{2,80}:\s*find\s+\d+\s+"
        r"(?:best\s+)?[\w\s/&+.-]+?\s+jobs?\s+in\s+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfind\s+\d+\s+(?:best\s+)?[\w\s/&+.-]+?\s+jobs?\s+in\s+"
        r"[\w\s/&+.-]+(?:\s+-\s+internshala)?\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bfind\s+\d+\s+(?:best\s+)?[\w\s/&+.-]+?\s+jobs?\s+"
        r"(?:-\s+internshala)?\.?$",
        re.IGNORECASE,
    ),
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
    if any(pattern.search(title) for pattern in GARBAGE_TITLE_REGEXES): return False
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

def _emails_text(v) -> str:
    if isinstance(v, (list, tuple, set)):
        return ", ".join(map(str, v))
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return "" if v is None else str(v)


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
            "emails":      _nk_clean(d.get("emails")),
        })
    return records


def _normalize_workday_jobs_url(url: str) -> str:
    clean = (url or "").strip().rstrip("/")
    if not clean:
        raise ValueError("Empty Workday tenant URL")
    if not clean.endswith("/jobs"):
        raise ValueError(f"Workday URL must end with /jobs: {url}")
    return clean


def _workday_cxs_api_root(jobs_url: str) -> str:
    """CXS JSON APIs use .../wday/cxs/{tenant}/{site} — detail lives here, not under .../jobs."""
    clean = (jobs_url or "").strip().rstrip("/")
    if clean.endswith("/jobs"):
        return clean[:-5]
    return clean


def _workday_detail_json_url(jobs_url: str, external_path: str) -> str:
    """Full URL for GET job detail JSON (ApplyPilot workday_detail)."""
    root = _workday_cxs_api_root(jobs_url)
    ext = (external_path or "").strip()
    if not ext:
        return ""
    if ext.startswith("/"):
        return root + ext
    return f"{root}/{ext}"


def _workday_public_job_url(jobs_url: str, external_path: str) -> str:
    """Browser careers URL: {origin}/{site_id}{externalPath} (human-readable posting link)."""
    u = urlparse(jobs_url)
    parts = u.path.strip("/").split("/")
    if len(parts) >= 5 and parts[0] == "wday" and parts[1] == "cxs" and parts[-1] == "jobs":
        site = parts[-2]
        ext = (external_path or "").strip()
        if ext and not ext.startswith("/"):
            ext = "/" + ext
        return f"{u.scheme}://{u.netloc}/{site}{ext}"
    return ""


def _load_workday_companies() -> list[tuple[str, str]]:
    """
    Optional override:
      WORKDAY_COMPANIES_JSON='[["Name","https://.../jobs"], ...]'
    """
    raw = os.environ.get("WORKDAY_COMPANIES_JSON", "").strip()
    if not raw:
        return WORKDAY_COMPANIES

    try:
        parsed = json.loads(raw)
        companies: list[tuple[str, str]] = []
        for item in parsed:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                continue
            name = str(item[0]).strip()
            url = str(item[1]).strip()
            if name and url:
                companies.append((name, url))
        return companies or WORKDAY_COMPANIES
    except Exception as exc:
        logger.warning("Invalid WORKDAY_COMPANIES_JSON, using defaults: %s", exc)
        return WORKDAY_COMPANIES


def _extract_workday_job_id(posting: dict) -> str:
    if not isinstance(posting, dict):
        return ""

    bullet = posting.get("bulletFields")
    bullet_id = ""
    if isinstance(bullet, list):
        for item in bullet:
            if isinstance(item, dict) and item.get("id"):
                bullet_id = item.get("id")
                break

    candidates = [
        bullet_id,
        posting.get("jobReqId"),
        posting.get("externalPath"),
        posting.get("id"),
    ]
    for c in candidates:
        if c:
            return str(c).strip("/")
    return ""


def _extract_posted_date(posting: dict) -> str:
    if not isinstance(posting, dict):
        return ""

    raw = (
        posting.get("postedOn")
        or posting.get("postedDate")
        or posting.get("startDate")
        or posting.get("timeType")
        or ""
    )
    text = str(raw).strip()
    if not text:
        return ""
    if "T" in text:
        return text.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return text


def _extract_detail_description(detail_payload: dict) -> str:
    if not isinstance(detail_payload, dict):
        return ""
    candidates = [
        detail_payload.get("jobPostingInfo", {}).get("jobDescription"),
        detail_payload.get("jobDescription"),
        detail_payload.get("description"),
        detail_payload.get("jobPostingInfo", {}).get("externalDescription"),
    ]
    for c in candidates:
        if c:
            return _html_to_text(str(c))
    return ""


async def _workday_post_with_retry(
    client: httpx.AsyncClient,
    url: str,
    company_name: str,
    payload: dict,
    retries: int = 3,
    log_client_error: bool = True,
) -> dict:
    for attempt in range(1, retries + 1):
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 429:
                wait = min(2 ** attempt, 10)
                logger.warning("%s: rate limited (429), retry %d/%d in %ss",
                               company_name, attempt, retries, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} server error",
                    request=resp.request,
                    response=resp,
                )
            if resp.status_code >= 400:
                # Return client errors so caller can try alternate payloads.
                log_fn = logger.warning if log_client_error else logger.debug
                log_fn("%s: Workday returned %s for payload keys=%s",
                       company_name, resp.status_code, sorted(payload.keys()))
                return {
                    "__http_error__": resp.status_code,
                    "__response_text__": (resp.text or "")[:400],
                }
            resp.raise_for_status()
            raw = (resp.text or "").strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(
                    "%s: non-JSON Workday response for payload keys=%s (prefix=%r)",
                    company_name, sorted(payload.keys()), raw[:120],
                )
                return {}
            return parsed if isinstance(parsed, dict) else {}
        except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
            if attempt >= retries:
                logger.warning("%s: request failed after %d attempts: %s",
                               company_name, retries, exc)
                return {}
            backoff = min(2 ** attempt, 8)
            logger.warning("%s: request error (%s), retry %d/%d in %ss",
                           company_name, exc, attempt, retries, backoff)
            await asyncio.sleep(backoff)
    return {}


async def _fetch_workday_job_description(
    client: httpx.AsyncClient,
    jobs_url: str,
    company_name: str,
    posting: dict,
) -> str:
    raw_ext = str(posting.get("externalPath") or "").strip()
    if not raw_ext:
        return str(posting.get("description") or "")

    # JSON detail is .../cxs/{tenant}/{site}/job/... — never insert .../jobs/ before /job/...
    detail_url = _workday_detail_json_url(jobs_url, raw_ext)
    if not detail_url:
        return str(posting.get("description") or "")

    try:
        r = await client.get(detail_url)
        if r.status_code == 200:
            raw = (r.text or "").strip()
            if not raw:
                return str(posting.get("description") or "")
            try:
                detail_json = json.loads(raw)
            except json.JSONDecodeError:
                return str(posting.get("description") or "")
            desc = _extract_detail_description(detail_json)
            if desc:
                return desc
    except Exception:
        pass

    return str(posting.get("description") or "")


def filter_workday_jobs(
    jobs: list[dict],
    title_keywords: tuple[str, ...] | list[str] | None = None,
    allowed_locations: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    title_keywords = tuple(k.lower() for k in (title_keywords or ()))
    allowed_locations = tuple(c.lower() for c in (allowed_locations or ()))

    filtered = []
    for job in jobs:
        title = str(job.get("title") or "").lower()
        location = str(job.get("location") or "").lower()

        title_ok = True if not title_keywords else any(k in title for k in title_keywords)
        location_ok_flag = _workday_loc_state(job.get("location")) != "no"
        #location_ok = True if not allowed_locations else any(c in location for c in allowed_locations)
        if title_ok and location_ok_flag:
            filtered.append(job)
    return filtered


async def _scrape_workday_company(
    client: httpx.AsyncClient,
    company_name: str,
    jobs_url: str,
    search_queries: list[str],
    title_keywords: tuple[str, ...] | list[str] | None = None,
    allowed_locations: tuple[str, ...] | list[str] | None = None,
) -> list[dict]:
    jobs_url = _normalize_workday_jobs_url(jobs_url)

    all_postings: list[dict] = []
    total_results_sum = 0
    query_list = [q.strip() for q in (search_queries or []) if str(q).strip()]
    if not query_list:
        query_list = [""]
      
    failed_queries = 0
  
    for search_query in query_list:
        failed_here = False
        for page_idx in range(WORKDAY_MAX_PAGES):
            offset = page_idx * WORKDAY_PAGE_SIZE
            payload_variants = [
                {
                    "appliedFacets": {},
                    "limit": WORKDAY_PAGE_SIZE,
                    "offset": offset,
                    "searchText": search_query,
                },
                {
                    "appliedFacets": {},
                    "limit": WORKDAY_PAGE_SIZE,
                    "offset": offset,
                    "searchText": search_query,
                    "userPreferredLanguage": "en-US",
                },
                {
                    "appliedFacets": [],
                    "limit": WORKDAY_PAGE_SIZE,
                    "offset": offset,
                    "searchText": search_query,
                },
            ]

            response_json: dict = {}
            last_status = None
            for payload in payload_variants:
                response_json = await _workday_post_with_retry(
                    client, jobs_url, company_name, payload, log_client_error=False,
                )
                if response_json and not response_json.get("__http_error__"):
                    break
                last_status = response_json.get("__http_error__")

            if not response_json or response_json.get("__http_error__"):
                logger.warning(
                    "%s: Workday search failed offset=%s query=%r (tried %d payload variants, last HTTP=%s)",
                    company_name, offset, search_query, len(payload_variants), last_status,
                )
                failed_here = True
                break

            total_results = int(response_json.get("total", 0) or 0)
            postings = response_json.get("jobPostings", []) or []
            if not isinstance(postings, list) or not postings:
                break
            total_results_sum += total_results
            all_postings.extend(postings)

            if len(all_postings) >= WORKDAY_MAX_RESULTS:
                break
            if offset + WORKDAY_PAGE_SIZE >= total_results:
                break
        if failed_here:
            failed_queries += 1
            if failed_queries >= 3:
                logger.warning("%s: 3 queries failed, skipping the rest of this tenant", company_name)
                break
        if len(all_postings) >= WORKDAY_MAX_RESULTS:
            break

    logger.info("%s: %d total results", company_name, total_results_sum)

    # Dedup by Workday job ID within company response.
    deduped: list[dict] = []
    seen_job_ids: set[str] = set()
    for posting in all_postings[:WORKDAY_MAX_RESULTS]:
        if not isinstance(posting, dict):
            continue
        job_id = _extract_workday_job_id(posting)
        if job_id and job_id in seen_job_ids:
            continue
        if job_id:
            seen_job_ids.add(job_id)
        deduped.append(posting)

    records: list[dict] = []
    for posting in deduped:
        title = str(posting.get("title") or "").strip()
        raw_external = str(posting.get("externalPath") or "")
        ext_path = raw_external.strip("/")
        location = str(
            posting.get("locationsText") or posting.get("location")
            or posting.get("formattedLocation") or ""
        ).strip()
        job_id = _extract_workday_job_id(posting) or ext_path
        external_url = (
            _workday_public_job_url(jobs_url, raw_external)
            or _workday_detail_json_url(jobs_url, raw_external)
            or jobs_url
        )
        records.append({
            "title": title, "company": company_name, "location": location,
            "job_url": external_url, "description": "",
            "date_posted": _extract_posted_date(posting), "source": "workday",
            "job_id": job_id, "_posting": posting,
        })
    logger.info("%s sample: %s", company_name, [(r["title"][:40], r["location"][:30]) for r in records[:5]])
    filtered = filter_workday_jobs(
        records, title_keywords=title_keywords, allowed_locations=allowed_locations,
    )
    sem = asyncio.Semaphore(4)

    async def _detail(rec):
        async with sem:
            desc, locs = await _fetch_workday_detail(client, jobs_url, company_name, rec.pop("_posting"))
        return rec, desc, locs

    kept, dropped = [], 0
    for rec, desc, locs in await asyncio.gather(*[_detail(r) for r in filtered]):
        rec["description"] = desc
        if _workday_loc_state(rec["location"]) == "maybe":
            ok = [x for x in locs if _INDIA_RE.search(x) and location_ok(x)]
            if not ok:
                dropped += 1
                continue
            rec["location"] = " | ".join(ok)
        kept.append(rec)
    filtered = kept
    logger.info("%s: %d dropped, no India location", company_name, dropped)
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

    workers = max(1, min(int(worker_count or 1), 3))
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


# ════════════════════════════════════════════════════════════════════════════
# GREENHOUSE SCRAPER - FIXED (uses requests, proper error handling)
# ════════════════════════════════════════════════════════════════════════════
def _scrape_greenhouse_company(company_slug: str) -> list[dict]:
    """
    Greenhouse boards API: GET https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    Returns JSON with 'jobs' array. No auth required.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        
        # Don't log 403/404 - many companies won't have India jobs
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        if not isinstance(data, dict) or "jobs" not in data:
            return []
        
        jobs = data.get("jobs", [])
        if not isinstance(jobs, list):
            return []
        
        results = []
        for job in jobs:
            title = str(job.get("title") or "").strip()
            
            # Location can be dict or string
            location_obj = job.get("location") or {}
            if isinstance(location_obj, dict):
                location = str(location_obj.get("name", ""))
            else:
                location = str(location_obj)
            
            # Filter: India/Bangalore only
            if location and not location_ok(location):
                continue
                if "india" not in loc_lower and "bangalore" not in loc_lower and "bengaluru" not in loc_lower:
                    continue
            
            # Filter: Security/risk/compliance roles only
            title_lower = title.lower()
            if not any(kw in title_lower for kw in [
                "security", "cyber", "risk", "compliance", "grc", "soc", 
                "iam", "fraud", "privacy", "audit", "vapt", "penetration"
            ]):
                continue
            
            job_url = job.get("absolute_url") or f"https://boards.greenhouse.io/{company_slug}/jobs/{job.get('id')}"
            
            results.append({
                "title": title,
                "company": company_slug.replace("-", " ").title(),
                "location": location or "India",
                "job_url": job_url,
                "description": _html_to_text(job.get("content"))[:4000], # Truncate long descriptions
                "date_posted": str(job.get("updated_at") or "")[:10],
                "source": "greenhouse",
            })
        
        if results:
            logger.info(f"  {company_slug}: {len(results)} security jobs in India")
        
        return results
        
    except requests.exceptions.Timeout:
        logger.debug(f"{company_slug}: Timeout")
        return []
    except requests.exceptions.RequestException as e:
        logger.debug(f"{company_slug}: {type(e).__name__}")
        return []
    except Exception as e:
        logger.debug(f"{company_slug}: Unexpected error - {type(e).__name__}")
        return []
 
def _scrape_greenhouse() -> list[dict]:
    logger.info(f"=== Greenhouse API: {len(GREENHOUSE_COMPANIES)} companies ===")
    all_results = []
    
    for company_slug in GREENHOUSE_COMPANIES:
        results = _scrape_greenhouse_company(company_slug)
        all_results.extend(results)
        time.sleep(1)  # Be respectful of rate limits
    
    logger.info(f"Greenhouse: {len(all_results)} jobs found")
    return all_results



def _lever_date(created_at) -> str:
    try:
        return datetime.fromtimestamp(int(created_at) / 1000, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""
# ════════════════════════════════════════════════════════════════════════════
# LEVER SCRAPER - FIXED (uses requests, proper error handling)
# ════════════════════════════════════════════════════════════════════════════
def _scrape_lever_company(company_slug: str) -> list[dict]:
    """
    Lever API: GET https://api.lever.co/v0/postings/{company}?mode=json
    Returns JSON array of postings. No auth required.
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        
        if resp.status_code != 200:
            return []
        
        jobs = resp.json()
        if not isinstance(jobs, list):
            return []
        
        results = []
        for job in jobs:
            title = str(job.get("text") or "").strip()
            
            # Location from categories
            categories = job.get("categories") or {}
            location = ""
            if isinstance(categories, dict):
                location = str(categories.get("location", ""))
            
            # Filter: India/Bangalore only
            if location and not location_ok(location):
                continue
                if "india" not in loc_lower and "bangalore" not in loc_lower and "bengaluru" not in loc_lower:
                    continue
            
            # Filter: Security/risk/compliance roles only
            title_lower = title.lower()
            if not any(kw in title_lower for kw in [
                "security", "cyber", "risk", "compliance", "grc", "soc",
                "iam", "fraud", "privacy", "audit", "vapt", "penetration"
            ]):
                continue
            
            job_url = job.get("hostedUrl") or job.get("applyUrl") or f"https://jobs.lever.co/{company_slug}/{job.get('id')}"
            
            # Get date
            created_at = str(job.get("createdAt", ""))
            date_posted = _lever_date(job.get("createdAt"))
            
            results.append({
                "title": title,
                "company": company_slug.replace("-", " ").title(),
                "location": location or "India",
                "job_url": job_url,
                "description": _html_to_text(job.get("descriptionPlain") or job.get("description"))[:4000],
                "date_posted": date_posted,
                "source": "lever",
            })
        
        if results:
            logger.info(f"  {company_slug}: {len(results)} security jobs in India")
        
        return results
        
    except requests.exceptions.Timeout:
        logger.debug(f"{company_slug}: Timeout")
        return []
    except requests.exceptions.RequestException as e:
        logger.debug(f"{company_slug}: {type(e).__name__}")
        return []
    except Exception as e:
        logger.debug(f"{company_slug}: Unexpected error - {type(e).__name__}")
        return []
 
def _scrape_lever() -> list[dict]:
    logger.info(f"=== Lever API: {len(LEVER_COMPANIES)} companies ===")
    all_results = []
    
    for company_slug in LEVER_COMPANIES:
        results = _scrape_lever_company(company_slug)
        all_results.extend(results)
        time.sleep(1)  # Be respectful of rate limits
    
    logger.info(f"Lever: {len(all_results)} jobs found")
    return all_results








# ════════════════════════════════════════════════════════════════
# HN WHO'S HIRING — Firebase API (works from GH Actions, no auth)
#
# Monthly thread posted first business day. Firebase CDN is not
# in any WAF blocklist. Covers YC companies + funded startups that
# never post on LinkedIn. Many India / remote roles.
#
# Thread IDs: update HN_HIRING_THREAD_IDS monthly.
# Find current thread: https://news.ycombinator.com/submitted?id=whoishiring
# ════════════════════════════════════════════════════════════════

HN_HIRING_THREAD_IDS = [
    43543518,   # May 2025
    42919245,   # Apr 2025
    42297316,   # Mar 2025
]

HN_FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0/item/{}.json"

HN_SECURITY_KEYWORDS = (
    "security", "cyber", "soc", "grc", "risk", "compliance",
    "iam", "appsec", "vapt", "pentest", "fraud", "infosec",
    "cloud security", "devsecops", "threat", "vulnerability",
)

HN_INDIA_KEYWORDS = (
    "india", "bangalore", "bengaluru", "remote", "anywhere",
    "worldwide", "global",
)

# Companies known to post India roles on HN (used to boost confidence)
HN_INDIA_COMPANIES = {
    "razorpay", "groww", "zepto", "browserstack", "postman",
    "hasura", "setu", "niyo", "open financial", "m2p",
    "signzy", "leegality", "zoho", "freshworks", "chargebee",
    "capillary", "mindtickle", "darwinbox", "locus", "keka",
    "smallcase", "zetwerk", "shiprocket", "delhivery", "meesho",
    "dunzo", "slice", "jupiter", "fi money", "salt",
    "hyperface", "zolve", "freo", "epifi", "navi",
    "simpl", "kredivo", "axio", "perfios", "karza",
    "bureau", "seon", "sardine",                          # fraud/risk startups
    "safe security", "sequretek", "sectona", "sattrix",   # Indian cybersec
    "lucideus", "appknox", "we45", "mwrinfosecurity",
}


def _hn_parse_comment(comment_text: str, comment_id: int) -> dict | None:
    """
    Parse a single HN Who's Hiring comment into a job record.
    Comments follow loose convention: "Company | Role | Location | ..."
    Returns None if comment doesn't match India + security criteria.
    """
    if not comment_text or len(comment_text.strip()) < 40:
        return None

    text_lower = comment_text.lower()

    # Must mention India / remote
    if not any(kw in text_lower for kw in HN_INDIA_KEYWORDS):
        return None

    # Must mention security / relevant domain
    if not any(kw in text_lower for kw in HN_SECURITY_KEYWORDS):
        return None

    # Parse first line: usually "Company | Role | Location | ..."
    first_line = comment_text.strip().split("\n")[0].strip()
    parts = [p.strip() for p in re.split(r"\s*[|/]\s*", first_line)]

    company = parts[0] if parts else "Unknown"
    title   = parts[1] if len(parts) > 1 else first_line[:80]
    location = parts[2] if len(parts) > 2 else "Remote / India"

    # Clean HTML tags that sometimes appear
    company = re.sub(r"<[^>]+>", "", company).strip()
    title   = re.sub(r"<[^>]+>", "", title).strip()

    if not title or len(title) < 3:
        title = first_line[:100]

    return {
        "title":       title[:120],
        "company":     company[:80],
        "location":    location[:80],
        "job_url":     f"https://news.ycombinator.com/item?id={comment_id}",
        "description": re.sub(r"<[^>]+>", " ", comment_text)[:1200],
        "date_posted": "",
        "source":      "hn_hiring",
    }


def _scrape_hn_hiring() -> list[dict]:
    """
    Fetch HN Who's Hiring threads via Firebase API.
    Each thread has 200-800 top-level comments, each = one job post.
    """
    logger.info("=== HN Who's Hiring: %d threads ===", len(HN_HIRING_THREAD_IDS))
    results: list[dict] = []
    seen_ids: set[int] = set()

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    })

    for thread_id in HN_HIRING_THREAD_IDS:
        try:
            # Fetch thread metadata to get top-level comment IDs
            thread_url = HN_FIREBASE_BASE.format(thread_id)
            r = session.get(thread_url, timeout=15)
            if r.status_code != 200:
                logger.warning("HN thread %d: HTTP %s", thread_id, r.status_code)
                continue

            thread_data = r.json()
            kids = thread_data.get("kids", [])  # top-level comment IDs
            logger.info("  Thread %d: %d top-level comments", thread_id, len(kids))

            found = 0
            for comment_id in kids[:600]:    # cap at 600 comments per thread
                if comment_id in seen_ids:
                    continue
                seen_ids.add(comment_id)

                try:
                    cr = session.get(HN_FIREBASE_BASE.format(comment_id), timeout=10)
                    if cr.status_code != 200:
                        continue
                    comment_data = cr.json()
                    if not isinstance(comment_data, dict):
                        continue
                    # Skip deleted/dead comments
                    if comment_data.get("deleted") or comment_data.get("dead"):
                        continue

                    text = comment_data.get("text", "")
                    record = _hn_parse_comment(text, comment_id)
                    if record:
                        results.append(record)
                        found += 1

                    time.sleep(0.05)   # 50ms between comment fetches — polite

                except Exception:
                    continue

            logger.info("  Thread %d: %d matching jobs", thread_id, found)
            time.sleep(2)

        except Exception as exc:
            logger.error("HN thread %d failed: %s", thread_id, exc)
            continue

    logger.info("HN Who's Hiring: %d total jobs", len(results))
    return results










# ════════════════════════════════════════════════════════════════════════════
# [Keep ALL your existing scrapers - LinkedIn, Google, Indeed, Posts]
# ════════════════════════════════════════════════════════════════════════════




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
    plan = LINKEDIN_PLAN if SCRAPE_EXTRA_LOCATIONS else LINKEDIN_PLAN[:1]
    total_terms = sum(len(t) for _, _, t in plan)
    logger.info("=== LinkedIn Jobs: %d searches across %d locations ===", total_terms, len(plan))
    seen: set = set()
    results: list[dict] = []
    empty_streak = 0
    n = 0
    for location, remote, terms in plan:
        label = "remote" if remote else location.split(",")[0]
        for term in terms:
            n += 1
            logger.info("  [%d/%d] %s | %s…", n, total_terms, label, term[:50])
            batch = _run_scrape(["linkedin"], term, {"location": location})
            kept = batch
            if remote:                       # keep only genuinely remote-looking results
                kept = []
                for r in batch:
                    loc = str(r.get("location") or "").strip().lower()
                    if _REMOTE_RE.search(f"{r.get('title', '')} {loc}") or loc in ("", "india"):
                        r["is_remote"] = True
                        kept.append(r)
            new = [r for r in kept if r["job_url"] not in seen]
            for r in new:
                seen.add(r["job_url"])
            results.extend(new)
            logger.info("      +%d (total %d)", len(new), len(results))
            if batch:
                empty_streak = 0
            else:
                empty_streak += 1
                if empty_streak >= 3:
                    cool = random.uniform(90, 150)
                    logger.warning("LinkedIn: %d empty batches in a row (likely 429), cooling off %.0fs",
                                   empty_streak, cool)
                    time.sleep(cool)
                    empty_streak = 0
            time.sleep(random.uniform(8, 14))
    logger.info("LinkedIn Jobs: %d unique", len(results))
    return results


def _scrape_google_jobs() -> list[dict]:
    logger.info("=== Google Jobs ===")
    seen: set = set()
    results = []
    terms = [
        # Existing terms
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

        # ── NEW: Google crawls startup ATS pages — reaches companies blocked via direct API ──
        # Startup-specific signals
        qf('"security engineer" OR "security analyst" Bangalore "Series A" OR "Series B" OR "funded"'),
        qf('"security" Bangalore "YC" OR "Y Combinator" OR "startup"'),

        # Indian company names that don't show on LinkedIn but post on Google Jobs
        qf('"security analyst" OR "security engineer" "Razorpay" OR "Groww" OR "BrowserStack"'),
        qf('"security" "Zepto" OR "Swiggy" OR "Zomato" OR "Meesho" OR "Flipkart"'),
        qf('"security analyst" OR "risk analyst" "Cred" OR "PhonePe" OR "Paytm" OR "Juspay"'),
        qf('"security" "Freshworks" OR "Zoho" OR "Chargebee" OR "Postman" OR "Hasura"'),
        qf('"security" OR "fraud" OR "risk" "Perfios" OR "Karza" OR "Bureau" OR "Signzy"'),
        qf('"security" "Darwinbox" OR "Keka" OR "Leegality" OR "Locus" OR "Zetwerk"'),

        # BFSI startups specifically — GRC/fraud/risk roles
        qf('"fraud analyst" OR "AML analyst" OR "KYC analyst" "fintech" Bangalore'),
        qf('"risk analyst" OR "compliance analyst" "NBFC" OR "neo bank" OR "neobank" Bangalore'),

        # Wellfound/AngelList via Google (reaches it without direct API)
        f'site:wellfound.com security analyst OR security engineer Bangalore {FQ_ALL}',
        f'site:wellfound.com fraud risk compliance Bangalore',

        # Cutshort via Google
        f'site:cutshort.io "security" OR "cyber" OR "risk" Bangalore',
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
    rejects = Counter()
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
                reason = None
                if not link or link in seen:                reason = "dup_or_empty"
                elif not _is_valid_post(title, link):       reason = "garbage"
                elif _is_profile_headline(title):           reason = "profile_headline"
                elif len(desc.strip()) < 80:                reason = "short_desc"
                else:
                    pub = entry.get("published_parsed")
                    if pub and (time.time() - time.mktime(pub)) / 86400 > MAX_AGE_DAYS:
                        reason = "too_old"
                if reason:
                    rejects[reason] += 1
                    continue
                seen.add(link)
                valid += 1
                results.append({"title": title, "company": "", "location": "Bengaluru", "job_url": link,
                                "description": f"{title}. {desc[:600]}", "date_posted": entry.get("published", ""),
                                "source": "linkedin_post"})

            logger.info("  [%d/%d] '%s…' → %d valid / %d total",
                        i+1, len(LINKEDIN_POST_QUERIES), query[:45],
                        valid, len(feed.entries))

        except Exception as e:
            logger.error("  Posts RSS error: %s", e)

        time.sleep(1)
    logger.info("LinkedIn Posts rejected: %s", dict(rejects))
    logger.info("LinkedIn Posts: %d valid posts", len(results))
    return results




# ═══════════════════════════════════════════════════════════════════════
# ⑤ ADD — Ashby scraper functions
# Paste these BEFORE the gather_all_listings() function
# ═══════════════════════════════════════════════════════════════════════
 
ASHBY_TITLE_KEYWORDS = (
    "security", "cyber", "soc", "risk", "compliance", "grc", "iam", "appsec",
    "cloud", "fraud", "privacy", "audit", "vapt", "penetration", "devsecops",
    "threat", "vulnerability", "identity", "cryptography", "forensic",
)
ASHBY_ALLOWED_LOCATIONS = ("india", "bengaluru", "bangalore", "remote", "worldwide", "global", "anywhere")
 
 
def _scrape_ashby_company(company_slug: str) -> list[dict]:
    """
    Ashby public job board API — no authentication required.
    GET https://api.ashbyhq.com/posting-api/job-board/{slug}
    Returns JSON: {"jobs": [{"title", "location", "jobUrl", "publishedAt",
                              "descriptionPlain", "isRemote", "department"}]}
    Docs: https://developers.ashbyhq.com/docs/public-job-posting-api
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
 
    try:
        resp = requests.get(url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []
 
        data = resp.json()
        if not isinstance(data, dict):
            return []
 
        jobs = data.get("jobs", [])
        if not isinstance(jobs, list):
            return []
 
        results = []
        for job in jobs:
            title = str(job.get("title") or "").strip()
 
            # Location — Ashby nests it
            location_obj = job.get("location") or {}
            if isinstance(location_obj, dict):
                location = str(location_obj.get("city") or location_obj.get("name") or "")
            else:
                location = str(location_obj)
 
            # Also check secondaryLocations and isRemote
            is_remote = bool(job.get("isRemote"))
            if not location and is_remote:
                location = "Remote"
 
            # Location filter: India / Bangalore / Remote
            loc_lower = location.lower()
            if not is_remote and location:
                if not is_remote and location and not location_ok(location): 
                    continue
 
            # Title keyword filter
            title_lower = title.lower()
            if not any(kw in title_lower for kw in ASHBY_TITLE_KEYWORDS):
                continue
 
            job_url = str(job.get("jobUrl") or "").strip()
            if not job_url:
                job_url = f"https://jobs.ashbyhq.com/{company_slug}"
 
            published_at = str(job.get("publishedAt") or "").strip()
            date_posted = published_at[:10] if published_at else ""
 
          
            description = _html_to_text(job.get("descriptionPlain") or job.get("descriptionHtml"))[:4000]
 
            results.append({
                "title": title,
                "company": company_slug.replace("-", " ").title(),
                "location": location or ("Remote" if is_remote else "India"),
                "job_url": job_url,
                "description": description,
                "date_posted": date_posted,
                "source": "ashby",
            })
 
        if results:
            logger.info("  ashby/%s: %d security jobs", company_slug, len(results))
        return results
 
    except requests.exceptions.Timeout:
        logger.debug("ashby/%s: Timeout", company_slug)
        return []
    except requests.exceptions.RequestException as e:
        logger.debug("ashby/%s: %s", company_slug, type(e).__name__)
        return []
    except Exception as e:
        logger.debug("ashby/%s: unexpected %s", company_slug, type(e).__name__)
        return []
 
 
def _scrape_ashby() -> list[dict]:
    logger.info("=== Ashby API: %d companies ===", len(ASHBY_COMPANIES))
    all_results = []
 
    for slug in ASHBY_COMPANIES:
        results = _scrape_ashby_company(slug)
        all_results.extend(results)
        time.sleep(1)  # polite rate limit
 
    logger.info("Ashby: %d jobs found", len(all_results))
    return all_results
 
 
# ═══════════════════════════════════════════════════════════════════════
# ⑥ ADD — Workable scraper functions
# Paste these AFTER the Ashby functions and BEFORE gather_all_listings()
# ═══════════════════════════════════════════════════════════════════════
 
WORKABLE_TITLE_KEYWORDS = ASHBY_TITLE_KEYWORDS  # reuse same set
WORKABLE_ALLOWED_LOCATIONS = ASHBY_ALLOWED_LOCATIONS
 
 
def _scrape_workable_company(company_name: str, subdomain: str) -> list[dict]:
    try:
        resp = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{subdomain}",
                            params={"details": "true"}, headers=_JSON_HEADERS, timeout=15)
        jobs = (resp.json() or {}).get("jobs") or [] if resp.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for job in jobs:
        title = str(job.get("title") or "").strip()
        if not title or not any(kw in title.lower() for kw in WORKABLE_TITLE_KEYWORDS):
            continue
        locs = job.get("locations") or []
        first = locs[0] if locs and isinstance(locs[0], dict) else {}
        place = ", ".join(x for x in (first.get("city") or job.get("city") or "",
                                      first.get("region") or job.get("state") or "",
                                      first.get("country") or job.get("country") or "") if x)
        country = str(first.get("country") or job.get("country") or "")
        remote = bool(job.get("telecommuting"))
        if place and not remote and not location_ok(place):
            continue
        out.append({
            "title": title, "company": company_name, "location": place or ("Remote" if remote else "India"),
            "is_remote": remote and (not country or country.lower() in ("india", "in")),
            "job_url": str(job.get("shortlink") or job.get("url") or f"https://apply.workable.com/{subdomain}/"),
            "description": _html_to_text(job.get("description"))[:4000],
            "date_posted": str(job.get("published_on") or job.get("created_at") or "")[:10],
            "source": "workable",
        })
    if out:
        logger.info("  workable/%s: %d security jobs", subdomain, len(out))
    return out
 
 
def _scrape_workable() -> list[dict]:
    logger.info("=== Workable API: %d companies ===", len(WORKABLE_COMPANIES))
    all_results = []
 
    for company_name, subdomain in WORKABLE_COMPANIES:
        results = _scrape_workable_company(company_name, subdomain)
        all_results.extend(results)
        time.sleep(1)
 
    logger.info("Workable: %d jobs found", len(all_results))
    return all_results
 
 
# ═══════════════════════════════════════════════════════════════════════
# ⑥ NAUKRI SCRAPER
# ═══════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════
# NAUKRI via jobspy
# Works from residential IPs. GitHub-hosted runners are usually blocked
# (HTTP 406 recaptcha), so this has a kill switch and a circuit breaker.
#   NAUKRI_ENABLED=0  -> skip entirely
# ═══════════════════════════════════════════════════════════════════════
NAUKRI_LOCATION         = "Bangalore"
NAUKRI_RESULTS_PER_TERM = 25
NAUKRI_MAX_AGE_DAYS     = MAX_AGE_DAYS
NAUKRI_MAX_EXP_YEARS    = 2      # drop roles whose minimum experience is above this
NAUKRI_FAIL_FAST        = 3      # abort after this many consecutive empty/blocked terms

# Naukri search is plain keywords, not boolean OR queries
NAUKRI_TERMS = [
    "soc analyst", "soc analyst fresher", "l1 soc analyst",
    "security analyst", "cyber security analyst", "information security analyst",
    "threat intelligence analyst", "incident response analyst",
    "vulnerability analyst", "vapt", "penetration tester",
    "siem analyst", "splunk analyst",
    "cloud security", "application security",
    "iam analyst", "grc analyst", "it audit", "compliance analyst",
    "risk analyst",
    "soc intern", "cybersecurity intern", "security intern",
    "network engineer", "noc engineer", "iam engineer", "sailpoint", "cyberark",
    "internal audit", "ai red team",
    "security apprentice", "fixed term security", "walk in fraud",
]
NAUKRI_CORE_TERMS = [
    "soc analyst", "security analyst", "cyber security analyst", "siem analyst", "vapt",
    "cloud security", "iam analyst", "grc analyst", "it audit", "risk analyst",
    "fraud analyst", "network engineer", "security intern",
]
NAUKRI_PLAN = [("Bangalore", NAUKRI_TERMS)]

_NAUKRI_WALK_IN_RE = re.compile(
    r"walk[\s\-]?in|walkin|direct interview|drive on|interview on|venue\s*:",
    re.IGNORECASE,
)
_NAUKRI_EXP_RE = re.compile(r"(\d+)\s*(?:-|to)\s*(\d+)")
_NAUKRI_BLOCK_HINTS = ("406", "recaptcha", "captcha", "forbidden", "403", "429")


def _nk_clean(v):
    """NaN / NaT / None -> '' so we never leak the strings 'nan' or 'None'."""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _nk_min_experience(exp: str) -> int | None:
    """'0-2 Yrs' -> 0, '3-5 Yrs' -> 3, '' -> None (unknown, keep the job)."""
    m = _NAUKRI_EXP_RE.search(exp or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", exp or "")
    return int(m.group(1)) if m else None


def _nk_date(value) -> str:
    ts = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(ts) else ts.strftime("%Y-%m-%d")

def _naukri_to_records(df, default_location="Bangalore"):
    if df is None or df.empty:
        return []

    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=NAUKRI_MAX_AGE_DAYS)
    records = []
    for _, row in df.iterrows():
        d = row.to_dict()

        title = _nk_clean(d.get("title"))
        url   = _nk_clean(d.get("job_url"))
        if not title or not url:
            continue

        # Freshness filter
        posted = _nk_date(d.get("date_posted"))
        if posted and pd.Timestamp(posted) < cutoff:
            continue

        # Experience filter: keep fresher-friendly roles, keep unknowns
        exp = _nk_clean(d.get("experience_range"))
        min_exp = _nk_min_experience(exp)
        if min_exp is not None and min_exp > NAUKRI_MAX_EXP_YEARS:
            continue

        desc = _nk_clean(d.get("description"))
        skills = _nk_clean(d.get("skills"))
        extras = []
        if exp:
            extras.append(f"Experience: {exp}")
        if skills:
            extras.append(f"Skills: {skills}")
        vac = _nk_clean(d.get("vacancy_count"))
        if vac:
            extras.append(f"Vacancies: {vac}")

        # Surface walk-in drives to the scorer LLM through the description,
        # so no schema change is needed in scorer/storage/notifier.
        is_walk_in = bool(_NAUKRI_WALK_IN_RE.search(f"{title} {desc}"))
        prefix = "WALK-IN DRIVE. " if is_walk_in else ""

        records.append({
            "title":       title,
            "company":     _nk_clean(d.get("company")),
            "location": _nk_clean(d.get("location")) or default_location,
            "job_url":     url,
            "description": (prefix + " | ".join(extras) + ". " + desc)[:1500],
            "date_posted": posted,
            "source":      "naukri",
            "emails":      _nk_clean(d.get("emails")),
            "tail":        desc[-800:],
        }) 
    return records
  
def _scrape_naukri() -> list[dict]:
    if os.environ.get("NAUKRI_ENABLED", "1").strip() == "0":
        logger.info("=== Naukri: disabled via NAUKRI_ENABLED=0 ===")
        return []

    plan = NAUKRI_PLAN if SCRAPE_EXTRA_LOCATIONS else NAUKRI_PLAN[:1]
    total_terms = sum(len(t) for _, t in plan)
    logger.info("=== Naukri (jobspy): %d searches across %d locations ===", total_terms, len(plan))
    seen: set = set()
    results: list[dict] = []
    dead_streak = 0
    n = 0
    for location, terms in plan:
        for term in terms:
            n += 1
            blocked = False
            batch: list[dict] = []
            try:
                df = jobspy.scrape_jobs(
                    site_name=["naukri"], search_term=term, location=location,
                    results_wanted=NAUKRI_RESULTS_PER_TERM,
                )
                errs = getattr(df, "attrs", {}).get("errors")
                if errs:
                    logger.warning("  Naukri errors for %r: %s", term, errs)
                    blocked = any(h in str(errs).lower() for h in _NAUKRI_BLOCK_HINTS)
                batch = _naukri_to_records(df, default_location=location)
            except Exception as exc:
                logger.warning("  Naukri failed for %r: %s", term, exc)
                blocked = any(h in str(exc).lower() for h in _NAUKRI_BLOCK_HINTS)

            new = [r for r in batch if r["job_url"] not in seen]
            for r in new:
                seen.add(r["job_url"])
            results.extend(new)
            logger.info("  [%d/%d] Naukri %s +%d (total %d) | %s", n, total_terms, location, len(new), len(results), term)

            dead_streak = dead_streak + 1 if (not batch and blocked) or (not batch and not results) else 0
            if dead_streak >= NAUKRI_FAIL_FAST:
                logger.warning("Naukri: %d consecutive empty/blocked terms, aborting (likely IP block).", dead_streak)
                return results
            time.sleep(4)

    logger.info("Naukri: %d unique (%d walk-in)", len(results),
                sum(r["description"].startswith("WALK-IN") for r in results))
    return results






# ═══════════════════════════════════════════════════════════════════════
# BIG EMPLOYER CAREER SITES + BUILT IN BENGALURU + SECURITY RSS FEEDS
# ═══════════════════════════════════════════════════════════════════════
# Prefix-boundary match, so "soc" hits "SOC Analyst" but not "Associate"
_BIG_TITLE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in WORKDAY_TITLE_KEYWORDS) + ")", re.I
)
_JSON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}
_HTML_HEADERS = {**_JSON_HEADERS, "Accept": "text/html,application/xhtml+xml"}

def _html_to_text(value) -> str:
    s = _html.unescape(str(value or ""))          # Greenhouse content is escaped markup
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()

def _plain(text) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))).strip()


def _title_ok(title: str) -> bool:
    return bool(_BIG_TITLE_RE.search(title or ""))


def _epoch_date(ts) -> str:
    try:
        ts = float(ts)
        if ts > 1e11:          # milliseconds
            ts /= 1000
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


# ── Amazon (public search.json behind amazon.jobs) ───────────────────────
AMAZON_SEARCH_URL = "https://www.amazon.jobs/en/search.json"
AMAZON_CITIES  = ("Bengaluru",)   # Amazon's city filter is exact: "Bangalore" returns nothing
AMAZON_QUERIES = [
    "security analyst", "cyber security", "information security", "SOC", "security engineer",
    "vulnerability", "penetration", "application security", "cloud security",
    "identity access management", "GRC compliance", "risk analyst", "fraud", "audit",
    "network engineer", "security intern",
]
AMAZON_PAGE_SIZE = 50
AMAZON_MAX_PAGES = 3


def _amazon_date(raw) -> str:
    raw = str(raw or "").strip()
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _scrape_amazon() -> list[dict]:
    logger.info("=== Amazon Jobs: %d cities x %d queries ===", len(AMAZON_CITIES), len(AMAZON_QUERIES))
    results: list[dict] = []
    seen: set[str] = set()
    blocked = False
    for city in AMAZON_CITIES:
        if blocked:
            break
        for q in AMAZON_QUERIES:
            if blocked:
                break
            for page in range(AMAZON_MAX_PAGES):
                params = {"base_query": q, "country": "IND", "city": city, "sort": "recent",
                          "offset": page * AMAZON_PAGE_SIZE, "result_limit": AMAZON_PAGE_SIZE}
                try:
                    r = requests.get(AMAZON_SEARCH_URL, params=params, headers=_JSON_HEADERS, timeout=20)
                except requests.RequestException as exc:
                    logger.debug("Amazon %s/%s: %s", city, q, type(exc).__name__)
                    break
                if r.status_code in (403, 429):
                    logger.warning("Amazon Jobs: HTTP %s, skipping it for the rest of this run", r.status_code)
                    blocked = True
                    break
                if r.status_code != 200:
                    break
                try:
                    data = r.json()
                except ValueError:
                    break
                jobs = data.get("jobs") if isinstance(data, dict) else None
                if not jobs:
                    break
                for j in jobs:
                    job_path = str(j.get("job_path") or "")
                    jid = str(j.get("id_icims") or j.get("id") or job_path)
                    if not jid or jid in seen:
                        continue
                    title = str(j.get("title") or "").strip()
                    if not _title_ok(title):
                        continue
                    country = str(j.get("country_code") or "")
                    if country and country != "IND":
                        continue
                    location = str(j.get("normalized_location") or j.get("location") or city)
                    if not location_ok(location):
                        continue
                    url = ("https://www.amazon.jobs" + job_path) if job_path.startswith("/") \
                        else str(j.get("url_next_step") or "")
                    if not url:
                        continue
                    parts = [_plain(j.get("description_short"))]
                    basic = _plain(j.get("basic_qualifications"))
                    if basic:   # feeds the experience rule ("3+ years of experience")
                        parts.append("Basic qualifications: " + basic)
                    seen.add(jid)
                    results.append({
                        "title": title, "company": "Amazon", "location": location,
                        "job_url": url, "description": " ".join(p for p in parts if p)[:1500],
                        "date_posted": _amazon_date(j.get("posted_date")), "source": "amazon",
                    })
                if len(jobs) < AMAZON_PAGE_SIZE:
                    break
                time.sleep(1)
            time.sleep(1)
    logger.info("Amazon Jobs: %d jobs", len(results))
    return results


# ── Eightfold-hosted career sites (best effort, add more rows as you find them) ──
EIGHTFOLD_SITES = [
    # (display name, careers base URL, eightfold domain)
    ("Microsoft", "https://apply.careers.microsoft.com", "microsoft.com"),
]
EIGHTFOLD_QUERIES = ["security analyst", "cyber security", "security engineer", "SOC", "compliance",
                     "risk analyst", "fraud", "identity", "network engineer", "security intern"]


def _scrape_eightfold() -> list[dict]:
    logger.info("=== Eightfold sites: %d ===", len(EIGHTFOLD_SITES))
    results: list[dict] = []
    seen: set[str] = set()
    for name, base, domain in EIGHTFOLD_SITES:
        dead = False
        for q in EIGHTFOLD_QUERIES:
            if dead:
                break
            for start in range(0, 40, 10):                   # 10 rows per page
                try:
                    r = requests.get(
                        f"{base}/api/pcsx/search",
                        params={"domain": domain, "query": q, "location": "India",
                                "start": start, "sort_by": "timestamp"},
                        headers=_JSON_HEADERS, timeout=20)
                except requests.RequestException as exc:
                    logger.debug("Eightfold %s: %s", name, type(exc).__name__)
                    dead = True
                    break
                if r.status_code != 200:
                    logger.warning("Eightfold %s: HTTP %s (endpoint may differ), skipping this site",
                                   name, r.status_code)
                    dead = True
                    break
                try:
                    data = r.json()
                except ValueError:
                    dead = True
                    break
                block = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
                positions = block.get("positions") if isinstance(block, dict) else None
                if not positions:
                    break
                for p in positions:
                    pid = str(p.get("id") or "")
                    title = str(p.get("name") or p.get("title") or "").strip()
                    if not pid or pid in seen or not _title_ok(title):
                        continue
                    locs = p.get("locations") or p.get("location") or ""
                    location = ", ".join(map(str, locs)) if isinstance(locs, list) else str(locs)
                    if location and not location_ok(location):
                        continue
                    rel = str(p.get("positionUrl") or p.get("canonicalPositionUrl") or f"/careers/job/{pid}")
                    seen.add(pid)
                    results.append({
                        "title": title, "company": name, "location": location or "India",
                        "job_url": base + rel if rel.startswith("/") else rel,
                        "description": "",                    # search results carry no description
                        "date_posted": _epoch_date(p.get("postedTs")), "source": "eightfold",
                    })
                if len(positions) < 10:
                    break
                time.sleep(1)
            time.sleep(1)
    logger.info("Eightfold: %d jobs", len(results))
    return results


# ── Built In Bengaluru ───────────────────────────────────────────────────

_LDJSON_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)


def _jobposting_from_html(html_text: str) -> dict | None:
    for m in _LDJSON_RE.finditer(html_text or ""):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        for o in stack:
            if not isinstance(o, dict):
                continue
            nodes = o.get("@graph") if isinstance(o.get("@graph"), list) else [o]
            for n in nodes:
                t = n.get("@type") if isinstance(n, dict) else None
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return n
    return None


def _jobposting_location(jp: dict) -> str:
    loc = jp.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address") if isinstance(loc, dict) else None
    if not isinstance(addr, dict):
        return ""
    country = addr.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    return ", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x)

def _scrape_builtin_hub(hub: str, base: str) -> list[dict]:
    session = requests.Session()
    session.headers.update(_HTML_HEADERS)
    candidates: dict[str, str] = {}
    template: str | None = None
    for q in BUILTIN_QUERIES:
        for page in range(1, BUILTIN_PAGES + 1):
            found = False
            for tpl in ([template] if template else BUILTIN_LIST_TEMPLATES):
                try:
                    r = session.get(base + tpl.format(q=quote(q), p=page), timeout=20)
                except requests.RequestException:
                    continue
                if r.status_code in (403, 429):
                    logger.warning("Built In %s: HTTP %s, skipping this hub", hub, r.status_code)
                    return []
                if r.status_code != 200:
                    continue
                links = _BUILTIN_JOB_RE.findall(r.text)
                if links:
                    if template is None:
                        template = tpl
                        logger.info("  Built In %s: list template that works: %s", hub, tpl)
                    for path in links:
                        candidates.setdefault(path, path.split("/")[2])
                    found = True
                    break
            if not found:
                break
            time.sleep(1.5)
    todo = [p for p, slug in candidates.items() if _title_ok(slug.replace("-", " "))][:BUILTIN_MAX_DETAIL]
    logger.info("  Built In %s: %d links, %d pass the title prefilter", hub, len(candidates), len(todo))

    results: list[dict] = []
    for path in todo:
        try:
            r = session.get(base + path, timeout=20)
        except requests.RequestException:
            continue
        if r.status_code in (403, 429):
            logger.warning("Built In %s: HTTP %s on a job page, stopping", hub, r.status_code)
            break
        if r.status_code != 200 or "this job was removed" in r.text.lower():
            continue
        jp = _jobposting_from_html(r.text)
        if jp:
            title = str(jp.get("title") or "").strip()
            org = jp.get("hiringOrganization")
            company = str(org.get("name") if isinstance(org, dict) else org or "")
            location = _jobposting_location(jp) or hub
            desc = _plain(jp.get("description"))
            posted = str(jp.get("datePosted") or "")[:10]
        else:                                    # no JSON-LD: title/company from <title>, short description
            t = _TITLE_TAG_RE.search(r.text)
            raw = _plain(t.group(1)).rsplit("|", 1)[0].strip() if t else ""
            title, _, company = raw.rpartition(" - ") if " - " in raw else (raw, "", "")
            m = _META_DESC_RE.search(r.text)
            location, desc, posted = hub, _plain(m.group(1)) if m else "", ""
        if not title or not _title_ok(title) or not location_ok(location):
            continue
        results.append({
            "title": title, "company": company.strip(), "location": location,
            "job_url": base + path, "description": desc[:1500],
            "date_posted": posted, "source": f"builtin_{hub.lower()}",
        })
        time.sleep(1.5)
    logger.info("Built In %s: %d jobs", hub, len(results))
    return results


def _scrape_builtin() -> list[dict]:
    logger.info("=== Built In hubs: %d ===", len(BUILTIN_HUBS))
    results: list[dict] = []
    for hub, base in BUILTIN_HUBS:
        results.extend(_scrape_builtin_hub(hub, base))
        time.sleep(2)
    return results

# ── Built In hubs: Bengaluru, Hyderabad, Pune ────────────────────────────
BUILTIN_HUBS = [
    ("Bengaluru", "https://builtinbengaluru.in"),

]
BUILTIN_QUERIES = ["security", "cyber", "soc analyst", "grc", "compliance", "risk analyst",
                   "fraud", "iam", "vapt", "network engineer", "audit", "privacy"]
BUILTIN_PAGES = 2
BUILTIN_MAX_DETAIL = 40          # job pages fetched per hub
# /jobs/<category>/search/<query> is a confirmed pattern; the security category slug is not,
# so these are tried in order and the first that returns job links is reused (see the log)
BUILTIN_LIST_TEMPLATES = [
    "/jobs/cybersecurity-it/search/{q}?page={p}",
    "/jobs/search/{q}?page={p}",
    "/jobs?search={q}&page={p}",
]
_BUILTIN_JOB_RE = re.compile(r'href="(?:https?://[a-z.]+)?(/job/[a-z0-9\-]+/\d+)"', re.I)
_TITLE_TAG_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_META_DESC_RE = re.compile(r'<meta[^>]+name="description"[^>]+content="([^"]*)"', re.I)




# ── Oracle HCM career sites (JPMorgan Chase). Add more (name, host, siteNumber) rows later. ──
ORACLE_HCM_SITES = [
    ("JPMorgan Chase", "jpmc.fa.oraclecloud.com", "CX_1001"),
]
ORACLE_HCM_QUERIES = ["security analyst", "cyber", "SOC", "information security", "risk analyst",
                      "compliance", "fraud", "audit", "identity access", "vulnerability",
                      "network engineer", "security intern"]
ORACLE_HCM_PAGE = 25
ORACLE_HCM_MAX_PAGES = 5


def _scrape_oracle_hcm() -> list[dict]:
    logger.info("=== Oracle HCM sites: %d ===", len(ORACLE_HCM_SITES))
    results: list[dict] = []
    for name, host, site in ORACLE_HCM_SITES:
        api = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        seen: set[str] = set()
        use_sort, dead = True, False
        for q in ORACLE_HCM_QUERIES:
            if dead:
                break
            for page in range(ORACLE_HCM_MAX_PAGES):
                finder = (f"findReqs;siteNumber={site},limit={ORACLE_HCM_PAGE},"
                          f"offset={page * ORACLE_HCM_PAGE},keyword={q}")
                if use_sort:
                    finder += ",sortBy=POSTING_DATES_DESC"
                try:
                    r = requests.get(api, params={"onlyData": "true", "expand": "requisitionList",
                                                  "finder": finder},
                                     headers=_JSON_HEADERS, timeout=30)
                except requests.RequestException as exc:
                    logger.debug("Oracle HCM %s: %s", name, type(exc).__name__)
                    break
                if r.status_code == 400 and use_sort:      # sortBy value not accepted: drop it for later requests
                    use_sort = False
                    break
                if r.status_code != 200:
                    logger.warning("Oracle HCM %s: HTTP %s, skipping this site", name, r.status_code)
                    dead = True
                    break
                try:
                    items = (r.json() or {}).get("items") or []
                except ValueError:
                    dead = True
                    break
                reqs = (items[0].get("requisitionList") if items and isinstance(items[0], dict) else None) or []
                for rq in reqs:
                    rid = str(rq.get("Id") or "")
                    title = str(rq.get("Title") or "").strip()
                    if not rid or rid in seen or not _title_ok(title):
                        continue
                    location = str(rq.get("PrimaryLocation") or "")
                    country = str(rq.get("PrimaryLocationCountry") or "").upper()
                    if country not in ("IN", "IND", "INDIA") and "india" not in location.lower():
                        continue
                    if not location_ok(location):
                        continue
                    seen.add(rid)
                    results.append({
                        "title": title, "company": name, "location": location,
                        "job_url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                        "description": _plain(rq.get("ShortDescriptionStr"))[:1500],
                        "date_posted": str(rq.get("PostedDate") or "")[:10],
                        "source": "oracle_hcm",
                    })
                if len(reqs) < ORACLE_HCM_PAGE:
                    break
                time.sleep(1)
            time.sleep(1)
    logger.info("Oracle HCM: %d jobs", len(results))
    return results



# ── Security job-board RSS feeds (global feeds, India filtered here) ─────
SECURITY_JOB_FEEDS = [
    # (source name, RSS/Atom URL). Open the site, copy its "RSS Feed" link, paste it here, e.g.
    # ("nulljobs", "<link from jobs.null.community>"),
    # ("isecjobs", "<link from isecjobs.com>"),
]
_FEED_INDIA_RE = re.compile(r"\b(?:india|bengaluru|bangalore)\b", re.I)


def _scrape_security_feeds() -> list[dict]:
    if not SECURITY_JOB_FEEDS:
        return []
    logger.info("=== Security RSS feeds: %d ===", len(SECURITY_JOB_FEEDS))
    results: list[dict] = []
    seen: set[str] = set()
    for name, url in SECURITY_JOB_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("  %s feed failed: %s", name, exc)
            continue
        kept = 0
        for e in feed.entries:
            link, title = e.get("link", ""), _plain(e.get("title", ""))
            summary = _plain(e.get("summary", "") or e.get("description", ""))
            if not link or link in seen or not title:
                continue
            if not _FEED_INDIA_RE.search(f"{title} {summary}"):
                continue
            pub = e.get("published_parsed")
            if pub and (time.time() - time.mktime(pub)) / 86400 > MAX_AGE_DAYS:
                continue
            seen.add(link)
            kept += 1
            results.append({
                "title": title, "company": "", "location": "India", "job_url": link,
                "description": summary[:1500], "date_posted": e.get("published", ""), "source": name,
            })
        logger.info("  %s: %d/%d kept", name, kept, len(feed.entries))
    return results



# ═══════════════════════════════════════════════════════════════════════
# REMOTE JOB BOARDS: Remotive, Himalayas, RemoteOK (public JSON, no key)
# Each board asks for a link back to the job, which the stored job_url already is.
# ═══════════════════════════════════════════════════════════════════════
REMOTE_TITLE_RE = re.compile(
    r"\b(?:security|cyber\w*|infosec|soc|siem|iam|identity|grc|compliance|audit\w*|privacy|threat|vulnerabilit\w*|"
    r"pentest\w*|penetration|appsec|fraud|detection|incident|forensic\w*|risk)\b", re.I)
_REMOTE_OK_RE = re.compile(r"worldwide|anywhere|global|\bapac\b|\basia\b|any\s+location|open\s+to\s+all|\bindia\b", re.I)


def _remote_loc_ok(loc) -> bool:
    s = str(loc or "").strip()
    return (not s) or bool(_REMOTE_OK_RE.search(s))


def _remote_record(title, company, loc, url, desc, date, source) -> dict:
    return {
        "title": str(title or "").strip(), "company": str(company or "").strip(),
        "location": f"Remote ({str(loc or '').strip() or 'Worldwide'})", "is_remote": True,
        "job_url": str(url or "").strip(), "description": _html_to_text(desc)[:4000],
        "date_posted": str(date or "")[:10], "source": source,
    }


def _scrape_remotive() -> list[dict]:
    try:
        r = requests.get("https://remotive.com/api/remote-jobs", headers=_JSON_HEADERS, timeout=30)
        jobs = (r.json() or {}).get("jobs", []) if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for j in jobs:                                   # one call, filtered here (the API allows about 2 requests a minute)
        if REMOTE_TITLE_RE.search(str(j.get("title") or "")) and _remote_loc_ok(j.get("candidate_required_location")):
            out.append(_remote_record(j.get("title"), j.get("company_name"), j.get("candidate_required_location"),
                                      j.get("url"), j.get("description"), j.get("publication_date"), "remotive"))
    logger.info("Remotive: %d/%d kept", len(out), len(jobs))
    return out


def _scrape_himalayas() -> list[dict]:
    out, seen = [], set()
    for q in ("security", "cyber", "soc analyst", "grc compliance", "fraud", "identity access"):
        try:
            r = requests.get("https://himalayas.app/jobs/api/search",
                             params={"q": q, "country": "IN", "sort": "recent", "page": 1},
                             headers=_JSON_HEADERS, timeout=30)
            data = r.json() if r.status_code == 200 else {}
        except (requests.RequestException, ValueError):
            continue
        for j in (data.get("jobs") or []):
            url = j.get("applicationLink") or j.get("guid") or ""
            if not url or url in seen or not REMOTE_TITLE_RE.search(str(j.get("title") or "")):
                continue
            restr = j.get("locationRestrictions") or []
            loc = ", ".join(map(str, restr)) if isinstance(restr, list) else str(restr)
            if not _remote_loc_ok(loc):
                continue
            seen.add(url)
            out.append(_remote_record(j.get("title"), j.get("companyName"), loc, url,
                                      j.get("description") or j.get("excerpt"), _epoch_date(j.get("pubDate")), "himalayas"))
        time.sleep(1.5)
    logger.info("Himalayas: %d kept", len(out))
    return out


def _scrape_remoteok() -> list[dict]:
    try:
        r = requests.get("https://remoteok.com/api", headers=_JSON_HEADERS, timeout=30)
        rows = r.json() if r.status_code == 200 else []
    except (requests.RequestException, ValueError):
        return []
    out = []
    for j in rows:
        if not isinstance(j, dict) or not j.get("position"):          # first element is a legal notice
            continue
        tags = " ".join(map(str, j.get("tags") or []))
        if not REMOTE_TITLE_RE.search(f"{j.get('position')} {tags}") or not _remote_loc_ok(j.get("location")):
            continue
        out.append(_remote_record(j.get("position"), j.get("company"), j.get("location"), j.get("url") or j.get("apply_url"),
                                  j.get("description"), j.get("date") or _epoch_date(j.get("epoch")), "remoteok"))
    logger.info("RemoteOK: %d/%d kept", len(out), len(rows))
    return out


def _scrape_remote_boards() -> list[dict]:
    logger.info("=== Remote boards: Remotive, Himalayas, RemoteOK ===")
    results: list[dict] = []
    for fn in (_scrape_remotive, _scrape_himalayas, _scrape_remoteok):
        try:
            results.extend(fn())
        except Exception as exc:
            logger.warning("%s failed: %s", fn.__name__, exc)
        time.sleep(2)
    logger.info("Remote boards: %d jobs", len(results))
    return results





def gather_all_listings() -> list[dict]:
    all_results = []
    seen: set   = set()

    sources = [
        ("Naukri",         _scrape_naukri),      # NEW
        ("LinkedIn Jobs",  _scrape_linkedin),
        ("LinkedIn Posts", fetch_linkedin_posts),
        ("Workday",        _scrape_workday),
        ("Greenhouse",     _scrape_greenhouse),       # ← ACTIVE
        ("Lever",          _scrape_lever), 
        ("Ashby",          _scrape_ashby),       # ← NEW: Ashby public API
        ("Workable",       _scrape_workable),
        ("JobRadar boards", _scrape_jobradar), 
        ("Amazon",            _scrape_amazon),
        ("Eightfold",         _scrape_eightfold),
        ("Security feeds",    _scrape_security_feeds),
        ("Oracle HCM",        _scrape_oracle_hcm),
        ("BuiltIn",           _scrape_builtin),
        ("Remote boards", _scrape_remote_boards),
        ("SmartRecruiters", _scrape_smartrecruiters),
      
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
