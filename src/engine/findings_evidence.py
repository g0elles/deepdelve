# Findings evidence-assembly, extracted from engine/completion.py (2026-08-24, group B of the
# completion.py decomposition plan -- see session_status/CURRENT.md and ROADMAP.md's completion.py
# entry). Mechanical move: the task-verification ledger mutator, the citable-finding predicate, and
# FindingsWriter's own evidence-blob assembly, none of which touch Ctx/Verdict or the check-list/
# starvation machinery that stayed in completion.py.
import re
import time
from typing import Optional

from utils.grounding import _is_null_finding_summary
from engine.orchestrator import _looks_like_renamed_task, _content_word_overlap, get_context_budget

# Matches orchestrator.py's task_deadline cutoff marker text exactly (both variants: mid-turn
# and no-update-before-deadline) when it is the ENTIRE summary -- i.e. the dispatch never
# synthesized anything real before being cut off. Deliberately does NOT match a summary that has
# real content followed by the marker (a partial synthesis is still worth showing).
_CUTOFF_ONLY_SUMMARY_RE = re.compile(
    r"^\s*\[SYSTEM: task '.*?' cut short -- sub_agent_timeout_minutes \(\d+\) exceeded"
    r"(?: \(stream produced no update before the deadline\))?\.\]\s*$"
)


def _is_citable_finding(f: dict) -> bool:
    """A real, http(s) source_url whose summary isn't a pure sub_agent_timeout_minutes cutoff
    marker, AND isn't confirmed off-topic by the scope-relevance check, AND isn't flagged by the
    citation-mismatch verification check. Shared predicate (2026-07-22) so
    _build_findings_source_material, _uncited_task_names, and _find_propagated_bad_content all
    agree on one definition.

    The relevance-flag condition (found live 2026-07-21): orchestrator.py's topical-relevance
    check appends a "[SYSTEM RELEVANCE WARNING: none of the sources fetched for this task
    actually mention {entities}...]" marker when NONE of a task's fetched sources mention a
    required scope entity -- but the finding was still rendered as an ordinary citable entry,
    indistinguishable from genuinely useful findings around it. Live-observed: a Colombia-holidays
    task that fetched a New Zealand page carried exactly this marker, sat near the front of a
    30-entry evidence base, and FindingsWriter abandoned the whole structured list afterward
    (confirmed via literature, not just this one incident: RAG noise-robustness research shows
    irrelevant retrieved content measurably degrades generation; DeepResearch-Slice, arXiv:
    2601.03261, names "distracted by spurious passages" as a root cause of exactly this pattern).

    The verification-flag condition (2026-07-26, REVERSING a 2026-07-22 design decision): this
    used to scope the exclusion to RELEVANCE warnings only, reasoning "a verification warning
    flags a narrower citation mismatch that may still coexist with other real, usable content."
    Confirmed live, twice, that this bet doesn't hold: a finding carrying a "[SYSTEM VERIFICATION
    WARNING: this summary cites 'X', which does not match the source URL you were actually given
    to analyze...]" marker stayed in FindingsWriter's evidence, and FindingsWriter kept citing the
    flagged bad URL anyway -- 2026-07-24 (calendarr.com) and 2026-07-26 (insidetx.com, 7
    independent dispatch attempts in one run, see RESEARCH.md Sec.10 for the full incident +
    literature review). Researched before reversing this, not guessed: embedded negative
    instructions are documented as fragile by mechanism, not just anecdote -- naming the forbidden
    content inside a "do not cite X" warning can itself prime reproduction of X (the "ironic
    rebound" effect, arXiv:2511.12381), and negation-following is separately documented as
    unreliable in small models (arXiv:2601.21433). CRAG (arXiv:2401.15884) and Self-RAG
    (arXiv:2310.11511) -- read directly, not inferred -- both structurally filter flagged evidence
    OUT before the generator ever sees it, rather than annotate it in place and hope. This project
    already treats the same warning more strictly elsewhere for a similar reason: _should_cache_finding
    (orchestrator.py) already refuses to cache anything carrying a VERIFICATION warning ("a cache
    entry must never be less verified than a same-run finding") -- this function was the odd one
    out relative to that existing, stricter precedent.

    `_is_null_finding_summary` exclusion (2026-08-17, evidence-crowding root cause, session_status
    2026-08-16 item 3): a fetch that came back but yielded nothing extractable ("No key findings
    extracted from this source...") still has a real http source_url and no warning marker, so it
    used to pass every check above and render as an ordinary "### Source: ..." entry in
    _build_findings_source_material's evidence blob -- indistinguishable from a genuinely useful
    finding. On a busy task with 8+ fetched sources this let several null placeholders crowd the
    same one-shot evidence blob real content has to compete with, and confirmed live (repeatedly,
    2026-08-16) FindingsWriter then wrote the placeholder text itself into findings.md rather than
    skipping it. check_report_underuses_findings/`_facet_coverage` already special-cased
    `_is_null_finding_summary` around this same predicate; folding it into `_is_citable_finding`
    directly means every consumer (evidence blob, uncited-task accounting, the verification ledger)
    now agrees a null finding is not real evidence, instead of each call site needing its own
    bolted-on exclusion.

    `_verification_warning_targets_url` scoping (2026-08-17, live incident, session_status 2026-08-16
    item 3's own long-open root cause): `orchestrator.py::_run_single_task` attaches the SAME
    task-level synthesis text (and the SAME verification_warnings string) to EVERY URL fetched in
    one turn via one `add_finding` call per URL -- see `_collapse_multi_url_task_findings`'s own
    docstring. A verification warning about ONE of those co-fetched URLs used to wholesale-exclude
    the shared finding record for ALL of them, discarding real content about URLs the warning never
    named. Confirmed live: a Mexico City rent synthesis covering Blueground (a stub page) AND
    Rentberry (real, with a genuine 'MX$17,300/month' figure) in one text got a
    `stub_source:...blueground...` warning; the old wholesale check threw away Rentberry's real
    price too, for all 3 co-fetched URLs including mexicoinsider.mx (never even mentioned in the
    warning) -- the single largest content loss found in that run's final report. Now scoped to the
    finding's OWN `source_url` when the warning names specific URL(s) (`unverified_urls:`/
    `stub_source:`/`claim_unsupported:` are the only labels that reliably carry real_grounding_
    problem's own flagged URLs -- see that function and claim_grounding_problem's own return
    strings); falls back to the prior wholesale exclusion when it can't (a warning shape that
    identifies its problem by quoted text or a regulation ID, not a URL, or the Analyzer-tier
    reconstructed-URL message, which deliberately also names the CORRECT reference URL alongside
    the bad one and must not have that safe URL swept up by a broader match)."""
    src = f.get("source_url") or ""
    summary = f.get("summary") or ""
    if not (src.startswith("http") and not _CUTOFF_ONLY_SUMMARY_RE.match(summary)):
        return False
    if _is_null_finding_summary(summary):
        return False
    if "[SYSTEM RELEVANCE WARNING" in summary:
        return False
    if "[SYSTEM VERIFICATION WARNING" in summary and _verification_warning_targets_url(summary, src):
        return False
    return True


