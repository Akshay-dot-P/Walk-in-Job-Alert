        return extract_text(str(path)) or ""
    except Exception as exc:
        logger.debug("PDF text extraction failed for %s: %s", path.name, exc)
        return ""


def _extract_pdf_text_from_bytes(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        return _extract_pdf_text_from_file(Path(tmp.name))


def _extract_docx_text_from_file(path: Path) -> str:
    try:
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.debug("DOCX text extraction failed for %s: %s", path.name, exc)
        return ""


def _extract_docx_text_from_bytes(data: bytes) -> str:
    try:
        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as exc:
        logger.debug("DOCX text extraction failed from bytes: %s", exc)
        return ""


def _role_research_terms(job: dict, jd_keywords: dict) -> list[str]:
    domain = str(job.get("domain", "General")).strip() or "General"
    raw_terms = []
    raw_terms.extend(_DOMAIN_RESEARCH_TERMS.get(domain, _DOMAIN_RESEARCH_TERMS["General"]))
    raw_terms.extend(jd_keywords.get("ranked", [])[:10])
    raw_terms.extend(jd_keywords.get("tools", [])[:6])
    raw_terms.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}", str(job.get("job_title", ""))))
    raw_terms.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9+#.-]{2,}", str(job.get("skills", "")))[:12])

    seen, terms = set(), []
    for term in raw_terms:
        term = re.sub(r"\s+", " ", str(term).lower()).strip(" -_/|")
        if len(term) < 3 or term in _RESEARCH_STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms[:28]


def _source_relevance_score(text: str, terms: list[str]) -> int:
    lower = (text or "").lower()
    score = 0
    for term in terms:
        if not term:
            continue
        weight = 3 if " " in term else 1
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lower):
            score += weight
    return score


def _unwrap_duckduckgo_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _normalize_public_doc_url(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    netloc = parsed.netloc.lower()
    path = parsed.path

    if "linkedin.com" in netloc and not RESUME_RESEARCH_ALLOW_LINKEDIN:
        return None

    if netloc == "github.com" and "/blob/" in path:
        parts = path.strip("/").split("/")
        if len(parts) >= 5:
            owner, repo, _, branch = parts[:4]
            rest = "/".join(parts[4:])
            return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{rest}"

    suffix = Path(path).suffix.lower()
    if suffix in _PUBLIC_DOC_EXTS:
        return url
    if "raw.githubusercontent.com" in netloc and suffix in _PUBLIC_DOC_EXTS:
        return url
    return None


def _discover_public_resume_urls(job: dict, terms: list[str]) -> list[str]:
    if not WEB_RESUME_RESEARCH or RESUME_RESEARCH_MAX_WEB <= 0:
        return []

    query_terms = " ".join(terms[:6])
    query = (
        f'{job.get("job_title", "")} {query_terms} resume '
        "filetype:pdf OR filetype:docx github linkedin"
    )
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query[:220]},
            headers=_HDRS,
            timeout=8,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("Public resume discovery failed: %s", exc)
        return []

    urls = []
    soup = BeautifulSoup(resp.text, "html.parser")
    for a in soup.select("a.result__a"):
        norm = _normalize_public_doc_url(_unwrap_duckduckgo_url(a.get("href", "")))
        if norm and norm not in urls:
            urls.append(norm)
        if len(urls) >= RESUME_RESEARCH_MAX_WEB:
            break
    return urls


def _fetch_public_document_text(url: str) -> str:
    try:
        resp = requests.get(url, headers=_HDRS, timeout=12, allow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("Public resume fetch failed for %s: %s", url, exc)
        return ""

    if len(resp.content) > _MAX_PUBLIC_DOC_BYTES:
        logger.debug("Skipping oversized public resume: %s", url)
        return ""

    suffix = Path(urlparse(resp.url).path).suffix.lower()
    ctype = resp.headers.get("content-type", "").lower()
    data = resp.content
    if suffix == ".pdf" or "application/pdf" in ctype:
        text = _extract_pdf_text_from_bytes(data)
    elif suffix in {".docx", ".doc"} or "wordprocessingml" in ctype:
        text = _extract_docx_text_from_bytes(data)
    elif "text/html" in ctype:
        text = _visible_html_text(resp.text)
    else:
        text = ""
    return _scrub_external_text(text)


def _load_public_resume_sources(job: dict, terms: list[str]) -> list[dict]:
    urls = []
    for url in RESUME_SOURCE_URLS:
        norm = _normalize_public_doc_url(url)
        if norm and norm not in urls:
            urls.append(norm)
    for url in _discover_public_resume_urls(job, terms):
        if url not in urls:
            urls.append(url)

    sources = []
    for url in urls[:RESUME_RESEARCH_MAX_WEB]:
        text = _fetch_public_document_text(url)
        if len(text) < 150:
            continue
        sources.append({
            "kind": "public_resume",
            "source": urlparse(url).netloc,
            "text": text,
            "score": _source_relevance_score(text, terms),
        })
    sources.sort(key=lambda item: item.get("score", 0), reverse=True)
    return sources[:RESUME_RESEARCH_MAX_WEB]


def _fetch_reddit_forum_posts(job: dict, terms: list[str]) -> list[dict]:
    if not FORUM_RESEARCH or FORUM_RESEARCH_MAX_POSTS <= 0:
        return []

    subs = REDDIT_RESEARCH_SUBS or ["cybersecurityindia", "cybersecurity", "AskNetsec"]
    per_sub = max(1, min(3, (FORUM_RESEARCH_MAX_POSTS + len(subs) - 1) // len(subs)))
    q_terms = " ".join([str(job.get("job_title", "")), str(job.get("domain", "")), *terms[:4]])
    query = f"{q_terms} resume job career India".strip()

    posts = []
    for sub in subs:
        if len(posts) >= FORUM_RESEARCH_MAX_POSTS:
            break
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                params={"q": query[:180], "restrict_sr": "1", "sort": "relevance", "limit": str(per_sub)},
                headers=_HDRS,
                timeout=8,
            )
            if resp.status_code in (403, 429):
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("Reddit research failed for r/%s: %s", sub, exc)
            continue
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            title = post.get("title", "")
            body = post.get("selftext", "")
            text = _scrub_external_text(f"{title}. {body}", max_chars=1200)
            if len(text) < 40:
                continue
            posts.append({
                "kind": "forum",
                "source": f"r/{sub}",
                "text": text,
                "score": _source_relevance_score(text, terms),
            })
            if len(posts) >= FORUM_RESEARCH_MAX_POSTS:
                break
    posts.sort(key=lambda item: item.get("score", 0), reverse=True)
    return posts[:FORUM_RESEARCH_MAX_POSTS]


def _extract_resume_bullet_lines(texts: list[str], terms: list[str]) -> list[str]:
    bullets = []
    term_blob = "|".join(re.escape(t) for t in terms[:18] if len(t) > 2)
    term_re = re.compile(term_blob, re.IGNORECASE) if term_blob else None

    for text in texts:
        for raw in re.split(r"[\n\r]+|(?<=\.)\s+(?=[A-Z][a-z]+(?:ed|d|ing)\b)", text):
            line = raw.strip(" \t-*•·")
            if not (45 <= len(line) <= 280):
                continue
            first = re.match(r"([A-Za-z]+)", line.lower())
            starts_action = bool(first and first.group(1) in _RESUME_ACTION_VERBS)
            has_term = bool(term_re and term_re.search(line))
            if starts_action or has_term:
                bullets.append(line)
            if len(bullets) >= 80:
                return bullets
    return bullets
