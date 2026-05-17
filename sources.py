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
import asyncio
import os
import json
import requests
from datetime import datetime
from urllib.parse import urlparse
import feedparser
import httpx
import jobspy
import pandas as pd
import re as _re


logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

LOCATION            = "Bengaluru/Bangalore, Karnataka, India"
HOURS_OLD           = 300
RESULTS_PER_TERM    = 40
WORKDAY_PAGE_SIZE   = 20
WORKDAY_MAX_PAGES   = 25
WORKDAY_MAX_RESULTS = WORKDAY_PAGE_SIZE * WORKDAY_MAX_PAGES
WORKDAY_TIMEOUT_S   = 30.0
WORKDAY_WORKERS     = 5

# CXS list URL must be /wday/cxs/{tenant}/{site_id}/jobs (see ApplyPilot employers.yaml).

WORKDAY_COMPANIES = [
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
    ("TD Bank", "https://td.wd3.myworkdayjobs.com/wday/cxs/td/TD_Bank_Careers/jobs"),
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
    "cloudflare",         # CDN / zero trust
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
    ("Visa",      "visa"),       # large Bangalore GCC, security roles
    ("Bosch",     "bosch"),      # Bangalore engineering hub
    ("Logitech",  "logitech"),   # India office
    ("Autodesk",  "autodesk"),   # Hyderabad + Bangalore presence
    ("LinkedIn",  "linkedin"),   # Bangalore office, security team
    ("Equinix",   "equinix"),    # India data centers, security ops
    ("Twilio",    "twilio"),     # Bangalore engineering
    ("Atlassian", "atlassian"),  # large Bangalore office
    ("Zendesk",   "zendesk"),    # Bangalore engineering
    ("DocuSign",  "docusign"),   # India office
    ("Square",    "square"),     # Block Inc, India presence
    ("Robinhood", "robinhood"),  # India engineering hub
]

################################]


WORKDAY_SEARCH_QUERIES = [
    "Tax Intern",
    "VAPT Intern",
    "Cybersecurity Intern",
    "Networking Security Intern",
    "Senior Security Engineer",
    "Cloud Security Engineer",
    "DevSecOps Engineer",
    "Application Security Engineer",
    "SOC Analyst",
    "Information Security Analyst",
    "Cyber Security Analyst",
    "Cloud Security Engineer",
    "Container Security Engineer",
    "Data Security Engineer",
    "Risk and Compliance Analyst",
    "Fraud Risk Analyst",
]
WORKDAY_TITLE_KEYWORDS = (
    "security", "cyber", "soc", "risk", "compliance", "grc", "iam", "appsec", "cloud"
)
WORKDAY_ALLOWED_LOCATIONS = ("india", "bengaluru", "bangalore")

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
  
    "site:instahyre.com cybersecurity security analyst bangalore",
    "site:instahyre.com SOC analyst GRC compliance bangalore",
    "site:instahyre.com information security risk analyst bangalore",
    "site:instahyre.com cloud security IAM analyst bangalore",
    "site:instahyre.com VAPT penetration appsec bangalore",
    "site:instahyre.com fraud KYC AML analyst bangalore",
    "site:instahyre.com IT audit compliance analyst bangalore",
    "site:instahyre.com threat intelligence incident response bangalore",
    "site:instahyre.com security intern internship bangalore",
    "site:instahyre.com soc intern internship bangalore",

   "site:iimjobs.com risk analyst compliance bangalore",
   "site:iimjobs.com GRC IT audit bangalore",
   "site:iimjobs.com KYC AML fraud analyst bangalore",
   "site:iimjobs.com information security compliance bangalore",
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
   "site:naukri.com KYC AML fraud analyst bangalore fresher",
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


# ═══════════════════════════════════════════════════════════
# SHARED FILTER HELPERS  — used by Greenhouse, Lever, Ashby,
#                          Workable, SmartRecruiters
# ═══════════════════════════════════════════════════════════

_SECURITY_KW = (
    "security", "cyber", "soc", "risk", "compliance", "grc", "iam",
    "appsec", "cloud", "fraud", "privacy", "audit", "vapt", "pentest",
    "penetration", "devsecops", "threat", "vulnerability", "identity",
    "forensic", "infosec", "krypto", "cryptography", "dlp", "edr", "siem",
)

