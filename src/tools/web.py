import httpx
import os
import re
import asyncio
import threading
from bs4 import BeautifulSoup
from agent_framework import tool
from tools.core import with_quota
from tools.fs import _get_safe_path, _get_workspace_type, _IN_MEMORY_FS
from utils.run_state import record_fetched_url
from utils.browser_fetch import fetch_via_headless_browser


def _run_with_daemon_timeout(func, timeout: float):
    """Run a blocking `func()` with a real timeout that ALSO can't block process exit — unlike a
    plain `asyncio.wait_for(asyncio.to_thread(func), timeout=...)`. That combination does unblock
    the awaiting coroutine on time, but `asyncio.to_thread`'s underlying executor thread is not a
    daemon thread, so if `func()` never actually returns (confirmed live 2026-07-13/14: ddgs's
    underlying `primp` Rust HTTP client can block below anything asyncio can interrupt — see
    HKUDS/nanobot#2804, microsoft/amplifier#219), that orphaned thread lingers and Python's
    interpreter-shutdown thread-join then hangs the WHOLE process at exit, even though the
    original tool call itself already returned an honest timeout error long before. Verified
    directly: a `time.sleep(999)`-hung call wrapped only in wait_for(to_thread(...)) times out the
    caller correctly but the process never exits; the same call run through THIS helper both times
    out the caller and lets the process exit cleanly, since the hung inner thread is daemonized.

    Must be called from inside `asyncio.to_thread` (not directly on the event loop thread) — the
    `Thread.join(timeout)` below is itself a blocking call bounded by `timeout`, so the outer
    to_thread call returns promptly either way; it must not run on the loop thread, or it would
    block all other concurrent tasks for up to `timeout` seconds."""
    box = {}

    def _wrapper():
        try:
            box['value'] = func()
        except Exception as e:
            box['error'] = e

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"operation timed out after {timeout}s with no response")
    if 'error' in box:
        raise box['error']
    return box.get('value')


def _looks_like_redirect_stub(md_content: str) -> str | None:
    """Detect a client-side ("click here to be redirected") landing page that httpx's real HTTP
    redirect-following can't see, since it's not an HTTP 3xx — just rendered page content with one
    link. Found live: auto-fetching Rust's own 'latest release' URL returned exactly this ("Redirect
    \\n\\n [Click here](/2026/07/09/Rust-1.97.0/) to be redirected...") with the actual answer sitting
    unused in the link target. Returns the redirect target URL if this pattern matches, else None."""
    if not md_content or len(md_content) > 300:
        return None
    links = re.findall(r'\[[^\]]*\]\(([^)]+)\)', md_content)
    if len(links) == 1 and re.search(r'redirect', md_content, re.IGNORECASE):
        return links[0]
    return None


def _meta_declared_encoding(content: bytes) -> str | None:
    """Charset declared inside the document's own head (<meta charset=...> or the http-equiv
    Content-Type form) — scanned in the first 2KB, where the HTML spec requires it to live."""
    m = re.search(rb'<meta[^>]{0,200}charset\s*=\s*["\']?\s*([\w.-]{2,20})', content[:2048], re.IGNORECASE)
    return m.group(1).decode("ascii", errors="replace") if m else None


def _decode_html_bytes(content: bytes, header_encoding: str | None) -> str:
    """Decode fetched HTML to text honoring the page's real encoding — C8 fix. Run 14's DIAN
    law text (the flagship 750KB source) was saved full of mojibake (�), so 'Resolución'/'número'
    could never string-match and every accent-bearing Spanish term silently dropped out of the
    scope/term/regulation checks. Root cause: raw bytes went to BeautifulSoup/markitdown with the
    HTTP Content-Type charset discarded, leaving them to guess.

    Order: strict UTF-8 first (it self-validates — genuine Latin-1 accents make it fail, so a
    wrong/absent header can't corrupt a UTF-8 page), then the HTTP header charset, then the
    meta-declared charset, then cp1252 with replacement (a superset of Latin-1 that never fails
    and keeps Spanish smart quotes)."""
    for enc in ("utf-8", header_encoding, _meta_declared_encoding(content)):
        if not enc:
            continue
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("cp1252", errors="replace")


