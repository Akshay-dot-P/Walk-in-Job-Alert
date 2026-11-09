import logging
import sys
from datetime import datetime, timezone

from sources  import gather_all_listings
from scorer   import score_all
from storage  import save_new_listings
from notifier import notify_all
from config   import MIN_LEGITIMACY_SCORE

import json, os

RAW_CACHE = os.environ.get("RAW_CACHE", "raw_listings.json")


def load_or_scrape() -> list[dict]:
    if os.environ.get("REUSE_RAW") == "1" and os.path.exists(RAW_CACHE):
        with open(RAW_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        logger.info("Loaded %d listings from %s (no scraping)", len(data), RAW_CACHE)
        return data
    data = gather_all_listings()
    try:
        with open(RAW_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as exc:
        logger.warning("Could not write %s: %s", RAW_CACHE, exc)
    return data


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main():
    started_at = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Cyber Job Scanner started at %s", started_at.isoformat())
    logger.info("=" * 60)

    logger.info("--- PHASE 1: Scraping ---")
    raw_listings = load_or_scrape()
    if not raw_listings:
        logger.info("No listings found. Exiting.")
        sys.exit(0)
    logger.info("Phase 1: %d listings collected", len(raw_listings))

    if os.environ.get("DRY_RUN") == "1":
        from scorer import pre_filter, freshness_filter, aggressive_deduplicate, flush_audit
        kept = aggressive_deduplicate(freshness_filter(pre_filter(raw_listings)))
        logger.info("DRY RUN: %d listings would go to scoring", len(kept))
        flush_audit()
        sys.exit(0)    

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

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed  = (datetime.now(timezone.utc) - started_at).seconds
    n_intern = sum(1 for l in new_listings if l.get("is_intern"))
    n_jobs   = len(new_listings) - n_intern

    logger.info("=" * 60)
    logger.info("COMPLETE | Scraped: %d | Scored: %d | New: %d | Runtime: %ds",
                len(raw_listings), len(scored), len(new_listings), elapsed)
    logger.info("  Internships: %d | Jobs: %d", n_intern, n_jobs)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
