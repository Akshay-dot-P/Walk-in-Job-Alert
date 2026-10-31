# =============================================================================
# storage.py
# =============================================================================
import os
import json
import logging
import re
import time
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_COLUMNS

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

DEFAULT_SHEET_NAME = "WalkIn Jobs Bangalore"


# =============================================================================
# DROPDOWN — set once when sheet is created / headers reset
# =============================================================================

def _set_status_dropdown(worksheet) -> None:
    """Apply a dropdown validation to the status column."""
    try:
        headers = worksheet.row_values(1)
        if "status" not in headers:
            return
        status_col_idx = headers.index("status")   # 0-based for API
        worksheet.spreadsheet.batch_update({
            "requests": [{
                "setDataValidation": {
                    "range": {
                        "sheetId":          worksheet.id,
                        "startRowIndex":    1,        # row 2 (0-indexed)
                        "endRowIndex":      1000,
                        "startColumnIndex": status_col_idx,
                        "endColumnIndex":   status_col_idx + 1,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": v}
                                for v in ["New", "Applied", "Interview",
                                          "Rejected", "Offer", "Not Relevant",
                                          "Invalid"]
                            ],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }]
        })
        logger.info("Status dropdown applied.")
    except Exception as exc:
        logger.warning("Could not apply status dropdown (non-fatal): %s", exc)


# =============================================================================
# CONNECT
# =============================================================================

def get_worksheet(sheet_name: str = DEFAULT_SHEET_NAME):
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDS_JSON environment variable is not set.")

    creds = Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )
    gc = gspread.authorize(creds)

    try:
        spreadsheet = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        logger.info("Sheet '%s' not found — creating it.", sheet_name)
        spreadsheet = gc.create(sheet_name)

    worksheet = spreadsheet.sheet1
    existing_headers = worksheet.row_values(1)

    if not existing_headers:
        logger.info("Empty sheet — writing headers.")
        worksheet.append_row(SHEET_COLUMNS)
        _set_status_dropdown(worksheet)

    elif existing_headers != SHEET_COLUMNS:
        all_rows       = worksheet.get_all_values()
        data_row_count = len(all_rows) - 1 if len(all_rows) > 1 else 0

        if data_row_count == 0:
            logger.info("Wrong headers, no data — rewriting headers.")
            worksheet.clear()
            worksheet.update("A1", [SHEET_COLUMNS])
            _set_status_dropdown(worksheet)
        else:
            logger.warning(
                "Sheet has %d rows under OLD headers. "
                "Clear the sheet manually to fix. "
                "Dedup still works via URL matching.",
                data_row_count
            )
            _set_status_dropdown(worksheet)
    else:
        logger.info("Sheet headers OK.")
        _set_status_dropdown(worksheet)

    return worksheet


# =============================================================================
# DEDUP
# =============================================================================

def _extract_workday_job_id_from_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    # Workday external URLs typically end with .../job/<externalPath>
    # Keep the final segment as stable ID fallback for historical rows.
    if "/myworkdayjobs.com/" not in text:
        return ""
    m = re.search(r"/job/([^/?#]+)", text)
    if not m:
        return ""
    return m.group(1).strip()







# =============================================================================
# SAVE
# =============================================================================
SEEN_TAB = "scored_log"
_TRACKING_HOSTS = ("linkedin.com", "naukri.com", "internshala.com", "iimjobs.com")
_TIER_SUFFIX_RE = re.compile(r"\s*\((?:mnc|startup|mid-tier|unknown)\)\s*$", re.IGNORECASE)


def normalize_url(u: str) -> str:
    u = (u or "").strip().lower().split("#")[0]
    if not u:
        return ""
    if any(h in u for h in _TRACKING_HOSTS):
        u = u.split("?")[0]          # drop tracking params only on these hosts
    return u.rstrip("/")


def _ct_key(company: str, title: str) -> str:
    """Company+title key that ignores the '(MNC)/(startup)' tier suffix and punctuation."""
    c = _TIER_SUFFIX_RE.sub("", (company or "").strip())
    c = re.sub(r"[^a-z0-9]+", " ", c.lower()).strip()
    t = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    return f"{c}|{t}" if c and t else ""


def _build_seen_sets(worksheet) -> tuple[set[str], set[str], set[str]]:
    seen_urls, seen_company_titles, seen_job_ids = set(), set(), set()
    try:
        all_values = worksheet.get_all_values()
        if not all_values or len(all_values) < 2:
            logger.info("Sheet is empty or header-only, no dedup history.")
            return seen_urls, seen_company_titles, seen_job_ids
        headers, rows = all_values[0], all_values[1:]
        col = {h: i for i, h in enumerate(headers) if h}
        url_idx = col.get("apply_url") if col.get("apply_url") is not None else col.get("url")
        company_idx, title_idx = col.get("company"), col.get("job_title")
        for row in rows:
            if url_idx is not None and url_idx < len(row):
                raw = row[url_idx].strip()
                if raw:
                    seen_urls.add(normalize_url(raw))
                    wid = _extract_workday_job_id_from_url(raw)
                    if wid:
                        seen_job_ids.add(wid)
            company = row[company_idx] if company_idx is not None and company_idx < len(row) else ""
            title = row[title_idx] if title_idx is not None and title_idx < len(row) else ""
            key = _ct_key(company, title)
            if key:
                seen_company_titles.add(key)
    except Exception as e:
        logger.error("Dedup read failed: %s, all listings treated as new", e)
    logger.info("Dedup index: %d URLs, %d company+title pairs, %d Workday IDs",
                len(seen_urls), len(seen_company_titles), len(seen_job_ids))
    return seen_urls, seen_company_titles, seen_job_ids


