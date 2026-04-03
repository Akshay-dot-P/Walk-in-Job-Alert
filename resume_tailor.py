# =============================================================================
# resume_tailor.py - Improved Version with Better Error Messages
# =============================================================================
import os
import json
import time
import logging
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from groq import Groq

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ====================== CONFIG ======================
SHEET_NAME = os.getenv("SHEET_NAME")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Sheet1")

logger.info(f"Using Sheet: {SHEET_NAME}")
logger.info(f"Using Worksheet: {WORKSHEET_NAME}")

# ====================== CONNECT TO GOOGLE SHEET ======================
def connect_to_sheet():
    try:
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        if not creds_json:
            raise ValueError("GOOGLE_CREDS_JSON is missing or empty")

        creds_dict = json.loads(creds_json)
        
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(credentials)
        
        sheet = client.open(SHEET_NAME).worksheet(WORKSHEET_NAME)
        
        logger.info("✅ Successfully connected to Google Sheet!")
        return sheet

    except json.JSONDecodeError:
        logger.error("❌ GOOGLE_CREDS_JSON is not valid JSON. Check the secret.")
        raise
    except gspread.exceptions.SpreadsheetNotFound:
        logger.error(f"❌ Sheet '{SHEET_NAME}' not found. Check SHEET_NAME secret.")
        raise
    except gspread.exceptions.WorksheetNotFound:
        logger.error(f"❌ Worksheet '{WORKSHEET_NAME}' not found. Check WORKSHEET_NAME secret.")
        raise
    except Exception as e:
        logger.error(f"❌ Google Sheets connection failed: {type(e).__name__} - {e}")
        raise


# ====================== MAIN ======================
def main():
    logger.info("============================================================")
    logger.info("Resume Tailor started")
    logger.info("============================================================")

    try:
        sheet = connect_to_sheet()
        logger.info("Connection test successful!")
        
        # For now, just test connection
        records = sheet.get_all_records()
        logger.info(f"Sheet has {len(records)} rows of data.")
        
    except Exception as e:
        logger.error("Failed to connect to Google Sheet. Please check secrets and permissions.")
        raise

    logger.info("Resume Tailor finished successfully.")


if __name__ == "__main__":
    main()
