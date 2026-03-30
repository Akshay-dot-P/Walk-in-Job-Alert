# =============================================================================
# storage.py
# =============================================================================
# Handles all persistence to Google Sheets.
#
# Google Sheets serves three roles:
#   1. PERSISTENT DATABASE  — every seen listing stored across runs
#   2. DEDUPLICATION ENGINE — prevents re-alerting the same listing
#   3. HUMAN DASHBOARD      — filterable, sortable by a human in a browser
#
# DEDUPLICATION STRATEGY (fixed from original):
# Primary key: job URL (fast, unambiguous)
# Fallback key: company name + walk-in date (catches same event reposted
#               with different URLs on different portals)
# The original code used only company+date which missed URL-based duplication.
# =============================================================================

import os
import json
import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_COLUMNS

logger = logging.getLogger(__name__)

# Google API scopes required
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Sheet name — must match the actual Google Sheet name exactly (case-sensitive).
# Share this sheet with your service account email from the JSON key file.
DEFAULT_SHEET_NAME = "WalkIn Jobs Bangalore"


# =============================================================================
# FUNCTION: Connect to Google Sheets and return the worksheet
# =============================================================================

def get_worksheet(sheet_name: str = DEFAULT_SHEET_NAME):
    """
    Authenticate via service account and return the first worksheet of the
    named spreadsheet. Creates the sheet and headers if it doesn't exist yet.
    """
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError(
            "GOOGLE_CREDS_JSON environment variable is not set. "
            "Add your service account JSON as a GitHub secret."
        )

    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)

    try:
        spreadsheet = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        logger.info(f"Sheet '{sheet_name}' not found — creating it automatically.")
        spreadsheet = gc.create(sheet_name)
        # Share with the service account itself so it can write to it
        logger.info(
            "Sheet created. Remember to share it with your service account email "
            "if you haven't already."
        )

    worksheet = spreadsheet.sheet1

    # Bootstrap headers on a fresh/empty sheet
existing_headers = worksheet.row_values(1)

if not existing_headers:
    logger.info("Empty sheet — writing headers.")
    worksheet.append_row(SHEET_COLUMNS)

elif existing_headers != SHEET_COLUMNS:
    all_rows = worksheet.get_all_values()
    data_row_count = len(all_rows) - 1 if len(all_rows) > 1 else 0

    if data_row_count == 0:
        # No data rows — safe to rewrite headers automatically
        logger.info("Wrong headers, no data — rewriting headers.")
        worksheet.delete_rows(1)
        worksheet.insert_row(SHEET_COLUMNS, 1)
    else:
        # Data exists — warn but don't touch it
        logger.warning(
            "Sheet has %d rows under OLD headers. "
            "Clear the sheet manually in your browser to fix. "
            "Dedup still works via URL matching.",
            data_row_count
        )
else:
    logger.info("Sheet headers OK.")
    return worksheet


# =============================================================================
# FUNCTION: Build dedup sets from existing sheet data
# =============================================================================

def _build_seen_sets(worksheet) -> tuple[set[str], set[str]]:
    """
    Read the full sheet once and return two sets for fast O(1) dedup lookups:
      seen_urls         — all job URLs already stored
      seen_company_dates — all "company|walk_in_date" pairs already stored

    Reading the whole sheet once (rather than querying per-listing) is much
    faster for batches of 50-150 listings.
    """
    seen_urls: set[str] = set()
    seen_company_dates: set[str] = set()

    try:
        records = worksheet.get_all_records()
    except Exception as e:
        logger.error(f"Could not read sheet for dedup check: {e}")
        return seen_urls, seen_company_dates

    for row in records:
        url = str(row.get("url", "")).strip()
        if url:
            seen_urls.add(url)

        company = str(row.get("company", "")).lower().strip()
        date = str(row.get("walk_in_date", "")).strip()
        if company and date:
            seen_company_dates.add(f"{company}|{date}")

    logger.info(
        f"Dedup index built: {len(seen_urls)} unique URLs, "
        f"{len(seen_company_dates)} company+date pairs in sheet"
    )
    return seen_urls, seen_company_dates


def _is_duplicate(
    listing: dict,
    seen_urls: set[str],
    seen_company_dates: set[str],
) -> bool:
    """
    Return True if this listing is a duplicate of something already in the sheet.

    Check 1 (primary): exact URL match
    Check 2 (fallback): same company name + same walk-in date
    """
    url = str(listing.get("url", "")).strip()
    if url and url in seen_urls:
        return True

    company = str(listing.get("company", "")).lower().strip()
    date = str(listing.get("walk_in_date", "")).strip()
    if company and date and f"{company}|{date}" in seen_company_dates:
        return True

    return False


# =============================================================================
# FUNCTION: Save a single scored listing to the sheet
# =============================================================================

def _save_listing(worksheet, listing: dict) -> bool:
    """
    Append a scored listing as a new row. Column order follows SHEET_COLUMNS.
    """
    try:
        row = []
        for col in SHEET_COLUMNS:
            value = listing.get(col, "")
            if col == "red_flags" and isinstance(value, list):
                value = ", ".join(value)
            row.append(str(value) if value is not None else "")

        # USER_ENTERED lets Google Sheets interpret dates as date cells, etc.
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(
            f"Saved: {listing.get('company', '?')} | {listing.get('job_title', '?')}"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save listing to sheet: {e}")
        return False


# =============================================================================
# FUNCTION: Main entry point — deduplicate and save a batch of listings
# =============================================================================

def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    """
    Deduplicates scored_listings against the Google Sheet, saves new ones,
    and returns only the truly new subset (for Telegram notification).

    On sheet connection failure, returns all listings so notifications still
    fire (better to re-alert than silently miss new walk-ins).
    """
    try:
        worksheet = get_worksheet()
    except Exception as e:
        logger.error(f"Cannot connect to Google Sheets: {e}")
        logger.warning("Proceeding with all listings as 'new' (sheet unavailable).")
        return scored_listings

    # Build dedup index once (one sheet read for the whole batch)
    seen_urls, seen_company_dates = _build_seen_sets(worksheet)

    new_listings = []
    for listing in scored_listings:
        if _is_duplicate(listing, seen_urls, seen_company_dates):
            logger.info(
                f"Duplicate — skipping: {listing.get('company', '?')} "
                f"on {listing.get('walk_in_date', '?')}"
            )
            continue

        success = _save_listing(worksheet, listing)
        if success:
            new_listings.append(listing)
            # Update our in-memory sets so we don't re-save within the same batch
            url = str(listing.get("url", "")).strip()
            if url:
                seen_urls.add(url)
            company = str(listing.get("company", "")).lower().strip()
            date = str(listing.get("walk_in_date", "")).strip()
            if company and date:
                seen_company_dates.add(f"{company}|{date}")

    logger.info(
        f"Storage complete: {len(new_listings)} new / "
        f"{len(scored_listings) - len(new_listings)} duplicates skipped"
    )
    return new_listings
