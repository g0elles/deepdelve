"""Scholarly literature tools for AcademicSearcher: official open APIs instead of web scraping.

Sources (all free; none needs a login): Europe PMC (life-science search, abstracts, open-access full-text XML),
OpenAlex (all fields: metadata, abstracts, open-access location), Unpaywall (DOI -> legal open-access copy).
A contact email is sent in the User-Agent and as the `email`/`mailto` parameter, as each service asks of polite
clients. It is read from env DEEPDELVE_CONTACT_EMAIL or settings.scholar.email and never lives in the repo.
OpenAlex without an API key shares a small free daily budget per IP (observed 2026-10-24: $0.10/day; a free key
gives $1/day), so OpenAlex is only the fallback; set OPENALEX_API_KEY or settings.scholar.openalex_api_key.

Every saved paper says in its first body line whether the text is FULL TEXT or ABSTRACT ONLY, so a claim can
never be grounded on more than was actually received. Nothing is saved when nothing real was obtained."""
import asyncio
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx
from agent_framework import tool

from tools.core import with_quota, TOOL_ERROR_PREFIX
from tools.web import _save_fetched, _fetch_raw, _stub_reason, _specialist_fetch_over_cap, _run_with_daemon_timeout, _user_agent

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX = "https://api.openalex.org"
UNPAYWALL = "https://api.unpaywall.org/v2"
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
_PMCID_RE = re.compile(r"\bPMC\d+\b", re.I)
_MAX_TEXT = 400_000


def _setting(key: str, default=None):
    import config as app_config
    return (app_config.get_setting("scholar", {}) or {}).get(key, default)


def _email() -> str:
    return os.environ.get("DEEPDELVE_CONTACT_EMAIL") or _setting("email", "") or ""


def _http_get(url: str, params: dict | None = None, as_json: bool = True):
    """One GET with the polite User-Agent. Raises on HTTP errors; kept tiny so tests patch it."""
    ua = _user_agent() + (f" (mailto:{_email()})" if _email() else "")
    r = httpx.get(url, params=params, headers={"User-Agent": ua}, timeout=25, follow_redirects=True)
    r.raise_for_status()
    return r.json() if as_json else r.content


def _pmc_url(pmcid: str) -> str:
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid.upper()}/"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _from_epmc(r: dict) -> dict:
    return {"title": _clean(r.get("title", "")), "authors": r.get("authorString", ""), "year": r.get("pubYear", ""),
            "journal": (r.get("journalInfo") or {}).get("journal", {}).get("title") or r.get("journalTitle", ""),
            "doi": r.get("doi", ""), "pmcid": r.get("pmcid", ""), "oa": r.get("isOpenAccess") == "Y" and r.get("inPMC") == "Y",
            "license": r.get("license", ""), "abstract": _clean(r.get("abstractText", "")), "cited_by": r.get("citedByCount", 0),
            "type": ",".join((r.get("pubTypeList") or {}).get("pubType", [])[:2]), "source": "Europe PMC"}


def _abstract_from_index(inv: dict | None) -> str:
    """OpenAlex ships abstracts as {word: [positions]}; put the words back in order."""
    if not inv:
        return ""
    words = {pos: w for w, poss in inv.items() for pos in poss}
    return " ".join(words[i] for i in sorted(words))


def _from_openalex(w: dict) -> dict:
    loc = w.get("best_oa_location") or w.get("primary_location") or {}
    oa = w.get("open_access") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    return {"title": _clean(w.get("title") or ""), "authors": ", ".join(a["author"]["display_name"] for a in (w.get("authorships") or [])[:6]),
            "year": w.get("publication_year", ""), "journal": ((w.get("primary_location") or {}).get("source") or {}).get("display_name", ""),
            "doi": doi, "pmcid": "", "oa": bool(oa.get("is_oa")), "oa_url": oa.get("oa_url") or loc.get("pdf_url") or "",
            "license": loc.get("license") or "", "abstract": _abstract_from_index(w.get("abstract_inverted_index")),
            "cited_by": w.get("cited_by_count", 0), "type": w.get("type", ""), "source": "OpenAlex"}


def search_europepmc(query: str, n: int) -> list[dict]:
    d = _http_get(f"{EPMC}/search", {"query": query, "format": "json", "resultType": "core", "pageSize": n})
    return [_from_epmc(r) for r in d.get("resultList", {}).get("result", [])]


