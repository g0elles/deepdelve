# GROUNDING_CHECKS tier -- is the artifact's CONTENT actually grounded in real fetched sources?
# (citation accuracy, breadth, self-consistency), as opposed to COMPLETION_CHECKS' structural/
# process tier (engine/completion_checks_structural.py). Split out of the former single
# engine/completion_checks.py (2098 lines, 2026-09-07 ponytail-audit finding) along the SAME
# boundary COMPLETION_CHECKS/GROUNDING_CHECKS already draws in engine/completion.py -- a pure move,
# no behavior change: every function below is unchanged from its prior body. Shared Verdict/Ctx/
# _citation_format_reminder stay in engine/completion_checks.py (now just the small shared core).
#
# engine/completion.py still imports every name below (for real internal use -- building
# GROUNDING_CHECKS itself -- not just re-export) and re-exports it under `engine.completion` for
# test_structural_checks.py/finetune/* exactly as before this split; that external import surface
# does not change. A handful of local (function-body) `from engine.completion import ...` lines
# below call helpers that stay in completion.py (_capped/_consecutive_occurrences/_dedupe_findings/
# _uncited_task_names/_find_propagated_bad_content/_CUTOFF_ONLY_SUMMARY_RE) -- see the original
# completion_checks.py history (git log) for why those stayed put.
#
# 2026-09-07 ponytail fix, same session as the split: the 10 `check_*` functions below that gate on
# `gp.startswith("...")` now share one `_gp()` helper instead of repeating
# `gp = ctx.grounding_problem; if not (gp and gp.startswith(...)): return None` verbatim each time.
import re
import unicodedata
from typing import Optional

import config
from tools import get_workspace_file_content
from utils.run_state import get_fetched_urls
from utils.grounding import (
    split_into_heading_sections, find_cross_source_contradictions,
    excluded_topic_semantic_hit, _NAMED_REGULATION_RE, _REGULATION_ID_RE,
    normalize_dashes, find_acronym_regulation_matches,
)
from engine.orchestrator import (
    _extract_excluded_topics, _content_word_overlap, _extract_required_facets,
    _extract_required_item_type,
)
from engine.completion_checks import Ctx, Verdict, _citation_format_reminder


def _gp(ctx: Ctx, prefix: str) -> Optional[str]:
    """Shared gate every simple grounding check below uses: ctx.grounding_problem, only if it
    starts with this check's own problem prefix, else None. Extracted 2026-09-07 (ponytail-audit
    finding) -- the same 2-line `gp = ctx.grounding_problem; if not (gp and gp.startswith(...)):
    return None` was hand-repeated in 10 check functions."""
    gp = ctx.grounding_problem
    return gp if (gp and gp.startswith(prefix)) else None