def _extract_html_metadata(soup: BeautifulSoup) -> dict:
    """Best-effort title/author/publish-date from a page's own <head> — never guessed, only what
    the page itself declares. Must run BEFORE _strip_boilerplate_html's tag-stripping, since that's
    the one point in the fetch pipeline where these tags are still in hand; markdown/text
    conversion afterward discards them entirely (attribute values never appear in get_text()
    output). A field the page doesn't declare is simply omitted from the returned dict, not
    fabricated — matches this project's zero-fabrication ethos for everything else it saves."""
    meta = {}

    def _meta_content(*names_or_props):
        for n in names_or_props:
            tag = soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"property": n})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return None

    if soup.title and soup.title.string and soup.title.string.strip():
        meta["title"] = soup.title.string.strip()
    else:
        og_title = _meta_content("og:title")
        if og_title:
            meta["title"] = og_title

    author = _meta_content("author", "article:author")
    if author:
        meta["author"] = author

    published = _meta_content("article:published_time", "date", "publish-date", "og:updated_time")
    if published:
        meta["published"] = published

    return meta


def _strip_boilerplate_html(html_text: str) -> tuple[bytes, dict]:
    """Remove common non-article chrome (nav, footer, script, style, ads, cookie banners) before
    markdown conversion, so a fetched page doesn't burn an Analyzer's context budget on chrome
    that was never going to contain a real finding. Applied to BOTH the markitdown path and the
    BeautifulSoup fallback below — previously only the fallback path stripped anything, so the
    primary (markitdown) path passed raw nav/footer/script content straight through untouched.

    Takes already-decoded text (see _decode_html_bytes) and returns (UTF-8 bytes, metadata dict)
    — any original charset-declaring meta tags are replaced by a UTF-8 one in the bytes (otherwise
    a stale '<meta charset=iso-8859-1>' inside now-UTF-8 bytes makes markitdown re-mojibake the
    exact content C8 fixed). The metadata dict is title/author/published extracted from the same
    parsed soup before boilerplate stripping — see _extract_html_metadata."""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        metadata = _extract_html_metadata(soup)
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg"]):
            tag.extract()
        boilerplate = re.compile(r'cookie|consent|advert|sidebar|popup|newsletter|subscribe-banner|site-header|site-footer', re.IGNORECASE)
        # Size-guarded: a real cookie banner/ad slot/popup is always short chrome (confirmed
        # live 2026-07-14: under a few hundred chars on every real example seen). This regex is a
        # SUBSTRING match, so it also matches unrelated layout classes that happen to contain one
        # of these words as part of a compound token — e.g. Springer's own article-body wrapper is
        # classed `eds-l-with-sidebar` (a CSS grid layout hint, "content area next to a sidebar"),
        # which matched "sidebar" and silently deleted the entire 220K-char article body, leaving
        # only the cookie-consent banner behind — a real paper then looked exactly like a stub.
        # Any match this large is far more likely to be a mislabeled content wrapper than genuine
        # chrome, so it's left alone rather than extracted.
        _BOILERPLATE_MAX_CHARS = 3000
        for tag in soup.find_all(attrs={"class": boilerplate}):
            if len(tag.get_text(strip=True)) <= _BOILERPLATE_MAX_CHARS:
                tag.extract()
        for tag in soup.find_all(attrs={"id": boilerplate}):
            if len(tag.get_text(strip=True)) <= _BOILERPLATE_MAX_CHARS:
                tag.extract()
        for tag in soup.find_all("meta"):
            if tag.get("charset") or (tag.get("http-equiv") or "").lower() == "content-type":
                tag.extract()
        return b'<meta charset="utf-8">' + soup.encode("utf-8"), metadata
    except Exception:
        return html_text.encode("utf-8", errors="replace"), {}


# Hard cap on RAW download size, independent of _save_fetched's separate 5MB SAVED-content cap
# (web.py:266,270) — that cap only applies AFTER a PDF/HTML has already been fully downloaded and
# parsed, so a multi-GB URL (a mislabeled video, a huge dataset dump) would previously OOM the run
# well before ever reaching it. Set comfortably above the save cap since PDF/HTML parsing wants
# the whole document, not a truncated one, to produce coherent markdown. Second full audit,
# 2026-07-12, item 3.
_MAX_FETCH_BYTES = 25_000_000


