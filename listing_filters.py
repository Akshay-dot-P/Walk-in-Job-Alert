"""
Heuristic entry-level / intern / 0–2y gate for job listings.

Used by:
  - sources.Workday filtering (WORKDAY_EXPERIENCE_STRICT, default on)
  - scorer.is_relevant (ENTRY_LEVEL_GATE, default on)

Disable globally: ENTRY_LEVEL_GATE=0
Disable Workday-only: WORKDAY_EXPERIENCE_STRICT=0 (scorer gate still runs if ENTRY_LEVEL_GATE=1)
"""

from __future__ import annotations

import os
import re

# ── Title: leadership / principal IC ─────────────────────────────────────
_SENIOR_TITLE_RE = re.compile(
    r"(?i)\b(senior|\bsr\.?\b|principal\b|staff\s+engineer|staff\s+architect|distinguished|"
    r"director\b|vice\s+president|\bvp\b|chief\s+|head\s+of|executive\s+vice|"
    r"lead\s+engineer|lead\s+developer|lead\s+architect|group\s+manager|"
    r"people\s+manager)\b",
)
# Roman grade II–VI at end of title or after a role word — reject (mid band).
# Roman **I** is treated as entry (e.g. "Technology Risk Analyst I") and is not matched here.
_TITLE_ROMAN_MID_OR_ABOVE_RE = re.compile(
    r"(?i)(?:\b(analyst|specialist|consultant|engineer|architect|officer|professional|associate)"
    r"\s+(ii|iii|iv|v|vi)\b"
    r"|(?:^|[\s,])(ii|iii|iv|v|vi)\s*$)",
)
_TITLE_ANALYST_L_TIER_RE = re.compile(
    r"(?i)\b(analyst|engineer|architect|consultant)\s+l[3-9]\b|\bl[3-9]\s+(analyst|engineer)\b",
)
_TITLE_MID_LEVEL_RE = re.compile(
    r"(?i)\b(mid[\s-]?level|middle[\s-]?level|midlevel)\b",
)
_TITLE_YEARS_PLUS_RE = re.compile(
    r"(?i)\(?\s*\d{1,2}\s*\+\s*(?:years?|yrs?)|\b\d{1,2}\s*\+\s*years?\b",
)

# Description: explicit minimum experience
_DESC_MIN_YEARS_RE = re.compile(
    r"(?is)(?:minimum|min\.?|at\s+least|requires?\s+(?:a\s+)?(?:minimum\s+)?(?:of\s+)?)"
    r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b",
)
_DESC_HIGH_RANGE_RE = re.compile(
    r"(?i)\b(\d{1,2})\s*[\+\-–]\s*(\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?(?:experience|exp)",
)
_ENTRY_SIGNAL_RE = re.compile(
    r"(?is)\b(intern|internship|apprentice|apprenticeship|trainee|co[\s-]?op\b|"
    r"entry[\s-]level|entry\s+level|fresher|new\s+grad|graduate\s+program|"
    r"campus\s+(hire|recruiting|program)|early\s+career|university\s+graduate|"
    r"0[\s\-–]*2\s*yrs?|0\s+to\s+2|upto\s+2|up\s+to\s+2|less\s+than\s+3\s+years?|"
    r"1[\s\-–]*2\s*year|associate\s+level|junior\b|graduate\s+hire|"
    r"analyst\s+i\b|engineer\s+i\b|engineer\s+1\b|l1\b|level\s+1\b|tier\s+1\b)\b",
)
_SENIOR_ASSOCIATE_OK_RE = re.compile(
    r"(?i)\b(senior\s+associate|sr\.?\s*associate)\b",
)


def entry_level_gate_enabled() -> bool:
    return os.environ.get("ENTRY_LEVEL_GATE", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def workday_experience_strict_enabled() -> bool:
    return os.environ.get("WORKDAY_EXPERIENCE_STRICT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _plain_text(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    t = re.sub(r"<[^>]+>", " ", html_or_text)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _title_has_broad_senior(title_lc: str) -> bool:
    """Reject generic 'Senior …' unless intern/trainee/apprentice or Senior Associate."""
    if re.search(r"(?i)\b(intern|internship|trainee|apprentice)\b", title_lc):
        return False
    if not re.search(r"(?i)\bsenior\b", title_lc):
        return False
    if _SENIOR_ASSOCIATE_OK_RE.search(title_lc):
        return False
    return True


def passes_entry_level_gate(title: str, description: str) -> bool:
    """
    True if posting looks like intern / apprentice / entry / 0–2y / junior analyst lane.

    Drops: obvious senior titles, mid-level labels, Roman II+ grade suffixes (allows Roman I),
    L3+ tier titles, titles with (N+ years), and descriptions that require 5+ years minimum.
    """
    t_raw = (title or "").strip()
    t = t_raw.lower()
    d_plain = _plain_text(description or "")
    blob = f"{t}\n{d_plain}"

    if _TITLE_YEARS_PLUS_RE.search(t_raw):
        return False

    intern_track = bool(re.search(r"(?i)\b(intern|internship|trainee|apprentice)\b", t))
    senior_assoc_track = bool(_SENIOR_ASSOCIATE_OK_RE.search(t))
    if not intern_track and not senior_assoc_track:
        if _title_has_broad_senior(t):
            return False
        if _SENIOR_TITLE_RE.search(t_raw):
            return False
    if _TITLE_ROMAN_MID_OR_ABOVE_RE.search(t_raw) and "intern" not in t and "trainee" not in t:
        return False
    if _TITLE_ANALYST_L_TIER_RE.search(t_raw):
        return False
    if _TITLE_MID_LEVEL_RE.search(t_raw):
        return False
    if re.search(r"\bmanager\b", t) and "intern" not in t and "trainee" not in t:
        return False

    for m in _DESC_MIN_YEARS_RE.finditer(blob):
        try:
            if int(m.group(1)) >= 5:
                return False
        except ValueError:
            pass
    for m in _DESC_HIGH_RANGE_RE.finditer(blob):
        try:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo >= 5 or hi >= 8:
                return False
        except ValueError:
            pass
    if re.search(r"(?i)\b([6-9]|\d{2})\s*\+\s*years?\b", blob):
        return False

    if _ENTRY_SIGNAL_RE.search(blob):
        return True
    if re.search(r"(?i)\b(fresher|graduate|stipend|stipendiary)\b", blob):
        return True

    if senior_assoc_track:
        return True

    if re.search(
        r"(?i)\b(soc|security|cyber|grc|risk|compliance|fraud|iam|appsec|"
        r"vulnerability|incident|threat|network\s+security)\b",
        t,
    ) and re.search(
        r"(?i)\b(analyst|specialist|consultant|engineer|administrator|officer)\b",
        t,
    ):
        return True

    return False


def experience_hint_for_listing(title: str, description: str) -> str:
    blob = _plain_text(f"{title}\n{description}")
    if re.search(r"(?i)\b(intern|internship)\b", blob):
        return "intern"
    if re.search(r"(?i)\b(apprentice|apprenticeship)\b", blob):
        return "apprenticeship"
    if _ENTRY_SIGNAL_RE.search(blob) or re.search(r"(?i)\b(fresher|graduate\s+hire)\b", blob):
        return "0-2 years"
    return ""
