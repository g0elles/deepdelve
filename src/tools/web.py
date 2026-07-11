import httpx
import os
import re
import asyncio
from bs4 import BeautifulSoup
from agent_framework import tool
from tools.core import with_quota
from tools.fs import _get_safe_path, _get_workspace_type, _get_workspace_dir, _IN_MEMORY_FS
from utils.run_state import record_fetched_url


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


def _strip_boilerplate_html(html_bytes: bytes) -> bytes:
    """Remove common non-article chrome (nav, footer, script, style, ads, cookie banners) before
    markdown conversion, so a fetched page doesn't burn an Analyzer's context budget on chrome
    that was never going to contain a real finding. Applied to BOTH the markitdown path and the
    BeautifulSoup fallback below — previously only the fallback path stripped anything, so the
    primary (markitdown) path passed raw nav/footer/script content straight through untouched."""
    try:
        soup = BeautifulSoup(html_bytes, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg"]):
            tag.extract()
        boilerplate = re.compile(r'cookie|consent|advert|sidebar|popup|newsletter|subscribe-banner|site-header|site-footer', re.IGNORECASE)
        for tag in soup.find_all(attrs={"class": boilerplate}):
            tag.extract()
        for tag in soup.find_all(attrs={"id": boilerplate}):
            tag.extract()
        return soup.encode()
    except Exception:
        return html_bytes


def _fetch_raw(url: str, convert_to_md: bool = True, _redirect_depth: int = 0):
    """Blocking fetch + parse (PDF/HTML -> Markdown, or raw bytes). Shared by
    fetch_url_to_workspace and web_search's auto-fetch — pulled out of the former so both paths
    use identical fetch/parse logic instead of drifting.

    Returns (data, urls_fetched) where urls_fetched is every URL actually retrieved this call,
    original first — more than one entry only when a client-side redirect stub was followed (see
    _looks_like_redirect_stub). The caller must record ALL of them as fetched, since the model may
    reasonably cite either the URL it searched (the stub) or the one the content is actually from.
    """
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    resp = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)

    if not convert_to_md:
        return resp.content, [url]  # Raw bytes

    content_type = resp.headers.get("content-type", "").lower()
    # Check actual bytes — a URL might say .pdf but serve HTML (JS-gated doc viewers)
    is_actual_pdf = resp.content[:4] == b"%PDF"
    is_pdf = is_actual_pdf or ("application/pdf" in content_type and is_actual_pdf)

    if is_pdf:
        # Save to temp file, then parse locally
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
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
                    return result.stdout, [url]

            # Fallback to markitdown on local file
            try:
                from utils.parsers import convert_to_markdown
                md_content = convert_to_markdown(tmp_path)
                if md_content:
                    return md_content, [url]
            except ImportError:
                pass

            return f"[ERROR: PDF at {url} could not be parsed. Size: {len(resp.content)} bytes. Try a different source.]", [url]
        finally:
            os.unlink(tmp_path)
    else:
        # HTML path: try markitdown on local temp file first, then BeautifulSoup fallback.
        # Boilerplate (nav/footer/script/ads/cookie banners) is stripped from the HTML BEFORE
        # either path runs, not after — see _strip_boilerplate_html.
        cleaned_html = _strip_boilerplate_html(resp.content)
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

        # Follow a client-side redirect stub one hop (real HTTP redirects are already handled by
        # follow_redirects=True above — this catches the ones that aren't, see
        # _looks_like_redirect_stub's docstring for the live case that motivated this).
        if _redirect_depth < 1:
            target = _looks_like_redirect_stub(md_content)
            if target:
                from urllib.parse import urljoin
                resolved = urljoin(str(resp.url), target)
                try:
                    inner_data, inner_urls = _fetch_raw(resolved, convert_to_md, _redirect_depth=_redirect_depth + 1)
                    return inner_data, [url] + inner_urls
                except Exception:
                    pass  # Fall through and return the stub rather than losing the fetch entirely.

        return md_content, [url]