_WARNING_MARKER_RE = re.compile(r"\[SYSTEM (?:VERIFICATION|RELEVANCE) WARNING:.{0,160}")
_VERIFICATION_WARNING_BLOCK_RE = re.compile(r"\[SYSTEM VERIFICATION WARNING:.*?\]", re.DOTALL)
_VERIFICATION_FLAGGED_URLS_RE = re.compile(r"\((?:unverified_urls|stub_source|claim_unsupported|academic_citation_unverified):([^)]*)\)")


def _verification_warning_targets_url(summary: str, source_url: str) -> bool:
    """True if a `[SYSTEM VERIFICATION WARNING...]` marker on `summary` specifically names
    `source_url` as the bad one -- see `_is_citable_finding`'s own docstring for why this must be
    URL-scoped, not wholesale. Returns True (exclude, the safe default) when the marker's own
    detail carries no extractable URL at all -- nothing to scope to, so the pre-existing wholesale
    behavior is preserved for those shapes."""
    from utils.grounding import extract_cited_urls, _urls_prefix_match
    flagged = set()
    for block in _VERIFICATION_WARNING_BLOCK_RE.findall(summary):
        for m in _VERIFICATION_FLAGGED_URLS_RE.finditer(block):
            flagged.update(u.rstrip("/") for u in extract_cited_urls(m.group(1)))
    if not flagged:
        return True
    own = source_url.rstrip("/")
    return any(own == u or _urls_prefix_match(own, u) for u in flagged)


