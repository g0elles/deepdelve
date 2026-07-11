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


def extract_cited_urls(text: str) -> list[str]:
    urls = re.findall(r'https?://[^\s\)\]\}"\'>]+', text or "")
    return [u.rstrip('.,;:\'")]}') for u in urls]


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
    compares against what the report actually CLAIMS rather than its own citation list."""
    m = re.search(r'^#{0,3}\s*(sources|references)\s*:?\s*$', report or "", re.IGNORECASE | re.MULTILINE)
    return report[:m.start()] if m else (report or "")


_SOURCE_LABEL_RE = re.compile(r'\bSource:?\b', re.IGNORECASE)
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


def claim_grounding_problem(report: str) -> str | None:
    """Content-level check beyond URL-presence: for a citation that WAS actually fetched, does its
    content share any checkable fact (number, version, proper noun) with what the report claims?
    Deliberately conservative (zero overlap only) to keep false positives rare."""
    prose = split_prose_from_sources(report)
    report_terms = extract_salient_terms(prose)
    if not report_terms:
        return None

    cited = extract_cited_urls(report)
    fetched_list = get_fetched_urls()
    fetched = {entry["url"].rstrip('/'): entry["filename"] for entry in fetched_list}

    unsupported = []
    for u in cited:
        key = u.rstrip('/')
        filename = fetched.get(key)
        if not filename:
            filename = next((f for orig, f in fetched.items() if key.startswith(orig) or orig.startswith(key)), None)
        if not filename:
            continue

        source_content = get_workspace_file_content(filename) or ""
        if len(source_content.strip()) < 50:
            continue

        source_terms = extract_salient_terms(source_content)
        if not (report_terms & source_terms):
            unsupported.append(u)

    if not unsupported:
        return None
    return f"claim_unsupported:{', '.join(unsupported[:3])}"


async def real_grounding_problem(content: str) -> str | None:
    """Cross-references every URL cited in `content` against URLs the engine actually saw
    fetch_url_to_workspace fetch this run. A URL not in that verified set is ALWAYS a grounding
    problem — this is the primary, hard gate. Returns a human-readable problem description, or
    None if every citation is verified (or there are no citations to check)."""
    cited = extract_cited_urls(content)
    if not cited:
        return "no_urls"

    fetched = {entry["url"].rstrip('/') for entry in get_fetched_urls()}
    unverified = [u for u in cited if u.rstrip('/') not in fetched and not any(u.rstrip('/').startswith(f) or f.startswith(u.rstrip('/')) for f in fetched)]

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

    if gc_cfg.get("non_url_citation_check", True):
        non_url = find_non_url_citations(content)
        if non_url:
            return f"non_url_citation:{'; '.join(non_url[:3])}"

    if gc_cfg.get("content_level_check", True):
        return claim_grounding_problem(content)

    return None