def _fetch_raw(url: str, convert_to_md: bool = True, _redirect_depth: int = 0):
    """Blocking fetch + parse (PDF/HTML -> Markdown, or raw bytes). Shared by
    fetch_url_to_workspace and web_search's auto-fetch — pulled out of the former so both paths
    use identical fetch/parse logic instead of drifting.

    Returns (data, urls_fetched, metadata) where urls_fetched is every URL actually retrieved
    this call, original first — more than one entry only when a client-side redirect stub was
    followed (see _looks_like_redirect_stub). The caller must record ALL of them as fetched, since
    the model may reasonably cite either the URL it searched (the stub) or the one the content is
    actually from. metadata is a best-effort dict of title/author/published extracted from the
    page's own <head> (HTML only — PDF/raw-bytes paths always return {}, see
    _extract_html_metadata), empty when nothing was declared or extraction failed.

    Streams the body instead of buffering it whole via httpx.get, so a URL over _MAX_FETCH_BYTES
    is caught (via Content-Length when the server sends one, or by aborting mid-stream when it
    doesn't) before the full body is ever held in memory.

    HTML path only: if the plain fetch looks like a bot-wall stub (see _stub_reason), retries once
    via a headless browser (settings.fetch.headless_fallback, default on; no-op if Playwright isn't
    installed) before giving up — see utils.browser_fetch.fetch_via_headless_browser.
    """
    # A 2021-vintage Chrome UA (91.x) here previously triggered publisher-side "your browser is
    # outdated" version-sniffing blocks (confirmed live 2026-07-14 against ScienceDirect, which
    # returns an HTTP 400 block page for it) even though the request itself was otherwise fine —
    # bumped to a current build.
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
    with httpx.stream("GET", url, headers=headers, timeout=30, follow_redirects=True) as resp:
        content_length = resp.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > _MAX_FETCH_BYTES:
            return (f"[ERROR: {url} reports Content-Length {int(content_length):,} bytes, over "
                    f"the {_MAX_FETCH_BYTES:,} byte fetch cap. Skipped without downloading.]"), [url], {}
        chunks, total = [], 0
        for piece in resp.iter_bytes():
            chunks.append(piece)
            total += len(piece)
            if total > _MAX_FETCH_BYTES:
                return (f"[ERROR: {url} exceeded the {_MAX_FETCH_BYTES:,} byte fetch cap while "
                        f"downloading (no Content-Length header caught it early). Skipped.]"), [url], {}
        content = b"".join(chunks)
        resp_url = resp.url
        charset_encoding = resp.charset_encoding

    if not convert_to_md:
        return content, [url], {}  # Raw bytes

    # Check actual bytes — a URL might say .pdf but serve HTML (JS-gated doc viewers). Sniffs the
    # first 1KB rather than requiring the magic bytes at offset 0 (fresh audit, 2026-07-12): the
    # old `content[:4] == b"%PDF"` missed a real PDF preceded by a BOM or a couple of stray bytes
    # some servers prepend, misrouting it to the HTML/BeautifulSoup path and producing garbage —
    # the previous `"application/pdf" in content_type and is_actual_pdf` branch was also dead code
    # (a strict subset of is_actual_pdf alone, content_type was never actually consulted).
    is_pdf = b"%PDF-" in content[:1024]

    if is_pdf:
        # Save to temp file, then parse locally
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            # Try liteparse first (better spatial accuracy for PDFs)
            import shutil
            if shutil.which("liteparse"):
                import subprocess
                result = subprocess.run(
                    ["liteparse", tmp_path],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout, [url], {}

            # Fallback to markitdown on local file
            try:
                from utils.parsers import convert_to_markdown
                md_content = convert_to_markdown(tmp_path)
                if md_content:
                    return md_content, [url], {}
            except ImportError:
                pass

            return f"[ERROR: PDF at {url} could not be parsed. Size: {len(content)} bytes. Try a different source.]", [url], {}
        finally:
            os.unlink(tmp_path)
    else:
        # HTML path: try markitdown on local temp file first, then BeautifulSoup fallback.
        # Boilerplate (nav/footer/script/ads/cookie banners) is stripped from the HTML BEFORE
        # either path runs, not after — see _strip_boilerplate_html. Bytes are decoded honoring
        # the page's real charset first (C8 — see _decode_html_bytes), so cleaned_html is always
        # well-formed UTF-8 whatever the server sent.
        cleaned_html, metadata = _strip_boilerplate_html(_decode_html_bytes(content, charset_encoding))
        md_content = None
        try:
            from utils.parsers import convert_to_markdown
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as tmp:
                tmp.write(cleaned_html)
                tmp_path = tmp.name
            try:
                md_content = convert_to_markdown(tmp_path)
            finally:
                os.unlink(tmp_path)
        except ImportError:
            pass

        if not md_content:
            # BeautifulSoup fallback for HTML
            soup = BeautifulSoup(cleaned_html, "html.parser")
            md_content = '\n'.join(line for line in (l.strip() for l in soup.get_text(separator='\n').splitlines()) if line)

        # Headless-browser retry for pages that bot-walled the plain httpx GET (Akamai/Cloudflare
        # JS challenges, browser-version-sniffing blocks — confirmed live 2026-07-14 against
        # Springer/ScienceDirect/MDPI, see ROADMAP.md). Only engages when the plain fetch already
        # looks like a stub, so it never adds latency to the common case. Re-runs the SAME
        # boilerplate-strip + markitdown/BeautifulSoup pipeline on the browser-rendered HTML rather
        # than duplicating parsing logic; only replaces md_content/metadata if the retry actually
        # produced real content, so a failed/unavailable/still-blocked retry leaves the original
        # stub result (and its accurate stub flag) untouched.
        if _stub_reason(md_content):
            import config as app_config
            headless_enabled = app_config.cfg.get("settings", {}).get("fetch", {}).get("headless_fallback", True)
            if headless_enabled:
                rendered_html = fetch_via_headless_browser(str(resp_url))
                if rendered_html:
                    retry_cleaned_html, retry_metadata = _strip_boilerplate_html(rendered_html)
                    retry_md_content = None
                    try:
                        from utils.parsers import convert_to_markdown
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as tmp:
                            tmp.write(retry_cleaned_html)
                            tmp_path = tmp.name
                        try:
                            retry_md_content = convert_to_markdown(tmp_path)
                        finally:
                            os.unlink(tmp_path)
                    except ImportError:
                        pass
                    if not retry_md_content:
                        retry_soup = BeautifulSoup(retry_cleaned_html, "html.parser")
                        retry_md_content = '\n'.join(
                            line for line in (l.strip() for l in retry_soup.get_text(separator='\n').splitlines()) if line
                        )
                    if not _stub_reason(retry_md_content):
                        md_content, metadata = retry_md_content, retry_metadata

        # Follow a client-side redirect stub one hop (real HTTP redirects are already handled by
        # follow_redirects=True above — this catches the ones that aren't, see
        # _looks_like_redirect_stub's docstring for the live case that motivated this).
        if _redirect_depth < 1:
            target = _looks_like_redirect_stub(md_content)
            if target:
                from urllib.parse import urljoin
                resolved = urljoin(str(resp_url), target)
                try:
                    inner_data, inner_urls, inner_meta = _fetch_raw(resolved, convert_to_md, _redirect_depth=_redirect_depth + 1)
                    return inner_data, [url] + inner_urls, inner_meta
                except Exception:
                    pass  # Fall through and return the stub rather than losing the fetch entirely.

        return md_content, [url], metadata


# Phrase-level soft-404/paywall markers (EN + ES — the flagship benchmark language), not single
# loaded words in isolation: "404" alone appears in real articles about errors, so it's only
# matched inside its not-found phrasings. Markers only ever fire together with the low-prose-mass
# condition in _stub_reason below, so a real article that merely SAYS "subscribe" is never flagged.
_STUB_MARKERS_RE = re.compile(
    r'page not found|p[aá]gina no encontrada|error 404|404 not found|no longer available|'
    r'suscr[ií]b|suscripci[oó]n|subscri(?:be|ption|ber)|sign in to continue|to continue reading|'
    r'inicia sesi[oó]n|reg[ií]strate|contenido exclusivo|paywall',
    re.IGNORECASE)


def _stub_reason(md_content: str) -> str | None:
    """Detect a soft-404/paywall stub: a fetch that returned HTTP 200 but no real article
    content. Confirmed live (run 14, 2026-07-12): a model-INVENTED El Tiempo URL answered 200
    with ~5KB of subscription chrome, got recorded as a real fetch, and the hard grounding gate
    passed — an invented citation wearing a 'successful' fetch. Returns a short reason string
    when the page looks like a stub, else None.

    Prose mass = words in non-heading lines of >=15 words (URLs and markdown link syntax
    stripped first): real articles are made of long paragraph lines, chrome is short
    link/button lines. Heading lines never count — verified against run 14's live URL, whose
    soft-404 is a "recommended headlines" shell whose only long lines are #### headline links
    (183 words of them, all chrome). A page with real prose (>=150 words of it) is never a
    stub no matter what markers it contains; below that, a not-found/paywall marker — or
    near-zero prose at all — flags it."""
    if not md_content or not md_content.strip():
        return "empty page"
    text = re.sub(r'https?://\S+', ' ', md_content)
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', ' ', text)      # images
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)   # links -> their label text
    prose_words = 0
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        words = line.split()
        if len(words) >= 15:
            prose_words += len(words)
    if prose_words >= 150:
        return None
    m = _STUB_MARKERS_RE.search(text)
    if m:
        return (f"paywall/not-found marker {m.group(0)!r} with almost no article text "
                f"({prose_words} prose words)")
    # No marker: flag only NEAR-ZERO prose (run 14's live shell scores 0). Threshold kept low
    # (10, not e.g. 20) on purpose — a tiny but legitimate page (example.com is ~15 prose
    # words) must not lose its citability without a paywall/not-found marker as evidence.
    if prose_words < 10:
        return f"no substantive text ({prose_words} prose words after boilerplate strip)"
    return None