def _update_task_verification(run_state: "RunState") -> None:  # noqa: F821 — utils.run_state.RunState, annotation only
    """VERIMAP-inspired (arXiv:2510.17109, RESEARCH.md Sec.9) per-task verification ledger,
    2026-07-26 -- DeepDelve's existing checks (check_thin_coverage, check_uneven_task_investment,
    check_findings_underuses_evidence) all derive task-level facts from RunState.coverage()/
    findings but only ever produce ONE whole-run Verdict per attempt; there was no per-task
    pass/fail record anywhere a task could be checked (or eventually retried) independently of
    the rest of the run. This is that ledger -- purely structural, recomputed fresh every
    completion-check attempt from already-existing ground truth (_is_citable_finding), never
    something the Planner authors. Deliberately NOT VERIMAP's own mechanism (the paper has the
    PLANNER author each subtask's own verification function) -- RunState.coverage()'s own
    docstring already explains why DeepDelve avoids handing local models a new structured
    convention to follow ("small local models have repeatedly proven unreliable at following new
    structured-output conventions"); this keeps VERIMAP's actual contribution (a task has its own
    checkable verification state) while computing that state the same "derive from ground truth"
    way every other check in this module already does.

    Only ever writes an entry for a task that has at least one depth==1 finding -- a task with
    none yet is still pending, not a problem (check_thin_coverage/check_missing_findings's job),
    so it's left out of the ledger rather than persisted as a third "unverified" status that
    nothing would ever read. "flagged" means EVERY finding this task produced was excluded by
    _is_citable_finding (fabricated/off-topic/contradicted) -- not "some", which would just be
    check_uneven_task_investment's territory again; this is specifically "nothing usable came
    of this task at all." A task that later produces even one real citable finding on a retry
    flips back to "verified" the next time this runs, since the whole ledger is recomputed, not
    incrementally patched.

    A second pass then downgrades a "flagged" entry to "superseded" if its dispatched instructions
    (from run_state.data["dispatched_tasks"], see orchestrator.py's delegate_tasks) closely match
    a currently-"verified" task's instructions -- i.e. the model renamed the angle on retry instead
    of reusing the task_name, and the RENAMED version already succeeded. Confirmed live 2026-07-26:
    a flagged "Research X" task got redispatched as "Research X (narrow)"/"Research X (peer-reviewed
    source)" instead of being retried under its own name; two of those renamed variants ended up
    verified, but check_task_verification_flagged kept nudging the stale original name specifically
    (it has no notion of "this angle is already covered under a different name") until the entire
    completion-check retry budget was spent on a task that was, in substance, already done -- the
    run ended with ZERO report ever written. "superseded" (not "verified") deliberately preserves
    that the exact original attempt genuinely failed -- check_task_verification_flagged only reads
    status == "flagged", so this is enough to exclude it from further nudging without erasing the
    history."""
    findings = run_state.data.get("findings", [])
    top_level = [f for f in findings if f.get("depth") == 1 and f.get("task_name")]
    by_task: dict = {}
    for f in top_level:
        by_task.setdefault(f["task_name"], []).append(f)
    # Ledger rollup (2026-08-17, live incident): a depth==1 task's OWN findings can be entirely
    # empty (the "zero trailing text" synthesis-vanishing mechanism, see RunState.coverage()'s own
    # docstring) while the depth>1 Analyzer children it dispatched produced real, citable content
    # that was simply never folded back up. Confirmed live: Lisbon_digital_nomad_visa's 5 own
    # findings were all empty-summary, but 2 of its 3 nested Analyzer dispatches ("Analyze SEF D8
    # Visa page", "Analyze MyVisaPortugal Fees") had full real content -- yet the ledger read
    # "flagged: no real citable source" and the Planner acknowledged a gap that didn't actually
    # exist, silently dropping the run's single best-researched task. Rolled in here (not into
    # RunState.coverage()) because coverage()'s own docstring deliberately excludes depth>1 for a
    # DIFFERENT reason (a child reusing already-fetched content with no new URL of its own must not
    # make coverage look artificially low) -- this ledger cares about "does real evidence exist for
    # this task anywhere", which is a strictly different question from coverage()'s "did the
    # Planner's own top-level breadth pay off".
    for f in findings:
        if f.get("depth") == 1 or not f.get("top_level_task_name"):
            continue
        by_task.setdefault(f["top_level_task_name"], []).append(f)
    ledger = run_state.data.setdefault("task_verification", {})
    for name, task_findings in by_task.items():
        if any(_is_citable_finding(f) for f in task_findings):
            ledger[name] = {"status": "verified", "reason": None, "checked_at": time.time()}
            continue
        reasons = []
        for f in task_findings:
            m = _WARNING_MARKER_RE.search(f.get("summary") or "")
            if m and m.group(0) not in reasons:
                reasons.append(m.group(0))
        # gap_acknowledged carries forward across this full-ledger recompute (2026-08-16) -- once
        # check_task_verification_flagged has told the model to stop redelegating this task for
        # good, that decision must survive a still-flagged task getting rewritten fresh here on
        # the next attempt, or a later quota top-up can resurrect the "redo it" directive (see
        # that check's own quota_exhausted branch for the full incident).
        prior_ack = ledger.get(name, {}).get("gap_acknowledged", False)
        ledger[name] = {
            "status": "flagged",
            "reason": "; ".join(reasons) if reasons else "no real citable source",
            "checked_at": time.time(),
            "gap_acknowledged": prior_ack,
        }

    dispatched = run_state.data.get("dispatched_tasks", [])
    if dispatched:
        latest_instructions: dict = {}
        for d in dispatched:
            if d.get("task_name"):
                latest_instructions[d["task_name"]] = d.get("instructions", "")
        verified_prior = [
            {"task_name": n, "instructions": latest_instructions[n]}
            for n, entry in ledger.items()
            if entry["status"] == "verified" and n in latest_instructions
        ]
        for name, entry in ledger.items():
            if entry["status"] != "flagged" or name not in latest_instructions:
                continue
            if _looks_like_renamed_task(name, latest_instructions[name], verified_prior):
                entry["status"] = "superseded"


_NEAR_DUP_FINDING_OVERLAP_THRESHOLD = 0.5


