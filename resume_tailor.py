def shorten_url(long_url: str) -> str:
    try:
        resp = requests.get(f"https://tinyurl.com/api-create.php?url={requests.utils.quote(long_url)}", timeout=8)
        if resp.status_code == 200 and resp.text.startswith("https://tinyurl.com"):
            return resp.text.strip()
    except Exception:
        pass
    return long_url


# ─────────────────────────────────────────────────────────────────────────────
# Sheets helpers
# ─────────────────────────────────────────────────────────────────────────────
def _get_creds() -> Credentials:
    j = os.environ.get("GOOGLE_CREDS_JSON","")
    if not j: raise EnvironmentError("GOOGLE_CREDS_JSON not set.")
    return Credentials.from_service_account_info(json.loads(j), scopes=SCOPES)


def ensure_column(ws, name: str) -> int:
    headers = ws.row_values(1)
    if name not in headers:
        idx = len(headers)+1
        ws.update_cell(1, idx, name)
        headers.append(name)
        logger.info("Added column '%s' at %d.", name, idx)
        return idx
    return headers.index(name)+1


def get_pending_jobs(ws, doc_col: int) -> list[dict]:
    rows = ws.get_all_values()
    if len(rows) < 2: return []
    headers = rows[0]
    col = {h:i for i,h in enumerate(headers)}
    def _get(row,key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""
    pending = []
    for row_num, row in enumerate(rows[1:], start=2):
        if _get(row,"status").lower() == "new" and not (row[doc_col-1].strip() if doc_col-1 < len(row) else ""):
            pending.append({
                "row_num": row_num,
                "job_title": _get(row,"job_title") or "Cybersecurity Role",
                "company":   _get(row,"company")   or "Unknown",
                "domain":    _get(row,"domain")     or "General",
                "summary":   _get(row,"summary"),
                "skills":    _get(row,"skills_required"),
            })
    return pending


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    logger.info("="*60)
    logger.info("Resume Tailor — Research Framework Edition (validation=%s)", VALIDATION_MODE)
    logger.info("="*60)

    for name, val in [("GROQ_API_KEY",GROQ_API_KEY),("GITHUB_TOKEN",GITHUB_TOKEN),("GITHUB_REPOSITORY",GITHUB_REPOSITORY)]:
        if not val: logger.error("%s not set.", name); sys.exit(1)
    if not TEMPLATE_PATH.exists():
        logger.error("resume_template.docx not found."); sys.exit(1)

    creds = _get_creds()
    gc    = gspread.authorize(creds)
    ws    = gc.open(SHEET_NAME).sheet1
    logger.info("Connected to Sheets.")

    doc_col   = ensure_column(ws, "resume_doc_link")
    pdf_col   = ensure_column(ws, "resume_pdf_link")
    val_col   = ensure_column(ws, "validation_notes")
    cov_col   = ensure_column(ws, "keyword_coverage")
    den_col   = ensure_column(ws, "keyword_density")
    sk_col    = ensure_column(ws, "total_skills_count")
    cred_col  = ensure_column(ws, "credibility")
    stuff_col = ensure_column(ws, "stuffing_suspicion")
    hire_col  = ensure_column(ws, "hireability")

    pending = get_pending_jobs(ws, doc_col)
    if not pending:
        logger.info("No New jobs with empty resume_doc_link."); sys.exit(0)

    logger.info("Found %d pending. Processing up to %d.", len(pending), MAX_JOBS_PER_RUN)
    pending = pending[:MAX_JOBS_PER_RUN]

    success = 0
    for i, job in enumerate(pending, 1):
        logger.info("-"*50)
        logger.info("[%d/%d] %s @ %s  (domain: %s)", i, len(pending),
                    job["job_title"], job["company"], job["domain"])
        try:
            # Projects + tools
            p1_key, p2_key = DOMAIN_TO_PROJECTS.get(job["domain"], ("soc_auto","vuln_scanner"))
            jd_text  = f"{job['skills']} {job['summary']} {job['job_title']}"
            p1_tools = select_tools(p1_key, jd_text)
            p2_tools = select_tools(p2_key, jd_text)
            logger.info("  Projects: %s + %s | P1 tools: %s", p1_key, p2_key, p1_tools[:3])

            # FEATURE 1: Extract keywords
            logger.info("  Extracting JD keywords...")
            jd_keywords = extract_keywords(jd_text)

            # GitHub research (strict only)
            github_notes = ""
            if VALIDATION_MODE == "strict":
                github_notes = research_github_projects(job["domain"], job["job_title"])

            # Company intel
            intel       = get_company_intel(job["company"])
            scraped_ctx = "" if intel else scrape_company(job["company"])

            # Generate content (includes Features 2, 3, 4)
            logger.info("  Generating content...")
            content = generate_content(job, p1_key, p2_key, intel, scraped_ctx,
                                       p1_tools, p2_tools, jd_keywords)

            # FEATURE 3: Track keyword usage
            track_keyword_usage(content, jd_keywords.get("ranked",[]))

            # Validate
            if VALIDATION_MODE != "lenient": time.sleep(3)
            val_result = validate_resume(content, job, github_notes, VALIDATION_MODE)
            ats_score  = val_result.get("ats_score","N/A")
            val_note   = (
                f"[{VALIDATION_MODE.upper()}] ATS:{ats_score}"
                + (f" | Missing:{val_result.get('missing_keywords','')}" if val_result.get("missing_keywords") else "")
                + (f" | Fix:{val_result.get('improvements','')}" if val_result.get("improvements") else "")
                + (f" | GitHub:{val_result.get('github_insight','')}" if val_result.get("github_insight") else "")
            )
            logger.info("  %s", val_note)

            # FEATURE 5: Metrics
            metrics = compute_metrics(content, jd_keywords, ats_score)

            # FEATURE 6: Recruiter simulation
            if VALIDATION_MODE != "lenient":
                time.sleep(2)
                rec_sim = recruiter_simulate(content, job)
            else:
                rec_sim = {"credibility":"skipped","stuffing_suspicion":"skipped","hireability":"skipped"}

            # FEATURE H: Single-page enforcement + PDF generation
            logger.info("  Generating DOCX+PDF (single-page enforcement)...")
            docx_bytes, pdf_bytes, trim_log = enforce_single_page(content, job, jd_keywords)
            if trim_log and trim_log not in ("certs-p2-ok",""):
                val_note += f" | Trimmed:{trim_log}"
            logger.info("  DOCX: %d bytes  PDF: %d bytes", len(docx_bytes), len(pdf_bytes))

            # Upload + shorten
            doc_raw, pdf_raw = upload_to_github(docx_bytes, pdf_bytes, job)
            doc_url = shorten_url(doc_raw)
            pdf_url = shorten_url(pdf_raw)
            logger.info("  Doc: %s", doc_url)
            logger.info("  PDF: %s", pdf_url)

            # Write all columns to sheet
            ws.update_cell(job["row_num"], doc_col,   doc_url)
            ws.update_cell(job["row_num"], pdf_col,   pdf_url)
            ws.update_cell(job["row_num"], val_col,   val_note)
            ws.update_cell(job["row_num"], cov_col,   metrics["keyword_coverage"])
            ws.update_cell(job["row_num"], den_col,   metrics["keyword_density"])
            ws.update_cell(job["row_num"], sk_col,    metrics["total_skills_count"])
            ws.update_cell(job["row_num"], cred_col,  str(rec_sim.get("credibility","")))
            ws.update_cell(job["row_num"], stuff_col, str(rec_sim.get("stuffing_suspicion","")))
            ws.update_cell(job["row_num"], hire_col,  str(rec_sim.get("hireability","")))
            logger.info("  ✓ Sheet updated.")

            success += 1
            time.sleep(4)

        except Exception as exc:
            logger.error("  ✗ Failed: %s", exc); continue

    logger.info("="*60)
    logger.info("Done: %d/%d succeeded.", success, len(pending))
    logger.info("="*60)


if __name__ == "__main__":
    main()
