# =============================================================================
# STORAGE MODULE - Saves scored walk-in listings to Google Sheets
# =============================================================================
# Features:
# 1. Saves new listings to Google Sheet
# 2. DEDUPLICATION ENGINE — prevents re-alerting the same listing
# 3. HUMAN DASHBOARD — filterable, sortable by a human in a browser
#
# DEDUPLICATION STRATEGY:
# Primary key: job URL (fast, unambiguous)
# Fallback key: company name + walk-in date (catches same event reposted
#               with different URLs)
# =============================================================================

import os
import logging
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_SHEET_NAME = "WalkInJobs"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Load credentials (from environment or file)
def get_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        import json
        return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    else:
        # Fallback to file (for local testing)
        return Credentials.from_service_account_file(
            "credentials.json", scopes=SCOPES
        )


def get_worksheet(sheet_name: str = DEFAULT_SHEET_NAME):
    """Get or create the Google Sheet worksheet."""
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        try:
            spreadsheet = gc.open(sheet_name)
        except gspread.SpreadsheetNotFound:
            logger.info(f"Sheet '{sheet_name}' not found — creating it automatically.")
            spreadsheet = gc.create(sheet_name)
            logger.info(
                "Sheet created. Remember to share it with your service account email "
                "if you haven't already."
            )
        worksheet = spreadsheet.sheet1
        # Ensure headers exist
        if not worksheet.get_all_values():
            headers = [
                "scraped_at", "source", "url", "job_title", "company",
                "company_tier", "walk_in_date", "walk_in_time",
                "location_address", "contact", "legitimacy_score",
                "red_flags", "summary", "status"
            ]
            worksheet.append_row(headers)
        return worksheet
    except Exception as e:
        logger.error(f"Failed to connect to Google Sheets: {e}")
        raise


def _build_seen_sets(worksheet) -> tuple[set[str], set[str]]:
    """
    Read the full sheet once and return two sets for fast O(1) dedup lookups:
      seen_urls — all job URLs already stored
      seen_company_dates — all "company|walk_in_date" pairs already stored
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
    Priority: URL > company + walk_in_date
    """
    url = str(listing.get("url", "")).strip()
    if url and url in seen_urls:
        return True

    company = str(listing.get("company", "")).lower().strip()
    date = str(listing.get("walk_in_date", "")).strip()
    if company and date and f"{company}|{date}" in seen_company_dates:
        return True

    return False


def _save_listing(worksheet, listing: dict) -> bool:
    """Append a single listing to the sheet."""
    try:
        row = [
            listing.get("scraped_at"),
            listing.get("source"),
            listing.get("url"),
            listing.get("job_title"),
            listing.get("company"),
            listing.get("company_tier"),
            listing.get("walk_in_date"),
            listing.get("walk_in_time"),
            listing.get("location_address"),
            listing.get("contact"),
            listing.get("legitimacy_score"),
            str(listing.get("red_flags", [])),
            listing.get("summary"),
            listing.get("status"),
        ]
        worksheet.append_row(row)
        return True
    except Exception as e:
        logger.error(f"Failed to save listing {listing.get('job_title')}: {e}")
        return False


def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    """
    Saves only new listings to Google Sheet after deduplication,
    and returns only the truly new subset (for Telegram notification).
    """
    if not scored_listings:
        return []

    try:
        worksheet = get_worksheet()
    except Exception:
        logger.warning("Google Sheets unavailable. Returning all listings for notification.")
        return scored_listings

    # Build dedup index once
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
            # Update in-memory sets for this batch
            url = str(listing.get("url", "")).strip()
            if url:
                seen_urls.add(url)
            company = str(listing.get("company", "")).lower().strip()
            date = str(listing.get("walk_in_date", "")).strip()
            if company and date:
                seen_company_dates.add(f"{company}|{date}")

    logger.info(
        f"Storage complete: {len(new_listings)} new / {len(scored_listings)} total listings saved"
    )
    return new_listings
