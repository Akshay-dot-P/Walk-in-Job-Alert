# update_sheet_with_links.py
"""
Reads the manifest.json file and updates Google Sheet with GitHub download URLs
"""
import os
import json
import logging
import sys
import gspread
from google.oauth2.service_account import Credentials
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]
SHEET_NAME = os.environ.get("SHEET_NAME", "WalkIn Jobs Bangalore")
WORKSHEET_NAME = os.environ.get("WORKSHEET_NAME", "Sheet1")
RELEASE_TAG = os.environ["RELEASE_TAG"]
GITHUB_REPOSITORY = os.environ["GITHUB_REPOSITORY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", "/tmp")
TAILORED_COL = "tailored_resume"

def get_worksheet():
    creds_info = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.Client(auth=creds)
    sh = gc.open(SHEET_NAME)
    return sh.worksheet(WORKSHEET_NAME)

def main():
    # Read manifest
    manifest_path = Path(RUNNER_TEMP) / "generated_resumes" / "manifest.json"
    
    if not manifest_path.exists():
        logger.info("No manifest found - no resumes were generated")
        sys.exit(0)
    
    with open(manifest_path) as f:
        manifest = json.load(f)
    
    if not manifest.get("files"):
        logger.info("No files in manifest")
        sys.exit(0)
    
    # Connect to sheet
    ws = get_worksheet()
    headers = ws.row_values(1)
    tailored_col_idx = headers.index(TAILORED_COL) + 1
    
    # Update each row with its download URL
    for file_info in manifest["files"]:
        filename = file_info["filename"]
        row_num = file_info["row_num"]
        
        # Construct GitHub download URL
        download_url = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/{RELEASE_TAG}/{filename}"
        
        # Update sheet
        ws.update_cell(row_num, tailored_col_idx, download_url)
        logger.info(f"Row {row_num}: {download_url}")
    
    logger.info(f"Updated {len(manifest['files'])} rows")

if __name__ == "__main__":
    main()
