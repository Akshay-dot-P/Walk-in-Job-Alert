# =============================================================================
# notifier.py
# =============================================================================
# Sends Telegram messages for each new verified walk-in listing.
#
# How Telegram bots work:
# You create a bot via BotFather (@BotFather). BotFather gives you a TOKEN.
# To send messages, POST to:
#   https://api.telegram.org/bot<TOKEN>/sendMessage
# with JSON: { chat_id, text, parse_mode }
#
# We use HTML parse mode (not MarkdownV2) because MarkdownV2 requires
# escaping almost every punctuation character in dynamic content.
# With HTML, only < > & need escaping, which we handle with e() below.
# =============================================================================

import os
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


# =============================================================================
# HELPER: HTML escape — defined first so all functions below can use it
# =============================================================================

def e(text) -> str:
    """
    Escape HTML special characters for Telegram HTML parse mode.
    Must handle None gracefully since AI-extracted fields can be None.
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
    Send a single message to the configured Telegram chat.
    Returns True on success, False on any failure.
    """
    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment")
        return False

    url = TELEGRAM_API_BASE.format(token=token, method="sendMessage")
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        # Prevents Telegram from fetching link previews — cleaner + faster
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.HTTPError as exc:
        # 400 usually means bad HTML — log the Telegram error message
        logger.error(f"Telegram HTTP error: {exc}")
        logger.error(f"Telegram response: {response.text[:500]}")
        return False
    except Exception as exc:
        logger.error(f"Telegram send error: {exc}")
        return False


# =============================================================================
# FUNCTION: Format a single listing as a Telegram HTML message
# =============================================================================

def format_listing_message(listing: dict) -> str:
    """
    Build a well-formatted HTML Telegram message for one walk-in listing.
    Uses e() to safely escape all dynamic content.
    """
    score = listing.get("legitimacy_score", 0)

    # Quality badge — users see this at a glance before reading details
    if score >= 8:
        badge = "✅"   # High confidence — definitely attend
    elif score >= 6:
        badge = "🟡"   # Reasonable — worth verifying
    else:
        badge = "⚠️"   # Low confidence — extra verification needed

    # Red flags list
    red_flags = listing.get("red_flags", [])
    if red_flags:
        flags_text = "\n".join(f"  • {e(flag)}" for flag in red_flags)
    else:
        flags_text = "  None detected"

    # Extract and escape all fields
    title    = e(listing.get("job_title") or "Unknown Role")
    company  = e(listing.get("company") or "Unknown Company")
    tier     = e(listing.get("company_tier") or "unknown")
    date_val = e(listing.get("walk_in_date") or "Not specified")
    time_val = e(listing.get("walk_in_time") or "Not specified")
    location = e(listing.get("location_address") or "Not specified")
    contact  = e(listing.get("contact") or "Not specified")
    summary  = e(listing.get("summary") or "")
    source   = e(listing.get("source") or "")
    url      = listing.get("url", "")  # Raw URL — not escaped (used in href)

    lines = [
        f"{badge} <b>Walk-In Alert</b>  |  Score: <b>{score}/10</b>",
        "",
        f"<b>Role:</b> {title}",
        f"<b>Company:</b> {company} <i>({tier})</i>",
        f"<b>Date:</b> {date_val}",
        f"<b>Time:</b> {time_val}",
        f"<b>Venue:</b> {location}",
        f"<b>Contact:</b> {contact}",
        "",
        f"<b>Summary:</b> {summary}",
        "",
        "<b>Red flags:</b>",
        flags_text,
        "",
        f"<b>Source:</b> {source}",
    ]

    if url:
        lines.append(f'<a href="{url}">View Original Listing →</a>')

    return "\n".join(lines)


# =============================================================================
# FUNCTION: Send a summary header before the per-listing alerts
# =============================================================================

def send_run_header(total_scraped: int, total_passed: int) -> None:
    """Send a single summary message at the start of a scan run."""
    # Use timezone-aware UTC
    now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    text = (
        f"🔍 <b>Walk-In Scanner Run</b> — {now}\n"
        f"Scraped <b>{total_scraped}</b> listings across all sources.\n"
        f"<b>{total_passed}</b> passed legitimacy threshold — alerts below 👇"
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
                f"Notified: {listing.get('company', '?')} | "
                f"{listing.get('job_title', '?')}"
            )
        else:
            logger.error(
                f"Notification failed for: {listing.get('company', '?')} | "
                f"{listing.get('job_title', '?')}"
            )

        # Telegram rate limit: ~30 msg/sec. 1s sleep is safe.
        time.sleep(1)
