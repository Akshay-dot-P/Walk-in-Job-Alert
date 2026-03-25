# =============================================================================
# notifier.py
# =============================================================================
# Sends Telegram messages for each new job listing.
#
# HTML parse mode is used instead of MarkdownV2 because MarkdownV2 requires
# escaping almost every punctuation character in dynamic content, which is
# fragile with AI-extracted text. HTML only needs &, <, > escaped (done
# by the e() helper below).
#
# Environment variables (match GitHub Actions secret names):
#   TELEGRAM_TOKEN   — bot token from BotFather
#   TELEGRAM_CHAT_ID — your personal or group chat ID
# =============================================================================

import os
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


# =============================================================================
# HELPER: HTML-escape dynamic content for Telegram HTML parse mode
# =============================================================================

def e(text) -> str:
    """
    Escape HTML special characters. Handles None gracefully since
    AI-extracted fields (location_address, contact, etc.) can be None.
    """
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# =============================================================================
# FUNCTION: Send a single Telegram message
# =============================================================================

def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    POST one message to the configured Telegram chat.
    Returns True on success, False on any failure.
    """
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment")
        return False

    url     = TELEGRAM_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id":                  chat_id,
        "text":                     text,
        "parse_mode":               parse_mode,
        "disable_web_page_preview": True,   # cleaner messages, no link previews
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.HTTPError:
        # 400 usually means bad HTML — log Telegram's error body for debugging
        logger.error("Telegram HTTP error: %s", response.text[:500])
        return False
    except Exception as exc:
        logger.error("Telegram send error: %s", exc)
        return False


# =============================================================================
# FUNCTION: Format a single listing as a Telegram HTML message
# =============================================================================

def format_listing_message(listing: dict) -> str:
    """
    Build a well-formatted HTML message for one job listing.
    Fields match SHEET_COLUMNS exactly:
      scraped_at, job_title, company, company_tier, location_address,
      contact, legitimacy_score, red_flags, source, url, status

    Note: walk_in_date / walk_in_time have been removed — the project now
    scrapes online job postings, not in-person walk-in events.
    """
    score = listing.get("legitimacy_score", 0)

    # Confidence badge — visible at a glance before reading the details
    if score >= 8:
        badge = "✅"   # High confidence — strong match, apply now
    elif score >= 6:
        badge = "🟡"   # Reasonable match — worth checking
    else:
        badge = "⚠️"   # Low confidence — verify before applying

    # Red flags (stored as comma-separated string by scorer.py)
    flags_raw = listing.get("red_flags", "") or ""
    if flags_raw and flags_raw.strip():
        flags_lines = "\n".join(f"  • {e(f.strip())}" for f in flags_raw.split(",") if f.strip())
    else:
        flags_lines = "  None detected"

    # Extract and escape all dynamic fields
    title   = e(listing.get("job_title")        or "Unknown Role")
    company = e(listing.get("company")          or "Unknown Company")
    tier    = e(listing.get("company_tier")     or "unknown")
    loc     = e(listing.get("location_address") or "Not specified")
    contact = e(listing.get("contact")          or "Not specified")
    source  = e(listing.get("source")           or "")
    url     = listing.get("url", "")            # raw URL — not HTML-escaped (used in href)

    lines = [
        f"{badge} <b>Job Alert</b>  |  Score: <b>{score}/10</b>",
        "",
        f"<b>Role:</b> {title}",
        f"<b>Company:</b> {company} <i>({tier})</i>",
        f"<b>Location:</b> {loc}",
        f"<b>Contact:</b> {contact}",
        "",
        "<b>Red flags:</b>",
        flags_lines,
        "",
        f"<b>Source:</b> {source}",
    ]

    if url:
        lines.append(f'<a href="{url}">View Listing →</a>')

    return "\n".join(lines)


# =============================================================================
# FUNCTION: Summary header sent before per-listing alerts
# =============================================================================

def send_run_header(total_scraped: int, total_passed: int) -> None:
    """One summary message at the top of each scan run."""
    now  = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    text = (
        f"🔍 <b>Job Scanner Run</b> — {now}\n"
        f"Scraped <b>{total_scraped}</b> listings across LinkedIn &amp; Indeed.\n"
        f"<b>{total_passed}</b> passed the relevance threshold — alerts below 👇"
    )
    send_message(text)


# =============================================================================
# FUNCTION: Main entry point — send all new listing notifications
# =============================================================================

def notify_all(new_listings: list, total_scraped: int) -> None:
    """
    Send a header summary, then one Telegram message per new listing.
    Called by scanner.py after storage.py returns the truly new subset.
    """
    if not new_listings:
        logger.info("No new listings to notify about")
        return

    send_run_header(total_scraped, len(new_listings))

    for listing in new_listings:
        message = format_listing_message(listing)
        success = send_message(message)

        if success:
            logger.info(
                "Notified: %s | %s",
                listing.get("company", "?"),
                listing.get("job_title", "?"),
            )
        else:
            logger.error(
                "Notification failed for: %s | %s",
                listing.get("company", "?"),
                listing.get("job_title", "?"),
            )

        # Telegram allows ~30 messages/sec; 1s gap is well within safe limits
        time.sleep(1)
