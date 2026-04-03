# =============================================================================
# storage.py
# =============================================================================
import os
import json
import logging

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
    """Apply a dropdown validation to the status column: New/Applied/Interview/Rejected/Offer/Not Relevant."""
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
                                          "Rejected", "Offer", "Not Relevant"]
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
    else:
        logger.info("Sheet headers OK.")

    return worksheet


# =============================================================================
# DEDUP
# =============================================================================

def _build_seen_sets(worksheet) -> tuple[set[str], set[str]]:
    seen_urls:           set[str] = set()
    seen_company_titles: set[str] = set()

    try:
        all_values = worksheet.get_all_values()

        if not all_values or len(all_values) < 2:
            logger.info("Sheet is empty or header-only — no dedup history.")
            return seen_urls, seen_company_titles

        headers = all_values[0]
        rows    = all_values[1:]
        col     = {h: i for i, h in enumerate(headers) if h}

        # Support both old sheets ("url") and new sheets ("apply_url")
        url_idx     = col.get("apply_url") if col.get("apply_url") is not None else col.get("url")
        company_idx = col.get("company")
        title_idx   = col.get("job_title")

        for row in rows:
            if url_idx is not None and url_idx < len(row):
                url = row[url_idx].strip()
                if url:
                    seen_urls.add(url)

            company = (row[company_idx].lower().strip()
                       if company_idx is not None and company_idx < len(row) else "")
            title   = (row[title_idx].lower().strip()
                       if title_idx   is not None and title_idx   < len(row) else "")

            if company and title:
                seen_company_titles.add(f"{company}|{title}")
            elif title:
                seen_company_titles.add(title)

    except Exception as e:
        logger.error("Dedup read failed: %s — all listings treated as new", e)

    logger.info("Dedup index: %d URLs, %d company+title pairs",
                len(seen_urls), len(seen_company_titles))
    return seen_urls, seen_company_titles


def _is_duplicate(listing, seen_urls, seen_company_titles) -> bool:
    # Check both "apply_url" and "url" keys since scorer may use either
    url = str(listing.get("apply_url", "") or listing.get("url", "")).strip()
    if url and url in seen_urls:
        return True

    company = str(listing.get("company", "")).lower().strip()
    title   = str(listing.get("job_title", "")).lower().strip()
    if company and title and f"{company}|{title}" in seen_company_titles:
        return True
    if not company and title and title in seen_company_titles:
        return True

    return False


# =============================================================================
# SAVE
# =============================================================================

def _save_listing(worksheet, listing: dict) -> bool:
    try:
        row = []
        for col in SHEET_COLUMNS:
            value = listing.get(col, "")
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            elif isinstance(value, bool):
                value = "TRUE" if value else "FALSE"
            row.append(str(value) if value is not None else "")

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Saved: %s | %s",
                    listing.get("company", "?"), listing.get("job_title", "?"))
        return True
    except Exception as e:
        logger.error("Failed to save listing: %s", e)
        return False


def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    try:
        worksheet = get_worksheet()
    except Exception as e:
        logger.error("Cannot connect to Google Sheets: %s", e)
        logger.warning("Proceeding with all listings as new (sheet unavailable).")
        return scored_listings

    seen_urls, seen_company_titles = _build_seen_sets(worksheet)

    new_listings = []
    for listing in scored_listings:
        if _is_duplicate(listing, seen_urls, seen_company_titles):
            logger.info("Duplicate — skipping: %s | %s",
                        listing.get("company", "?"), listing.get("job_title", "?"))
            continue

        success = _save_listing(worksheet, listing)
        if success:
            new_listings.append(listing)
            # Update in-memory sets so duplicates within this batch are caught
            url = str(listing.get("apply_url", "") or listing.get("url", "")).strip()
            if url:
                seen_urls.add(url)
            company = str(listing.get("company", "")).lower().strip()
            title   = str(listing.get("job_title", "")).lower().strip()
            if company and title:
                seen_company_titles.add(f"{company}|{title}")
            elif title:
                seen_company_titles.add(title)

    logger.info("Storage complete: %d new / %d duplicates skipped",
                len(new_listings), len(scored_listings) - len(new_listings))
    return new_listings