def _fetched_filename(filename: str, convert_to_md: bool = True) -> str:
    """Canonical workspace path for fetched content: everything goes under sources/ so the run
    root stays readable (final_report.md, findings.md, _todos.md, _run_state.json only) —
    previously ~20 fetched-page dumps buried the two files a human actually reads."""
    if convert_to_md and not filename.endswith('.md'):
        filename += '.md'
    if not filename.startswith("sources/"):
        filename = "sources/" + filename
    return filename


def _save_fetched(urls_fetched: list[str], filename: str, data, convert_to_md: bool = True,
                   metadata: dict | None = None) -> str:
    """Persist already-fetched content to the workspace and record ALL of urls_fetched (original
    plus any redirect target actually followed) as real fetches. Shared by fetch_url_to_workspace
    and web_search's auto-fetch.

    metadata (title/author/published, best-effort from the page's own <head> — see
    _extract_html_metadata) is written as extra header lines alongside Source-URL, only for fields
    actually present, so downstream Analyzers don't need to delegate a whole sub-agent call just to
    re-derive title/authors a file's own header already answers. Confirmed live 2026-07-12: this
    exact "Extract title/authors/abstract from [paper]" delegation pattern recurred identically
    across multiple benchmark runs, burning a full LLM sub-agent turn each time for what the
    fetched page's own metadata already declared."""
    filename = _fetched_filename(filename, convert_to_md)

    # Stub detection runs on the raw converted markdown BEFORE the Source-URL header is
    # prepended — the header's URL slug must never count as page content (see _stub_reason).
    stub = _stub_reason(data) if (convert_to_md and isinstance(data, str)) else None
    stub_warning = ""
    if stub:
        stub_warning = (
            f"\nWARNING: this page looks like a stub/soft-404 ({stub}) — a paywall or "
            f"not-found shell with no real article content. Do NOT cite this URL as a source; "
            f"find a different source for whatever you hoped this page contained."
        )

    # The file's true URL travels INSIDE the file. Root-cause fix for a confirmed live failure
    # (qwen3.6, 2026-07-11): Analyzers reading slugified filenames had no way to know a file's
    # real URL, so they reconstructed plausible-looking fake ones from the filename — 0 of 22
    # cited URLs in that run's findings.md were real.
    if convert_to_md and isinstance(data, str):
        header_lines = ["Source-URL: " + " | ".join(urls_fetched)]
        if metadata:
            if metadata.get("title"):
                header_lines.append(f"Title: {metadata['title']}")
            if metadata.get("author"):
                header_lines.append(f"Authors: {metadata['author']}")
            if metadata.get("published"):
                header_lines.append(f"Published: {metadata['published']}")
        data = "\n".join(header_lines) + "\n\n" + data

    path = _get_safe_path(filename)
    if not path:
        return f"Error: Invalid filename '{filename}'."

    if isinstance(data, str):
        chunk = data[:5000000]  # Allow larger sizes for markdown text (up to 5MB)
        mode = "w"
        encoding = "utf-8"
    else:
        chunk = data[:5000000]  # Cap raw binary at 5MB
        mode = "wb"
        encoding = None

    if _get_workspace_type() == "disk":
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        if encoding:
            with open(path, mode, encoding=encoding) as f:
                f.write(chunk)
        else:
            with open(path, mode) as f:
                f.write(chunk)
        # Deterministic, engine-level record of every URL actually involved in producing this
        # content — this is what the real grounding check cross-references, independent of what
        # the model later claims.
        for u in urls_fetched:
            record_fetched_url(u, filename, stub=stub)
        return f"Fetched URL successfully to '{filename}' on disk.{stub_warning}"
    else:
        _IN_MEMORY_FS[path] = chunk
        for u in urls_fetched:
            record_fetched_url(u, filename, stub=stub)
        return f"Fetched URL successfully to '{filename}' in memory.{stub_warning}"