def search_openalex(query: str, n: int) -> list[dict]:
    p = {"search": query, "per-page": n, "select": "id,doi,title,publication_year,authorships,open_access,best_oa_location,"
         "primary_location,abstract_inverted_index,cited_by_count,type"}
    key = os.environ.get("OPENALEX_API_KEY") or _setting("openalex_api_key", "")
    if key:
        p["api_key"] = key
    elif _email():
        p["mailto"] = _email()
    return [_from_openalex(w) for w in _http_get(f"{OPENALEX}/works", p).get("results", [])]


def _jats_to_markdown(xml: bytes) -> str:
    """Europe PMC full-text XML (JATS) -> plain markdown: title, abstract, body sections, table captions. In-text reference
    markers (xref) are dropped; back matter is not included."""
    root = ET.fromstring(xml)

    def txt(el) -> str:
        parts = [el.text or ""]
        for ch in el:
            if ch.tag != "xref":
                parts.append(txt(ch))
            parts.append(ch.tail or "")
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    out = []
    title = root.find(".//article-meta/title-group/article-title")
    if title is not None:
        out.append("# " + txt(title))
    for ab in root.findall(".//article-meta/abstract"):
        out.append("## Abstract\n" + "\n\n".join(txt(p) for p in ab.iter("p")))

    def section(sec, depth=2):
        t = sec.find("title")
        if t is not None:
            out.append("#" * min(depth, 4) + " " + txt(t))
        for ch in sec:
            if ch.tag == "p":
                out.append(txt(ch))
            elif ch.tag == "sec":
                section(ch, depth + 1)
            elif ch.tag == "table-wrap":
                cap = ch.find("caption")
                rows = [" | ".join(txt(c) for c in tr) for tr in ch.iter("tr")]
                out.append("Table: " + (txt(cap) if cap is not None else "") + ("\n" + "\n".join(rows) if rows else ""))
            elif ch.tag == "fig":
                cap = ch.find("caption")
                if cap is not None:
                    out.append("Figure: " + txt(cap))

    body = root.find(".//body")
    if body is not None:
        section(body)
    return "\n\n".join(o for o in out if o.strip())[:_MAX_TEXT]


def _full_text_epmc(pmcid: str) -> str | None:
    try:
        md = _jats_to_markdown(_http_get(f"{EPMC}/{pmcid.upper()}/fullTextXML", as_json=False))
    except Exception:
        return None
    return md if md and _stub_reason(md) is None else None


def _unpaywall_urls(doi: str) -> list[str]:
    """Legal open-access copies Unpaywall knows, repositories first (publisher pages are the ones that bot-wall us)."""
    if not (doi and _email()):
        return []
    try:
        d = _http_get(f"{UNPAYWALL}/{quote(doi, safe='/')}", {"email": _email()})
    except Exception:
        return []
    locs = sorted(d.get("oa_locations") or [], key=lambda l: l.get("host_type") != "repository")
    return list(dict.fromkeys(u for l in locs for u in (l.get("url_for_pdf"), l.get("url_for_landing_page")) if u))[:4]


def _already_fetched(url: str) -> str | None:
    from utils.run_state import get_fetched_urls
    return next((e.get("filename") for e in get_fetched_urls() if e.get("url") == url), None)


def _fetch_budget_error() -> str | None:
    """Saving a paper is a fetch: it takes one unit of the shared fetch_url_to_workspace quota and counts against the
    per-task cap, exactly like web_search's auto-fetch."""
    import config as app_config
    from tools.core import check_quota
    from utils.run_state import task_fetched_urls_ctx
    own = task_fetched_urls_ctx.get()
    cap = app_config.get_setting("specialist_fetch_cap", 5)
    if own is not None and _specialist_fetch_over_cap(len(own), cap):
        return f"this task has already fetched {len(own)} real source(s) (cap {cap} per task); synthesize now instead of fetching more"
    return check_quota("fetch_url_to_workspace")


