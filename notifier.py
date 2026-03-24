# =============================================================================
# notifier.py
# =============================================================================
# This module sends Telegram messages for each new verified listing.
#
# How Telegram bots work (the quick version):
# You create a bot via BotFather (@BotFather on Telegram). BotFather gives
# you a TOKEN — a long string like "7123456789:AAFBhSKJDHs...". This token
# is the bot's identity and password combined. Anyone who has this token
# can send messages as your bot, so keep it secret (GitHub secret).
#
# To send a message, you make an HTTP POST to:
#   https://api.telegram.org/bot<TOKEN>/sendMessage
# with a JSON body containing:
#   chat_id: who to send to (your personal chat ID, or a group/channel ID)
#   text:    the message content
#   parse_mode: "MarkdownV2" or "HTML" for formatted messages
#
# Finding your chat_id:
#   1. Start a conversation with your bot (search its username, click Start)
#   2. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
#   3. Send any message to the bot
#   4. Refresh the getUpdates URL
#   5. Look for "chat": {"id": 123456789} — that number is your chat_id
# =============================================================================

import os
import logging
import requests

logger = logging.getLogger(__name__)

# Base URL for all Telegram Bot API calls.
# The f-string will be filled with the actual token when build_api_url() is called.
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


# =============================================================================
# FUNCTION: Send a single message to Telegram
# =============================================================================
def send_message(text: str, parse_mode: str = "HTML") -> bool:
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in environment")
        return False

    url = TELEGRAM_API_BASE.format(token=token, method="sendMessage")

    payload = {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": parse_mode,
        # disable_web_page_preview=True prevents Telegram from fetching
        # link previews, which makes messages cleaner and sends faster.
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True

    except requests.exceptions.HTTPError as e:
        # A 400 error usually means our message text has invalid HTML/Markdown.
        # Log the response body to see Telegram's specific error message.
        logger.error(f"Telegram API HTTP error: {e}")
        logger.error(f"Response body: {response.text}")
        return False

    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


# =============================================================================
# FUNCTION: Format a single listing into a Telegram message
# =============================================================================
# Telegram supports HTML formatting with a limited set of tags:
#   <b>bold</b>, <i>italic</i>, <code>monospace</code>, <a href="...">link</a>
#
# We use HTML mode (not MarkdownV2) because MarkdownV2 requires escaping
# almost every punctuation character, which is fragile in dynamic content.
# With HTML mode, the only characters we need to escape in non-tag content
# are < > & (which we handle with the escape() helper below).
# =============================================================================
def format_listing_message(listing: dict) -> str:
    score = listing.get("legitimacy_score", 0)

    # Pick an emoji based on score tier so users can instantly see quality
    if score >= 8:
        badge = "✅"      # high confidence, definitely attend
    elif score >= 6:
        badge = "🟡"      # reasonable, worth investigating
    else:
        badge = "⚠️"      # low confidence, verify before going

    # Build red flags string. If empty list, show "None detected"
    red_flags = listing.get("red_flags", [])
    if red_flags:
        flags_text = "\n".join(f"  • {e(flag)}" for flag in red_flags)
    else:
        flags_text = "  None detected"

    # e() escapes HTML special characters so they display correctly in Telegram
    title    = e(listing.get("job_title", "Unknown Role"))
    company  = e(listing.get("company", "Unknown Company"))
    tier     = e(listing.get("company_tier", "unknown"))
    date     = e(listing.get("walk_in_date") or "Not specified")
    time_val = e(listing.get("walk_in_time") or "Not specified")
    location = e(listing.get("location_address") or "Not specified")
    contact  = e(listing.get("contact") or "Not specified")
    summary  = e(listing.get("summary", ""))
    source   = e(listing.get("source", ""))
    url      = listing.get("url", "")

    # Build the message. Each line uses HTML tags for formatting.
    lines = [
        f"{badge} <b>Walk-In Alert</b>  |  Score: <b>{score}/10</b>",
        "",
        f"<b>Role:</b> {title}",
        f"<b>Company:</b> {company} <i>({tier})</i>",
        f"<b>Date:</b> {date}",
        f"<b>Time:</b> {time_val}",
        f"<b>Venue:</b> {location}",
        f"<b>Contact:</b> {contact}",
        "",
        f"<b>Summary:</b> {summary}",
        "",
        f"<b>Red flags:</b>",
        flags_text,
        "",
        f"<b>Source:</b> {source}",
    ]

    # Add the URL as a clickable link only if we have one
    if url:
        lines.append(f'<a href="{url}">View Original Listing →</a>')

    return "\n".join(lines)


# =============================================================================
# FUNCTION: Send a summary header before the individual listing alerts
# =============================================================================
def send_run_header(total_found: int, total_passed: int):
    from datetime import datetime
    now = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")

    text = (
        f"🔍 <b>Walk-In Scanner Run</b> — {now}\n"
        f"Found {total_found} relevant listings across all sources.\n"
        f"{total_passed} passed legitimacy threshold — alerting below."
    )
    send_message(text)


# =============================================================================
# FUNCTION: Notify for all new listings
# =============================================================================
# Main entry point called by scanner.py.
# Sends a summary header, then one message per new listing.
# =============================================================================
def notify_all(new_listings: list, total_scraped: int):
    if not new_listings:
        logger.info("No new listings to notify about")
        return

    # Send the summary header first
    send_run_header(total_scraped, len(new_listings))

    import time
    for listing in new_listings:
        message = format_listing_message(listing)
        success = send_message(message)

        if success:
            logger.info(f"Notified: {listing.get('company')} | {listing.get('job_title')}")
        else:
            logger.error(f"Failed to notify for: {listing.get('company')}")

        # Telegram rate-limits to ~30 messages/second. 1 second between
        # messages is very safe and prevents any throttling issues.
        time.sleep(1)


# =============================================================================
# HELPER: HTML escape function
# =============================================================================
# Escapes the three special HTML characters: < > &
# This prevents our dynamic text from accidentally creating or breaking HTML tags.
# For example, a company name like "C&C Technologies" would break HTML
# without escaping the & to &amp;
# =============================================================================
def e(text: str) -> str:
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
