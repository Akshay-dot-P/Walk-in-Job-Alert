# =============================================================================
# scanner.py
# =============================================================================
# Entry point — the file GitHub Actions runs.
# Orchestrates the four pipeline phases in order:
#
#   1. Scrape  → sources.py  → raw listing dicts
#                              Sources: LinkedIn, Indeed India, Glassdoor, ZipRecruiter
#   2. Score   → scorer.py   → enriched + filtered dicts (Groq Llama 3 calls)
#   3. Store   → storage.py  → deduplicated against Google Sheets history
#   4. Notify  → notifier.py → one Telegram message per new listing
#
# This file contains no scraping/scoring/storage logic itself.
# It is a conductor: it calls each module in sequence, handles empty results
# gracefully, and prints a clean summary visible in the GH Actions log viewer.
# =============================================================================

import logging
import sys
from datetime import datetime, timezone

from sources import gather_all_listings
from scorer import score_all
from storage import save_new_listings
from notifier import notify_all
from config import MIN_LEGITIMACY_SCORE

# ---------------------------------------------------------------------------
# Logging — millisecond precision so timing across phases is visible
# in the GitHub Actions log viewer without extra tooling.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    # Use timezone-aware UTC (datetime.utcnow() is deprecated in Python 3.12+)
    started_at = datetime.now(timezone.utc)

    logger.info("=" * 60)
    logger.info(f"Walk-In Scanner started at {started_at.isoformat()}")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # PHASE 1 — SCRAPING
    # Sources: LinkedIn, Indeed India, Glassdoor, ZipRecruiter
    # gather_all_listings() handles per-source failures internally and
    # always returns a list — never raises.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 1: Scraping all sources ---")
    raw_listings = gather_all_listings()

    if not raw_listings:
        logger.info("No listings found across any source. Exiting.")
        sys.exit(0)

    logger.info(f"Phase 1 complete: {len(raw_listings)} raw listings collected")

    # ------------------------------------------------------------------
    # PHASE 2 — AI SCORING (Groq Llama 3, free tier)
    # score_all() drops listings below MIN_LEGITIMACY_SCORE.
    # Sleeps 2s between Groq calls to stay within 30 req/min free limit.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 2: AI scoring and extraction ---")
    scored_listings = score_all(raw_listings, min_score=MIN_LEGITIMACY_SCORE)

    if not scored_listings:
        logger.info("No listings passed the legitimacy threshold. Exiting.")
        sys.exit(0)

    logger.info(f"Phase 2 complete: {len(scored_listings)} listings passed scoring")

    # ------------------------------------------------------------------
    # PHASE 3 — DEDUPLICATION + STORAGE
    # Checks each scored listing against URL + company/date history in
    # Google Sheets. Saves new ones and returns the truly new subset.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 3: Deduplication and storage ---")
    new_listings = save_new_listings(scored_listings)

    if not new_listings:
        logger.info("All listings are duplicates of previously seen ones. Exiting.")
        sys.exit(0)

    logger.info(f"Phase 3 complete: {len(new_listings)} new listings to alert about")

    # ------------------------------------------------------------------
    # PHASE 4 — TELEGRAM NOTIFICATION
    # Sends one message per new listing plus a summary header.
    # notify_all() handles Telegram API errors internally.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 4: Sending Telegram notifications ---")
    notify_all(new_listings, total_scraped=len(raw_listings))

    # ------------------------------------------------------------------
    # SUMMARY — visible at a glance in the GitHub Actions log viewer
    # ------------------------------------------------------------------
    elapsed = (datetime.now(timezone.utc) - started_at).seconds
    logger.info("\n" + "=" * 60)
    logger.info("SCAN COMPLETE")
    logger.info(f"  Raw listings scraped : {len(raw_listings)}")
    logger.info(f"  Passed AI scoring    : {len(scored_listings)}")
    logger.info(f"  New (not duplicates) : {len(new_listings)}")
    logger.info(f"  Telegram alerts sent : {len(new_listings)}")
    logger.info(f"  Total runtime        : {elapsed}s")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
