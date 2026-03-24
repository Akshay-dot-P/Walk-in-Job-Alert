import os, json, logging, re, time, requests
from datetime import datetime
from config import GROQ_MODEL

logger = logging.getLogger(__name__)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

PROMPT = """Analyze this Indian job listing and return ONLY valid JSON, no other text.
Keys required:
{{"job_title":"string","company":"string","company_tier":"MNC or startup or mid-tier or unknown","walk_in_date":"YYYY-MM-DD or null","walk_in_time":"HH:MM-HH:MM or null","location_address":"string or null","contact":"string or null","legitimacy_score":1-10,"red_flags":[],"summary":"string"}}

Score 9-10: known MNC, full address, corporate email, specific date/time
Score 7-8: recognizable company, has address+contact+date
Score 5-6: unknown company but has specific venue+contact+date, no scam signals
Score 1-4: missing address or contact, registration fee, guaranteed offer, fake salary claims

Listing:
{listing_text}"""

def call_groq(prompt):
    key = os.environ.get("GROQ_API_KEY","")
    if not key:
        raise ValueError("GROQ_API_KEY not set")
    r = requests.post(
        GROQ_API_URL,
        headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
        json={"model":GROQ_MODEL,"messages":[{"role":"system","content":"Return only raw JSON, no markdown."},{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":600},
        timeout=30
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def score_listing(listing):
    text = f"TITLE: {listing.get('title','')}\nCOMPANY: {listing.get('company','')}\nLOCATION: {listing.get('location','')}\nURL: {listing.get('url','')}\nDESCRIPTION: {listing.get('description','')[:2000]}"
    try:
        raw = call_groq(PROMPT.format(listing_text=text))
        raw = re.sub(r"```json|```","",raw).strip()
        raw = raw[raw.find("{"):raw.rfind("}")+1]
        d = json.loads(raw)
        return {
            "scraped_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "source": listing.get("source",""),
            "url": listing.get("url",""),
            "job_title": d.get("job_title", listing.get("title","")),
            "company": d.get("company", listing.get("company","")),
            "company_tier": d.get("company_tier","unknown"),
            "walk_in_date": d.get("walk_in_date"),
            "walk_in_time": d.get("walk_in_time"),
            "location_address": d.get("location_address"),
            "contact": d.get("contact"),
            "legitimacy_score": int(d.get("legitimacy_score",1)),
            "red_flags": d.get("red_flags",[]),
            "summary": d.get("summary",""),
            "status": "pending",
        }
    except Exception as e:
        logger.error(f"Scoring error for '{listing.get('title')}': {e}")
        return None

def score_all(listings, min_score=5):
    scored = []
    for i,listing in enumerate(listings):
        logger.info(f"Scoring {i+1}/{len(listings)}: {listing.get('title','?')[:50]}")
        result = score_listing(listing)
        if result is None:
            continue
        if result["legitimacy_score"] < min_score:
            logger.info(f"  -> Dropped (score {result['legitimacy_score']})")
            continue
        scored.append(result)
        time.sleep(2)
    logger.info(f"Done: {len(scored)} passed")
    return scored