def probe_search_health(retry_delay: float = 3.0) -> str | None:
    """One trivial search before a headless run starts — a throttled or blocked search layer
    turns a 20-minute unattended run into garbage that looks like model failure (confirmed live
    2026-07-11: 13/30 searches failed in one end-of-day run). Returns None when healthy, else a
    short error description so the caller can abort in seconds instead. Deliberately lean: no
    tool quota, no workspace writes, one retry."""
    from ddgs import DDGS
    import time
    last = None
    for attempt in (1, 2):
        try:
            if DDGS().text("wikipedia", max_results=1):
                return None
            last = "search returned zero results (provider throttling usually presents this way)"
        except Exception as e:
            last = str(e)
        if attempt == 1:
            time.sleep(retry_delay)
    return last


def _scope_warning(query: str) -> str:
    """Soft warning when a search query drops the delegated task's own scope entity (e.g. a
    Colombia-scoped task searching "predictive maintenance offshore wind turbine" — confirmed
    live 2026-07-11, an entire quota's worth of off-continent results). Same philosophy as the
    post-fetch verify_scope_relevance check: warn, never block — the model decides."""
    import config as app_config
    if not app_config.cfg.get("settings", {}).get("grounding_check", {}).get("verify_scope_relevance", True):
        return ""
    from utils.run_state import scope_entities_ctx
    entities = scope_entities_ctx.get()
    if not entities:
        return ""
    q = query.lower()
    if any(e.lower() in q for e in entities):
        return ""
    ent_list = ", ".join(sorted(entities)[:5])
    return (
        f"\n\n[SCOPE WARNING: your task's instructions are explicitly about {ent_list}, but this "
        f"search query never mentions any of them — these results may be about the wrong "
        f"country/subject entirely. Re-search with the scope term included unless this was deliberate.]"
    )


