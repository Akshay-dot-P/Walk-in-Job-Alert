"""
recruiters.py (v2) - plugs into YOUR scorer.py / storage.py. Two private tabs in your existing Sheet:

  Recruiters         contact details a posting ITSELF prints (email / phone / name hint)
  Outreach_Targets   companies hiring in each of your domains + one-click "find a recruiter" search links

Reuses your own engine: scorer.evaluate() decides what counts as a role in your domains/cities/age window,
scorer.domain_of() buckets it. Nothing here logs an email or phone number (Actions logs may be public).
The Walkin_Intern_FTC_Contract tab is already created by storage.ensure_section_tab, so it is not duplicated here.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

RECRUITER_TAB = "Recruiters"
TARGET_TAB = "Outreach_Targets"
RECRUITER_HEADER = ["first_seen", "name", "email", "phone", "company", "domain", "role_seen",
                    "job_url", "source", "status", "notes"]
TARGET_HEADER = ["company", "domain", "activity", "open_roles", "sample_titles", "sources", "last_seen",
                 "find_on_linkedin", "find_via_google", "status", "notes"]
_DOMAIN_TERMS = {
    "SOC": "SOC security operations", "VAPT": "penetration testing", "AppSec": "application security",
    "CloudSec": "cloud security", "IAM": "identity access management", "Forensics": "incident response",
    "GRC": "GRC compliance audit", "Risk": "risk", "Fraud-AML": "fraud investigation", "General": "cyber security",
}
_SKIP_COMPANIES = {"", "unknown", "confidential", "n/a", "nan", "none"}
_TIER = re.compile(r"\s*\((?:MNC|startup|mid-tier|unknown)\)\s*$", re.I)
_CO_NOISE = re.compile(r"\b(pvt|private|limited|ltd|inc|llc|llp|india|technologies|technology|services|solutions|corp|corporation)\b\.?", re.I)

# ── public contact details printed inside a posting ─────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_BAD_LOCAL = ("noreply", "no-reply", "donotreply", "do-not-reply", "unsubscribe", "privacy", "abuse", "postmaster",
              "webmaster", "mailer-daemon", "notifications", "support@")
_BAD_DOMAINS = ("example.com", "sentry.io", "wixpress.com", "linkedin.com", "naukri.com", "indeed.com", "glassdoor.com")
_BAD_END = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?([6-9]\d{4}[\s\-]?\d{5})(?!\d)")
_PHONE_CTX = re.compile(r"call|contact|whats\s?app|mobile|phone|reach|hr\b|recruit|resume|cv", re.I)
_NAME_RE = re.compile(
    r"(?i:contact(?:\s+person)?|recruiter|hr(?:\s+(?:name|contact))?|talent acquisition|posted by|reach out to|"
    r"call|whats\s?app|send (?:your )?(?:cv|resume)[^.\n]{0,25}?to)\s*[:\-]?\s*(?i:mr\.?|ms\.?|mrs\.?)?\s*"
    r"([A-Z][a-z]{2,15}(?:\s+[A-Z][a-z]{2,15})?)")
_NOT_NAMES = {"Details", "Team", "Email", "Number", "Person", "Information", "Info", "Hr", "Talent", "Human", "Resources",
              "Mail", "Mobile", "Phone", "Contact", "Acquisition", "Recruitment", "Department", "Us", "Immediately", "Now"}


def _plain(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or "")))


def extract_contacts(text: str, extra_emails=None) -> dict:
    t = _plain(text)
    found = list(_EMAIL_RE.findall(t))
    if extra_emails:
        seq = extra_emails if isinstance(extra_emails, (list, tuple, set)) else re.split(r"[,\s;]+", str(extra_emails))
        found += [str(x) for x in seq if "@" in str(x)]
    emails: list[str] = []
    for raw in found:
        e = raw.strip(".,;:()<>[]'\"").lower()
        local, _, dom = e.partition("@")
        if not dom or e.endswith(_BAD_END) or any(b in e for b in _BAD_LOCAL):
            continue
        if any(dom == d or dom.endswith("." + d) for d in _BAD_DOMAINS):
            continue
        if e not in emails:
            emails.append(e)
    phones: list[str] = []
    for m in _PHONE_RE.finditer(t):
        if _PHONE_CTX.search(t[max(0, m.start() - 45): m.end() + 25]):
            p = "+91" + re.sub(r"\D", "", m.group(1))
            if p not in phones:
                phones.append(p)
    name = ""
    nm = _NAME_RE.search(t)
    if nm and not any(w in _NOT_NAMES for w in nm.group(1).split()):
        name = nm.group(1).strip()
    return {"emails": emails[:3], "phones": phones[:2], "name": name}


# ── helpers ─────────────────────────────────────────────────────────────────
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ckey(company: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _CO_NOISE.sub(" ", _TIER.sub("", company or "").lower())).strip()


def _role_key(title: str) -> str:
    return " ".join(sorted(set(re.findall(r"[a-z0-9]+", (title or "").lower()))))


def linkedin_people_url(company: str, domain: str) -> str:
    q = f'recruiter OR "talent acquisition" {company} {_DOMAIN_TERMS.get(domain, "cyber security")}'
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(q)


def google_xray_url(company: str, domain: str) -> str:
    q = (f'site:linkedin.com/in (recruiter OR "talent acquisition" OR "hiring manager") "{company}" '
         f'Bangalore {_DOMAIN_TERMS.get(domain, "cyber security")}')
    return "https://www.google.com/search?q=" + quote_plus(q)


def _link(url: str, label: str) -> str:
    return f'=HYPERLINK("{url}","{label}")'


def _default_relevance(listing: dict):
    """Uses your scorer: in your cities, fresh, and a role in your domains. Level mismatches still count here
    (a company hiring a senior SOC analyst still has a SOC recruiter)."""
    from scorer import evaluate, domain_of
    v = evaluate(listing)
    ok = v.keep or v.reason.startswith(("senior", "experience"))
    return ok, domain_of(str(listing.get("title") or ""))


# ── tab 1: contacts printed in postings ─────────────────────────────────────
def build_contact_rows(listings, known, relevance=_default_relevance):
    seen, rows = set(known), []
    for l in listings:
        try:
            ok, dom = relevance(l)
        except Exception:
            continue
        if not ok:
            continue
        title = str(l.get("title") or "")
        c = extract_contacts(f"{title}\n{l.get('description') or ''}\n{l.get('tail') or ''}", extra_emails=l.get("emails"))
        if not c["emails"] and not c["phones"]:
            continue
        company = _TIER.sub("", str(l.get("company") or "").strip())
        for email in (c["emails"] or [""]):
            key = email or c["phones"][0]
            if key in seen:
                continue
            seen.add(key)
            rows.append([_today(), c["name"], email, c["phones"][0] if c["phones"] else "", company, dom,
                         title[:90], str(l.get("job_url") or l.get("url") or ""), str(l.get("source") or ""),
                         "to contact", ""])
    return rows


# ── tab 2: companies with hiring activity ───────────────────────────────────
def build_target_groups(listings, relevance=_default_relevance) -> dict:
    groups: dict = defaultdict(lambda: {"company": "", "domain": "", "roles": set(), "titles": [], "sources": set()})
    for l in listings:
        company = _TIER.sub("", str(l.get("company") or "").strip())
        if company.lower() in _SKIP_COMPANIES:
            continue
        try:
            ok, dom = relevance(l)
        except Exception:
            continue
        if not ok:
            continue
        title = str(l.get("title") or "")
        g = groups[(_ckey(company), dom)]
        g["company"], g["domain"] = g["company"] or company, dom
        rk = _role_key(title)
        if rk not in g["roles"]:                     # same role seen on two boards counts once
            g["roles"].add(rk)
            if len(g["titles"]) < 3:
                g["titles"].append(title[:60])
        g["sources"].add(str(l.get("source") or "?"))
    return groups


def _activity(n: int) -> str:
    return "hot" if n >= 3 else ("active" if n == 2 else "single")


def _upsert_targets(ws, groups: dict):
    from gspread.utils import rowcol_to_a1
    vals = ws.get_all_values()
    header = vals[0] if vals else TARGET_HEADER
    col = {h: i + 1 for i, h in enumerate(header)}
    index = {(_ckey(r[0]), r[1].strip()): n for n, r in enumerate(vals[1:], start=2) if len(r) >= 2}
    updates, new_rows, refreshed = [], [], 0
    for (ck, dom), g in groups.items():
        n, titles, srcs = len(g["roles"]), " | ".join(g["titles"]), ", ".join(sorted(g["sources"]))
        if (ck, dom) in index:
            r = index[(ck, dom)]
            refreshed += 1
            for name, value in (("activity", _activity(n)), ("open_roles", n), ("sample_titles", titles),
                                ("sources", srcs), ("last_seen", _today())):
                if name in col:
                    updates.append({"range": rowcol_to_a1(r, col[name]), "values": [[value]]})
        else:
            new_rows.append([g["company"], dom, _activity(n), n, titles, srcs, _today(),
                             _link(linkedin_people_url(g["company"], dom), "LinkedIn search"),
                             _link(google_xray_url(g["company"], dom), "Google search"), "to research", ""])
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
    return len(new_rows), refreshed


# ── sheet plumbing ──────────────────────────────────────────────────────────
def _open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    from storage import DEFAULT_SHEET_NAME
    creds = Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_CREDS_JSON"]), scopes=SCOPES)
    return gspread.authorize(creds).open(DEFAULT_SHEET_NAME)


def _ensure_tab(sh, title, header):
    import gspread
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=2000, cols=max(12, len(header)))
        ws.append_row(header, value_input_option="RAW")
        try:
            ws.freeze(rows=1)
        except Exception:
            pass
        return ws


def save_contacts_and_targets(listings: list[dict]) -> None:
    sh = _open_sheet()
    ws = _ensure_tab(sh, RECRUITER_TAB, RECRUITER_HEADER)
    known = {v.strip().lower() for v in ws.col_values(3)[1:] if v.strip()}
    known |= {v.strip() for v in ws.col_values(4)[1:] if v.strip()}
    rows = build_contact_rows(listings, known)
    if rows:
        ws.append_rows(rows, value_input_option="RAW")
    logger.info("Recruiters tab: +%d contacts printed in postings (counts only, nothing else logged)", len(rows))
    wt = _ensure_tab(sh, TARGET_TAB, TARGET_HEADER)
    groups = build_target_groups(listings)
    added, refreshed = _upsert_targets(wt, groups)
    hot = sum(1 for g in groups.values() if len(g["roles"]) >= 3)
    logger.info("Outreach_Targets: %d company/domain pairs (%d hot) | +%d new, %d refreshed",
                len(groups), hot, added, refreshed)
