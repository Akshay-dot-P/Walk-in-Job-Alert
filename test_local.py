# =============================================================================
# test_local.py
# =============================================================================
# Run this BEFORE pushing to GitHub to verify each component works.
# This test uses real API calls but with a mock listing, so it's fast
# and won't spam your Telegram or fill your Google Sheet with test data.
#
# Usage:
#   Set your environment variables first, then run:
#   export GROQ_API_KEY="gsk_..."
#   export TELEGRAM_TOKEN="12345:AAF..."
#   export TELEGRAM_CHAT_ID="987654321"
#   export GOOGLE_CREDS_JSON='{"type":"service_account",...}'
#   python test_local.py
# =============================================================================

import os
import sys
import json

print("=" * 60)
print("Walk-In Scanner — Local Component Test")
print("=" * 60)

# =============================================================================
# TEST 1: Check environment variables are set
# =============================================================================
print("\n[Test 1] Checking environment variables...")

required_vars = ["GROQ_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_CREDS_JSON"]
missing = [v for v in required_vars if not os.environ.get(v)]

if missing:
    print(f"❌ Missing environment variables: {missing}")
    print("   Set them before running this test (see README Part 5)")
    sys.exit(1)
else:
    print("✅ All environment variables are present")

# =============================================================================
# TEST 2: Check imports work (all dependencies installed)
# =============================================================================
print("\n[Test 2] Checking Python imports...")

try:
    from groq import Groq
    print("  ✅ groq")
except ImportError:
    print("  ❌ groq — run: pip install groq")

try:
    import gspread
    print("  ✅ gspread")
except ImportError:
    print("  ❌ gspread — run: pip install gspread")

try:
    import feedparser
    print("  ✅ feedparser")
except ImportError:
    print("  ❌ feedparser — run: pip install feedparser")

try:
    import requests
    print("  ✅ requests")
except ImportError:
    print("  ❌ requests — run: pip install requests")

# JobSpy is optional — warn but don't fail
try:
    from jobspy import scrape_jobs
    print("  ✅ jobspy")
except ImportError:
    print("  ⚠️  jobspy not installed (optional — LinkedIn/Indeed disabled)")
    print("      Install with: pip install jobspy==0.1.77")

# =============================================================================
# TEST 3: Groq API — can we score a mock listing?
# =============================================================================
print("\n[Test 3] Testing Groq API (scoring a mock listing)...")

MOCK_LISTING = {
    "source": "test",
    "title": "Walk-In Drive for Cloud Engineers",
    "company": "Accenture",
    "location": "Prestige Tech Park, Whitefield, Bangalore",
    "url": "https://example.com/test",
    "description": (
        "Accenture is conducting a walk-in interview for Cloud Engineers "
        "on 15th April 2025 from 10 AM to 5 PM at Prestige Tech Park, Block B, "
        "Whitefield, Bangalore 560066. Bring 3 copies of your resume and photo ID. "
        "Contact: hr.blr@accenture.com | Phone: 080-12345678. "
        "Skills required: AWS/GCP, Terraform, Kubernetes, 2-5 years experience. "
        "Roles: Cloud Engineer, DevOps Engineer, SRE."
    ),
}

try:
    from scorer import score_listing
    result = score_listing(MOCK_LISTING)

    if result:
        print(f"  ✅ Groq scored the mock listing:")
        print(f"     Company:    {result['company']}")
        print(f"     Role:       {result['job_title']}")
        print(f"     Date:       {result['walk_in_date']}")
        print(f"     Time:       {result['walk_in_time']}")
        print(f"     Venue:      {result['location_address']}")
        print(f"     Score:      {result['legitimacy_score']}/10")
        print(f"     Red flags:  {result['red_flags']}")
    else:
        print("  ❌ Groq returned None — check your API key and try again")
except Exception as e:
    print(f"  ❌ Groq test failed: {e}")

# =============================================================================
# TEST 4: Google Sheets — can we connect and read?
# =============================================================================
print("\n[Test 4] Testing Google Sheets connection...")

try:
    from storage import get_worksheet
    ws = get_worksheet()
    row_count = len(ws.get_all_records())
    print(f"  ✅ Connected to Google Sheets. Current rows: {row_count}")
except Exception as e:
    print(f"  ❌ Google Sheets failed: {e}")
    print("     Common causes:")
    print("     - GOOGLE_CREDS_JSON is invalid JSON")
    print("     - Service account not shared on the sheet")
    print("     - Sheet not named 'WalkIn Jobs Bangalore' exactly")

# =============================================================================
# TEST 5: Telegram — can we send a test message?
# =============================================================================
print("\n[Test 5] Testing Telegram bot (sending a test message)...")

try:
    from notifier import send_message
    success = send_message(
        "🧪 <b>Test message from Walk-In Scanner</b>\n"
        "If you see this, your Telegram setup is working correctly!"
    )
    if success:
        print("  ✅ Telegram message sent! Check your Telegram now.")
    else:
        print("  ❌ Telegram message failed — check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")
except Exception as e:
    print(f"  ❌ Telegram test failed: {e}")

# =============================================================================
# TEST 6: Naukri source — can we fetch without crashing?
# =============================================================================
print("\n[Test 6] Testing Naukri source (live fetch)...")

try:
    from sources import fetch_naukri
    listings = fetch_naukri()
    print(f"  ✅ Naukri returned {len(listings)} relevant listings")
    if listings:
        print(f"     First result: {listings[0]['company']} — {listings[0]['title'][:50]}")
except Exception as e:
    print(f"  ❌ Naukri fetch failed: {e}")

# =============================================================================
# SUMMARY
# =============================================================================
print("\n" + "=" * 60)
print("All tests complete. Fix any ❌ items before pushing to GitHub.")
print("=" * 60)