def _first_of_list_arg(value, arg_name: str, tool_name: str):
    """Normalize a tool argument a model passed as a LIST when the schema says string. Confirmed
    live (run 17, Tongyi-DeepResearch): the model's own native tools take `"query": [array]` and
    `"url": [array]`, and under pressure it reverts to that trained schema — the framework then
    rejects the call, the model retries the identical shape, and after 3 consecutive errors the
    whole request is abandoned. Executing the FIRST element and telling the model exactly how to
    get the rest degrades gracefully instead: quotas stay honest (one call, one unit) and the
    model gets an actionable correction instead of an opaque validation error.

    Returns (first_value, note) — note is "" when the value wasn't a list."""
    if not isinstance(value, (list, tuple)):
        return value, ""
    items = [v for v in value if v]
    if not items:
        return "", ""
    note = ""
    if len(items) > 1:
        rest = ", ".join(str(v) for v in items[1:5])
        note = (
            f"\n(NOTE: you passed {len(items)} values for '{arg_name}' in one {tool_name} call, "
            f"but it accepts ONE per call — only the first was executed. Call {tool_name} again "
            f"once per remaining value: {rest})"
        )
    return str(items[0]), note


def _slugify_for_filename(url: str, query: str) -> str:
    """Deterministic, collision-resistant filename for an auto-fetched search result."""
    import hashlib
    from urllib.parse import urlparse
    host = urlparse(url).netloc.replace("www.", "")
    slug = re.sub(r'[^a-z0-9]+', '_', (host + "_" + query).lower()).strip('_')[:60]
    digest = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{slug}_{digest}"


@tool
@with_quota
async def fetch_url_to_workspace(url: str | list, filename: str = "", convert_to_md: bool = True) -> str:
    """Fetch external web content and save it directly to the workspace. If convert_to_md is True, parses to Markdown. url takes ONE URL per call. filename is optional — a name is auto-generated from the URL if you omit it."""
    url, list_note = _first_of_list_arg(url, "url", "fetch_url_to_workspace")
    # filename used to be a required argument with no default -- confirmed live 2026-07-12: the
    # model omitted it entirely in 5 separate calls across today's benchmark runs, and since a
    # missing required field is rejected by schema validation BEFORE the function body ever runs,
    # there was no way to recover inside the function -- the call was simply lost, unlike
    # _first_of_list_arg's wrong-TYPE handling just above, which DOES reach here. Making it
    # optional with an auto-derived default (reusing the same slugify helper web_search's
    # auto-fetch already uses) turns a rejected call into a working one instead.
    if not filename:
        filename = _slugify_for_filename(url, "")
    try:
        data, urls_fetched, metadata = await asyncio.to_thread(_fetch_raw, url, convert_to_md)
        return _save_fetched(urls_fetched, filename, data, convert_to_md, metadata=metadata) + list_note
    except Exception as e:
        import traceback
        return f"Failed: {e}\n\nTraceback:\n{traceback.format_exc()}"


