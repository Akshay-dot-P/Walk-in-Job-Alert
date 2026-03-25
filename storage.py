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
# DEDUPLICATION STRATEGY:
#   Primary key : job URL         (fast, unambiguous — catches exact duplicates)
#   Fallback key: company + job_title  (catches same job reposted at different URL)
#
#   Note: the original code used company + walk_in_date as fallback, but
#   walk_in_date has been removed since we now scrape online postings.
#   company + job_title is a more reliable fallback for online job boards.
# =============================================================================

import os
import json
import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_COLUMNS, SHEET_NAME

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =============================================================================
# FUNCTION: Connect to Google Sheets and return the worksheet
# =============================================================================

def get_worksheet(sheet_name: str = SHEET_NAME):
    """
    Authenticate via service account and return the first worksheet.
    Creates the sheet and writes headers if it does not exist yet.
    Environment variable: GOOGLE_CREDS_JSON (matches GitHub Actions secret name).
    """
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError(
            "GOOGLE_CREDS_JSON environment variable is not set. "
            "Add your service account JSON as a GitHub secret named GOOGLE_CREDS_JSON."
        )

    creds_dict = json.loads(creds_json)
    creds      = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc         = gspread.authorize(creds)

    try:
        spreadsheet = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        logger.info("Sheet '%s' not found — creating it.", sheet_name)
        spreadsheet = gc.create(sheet_name)

    worksheet = spreadsheet.sheet1

    # Bootstrap headers on a fresh/empty sheet
    existing_headers = worksheet.row_values(1)
    if not existing_headers:
        logger.info("Writing sheet headers (first run).")
        worksheet.append_row(SHEET_COLUMNS)
    elif existing_headers != SHEET_COLUMNS:
        logger.warning(
            "Sheet headers don't match SHEET_COLUMNS.\n"
            "Expected: %s\nFound:    %s\n"
            "Data will still be written but column alignment may be off. "
            "Clear row 1 and re-run to fix.",
            SHEET_COLUMNS, existing_headers,
        )

    return worksheet


# =============================================================================
# FUNCTION: Build dedup index from existing sheet data (one read, O(1) lookups)
# =============================================================================

def _build_seen_sets(worksheet) -> tuple[set[str], set[str]]:
    """
    Read the entire sheet once and return two sets for fast dedup:
      seen_urls          — job URLs already stored
      seen_company_titles — normalised "company|job_title" pairs already stored

    Reading the whole sheet once (vs. one query per listing) is critical for
    performance when the sheet already has hundreds of rows.
    """
    seen_urls:           set[str] = set()
    seen_company_titles: set[str] = set()

    try:
        records = worksheet.get_all_records()
    except Exception as exc:
        logger.error("Could not read sheet for dedup check: %s", exc)
        return seen_urls, seen_company_titles

    for row in records:
        url = str(row.get("url", "")).strip()
        if url:
            seen_urls.add(url)

        company   = str(row.get("company",   "")).lower().strip()
        job_title = str(row.get("job_title", "")).lower().strip()
        if company and job_title:
            seen_company_titles.add(f"{company}|{job_title}")

    logger.info(
        "Dedup index built: %d unique URLs, %d company+title pairs in sheet",
        len(seen_urls), len(seen_company_titles),
    )
    return seen_urls, seen_company_titles


def _is_duplicate(listing: dict, seen_urls: set[str], seen_company_titles: set[str]) -> bool:
    """
    True if this listing is already in the sheet.
    Check 1 (primary) : exact URL match
    Check 2 (fallback): same company + same job title (catches reposts)
    """
    url = str(listing.get("url", "")).strip()
    if url and url in seen_urls:
        return True

    company   = str(listing.get("company",   "")).lower().strip()
    job_title = str(listing.get("job_title", "")).lower().strip()
    if company and job_title and f"{company}|{job_title}" in seen_company_titles:
        return True

    return False


# =============================================================================
# FUNCTION: Append a single listing to the sheet
# =============================================================================

def _save_listing(worksheet, listing: dict) -> bool:
    """
    Write one listing as a new row. Column order follows SHEET_COLUMNS exactly.
    Any extra keys in the listing dict are silently ignored.
    """
    try:
        row = []
        for col in SHEET_COLUMNS:
            value = listing.get(col, "")
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            row.append(str(value) if value is not None else "")

        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Saved: %s | %s", listing.get("company", "?"), listing.get("job_title", "?"))
        return True
    except Exception as exc:
        logger.error("Failed to save listing to sheet: %s", exc)
        return False


# =============================================================================
# FUNCTION: Main entry point — deduplicate and persist a batch of listings
# =============================================================================

def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    """
    Deduplicates scored_listings against the Google Sheet, saves truly new ones,
    and returns the new subset for Telegram notification.

    On sheet connection failure, returns all listings so notifications still
    fire — better to re-alert than silently miss new jobs.
    """
    try:
        worksheet = get_worksheet()
    except Exception as exc:
        logger.error("Cannot connect to Google Sheets: %s", exc)
        logger.warning("Proceeding with all listings as 'new' (sheet unavailable).")
        return scored_listings

    # One sheet read for the whole batch (vs N reads in the original code)
    seen_urls, seen_company_titles = _build_seen_sets(worksheet)

    new_listings = []
    for listing in scored_listings:
        if _is_duplicate(listing, seen_urls, seen_company_titles):
            logger.info(
                "Duplicate — skipping: %s | %s",
                listing.get("company", "?"), listing.get("job_title", "?"),
            )
            continue

        success = _save_listing(worksheet, listing)
        if success:
            new_listings.append(listing)
            # Update in-memory sets so we don't re-save within this same batch
            url = str(listing.get("url", "")).strip()
            if url:
                seen_urls.add(url)
            company   = str(listing.get("company",   "")).lower().strip()
            job_title = str(listing.get("job_title", "")).lower().strip()
            if company and job_title:
                seen_company_titles.add(f"{company}|{job_title}")

    logger.info(
        "Storage complete: %d new / %d duplicates skipped",
        len(new_listings),
        len(scored_listings) - len(new_listings),
    )
    return new_listings