def _dedupe_findings(findings: list) -> list:
    """Exact (source_url, summary) dedup, shared by _build_findings_source_material and
    check_propagated_ungrounded_content (2026-07-22) -- extracted so both stay in sync on the one
    definition of "duplicate" instead of drifting.

    Near-duplicate pass added 2026-08-17 (live incident, ablation smoke-test): a task-name-renamed
    retry that got through as the FIRST rename match (advisory-only, not yet a hard reject at the
    time) still produced a genuinely SEPARATE finding under a different task_name, whose summary
    independently restates the same underlying research -- confirmed live: `final_report.md`
    carried two near-identical "Mexico City" rent sections citing the same sources with the same
    figures, from two dispatches of what was really one facet. `_dispatch_tasks_batch` in
    orchestrator.py now skips dispatch on EVERY rename match, first included (2026-08-27), so this
    specific live incident can no longer occur -- this pass remains as a backstop for whatever
    still slips through (e.g. `disable_rename_reject_escalation` set, or a genuine
    `_looks_like_renamed_task` false negative below its similarity threshold). Exact-key dedup
    above can't catch a near-duplicate case like this (the two summaries are independently
    generated text, not byte-identical). Reuses
    `_content_word_overlap` -- the SAME metric `_looks_like_renamed_task` already uses to detect
    this at dispatch time, applied here as a second, later-stage net for whichever renamed pair
    still slipped through (e.g. the first, still-advisory match). Threshold set higher than
    `_looks_like_renamed_task`'s own 0.3 (tuned for short, templated task INSTRUCTIONS) since
    findings summaries are longer, denser prose where topical vocabulary overlap alone (both about
    "Mexico City" and "rent") is far more likely to be coincidental -- 0.5 is a deliberately more
    conservative judgment call for this different text population, not a re-derivation of the same
    number. A minimum word count guards the short/generic-summary edge case (found while testing
    this fix): two SHORT, boilerplate-heavy summaries (e.g. "Real finding with a title." vs. "Real
    finding without a title.") can cross a word-overlap threshold on almost nothing but shared
    filler after stopword-stripping -- real findings summaries are long-form prose, so this only
    ever exempts thin/placeholder-shaped ones, never a genuine research summary."""
    seen = set()
    deduped = []
    kept_summaries: list[tuple] = []  # (task_name, summary) already kept, for the near-dup check
    for f in findings:
        key = (f.get("source_url"), f.get("summary"))
        if key in seen:
            continue
        seen.add(key)
        summary = f.get("summary") or ""
        task_name = f.get("task_name")
        if summary and len(summary.split()) >= 8 and any(
            other_name != task_name
            and len(other_summary.split()) >= 8
            and _content_word_overlap(summary, other_summary) > _NEAR_DUP_FINDING_OVERLAP_THRESHOLD
            for other_name, other_summary in kept_summaries
        ):
            continue
        kept_summaries.append((task_name, summary))
        deduped.append(f)
    return deduped


def _collapse_multi_url_task_findings(citable: list) -> list:
    """A Searcher task that fetches N URLs in one turn (orchestrator.py's `_run_single_task`) calls
    `add_finding` once per URL, but attaches the SAME task-level synthesis text to every one --
    not a per-URL summary. Rendered as N separate findings, this looks like N times the real
    distinct content: confirmed live 2026-07-22 (`i_want_documentation_on_heuristic_algoritms_for_de_
    20260722_204635`), 3 of 12 real research tasks accounted for 16 of 25 "citable findings" purely
    because they fetched the most URLs, each one a near-verbatim repeat of that task's one summary.
    This both pads what FindingsWriter has to read with redundant text AND lets whichever task
    fetched the most URLs dominate `_reorder_findings_for_position_bias`'s front/back edges by raw
    fetch count rather than by having more real distinct information -- the opposite of what that
    reorder exists to protect against. Collapses same-(task_name, summary) findings into one group
    carrying every URL, so the body text is kept once and the reorder/FindingsWriter's attention are
    spent on genuinely distinct content once each. Does NOT touch `run_state.data["findings"]` or
    any other consumer of the raw list (coverage(), the RAG cache, _find_propagated_bad_content) --
    purely a rendering-time grouping for _build_findings_source_material's own entries."""
    grouped: dict = {}
    order = []
    for f in citable:
        key = (f.get("task_name"), f.get("summary"))
        group = grouped.get(key)
        if group is None:
            group = {"task_name": f.get("task_name"), "summary": f.get("summary"), "findings": []}
            grouped[key] = group
            order.append(key)
        group["findings"].append(f)
    return [grouped[k] for k in order]


def _uncited_task_names(deduped: list) -> list:
    """task_names of findings that aren't real, citable content -- non-http source_url (add_finding's
    own task_name fallback) or a pure sub_agent_timeout_minutes cutoff marker. Shared by
    _build_findings_source_material and check_propagated_ungrounded_content (2026-07-22)."""
    return [
        f.get("task_name") or "(unnamed task)" for f in deduped if not _is_citable_finding(f)
    ]


