# =============================================================================
# scorer.py - AI Scoring Module with Rate Limit Handling (Fixed March 2026)
# =============================================================================
import time
import json
import logging
from datetime import datetime
from groq import Groq, APIError, RateLimitError

logger = logging.getLogger(__name__)

# Initialize Groq client with reasonable retries
client = Groq(
    api_key=None,  # will be read from GROQ_API_KEY env var
    max_retries=2,  # built-in retries for connection issues
)

# Scoring prompt template (customize as needed)
SCORING_PROMPT = """
You are an expert technical recruiter for Bangalore walk-in / urgent hiring roles.
Evaluate the following job if it is a **genuine walk-in / drive / interview** opportunity suitable for tech roles (Cloud, DevOps, SRE, Security, SDE, Software Engineer etc.).

Job Title: {title}
Company: {company}
Location: {location}
Description: {description}

Rate from 0 to 100 on how likely this is a **real walk-in / on-site interview drive** in Bangalore:
- 90-100: Clearly a walk-in/drive with immediate interviews
- 60-89: Likely relevant tech hiring (even if not explicit "walk-in")
- Below 60: Not relevant (remote-only, generic JD, no Bangalore mention, etc.)

Return ONLY a valid JSON:
{{
  "score": integer between 0-100,
  "reason": "short one-sentence explanation",
  "is_walkin": boolean,
  "tech_role": boolean
}}
"""

def score_job(job: dict) -> dict:
    """
    Scores a single job using Groq with robust rate-limit handling.
    Returns the original job enriched with score data or None if it fails badly.
    """
    title = job.get("title", "")
    company = job.get("company", "")
    location = job.get("location", "Bangalore")
    description = job.get("description", "")[:800]  # truncate to save tokens

    prompt = SCORING_PROMPT.format(
        title=title,
        company=company,
        location=location,
        description=description
    )

    for attempt in range(5):  # max 5 attempts with backoff
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",   # fast + good quality on free tier
                # model="mixtral-8x7b-32768",      # alternative if you prefer
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content.strip()
            result = json.loads(content)

            job["ai_score"] = result.get("score", 0)
            job["ai_reason"] = result.get("reason", "")
            job["is_walkin"] = result.get("is_walkin", False)
            job["tech_role"] = result.get("tech_role", False)

            logger.info(f"Scored: {title[:60]}... → Score: {job['ai_score']}")
            return job

        except RateLimitError as e:
            retry_after = None
            if hasattr(e, 'response') and e.response is not None:
                retry_after = e.response.headers.get("Retry-After")

            wait_time = int(retry_after) if retry_after else (2 ** attempt) * 3 + 2  # exponential backoff
            logger.warning(f"Rate limit hit (attempt {attempt+1}). Waiting {wait_time}s...")

            time.sleep(wait_time)

        except (APIError, json.JSONDecodeError) as e:
            logger.error(f"API/JSON error scoring '{title}': {e}")
            if attempt == 4:
                return None
            time.sleep(2 ** attempt * 1.5)

        except Exception as e:
            logger.error(f"Unexpected error scoring '{title}': {e}")
            return None

    logger.warning(f"Failed to score after retries: {title}")
    return None


def score_listings(listings: list, threshold: int = 65) -> list:
    """
    Score all listings with rate-limit protection.
    """
    logger.info(f"--- PHASE 2: AI scoring and extraction ({len(listings)} jobs) ---")
    passed = []
    start_time = datetime.now()

    for i, job in enumerate(listings, 1):
        logger.info(f"Scoring {i}/{len(listings)}: {job.get('title', 'No title')}")

        scored_job = score_job(job)

        if scored_job and scored_job.get("ai_score", 0) >= threshold:
            passed.append(scored_job)
            logger.info(f"✓ Passed (score: {scored_job['ai_score']})")
        elif scored_job:
            logger.info(f"✗ Rejected (score: {scored_job.get('ai_score', 0)})")

        # Gentle delay even on success to stay well under free-tier limits
        time.sleep(1.8)

    duration = datetime.now() - start_time
    logger.info(f"Done: {len(passed)} passed | Phase 2 took {duration}")
    return passed
