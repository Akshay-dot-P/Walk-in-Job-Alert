   #2. DEDUPLICATION ENGINE — prevents re-alerting the same listing
#   3. HUMAN DASHBOARD      — filterable, sortable by a human in a browser
#
# DEDUPLICATION STRATEGY (fixed from original):
# Primary key: job URL (fast, unambiguous)
# Fallback key: company name + walk-in date (catches same event reposted
#               with different URLs on different portals)
# The original code used only company+date which missed URL-based duplication.




# =============================================================================

import os
@@ -63,7 +67,6 @@ def get_worksheet(sheet_name: str = DEFAULT_SHEET_NAME):
    except gspread.SpreadsheetNotFound:
        logger.info(f"Sheet '{sheet_name}' not found — creating it automatically.")
        spreadsheet = gc.create(sheet_name)
        # Share with the service account itself so it can write to it
        logger.info(
            "Sheet created. Remember to share it with your service account email "
            "if you haven't already."
@@ -94,56 +97,65 @@ def get_worksheet(sheet_name: str = DEFAULT_SHEET_NAME):
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
@@ -186,7 +198,7 @@ def save_new_listings(scored_listings: list[dict]) -> list[dict]:
    and returns only the truly new subset (for Telegram notification).

    On sheet connection failure, returns all listings so notifications still
    fire (better to re-alert than silently miss new walk-ins).
    """
    try:
        worksheet = get_worksheet()
@@ -196,28 +208,29 @@ def save_new_listings(scored_listings: list[dict]) -> list[dict]:
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
