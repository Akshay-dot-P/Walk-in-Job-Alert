import os
import time
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"


def send_message(text: str) -> bool:
    token   = os.environ.get("TELEGRAM_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set")
        return False
    try:
        r = requests.post(
            TELEGRAM_URL.format(token=token),
            json={
                "chat_id":                  chat_id,
                "text":                     text,
                "parse_mode":               "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram error: %s", e)
        return False


def e(text) -> str:
    """Escape HTML special chars."""
    return (
        str(text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_alert(listing: dict) -> str:
    score = listing.get("legitimacy_score", 0)

    badge = "✅" if score >= 8 else "🟡" if score >= 6 else "⚠️"

    tags = []
    if listing.get("is_intern"):           tags.append("🎓 INTERN")
    if listing.get("is_fresher_eligible"): tags.append("🌱 FRESHER OK")

    mode = listing.get("work_mode", "unknown")
    mode_tag = {"remote": "💻 Remote", "hybrid": "🔀 Hybrid",
                "onsite": "🏢 Onsite"}.get(mode, "")

    domain = listing.get("domain", "")
    domain_tag = f"🔐 {domain}" if domain else ""

    flags = listing.get("red_flags", [])
    flags_text = "\n".join(f"  • {e(f)}" for f in flags) if flags else "  None"

    skills = listing.get("skills_required", [])
    skills_text = ", ".join(skills[:6]) if skills else "Not specified"

    lines = [
        f"{badge} <b>Score {score}/10</b>  {' | '.join(filter(None, [domain_tag, mode_tag] + tags))}",
        "",
        f"<b>Role:</b> {e(listing.get('job_title', 'Unknown'))}",
        f"<b>Company:</b> {e(listing.get('company', 'Unknown'))} <i>({e(listing.get('company_tier', 'unknown'))})</i>",
    ]

    exp = listing.get("experience_required")
    if exp:
        lines.append(f"<b>Experience:</b> {e(exp)}")

    salary = listing.get("salary_range")
    if salary:
        lines.append(f"<b>Salary:</b> {e(salary)}")

    lines.append(f"<b>Skills:</b> {e(skills_text)}")

    notice = listing.get("notice_period")
    if notice:
        lines.append(f"<b>Notice Period:</b> {e(notice)}")

    openings = listing.get("openings_count")
    if openings:
        lines.append(f"<b>Openings:</b> {e(str(openings))}")

    deadline = listing.get("application_deadline")
    if deadline:
        lines.append(f"<b>Apply By:</b> {e(deadline)}")

    lines += [
        "",
        f"<b>Summary:</b> {e(listing.get('summary', ''))}",
        "",
        "<b>Red flags:</b>",
        flags_text,
        "",
        f"<b>Source:</b> {e(listing.get('source', ''))}",
    ]

    apply_url = listing.get("apply_url") or listing.get("url", "")
    if apply_url:
        lines.append(f'<a href="{apply_url}">Apply Now →</a>')

    return "\n".join(lines)
    

def notify_all(new_listings: list, total_scraped: int):
    if not new_listings:
        logger.info("No new listings to notify")
        return

    interns  = sum(1 for l in new_listings if l.get("is_intern"))
    freshers = sum(1 for l in new_listings if l.get("is_fresher_eligible"))

    now = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    header = (
        f"🔍 <b>Cyber Job Scanner</b> — {now}\n"
        f"Scraped <b>{total_scraped}</b> listings → <b>{len(new_listings)} new</b>\n"
        f"🎓 Intern: {interns}  |  🌱 Fresher OK: {freshers}"
    )
    send_message(header)
    time.sleep(1)

    for listing in new_listings:
        msg = format_alert(listing)
        success = send_message(msg)
        if success:
            logger.info("Notified: %s | %s",
                        listing.get("company"), listing.get("job_title"))
        else:
            logger.error("Notify failed: %s", listing.get("company"))
        time.sleep(1)
