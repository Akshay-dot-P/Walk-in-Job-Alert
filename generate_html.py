#!/usr/bin/env python3
"""
generate_html.py
================
Reads job listings from Excel (or Google Sheets via storage.py) and
re-generates walkin_jobs_bangalore.html with live data injected.

Usage:
    python generate_html.py                  # reads from Excel (default)
    python generate_html.py --source sheets  # reads from Google Sheets
    python generate_html.py --excel path/to/file.xlsx
    python generate_html.py --out custom_output.html
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import date
from pathlib import Path

# ── CLI ───────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Regenerate jobs HTML from Excel or Sheets")
parser.add_argument("--source", choices=["excel", "sheets"], default="excel")
parser.add_argument("--excel", default="WalkIn_Jobs_Bangalore__1_.xlsx",
                    help="Path to the Excel file (used when --source excel)")
parser.add_argument("--out", default="walkin_jobs_bangalore.html",
                    help="Output HTML file path")
parser.add_argument("--template", default="walkin_jobs_bangalore.html",
                    help="Source HTML template (original file)")
args = parser.parse_args()


# ── LOAD DATA ─────────────────────────────────────────────────────────────────

def load_from_excel(path: str) -> list[dict]:
    import pandas as pd
    df = pd.read_excel(path)

    KEEP = ["job_title", "company", "domain", "apply_url", "status",
            "summary", "experience_required", "posted_date", "salary_range",
            "hireability", "credibility", "resume_doc_link", "resume_pdf_link",
            "source"]

    df = df[[c for c in KEEP if c in df.columns]]

    records = []
    for _, row in df.iterrows():
        rec = {}
        for col in KEEP:
            if col not in df.columns:
                rec[col] = ""
                continue
            val = row[col]
            if col in ("hireability", "credibility"):
                rec[col] = None if (val is None or (isinstance(val, float) and math.isnan(val))) else float(val)
            elif col == "posted_date":
                import pandas as pd
                if hasattr(val, "strftime") and not pd.isnull(val):
                    rec[col] = val.strftime("%Y-%m-%d")
                elif val and str(val) not in ("NaT", "nan", "None", "NaTType"):
                    rec[col] = str(val)[:10]
                else:
                    rec[col] = ""
            else:
                if val is None or (isinstance(val, float) and math.isnan(val)):
                    rec[col] = ""
                else:
                    rec[col] = str(val).strip()
        records.append(rec)
    return records


def load_from_sheets() -> list[dict]:
    """Load via storage.py's get_worksheet() — requires GOOGLE_CREDS_JSON env var."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from storage import get_worksheet   # your existing storage.py
    except ImportError:
        sys.exit("storage.py not found next to generate_html.py. "
                 "Place it in the same directory or use --source excel.")

    ws = get_worksheet()
    rows = ws.get_all_records()   # returns list of dicts keyed by header row

    KEEP = ["job_title", "company", "domain", "apply_url", "status",
            "summary", "experience_required", "posted_date", "salary_range",
            "hireability", "credibility", "resume_doc_link", "resume_pdf_link",
            "source"]

    records = []
    for row in rows:
        rec = {}
        for col in KEEP:
            val = row.get(col, "")
            if col in ("hireability", "credibility"):
                try:
                    rec[col] = float(val) if val not in ("", None) else None
                except (ValueError, TypeError):
                    rec[col] = None
            else:
                rec[col] = str(val).strip() if val not in ("", None) else ""
        records.append(rec)
    return records


# ── INJECT INTO TEMPLATE ──────────────────────────────────────────────────────

JOBS_PATTERN = re.compile(
    r"(const JOBS\s*=\s*)(\[.*?\]);",
    re.DOTALL
)

def inject_jobs(template_html: str, jobs: list[dict]) -> str:
    jobs_json = json.dumps(jobs, ensure_ascii=False, indent=None)
    active_jobs = [j for j in jobs if str(j.get("status", "")).strip().lower() != "invalid"]

    # Update the generated-at comment in the header if present
    today = date.today().isoformat()
    html = re.sub(
        r'(<!-- generated:)[^>]*(-->)',
        f'<!-- generated: {today} | {len(active_jobs)} active / {len(jobs)} total listings -->',
        template_html
    )

    # Replace the JOBS array
    if JOBS_PATTERN.search(html):
        html = JOBS_PATTERN.sub(
            lambda m: f"{m.group(1)}{jobs_json};",
            html,
            count=1
        )
    else:
        # Fallback: insert before </script>
        html = html.replace(
            "</script>",
            f"const JOBS = {jobs_json};\n</script>",
            1
        )

    # Update the totalCount stat shown in the header
    html = re.sub(
        r'(<strong id="totalCount"[^>]*>)\d*(</strong>)',
        rf'\g<1>{len(active_jobs)}\2',
        html
    )
    return html


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    # 1. Load jobs
    if args.source == "sheets":
        print("Loading from Google Sheets via storage.py …")
        jobs = load_from_sheets()
    else:
        excel_path = args.excel
        if not os.path.exists(excel_path):
            sys.exit(f"Excel file not found: {excel_path}")
        print(f"Loading from Excel: {excel_path} …")
        jobs = load_from_excel(excel_path)

    print(f"  → {len(jobs)} listings loaded.")

    # 2. Read template
    template_path = args.template
    if not os.path.exists(template_path):
        sys.exit(f"Template HTML not found: {template_path}\n"
                 f"Pass the original HTML as --template path/to/walkin_jobs_bangalore.html")

    with open(template_path, "r", encoding="utf-8") as f:
        template_html = f.read()

    # 3. Inject
    output_html = inject_jobs(template_html, jobs)

    # 4. Write
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(output_html)

    print(f"  → Written to: {args.out}")
    print("Done ✓")


if __name__ == "__main__":
    main()
