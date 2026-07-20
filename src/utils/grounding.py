import re
from typing import Optional
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
    #
    # NOTE: ')' is deliberately NOT excluded here (unlike ']', '}', quotes) — a real URL can
    # contain a literal balanced '(...)', e.g. Wikipedia disambiguator pages
    # (.../wiki/Heuristic_(computer_science)). Confirmed live 2026-07-12: excluding ')' entirely
    # truncated that exact URL mid-slug, so a genuinely-fetched citation was false-flagged as
    # unverified/hallucinated for 3 consecutive completion-check attempts, wasting the run's
    # retry budget chasing a bug in the extractor, not the model. The regex now captures through
    # any ')' (including the markdown link's own closing paren), and _strip_trailing_punct below
    # removes only an UNBALANCED trailing ')' — i.e. one with no matching '(' earlier in the same
    # URL — which correctly strips markdown syntax while preserving a URL's own balanced parens.
    urls = re.findall(r'https?://[^\s\]\}"\'>【】]+', text or "")
    return [_strip_trailing_punct(u) for u in urls]


def _strip_trailing_punct(url: str) -> str:
    # '*' added 2026-07-14: Builder's own citation style is `**[Title](URL)**` — the closing `**`
    # sat right after the URL's markdown-link `)`, which made `.endswith(')')` below false
    # (the literal last char was '*', not ')'), so the unbalanced-paren strip never even ran and
    # every Builder-written citation carried a trailing '**' that could never match a real fetched
    # URL — a false not_grounded on every single report using this style. Stripped BEFORE the
    # paren check specifically so a bold-wrapped URL still exposes its real trailing ')' to it.
    url = url.rstrip('.,;:\'"]}】*')
    while url.endswith(')') and url.count(')') > url.count('('):
        url = url[:-1]
    return url


# Shared by extract_salient_terms below and find_cross_source_contradictions (ROADMAP Phase 2) —
# both need the same "what counts as a real proper-noun subject phrase" definition.
_PROPER_NOUN_PHRASE_RE = re.compile(r'\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*){1,4}\b')


def _normalize_proper_noun_phrase(raw: str) -> Optional[str]:
    """Strip leading stopwords instead of discarding the whole phrase — "The Python Programming
    Language" used to be thrown away entirely because "The" matched the stopword list. Returns
    None if nothing real is left (a bare stopword run)."""
    words = raw.split()
    while words and words[0] in _PROPER_NOUN_STOPWORDS:
        words.pop(0)
    return " ".join(words) if words else None


def extract_salient_terms(text: str) -> set:
    """Extract checkable, hard-to-coincidentally-reproduce tokens: numbers/versions/years/percentages,
    and multi-word capitalized phrases (proper nouns/titles). Deliberately cheap and deterministic
    rather than another LLM call, since this local model class has already proven unreliable as a
    judge of its own output elsewhere in this project."""
    if not text:
        return set()
    terms = set(re.findall(r'\b\d+(?:\.\d+)+\b|\b\d{4}\b|\b\d+%\b', text))
    for m in _PROPER_NOUN_PHRASE_RE.finditer(text):
        normalized = _normalize_proper_noun_phrase(m.group(0))
        if normalized:
            terms.add(normalized)
    return terms


# Shared by split_prose_from_sources (everything BEFORE this heading) and extract_sources_section
# (everything FROM this heading onward, used to parse academic-style References entries below).
# Suffixes are a fixed allowlist, NOT any trailing words: a real content heading like "Sources of
# growth in Colombia" must never be treated as the start of a citation section. Heading depth is
# capped at h3 (see split_prose_from_sources docstring). Includes English "Bibliography" alongside
# the Spanish "bibliografía" — added for the academic-style literature-review References/
# Bibliography heading, which the Spanish-only pattern didn't cover.
_SOURCES_HEADING_RE = re.compile(
    r'^[\s>*_]*#{0,3}[\s>*_]*(?:sources?|references?|referencias?|fuentes?|bibliograph(?:y|ies)|bibliograf[ií]a)'
    r'(?:\s+(?:urls?|list|used|cited|consultadas?|utilizadas?))?[\s*_:]*$',
    re.IGNORECASE | re.MULTILINE)


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
    m = _SOURCES_HEADING_RE.search(report or "")
    return report[:m.start()] if m else (report or "")


def extract_sources_section(report: str) -> str:
    """Companion to split_prose_from_sources: returns the trailing Sources/References/
    Bibliography section itself (heading onward), used to parse academic-style (Author, Year)
    reference-list entries in parse_academic_references below."""
    m = _SOURCES_HEADING_RE.search(report or "")
    return report[m.start():] if m else ""


# Leading-capital character class shared by every academic-citation regex below: ASCII A-Z plus
# Latin-1/Latin Extended-A uppercase (À-Ö, Ø-Þ — covers accented surnames like "Ürgenç"/"Özgüz").
# Fresh audit, 2026-07-12: _REFERENCE_ENTRY_RE and _ACADEMIC_KEY_RE originally used bare [A-Z],
# so even after _PARENTHETICAL_CITATION_RE (below) was fixed to DETECT a Unicode-capital citation,
# resolving its (surname, year) key still silently failed for one starting with an accented
# capital — detected as a citation, but never resolvable against any References entry.
_CAP_CHAR = "A-ZÀ-ÖØ-Þ"

