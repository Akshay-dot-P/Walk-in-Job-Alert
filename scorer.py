import requests
import time
import logging
import json
from datetime import datetime, timezone
from config import GROQ_MODEL   # assuming this contains GROQ_API_KEY etc.

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
SYSTEM_PROMPT = "Return only raw JSON. No markdown fences, no preamble, no explanation."

USER_PROMPT = """\
Analyze this Indian job listing and return ONLY valid JSON with these exact keys:
{
  "job_title": "string",
  "company": "string",
  "company_tier": "MNC or startup or mid-tier or unknown",
  "walk_in_date": "YYYY-MM-DD or null",
  "walk_in_time": "HH:MM-HH:MM or null",
  "location_address": "full venue address string or null",
  "contact": "email or phone or null",
  "legitimacy_score": <integer 1-10>,
  "red_flags": ["list", "of", "strings"],
  "summary": "2-sentence plain-text summary"
}
Target roles (flag as highly relevant if listing matches):
  Security : security analyst, appsec, application security, SOC analyst, infosec,
             cybersecurity, social engineering, VAPT, pentester, threat analyst
  GRC : GRC analyst, compliance analyst, IT audit, regulatory compliance
  Risk : risk analyst, operational risk, credit risk, market risk
  Fraud/ORC : fraud analyst, AML, anti-money laundering, transaction monitoring,
              organized retail crime, loss prevention, financial crimes
  Intern/Entry: intern, internship, trainee, fresher, junior analyst, entry level

Scoring:
  9-10: Known MNC, full street address, corporate email, specific date+time
  7-8 : Recognizable company, has address + contact + date
  5-6 : Unknown company, has venue + contact + date, no scam signals
  1-4 : Missing address OR contact, registration fee, guaranteed offer, fake salary

Listing:
{listing_text}"""


def _call_groq(prompt: str, max_retries: int = 3) -> str:
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",   # ← make sure GROQ_API_KEY is in config or env
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 600,
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(f"Groq call failed (attempt {attempt}/{max_retries}): {e}")
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)  # exponential backoff


def _parse_json(raw: str):
    """Safely parse JSON even if model adds extra text"""
    raw = raw.strip()
    # Remove possible markdown code fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0]
    return json.loads(raw)


def score_listing(listing: dict) -> dict | None:
    try:
        text = listing.get("description") or listing.get("full_text", "")
        if not text:
            logger.warning(f"No text to score for {listing.get('title', '?')}")
            return None

        raw = _call_groq(USER_PROMPT.format(listing_text=text))
        d = _parse_json(raw)

        job_url = listing.get("url", "")

        return {
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "source": listing.get("source", ""),
            "url": job_url,
            "job_title": d.get("job_title") or listing.get("title", ""),
            "company": d.get("company") or listing.get("company", ""),
            "company_tier": d.get("company_tier", "unknown"),
            "walk_in_date": d.get("walk_in_date"),
            "walk_in_time": d.get("walk_in_time"),
            "location_address": d.get("location_address"),
            "contact": d.get("contact"),
            "legitimacy_score": int(d.get("legitimacy_score", 1)),
            "red_flags": d.get("red_flags", []),
            "summary": d.get("summary", ""),
            "status": "pending",
        }
    except Exception as exc:
        logger.error(f"Scoring failed for '{listing.get('title', '?')}': {exc}")
        return None


def score_all(listings: list[dict], min_score: int = 5) -> list[dict]:
    scored = []
    total = len(listings)

    for i, listing in enumerate(listings, 1):
        logger.info(f"[{i}/{total}] Scoring: {listing.get('title', '?')}")
        
        result = score_listing(listing)
        if not result:
            continue

        score = result["legitimacy_score"]
        if score < min_score:
            logger.info(f" → Dropped (score {score} < {min_score})")
        else:
            logger.info(f" → Kept (score {score})")
            scored.append(result)

        time.sleep(2)  # respect Groq free tier rate limit (~30 req/min)

    logger.info(f"Scoring done: {len(scored)}/{total} passed")
    return scored