def _save_paper(rec: dict, text: str, full: bool) -> tuple[str, str]:
    """Write one paper to sources/. Returns (workspace filename, url). The first body line states the content level."""
    from tools.web import _slugify_for_filename, _fetched_filename
    url = _pmc_url(rec["pmcid"]) if full and rec.get("pmcid") and rec.get("oa") else f"https://doi.org/{rec['doi']}"
    level = (f"Content-Level: FULL TEXT ({rec.get('source', '')}, license: {rec.get('license') or 'not stated'})" if full else
             "Content-Level: ABSTRACT ONLY. The full text was not available to this run; state nothing beyond what this abstract says, "
             "and say in the report that only the abstract was read.")
    head = f"{level}\nDOI: {rec.get('doi') or 'n/a'}\nJournal: {rec.get('journal') or 'n/a'}\n\n"
    meta = {"title": rec.get("title"), "author": rec.get("authors"), "published": str(rec.get("year") or "")}
    fn = _slugify_for_filename(url, rec.get("title") or "paper")
    _save_fetched([url], fn, head + text, True, metadata=meta)
    return _fetched_filename(fn), url


def _line(i: int, r: dict, saved: str = "", note: str = "") -> str:
    ids = " ".join(x for x in (f"DOI {r['doi']}" if r.get("doi") else "", r.get("pmcid", "")) if x)
    s = f"## {i}. {r['title']}\n{r['authors']} ({r['year'] or 'n.d.'}). {r['journal']}. {ids}\n**Open access:** {'yes' if r['oa'] else 'no'}"
    s += f" | **Cited by:** {r['cited_by']} | **Type:** {r['type'] or 'n/a'} | via {r['source']}\n"
    if r["abstract"]:
        s += f"**Abstract:** {r['abstract'][:900]}{'...' if len(r['abstract']) > 900 else ''}\n"
    if saved:
        s += f"**Full text saved to workspace file:** `{saved}`: delegate its analysis directly.\n"
    elif note:
        s += f"**Note:** {note}\n"
    return s


@tool
@with_quota
async def scholar_search(query: str, max_results: int = 5, source: str = "auto") -> str:
    """Search the scholarly literature through official open APIs (Europe PMC, OpenAlex). Takes ONE query per call.

    Use plain keywords of the topic, e.g. 'prime editing clinical trial' (Europe PMC also accepts field syntax such as
    TITLE:"base editing" AND PUB_YEAR:[2024 TO 2026]). Returns title, authors, year, journal, DOI, open-access flag, citation
    count and the abstract. The full text of the top open-access results is saved to the workspace automatically; for any other
    result call scholar_fetch with its DOI or PMCID. A result marked 'no' for open access has no free full text: only its
    abstract can be read, and the report must say so.

    Args:
        query: keywords for the topic
        max_results: results to return (default 5, max 10)
        source: 'auto' (Europe PMC, then OpenAlex if few hits), 'europepmc' (life sciences and preprints), or 'openalex' (all fields)
    """
    n = max(1, min(int(max_results), 10))
    src = source.lower()
    try:
        results = [] if src == "openalex" else await asyncio.to_thread(_run_with_daemon_timeout, lambda: search_europepmc(query, n), 40)
        if src == "openalex" or (src == "auto" and len(results) < 2):
            try:
                results = results + await asyncio.to_thread(_run_with_daemon_timeout, lambda: search_openalex(query, n), 40)
            except Exception:
                if src == "openalex":  # the fallback failing (e.g. the shared keyless budget is spent) must not discard Europe PMC hits
                    raise
    except Exception as e:
        from tools.core import refund_quota
        refund_quota("scholar_search")
        return f"{TOOL_ERROR_PREFIX}Scholar search failed: {type(e).__name__}: {e}"
    if not results:
        return f"No scholarly results for '{query}'. Try broader keywords, or source='openalex'."

    saved, notes = {}, {}
    for i, r in enumerate(results[:n]):
        if len(saved) >= int(_setting("auto_fetch_top", 2) or 0) or not (r.get("pmcid") and r["oa"]):
            continue
        url = _pmc_url(r["pmcid"])
        if (prev := _already_fetched(url)):
            saved[i] = prev
            continue
        if (err := _fetch_budget_error()):
            notes[i] = f"not saved ({err})"
            break
        text = await asyncio.to_thread(_full_text_epmc, r["pmcid"])
        if text:
            saved[i], _ = await asyncio.to_thread(_save_paper, r, text, True)
        else:
            from tools.core import refund_quota
            refund_quota("fetch_url_to_workspace")
            notes[i] = "open-access full text could not be retrieved; use the abstract only"
    body = "\n".join(_line(i + 1, r, saved.get(i, ""), notes.get(i, "") or ("" if r["oa"] else "paywalled: abstract only"))
                     for i, r in enumerate(results[:n]))
    return f"📚 {len(results[:n])} scholarly result(s) for '{query}':\n\n{body}"