# A numbered or bracket-numbered References-list entry in academic style, e.g. "1. Punati, S. B.,
# et al. (2025). Temporal Fusion Transformer..." or "[1] Smith, J. (2024)...". Captures the first
# author's surname and the publication year — together the same (surname, year) key an in-text
# `(Author, Year)` citation uses, so a citation can be resolved against this entry without needing
# full bibliographic parsing.
_REFERENCE_ENTRY_RE = re.compile(rf'^[\s>*_]*(?:\d+\.|\[\d+\])\s*([{_CAP_CHAR}][\w\-\']+)[^\n(]{{0,80}}\(((?:19|20)\d{{2}})\)')

# A bare `(Author, Year)` / `(Author et al., Year)` / `(Author & Other, Year)` in-text citation.
# Shares its shape with _PARENTHETICAL_CITATION_RE below (both require a capitalized name-like
# token followed by a real 19xx/20xx year) — kept as a separate compiled pattern only for the
# distinct capture groups this one needs to build a resolution key.
_ACADEMIC_KEY_RE = re.compile(rf'\(([{_CAP_CHAR}][\w\-\']+).*?,\s*((?:19|20)\d{{2}})\)')


def _academic_citation_key(citation_text: str) -> str | None:
    """Normalize an in-text `(Author, Year)`-shaped citation (or a References-entry's leading
    `Author (Year)`) to a lookup key: lowercased first-author surname + year. Both sides of the
    match (in-text citation, reference-list entry) go through this same function so "Punati et
    al., 2025" and "Punati, S. B., et al. (2025)" resolve to the same key.

    _REFERENCE_ENTRY_RE (anchored, numbered-entry-only) is tried FIRST — found live (fresh audit,
    2026-07-12): trying the unanchored _ACADEMIC_KEY_RE first meant it could match a `(Word, YYYY)`
    -shaped substring INSIDE a reference entry's own title (e.g. a paper titled "...analysis
    (Preliminary, 1998)...") before ever reaching the entry's real leading author/year, silently
    mis-keying that entry to the title's inner parenthetical instead of its actual citation key.
    _REFERENCE_ENTRY_RE only ever matches a numbered/bracketed reference-list LINE (its leading
    anchor plus required "N." / "[N]" prefix), so trying it first is always safe for a plain
    in-text citation like "(Punati et al., 2025)" — it falls through to _ACADEMIC_KEY_RE unchanged.
    """
    m = _REFERENCE_ENTRY_RE.match(citation_text) or _ACADEMIC_KEY_RE.search(citation_text)
    if not m:
        return None
    return f"{m.group(1).lower()},{m.group(2)}"


def parse_academic_references(report: str) -> dict[str, str]:
    """Map (surname, year) citation keys to the real URL on that References-list entry, for
    reports using academic `(Author, Year)` in-text citations instead of the project's default
    inline `- **[Title](URL)**` format (the literature-review output style modeled on
    eval/reference/sales_forecasting_deepseek.md). Only entries that actually list a fetchable
    URL resolve — a reference entry that cites a paper by title/arXiv-ID text alone with no URL
    (a real risk: that's exactly how the DeepSeek reference itself writes its own References list)
    is deliberately left unresolvable, so it still gets caught as an ungrounded citation by
    find_non_url_citations/the hard URL-presence gate, same as a fabricated inline citation would."""
    section = extract_sources_section(report)
    if not section:
        return {}
    mapping = {}
    for line in section.splitlines():
        key = _academic_citation_key(line.strip())
        if not key:
            continue
        urls = extract_cited_urls(line)
        if urls:
            mapping[key] = urls[0]
    return mapping


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
# 2021)", "(World Bank, 2020)" — AND an academic "(Author, Year)" in-text citation, including
# "et al.", "&", "and", and accented surnames: "(Punati et al., 2025)", "(Ürgenç & Özgüz, 2025)",
# "(Smith and Jones, 2024)". Requires a real 4-digit 19xx/20xx year so it doesn't false-positive
# on something like "(Figure 2)"; deliberately does NOT match markdown link syntax (`[Title](url)`
# uses brackets for the title, not parens, and a URL never starts with a capital letter followed by
# more word characters and a comma).
#
# Found live (fresh audit, 2026-07-12) that the original all-tokens-must-be-capitalized version
# silently failed to match "et al."/"&"/"and"/non-ASCII-capital citations at all — not a
# false-positive, a total MISS, which broke academic-mode grounding in BOTH directions: a
# fabricated "(Nobody et al., 2099)" went undetected by find_non_url_citations (the check simply
# never saw it as citation-shaped), while a genuinely well-formed "(Punati et al., 2025)" section
# was NOT exempted by find_uncited_claim_lines' presence check, quarantining a correctly-cited
# report. Every FIRST token must still start with a capital letter (ASCII or Latin-1/Extended-A
# uppercase, À-ÖØ-Þ) to keep the original false-positive guard against
# lowercase narrative parentheticals like "(and this happened, 2020)" — only "et al."/"and"/"&"
# are allowed as SUBSEQUENT connector tokens, never as the sole/first token.
_CITATION_NAME_TOKEN = rf"[{_CAP_CHAR}][\w.'-]*"
_CITATION_CONNECTOR = r"(?:et\s+al\.?|and|&)"
_PARENTHETICAL_CITATION_RE = re.compile(
    rf'\({_CITATION_NAME_TOKEN}(?:\s+(?:{_CITATION_NAME_TOKEN}|{_CITATION_CONNECTOR})){{0,5}},'
    rf'\s*(?:19|20)\d{{2}}\)'
)


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

    ACADEMIC style exception: a `(Author, Year)` parenthetical that resolves through
    parse_academic_references to a real URL-bearing References entry is not a bare pseudo-citation
    at all — it's this project's second supported citation dialect (see
    utils.grounding.parse_academic_references), so it's excluded from the hits here rather than
    flagged. An UNresolved `(Author, Year)` (no matching References entry, or an entry with no URL)
    still gets flagged exactly as before — this only carves out the case that's actually grounded.
    """
    if not text:
        return []
    ref_map = parse_academic_references(text)
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
        label_m = _SOURCE_LABEL_RE.search(line)
        paren_ms = list(_PARENTHETICAL_CITATION_RE.finditer(line))
        if paren_ms and not label_m:
            # A line can carry more than one (Author, Year) citation — resolve EVERY match, not
            # just the first, or a real citation earlier on the line would mask an unresolved
            # (fabricated) one later on the same line.
            if all(_academic_citation_key(m.group(0)) in ref_map for m in paren_ms):
                continue
        m = label_m or (paren_ms[0] if paren_ms else None)
        if m:
            hits.append(line.strip()[:120])
    return hits


def _source_body(content: str) -> str:
    """Strip the engine-injected header block (tools/web.py::_save_fetched) before any term/number
    matching against a source file. The header's URL slug contains the very tokens being verified
    — confirmed live (run 14): the report's '1819' regulation number existed in its cited stub
    file ONLY via this header, so the regulation check self-grounded on our own injected
    provenance line.

    Splits on the first BLANK line, not the first newline: since 2026-07-12 the header can be
    multiple lines (Source-URL, optionally Title/Authors/Published — see _save_fetched), all
    ending with one blank-line separator before the real body, same as the original single-line
    shape. A split on the first bare '\\n' would have left Title:/Authors:/Published: lines mixed
    into what's treated as body content."""
    if content.startswith("Source-URL:"):
        return content.split("\n\n", 1)[1] if "\n\n" in content else ""
    return content