def _reorder_findings_for_position_bias(entries: list) -> list:
    """Zigzag/sandwich reorder (2026-07-22, "Lost in the Middle" arXiv:2307.03172 + PING's "Anchor
    Effect", both in RESEARCH.md §1): models use context well at the start/end and poorly in the
    middle, and separately tend to over-favor early-retrieved info over late-retrieved info. No
    per-finding value/importance signal exists anywhere in this project's data model (RunState.
    add_finding's fields are source_url/summary/timestamp/task_name/depth/follow_up_directions/
    agent_id — nothing to rank by), so this is a pure POSITIONAL transform, not a ranking: split
    the chronological entries in half, interleave front-half-forward with back-half-reversed, so
    every entry lands within one "hop" of either edge of the assembled block instead of only the
    earliest entries getting favorable positioning and everything else drifting toward the middle
    as a run accumulates more findings."""
    mid = (len(entries) + 1) // 2
    front, back = entries[:mid], entries[mid:][::-1]
    out = []
    for i in range(mid):
        out.append(front[i])
        if i < len(back):
            out.append(back[i])
    return out


def _find_propagated_bad_content(deduped_findings: list, uncited_task_names: list) -> list:
    """Propagation-aware hallucination check, narrowed scope (2026-07-22, PING taxonomy
    arXiv:2601.22984, RESEARCH.md §1): the paper's own "Propagation" category is a later claim
    built on an earlier hallucinated one, cascading through a multi-round trajectory. DeepDelve has
    no claim-dependency graph to trace that generally (and confirmed the paper's own released code
    doesn't build one either) -- this targets the SPECIFIC, already-documented failure shape in
    this project's own History instead: a task gets redispatched, one attempt produces a real-URL-
    but-cutoff-summary entry (routed to uncited_task_names by the check above), a LATER attempt for
    the SAME task_name produces different "real-looking" content that reuses/derives from it
    without ever being independently grounded -- the exact qwen3:8b/MiniCPM4-MCP split-brain
    pattern already fixed at the citation-rendering level (2026-07-21) but not yet checked for at
    the content level. Term-overlap only (extract_salient_terms, utils/grounding.py) -- no NLI
    model, the smallest defensible version, same tool find_cross_source_contradictions already
    uses for a structurally similar cross-source comparison."""
    from utils.grounding import extract_salient_terms
    uncited_set = set(uncited_task_names)
    by_task = {}
    for f in deduped_findings:
        by_task.setdefault(f.get("task_name"), []).append(f)
    flagged = []
    for task_name, group in by_task.items():
        if task_name not in uncited_set or len(group) < 2:
            continue
        bad_summaries = [f.get("summary") or "" for f in group if not _is_citable_finding(f)]
        good_summaries = [f.get("summary") or "" for f in group if _is_citable_finding(f)]
        if not bad_summaries or not good_summaries:
            continue
        for bad in bad_summaries:
            bad_terms = extract_salient_terms(bad)
            if not bad_terms:
                continue
            for good in good_summaries:
                overlap = len(bad_terms & extract_salient_terms(good)) / len(bad_terms)
                if overlap > 0.5:
                    flagged.append(task_name)
                    break
            if task_name in flagged:
                break
    return flagged


