import logging
import sys
from datetime import datetime, timezone

from sources  import gather_all_listings
from scorer   import score_all
from storage  import save_new_listings
from notifier import notify_all
from config   import MIN_LEGITIMACY_SCORE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("Walk-In + Intern Scanner started at %s",
                datetime.now(timezone.utc).isoformat())
    logger.info("=" * 60)

    logger.info("--- PHASE 1: Scraping ---")
    raw_listings = gather_all_listings()
    if not raw_listings:
        logger.info("No listings found. Exiting.")
        sys.exit(0)
    logger.info("Phase 1: %d listings collected", len(raw_listings))

    logger.info("--- PHASE 2: AI Scoring ---")
    scored = score_all(raw_listings, min_score=MIN_LEGITIMACY_SCORE)
    if not scored:
        logger.info("No listings passed scoring. Exiting.")
        sys.exit(0)
    logger.info("Phase 2: %d listings passed", len(scored))

    logger.info("--- PHASE 3: Storage ---")
    new_listings = save_new_listings(scored)
    if not new_listings:
        logger.info("All duplicates. Exiting.")
        sys.exit(0)
    logger.info("Phase 3: %d new listings", len(new_listings))

    logger.info("--- PHASE 4: Telegram Alerts ---")
    notify_all(new_listings, total_scraped=len(raw_listings))

    interns  = sum(1 for l in new_listings if l.get("is_intern"))
    freshers = sum(1 for l in new_listings if l.get("is_fresher_eligible"))

    logger.info("=" * 60)
    logger.info("COMPLETE | Scraped: %d | Scored: %d | New: %d",
                len(raw_listings), len(scored), len(new_listings))
    logger.info("  Intern: %d | Walk-in: %d | Fresher-eligible: %d",
                interns, walkins, freshers)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