def _fetched_url_files() -> dict:
    """url -> filename map for the LINE-SCOPED checks (regulation/claim), excluding stub fetches
    (soft-404/paywall shells flagged by tools/web.py's _stub_reason): a stub can neither support
    a claim nor be meaningfully line-checked — chrome text matching a claim's terms proves
    nothing. Citing a stub is caught upstream by real_grounding_problem's stub gate instead."""
    return {entry["url"].rstrip('/'): entry["filename"]
            for entry in get_fetched_urls() if not entry.get("stub")}


def _line_cited_files(line: str, fetched: dict, ref_map: dict) -> list[str]:
    """Resolve every citation on a line — inline `https://...` URLs AND academic-style
    `(Author, Year)` citations resolved through ref_map — to the fetched source's workspace
    filename. Shared by find_unsupported_regulation_ids and claim_grounding_problem so both
    line-scoped checks see academic citations the same way the hard URL gate does."""
    files = []
    for u in extract_cited_urls(line):
        key = u.rstrip('/')
        fn = fetched.get(key) or next(
            (f for orig, f in fetched.items() if _urls_prefix_match(key, orig)), None)
        if fn:
            files.append(fn)
    for pm in _PARENTHETICAL_CITATION_RE.finditer(line):
        rkey = _academic_citation_key(pm.group(0))
        if not rkey or rkey not in ref_map:
            continue
        url = ref_map[rkey].rstrip('/')
        fn = fetched.get(url) or next(
            (f for orig, f in fetched.items() if _urls_prefix_match(url, orig)), None)
        if fn and fn not in files:
            files.append(fn)
    return files


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
    ref_map = parse_academic_references(text or "")
    hits = []
    for line in (text or "").splitlines():
        ids = list(_REGULATION_ID_RE.finditer(line))
        if not ids:
            continue
        files = _line_cited_files(line, fetched, ref_map)
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