def _build_findings_source_material(run_state: "RunState", task_names: Optional[set] = None) -> str:  # noqa: F821 — utils.run_state.RunState, annotation only
    """Everything FindingsWriter needs to write findings.md, assembled from RunState's structured
    per-task records rather than the Planner's own conversation — FindingsWriter is dispatched in
    a fresh context with no memory of what the Planner saw, so this is its entire evidence base.
    `run_state.data["findings"]` already accumulates a {source_url, summary} entry for EVERY
    dispatched task (Searcher tier AND nested Analyzer tier alike — see
    engine/orchestrator.py::_run_single_task's `run_state.add_finding` call, which fires
    unconditionally on every task, not just top-level ones), so this is a complete record of the
    run's real research, not a lossy approximation of it.

    Deduplicated by exact (source_url, summary) match before serializing — every completion-check
    retry that re-delegates the same task_name re-adds a finding without ever removing the stale
    one from the earlier round, so across a multi-attempt run the raw list accumulates exact
    repeats (confirmed live 2026-07-14: 25 entries for ~8-10 distinct pieces of research, e.g. the
    same `colombia_cultural_factors` summary appearing identically 5 times). Left as-is in
    `run_state.data` itself — `coverage()` only checks per-task_name presence of a real URL, which
    duplicates don't affect, and the raw list is the audit trail other tooling may want intact.

    `task_names` (2026-08-01, `_dispatch_per_facet_findings_writer_fix`): when given, scopes
    EVERYTHING below to just those tasks' own findings — not just findings_block, but also the
    "ALL URLS FETCHED THIS RUN" cross-reference section, which would otherwise still show every
    OTHER facet's fetched URLs and reopen the exact evidence-crowding surface this scoping exists
    to remove. Applied first, as an input filter, so every downstream step (dedup, budget,
    uncited-task accounting) already only ever sees the scoped subset — no separate scoped code
    path to keep in sync with the unscoped one."""
    findings = run_state.data.get("findings", [])
    if task_names is not None:
        # A depth>1 (Analyzer) finding's OWN task_name is the child dispatch's name (e.g. "Analyze
        # SEF D8 Visa page"), never one of the depth==1 facet names task_names scopes to -- without
        # also matching top_level_task_name, a per-facet FindingsWriter retry (
        # _dispatch_per_facet_findings_writer_fix) would silently drop real nested-Analyzer content
        # for the exact facet it was scoped to include (2026-08-17, same rollup fix as
        # _update_task_verification's ledger; see that function's docstring for the live incident).
        findings = [
            f for f in findings
            if f.get("task_name") in task_names or f.get("top_level_task_name") in task_names
        ]
    urls = run_state.data.get("fetched_urls", [])
    if task_names is not None:
        _scoped_urls_lower = {(f.get("source_url") or "").rstrip("/").lower() for f in findings}
        urls = [u for u in urls if (u.get("url") or "").rstrip("/").lower() in _scoped_urls_lower]
    deduped = _dedupe_findings(findings)
    # Real filename per entry, resolved from run_state's own fetched_urls record -- NOT left for
    # FindingsWriter to guess or reconstruct. Same fix shape as the delegate_tasks filename check
    # (2026-07-19): a model given only a bare URL has no reliable way to know the real saved
    # filename, and this project has now confirmed twice today (Searcher->Analyzer delegation, and
    # findings.md's own per-entry fabrication) that leaving a filename to be inferred is a real,
    # not theoretical, failure mode. Explicitly showing it here means FindingsWriter's own
    # findings.md entries can carry the real filename too, and any downstream re-verification
    # (Builder, PeerReviewer, a human) never has to guess it either.
    url_to_meta = {u.get("url", "").rstrip("/"): u for u in urls}
    # Split on whether source_url is a real fetched URL vs. add_finding's own bookkeeping
    # fallback (the bare task_name, used when a task produced no fetchable/reference URL at
    # all -- see orchestrator.py's _run_single_task). Rendering both under the same
    # "### Source: ..." heading (pre-2026-07-21) gave FindingsWriter no way to tell a real
    # citation from a placeholder, and a live qwen3:8b run cited the placeholder as if it were
    # a real URL (5/19 findings). Only real, http(s) source_urls become "### Source: ..."
    # entries now; the rest are named separately below as explicitly non-citable.
    #
    # A THIRD case, same treatment as the non-http fallback (2026-07-21, "4th synthesis-vanishing
    # mechanism"): source_url is real (fetched) but the entire summary is
    # sub_agent_timeout_minutes' own cutoff marker (orchestrator.py's task_deadline handling) --
    # the dispatch fetched something but was cut off before synthesizing it. There is nothing
    # real to cite here even though the URL is genuine, so it goes in uncited_task_names instead
    # of being rendered as if it were real content.
    def _heading_for(src: str) -> str:
        meta = url_to_meta.get(src.rstrip("/"), {})
        fn = meta.get("filename")
        title = meta.get("title")
        # A real title (tools/web.py::_extract_html_metadata, threaded through
        # record_fetched_url as of 2026-07-21) lets this heading match
        # FINDINGS_WRITER_INSTRUCTIONS' own required output format exactly -- turning most
        # entries into a copy/light-edit task instead of invent-a-title-then-write for every
        # one of them. Falls back to the plain "### Source: url" shape when no title was
        # extracted (non-HTML fetches, or extraction failed) -- never a hard requirement.
        heading = f"### [{title}]({src})" if title else f"### Source: {src}"
        return heading + (f" (saved as {fn})" if fn else "")

    citable = [f for f in deduped if _is_citable_finding(f)]
    entries = []
    for group in _collapse_multi_url_task_findings(citable):
        group_urls = [g.get("source_url") or "" for g in group["findings"]]
        first_heading = _heading_for(group_urls[0])
        # Strip any embedded [SYSTEM VERIFICATION/RELEVANCE WARNING...] marker before rendering
        # (2026-08-17, live incident): _is_citable_finding's URL-scoping fix (same date) means a
        # finding can now correctly stay citable while its OWN summary still carries a warning
        # marker about a DIFFERENT co-cited URL that turn (see that function's own docstring --
        # add_finding attaches one shared synthesis text to every URL fetched in a turn). Before
        # that fix, ANY warning marker wholesale-excluded the finding, so this text never reached
        # here at all; now it legitimately can. Confirmed live: a real, correctly-citable
        # globallawexperts.com finding still carried "[SYSTEM VERIFICATION WARNING: this summary
        # cites '...family-reunification...).<br', which does not match...]" verbatim in its
        # summary -- rendered as-is into findings.md by the deterministic fallback (FindingsWriter
        # itself kept returning nothing usable that run), the warning's OWN mentioned bad URL then
        # got picked up by findings.md-level grounding checks as if it were a real citation IN
        # findings.md, flagging `findings_ungrounded` on content that was otherwise entirely real
        # and correctly grounded -- a self-inflicted loop that reproduced identically on every
        # retry since the underlying research data never changed. The marker was only ever meant
        # to inform this project's OWN citability decision, never to be copied verbatim into a
        # human-facing artifact.
        clean_summary = _VERIFICATION_WARNING_BLOCK_RE.sub("", group["summary"] or "").rstrip()
        block = f"{first_heading}\n{clean_summary}"
        if len(group_urls) > 1:
            # See _collapse_multi_url_task_findings's own docstring: these URLs shared the exact
            # same task-level synthesis text, so it's kept ONCE above instead of repeated per URL --
            # but findings.md's own required format is still one entry per URL (FINDINGS_WRITER_
            # INSTRUCTIONS: "never per task"), so every additional real URL is still named here,
            # just without a second copy of the body text.
            more = "\n".join(f"- {_heading_for(u)}" for u in group_urls[1:])
            block += (
                f"\n\n(This same research pass also covered these additional real, citable "
                f"sources -- write a SEPARATE findings.md entry for each below too, using the "
                f"summary above unless a source-specific detail differs:\n{more})"
            )
        entries.append((group["task_name"] or group_urls[0], block))
    uncited_task_names = _uncited_task_names(deduped)

    # Positional-bias reorder (2026-07-22, see _reorder_findings_for_position_bias's own
    # docstring) -- applied here, AFTER entries is fully built but BEFORE the budget-truncation
    # scan below, so truncation still walks entries in their new (not chronological) order. The
    # uncited_note/omitted_note bookkeeping stays keyed by task_name, insensitive to entry order.
    entries = _reorder_findings_for_position_bias(entries)

    # 2026-07-19 QA audit ("real grounded content silently vanishes during synthesis" — 3
    # independently-fixed prior incidents, this is the common structural gap none of them closed):
    # unlike the Planner's own stream (context_budget_chars) and a sub-agent's own generation
    # (get_context_budget's guard in orchestrator.py's dispatch loop), FindingsWriter's INITIAL
    # instructions had NO size cap at all — this whole block was concatenated raw. A long,
    # many-retry run can accumulate enough real findings (each up to _FINDING_SUMMARY_BUDGET=1500
    # chars, MORE with an attached verification warning, deliberately never truncated — see
    # orchestrator.py's _run_single_task) to exceed the model's actual num_ctx before FindingsWriter
    # ever gets a turn, which Ollama then silently truncates from the TOP of its context window —
    # the exact "looks like model collapse" failure this project's context_budget_chars guard exists
    # to prevent everywhere else it can happen. Findings are appended in chronological dispatch
    # order (oldest first), so an uncontrolled top-truncation would silently drop the EARLIEST real
    # research first while whatever survived at the end (often a later, less-central re-delegation)
    # is all the model ever sees — plausibly the same shape as "real findings dropped, replaced by
    # weaker fabricated content" observed in the citation-truncation incident. Fixed the same way
    # the rest of the codebase handles this: an explicit, application-level budget instead of
    # relying on the backend's silent behavior — keep whole entries (never truncate one mid-way)
    # until the budget is spent, and tell the model exactly which task names were omitted so it can
    # acknowledge the gap rather than silently drop it (same "acknowledge, don't omit" pattern
    # check_thin_coverage's own escalation wording already uses).
    # fetched_block built BEFORE the findings budget loop (2026-07-23 fix, live regression):
    # it used to be appended to the return value AFTER findings_block's own budget check, with
    # zero accounting of its own -- confirmed live, tonight's run: findings_block correctly
    # capped at 48,308 chars under a 50,000 budget, but fetched_block (10,368 chars for this
    # run's 65 fetched URLs) got tacked on afterward anyway, for a true total of 58,676 --
    # 8,676 chars over the intended ceiling, on top of the model's own reasoning/output needs
    # within the same context window. Now computed first so its real cost can be reserved from
    # the SAME shared total budget before findings_block is capped, so the promise "everything
    # returned fits within budget" is actually kept, not just true for one of the two sections.
    # Fixed boilerplate around the two dynamic sections -- named here and reused verbatim in the
    # return statement below (not just measured then duplicated) so the two can never drift out
    # of sync with each other. Its own length must also come out of the shared budget: an
    # earlier version of this fix reserved fetched_block's cost but not this, and still landed
    # 499 chars over a 50,000 budget on tonight's real data -- close, but the promise is "the
    # TRUE total fits," not "the two dynamic sections add up correctly."
    _intro = (
        "REAL RESEARCH RESULTS FROM THIS RUN, one entry per dispatched Searcher/Analyzer task "
        "that returned a real citable source "
        "(this is your ENTIRE evidence base — you have no other memory of this run):\n\n"
    )
    _outro_label = (
        "\n\nALL URLS ACTUALLY FETCHED THIS RUN, for cross-reference — each file's full content is "
        "readable under its saved filename via read_workspace_file/grep_workspace_file if a "
        "summary above isn't detailed enough:\n"
    )
    # uncited_note computed here, BEFORE the budget math -- unlike omitted_note (which depends on
    # which findings entries the budget loop below ends up dropping, a genuine chicken-and-egg
    # problem), uncited_task_names is already fully known at this point, so its real rendered
    # cost can be reserved exactly rather than estimated.
    uncited_note = ""
    if uncited_task_names:
        # Order-preserving dedup -- a task redispatched across multiple retries (e.g. the same
        # task_name appearing many times) must only be named once here, not once per occurrence
        # (confirmed live, tonight's run: 'background' listed 10 times, pure wasted budget
        # characters and misleading noise -- looked like 10 distinct failed tasks, was 1).
        uncited_unique = list(dict.fromkeys(uncited_task_names))
        uncited_list = ", ".join(f"'{n}'" for n in uncited_unique[:20])
        uncited_note = (
            f"\n\n({len(uncited_unique)} dispatched task(s) produced no real fetched or "
            f"reference URL, so they have nothing citable: {uncited_list}. These are NOT source "
            f"entries above and must never be turned into one — do not invent a URL or title for "
            f"them. If they matter, note that this research was attempted but produced no "
            f"citable source, rather than fabricating one.)"
        )
    # budget_enabled is checked from here on, not `budget` itself -- budget legitimately reaches
    # 0 in the degenerate case (boilerplate/fetched_block alone consuming the whole allowance),
    # and `if budget:` on 0 would be falsy, wrongly falling into the "capping disabled" branch
    # right when capping to (near-)nothing is exactly what's needed.
    _raw_budget = get_context_budget()
    budget_enabled = bool(_raw_budget)

    # omitted_note's real cost can't be known yet (circular: it lists whichever findings entries
    # the budget loop below decides to drop) -- reserved as a generous estimate instead. 900
    # chars comfortably covers the template text plus 20 quoted task names at a generous ~35
    # chars each under a realistic budget; if the real note ends up smaller, that's unused slack,
    # never an overrun. Capped to a QUARTER of the raw budget, not a flat constant, so an
    # unrealistically tiny budget can't let this reserve alone exceed the whole thing and zero
    # out every section below it -- the reserve should degrade gracefully, never dominate.
    _omitted_note_reserve = min(900, _raw_budget // 4) if budget_enabled else 900

    boilerplate_len = len(_intro) + len(_outro_label) + len(uncited_note) + _omitted_note_reserve

    fetched_lines = [f"- {u.get('url')} (saved as {u.get('filename')})" for u in urls]
    budget = max(_raw_budget - boilerplate_len, 0)
    omitted_fetched_count = 0
    if budget_enabled:
        # Guard the degenerate case: enough fetched URLs that this section alone approaches or
        # exceeds the total budget, which would otherwise zero out findings_budget below and
        # silently drop EVERY finding just to make room for a URL list. Never let one section's
        # overflow starve the other to zero -- cap this section at half the total budget,
        # keeping whole lines only, same "note what's omitted" philosophy as findings_block.
        fetched_cap = budget // 2
        kept_lines = []
        used = 0
        for line in fetched_lines:
            if used + len(line) > fetched_cap:
                omitted_fetched_count += 1
                continue
            kept_lines.append(line)
            used += len(line) + 1  # +1 for the "\n" join separator
        fetched_block = "\n".join(kept_lines) or "(no URLs fetched yet)"
    else:
        fetched_block = "\n".join(fetched_lines) or "(no URLs fetched yet)"
    if omitted_fetched_count:
        fetched_block += f"\n... and {omitted_fetched_count} more URL(s), omitted here to stay within budget."

    omitted_task_names = []
    if budget_enabled:
        findings_budget = max(budget - len(fetched_block), 0)
        kept = []
        used = 0
        for task_name, block in entries:
            if used + len(block) > findings_budget:
                omitted_task_names.append(task_name)
                continue
            kept.append(block)
            used += len(block) + 2  # +2 for the "\n\n" join separator
        findings_block = "\n\n".join(kept) or "(no findings recorded yet)"
    else:
        findings_block = "\n\n".join(block for _, block in entries) or "(no findings recorded yet)"

    omitted_note = ""
    if omitted_task_names:
        # Order-preserving dedup -- a task redispatched across multiple retries (e.g. the same
        # task_name appearing many times) must only be named once here, not once per occurrence
        # (confirmed live, tonight's run: 'background' listed 10 times, pure wasted budget
        # characters and misleading noise -- looked like 10 distinct failed tasks, was 1).
        omitted_unique = list(dict.fromkeys(omitted_task_names))
        omitted_list = ", ".join(f"'{n}'" for n in omitted_unique[:20])
        omitted_note = (
            f"\n\n({len(omitted_unique)} more finding(s) exist for this run but were omitted "
            f"here to stay within the model's context budget: {omitted_list}. Do NOT silently "
            f"drop these — if they matter for the report, note that this research exists but its "
            f"detail wasn't available to you, rather than pretending it doesn't exist.)"
        )
    return f"{_intro}{findings_block}{omitted_note}{uncited_note}{_outro_label}{fetched_block}"