_INDIA_LOC_KW = (
    "india", "bengaluru", "bangalore", "remote", "worldwide",
    "global", "anywhere", "hybrid", "",   # empty = pass through
)

def _is_security_role(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _SECURITY_KW)

def _is_india_eligible(location: str, title: str, description: str = "") -> bool:
    loc = location.lower().strip()
    if not loc:                                          # empty location → pass through
        return True
    if any(kw in loc for kw in _INDIA_LOC_KW if kw):
        return True
    # Some companies bury location in the description
    desc_snippet = description.lower()[:600]
    return any(kw in desc_snippet for kw in ("india", "bengaluru", "bangalore"))




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
            return str(c)
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
        location_ok = True if not allowed_locations else any(c in location for c in allowed_locations)
        if title_ok and location_ok:
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

    for search_query in query_list:
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
            posting.get("locationsText")
            or posting.get("location")
            or posting.get("formattedLocation")
            or ""
        ).strip()
        job_id = _extract_workday_job_id(posting) or ext_path
        external_url = (
            _workday_public_job_url(jobs_url, raw_external)
            or _workday_detail_json_url(jobs_url, raw_external)
            or jobs_url
        )

        description = await _fetch_workday_job_description(
            client=client,
            jobs_url=jobs_url,
            company_name=company_name,
            posting=posting,
        )
        records.append({
            "title": title,
            "company": company_name,
            "location": location,
            "job_url": external_url,
            "description": description,
            "date_posted": _extract_posted_date(posting),
            "source": "workday",
            "job_id": job_id,
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
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_slug}/jobs?content=true"
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
            if not _is_security_role(title):
                continue
            
            offices = job.get("offices") or []
            office_loc = " ".join(str(o.get("name") or "") for o in offices if isinstance(o, dict)).strip()
            effective_location = location or office_loc
            
            description_text = _re.sub(r"<[^>]+>", " ", str(job.get("content") or ""))[:2000]
            
            if not _is_india_eligible(effective_location, title, description_text):
                continue
            
            job_url = job.get("absolute_url") or f"https://boards.greenhouse.io/{company_slug}/jobs/{job.get('id')}"
            
            results.append({
                "title": title,
                "company": company_slug.replace("-", " ").title(),
                "location": location or "India",
                "job_url": job_url,
                "description": _re.sub(r"<[^>]+>", " ", str(job.get("content") or ""))[:1000],
                "date_posted": "",
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
 
# ════════════════════════════════════════════════════════════════════════════
# LEVER SCRAPER - FIXED (uses requests, proper error handling)
# ════════════════════════════════════════════════════════════════════════════
def _scrape_lever_company(company_slug: str) -> list[dict]:
    """
    Lever API: GET https://api.lever.co/v0/postings/{company}?mode=json
    Returns JSON array of postings. No auth required.
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}?mode=json&include=description"
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
            if not _is_security_role(title):
                continue
            
            workplace = str(job.get("workplaceType") or "").lower()
            if workplace == "remote" and not location:
                location = "Remote"
            
            description_raw = str(job.get("description") or job.get("descriptionPlain") or "")
            if not _is_india_eligible(location, title, description_raw):
                continue
            
            job_url = job.get("hostedUrl") or job.get("applyUrl") or f"https://jobs.lever.co/{company_slug}/{job.get('id')}"
            
          
            ts = job.get("createdAt")
            try:
                date_posted = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d") if ts else ""
            except Exception:
                date_posted = ""
            
            results.append({
                "title": title,
                "company": company_slug.replace("-", " ").title(),
                "location": location or "India",
                "job_url": job_url,
                "description": str(job.get("description") or job.get("descriptionPlain") or "")[:1000],
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

def _get_hn_thread_ids(max_threads: int = 2) -> list[int]:
    """Auto-fetch CURRENT month's HN hiring thread IDs via Algolia."""
    import time as _t, urllib.request as _ur
    FALLBACK = []   # May 2025 — update if Algolia keeps failing
    try:
        # Only look at threads created in the last 45 days
        min_ts = int(_t.time()) - (45 * 86400)
        url = (
            "https://hn.algolia.com/api/v1/search"
            "?query=Ask+HN%3A+Who+is+hiring"
            "&tags=story,ask_hn"
            f"&numericFilters=created_at_i%3E{min_ts}"
            "&hitsPerPage=5"
            "&attributesToRetrieve=objectID,title,created_at_i"
        )
        req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        ids = []
        for hit in data.get("hits", []):
            t = hit.get("title", "").lower()
            oid = hit.get("objectID", "")
            if "who is hiring" in t and "hired" not in t and oid:
                ids.append(int(oid))
                if len(ids) >= max_threads:
                    break
        if ids:
            logger.info("HN thread IDs (current): %s", ids)
            return ids
    except Exception as exc:
        logger.warning("HN auto-fetch failed (%s) — fallback", exc)
    return FALLBACK[:max_threads]

HN_HIRING_THREAD_IDS = _get_hn_thread_ids(max_threads=2)

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
                    age_days = (time.time() - time.mktime(published)) / 86400
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




# ═══════════════════════════════════════════════════════════════════════
# ⑤ ADD — Ashby scraper functions
# Paste these BEFORE the gather_all_listings() function
# ═══════════════════════════════════════════════════════════════════════
 
#ASHBY_TITLE_KEYWORDS = (
#    "security", "cyber", "soc", "risk", "compliance", "grc", "iam", "appsec",
#    "cloud", "fraud", "privacy", "audit", "vapt", "penetration", "devsecops",
#    "threat", "vulnerability", "identity", "cryptography", "forensic",
#)
#ASHBY_ALLOWED_LOCATIONS = ("india", "bengaluru", "bangalore", "remote", "worldwide", "global", "anywhere")
 
 
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
            if not _is_security_role(title):
                continue
            
            description_for_loc = str(job.get("descriptionPlain") or job.get("descriptionHtml") or "")
            if not _is_india_eligible(location, title, description_for_loc):
                continue
 
            job_url = str(job.get("jobUrl") or "").strip()
            if not job_url:
                job_url = f"https://jobs.ashbyhq.com/{company_slug}"
 
            published_at = str(job.get("publishedAt") or "").strip()
            date_posted = published_at[:10] if published_at else ""
 
            description = str(
                job.get("descriptionPlain")
                or job.get("descriptionHtml")
                or ""
            )[:1200]
 
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
 
#WORKABLE_TITLE_KEYWORDS = ASHBY_TITLE_KEYWORDS  # reuse same set
#WORKABLE_ALLOWED_LOCATIONS = ASHBY_ALLOWED_LOCATIONS
 
 
def _scrape_workable_company(company_name: str, subdomain: str) -> list[dict]:
    """
    Workable public job listing API — no authentication required.
    GET https://www.workable.com/api/accounts/{subdomain}?details=true
    Returns JSON: {"jobs": [{"title", "location", "url", "created_at", "description"}]}
    Docs: https://help.workable.com/hc/en-us/articles/4903195036183
    """
    url = f"https://www.workable.com/api/accounts/{subdomain}?details=true"
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
 
            # Location — Workable flat string or nested dict
            location_obj = job.get("location") or {}
            if isinstance(location_obj, dict):
                city    = str(location_obj.get("city") or "")
                country = str(location_obj.get("country") or "")
                region  = str(location_obj.get("region") or "")
                telecommuting = bool(location_obj.get("telecommuting"))
                location = ", ".join(filter(None, [city, region, country]))
            else:
                location = str(location_obj)
                telecommuting = False
 
 
            if not _is_security_role(title):
                continue
            
            description_raw = str(job.get("description") or job.get("full_description") or "")
            if not telecommuting and not _is_india_eligible(location, title, description_raw):
                continue
 
            job_url = str(job.get("url") or job.get("application_url") or job.get("shortlink") or "").strip()
            if not job_url:
                job_url = f"https://{subdomain}.workable.com"
 
            created_at = str(job.get("created_at") or "").strip()
            date_posted = created_at[:10] if created_at else ""
 
            description = str(job.get("description") or job.get("full_description") or "")[:1200]
 
            results.append({
                "title": title,
                "company": company_name,
                "location": location or ("Remote" if telecommuting else "India"),
                "job_url": job_url,
                "description": description,
                "date_posted": date_posted,
                "source": "workable",
            })
 
        if results:
            logger.info("  workable/%s: %d security jobs", subdomain, len(results))
        return results
 
    except requests.exceptions.Timeout:
        logger.debug("workable/%s: Timeout", subdomain)
        return []
    except requests.exceptions.RequestException as e:
        logger.debug("workable/%s: %s", subdomain, type(e).__name__)
        return []
    except Exception as e:
        logger.debug("workable/%s: unexpected %s", subdomain, type(e).__name__)
        return []
 
 
def _scrape_workable() -> list[dict]:
    logger.info("=== Workable API: %d companies ===", len(WORKABLE_COMPANIES))
    all_results = []
 
    for company_name, subdomain in WORKABLE_COMPANIES:
        results = _scrape_workable_company(company_name, subdomain)
        all_results.extend(results)
        time.sleep(1)
 
    logger.info("Workable: %d jobs found", len(all_results))
    return all_results
 




def _scrape_smartrecruiters_company(company_name: str, company_id: str) -> list[dict]:
    list_url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings?country=IN&limit=100"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(list_url, headers=headers, timeout=12)
        if resp.status_code != 200:
            return []
        jobs = resp.json().get("content", [])
        if not isinstance(jobs, list):
            return []
    except Exception as e:
        logger.debug("sr/%s: list fetch %s", company_id, type(e).__name__)
        return []

    results = []
    for job in jobs:
        title = str(job.get("name") or "").strip()
        if not _is_security_role(title):
            continue

        loc_obj  = job.get("location") or {}
        city     = str(loc_obj.get("city") or "")
        country  = str(loc_obj.get("country") or "")
        remote   = bool(loc_obj.get("remote"))
        location = ", ".join(filter(None, [city, country]))

        job_id  = str(job.get("id") or "")
        if not job_id:
            continue

        # Fetch description from detail endpoint (not available in list)
        description = ""
        try:
            det = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{job_id}",
                headers=headers, timeout=8,
            )
            if det.status_code == 200:
                sections = det.json().get("jobAd", {}).get("sections", {})
                description = str(sections.get("jobDescription", {}).get("text") or "")[:2000]
        except Exception:
            pass
        time.sleep(0.3)  # polite between detail fetches

        if not remote and not _is_india_eligible(location, title, description):
            continue

        job_url   = f"https://jobs.smartrecruiters.com/{company_id}/{job_id}"
        posted_on = str(job.get("releasedDate") or job.get("createdOn") or "")
        results.append({
            "title":       title,
            "company":     company_name,
            "location":    location or ("Remote" if remote else "India"),
            "job_url":     job_url,
            "description": description[:1000],
            "date_posted": posted_on[:10] if posted_on else "",
            "source":      "smartrecruiters",
        })

    if results:
        logger.info("  sr/%s: %d security jobs in India", company_id, len(results))
    return results

def _scrape_smartrecruiters() -> list[dict]:
    logger.info("=== SmartRecruiters API: %d companies ===", len(SMARTRECRUITERS_COMPANIES))
    all_results = []
    for company_name, company_id in SMARTRECRUITERS_COMPANIES:
        all_results.extend(_scrape_smartrecruiters_company(company_name, company_id))
        time.sleep(1)
    logger.info("SmartRecruiters: %d jobs found", len(all_results))
    return all_results
  




def gather_all_listings() -> list[dict]:
    all_results = []
    seen: set   = set()

    sources = [
        ("LinkedIn Jobs",  _scrape_linkedin),
        ("LinkedIn Posts", fetch_linkedin_posts),
        ("Workday",        _scrape_workday),
        ("Greenhouse",     _scrape_greenhouse),       # ← ACTIVE
        ("Lever",          _scrape_lever), 
        ("HN Hiring",      _scrape_hn_hiring),
        ("Ashby",          _scrape_ashby),       # ← NEW: Ashby public API
        ("Workable",       _scrape_workable),
        ("SmartRecruiters", _scrape_smartrecruiters),   # ← add this line

      
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
