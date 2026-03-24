# =============================================================================
# storage.py
# =============================================================================
# This module handles everything related to persisting data to Google Sheets.
# Google Sheets serves three roles in our pipeline:
#
#   1. PERSISTENT DATABASE — stores every listing we've ever found, so we
#      can track what we've seen before
#
#   2. DEDUPLICATION ENGINE — before alerting about a new listing, we check
#      the sheet to see if we already stored it in a previous run
#
#   3. HUMAN-READABLE DASHBOARD — you can open the sheet in a browser,
#      filter by date, sort by score, and update the "status" column manually
#
# Why Google Sheets instead of a real database?
# A database (Postgres, SQLite, etc.) would require a persistent server.
# GitHub Actions spins up a fresh Ubuntu machine for every run — there's
# no persistent disk. Google Sheets lives in the cloud, persists between
# runs, and is free. For our scale (maybe 50 listings/day), it's perfect.
#
# HOW AUTHENTICATION WORKS:
# Google Sheets requires OAuth authentication. We use a "Service Account" —
# a special Google account that represents our application rather than a
# person. The service account has a JSON key file with a private key.
# We store that JSON as a GitHub secret, load it at runtime, and use it
# to authenticate. The service account is then "shared" on the Google Sheet
# (like sharing with a person), giving it write access.
# =============================================================================

import os
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_COLUMNS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# GOOGLE SHEETS SCOPES
# Scopes tell Google which APIs this service account is allowed to use.
# "spreadsheets" = read and write spreadsheet data
# "drive" = needed to find the spreadsheet by name (not just by ID)
# ---------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =============================================================================
# FUNCTION: Connect to Google Sheets and return the worksheet
# =============================================================================
# This function handles the authentication flow and returns a gspread
# Worksheet object that we can read from and write to.
#
# The GOOGLE_CREDS_JSON environment variable holds the entire service account
# JSON key as a string. We parse it, create a Credentials object, and use
# that to authorize gspread.
# =============================================================================
def get_worksheet(sheet_name: str = "WalkIn Jobs Bangalore"):
    # Load the service account credentials from the environment variable.
    # json.loads() converts the JSON string into a Python dict.
    creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
    if not creds_json:
        raise EnvironmentError(
            "GOOGLE_CREDS_JSON environment variable is not set. "
            "Add your service account JSON as a GitHub secret."
        )

    creds_dict = json.loads(creds_json)

    # Create a Credentials object from the service account dict.
    # from_service_account_info() is the in-memory equivalent of
    # from_service_account_file() — we use it because we don't write
    # the key to disk (that would be a security risk in CI environments).
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

    # gspread.authorize() creates an authenticated client
    gc = gspread.authorize(creds)

    # Open the spreadsheet by name. The service account must have been
    # added as an editor on this sheet (share it from Google Sheets UI,
    # using the service account's email address from the JSON key).
    try:
        spreadsheet = gc.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        # If the sheet doesn't exist, create it automatically.
        # This makes first-time setup easier — just run the script once.
        logger.info(f"Sheet '{sheet_name}' not found. Creating it...")
        spreadsheet = gc.create(sheet_name)

    # Get the first worksheet (tab) in the spreadsheet.
    # gspread numbers worksheets starting at 0.
    worksheet = spreadsheet.sheet1

    # If the sheet is empty (a new sheet), add the column headers in row 1.
    # We check by reading the first row and seeing if it matches our expected headers.
    existing_headers = worksheet.row_values(1)
    if existing_headers != SHEET_COLUMNS:
        logger.info("Setting up sheet headers...")
        # clear() wipes all data. Then we append the header row.
        # We only do this if headers are wrong — not on every run.
        if not existing_headers:  # only clear if truly empty
            worksheet.clear()
            worksheet.append_row(SHEET_COLUMNS)

    return worksheet


# =============================================================================
# FUNCTION: Check if a listing already exists in the sheet
# =============================================================================
# Deduplication logic: we consider a listing a duplicate if a row with the
# same company name AND the same walk-in date already exists in the sheet.
#
# Why company+date instead of URL?
# Sometimes the same walk-in gets posted multiple times by the same company
# with slightly different URLs (different portal, different session ID).
# Company + date is a more robust fingerprint for the actual event.
# =============================================================================
def is_duplicate(worksheet, company: str, walk_in_date: str) -> bool:
    if not company or not walk_in_date:
        return False  # can't dedup without both fields

    try:
        # get_all_records() reads the entire sheet into a list of dicts.
        # Each dict has the column name as key and the cell value as value.
        # This is slightly slow for large sheets but fine for our scale.
        all_records = worksheet.get_all_records()

        for row in all_records:
            # Compare case-insensitively to handle "Accenture" vs "ACCENTURE"
            row_company = str(row.get("company", "")).lower().strip()
            row_date    = str(row.get("walk_in_date", "")).strip()

            if row_company == company.lower().strip() and row_date == walk_in_date:
                return True  # found a match — this is a duplicate

        return False  # no match found

    except Exception as e:
        logger.error(f"Error checking for duplicate: {e}")
        # If the check fails, we conservatively return False (not a duplicate)
        # so we don't silently skip potentially new listings
        return False


# =============================================================================
# FUNCTION: Save a scored listing to the sheet
# =============================================================================
# Converts a scored listing dict into a row of values in the correct column
# order and appends it to the sheet.
# =============================================================================
def save_listing(worksheet, listing: dict) -> bool:
    try:
        # Build a row by extracting each field in the order defined in SHEET_COLUMNS.
        # .get() with a default of "" means missing fields become empty cells.
        row = []
        for col in SHEET_COLUMNS:
            value = listing.get(col, "")

            # red_flags is a list — join it into a comma-separated string for the cell
            if col == "red_flags" and isinstance(value, list):
                value = ", ".join(value)

            row.append(str(value) if value is not None else "")

        # append_row() adds a new row at the bottom of the data in the sheet.
        # value_input_option="USER_ENTERED" means Google Sheets will interpret
        # values the way a user would — so "2025-04-15" becomes a date cell,
        # numbers become number cells, etc.
        worksheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info(f"Saved to sheet: {listing.get('company')} | {listing.get('job_title')}")
        return True

    except Exception as e:
        logger.error(f"Error saving to sheet: {e}")
        return False


# =============================================================================
# FUNCTION: Process and save a batch of listings
# =============================================================================
# Main entry point called by scanner.py. Takes a list of scored listings,
# deduplicates them against the sheet, and saves the new ones.
# Returns only the truly new listings (for notification purposes).
# =============================================================================
def save_new_listings(scored_listings: list) -> list:
    new_listings = []

    try:
        worksheet = get_worksheet()
    except Exception as e:
        logger.error(f"Cannot connect to Google Sheets: {e}")
        # If sheets is down, we still want to notify — so we return all listings
        # and treat them all as "new". Not ideal but better than silent failure.
        return scored_listings

    for listing in scored_listings:
        company     = listing.get("company", "")
        walk_in_date = listing.get("walk_in_date", "")

        if is_duplicate(worksheet, company, walk_in_date):
            logger.info(f"Duplicate — skipping: {company} on {walk_in_date}")
            continue

        # Not a duplicate — save it and mark it for notification
        success = save_listing(worksheet, listing)
        if success:
            new_listings.append(listing)

    logger.info(f"Saved {len(new_listings)} new listings to Google Sheets")
    return new_listings
