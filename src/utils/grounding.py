import re
import config
from utils.run_state import get_fetched_urls
from tools.fs import get_workspace_file_content

# -------------------------------------------------------------
# Shared grounding-check logic, used by:
# - engine/tui.py's run_completion_check (final-report-level gate)
# - engine/orchestrator.py's _run_single_task (upstream, per-specialist-summary gate — catches a
#   hallucinated citation before it ever reaches the Planner's context, instead of only at the end)
# Pulled out of tui.py into its own module specifically so orchestrator.py can reuse it without a
# circular import (tui.py already imports engine.orchestrator).
# -------------------------------------------------------------

_PROPER_NOUN_STOPWORDS = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "In", "On", "At", "For", "With",
    "Source", "Sources", "Reference", "References", "Final", "Report", "Summary", "Note", "System",
}


def _urls_prefix_match(a: str, b: str) -> bool:
    """Prefix-match fallback for URL grounding (handles redirect variants and added query
    strings) — but a bare-origin URL (no path) never prefix-grounds a deep link. Confirmed live
    (2026-07-11, qwen3.6 Colombia run): one fetch of mercadolibre.com's ROOT let a fully
    fabricated deep URL on that domain pass fully_ungrounded, which waved the whole fabricated
    findings.md through. A domain root only matches itself exactly.

    The longer URL must continue at a path-segment boundary (/, ?, # or end) after the shorter
    one: bare string-prefixing let a DECORATED fabrication ride a real fetch — a genuinely
    fetched .../article grounded an invented .../article-fake-2024, so a model could dress up
    any real fetched URL and pass the primary gate (2026-07-12 audit, G1)."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if not longer.startswith(shorter):
        return False
    if len(longer) > len(shorter) and longer[len(shorter)] not in "/?#":
        return False
    from urllib.parse import urlparse
    return bool(urlparse(shorter).path.strip("/"))


def extract_cited_urls(text: str) -> list[str]:
    # Fullwidth 【】 brackets included: gpt-oss habitually cites as 【URL】, and the closing 】 was
    # observed leaking into the knowledge cache's pre-registered URLs (grounding still passed, but
    # only via the prefix-match fallback).
    urls = re.findall(r'https?://[^\s\)\]\}"\'>【】]+', text or "")
    return [u.rstrip('.,;:\'")]}】') for u in urls]


def extract_salient_terms(text: str) -> set:
    """Extract checkable, hard-to-coincidentally-reproduce tokens: numbers/versions/years/percentages,
    and multi-word capitalized phrases (proper nouns/titles). Deliberately cheap and deterministic
    rather than another LLM call, since this local model class has already proven unreliable as a
    judge of its own output elsewhere in this project."""
    if not text:
        return set()
    terms = set(re.findall(r'\b\d+(?:\.\d+)+\b|\b\d{4}\b|\b\d+%\b', text))
    for m in re.finditer(r'\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){1,4}\b', text):
        # Strip leading stopwords instead of discarding the whole phrase — "The Python Programming
        # Language" used to be thrown away entirely because "The" matched the stopword list.
        words = m.group(0).split()
        while words and words[0] in _PROPER_NOUN_STOPWORDS:
            words.pop(0)
        if words:
            terms.add(" ".join(words))
    return terms


def split_prose_from_sources(report: str) -> str:
    """Best-effort strip of a trailing Sources/References section, so the content-level check
    compares against what the report actually CLAIMS rather than its own citation list.
    Matches the bare heading plus common suffixed/Spanish variants ("### Source URLs",
    "Sources used", "Fuentes consultadas") — run 14's report used "### Source URLs", which
    this didn't match, so its citation list counted as prose. Suffixes are a fixed allowlist,
    NOT any trailing words: a real content heading like "Sources of growth in Colombia" must
    never be treated as the start of a citation section (that would hide claims from checks).
    Heading depth is capped at h3: an h4+ block ("#### Sources" under each niche section) is a
    PER-SECTION citation list, not the report's trailing sources section — confirmed live
    (run 15): stripping at the first such block deleted the rest of the report including its
    own citations, and find_uncited_claim_lines then flagged a correctly-sourced report."""
    m = re.search(
        r'^[\s>*_]*#{0,3}[\s>*_]*(?:sources?|references?|referencias?|fuentes?|bibliograf[ií]a)'
        r'(?:\s+(?:urls?|list|used|cited|consultadas?|utilizadas?))?[\s*_:]*$',
        report or "", re.IGNORECASE | re.MULTILINE)
    return report[:m.start()] if m else (report or "")


# Line-anchored citation-label shape ("Source: ..." / "- **Fuentes:** ..."), NOT any line merely
# containing the word "source" — confirmed live (2026-07-11 Colombia benchmark, qwen3.6): a
# heading "## Methodology & Source Quality Notes" and the prose "No claims were made without
# source attribution" were flagged as pseudo-citations on the FINAL attempt, quarantining a
# well-grounded 6-niche report that then could never be rewritten. The colon is required; a
# mid-line "(Source: X)" is now missed, which the parenthetical (Org, Year) regex below and the
# URL-presence gate still largely cover — conservative beats report-destroying.
# Trailing [^\s*_] requires real content (not markdown decoration) after the colon, so a bare
# "**Sources:**" section header (URLs on the FOLLOWING lines, which the http-skip exempts
# individually) isn't flagged.
_SOURCE_LABEL_RE = re.compile(r'^[\s>*_\-#]*(?:sources?|fuentes?)\s*:[\s*_]*[^\s*_]', re.IGNORECASE)
# A bare "(Org Name, 2020)"-style attribution — e.g. "(DANE, 2020)", "(Ministry of Environment,
# 2021)", "(World Bank, 2020)". Requires a real 4-digit 19xx/20xx year so it doesn't false-positive
# on something like "(Figure 2)"; deliberately does NOT match markdown link syntax (`[Title](url)`
# uses brackets for the title, not parens, and a URL never starts with a capital letter followed by
# more word characters and a comma).
_PARENTHETICAL_CITATION_RE = re.compile(r'\((?:[A-Z][\w.&\'-]*\s?){1,6},\s*(?:19|20)\d{2}\)')


def find_non_url_citations(text: str) -> list[str]:
    """Find claim-supporting attributions that aren't a real URL at all — a bare parenthetical
    like `(DANE, 2020)` or a `Source: Expert opinion from...` line — and so evade
    extract_cited_urls entirely, since it only recognizes `https?://` patterns. Confirmed live
    (ROADMAP.md "Findings from live testing", SESSION_STATUS.md's tracked #1 open item): a report
    can have real, correctly-fetched URL citations for MOST claims while still smuggling in an
    unverifiable non-URL attribution for others (e.g. "Expert opinion from a cold storage facility
    manager in Colombia") — the URL-presence gate in real_grounding_problem only runs when there's
    at least one non-URL-shaped citation to check in the first place, so a report that already has
    some real URLs elsewhere never even reaches a check for this.

    Line-scoped, not whole-report: this project's own required report format keeps a claim and its
    citation on one line (`- **[Title](URL)**`), so if a URL appears ANYWHERE on the same line as a
    "Source" label or a parenthetical org/year, it's treated as satisfied rather than flagging a
    real citation on the same line just because a separate unrelated pseudo-citation exists
    elsewhere in the report — keeps this conservative, matching claim_grounding_problem's own
    "keep false positives rare" design.
    """
    if not text:
        return []
    hits = []
    for line in text.splitlines():
        stripped = line.strip()
        if "http" in line:
            continue
        # Skip the engine's own injected nudges (e.g. "[SYSTEM RELEVANCE WARNING: ... source ...]")
        # — confirmed live, a salvaged report can carry one of these across a turn boundary (see
        # _find_last_substantial_text), and its own use of the word "source" inside a warning ABOUT
        # sourcing would otherwise get misread as the model's own pseudo-citation.
        if stripped.startswith("[SYSTEM"):
            continue
        m = _SOURCE_LABEL_RE.search(line) or _PARENTHETICAL_CITATION_RE.search(line)
        if m:
            hits.append(line.strip()[:120])
    return hits


def _source_body(content: str) -> str:
    """Strip the engine-injected 'Source-URL: ...' line-1 header (tools/web.py::_save_fetched)
    before any term/number matching against a source file. The header's URL slug contains the
    very tokens being verified — confirmed live (run 14): the report's '1819' regulation number
    existed in its cited stub file ONLY via this header, so the regulation check self-grounded
    on our own injected provenance line."""
    if content.startswith("Source-URL:"):
        return content.split("\n", 1)[1] if "\n" in content else ""
    return content


def _fetched_url_files() -> dict:
    """url -> filename map for the LINE-SCOPED checks (regulation/claim), excluding stub fetches
    (soft-404/paywall shells flagged by tools/web.py's _stub_reason): a stub can neither support
    a claim nor be meaningfully line-checked — chrome text matching a claim's terms proves
    nothing. Citing a stub is caught upstream by real_grounding_problem's stub gate instead."""
    return {entry["url"].rstrip('/'): entry["filename"]
            for entry in get_fetched_urls() if not entry.get("stub")}


# Regulation identifiers, ES + EN: "Ley 1906 de 2021", "Decreto 2242/2015", "Resolución
# 2275/2023", "Directive 2014/55/EU", "Regulation (EU) 2024/1689", "Circular 052", "CONPES 3995".
_REGULATION_ID_RE = re.compile(
    r'\b(?:Ley|Decreto|Resoluci[oó]n|Circular|Directiva|Directive|Regulation|CONPES)'
    r'(?:\s+\(?(?:EU|UE)\)?)?'
    r'\s*(?:N[o°º.]*\s*)?'
    r'(\d{2,}(?:[./]\d{2,4})?)'
    r'(?:\s+de\s+\d{4})?',
    re.IGNORECASE,
)


def find_unsupported_regulation_ids(text: str) -> list[str]:
    """A regulation identifier cited to a fetched source whose content never mentions the
    regulation's number. Confirmed live (run 12, 2026-07-11): the report's flagship niche hung on
    'Ley 1906 de 2021', cited to a genuinely-fetched Mintic page that is actually about the 2025-2027
    digital security strategy and contains no '1906' anywhere — the URL-presence gate passed (real
    fetch) and the zero-overlap content check passed (shared generic terms like 'Colombia'), so a
    misattributed law number sailed through both. Line-scoped like find_non_url_citations (this
    project's report format keeps a claim and its citation on one line); only fires when the line's
    cited URL WAS fetched — unfetched URLs are already the hard gate's job. Conservative by
    construction: only the identifier's primary number is required, as a whole word, anywhere in
    the source — absence is a strong signal, coincidental presence just means no flag."""
    fetched = _fetched_url_files()
    hits = []
    for line in (text or "").splitlines():
        ids = list(_REGULATION_ID_RE.finditer(line))
        if not ids:
            continue
        files = []
        for u in extract_cited_urls(line):
            key = u.rstrip('/')
            fn = fetched.get(key) or next(
                (f for orig, f in fetched.items() if _urls_prefix_match(key, orig)), None)
            if fn:
                files.append(fn)
        if not files:
            continue
        content = "\n".join(_source_body(get_workspace_file_content(f) or "") for f in files)
        if len(content.strip()) < 50:
            continue
        for m in ids:
            num = re.split(r'[./]', m.group(1))[0]
            if len(num) >= 2 and not re.search(rf'\b{re.escape(num)}\b', content):
                hits.append(m.group(0).strip())
    return hits


# Figure-bearing tokens only (amounts, decimals, years, percentages, counted quantities) — the
# quantitative-claim vector specifically, not proper nouns, which appear in harmless narrative
# far too often to gate on.
_NUMERIC_CLAIM_RE = re.compile(r'\b\d+(?:[.,]\d+)+\b|\b(?:19|20)\d{2}\b|\b\d+\s?%|\b(?:USD|COP|EUR)\s?\d')


def find_uncited_claim_lines(report: str) -> list[str]:
    """Figure-bearing claim lines that carry no citation at all — the format hole every
    line-scoped check falls through. Confirmed live (run 14): the model wrote its claims as a
    table plus a detached '### Source URLs' section, so no claim line carried a URL and the
    regulation/claim checks had literally nothing to bite on, while the hard gate passed on the
    detached list. This project's required format is claim+citation on ONE line; a claim that
    names a figure but no source on its own line is unverifiable BY CONSTRUCTION here even when
    the report's URL list is fully real. Conservative: only lines with a hard number (figure,
    percent, year, amount), only substantial lines, and the caller only acts on a pile of them
    (>=3) — narrative context lines or a single stray year never trip a verdict alone.

    SECTION-scoped, not line-scoped (run 15's live false positive): a report that puts a
    '#### Sources' block inside each niche's own h1-h3 section HAS tied those claims to
    sources — per-section rather than per-line, which the per-line checks can't read but is
    not the run-14 decoupling this exists to catch. Any URL anywhere in a section (delimited
    by h1-h3 headings; h4+ subsections stay with their parent) exempts that whole section;
    run 14's shape (figure table in one section, every URL in a detached 'Source URLs'
    section) still fires."""
    sections: list[list[str]] = [[]]
    for raw in split_prose_from_sources(report or "").splitlines():
        if re.match(r'#{1,3}\s', raw):
            sections.append([])
        sections[-1].append(raw)
    hits = []
    for section in sections:
        if any("http" in l for l in section):
            continue
        for raw in section:
            line = raw.strip()
            if len(line) < 30 or line.startswith(("#", ">", "[SYSTEM")):
                continue
            if re.fullmatch(r'[|\s:\-]+', line):  # markdown table separator row
                continue
            if _NUMERIC_CLAIM_RE.search(line):
                hits.append(line[:120])
    return hits


def claim_grounding_problem(report: str) -> str | None:
    """Content-level check beyond URL-presence, LINE-scoped (this project's report format keeps a
    claim and its citation on one line, same as find_non_url_citations/find_unsupported_regulation_ids):
    for each line citing a fetched source, do that line's own checkable facts (numbers, versions,
    proper nouns) share anything with that source's content? The previous version compared
    WHOLE-report terms against each source, so generic shared terms ('Colombia', a year) masked
    per-claim fabrication — run 12's flagship figure (USD 3.5B, absent from its cited source)
    passed because OTHER lines shared terms with the same source. Still deliberately conservative,
    to keep false positives rare: only fires on a line with >=1 checkable term of its own and ZERO
    overlap with every fetched source it cites; lines with no checkable terms, unfetched citations
    (the hard gate's job), or thin sources (<50 chars) are skipped. URL text is stripped before
    term extraction so a slug like 'ley_1819_2016' can neither support nor incriminate a claim."""
    prose = split_prose_from_sources(report)
    fetched = _fetched_url_files()

    unsupported = []
    source_terms_cache: dict = {}
    for line in prose.splitlines():
        cited = extract_cited_urls(line)
        if not cited:
            continue
        line_terms = extract_salient_terms(re.sub(r'https?://[^\s\)\]\}"\'>【】]+', '', line))
        if not line_terms:
            continue
        files = []
        for u in cited:
            key = u.rstrip('/')
            fn = fetched.get(key) or next(
                (f for orig, f in fetched.items() if _urls_prefix_match(key, orig)), None)
            if fn:
                files.append((u, fn))
        if not files:
            continue
        checkable, supported = False, False
        for _, fn in files:
            if fn not in source_terms_cache:
                content = _source_body(get_workspace_file_content(fn) or "")
                source_terms_cache[fn] = extract_salient_terms(content) if len(content.strip()) >= 50 else None
            source_terms = source_terms_cache[fn]
            if source_terms is None:
                continue
            checkable = True
            if line_terms & source_terms:
                supported = True
                break
        if checkable and not supported:
            unsupported.append(files[0][0])

    if not unsupported:
        return None
    return f"claim_unsupported:{', '.join(unsupported[:3])}"


def fully_ungrounded(content: str) -> str | None:
    """Wholesale-fabrication gate for findings.md (Pass 1): 'no_urls' if it cites nothing at all,
    'all_cited_urls_unverified' if not a single cited URL matches anything actually fetched this
    run; None if at least one citation is real. Deliberately laxer than real_grounding_problem —
    Pass-1 notes legitimately mention extra search-snippet URLs a Searcher never fetched, and the
    final report (which may only cite URLs already in findings.md) still gets the strict per-URL
    check. This only has to catch the confirmed live failure: a Planner that abandons delegation
    and fabricates the ENTIRE Pass-1 file from memory, which Pass 2 then treats as ground truth."""
    cited = extract_cited_urls(content)
    if not cited:
        return "no_urls"
    fetched = {entry["url"].rstrip('/') for entry in get_fetched_urls()}
    for u in cited:
        key = u.rstrip('/')
        if key in fetched or any(_urls_prefix_match(key, f) for f in fetched):
            return None
    return "all_cited_urls_unverified"


async def real_grounding_problem(content: str) -> str | None:
    """Cross-references every URL cited in `content` against URLs the engine actually saw
    fetch_url_to_workspace fetch this run. A URL not in that verified set is ALWAYS a grounding
    problem — this is the primary, hard gate. Returns a human-readable problem description, or
    None if every citation is verified (or there are no citations to check)."""
    cited = extract_cited_urls(content)
    if not cited:
        return "no_urls"

    fetched_entries = get_fetched_urls()
    fetched = {entry["url"].rstrip('/') for entry in fetched_entries}
    unverified = [u for u in cited if u.rstrip('/') not in fetched and not any(_urls_prefix_match(u.rstrip('/'), f) for f in fetched)]

    gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})

    if unverified:
        detail = f"unverified_urls:{', '.join(unverified[:3])}"
        if gc_cfg.get("live_http_verify", False):
            from tools.web import verify_url_live
            timeout = gc_cfg.get("live_http_timeout", 5)
            dead = [u for u in unverified if not await verify_url_live(u, timeout=timeout)]
            if dead:
                detail += f" (also unreachable: {', '.join(dead[:3])})"
        return detail

    # Every cited URL WAS fetched — but a fetch that returned only a soft-404/paywall shell
    # (entry["stub"], see tools/web.py's _stub_reason) has no real content behind it, so a
    # citation resolving ONLY to stub fetches is hollow. Closes run 14's hole: a model-invented
    # URL that the domain answered with a 200 subscription shell passed this gate as a "real"
    # fetch. A URL also fetched non-stub elsewhere (retry that got the real page) stays valid.
    if gc_cfg.get("stub_detection", True):
        non_stub = {e["url"].rstrip('/') for e in fetched_entries if not e.get("stub")}
        stub_only = {e["url"].rstrip('/') for e in fetched_entries if e.get("stub")} - non_stub
        if stub_only:
            stub_cited = [
                u for u in cited
                if u.rstrip('/') not in non_stub
                and not any(_urls_prefix_match(u.rstrip('/'), f) for f in non_stub)
                and (u.rstrip('/') in stub_only
                     or any(_urls_prefix_match(u.rstrip('/'), f) for f in stub_only))
            ]
            if stub_cited:
                return f"stub_source:{', '.join(stub_cited[:3])}"

    if gc_cfg.get("non_url_citation_check", True):
        non_url = find_non_url_citations(content)
        if non_url:
            return f"non_url_citation:{'; '.join(non_url[:3])}"

    if gc_cfg.get("regulation_id_check", True):
        bad_regs = find_unsupported_regulation_ids(content)
        if bad_regs:
            return f"regulation_unsupported:{'; '.join(bad_regs[:3])}"

    if gc_cfg.get("content_level_check", True):
        problem = claim_grounding_problem(content)
        if problem:
            return problem

    # Last, because it's the weakest signal: everything cited is real, no line-scoped check
    # fired — but if the claims are decoupled from the citations wholesale (table + detached
    # URL list, run 14's shape), that silence is vacuous, not a pass.
    if gc_cfg.get("citation_format_check", True):
        uncited = find_uncited_claim_lines(content)
        if len(uncited) >= 3:
            return (f"uncited_claims:{len(uncited)} figure-bearing lines with no citation on "
                    f"the line, e.g. {uncited[0][:80]!r}")

    return None