@tool
@with_quota
async def scholar_fetch(identifier: str) -> str:
    """Save one paper to the workspace by DOI or PMCID (e.g. '10.1038/nature12373' or 'PMC13058825'). Tries, in order: Europe PMC
    open-access full text, the legal open-access copy Unpaywall points to, then the abstract. The saved file's first line says
    'FULL TEXT' or 'ABSTRACT ONLY'; if it is abstract only, say so in the findings and do not claim anything the abstract does not
    state. Nothing is saved (and no quota is spent) when no real content could be obtained.

    Args:
        identifier: a DOI, a PMCID, or a URL containing one
    """
    ident = identifier.strip()
    pm, dm = _PMCID_RE.search(ident), _DOI_RE.search(ident)
    doi = dm.group(0).rstrip(".,;)") if dm else ""
    pmcid = pm.group(0).upper() if pm else ""
    if not (doi or pmcid):
        return f"{TOOL_ERROR_PREFIX}scholar_fetch needs a DOI (10.xxxx/...) or a PMCID (PMC123...). Use scholar_search to find one."

    def work():
        rec = None
        if pmcid or doi:
            hits = search_europepmc(f"PMCID:{pmcid}" if pmcid else f'DOI:"{doi}"', 1)
            rec = hits[0] if hits else None
        if rec is None and doi:
            try:
                rec = _from_openalex(_http_get(f"{OPENALEX}/works/doi:{doi}", {"mailto": _email()} if _email() else None))
            except Exception:
                rec = None
        rec = rec or {"title": "", "authors": "", "year": "", "journal": "", "doi": doi, "pmcid": pmcid, "oa": False,
                      "license": "", "abstract": "", "source": ""}
        return rec

    try:
        rec = await asyncio.to_thread(_run_with_daemon_timeout, work, 40)
    except Exception as e:
        return f"{TOOL_ERROR_PREFIX}Lookup failed: {type(e).__name__}: {e}"
    url = _pmc_url(rec["pmcid"]) if rec.get("pmcid") and rec.get("oa") else f"https://doi.org/{rec['doi']}"
    if (prev := _already_fetched(url)):
        return f"Already fetched this run: see workspace file '{prev}'. Read it instead of fetching again."
    if (err := _fetch_budget_error()):
        return f"Error: scholar_fetch rejected: {err}"
    from tools.core import refund_quota

    text = await asyncio.to_thread(_full_text_epmc, rec["pmcid"]) if rec.get("pmcid") and rec.get("oa") else None
    if text:
        fn, url = await asyncio.to_thread(_save_paper, rec, text, True)
        return f"Saved FULL TEXT to '{fn}' (Source-URL {url})."
    cands = ([rec["oa_url"]] if rec.get("oa_url") else []) + await asyncio.to_thread(_unpaywall_urls, rec["doi"])
    for oa in list(dict.fromkeys(cands))[:4]:
        try:
            data, urls, meta = await asyncio.to_thread(_run_with_daemon_timeout, lambda oa=oa: _fetch_raw(oa, True), 180)
            if isinstance(data, str) and _stub_reason(data) is None and len(data) > 3000:
                fn, url = await asyncio.to_thread(_save_paper, rec, data, True)
                return f"Saved FULL TEXT (legal open-access copy) to '{fn}' (Source-URL {url})."
        except Exception:
            continue
    if rec.get("abstract"):
        fn, url = await asyncio.to_thread(_save_paper, rec, "## Abstract\n" + rec["abstract"], False)
        return f"Only the ABSTRACT was available (no free full text): saved to '{fn}' (Source-URL {url}). Do not cite results beyond it."
    refund_quota("fetch_url_to_workspace")
    return f"{TOOL_ERROR_PREFIX}No full text or abstract could be obtained for {identifier}; nothing was saved. Do not cite it."
