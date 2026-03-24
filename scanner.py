# =============================================================================
# scanner.py
# =============================================================================
# This is the entry point — the file GitHub Actions runs.
# It does one thing: call the other modules in the right order and handle
# errors gracefully so one bad source doesn't kill the whole pipeline.
#
# The flow is:
#   1. Scrape all sources      → list of raw listing dicts
#   2. Score with Groq AI      → list of enriched, scored dicts
#   3. Save new ones to Sheets → list of only truly new dicts (deduped)
#   4. Notify via Telegram     → one message per new listing
#
# Think of this file as a conductor: it doesn't play any instruments itself,
# it just tells each section when to play. All the real logic lives in the
# imported modules (sources.py, scorer.py, storage.py, notifier.py).
# =============================================================================

import logging
import sys
from datetime import datetime

from sources  import gather_all_listings
from scorer   import score_all
from storage  import save_new_listings
from notifier import notify_all
from config   import MIN_LEGITIMACY_SCORE

# ---------------------------------------------------------------------------
# Configure logging for the main script.
# The format includes milliseconds (%f) so when debugging timing issues you
# can see exactly how long each phase took.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),  # print to console (visible in GitHub Actions logs)
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info(f"Walk-In Scanner started at {datetime.utcnow().isoformat()}")
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # PHASE 1: SCRAPING
    # Collect raw listings from all sources. This phase is the most likely
    # to have partial failures — one source might be down, another might
    # be blocked. gather_all_listings() handles all those failures internally
    # and always returns a list (possibly empty, never raises an exception).
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 1: Scraping all sources ---")
    raw_listings = gather_all_listings()

    if not raw_listings:
        logger.info("No relevant listings found in any source. Exiting.")
        # sys.exit(0) = success exit (job ran fine, just nothing to do)
        # Using exit(0) rather than just returning from main() is a convention
        # in CLI scripts to make the exit status visible in GitHub Actions logs.
        sys.exit(0)

    logger.info(f"Phase 1 complete: {len(raw_listings)} raw listings collected")

    # ------------------------------------------------------------------
    # PHASE 2: AI SCORING
    # Send each listing to Groq for structured extraction and legitimacy
    # scoring. score_all() drops listings below MIN_LEGITIMACY_SCORE.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 2: AI scoring and extraction ---")
    scored_listings = score_all(raw_listings, min_score=MIN_LEGITIMACY_SCORE)

    if not scored_listings:
        logger.info("No listings passed the legitimacy threshold. Exiting.")
        sys.exit(0)

    logger.info(f"Phase 2 complete: {len(scored_listings)} listings passed scoring")

    # ------------------------------------------------------------------
    # PHASE 3: DEDUPLICATION + STORAGE
    # Check against Google Sheets for listings we've already seen.
    # Save new ones. Returns only the brand-new listings.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 3: Deduplication and storage ---")
    new_listings = save_new_listings(scored_listings)

    if not new_listings:
        logger.info("All listings are duplicates of previously seen ones. Exiting.")
        sys.exit(0)

    logger.info(f"Phase 3 complete: {len(new_listings)} new listings to alert about")

    # ------------------------------------------------------------------
    # PHASE 4: NOTIFICATION
    # Send one Telegram message per new listing, plus a summary header.
    # ------------------------------------------------------------------
    logger.info("\n--- PHASE 4: Sending Telegram notifications ---")
    notify_all(new_listings, total_scraped=len(raw_listings))

    # ------------------------------------------------------------------
    # FINAL SUMMARY
    # A clean summary at the end makes it easy to audit runs in the
    # GitHub Actions log viewer without scrolling through everything.
    # ------------------------------------------------------------------
    logger.info("\n" + "=" * 60)
    logger.info("SCAN COMPLETE")
    logger.info(f"  Raw listings scraped:   {len(raw_listings)}")
    logger.info(f"  Passed AI scoring:      {len(scored_listings)}")
    logger.info(f"  New (not duplicates):   {len(new_listings)}")
    logger.info(f"  Telegram alerts sent:   {len(new_listings)}")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# Standard Python entry point guard.
# The condition `if __name__ == "__main__"` means: only run main() when this
# file is executed directly (python scanner.py), NOT when it's imported by
# another module. This makes scanner.py importable for testing without
# accidentally triggering the full pipeline on import.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    main()