def _save_fetched(urls_fetched: list[str], filename: str, data, convert_to_md: bool = True) -> str:
    """Persist already-fetched content to the workspace and record ALL of urls_fetched (original
    plus any redirect target actually followed) as real fetches. Shared by fetch_url_to_workspace
    and web_search's auto-fetch."""
    if convert_to_md and not filename.endswith('.md'):
        filename += '.md'

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
            record_fetched_url(u, filename)
        return f"Fetched URL successfully to '{filename}' on disk."
    else:
        _IN_MEMORY_FS[path] = chunk
        for u in urls_fetched:
            record_fetched_url(u, filename)
        return f"Fetched URL successfully to '{filename}' in memory."


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
async def fetch_url_to_workspace(url: str, filename: str, convert_to_md: bool = True) -> str:
    """Fetch external web content and save it directly to the workspace. If convert_to_md is True, parses to Markdown."""
    try:
        data, urls_fetched = await asyncio.to_thread(_fetch_raw, url, convert_to_md)
        return _save_fetched(urls_fetched, filename, data, convert_to_md)
    except Exception as e:
        import traceback
        return f"Failed: {e}\n\nTraceback:\n{traceback.format_exc()}"


@tool
async def web_search(
    query: str,
    max_results: int = 5,
    topic: str = "general",
) -> str:
    """Search the web for information on a given query.

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
        import config as app_config

        def _sanitize_snippet(text: str) -> str:
            """Strip CSS, SVG, and HTML artifacts from search snippets."""
            text = re.sub(r'<svg[\s\S]*?</svg>', '', text, flags=re.IGNORECASE)
            text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
            text = re.sub(r'<[^>]+>', '', text)
            text = re.sub(r"(?:[\w-]+=(?:'[^']*'|\"[^\"]*\")[\s]*){3,}", '', text)
            text = re.sub(r'%3[CEce][^%\s]{10,}', '', text)
            return re.sub(r'\s+', ' ', text).strip()

        provider = app_config.cfg.get("settings", {}).get("search_provider", "duckduckgo")
        results = []

        if provider == "duckduckgo" or provider not in ("duckduckgo", "tavily"):
            # Default/fallback: DuckDuckGo (free, no API key required). A fresh, short-lived
            # client per call rather than a shared singleton — concurrent specialists search in
            # parallel via asyncio.gather (see engine/orchestrator.py's delegate_tasks), and the
            # duckduckgo_search/ddgs library is not documented as safe for concurrent calls on one
            # shared client instance. DDGS() itself is lightweight to construct.
            from ddgs import DDGS
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
        elif provider == "tavily":
            pass  # Removed Tavily placeholder to avoid undefined get_tavily_client() error in scaffold

        return results

    from utils.run_state import record_search_health
    try:
        results = await asyncio.to_thread(_do_search)
    except Exception as e:
        import traceback
        record_search_health(ok=False)
        return f"Search failed: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
    # Zero results counts as a failure: under provider throttling ddgs often returns empty rather
    # than raising, and an all-empty run is indistinguishable from a fabrication-prone one.
    record_search_health(ok=bool(results))

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
            data, urls_fetched = await asyncio.to_thread(_fetch_raw, r["url"], True)
            save_msg = await asyncio.to_thread(_save_fetched, urls_fetched, filename, data, True)
            r["auto_fetched_filename"] = filename if filename.endswith(".md") else filename + ".md"
            r["auto_fetch_status"] = save_msg
        except Exception as e:
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

    return f"🔍 Found {len(result_texts)} result(s) for '{query}':\n\n{chr(10).join(result_texts)}{auto_fetch_note}"


async def verify_url_live(url: str, timeout: float = 5.0) -> bool:
    """Cheap live-HTTP check that a cited URL still resolves. Not an agent tool — called
    deterministically by the engine's grounding check (per CYC2002tommy's DOI-verification idea)."""
    def _check():
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
            resp = httpx.head(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code >= 400:
                # Some servers reject HEAD; retry with a lightweight GET before giving up.
                resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            return resp.status_code < 400
        except Exception:
            return False
    return await asyncio.to_thread(_check)