@tool
async def web_search(
    query: str | list,
    max_results: int = 5,
    topic: str = "general",
) -> str:
    """Search the web for information on a given query. Takes ONE query per call.

    Automatically fetches the FULL page content of the top result (not just its snippet) and
    saves it to the workspace — you do not need to call fetch_url_to_workspace separately for
    that result. Remaining results are returned as snippets only; fetch those explicitly with
    fetch_url_to_workspace if you need their full content too.

    Args:
        query: Search query to execute
        max_results: Maximum number of results to return (default: 5)
        topic: Topic filter - 'general', 'news', or 'finance' (default: 'general')

    Returns:
        Formatted search results with titles, URLs, and snippets. The top result also includes
        the workspace filename its full content was auto-saved to.
    """
    query, list_note = _first_of_list_arg(query, "query", "web_search")

    from tools.core import check_quota
    quota_error = check_quota("web_search")
    if quota_error:
        return quota_error

    import config as app_config
    search_mode = app_config.cfg.get("settings", {}).get("search_mode", "light")
    if search_mode == "heavy":
        # Test-time search scaling (inspired by Tongyi DeepResearch's "Heavy Mode"): search deeper
        # and auto-fetch more of the top results per call, rather than fabricating fake
        # query-variant strings via heuristics — that would likely just return near-duplicate
        # results under a different label. A larger verified data pool per call is the real goal,
        # achieved deterministically instead of with a fragile string-rewriting trick.
        max_results = max(max_results, 8)

    def _do_search():
        from ddgs import DDGS

        def _sanitize_snippet(text: str) -> str:
            """Strip CSS, SVG, and HTML artifacts from search snippets."""
            text = re.sub(r'<svg[\s\S]*?</svg>', '', text, flags=re.IGNORECASE)
            text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r"(?:[\w-]+=(?:'[^']*'|\"[^\"]*\")[\s]*){3,}", '', text)
            text = re.sub(r'%3[CEce][^%\s]{10,}', '', text)
            return re.sub(r'\s+', ' ', text).strip()

        results = []

        # ddgs (free, no API key required) is the only provider — the old search_provider config
        # key had a dead "tavily" branch that silently returned zero results and recorded every
        # search as a health failure. A fresh, short-lived client per call rather than a shared
        # singleton — concurrent specialists search in parallel via asyncio.gather (see
        # engine/orchestrator.py's delegate_tasks), and the duckduckgo_search/ddgs library is not
        # documented as safe for concurrent calls on one shared client instance. DDGS() itself is
        # lightweight to construct.
        client = DDGS()
        # ddgs 9.x is a metasearcher over 10+ engines (google, bing, brave, yahoo, startpage,
        # mojeek, ...) — "auto" rotates/falls back across them instead of pinning every call
        # to DuckDuckGo. Confirmed live (2026-07-11): DDG throttling after a search-heavy day
        # made two models' runs fail in ways that looked like model fabrication. A comma list
        # (e.g. "google,brave,duckduckgo") pins specific engines in order.
        backend = app_config.cfg.get("settings", {}).get("search_backend", "auto")

        if topic == "news":
            search_results = client.news(query, max_results=max_results, backend=backend)
            for result in search_results:
                results.append({
                    "url": result.get("url", ""),
                    "title": result.get("title", ""),
                    "snippet": _sanitize_snippet(result.get("body", "No snippet available")),
                })
        else:
            search_results = client.text(query, max_results=max_results, backend=backend)
            for result in search_results:
                results.append({
                    "url": result.get("href", ""),
                    "title": result.get("title", ""),
                    "snippet": _sanitize_snippet(result.get("body", "No snippet available")),
                })

        return results

    from utils.run_state import record_search_health
    from tools.core import refund_quota
    # Outer timeout, not just a retry — confirmed live 2026-07-13 (a full run stalled indefinitely
    # here) and root-caused against two real GitHub issues describing the identical failure
    # (HKUDS/nanobot#2804, microsoft/amplifier#219): ddgs's underlying `primp` Rust HTTP client can
    # block in a way a plain `asyncio.to_thread` background thread never returns from. Uses
    # _run_with_daemon_timeout (not a bare asyncio.wait_for around asyncio.to_thread) specifically
    # because a plain wait_for only unblocks the CALLING coroutine on time — the orphaned executor
    # thread itself is not a daemon thread, so it silently blocks the whole process from exiting
    # cleanly later (confirmed directly: a hung call wrapped in bare wait_for/to_thread times out
    # the caller fine but then Python's interpreter-shutdown thread-join hangs forever). Full
    # process-based isolation (spawn+kill a subprocess) was considered instead and rejected: it
    # would require calling ddgs from a module-level, picklable worker function, which breaks this
    # exact call site's existing in-process `ddgs.DDGS` monkeypatch test
    # (test_structural_checks.py) since a subprocess re-imports fresh, unpatched modules — the
    # daemon-thread approach solves the same user-visible symptom (including the exit-hang) without
    # that test-compatibility cost.
    timeout_s = app_config.cfg.get("settings", {}).get("web_search", {}).get("timeout_seconds", 20)
    results, err = [], None
    try:
        results = await asyncio.to_thread(_run_with_daemon_timeout, _do_search, timeout_s)
    except Exception as e:
        err = e
    if not results:
        # Throttling is transient — one short backoff retry before giving up. Confirmed live
        # (2026-07-11): 13/30 searches failed in a single end-of-day run, each burning a quota
        # unit and returning nothing.
        await asyncio.sleep(3)
        try:
            results = await asyncio.to_thread(_run_with_daemon_timeout, _do_search, timeout_s)
            err = None
        except Exception as e:
            err = e
    if err is not None:
        record_search_health(ok=False)
        # An environmental failure must not burn the model's research budget on top of failing.
        refund_quota("web_search")
        if isinstance(err, TimeoutError):
            return (f"Search failed: timed out after {timeout_s}s with no response — the search "
                    f"layer appears to be hanging, not just slow. This is an environmental issue, "
                    f"not a query problem; try a different query or search backend.")
        import traceback
        return f"Search failed: {err}\n\nTraceback:\n{''.join(traceback.format_exception(err))}"
    # Zero results counts as a failure: under provider throttling ddgs often returns empty rather
    # than raising, and an all-empty run is indistinguishable from a fabrication-prone one.
    record_search_health(ok=bool(results))
    if not results:
        refund_quota("web_search")

    # -------------------------------------------------------------
    # Auto-fetch fusion: search and fetch used to be two separate tools, which meant a model could
    # (and, in extensive live testing, reliably did) stop after search and answer from snippets
    # alone — snippets frequently don't contain the specific fact a query needs, so the model would
    # fall back to fabricating from its own training knowledge instead. This was the single
    # highest-confidence unresolved item after a full test battery (see ROADMAP.md).
    # Independently confirmed as the right fix by three separate reference sources: DelveAgent's
    # grounding requirement, llm_wiki's "full content extraction, no truncation" search design, and
    # CYC2002tommy's explicit "FULL-TEXT READING IS MANDATORY — NO ABSTRACT-ONLY SHORTCUTS" rule
    # (same principle, applied to papers instead of web pages).
    # Structural, not prompt-level: eliminate the snippet-only path entirely instead of asking the
    # model not to take it — the same category of fix as the delegate_tasks schema validation and
    # the narrated-report salvage.
    # -------------------------------------------------------------
    auto_fetch_top = app_config.cfg.get("settings", {}).get("web_search", {}).get("auto_fetch_top", 1)
    if search_mode == "heavy":
        auto_fetch_top = max(auto_fetch_top, 3)
    auto_fetch_note = ""
    for i, r in enumerate(results[:max(auto_fetch_top, 0)]):
        if not r["url"]:
            continue
        fetch_quota_error = check_quota("fetch_url_to_workspace")
        if fetch_quota_error:
            auto_fetch_note = f"\n(Auto-fetch skipped — fetch_url_to_workspace quota exhausted: {fetch_quota_error})"
            break
        filename = _slugify_for_filename(r["url"], query)
        try:
            data, urls_fetched, metadata = await asyncio.to_thread(_fetch_raw, r["url"], True)
            save_msg = await asyncio.to_thread(_save_fetched, urls_fetched, filename, data, True, metadata)
            r["auto_fetched_filename"] = _fetched_filename(filename)
            r["auto_fetch_status"] = save_msg
        except Exception as e:
            # check_quota above already consumed a fetch_url_to_workspace unit for this attempt —
            # an environmental failure here (network error, parse crash) must not also burn the
            # model's budget, same refund philosophy web_search's own failure path already
            # follows. Second full audit, 2026-07-12, item 4.
            from tools.core import refund_quota
            refund_quota("fetch_url_to_workspace")
            r["auto_fetch_status"] = f"Auto-fetch failed ({e}) — snippet only for this result."

    result_texts = []
    for r in results:
        block = f"## {r['title']}\n**URL:** {r['url']}\n**Snippet:** {r['snippet']}"
        if r.get("auto_fetched_filename"):
            block += (
                f"\n**Full content already fetched and saved to workspace file:** "
                f"`{r['auto_fetched_filename']}` — delegate its analysis directly, no need to call "
                f"fetch_url_to_workspace for this URL again."
            )
        elif r.get("auto_fetch_status"):
            block += f"\n**Note:** {r['auto_fetch_status']}"
        result_texts.append(block + "\n")

    return f"🔍 Found {len(result_texts)} result(s) for '{query}':\n\n{chr(10).join(result_texts)}{auto_fetch_note}{_scope_warning(query)}{list_note}"


async def verify_url_live(url: str, timeout: float = 5.0) -> bool:
    """Cheap live-HTTP check that a cited URL still resolves. Not an agent tool — called
    deterministically by the engine's grounding check (per CYC2002tommy's DOI-verification idea)."""
    def _check():
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
            resp = httpx.head(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code >= 400:
                # Some servers reject HEAD; retry with a lightweight GET before giving up.
                resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            return resp.status_code < 400
        except Exception:
            return False
    return await asyncio.to_thread(_check)