def check_report_underuses_findings(ctx: Ctx) -> Optional[Verdict]:
    """Builder's own version of check_thin_coverage's diagnosis, one stage downstream: a report
    can be perfectly GROUNDED (every citation it does make traces to a real fetch) while still
    silently abandoning most of findings.md's real, distinct sources -- the exact evidence-
    abandonment pattern this project spent 2026-07-22 fixing for FindingsWriter (writer_gate_ctx,
    _collapse_multi_url_task_findings), confirmed live to recur one layer downstream: a run with a
    genuinely diverse, 15-entry findings.md (heuristic-algorithm papers AND Colombian cultural
    sources, covering both facets the query asked for) produced a final_report.md that only ever
    cited the Colombian cluster -- the entire heuristic-algorithms half, present and citable in
    findings.md, never appears anywhere in the report. None of the existing GROUNDING_CHECKS catch
    this: they all verify whether a citation the report DOES make is real, never whether the
    report used enough of what was actually available.

    Model-independent structural signal, same shape as check_thin_coverage: findings.md's own
    distinct cited URLs (extract_cited_urls on its raw text -- the same extractor every grounding
    check already uses, so "cited" here means the exact same thing it means everywhere else in
    this project) vs. final_report.md's own cited URLs. Fires when a MAJORITY of findings.md's
    real sources never made it into the report (ratio below threshold, default 0.5) AND there are
    enough of them for that ratio to mean something (min_sources, default 3) -- a findings.md with
    only 1-2 real sources being used at ratio 1.0 or 0.5 is expected, not evidence of abandonment."""
    cov_cfg = config.get_setting("report_coverage_check", {})
    if not cov_cfg.get("enabled", True):
        return None
    if "findings.md" not in ctx.files or ctx.content is None:
        return None
    from utils.grounding import extract_cited_urls, _is_null_finding_summary
    findings_urls = set(extract_cited_urls(get_workspace_file_content("findings.md") or ""))
    # Exclude URLs whose EVERY run_state.data["findings"] entry is a null/failed-extraction
    # summary — see _is_null_finding_summary's own docstring. A URL absent from
    # run_state.data["findings"] entirely (e.g. this check running against a findings.md written
    # by a path that doesn't populate that list) is NOT excluded here — absence of tracking data
    # is not evidence of failure, only an explicit null summary is, so this can only ever shrink
    # findings_urls, never silently disable the whole check.
    summaries_by_url: dict[str, list] = {}
    for f in ctx.run_state.data.get("findings", []):
        u = (f.get("source_url") or "").strip().rstrip('/')
        if u.startswith("http"):
            summaries_by_url.setdefault(u, []).append(f.get("summary"))
    null_urls = {u for u, sums in summaries_by_url.items() if all(_is_null_finding_summary(s) for s in sums)}
    if null_urls:
        findings_urls = {u for u in findings_urls if u.rstrip('/') not in null_urls}
    min_sources = cov_cfg.get("min_sources", 3)
    if len(findings_urls) < min_sources:
        return None
    report_urls = set(extract_cited_urls(ctx.content))
    unused = sorted(findings_urls - report_urls)
    if not unused:
        return None
    ratio = (len(findings_urls) - len(unused)) / len(findings_urls)
    threshold = cov_cfg.get("threshold", 0.5)
    # `>` not `>=` (2026-08-20 live incident): a ministral-3:8b run with 4 real findings.md
    # sources dropped an entire city (2 of 4) from a two-city comparison query, landing EXACTLY
    # on ratio == 0.5 == threshold -- the old `>=` treated that boundary as a pass and let a
    # report missing half its requested comparison through with no report_underuses_findings
    # verdict ever firing. `>` means landing exactly on the threshold now fails closed.
    if ratio > threshold:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "report_underuses_findings":
            prior_same += 1
        else:
            break

    # 2026-07-31 (research finding, not a live incident): both branches already asked conceptually
    # for an ADDITION ("actually add sections") but never named the one tool built exactly for a
    # scoped addition -- edit_workspace_file (BUILDER_INSTRUCTIONS, src/prompts.py, explicitly
    # reserved for "a small targeted fix... it can't accidentally drop or alter anything else in
    # the file"). Literature (arXiv:2507.02778, "Self-Correction Bench" -- the "self-correction
    # blind spot": models are measurably worse at fixing errors in their OWN prior output than
    # identical errors framed as external input; corrected 2026-08-17 from a wrong arXiv:2406.01297
    # attribution -- that paper is a methodology-critique survey with no such finding, see
    # RESEARCH.md §18b) suggests a full write_workspace_file regeneration -- which the old wording
    # implicitly invited by never specifying a narrower tool -- is exactly the weakest operation
    # here. Now explicit on BOTH branches, not just the escalated one, so the narrower ask is tried
    # from the first attempt rather than only after a full rewrite has already failed once.
    unused_list = ", ".join(unused[:5])
    if prior_same == 0:
        directive = (
            f"'{ctx.req_artifact}' only cites {len(findings_urls) - len(unused)} of "
            f"{len(findings_urls)} real sources actually present in findings.md — the rest "
            f"({unused_list}) are real, fetched, and available but never appear anywhere in the "
            f"report. Use edit_workspace_file to insert a new section covering ONLY these neglected "
            f"sources — do not rewrite or touch any other part of the report. If findings.md covers "
            f"multiple distinct angles, the report must reflect all of them, not just one."
        )
    else:
        directive = (
            f"'{ctx.req_artifact}' STILL neglects real sources from findings.md after a prior "
            f"warning ({unused_list}). Use edit_workspace_file to insert a new section covering "
            f"ONLY these neglected sources — do not rewrite or touch any other part of the report."
        )
    return Verdict(
        "report_underuses_findings",
        f"'{ctx.req_artifact}' cites only {len(findings_urls) - len(unused)}/{len(findings_urls)} of findings.md's real sources ({unused_list} never cited). Pushing agent to incorporate the rest.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    )


def _facet_coverage(ctx: Ctx) -> tuple[dict[str, set], list[str]]:
    """by_task: {task_name: {real URLs that survived into findings.md for that task}}, dropped:
    sorted task names among by_task with ZERO of their URLs cited anywhere in ctx.content
    (final_report.md). Factored out of check_report_underuses_evidence (2026-08-01) so its verdict
    and _dispatch_per_facet_builder_fix's real per-facet URL sets can never drift onto two
    different notions of "dropped" -- one computation, read twice in the same completion-check
    iteration (check's ctx, then the dispatch branch's same ctx), not duplicated.

    `_is_citable_finding` exclusion (2026-08-16 live incident, sibling bug to `_is_null_finding_
    summary` below): this loop used to ONLY skip a finding via `_is_null_finding_summary` -- "a
    fetch that yielded nothing extractable" -- which does NOT catch a finding carrying a
    `[SYSTEM VERIFICATION WARNING...]`/`[SYSTEM RELEVANCE WARNING...]` marker (real-looking but
    FABRICATED or off-topic content; `_is_citable_finding`'s own, stricter exclusion). That gap let
    this function and `check_task_verification_flagged`'s ledger (`_is_citable_finding`-based)
    disagree about the SAME task in the SAME run: the ledger correctly flags a task as fabricated
    and tells the Planner to acknowledge the gap and stop, while this function -- feeding both
    `check_report_underuses_evidence`'s verdict AND `_dispatch_per_facet_builder_fix`'s own
    per-facet URL set -- still counted that task's fabricated finding as "real surviving evidence"
    and told Builder to go cite it. Confirmed live: a run's `digital_nomad_visa_portugal` was
    flagged fabricated/quota-exhausted at attempt 0, then `report_underuses_evidence` told Builder
    at attempt 3 it had "real surviving sources" for that exact task and to cite them -- directly
    contradicting the acknowledge-the-gap directive one attempt earlier, and a plausible
    contributor to the citation-accuracy churn (`uncited_claims`/`quote_paraphrased`) that followed
    before the whole facet was silently dropped from the final report with no gap ever disclosed."""
    from utils.grounding import extract_cited_urls, _urls_prefix_match, _is_null_finding_summary
    from engine.completion import _is_citable_finding
    findings_urls = {u.rstrip('/') for u in extract_cited_urls(get_workspace_file_content("findings.md") or "")}
    report_urls = {u.rstrip('/') for u in extract_cited_urls(ctx.content or "")}

    by_task: dict[str, set] = {}
    for f in ctx.run_state.data.get("findings", []):
        if f.get("depth") != 1:
            continue
        name = f.get("task_name")
        url = (f.get("source_url") or "").strip().rstrip('/')
        if not name or not url.startswith("http"):
            continue
        if url not in findings_urls and not any(_urls_prefix_match(url, f2) for f2 in findings_urls):
            continue  # never reached findings.md -- check_findings_underuses_evidence's job.
        if _is_null_finding_summary(f.get("summary")):
            continue  # a fetch that yielded nothing extractable isn't real surviving evidence --
                       # see _is_null_finding_summary's own docstring. A URL with a DIFFERENT,
                       # real-content finding entry elsewhere in this same loop still gets added
                       # normally, since this only skips THIS null entry, not the URL as a whole.
        if not _is_citable_finding(f):
            continue  # fabricated/off-topic (SYSTEM WARNING marker) -- see this function's own
                      # docstring above. Must never be counted as "real surviving evidence" to
                      # recover, or this directly contradicts check_task_verification_flagged's
                      # own acknowledge-the-gap directive for the same task.
        by_task.setdefault(name, set()).add(url)

    dropped = sorted(
        name for name, urls in by_task.items()
        if not any(u in report_urls or any(_urls_prefix_match(u, r) for r in report_urls) for u in urls)
    )
    return by_task, dropped


def check_report_underuses_evidence(ctx: Ctx) -> Optional[Verdict]:
    """check_findings_underuses_evidence's own per-TASK diagnosis, one stage further downstream:
    that check guarantees every covered top-level task has at least one real URL surviving into
    findings.md, but nothing then guarantees Builder's own selection FROM findings.md represents
    every task either. check_report_underuses_findings' flat citation-count ratio can clear its
    threshold while every surviving citation comes from a single task -- a report can be
    well-formatted, fully grounded, AND pass the ratio check while still reducing a multi-facet
    query to one facet.

    Confirmed live 2026-07-28 (RESEARCH.md Sec.14h): a `gpt-oss:20b` run's findings.md correctly
    covered both a heuristic-algorithms task and a Colombia-cultural task (no upstream check fired).
    final_report.md dropped the heuristic-algorithms task entirely in favor of an off-topic citation,
    yet still cited ~57% of findings.md's total URLs by RAW COUNT (Colombia had more sources) --
    comfortably above check_report_underuses_findings' 50% ratio threshold. The dropped 43% happened
    to be the query's most relevant content; the check has no way to see that, since it only counts,
    never groups by task. Same root cause the 2026-07-26 catalog review (session_status/CURRENT.md)
    named as the single clearest counter-example to a pure budget-pressure theory: this recurs even
    on the trusted baseline model, with no quota/timeout signal, in small balanced runs.

    Deliberately per-TASK like its sibling, not a second ratio: reuses the exact same ground truth
    (run_state.data["findings"], depth==1) filtered to URLs that check_findings_underuses_evidence
    has already confirmed survive into findings.md -- a task whose real URLs never reached
    findings.md at all is that check's problem, not this one. Fires when at least one task with
    real, surviving findings.md coverage has ZERO of its URLs cited anywhere in the report.

    Capped via the shared _capped helper (2026-07-31, found by the same systematic audit that
    caught check_propagated_ungrounded_content -- not a live incident, the audit caught it first).
    "report_underuses_evidence" is in neither _BUILDER_FIXABLE_PROBLEMS nor _FINDINGS_WRITER_
    FIXABLE_PROBLEMS (its own combined-instruction Planner-mediated fix got a clean negative live
    result -- see run_completion_check's own report_underuses_evidence branch and
    _dispatch_per_facet_builder_fix for the per-facet dispatch that replaced it), and although this
    check is the declared _STARVATION_YIELD_TARGETS entry for its sibling
    check_report_underuses_findings, it can also win the normal first-match scan entirely on its
    own (report_underuses_findings' ratio can clear while this check's own per-task gap remains) --
    at which point, uncapped, it could starve check_not_grounded (the generic catch-all, last in
    GROUNDING_CHECKS) the same way its sibling used to starve it."""
    cov_cfg = config.get_setting("report_evidence_check", {})
    if not cov_cfg.get("enabled", True):
        return None
    if "findings.md" not in ctx.files or "final_report.md" not in ctx.files or ctx.content is None:
        return None
    by_task, dropped = _facet_coverage(ctx)

    min_tasks = cov_cfg.get("min_tasks", 2)
    if len(by_task) < min_tasks:
        return None
    if not dropped:
        return None

    # Uses the shared _consecutive_occurrences instead of a hand-rolled loop (see that function's
    # docstring) -- one more duplicate of this exact loop shape found during the 2026-07-31 audit.
    from engine.completion import _capped, _consecutive_occurrences
    prior_same = _consecutive_occurrences(ctx.run_state, "report_underuses_evidence")

    # 2026-07-31: same treatment as check_report_underuses_findings' sibling directive (see its own
    # comment for the full reasoning) -- explicit edit_workspace_file instruction on BOTH branches
    # instead of an implied "add sections" that never named the narrow-scope tool.
    dropped_list = ", ".join(f"'{n}'" for n in dropped[:5])
    if prior_same == 0:
        directive = (
            f"'{ctx.req_artifact}' has NO citations at all for task(s) {dropped_list}, even though "
            f"findings.md has real, surviving sources for them. This looks like one research angle "
            f"crowded out another during synthesis, not thin coverage of a single topic. Use "
            f"edit_workspace_file to insert a new section covering ONLY task(s) {dropped_list}'s "
            f"real sources from findings.md — do not rewrite or touch any other part of the report."
        )
    else:
        directive = (
            f"'{ctx.req_artifact}' STILL has no citations for task(s) {dropped_list} after a prior "
            f"warning. Use edit_workspace_file to insert a new section covering ONLY task(s) "
            f"{dropped_list}'s real sources from findings.md — do not rewrite or touch any other "
            f"part of the report."
        )

    return _capped(ctx, "report_underuses_evidence", Verdict(
        "report_underuses_evidence",
        f"'{ctx.req_artifact}' has zero citations for task(s) {dropped_list}, despite findings.md having real surviving sources for them. Pushing agent to cover every task, not just one.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ))


_NEAR_DUP_SECTION_OVERLAP_THRESHOLD = 0.6


def find_duplicate_report_sections(report: str) -> list[str]:
    """Near-duplicate heading-delimited SUBSECTIONS within the same document — self-consistency,
    not cross-source citation grounding (unlike every other check in this file/utils/grounding.py).
    Confirmed live (2026-08-17): a Builder Fix-pass's own `edit_workspace_file` call anchored
    `old_string` on a bare section heading alone, then wrote a `new_string` that retyped the
    heading's own PRE-EXISTING content verbatim before appending a new, more specific subsection
    next to it — since the original content was never part of `old_string`, it stayed in place,
    and the retyped copy landed right in front of it. `final_report.md` ended up with two
    near-identical "### Mexico City" / "### Mexico City – Central Districts" sections citing the
    same figures. `BUILDER_INSTRUCTIONS`/`FINDINGS_WRITER_INSTRUCTIONS` were given the matching
    prompt-level fix the same day (anchor edits on content boundaries, never retype existing
    content into `new_string`) — this is the structural backstop, since this project's own history
    (`ARCHITECTURE.md`, the no-progress-guard writeup, `writer_gate_ctx`'s own docstring) is
    consistently skeptical that a prompt-only fix holds reliably on a small local model.

    Reuses `_content_word_overlap` — the same metric `_looks_like_renamed_task` uses for the
    identical underlying question ("do these two things restate the same content"), just applied
    to report SECTIONS instead of task instructions. Scoped to h3+ SUBSECTIONS only (via
    `split_into_heading_sections`, this project's existing h1-h3 section-detection helper) —
    comparing every section against every other, including h1/h2 or the headingless intro/
    conclusion, would false-positive on the normal pattern of a summary section legitimately
    restating figures already covered in detail elsewhere. Returns the heading text of each
    duplicate section found (the SECOND of a matched pair — the one to merge into the first, not
    delete outright, since a Fix pass needs to know which two to reconcile, not just that a
    duplicate exists)."""
    sections = split_into_heading_sections(report or "")
    headed = []
    for sec in sections:
        if not sec:
            continue
        heading_line = sec[0].strip()
        if not re.match(r'#{3,}\s', heading_line):
            continue  # only h3+ subsections -- see docstring for why h1/h2/intro are excluded
        headed.append((heading_line, "\n".join(sec)))
    dups = []
    for i, (_, text_a) in enumerate(headed):
        for heading_b, text_b in headed[i + 1:]:
            if _content_word_overlap(text_a, text_b) > _NEAR_DUP_SECTION_OVERLAP_THRESHOLD:
                dups.append(heading_b)
                break
    return dups


def find_duplicate_heading_text(report: str) -> list[str]:
    """Sibling signal to find_duplicate_report_sections' content-similarity check, for the INVERSE
    failure shape: the SAME heading text appearing twice with DIFFERENT content under each
    occurrence, at ANY heading level (not just h3+). Confirmed live (gemma4:e4b bake-off,
    2026-08-28): a report had '## 2. Key Findings' appear twice, each followed by a different,
    non-overlapping set of subsections (A/B/C the first time, A/B/C/D the second) — the same
    edit_workspace_file "retyped an existing heading, appended new content after it" mechanism
    find_duplicate_report_sections' own docstring already documents, just landing one level higher
    (h2, not h3) than that check's h3+-only scope, and with genuinely DIFFERENT content under each
    copy — so the content-similarity comparison there would never have caught it even without the
    h3+ scoping.

    Safe to run at EVERY heading level, unlike the content-similarity check: an exact repeated
    heading string is a much narrower, stronger signal than content overlap — a legitimate
    summary-then-detail report pattern always uses two DIFFERENT heading texts (e.g. "Executive
    Summary" vs "Detailed Findings"), never the identical string twice, so this can't false-positive
    on that pattern the way broadening the content-similarity check's own h3+ scope could have.

    Case/whitespace-normalized, and strips a leading numbered/lettered list prefix ("2. ", "A. ")
    so "## 2. Key Findings" vs a later, renumbered "## 3. Key Findings" with the same title still
    counts as the same duplicate heading."""
    sections = split_into_heading_sections(report or "")
    seen: dict[str, str] = {}
    dups = []
    for sec in sections:
        if not sec:
            continue
        heading_line = sec[0].strip()
        m = re.match(r'#{1,6}\s+(.*)', heading_line)
        if not m:
            continue
        norm = re.sub(r'^[0-9A-Za-z][.)]\s*', '', m.group(1).strip().lower())
        if not norm:
            continue
        if norm in seen:
            dups.append(heading_line)
        else:
            seen[norm] = heading_line
    return dups


def check_duplicate_report_sections(ctx: Ctx) -> Optional[Verdict]:
    """See find_duplicate_report_sections' own docstring for the live incident this exists for.
    Also checks find_duplicate_heading_text (2026-08-28) for the inverse failure shape — see its
    own docstring."""
    if not config.get_setting("duplicate_section_check", {}).get("enabled", True):
        return None
    if ctx.content is None:
        return None
    dups = find_duplicate_report_sections(ctx.content) or find_duplicate_heading_text(ctx.content)
    if not dups:
        return None
    dup_list = ", ".join(f"'{h}'" for h in dups[:3])
    return Verdict(
        "duplicate_report_sections",
        f"`{ctx.req_artifact}` has near-duplicate sections ({dup_list}) restating the same content under different headings.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' has near-duplicate sections that restate the same "
        f"figures/claims under different headings ({dup_list}) — most likely from an edit that "
        f"retyped existing content instead of only appending new material. Use edit_workspace_file "
        f"to MERGE the duplicate section(s) into the first one covering that subject and remove the "
        f"redundant heading(s) — do not just delete the new content, keep whatever the duplicate "
        f"section added that the first one didn't already have.{_redelegate_directive(ctx)}",
    )


def _facet_token_match(facet: frozenset, tokens: frozenset) -> bool:
    """Shared token-match rule for check_missing_specific_item_per_facet's entity association --
    exact match OR a shared >=4-char prefix in either direction, same convention
    check_missing_query_facet's own _token_covered uses for the country/demonym relationship
    ('German' vs 'Germany')."""
    return any(
        tok == e or (len(tok) >= 4 and len(e) >= 4 and (tok.startswith(e) or e.startswith(tok)))
        for tok in facet for e in tokens
    )


# Bare capitalized-word extraction for check_missing_specific_item_per_facet below -- deliberately
# NOT `_instruction_entities` (orchestrator.py), whose "skip a sentence's first word" heuristic
# exists to avoid extracting generic nouns from freeform task instructions but is actively wrong
# here: a regulation-naming sentence routinely starts with the entity itself ("Germany's Renewable
# Energy Sources Act..."), and that heuristic would skip the exact mention this check needs.
# Since the caller only ever compares against known facet tokens already pulled from the query,
# over-collecting generic capitalized words is harmless. Applied only to markdown-emphasis-stripped
# text (see check_missing_specific_item_per_facet's own docstring for why "**Germany**" otherwise
# never matches at all).
_CAPITALIZED_WORD_RE = re.compile(r'\b[A-Z][a-zA-Z]{2,}\b')


def _facet_mentions(text: str) -> frozenset:
    """Diacritic-folded capitalized-word tokens found ANYWHERE in text, in no particular order --
    see _CAPITALIZED_WORD_RE above for why this doesn't reuse _instruction_entities."""
    return frozenset(
        unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode()
        for w in _CAPITALIZED_WORD_RE.findall(text)
    )


def _facet_for_regulation_match(match_text: str, preceding_text: str, facets: list) -> Optional[int]:
    """Index into `facets` of whichever required facet a regulation match is actually about --
    used to attribute a regulation found in a SHARED/comparative section (one discussing more than
    one facet) to the specific entity it's actually about, instead of crediting every facet the
    section happens to mention.

    Checks the match's OWN text first (e.g. "German Renewable Energy Sources Act" -- the country
    adjective is often the regulation NAME's own leading word, not external context at all), then
    falls back to the LAST (closest, rightmost) capitalized word in the preceding text. Confirmed
    both paths necessary live, in that order, on the SAME report: (1) a real intro sentence
    ("Germany's Renewable Energy Sources Act (EEG) sets binding capacity targets, while Japan's
    FIT policy guarantees...") mentions both entities, but only Germany's is adjacent to the
    regulation name -- "which entities does this section mention" (the first fix attempt) wrongly
    credited Japan too, fixed by preceding-text proximity; (2) a markdown bullet
    "[German Renewable Energy Sources Act](url)" has NO entity in its preceding text at all (the
    entity is the match's own leading adjective) -- proximity alone then fell back past it to
    "Japan" from an unrelated EARLIER sentence, wrongly crediting Japan a second, different way.
    Returns None if neither the match nor the preceding text names a required facet."""
    match_tokens = _facet_mentions(match_text)
    for i, f in enumerate(facets):
        if _facet_token_match(f, match_tokens):
            return i
    matches = list(_CAPITALIZED_WORD_RE.finditer(preceding_text))
    if not matches:
        return None
    nearest = _facet_mentions(matches[-1].group())
    for i, f in enumerate(facets):
        if _facet_token_match(f, nearest):
            return i
    return None


def check_missing_specific_item_per_facet(ctx: Ctx) -> Optional[Verdict]:
    """A different axis from every other grounding check here: those all verify whether content
    that ALREADY EXISTS in the report is accurately cited; this instead asks whether the report
    satisfied an EXPLICIT per-facet instruction the query itself stated. Confirmed live
    (2026-08-30): a Germany/Japan renewable-policy report was fully grounded (every claim verified
    against its cited source by hand) and passed check_missing_query_facet (both entities genuinely
    researched) -- but the query explicitly asked for "citing at least one specific regulation for
    each country," and the Japan section only ever cited renewable-share TARGETS, never a named
    regulation. Not a fabrication or a missing-research problem: the run had already fetched a
    source describing Japan's real 2012 FIT law, it just never made it into findings.md, so no
    citation-accuracy check had anything to flag.

    Deliberately narrow, same discipline as check_missing_query_facet: only engages when the query
    states an EXPLICIT per-item requirement (_extract_required_item_type) AND unambiguously
    enumerates 2+ facets (_extract_required_facets, reused unchanged -- no need to re-derive the
    entity list).

    TWO-TIER association per section, refined across three live-replay iterations against real
    reports (2026-08-30), not designed up front:
    1. A section whose HEADING names EXACTLY ONE required facet is a "dedicated" section -- trust
       its WHOLE body for a regulation mention, no per-line entity co-occurrence required (a
       dedicated section routinely refers to its own subject implicitly, e.g. "the Act mandates
       ..." with no repeated country name nearby).
    2. A section whose heading names ZERO or 2+ facets (a shared intro/comparison/conclusion
       section) requires PROXIMITY: each regulation match found in it is attributed via
       `_facet_for_regulation_match` -- the match's OWN text first (a regulation's name often
       carries its country as a leading adjective, e.g. "German Renewable Energy Sources Act"),
       then the nearest preceding capitalized word -- not every facet the section discusses.

    Tier 2 exists because tier 1 alone (the first fix attempt: scan a section only if its HEADING
    names the facet) wrongly flagged Germany as missing -- its regulation name lived only in a
    shared "## Introduction" section's own prose, never restated under "## Germany"'s own heading.
    Widening tier 1 to "any section MENTIONING the facet, anywhere" (the second fix attempt) then
    overcorrected: that same Introduction section discusses both Germany and Japan, so Germany's
    actual regulation mention got wrongly credited to Japan too, silencing the check entirely on
    the same real report it was built to catch. Proximity attribution (the third fix attempt) fixed
    that, but a FOURTH live report then found a second contamination path the same proximity logic
    still missed: a markdown bullet "[German Renewable Energy Sources Act](url)" has no entity
    at all in its preceding text (the entity is the match's own leading word), so proximity fell
    through past it to "Japan" from an unrelated earlier sentence. Checking the match's own text
    first (now in `_facet_for_regulation_match`) closes that gap.

    A facet mentioned in NO section at all (checked via `_facet_mentions` over the whole
    document) is skipped, not flagged -- that's check_missing_query_facet's job (total omission).

    Markdown emphasis (`*`/`_`) is stripped before all entity extraction: found live that a bold
    mention ("**Germany**") is one token to a plain capitalized-word regex, and a token starting
    with `**` never matches `[A-Z][a-zA-Z]{2,}` at all -- a heading's "## Introduction" only worked
    by accident, since the space there keeps "##" and "Introduction" as separate tokens.

    SIXTH fix (2026-09-08, live-repro'd from the docstring's own prior "not exhaustive" note,
    not yet from a real report): a section with NO capitalized entity word anywhere in its own
    body (a bare regulation table, entities established only in an earlier section) left
    `_facet_for_regulation_match` nothing to attribute to, wrongly flagging entities that WERE
    genuinely covered. Closed by an entity-free-section fallback: only engages when the section
    mentions no facet at all (so there's no ambiguous nearby entity to guess wrong, unlike every
    prior misattribution bug here) and the unattributed-match count exactly equals the
    uncovered-facet count; an unequal ratio still falls through unresolved rather than guessing.

    SEVENTH fix (2026-09-09, live incident: a real Germany/Japan run named Germany's own
    regulation as "Erneuerbare-Energien-Gesetz (EEG)" directly under its dedicated heading, but
    _NAMED_REGULATION_RE/_REGULATION_ID_RE only recognize English/Spanish regulation-noun
    keywords -- neither has German "Gesetz", so the section matched nothing even though the
    entity plainly named a real law right there). Rather than add "Gesetz" (the next language
    to hit this is only a matter of time -- see grounding.py::find_acronym_regulation_matches'
    own docstring), added a language-agnostic acronym-initials check: any "(ACRONYM)" whose
    letters are literally the initials of the capitalized words right before it counts as a
    named regulation too, regardless of what language named it. Needed dash normalization
    alongside it (grounding.py::normalize_dashes) since the live report used Unicode dashes
    (U+2011/U+2013) that made "Germany" and the regulation name read as one unbroken phrase,
    which would have polluted the initials count with the country name itself.

    Still not exhaustive: an entity-free section (SIXTH fix) with an unequal match/uncovered-
    facet count still can't be resolved and stays unattributed; a named regulation with NO
    acronym at all, in a language whose word for "law" isn't in the keyword list (e.g. a bare
    French "Loi relative a..." never abbreviated), still isn't caught by either tier. Calibrate
    against more real reports before fully trusting this.

    FIFTH bug, found via different-topic calibration (2026-09-07, Canada/South Korea AI-safety-
    regulation query -- the first real calibration incident NOT from the original Germany/Japan
    renewable-energy report): a report nesting each named law under its own dedicated "###"
    subsection heading beneath a facet's "##" heading (e.g. "## 2. Canada" > "### 2.1 Safe Social
    Media Act (Bill C-34)") wrongly flagged Canada as missing a named law even though two real,
    correctly-named laws (Safe Social Media Act, Artificial Intelligence and Data Act) were right
    there -- neither tier matched: the parent heading names the facet but its own (pre-subsection)
    body has no regulation, and each child heading names a regulation but no facet, so it fell into
    the "shared" tier with nothing in its own short body to anchor proximity to. Fixed by tracking
    the nearest ancestor dedicated heading's facet + `#`-level and inheriting it into any deeper
    (higher `#` count) child section that names zero facets of its own -- a sibling or
    higher-level heading (same or shallower level) still resets the inherited context, so this
    doesn't bleed into an unrelated later section.

    Builder-fixable, not Planner-only (unlike check_missing_query_facet): the fix here is "cite an
    already-fetched source you forgot to use," which Builder can do directly from findings.md,
    not "delegate new research" -- matching this project's real incident, where the source was
    already sitting in sources/, just never cited."""
    if not config.get_setting("specific_item_check", {}).get("enabled", True):
        return None
    if ctx.content is None:
        return None
    query = ctx.run_state.data.get("query") or ""
    item_type = _extract_required_item_type(query)
    if not item_type:
        return None
    facets = _extract_required_facets(query)
    if len(facets) < 2:
        return None

    clean_content = normalize_dashes(re.sub(r'[*_]', '', ctx.content))
    mentioned = [_facet_token_match(f, _facet_mentions(clean_content)) for f in facets]
    if not any(mentioned):
        return None

    covered = [False] * len(facets)
    # (dedicated facet index, its heading's # level) inherited from the nearest ancestor heading --
    # confirmed live (2026-09-07, Canada/South Korea AI-regulation calibration run, a genuinely
    # different topic from the original Germany/Japan incident): a report that nests each named law
    # under its own "### 2.1 Safe Social Media Act" subsection heading beneath a dedicated
    # "## 2. Canada" heading has NEITHER heading naming both the facet AND the regulation together
    # -- the parent names Canada with no regulation in its own (pre-subsection) body, and each child
    # names a regulation with no facet in ITS own heading, so tier 1 (own-heading-names-the-facet)
    # missed it and tier 2 (shared-section proximity) had nothing in that short subsection to anchor
    # to either. A dedicated heading's inheritance now propagates to any deeper-level child section
    # with zero facets of its own, since it's still entirely about the parent's subject.
    current_dedicated, current_level = None, None
    for sec in split_into_heading_sections(ctx.content):
        if not sec:
            continue
        clean_sec = normalize_dashes(re.sub(r'[*_]', '', "\n".join(sec)))
        level_m = re.match(r'(#{1,3})\s', sec[0])
        level = len(level_m.group(1)) if level_m else None
        heading_facets = [
            i for i, f in enumerate(facets)
            if _facet_token_match(f, _facet_mentions(re.sub(r'[*_]', '', sec[0])))
        ]
        acronym_matches = find_acronym_regulation_matches(clean_sec)
        if len(heading_facets) == 1:
            i = heading_facets[0]
            if not covered[i] and (_NAMED_REGULATION_RE.search(clean_sec) or _REGULATION_ID_RE.search(clean_sec)
                                    or acronym_matches):
                covered[i] = True
            current_dedicated, current_level = i, level
            continue
        if not heading_facets and level is not None and current_dedicated is not None and level > (current_level or 0):
            i = current_dedicated
            if not covered[i] and (_NAMED_REGULATION_RE.search(clean_sec) or _REGULATION_ID_RE.search(clean_sec)
                                    or acronym_matches):
                covered[i] = True
            continue
        current_dedicated, current_level = None, level
        unattributed = []
        for pattern in (_NAMED_REGULATION_RE, _REGULATION_ID_RE):
            for m in pattern.finditer(clean_sec):
                i = _facet_for_regulation_match(m.group(), clean_sec[:m.start()], facets)
                if i is not None:
                    covered[i] = True
                else:
                    unattributed.append(m)
        for match_text, preceding_text in acronym_matches:
            i = _facet_for_regulation_match(match_text, preceding_text, facets)
            if i is not None:
                covered[i] = True
            else:
                unattributed.append(match_text)
        # Entity-free-section fallback (2026-09-08, live-repro'd, not yet from a real report):
        # a section with NO capitalized entity word anywhere in its own body (a bare two-column
        # regulation table, each entity named only in an earlier section) gives
        # `_facet_for_regulation_match` nothing to attribute to at all -- see this function's
        # own docstring "Still not exhaustive" note. Safe specifically BECAUSE the section is
        # entity-free: every prior misattribution bug here required a section mentioning
        # MULTIPLE entities near the match (ambiguous which one), which by this guard's
        # definition cannot happen. Only applied when the match count exactly equals the
        # uncovered-facet count -- an unequal ratio is still genuinely ambiguous and falls
        # through unresolved rather than guessing.
        if unattributed and not any(_facet_token_match(f, _facet_mentions(clean_sec)) for f in facets):
            uncovered = [i for i in range(len(facets)) if mentioned[i] and not covered[i]]
            if len(unattributed) == len(uncovered):
                for i in uncovered:
                    covered[i] = True

    missing = [" ".join(sorted(facets[i])) for i in range(len(facets)) if mentioned[i] and not covered[i]]

    if not missing:
        return None

    from engine.completion import _capped, _consecutive_occurrences
    missing_str = ", ".join(missing)
    prior_same = _consecutive_occurrences(ctx.run_state, "missing_specific_item_per_facet")
    if prior_same == 0:
        directive = (
            f"Your query requires citing a specific {item_type} for each entity, but the "
            f"section(s) covering {missing_str} name no specific {item_type}. Check findings.md "
            f"for an already-fetched source naming one before assuming none exists, and cite it "
            f"by name in that section."
        )
    else:
        directive = (
            f"Still no named {item_type} in the {missing_str} section(s) after a prior warning. "
            f"If genuinely none exists in your findings, say so explicitly in the report as an "
            f"acknowledged gap."
        )
    return _capped(ctx, "missing_specific_item_per_facet", Verdict(
        "missing_specific_item_per_facet",
        f"Query requires a specific {item_type} per entity but {missing_str} names none. Pushing agent to add it or acknowledge the gap.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ))


def _redelegate_directive(ctx: Ctx) -> str:
    """Structural signal for a real, confirmed failure mode: a model makes ONE
    delegate_tasks call early on (satisfying "you must delegate"), then — after a
    grounding-check rejection — just rewrites the SAME report from memory with different
    fake citations instead of ever delegating again, because the existing nudges all
    phrase the fix as "rewrite using what you have," which quietly assumes enough real
    findings already exist. Confirmed live: a 9-attempt run with fetched_url_count stuck
    at 2 the entire time, one delegate_tasks call total, ending in salvage. Detected here
    deterministically (no new fetches since the last completion check) rather than
    guessed from wording, and used by the grounding checks to make the redelegation
    instruction explicit instead of implicit."""
    prior_attempts = ctx.run_state.data.get("completion_check_attempts", [])
    no_new_fetches = bool(prior_attempts) and prior_attempts[-1].get("fetched_url_count") == len(get_fetched_urls())
    if not no_new_fetches:
        return ""
    return (
        " You have NOT fetched any new sources since your last attempt — rewriting the "
        "report with the same information will fail the exact same way again. Your ONLY "
        "next tool call must be delegate_tasks, with real research tasks covering the "
        "specific claims or sectors that don't have a grounded source yet. Do NOT call "
        "write_workspace_file again until you have new, real findings to write from."
    )


def check_claim_unsupported(ctx: Ctx) -> Optional[Verdict]:
    """Distinct from "not_grounded": the URL WAS actually fetched — the problem is that
    the report's claims don't appear to come from what that source actually says. The
    right correction is different too: re-read the source and use what it actually
    says, not re-delegate for a new URL (which the not_grounded message would suggest)."""
    gp = _gp(ctx, "claim_unsupported")
    if not gp:
        return None
    return Verdict(
        "claim_unsupported",
        f"`{ctx.req_artifact}` cites a source that was fetched, but the claims near it don't appear to come from that source's actual content ({gp}). Pushing agent to re-check.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites at least one source that WAS actually fetched ({gp}), but the specific claims attributed to it don't share any checkable fact (number, name, or figure) with what that source actually contains. This looks like the source was cited without being read, or the claim was written from memory and a real citation was attached to it afterward. The previous draft has been moved aside. Before rewriting: delegate re-reading of that exact fetched file to an Analyzer if you haven't already, and only state what the Analyzer's findings actually say — do not keep the same claim and just hope the citation makes it look sourced.",
    )


def check_no_urls(ctx: Ctx) -> Optional[Verdict]:
    """Distinct from "cited a URL that wasn't fetched": here there are no citations AT
    ALL, not a wrong one — the generic "cites at least one URL that does not match"
    message doesn't even make sense for this case, and a live test showed a model
    get this generic nudge 3 times in a row without ever adapting (it kept naming
    sources in prose without ever hyperlinking them). Escalates on repeat, same
    pattern as the not_delegated/missing_artifact escalations."""
    if ctx.grounding_problem != "no_urls":
        return None
    no_urls_count = ctx.run_state.data.get("no_urls_count", 0) + 1
    ctx.run_state.data["no_urls_count"] = no_urls_count
    fmt = _citation_format_reminder(ctx.report_style)
    escalation = ""
    if no_urls_count >= 2:
        # Words alone didn't work the first time ("add real citation links" was
        # already said once) — handing back the exact URL list removes any excuse to
        # keep failing the same way. Confirmed live: a model that failed this same
        # check twice in a row, both times with real sources already sitting in its
        # own findings, never once copied one in on its own.
        real_urls = get_fetched_urls()
        url_list = "\n".join(f"- {u['url']}" for u in real_urls[:20]) or "(none fetched yet)"
        # 2026-08-24 fix: this used to unconditionally say naming a source in prose like
        # "(World Bank, 2020)" doesn't count -- exactly backwards for academic style, where
        # that IS the required in-text format (just needs a matching References entry with a
        # real URL, which is what's actually missing when this fires).
        prose_note = (
            "Citing a source with no matching References entry (or a References entry with no "
            "real URL) does NOT count."
            if ctx.report_style == "academic" else
            "Naming a source in prose (e.g. \"(World Bank, 2020)\") does NOT count as a citation."
        )
        escalation = (
            f" This is the {no_urls_count}th time in a row you have written this report "
            f"with ZERO real citations. {prose_note} Here are the EXACT URLs actually "
            f"fetched this run — use these, copied verbatim, do not paraphrase or "
            f"invent your own:\n{url_list}\nEvery single claim must be backed by {fmt}, "
            f"using one of the URLs above."
        )
    return Verdict(
        "not_grounded",
        f"`{ctx.req_artifact}` contains zero hyperlinked sources — no citations at all. Pushing agent to add real ones.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' does not contain a single real citation anywhere — you named sources in prose but never actually cited them with a working link. The previous draft has been moved aside. Rewrite '{ctx.req_artifact}' using {fmt} for every source, with real URLs your Searcher(s) actually returned in their findings.{escalation}{_redelegate_directive(ctx)}",
    )


def check_regulation_unsupported(ctx: Ctx) -> Optional[Verdict]:
    """The URL is real and fetched, but the specific regulation number attributed to it
    doesn't exist anywhere in that source's content — a misattributed or invented law
    number wearing a legitimate citation. Confirmed live (run 12): 'Ley 1906 de 2021'
    cited to a fetched Mintic page about the 2025-2027 strategy, no '1906' in it."""
    gp = _gp(ctx, "regulation_unsupported")
    if not gp:
        return None
    return Verdict(
        "regulation_unsupported",
        f"`{ctx.req_artifact}` names a regulation whose own cited source never mentions that regulation's number ({gp}) — likely a misattributed or invented identifier.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attributes a specific regulation ({gp}) to a source whose content never mentions that number anywhere. Naming a law the cited source does not contain is fabrication even when the URL itself is real and was fetched. The previous draft has been moved aside. Either delegate a Searcher to fetch the regulation's actual text or official page and cite THAT for the identifier, or rewrite the claim using only what the cited source actually says — without a law number you cannot support.{_redelegate_directive(ctx)}",
    )


def check_specific_figure_unsupported(ctx: Ctx) -> Optional[Verdict]:
    """The URL is real and fetched, but a specific dollar figure, fee, or day/month-count
    attributed to it doesn't appear anywhere in that source's content — most often because the
    claim actually belongs to a DIFFERENT, also-genuinely-fetched source on the same narrow topic.
    Confirmed live (2026-08-17 ablation smoke-test): a report's Mexico visa income/fee/duration
    figures were all real, but attributed to the wrong one of two similar, both-fetched sources —
    the URL-presence gate and the term-overlap gate (which passed on nothing but a coincidentally
    shared bare year) both let it through. See utils/grounding.py::find_unsupported_specific_figures."""
    gp = _gp(ctx, "specific_figure_unsupported")
    if not gp:
        return None
    return Verdict(
        "specific_figure_unsupported",
        f"`{ctx.req_artifact}` attributes a specific figure to a source whose content never mentions it ({gp}) — likely misattributed to the wrong (but also genuinely fetched) source.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attributes a specific figure ({gp}) to a source whose content never mentions it anywhere. This is usually NOT a fabrication from nothing — it is more often a real figure from a DIFFERENT source you also fetched this run, attached to the wrong one. The previous draft has been moved aside. Re-check EACH of your fetched sources individually and re-attach every specific number (fee, income threshold, day/month count) to the exact source that actually states it — do not assume two similar sources on the same topic are interchangeable.{_redelegate_directive(ctx)}",
    )


def check_quote_paraphrased(ctx: Ctx) -> Optional[Verdict]:
    """A quoted span (implying "this is the source's own exact words") that's actually a
    paraphrase — see utils/grounding.py::find_paraphrased_quotes for the live case that motivated
    this (2026-07-24): a report quoted a plausible-sounding "sunset colors" sentence and attributed
    it to a real, fetched source whose actual text says something factually equivalent but
    differently worded. The underlying claim was true and traceable, so no other grounding check
    catches it — this is specifically about textual exactness of something presented as exact."""
    gp = _gp(ctx, "quote_paraphrased")
    if not gp:
        return None
    return Verdict(
        "quote_paraphrased",
        f"`{ctx.req_artifact}` presents at least one quotation mark-enclosed span as if verbatim, but it doesn't match its cited source's actual text ({gp}). Pushing agent to fix.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' puts text in quotation marks (implying an exact quote) that does not actually appear, word-for-word, in its cited source ({gp}). Putting words in quotes is a claim of exactness, not just support — either copy the source's ACTUAL wording exactly, or remove the quotation marks and state the point as your own paraphrase (still citing the source, just not pretending it's their exact words). The previous draft has been moved aside.{_redelegate_directive(ctx)}",
    )


def check_non_url_citation(ctx: Ctx) -> Optional[Verdict]:
    """Distinct from "no_urls": the report DOES have real hyperlinked citations
    elsewhere (that's why it reached this check instead of check_no_urls above), but at
    least one OTHER claim is attributed to something that isn't a URL at all — a bare
    "(DANE, 2020)"-style parenthetical or a "Source: <prose>" line. This evades the
    URL-presence check entirely (extract_cited_urls never sees a non-URL attribution),
    so a report can look grounded overall while still smuggling in an unverifiable
    claim — confirmed live (SESSION_STATUS.md's tracked #1 open item at the time)."""
    gp = _gp(ctx, "non_url_citation")
    if not gp:
        return None
    fmt = _citation_format_reminder(ctx.report_style)
    # 2026-08-24 fix: for academic style, find_non_url_citations (utils/grounding.py) already
    # excludes a `(Author, Year)` that resolves via parse_academic_references to a real
    # URL-bearing References entry -- so a hit here for THIS style means the citation is
    # unresolved (no matching entry, or an entry with no URL), not that the format itself is
    # wrong. Telling the model to switch to `[Title](URL)` (the old, style-blind wording) directly
    # contradicts ACADEMIC_CITATION_FORMAT_INSTRUCTIONS and was confirmed live to cause exactly
    # the oscillation described in _citation_format_reminder's own docstring.
    fix_note = (
        "Add or fix the matching numbered References entry for that citation (with a real URL you "
        "actually fetched) — do NOT switch to inline markdown links, this run's citation format is "
        "(Author, Year) + References, not standard style."
        if ctx.report_style == "academic" else
        "If you don't have a real fetched URL for a specific claim, either delegate to get one or "
        "remove the claim entirely — do not attribute it to an organization name, a year, or a "
        "vague description instead."
    )
    return Verdict(
        "non_url_citation",
        f"`{ctx.req_artifact}` attributes at least one claim to something that isn't a real URL ({gp}) — pushing agent to fix it.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attributes at least one claim to a non-URL citation ({gp}) — e.g. a bare parenthetical like \"(DANE, 2020)\" or a \"Source: <description>\" line with no link. This is exactly as unverifiable as a fabricated URL — there is nothing to check it against. The previous draft has been moved aside. Every single claim must be backed by {fmt}. {fix_note}{_redelegate_directive(ctx)}",
    )


def check_stub_source(ctx: Ctx) -> Optional[Verdict]:
    """The URL was really fetched, but every fetch of it returned only a paywall/not-found
    shell (a 200 soft-404) — the citation is hollow even though the fetch 'succeeded'.
    Confirmed live (run 14, 2026-07-12): a model-INVENTED El Tiempo URL answered 200 with
    ~5KB of subscription chrome, was recorded as a real fetch, and passed the hard URL gate.
    Distinct correction from not_grounded: the model must find a genuinely different source
    (or the publisher's working URL), not just re-cite something it already fetched."""
    gp = _gp(ctx, "stub_source")
    if not gp:
        return None
    return Verdict(
        "stub_source",
        f"`{ctx.req_artifact}` cites a URL whose fetch returned only a paywall/not-found stub ({gp}) — there is no real article content behind that citation.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites at least one URL ({gp}) whose fetch returned only a subscription/not-found shell — the page contains no real article content, so nothing attributed to it can actually be verified from it. A citation to an empty shell is exactly as unverifiable as a fabricated URL. The previous draft has been moved aside. Delegate a Searcher to find a REAL source for those claims (a different site, or the publisher's actual working URL) and cite THAT — or drop the claims entirely. Do not keep citing the stub URL.{_redelegate_directive(ctx)}",
    )


def check_nli_unsupported(ctx: Ctx) -> Optional[Verdict]:
    """The URL was fetched and the claim shares a checkable term with its source's content (so
    check_claim_unsupported already passed) — but a small NLI entailment model judges the claim as
    CONTRADICTED by that source's most relevant passage, not just coincidentally overlapping.
    Confirmed live 2026-07-12: a citation to a real, fetched arXiv paper quoted its title with one
    word swapped ('Dual Causal Network' vs the real 'Dual Correlation Network') — enough shared
    terms to pass term-overlap outright. Distinct correction from claim_unsupported: the citation
    itself is real and the general topic checks out, only the SPECIFIC detail attached to it is
    wrong — a name, title, or figure was likely swapped or misremembered while the citation stayed
    attached."""
    gp = _gp(ctx, "nli_unsupported")
    if not gp:
        return None
    return Verdict(
        "nli_unsupported",
        f"`{ctx.req_artifact}` cites a source that was fetched and shares terms with the claim, but an NLI check found the claim isn't actually entailed by that source's content ({gp}).",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites a real, fetched source for a claim that shares some words with that source but is NOT actually supported by what it says ({gp}). This often means a specific detail (a name, title, or figure) was swapped or misremembered while the citation itself was kept. The previous draft has been moved aside. Re-read the cited source's actual content and rewrite the claim to match exactly what it says, or drop it if you can't verify it.{_redelegate_directive(ctx)}",
    )


def check_topical_mismatch(ctx: Ctx) -> Optional[Verdict]:
    """ROADMAP Phase 4: a citation passed both lexical term-overlap (check_claim_unsupported) and
    NLI entailment (check_nli_unsupported) — the terms line up and nothing is contradicted — but a
    cross-encoder reranker judges the source as topically UNRELATED to the claim's actual subject.
    Distinct failure mode from both upstream checks: catches an acronym collision like GOA (the
    Grasshopper Optimization Algorithm) vs. Goa (the Indian state) — 'GOA'/'Goa' term-overlap
    passes and the sentences aren't strictly contradictory (an EV-policy claim about Goa doesn't
    CONTRADICT an algorithm claim, it's just about something else), so neither upstream layer
    catches it; only a semantic relevance judgment does. See
    utils.grounding.topical_relevance_problem for the conservative threshold and reused evidence
    set (the exact same claim/source pairs the NLI check already matched)."""
    gp = _gp(ctx, "topical_mismatch")
    if not gp:
        return None
    return Verdict(
        "topical_mismatch",
        f"`{ctx.req_artifact}` cites a source that shares terms with the claim and isn't contradicted by it, but a topical-relevance check found the source is about a different subject entirely ({gp}).",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites a real, fetched source that shares words with a claim but appears to be about a DIFFERENT SUBJECT entirely, not the one the claim is actually about ({gp}). This is the acronym-collision pattern (e.g. a source about a place or organization that happens to share an abbreviation with the real subject). The previous draft has been moved aside. Re-check that the cited source is genuinely about the claim's real subject, not just sharing a term or acronym with it, and rewrite or drop the claim if it isn't.{_redelegate_directive(ctx)}",
    )


def check_editorializing_content(ctx: Ctx) -> Optional[Verdict]:
    """Fifth grounding layer (2026-08-29, RAGTruth-informed): the citation is real, shares terms
    with its source, isn't contradicted, and is topically on-subject -- but a span classifier
    finds part of the claim the source's own passage simply never states. Distinct from
    check_nli_unsupported (CONTRADICTION) and check_topical_mismatch (wrong subject entirely):
    here the model has added its own inference/interpretation and attributed it to the source as
    if it were a stated fact -- root-caused this session as the shared cause behind a whack-a-mole
    pattern across claim_unsupported/topical_mismatch/uncited_claims/non_url_citation, each
    catching only the specific downstream SHAPE one rewrite happened to take. UNVALIDATED against
    live traffic (opt-in, settings.grounding_check.editorial_detection_check default False) -- see
    session_status/CURRENT.md's calibration note."""
    gp = _gp(ctx, "editorializing")
    if not gp:
        return None
    return Verdict(
        "editorializing",
        f"`{ctx.req_artifact}` cites a real, on-topic, uncontradicted source, but part of the claim appears to be the model's own added interpretation, not something the source actually states ({gp}).",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attaches a real citation to a claim that includes content the cited source does NOT actually say ({gp}) -- this looks like your own inference or interpretation being presented as if the source stated it. The previous draft has been moved aside. Rewrite the claim to state ONLY what the cited source actually says, or if you want to include your own analysis, present it explicitly as your own reasoning rather than attributing it to the source.{_redelegate_directive(ctx)}",
    )


def check_uncited_claims(ctx: Ctx) -> Optional[Verdict]:
    """The report's citations are all real, but its claims are structurally decoupled from
    them — figure-bearing claim lines with no citation on the line (e.g. a table of numbers
    plus a detached '### Source URLs' list, run 14's exact shape). Every line-scoped check
    passes vacuously on that format, so nothing ties any specific figure to any specific
    source. NOT quarantined (like no_urls, unlike the fabrication verdicts): the content may
    be fine — the fix is re-attaching citations, and the model needs its own draft visible
    to do that."""
    gp = _gp(ctx, "uncited_claims")
    if not gp:
        return None
    # 2026-08-24 fix: find_uncited_claim_lines (utils/grounding.py) already exempts a section
    # containing a `(Author, Year)`-shaped citation for academic style -- a hit here means the
    # claim line/section has neither a URL nor an academic in-text citation, not that the
    # required format itself is `[Title](URL)`. Same style-blind-wording bug as check_no_urls/
    # check_non_url_citation, see _citation_format_reminder's own docstring for the live incident.
    fmt = _citation_format_reminder(ctx.report_style)
    per_line_note = (
        "every claim (including every table row) must be in a section carrying its own "
        f"{fmt}"
        if ctx.report_style == "academic" else
        f"every claim line (including every table row) must carry its own {fmt} on the SAME line"
    )
    return Verdict(
        "uncited_claims",
        f"`{ctx.req_artifact}`'s figures aren't tied to sources — claim lines carry no citation of their own ({gp}), so none of them can be verified against anything.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' states specific figures on lines that carry no citation ({gp}). A separate list of source URLs does NOT tie any claim to any source — {per_line_note}, using a URL your Searcher(s) actually fetched this run. Rewrite '{ctx.req_artifact}' keeping the content but attaching to each claim the exact fetched URL that supports it; if no fetched source supports a figure, remove the figure rather than leaving it uncited.",
    )


def check_excluded_topic(ctx: Ctx) -> Optional[Verdict]:
    """A live-observed, twice-confirmed failure mode (ROADMAP "Findings from live testing"):
    `delegate_tasks` already skips DISPATCHING a task whose own topic matches an explicit query
    exclusion ("excluding X") via `_extract_excluded_topics`, but that only stops NEW research on
    X — it does nothing to stop X showing up as its own section in the final artifact anyway
    (recalled from a sibling task's tangential findings, or synthesized by Builder without ever
    being explicitly delegated). Confirmed live twice, different prompt wordings: an
    explicitly-excluded sector got researched and included in the final report anyway.

    Deliberately HEADING-scoped, not line/whole-document-scoped: a topic mentioned once in
    passing prose (e.g. a source that discusses it tangentially while covering something else)
    is not the same failure as giving it its own section, and a bare substring match across the
    whole document would false-positive constantly on legitimate incidental mentions — same
    section-scoping principle as check_uncited_claims's h1-h3 split
    (`utils.grounding.split_into_heading_sections`). Reuses the exact same
    `_extract_excluded_topics` parser `delegate_tasks` already uses, so a phrase like "excluding
    X" is detected identically at both dispatch time and report-write time.

    Substring match alone shares one blind spot with the dispatch-time filter: a heading covering
    the excluded topic under a translation or same-language paraphrase of the query's own wording
    passes both untouched (2026-08-29 audit finding — this project's own benchmark instructs
    bilingual search, so a Spanish-language heading for an English-named exclusion is a live risk,
    not a hypothetical). `excluded_topic_semantic_hit` (utils.grounding) is a cross-lingual
    cross-encoder backstop layered after the cheap substring check, not a replacement for it."""
    query = ctx.run_state.data.get("query", "") if ctx.run_state else ""
    excluded_topics = _extract_excluded_topics(query)
    if not excluded_topics or not ctx.content:
        return None
    for section in split_into_heading_sections(ctx.content):
        heading = next((line for line in section if re.match(r'#{1,3}\s', line)), None)
        if not heading:
            continue
        heading_text = heading.lower()
        hit = next((topic for topic in excluded_topics if topic in heading_text), None)
        if not hit:
            hit = excluded_topic_semantic_hit(excluded_topics, heading_text)
        if hit:
            return Verdict(
                "excluded_topic_present",
                f"`{ctx.req_artifact}` has a section on {hit!r}, which the query explicitly excluded. Pushing agent to remove it.",
                f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' has a section covering {hit!r} — the original query explicitly excluded this topic from the research. Remove that entire section and any content specific to it, keeping the rest of the report intact.",
            )
    return None


def check_cross_source_contradiction(ctx: Ctx) -> Optional[Verdict]:
    """ROADMAP Phase 2 (cross-source contradiction detection, FEVER-style — depends on Phase 1's
    claim segmentation). A claim's own citation can pass claim_grounding_problem's term-overlap
    check (the cited source really does say what's claimed) while a DIFFERENT fetched source
    disagrees on the same named subject's figure — and the report never surfaces that
    disagreement anywhere. Distinct from claim_unsupported: this isn't fabrication, it's a real
    disagreement between two real fetched sources that got silently resolved by picking one side.
    See utils.grounding.find_cross_source_contradictions for the conservative
    same-subject-phrase + differing-figure detection (exact 2+-word proper-noun match required,
    the conflicting figure must not already appear anywhere else in the report)."""
    if not ctx.content:
        return None
    hits = find_cross_source_contradictions(ctx.content)
    if not hits:
        return None
    return Verdict(
        "cross_source_contradiction",
        f"`{ctx.req_artifact}` states a figure that a DIFFERENT fetched source disagrees with, unacknowledged ({hits[0]}). Pushing agent to surface the conflict.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' states a figure for a subject where a DIFFERENT source you actually fetched this run reports a conflicting number, and the report never mentions the disagreement: {hits[0]}. Do not silently pick a side — rewrite that claim to surface BOTH figures (e.g. \"Source A reports X, while Source B reports Y\") rather than stating only one as fact.",
    )


def check_propagated_ungrounded_content(ctx: Ctx) -> Optional[Verdict]:
    """Propagation-aware check (2026-07-22, PING taxonomy, see _find_propagated_bad_content's own
    docstring for the mechanism). Only fires if a flagged task_name's suspect content also shows
    up inside ctx.content itself -- otherwise this is a findings.md-hygiene issue the Builder never
    actually drew on, not yet a report-level grounding problem worth quarantining over.

    Capped via the shared _capped helper (2026-07-31, found by systematically auditing every
    GROUNDING_CHECKS/COMPLETION_CHECKS entry against the landmine class documented in
    ARCHITECTURE.md -- not a live incident this time, the audit itself caught it first) -- an
    unresolved propagated-content condition could otherwise permanently starve
    check_report_underuses_findings/evidence/check_not_grounded, positioned right after this one in
    GROUNDING_CHECKS, the same way check_task_verification_flagged and check_thin_coverage did
    before their own fixes.

    Builder-fixable (added 2026-08-29, live incident): this problem sat in NEITHER
    _BUILDER_FIXABLE_PROBLEMS nor _FINDINGS_WRITER_FIXABLE_PROBLEMS for over a month, meaning a
    correct detection had no structural fix path at all -- only a nag into the Planner's own
    conversation. A real run fired this 5+ times, exhausted its retry budget, and fell back to a
    stale, off-topic salvage draft instead of either a correct report or a clean failure. Now in
    _BUILDER_FIXABLE_PROBLEMS: the problem is entirely about what ctx.content (the report) cites,
    not how findings.md itself was assembled, and Builder already only ever draws from findings.md
    (never delegates new research), so a fresh-context Builder told exactly which task's content is
    suspect can simply stop citing it -- same mechanism claim_unsupported/report_underuses_findings
    already use successfully."""
    if not ctx.content:
        return None
    findings = ctx.run_state.data.get("findings", []) if ctx.run_state else []
    if not findings:
        return None
    from utils.grounding import extract_salient_terms
    from engine.completion import _dedupe_findings, _uncited_task_names, _find_propagated_bad_content, _CUTOFF_ONLY_SUMMARY_RE, _capped
    deduped = _dedupe_findings(findings)
    uncited_task_names = _uncited_task_names(deduped)
    flagged = _find_propagated_bad_content(deduped, uncited_task_names)
    if not flagged:
        return None
    content_terms = extract_salient_terms(ctx.content)
    for task_name in flagged:
        for f in deduped:
            if f.get("task_name") != task_name:
                continue
            src = f.get("source_url") or ""
            if src.startswith("http") and not _CUTOFF_ONLY_SUMMARY_RE.match(f.get("summary") or ""):
                summary_terms = extract_salient_terms(f.get("summary") or "")
                if summary_terms and (summary_terms & content_terms):
                    return _capped(ctx, "propagated_ungrounded", Verdict(
                        "propagated_ungrounded",
                        f"`{ctx.req_artifact}` draws on findings for task '{task_name}' that reuse "
                        f"content from an earlier, ungrounded (cutoff/unfetched) attempt at the same "
                        f"task, without independent verification. Pushing agent to re-verify.",
                        f"SYSTEM WARNING: {ctx.last_chance_prefix}Some content attributed to task "
                        f"'{task_name}' in findings.md closely matches an EARLIER, ungrounded attempt "
                        f"at that same task (one that was cut off or never fetched a real source) — "
                        f"this looks like content propagated forward without being independently "
                        f"re-verified. Do not simply repeat it in '{ctx.req_artifact}'; cite a "
                        f"DIFFERENT, independently-grounded finding for task '{task_name}' from "
                        f"findings.md instead, or omit that claim entirely if no other genuine "
                        f"finding for it exists.",
                    ))
    return None


def check_not_grounded(ctx: Ctx) -> Optional[Verdict]:
    """The generic hard gate: at least one cited URL matches nothing actually fetched this run."""
    gp = ctx.grounding_problem
    if not gp:
        return None
    return Verdict(
        "not_grounded",
        f"`{ctx.req_artifact}` cites a URL that was never actually fetched this run ({gp}) — this looks ungrounded or hallucinated. Pushing agent to fix citations.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites at least one URL that does not match anything your Searcher(s) actually fetched this run ({gp}). This is a strong signal of a hallucinated source. The previous draft has been moved aside — write a fresh '{ctx.req_artifact}' using ONLY URLs your Searcher(s) actually returned in their findings. If you don't have a real source for a claim, delegate again and use exactly what comes back, not your own prior knowledge.{_redelegate_directive(ctx)}",
    )