def split_into_heading_sections(text: str) -> list[list[str]]:
    """Splits markdown into sections at each h1-h3 heading line (h4+ stays with its parent
    section, e.g. a per-niche `#### Sources` block) — shared by find_uncited_claim_lines below
    and engine/completion.py's check_excluded_topic, which both need the same section boundaries.
    Returns a list of sections, each a list of RAW (unstripped) lines; the heading line itself is
    the first line of the section it starts. First section (before any heading) may be empty."""
    sections: list[list[str]] = [[]]
    for raw in (text or "").splitlines():
        if re.match(r'#{1,3}\s', raw):
            sections.append([])
        sections[-1].append(raw)
    return sections


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
    section) still fires.

    ACADEMIC style: a section is also exempt if it contains a `(Author, Year)`-shaped citation —
    presence-only, same bar as the "http" exemption (whether it actually RESOLVES to a real
    fetched source is find_non_url_citations/claim_grounding_problem's job, which both run before
    this check in real_grounding_problem's ordering, so an unresolved academic citation is already
    caught there and this exemption never masks it)."""
    sections = split_into_heading_sections(split_prose_from_sources(report or ""))
    hits = []
    for section in sections:
        if any("http" in l or _PARENTHETICAL_CITATION_RE.search(l) for l in section):
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


_CLAIM_CITATION_TOKEN_RE = re.compile(
    r'\[[^\]]*\]\(https?://[^\s\)]+\)'    # markdown [title](url) — the common case
    r'|https?://[^\s\]\}"\'>【】]+'         # a bare, non-markdown URL
    r'|' + _PARENTHETICAL_CITATION_RE.pattern  # (Author, Year) academic-style
)


def decompose_claim_segments(line: str) -> list[str]:
    """ROADMAP "Claim-level grounding upgrade" (Phase 1.1): splits a claim line into atomic
    segments, each ending at (and including) its own citation — the unit claim_grounding_problem
    now checks against a single source, instead of treating a whole multi-citation line as one
    unit whose claims can borrow credit from each other's citations. Concretely, a line like
    "Sector A grew 12% [gov](url1), while Sector B declined 3% [news](url2)" used to be checked
    as ONE bag of terms against the UNION of both sources — a shared generic term anywhere would
    mark BOTH claims "supported" even if only one citation actually backs its own claim. This now
    decomposes it into two segments, each checked only against its own bound citation.

    Deliberately mechanical (regex token boundaries), not NLP sentence-splitting — no new
    dependency, and it only splits, it never judges what's true (that stays claim_grounding_problem's
    job). A line with zero or one citation decomposes to `[line]` unchanged, so this is a strict
    refinement of the prior whole-line behavior, not a new base case — every previously-tested
    single-citation line is unaffected. Trailing text after the last citation (no citation of its
    own) stays attached to the last segment rather than becoming an uncheckable orphan."""
    tokens = list(_CLAIM_CITATION_TOKEN_RE.finditer(line))
    if len(tokens) <= 1:
        return [line]
    segments = []
    start = 0
    for m in tokens:
        segments.append(line[start:m.end()])
        start = m.end()
    if start < len(line) and line[start:].strip():
        segments[-1] += line[start:]
    return segments


def claim_grounding_problem(report: str) -> str | None:
    """Content-level check beyond URL-presence, CLAIM-scoped (this project's report format keeps
    a claim and its citation on one line, same as find_non_url_citations/find_unsupported_regulation_ids):
    for each citation-bearing SEGMENT of a line (see decompose_claim_segments — a line can carry
    more than one claim+citation pair), do that segment's own checkable facts (numbers, versions,
    proper nouns) share anything with ITS OWN cited source's content? An earlier version compared
    WHOLE-LINE terms against the union of every source cited anywhere on the line, so a multi-claim
    line could pass on a shared term between claim A and claim B's source even though claim A's own
    citation didn't support it at all — the citation-sharing/drift gap ROADMAP's claim-level
    grounding upgrade item exists to close (before that, an earlier fix already closed the same
    class of bug at the whole-REPORT level: run 12's flagship figure, USD 3.5B, absent from its
    cited source, passed because OTHER LINES shared terms with the same source). Still deliberately
    conservative, to keep false positives rare: only fires on a segment with >=1 checkable term of
    its own and ZERO overlap with every fetched source it cites; segments with no checkable terms,
    unfetched citations (the hard gate's job), or thin sources (<50 chars) are skipped. URL text is
    stripped before term extraction so a slug like 'ley_1819_2016' can neither support nor
    incriminate a claim.

    ACADEMIC style: a segment's `(Author, Year)` citation resolves through parse_academic_references
    to the same fetched-source file a `- **[Title](URL)**` citation would (via _line_cited_files),
    so an academic-style claim gets the identical term-overlap check as the default format."""
    prose = split_prose_from_sources(report)
    fetched = _fetched_url_files()
    ref_map = parse_academic_references(report)

    unsupported = []
    source_terms_cache: dict = {}
    for line in prose.splitlines():
        for segment in decompose_claim_segments(line):
            # A bare `- Source: [Title](url).` sub-bullet is a citation ATTRIBUTION, not a claim
            # (see _is_citation_only_line) -- skipping it here closes a false-positive class this
            # check didn't have until it existed: extract_salient_terms pulls the citation's own
            # ANCHOR TEXT ("Official Eiffel Tower" from "[Official Eiffel Tower website](url)") as
            # if it were a checkable fact, then flags it because that editorialized phrase (the
            # writer's own paraphrase, not something literally in the source) doesn't appear
            # verbatim in the source content -- confirmed live 2026-07-14, a genuinely-supported
            # report (both cited claims verbatim in the fetched source) still burned its entire
            # retry budget on this exact false positive, repeatedly, across 6 completion-check
            # attempts. Same guard already applied to _extract_figure_claims for the identical
            # false-positive class in cross-source-contradiction detection.
            if _is_citation_only_line(segment):
                continue
            display = (extract_cited_urls(segment) + [m.group(0) for m in _PARENTHETICAL_CITATION_RE.finditer(segment)])
            if not display:
                continue
            seg_terms = extract_salient_terms(re.sub(r'https?://[^\s\)\]\}"\'>【】]+', '', segment))
            if not seg_terms:
                continue
            files = _line_cited_files(segment, fetched, ref_map)
            if not files:
                continue
            checkable, supported = False, False
            for fn in files:
                if fn not in source_terms_cache:
                    content = _source_body(get_workspace_file_content(fn) or "")
                    source_terms_cache[fn] = extract_salient_terms(content) if len(content.strip()) >= 50 else None
                source_terms = source_terms_cache[fn]
                if source_terms is None:
                    continue
                checkable = True
                if seg_terms & source_terms:
                    supported = True
                    break
            if checkable and not supported:
                unsupported.append(display[0])

    if not unsupported:
        return None
    return f"claim_unsupported:{', '.join(unsupported[:3])}"


def _figure_kind(figure: str) -> str:
    """Classifies a figure string so find_cross_source_contradictions only ever compares like
    with like (a percentage against a percentage, never a bare year against a percentage) —
    without this, a line naming both a year and an unrelated percentage would "contradict" any
    other source mentioning a different percentage for a totally different reason, pure noise."""
    if figure.endswith('%'):
        return "percent"
    if re.fullmatch(r'(?:19|20)\d{2}', figure):
        return "year"
    if re.match(r'(?:USD|COP|EUR)', figure):
        return "currency"
    return "decimal"


_MARKDOWN_LINK_RE = re.compile(r'\[[^\]]*\]\([^)]*\)')
_CITATION_LINE_PREFIX_RE = re.compile(r'^\s*(?:[-*]|\d+[.)]|\[[↑\d]+\])\s*(?:source|retrieved)?\s*:?\s*', re.IGNORECASE)


def _is_citation_only_line(line: str) -> bool:
    """True for a bibliographic/citation line — a `- Source: [Title](URL)` attribution, a
    numbered reference-list entry (`12. ↑ ["Title"](url). *Publisher*. Retrieved ...`), or any
    line that is essentially just markdown link(s) plus punctuation — as opposed to a genuine
    prose sentence that happens to contain a link. Confirmed live 2026-07-14: an agency name used
    only as a citation attribution (e.g. 'Statistics Iceland' appearing solely inside
    `- Source: [... - Statistics Iceland](url)` and dozens of times across a long fetched
    Wikipedia article's own reference list / image captions) got treated by
    _extract_figure_claims as a genuine claim subject, spuriously pairing it with an unrelated
    nearby year/figure and firing a false cross-source-contradiction. A citation line is not a
    claim — it names WHERE information came from, not WHAT was claimed — so it must never
    contribute a (subject, figure) pair."""
    stripped = (line or "").strip()
    if not stripped:
        return True
    without_links = _MARKDOWN_LINK_RE.sub("", stripped)
    without_prefix = _CITATION_LINE_PREFIX_RE.sub("", without_links).strip()
    # Whatever remains after stripping markdown links and a leading bullet/number/"Source:"
    # marker: if it's short and has no real alphabetic content (just punctuation, italics
    # markers, dates, footnote refs), the line was never more than a citation to begin with.
    remaining_letters = re.sub(r'[^a-zA-Z]', '', without_prefix)
    return len(remaining_letters) < 8


def _extract_figure_claims(text: str) -> list[tuple[str, str]]:
    """Per-line (subject_phrase, figure) pairs: every real 2+-word proper-noun subject phrase on
    a line, paired with its NEAREST checkable number on that SAME line (by character distance,
    not a full cross-product) — the minimal "a claim about subject X states figure Y" unit
    find_cross_source_contradictions clusters on. Nearest-pairing, not every-pairing, because a
    line naming two different subjects with two different figures (e.g. "Sector A grew 12% while
    Sector B grew 8%") must bind each subject to ITS OWN nearby figure, not get cross-paired with
    the other subject's — a full cross-product would manufacture a fake "contradiction" between
    unrelated claims that happen to share a line. subject_phrase is lowercased for exact-match
    clustering (case rarely carries meaning for whether two mentions are "the same subject").
    Citation-only lines (_is_citation_only_line) are skipped entirely — a bibliographic
    attribution or reference-list entry is not a claim, and treating one as such is exactly the
    live-confirmed false-positive class this guard exists to close."""
    pairs = []
    for line in (text or "").splitlines():
        if _is_citation_only_line(line):
            continue
        subject_matches = []
        for m in _PROPER_NOUN_PHRASE_RE.finditer(line):
            normalized = _normalize_proper_noun_phrase(m.group(0))
            if normalized:
                subject_matches.append((normalized.lower(), m.start(), m.end()))
        figure_matches = [(m.group(0), m.start(), m.end()) for m in _NUMERIC_CLAIM_RE.finditer(line)]
        if not subject_matches or not figure_matches:
            continue
        for subject, s_start, s_end in subject_matches:
            figure, _, _ = min(
                figure_matches,
                key=lambda f: min(abs(f[1] - s_end), abs(s_start - f[2])),
            )
            pairs.append((subject, figure))
    return pairs


def find_cross_source_contradictions(report: str) -> list[str]:
    """ROADMAP Phase 2 (FEVER-style cross-source disagreement detection, Thorne et al., NAACL
    2018) — depends on Phase 1's claim segmentation (decompose_claim_segments). Distinct from
    every other check in this file: those verify a claim against ITS OWN cited source; this asks
    whether the CHOICE of source itself hid a real disagreement the reader was never told about.
    When two fetched sources report a DIFFERENT figure for the SAME named subject (e.g. one says
    Sector X grew 12%, another says 15%), and the report cites one of them for that subject
    without ever mentioning the other source's conflicting figure anywhere in the report, that's a
    silently-resolved contradiction. Not an LLM judging which figure is right — only that TWO
    fetched sources disagree and the report picked a side without saying so.

    Conservative by construction: requires an EXACT match on a real 2+-word proper-noun subject
    phrase shared between the report's citing segment and a DIFFERENT fetched source's own line,
    a figure of the SAME KIND (see _figure_kind — never a year against a percentage) that is
    numerically DIFFERENT on that other source's line for the same subject, and the differing
    figure must not already appear anywhere else in the report (a report that already surfaces
    both figures has done the job this check exists to force — not a false-flag)."""
    fetched = _fetched_url_files()
    prose = split_prose_from_sources(report)
    ref_map = parse_academic_references(report)

    per_file_claims: dict[str, list[tuple[str, str]]] = {}
    for fn in set(fetched.values()):
        content = _source_body(get_workspace_file_content(fn) or "")
        if len(content.strip()) < 50:
            continue
        per_file_claims[fn] = _extract_figure_claims(content)

    hits = []
    seen = set()
    for line in prose.splitlines():
        for segment in decompose_claim_segments(line):
            cited_files = _line_cited_files(segment, fetched, ref_map)
            if not cited_files:
                continue
            for subject, figure in _extract_figure_claims(segment):
                for other_fn, other_claims in per_file_claims.items():
                    if other_fn in cited_files:
                        continue  # comparing a source against itself proves nothing
                    for other_subject, other_figure in other_claims:
                        if other_subject != subject or other_figure == figure:
                            continue
                        if _figure_kind(other_figure) != _figure_kind(figure):
                            continue  # never compare a year against a percentage, etc.
                        if other_figure in report:
                            continue  # already surfaced elsewhere -- not silent
                        key = (subject, figure, other_figure)
                        if key in seen:
                            continue
                        seen.add(key)
                        hits.append(
                            f"'{subject}': report states {figure!r} but a DIFFERENT fetched "
                            f"source ({other_fn}) states {other_figure!r} for the same subject, "
                            f"unmentioned in the report")
    return hits


_nli_model = None
_nli_load_failed = False


def _get_nli_model():
    """Lazy, process-wide singleton — loaded at most once (measured live 2026-07-12: ~14s first
    load on CPU, ~0.03s/pair thereafter, so paying that cost once per process is the right trade,
    not once per completion-check call). Explicit device='cpu': this project budgets its VRAM
    entirely for the main research LLM — a verification classifier must never contend for it.
    Fails OPEN (returns None) on any load error (missing sentence-transformers dependency, no
    network for the first-run HuggingFace download, offline/air-gapped environment) —
    nli_unsupported_problem then no-ops rather than crashing a run that would otherwise complete
    cleanly, same philosophy as every other check in this module."""
    global _nli_model, _nli_load_failed
    if _nli_model is not None or _nli_load_failed:
        return _nli_model
    try:
        from sentence_transformers import CrossEncoder
        _nli_model = CrossEncoder("cross-encoder/nli-deberta-v3-small", device="cpu")
    except Exception:
        _nli_load_failed = True
    return _nli_model


def _select_relevant_window(claim_terms: set, source_text: str, window_size: int = 1) -> str:
    """Cheapest-adequate relevance selection before an NLI call, not whole-document NLI: this
    checkpoint class is trained on short claim/evidence pairs and degrades on long, multi-paragraph
    RAG source text. Reuses extract_salient_terms (already computed for claim_grounding_problem)
    to find the source's best-overlapping paragraph, then returns that paragraph plus its
    immediate neighbors — cheap (pure Python, no model call) and keeps the NLI call's input in the
    size class it was actually trained on."""
    paragraphs = [p for p in source_text.split("\n\n") if p.strip()]
    if not paragraphs:
        return source_text[:2000]
    best_idx, best_score = 0, -1
    for i, p in enumerate(paragraphs):
        score = len(claim_terms & extract_salient_terms(p))
        if score > best_score:
            best_idx, best_score = i, score
    lo, hi = max(0, best_idx - window_size), min(len(paragraphs), best_idx + window_size + 1)
    window = "\n\n".join(paragraphs[lo:hi])
    return window[:2500]  # hard cap regardless — a single huge paragraph must not blow past this


def _grounded_claim_pairs(report: str) -> list[tuple[str, str, str]]:
    """Shared by nli_unsupported_problem and topical_relevance_problem (ROADMAP Phase 4): every
    report prose CLAIM SEGMENT whose citation resolves to a fetched source AND already passed
    claim_grounding_problem's term-overlap check — the same evidence set, just handed to two
    different second-stage classifiers (entailment vs. topical relevance) instead of duplicating
    this matching loop twice. Returns (window, claim_line_text, display_citation) triples, where
    window is _select_relevant_window's best-overlapping passage from the source (sized for a
    short claim/evidence classifier, not whole-document input).

    SEGMENT-scoped via decompose_claim_segments, not whole-line (ROADMAP "Residual note" on the
    Phase 1 claim-level grounding upgrade, closed 2026-07-14): claim_grounding_problem was fixed
    to check each citation-bearing segment of a multi-claim line against only its OWN cited
    source, but this function still compared WHOLE-LINE terms against whichever source happened
    to be cited anywhere on the line — a multi-claim line ("Sector A grew 12% [gov](url1), while
    Sector B declined 3% [news](url2)") could pass NLI/topical-relevance on claim A's own
    entailment while silently attributing the display citation and evidence window to the WRONG
    claim if segment iteration order didn't line up, and vice versa. Same latent gap class as the
    one Phase 1 already closed for claim_grounding_problem; a line with zero or one citation
    decomposes to `[line]` unchanged, so this is a strict refinement, not a behavior change for
    the common single-citation-per-line case."""
    prose = split_prose_from_sources(report)
    fetched = _fetched_url_files()
    ref_map = parse_academic_references(report)

    pairs = []  # (window, claim_line_text, display_citation)
    for line in prose.splitlines():
        for segment in decompose_claim_segments(line):
            # See claim_grounding_problem's identical guard for why: a bare `- Source: [Title](url).`
            # attribution line is not a claim, and its own citation anchor text ("Official Eiffel
            # Tower" from "[Official Eiffel Tower website](url)") is not a checkable fact.
            if _is_citation_only_line(segment):
                continue
            display = (extract_cited_urls(segment) + [m.group(0) for m in _PARENTHETICAL_CITATION_RE.finditer(segment)])
            if not display:
                continue
            stripped_segment = re.sub(r'https?://[^\s\)\]\}"\'>【】]+', '', segment)
            segment_terms = extract_salient_terms(stripped_segment)
            if not segment_terms:
                continue
            files = _line_cited_files(segment, fetched, ref_map)
            if not files:
                continue
            for fn in files:
                content = _source_body(get_workspace_file_content(fn) or "")
                if len(content.strip()) < 50:
                    continue
                source_terms = extract_salient_terms(content)
                if not (segment_terms & source_terms):
                    continue  # term-overlap didn't pass for this file -- claim_grounding_problem's job
                window = _select_relevant_window(segment_terms, content)
                pairs.append((window, stripped_segment.strip(), display[0]))
                break  # one passing source is enough evidence to classify this segment against
    return pairs


def nli_unsupported_problem(report: str) -> str | None:
    """Second-stage grounding check beyond claim_grounding_problem's term-overlap: for each
    (window, claim, citation) triple _grounded_claim_pairs already matched, does a small NLI
    cross-encoder actually judge the claim as entailed by the source's most relevant passage — or
    does it CONTRADICT what the source says, despite the shared terms?

    Confirmed live 2026-07-12 (NVIDIA NIM gpt-oss-20b benchmark run): a report cited a real,
    fetched arXiv paper whose title was quoted with one word swapped ('Dual Causal Network' vs the
    real 'Dual Correlation Network') — enough shared capitalized-phrase/number overlap to pass
    claim_grounding_problem's zero-overlap gate outright, while the specific claim sentence isn't
    actually what the source says. Per HALT-RAG's combine-lexical-and-NLI finding: this deliberately
    runs ONLY on lines term-overlap already passed — the zero-overlap wholesale-fabrication cases
    are already caught cheaply upstream; this is the harder tier those miss.

    Flags ONLY on **contradiction**, never on **neutral** — neutral is the expected, common case
    for a claim that legitimately paraphrases or summarizes its source (NLI's precision on
    'neutral' vs. 'not explicitly stated' is poor, and treating it as a failure would over-flag
    ordinary paraphrase). Conservative by construction, same as every other check in this file.
    Fails open (returns None) if the model isn't available — see _get_nli_model. The model is
    loaded (or, when mocked/unavailable, checked) only AFTER pairs is confirmed non-empty below —
    a report with no NLI-checkable line (e.g. every citation's claim already had no checkable term
    at all, claim_grounding_problem's own job) must not pay the model-load cost for nothing."""
    pairs = _grounded_claim_pairs(report)
    if not pairs:
        return None

    model = _get_nli_model()
    if model is None:
        return None

    scores = model.predict([(w, c) for w, c, _ in pairs])
    # Verified live 2026-07-12 against this exact checkpoint: id2label == {0: 'contradiction',
    # 1: 'entailment', 2: 'neutral'} -- argmax index 0 is the only flag-worthy outcome.
    contradicted = [display for (score, (_, __, display)) in zip(scores, pairs) if score.argmax() == 0]
    if not contradicted:
        return None
    return f"nli_unsupported:{', '.join(contradicted[:3])}"


_topical_relevance_model = None
_topical_relevance_load_failed = False


def _get_topical_relevance_model():
    """Lazy, process-wide singleton — same pattern as _get_nli_model, a second CPU-only
    sentence-transformers CrossEncoder checkpoint (BAAI/bge-reranker-v2-m3, ~278M params). NOT a
    new pip dependency — sentence-transformers is already a core dependency for the NLI check
    above; this only downloads/loads a second checkpoint through the same already-installed
    library. Constructed with an explicit Sigmoid activation so .predict() returns a 0-1 relevance
    probability directly (BAAI's own documented usage for this checkpoint), rather than a raw
    logit the caller would have to transform itself. Fails OPEN (returns None) on any load error,
    same philosophy as every other check in this module."""
    global _topical_relevance_model, _topical_relevance_load_failed
    if _topical_relevance_model is not None or _topical_relevance_load_failed:
        return _topical_relevance_model
    try:
        from sentence_transformers import CrossEncoder
        import torch
        _topical_relevance_model = CrossEncoder(
            "BAAI/bge-reranker-v2-m3", device="cpu", activation_fn=torch.nn.Sigmoid())
    except Exception:
        _topical_relevance_load_failed = True
    return _topical_relevance_model


def topical_relevance_problem(report: str) -> str | None:
    """ROADMAP Phase 4: third-stage grounding check, layered after claim_grounding_problem
    (lexical term-overlap) and nli_unsupported_problem (entailment/contradiction) — a cross-encoder
    reranker scoring (claim, source-passage) pairs for TOPICAL relevance, not entailment or
    lexical overlap. Catches a real, documented gap neither of those two layers can: the GOA (the
    Grasshopper Optimization Algorithm) vs. Goa (the Indian state) acronym collision (ROADMAP
    "Findings from live testing") — 'GOA'/'Goa' term-overlap passes outright, and NLI entailment
    can score neutral/borderline since the sentences aren't strictly contradictory (an EV-policy
    sentence about Goa doesn't CONTRADICT a claim about an algorithm, it's just about something
    else entirely) — only a semantic relevance judgment catches that the source is about a
    completely different subject than the claim.

    Reuses the exact same evidence set as the NLI check (_grounded_claim_pairs — a line whose
    citation already passed term-overlap), so this never re-flags what the cheaper upstream checks
    would already catch on their own; it only adds a check those two structurally cannot make.
    Conservative threshold (default 0.1, `settings.grounding_check.topical_relevance_threshold`):
    bge-reranker-v2-m3 scores a genuinely relevant pair close to 1.0, so only a CLEARLY unrelated
    pair (near 0) fires — a merely thin or terse-but-relevant match must not trip this. Fails open
    (returns None) if the model isn't available, same as every other check in this module."""
    pairs = _grounded_claim_pairs(report)
    if not pairs:
        return None

    model = _get_topical_relevance_model()
    if model is None:
        return None

    threshold = config.cfg.get("settings", {}).get("grounding_check", {}).get(
        "topical_relevance_threshold", 0.1)
    # bge-reranker convention is (query, passage) -- the CLAIM is the query, the source window is
    # the passage being judged relevant or not to it.
    scores = model.predict([(c, w) for w, c, _ in pairs])
    irrelevant = [display for (score, (_, __, display)) in zip(scores, pairs) if score < threshold]
    if not irrelevant:
        return None
    return f"topical_mismatch:{', '.join(irrelevant[:3])}"


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


_FINDINGS_ENTRY_HEADING_RE = re.compile(r'^###\s*\[[^\]]*\]\(([^)]+)\)', re.MULTILINE)


def partially_ungrounded(content: str) -> str | None:
    """Per-entry gate for findings.md (Pass 1), stricter than fully_ungrounded's wholesale check —
    confirmed live 2026-07-19: a findings.md that was 40% fabricated (6/15 entries citing an
    unfetched URL as their OWN primary source) passed fully_ungrounded cleanly ('at least one
    citation is real' was satisfied by the other 9), then poisoned Builder's downstream rewrite so
    badly it discarded almost all real content rather than risk keeping a fake one — the
    'legitimately-mixed Pass-1 notes' fully_ungrounded was built to tolerate turned out, in
    practice, to make the final report worse than a stricter earlier gate would have.

    Deliberately checks ONLY each entry's own HEADING url (the '### [Title](URL)' line
    FindingsWriter's real template produces one per finding — see _build_findings_source_material's
    real output format) — NOT every URL mentioned anywhere in an entry's summary body, which may
    legitimately reference other sources found IN the primary source's own text without having
    fetched them itself. fully_ungrounded's own original reasoning (extra unfetched snippet URLs in
    prose are normal, not fabrication) still holds at the body-text level; this only tightens the
    ENTRY'S OWN claimed source, the one thing that should never be unverifiable."""
    headings = _FINDINGS_ENTRY_HEADING_RE.findall(content)
    if not headings:
        return None  # no per-entry headings at all -- fully_ungrounded's own no_urls case covers this
    fetched = {entry["url"].rstrip('/') for entry in get_fetched_urls()}
    bad = [
        u for u in headings
        if u.rstrip('/') not in fetched and not any(_urls_prefix_match(u.rstrip('/'), f) for f in fetched)
    ]
    if not bad:
        return None
    return f"unverified_entry_sources:{', '.join(bad[:3])}"


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

    if gc_cfg.get("nli_verify", True):
        problem = nli_unsupported_problem(content)
        if problem:
            return problem

    if gc_cfg.get("topical_relevance_check", True):
        problem = topical_relevance_problem(content)
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