def _is_duplicate(listing, seen_urls, seen_company_titles, seen_job_ids) -> bool:
    job_id = str(listing.get("job_id", "")).strip()
    if job_id and job_id in seen_job_ids:
        return True
    raw_url = str(listing.get("apply_url", "") or listing.get("url", "")).strip()
    url = normalize_url(raw_url)
    if url and url in seen_urls:
        return True
    wid = _extract_workday_job_id_from_url(raw_url)
    if wid and wid in seen_job_ids:
        return True
    key = _ct_key(str(listing.get("company", "")), str(listing.get("job_title", "")))
    return bool(key and key in seen_company_titles)


def _get_seen_tab(spreadsheet):
    try:
        return spreadsheet.worksheet(SEEN_TAB)
    except gspread.WorksheetNotFound:
        tab = spreadsheet.add_worksheet(title=SEEN_TAB, rows=2000, cols=4)
        tab.update(range_name="A1", values=[["url", "score", "title", "logged_at"]])
        return tab


def load_known_urls() -> set[str]:
    """Every URL already saved in the sheet plus every URL in the scored_log tab."""
    known: set[str] = set()
    try:
        ws = get_worksheet()
    except Exception as e:
        logger.warning("Pre-scoring dedup unavailable (sheet): %s", e)
        return known
    seen_urls, _, _ = _build_seen_sets(ws)
    known |= seen_urls
    try:
        tab = _get_seen_tab(ws.spreadsheet)
        for row in tab.get_all_values()[1:]:
            if row and row[0].strip():
                known.add(normalize_url(row[0]))
    except Exception as e:
        logger.warning("Could not read %s tab: %s", SEEN_TAB, e)
    logger.info("Known URLs for pre-scoring skip: %d", len(known))
    return known


def log_scored_urls(entries) -> None:
    """entries: (url, score, title) for listings that were scored but NOT saved."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    rows = [[normalize_url(u), str(s), str(t)[:80], now] for u, s, t in entries if normalize_url(u)]
    if not rows:
        return
    ws = get_worksheet()
    tab = _get_seen_tab(ws.spreadsheet)
    for i in range(0, len(rows), 200):
        tab.append_rows(rows[i:i + 200], value_input_option="RAW")
    logger.info("scored_log: recorded %d URLs", len(rows))


def _row_for(listing: dict) -> list[str]:
    row = []
    for col in SHEET_COLUMNS:
        value = listing.get(col, "")
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            value = "TRUE" if value else "FALSE"
        row.append(str(value) if value is not None else "")
    return row


def _append_rows_safely(worksheet, rows: list, chunk: int = 40) -> int:
    saved = 0
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        for attempt in (1, 2):
            try:
                worksheet.append_rows(part, value_input_option="USER_ENTERED")
                saved += len(part)
                break
            except Exception as e:
                logger.error("Batch append failed (attempt %d): %s", attempt, e)
                if attempt == 1:
                    time.sleep(20)
        time.sleep(1.5)
    return saved


def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    try:
        worksheet = get_worksheet()
    except Exception as e:
        logger.error("Cannot connect to Google Sheets: %s", e)
        logger.warning("Proceeding with all listings as new (sheet unavailable).")
        return scored_listings

    seen_urls, seen_company_titles, seen_job_ids = _build_seen_sets(worksheet)
    new_listings, rows_to_write, skipped = [], [], []

    for listing in scored_listings:
        if _is_duplicate(listing, seen_urls, seen_company_titles, seen_job_ids):
            logger.info("Duplicate, skipping: %s | %s", listing.get("company", "?"), listing.get("job_title", "?"))
            skipped.append((listing.get("apply_url") or listing.get("url") or "",
                            listing.get("legitimacy_score", ""), listing.get("job_title", "")))
            continue
        new_listings.append(listing)
        rows_to_write.append(_row_for(listing))
        raw_url = str(listing.get("apply_url", "") or listing.get("url", "")).strip()
        if raw_url:
            seen_urls.add(normalize_url(raw_url))
            wid = _extract_workday_job_id_from_url(raw_url)
            if wid:
                seen_job_ids.add(wid)
        job_id = str(listing.get("job_id", "")).strip()
        if job_id:
            seen_job_ids.add(job_id)
        key = _ct_key(str(listing.get("company", "")), str(listing.get("job_title", "")))
        if key:
            seen_company_titles.add(key)

    saved = _append_rows_safely(worksheet, rows_to_write)
    if saved < len(rows_to_write):
        logger.error("Only %d of %d rows were written; the rest will be retried next run.",
                     saved, len(rows_to_write))
    if skipped:
        try:
            log_scored_urls(skipped)
        except Exception as e:
            logger.warning("Could not log skipped duplicates: %s", e)
    logger.info("Storage complete: %d new / %d duplicates skipped", len(new_listings), len(skipped))
    return new_listings

