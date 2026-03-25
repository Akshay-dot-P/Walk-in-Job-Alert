import requests
from datetime import datetime, timezone

from config import GROQ_MODEL









logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = "Return only raw JSON. No markdown fences, no preamble, no explanation."

USER_PROMPT = """\
Analyze this Indian job listing and return ONLY valid JSON with these exact keys:





















{{
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

}}

Target roles (flag as highly relevant if listing matches):
  Security   : security analyst, appsec, application security, SOC analyst, infosec,
               cybersecurity, social engineering, VAPT, pentester, threat analyst
  GRC        : GRC analyst, compliance analyst, IT audit, regulatory compliance
  Risk       : risk analyst, operational risk, credit risk, market risk
  Fraud/ORC  : fraud analyst, AML, anti-money laundering, transaction monitoring,
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
@@ -68,7 +104,9 @@ def _call_groq(prompt: str, max_retries: int = 3) -> str:
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 600,


    }

    for attempt in range(1, max_retries + 1):
def score_listing(listing: dict) -> dict | None:
            "job_title":        d.get("job_title") or listing.get("title", ""),
            "company":          d.get("company") or listing.get("company", ""),
            "company_tier":     d.get("company_tier", "unknown"),
            "walk_in_date": d.get("walk_in_date"),
            "walk_in_time": d.get("walk_in_time"),
            "location_address": d.get("location_address"),
            "contact":          d.get("contact"),
            "legitimacy_score": int(d.get("legitimacy_score", 1)),






            "red_flags":        d.get("red_flags", []),
            "summary": d.get("summary", ""),
            "status":           "pending",
        }
    except Exception as exc:
def score_all(listings: list[dict], min_score: int = 5) -> list[dict]:
            time.sleep(2)
            continue
        score = result["legitimacy_score"]

        if score < min_score:
            logger.info(f"  → Dropped (score {score} < {min_score})")
        else:
            logger.info(f"  → Kept (score {score})")
            scored.append(result)
        time.sleep(2)   # 2s between calls = max 30/min (Groq free tier)
    logger.info(f"Scoring done: {len(scored)}/{total} passed")
