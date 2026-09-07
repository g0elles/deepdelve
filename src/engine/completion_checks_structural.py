# COMPLETION_CHECKS tier -- structural/process checks (delegation happened? findings.md written and
# fresh? artifact exists? research breadth adequate? plan hygiene?), as opposed to GROUNDING_CHECKS'
# content-accuracy tier (engine/completion_checks_grounding.py). Split out of the former single
# engine/completion_checks.py (2098 lines, 2026-09-07 ponytail-audit finding) along the SAME
# boundary COMPLETION_CHECKS/GROUNDING_CHECKS already draws in engine/completion.py -- a pure move,
# no behavior change: every function below is unchanged from its prior body. Shared Verdict/Ctx/
# _citation_format_reminder stay in engine/completion_checks.py (now just the small shared core);
# GROUNDING_CHECKS' own members moved to engine/completion_checks_grounding.py instead.
#
# engine/completion.py still imports every name below (for real internal use -- building
# COMPLETION_CHECKS itself -- not just re-export) and re-exports it under `engine.completion` for
# test_structural_checks.py/finetune/* exactly as before this split; that external import surface
# does not change. A handful of local (function-body) `from engine.completion import ...` lines
# below call helpers that stay in completion.py (_capped/_consecutive_occurrences/_is_citable_
# finding/_dedupe_findings) -- see the original completion_checks.py history (git log) for why
# those stayed put (findings-evidence-assembly and starvation/capping machinery in completion.py
# also need them; a module-level import back would be circular).
import re
from typing import Optional

import config
from tools import get_workspace_file_content
from utils.run_state import get_fetched_urls
from utils.grounding import fully_ungrounded, partially_ungrounded, extract_cited_urls, parse_academic_references
from engine.orchestrator import _extract_required_facets, _instruction_entities, _EXCLUSION_CUE_RE
from engine.completion_checks import Ctx, Verdict

def check_not_delegated(ctx: Ctx) -> Optional[Verdict]:
    """A real, live-observed failure mode distinct from every other one fixed so far: the
    Planner writes/rewrites _todos.md across every nudge (satisfying "take an action" with
    write_todos instead of delegate_tasks) and answers from its own memory — sometimes
    explicitly narrating fake delegation that never happened, e.g. literally writing
    "After delegating the tasks to a human Searcher, here's what I've found:" despite
    delegate_tasks never once appearing in the tool-call log. Generic "you must verify"
    wording didn't stop this in testing; naming the specific wrong action (rewriting the
    plan, fabricating delegation narration) does, per the same pattern that fixed the
    missing_artifact re-delegation loop."""
    if ctx.delegated:
        return None
    todos_used = (ctx.quotas or {}).get("write_todos", {}).get("used", 0)
    escalation = ""
    if todos_used >= 2:
        escalation = (
            f" You have called write_todos {todos_used} times but delegate_tasks ZERO times — "
            f"rewriting the plan is not research and does not satisfy this requirement. Do NOT "
            f"call write_todos again. Do NOT write a report claiming you delegated or received "
            f"results from a Searcher when delegate_tasks was never actually called — that is "
            f"fabrication, not synthesis."
        )
    return Verdict(
        "not_delegated",
        "No `delegate_tasks` call was ever made — this looks like an answer from memory, not real research. Forcing verification.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}You are attempting to finish the task, but you never called delegate_tasks. Your training data can be stale or wrong — you MUST verify any facts with a real Searcher delegation before finishing.{escalation} Your ONLY next tool call must be delegate_tasks, with a real task_name/instructions/agent_id for each research angle. Only after receiving real results should you write (or overwrite) '{ctx.req_artifact}'.",
    )


_ITEM_COUNT_RE = re.compile(
    r'\b(?:identify|find|list|name|provide|give|include|select|recommend)\s+'
    r'(?:at least\s+|between\s+)?(\d+)'
    r'(?:\s*(?:to|-|–|—|and)\s*(\d+))?\b',
    re.IGNORECASE,
)


def _extract_requested_item_range(query: str) -> Optional[tuple[int, int]]:
    """Looks for an explicit enumerate-N-things request in the query text (e.g. 'identify 4 to 6
    niches', 'list at least 3 examples') -- conservative on purpose: only a small, well-established
    set of list-request verbs immediately followed by a number/range counts, so the vast majority
    of queries (no explicit count) correctly return None and never engage check_requested_count_
    shortfall at all. Returns (floor, ceiling) -- floor == ceiling for a bare 'N' with no range."""
    if not query:
        return None
    m = _ITEM_COUNT_RE.search(query)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    if lo <= 0 or lo > 50 or hi < lo:  # sanity guard against a nonsense/unrelated number match
        return None
    return (lo, hi)


def check_requested_count_shortfall(ctx: Ctx) -> Optional[Verdict]:
    """Catches a gap ONE LEVEL UPSTREAM of check_thin_coverage: that check verifies whether tasks
    that WERE delegated actually produced a real source; this instead asks whether the Planner even
    delegated ENOUGH of them in the first place, when the query itself states an explicit
    enumerate-N-things requirement (e.g. "identify 4 to 6 niches"). Confirmed live (gemma4:e4b
    bake-off, 2026-08-28): a run's very first write_todos/delegate_tasks call targeted only 2
    candidate niches for a query explicitly asking for 4-6, and nothing in the existing pipeline
    ever caught it -- coverage_check/thin_coverage only measure whether what WAS planned got real
    sources, never whether enough was planned -- so the run finished "successfully," citing real
    sources for both its 2 planned niches, converging on a report less than half the requested
    breadth with no warning anywhere.

    Conservative by construction, same philosophy as every other structural check in this project:
    only engages when _extract_requested_item_range finds an explicit list-request verb + number/
    range in the query (the large majority of queries have no such phrasing and are completely
    unaffected); uses ctx.run_state.coverage()['total'] (distinct depth==1 delegated task names) as
    a model-independent proxy for "how many distinct angles has the Planner even attempted" -- the
    same structural signal check_thin_coverage/check_uneven_task_investment already rely on, not a
    new Planner-authored schema. Compares against the RANGE FLOOR, not the ceiling (a query asking
    for "4 to 6" is satisfied by 4) -- and only fires when the shortfall is clear (floor - total >=
    2, and floor itself >= 3), never on a near-miss or a small ask, to keep false-positive risk low
    given a single delegated task CAN legitimately surface multiple report-level niches on its own.

    Not Builder/FindingsWriter-fixable -- delegating more distinct research angles is a Planner-only
    action (delegate_tasks isn't in either writer role's toolset), same reasoning as
    check_thin_coverage. Capped via the shared _capped helper for the same reason: a genuinely
    hard-to-satisfy count (a niche market that really doesn't have 4-6 viable candidates) must not
    starve every check below it forever."""
    if not config.get_setting("requested_count_check", {}).get("enabled", True):
        return None
    item_range = _extract_requested_item_range(ctx.run_state.data.get("query") or "")
    if item_range is None:
        return None
    floor, _ceiling = item_range
    if floor < 3:
        return None  # a 1-2 item ask is well within "one task can cover it" territory
    total = ctx.run_state.coverage()["total"]
    if (floor - total) < 2:
        return None

    from engine.completion import _capped, _consecutive_occurrences
    prior_same = _consecutive_occurrences(ctx.run_state, "requested_count_shortfall")
    if prior_same == 0:
        directive = (
            f"Your query explicitly asks for {floor} distinct items, but you have only delegated "
            f"{total} distinct research task(s) so far. One task rarely surfaces {floor} genuinely "
            f"distinct, well-evidenced items on its own -- delegate_tasks again for additional "
            f"candidate angles until you have enough real research to plausibly cover the "
            f"requested count, or explicitly narrow the report's scope and say so if you have "
            f"already tried and genuinely cannot find that many."
        )
    else:
        directive = (
            f"Still only {total} distinct research task(s) delegated against a request for "
            f"{floor}+ items after a prior warning. If you have genuinely tried and cannot find "
            f"more, say so explicitly in the report as an acknowledged gap rather than silently "
            f"delivering fewer than asked."
        )
    return _capped(ctx, "requested_count_shortfall", Verdict(
        "requested_count_shortfall",
        f"Query asks for {floor}+ distinct items but only {total} research tasks were delegated. Pushing agent to broaden the plan or acknowledge the gap.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ))


def check_missing_query_facet(ctx: Ctx) -> Optional[Verdict]:
    """Same family as check_requested_count_shortfall just above (did the Planner even scope this
    correctly at dispatch time, one layer before check_thin_coverage asks whether what WAS
    dispatched succeeded) -- catches gpt-oss:20b's documented, literature-validated tendency to
    neglect the harder half of a multi-facet query (2026-08-29, see ROADMAP.md/wiki
    Model-Bakeoff's "Passed" section note: "on multi facet queries it reliably abandons the harder
    half rather than fabricating... a genuine model capability limit, not a bug"). A prior
    dedicated investigation (RESEARCH.md §16-17) already fixed the part of this that was a genuine
    engine bug -- a facet silently vanishing from a report with nothing able to detect it -- via
    per-facet FindingsWriter dispatch and five sibling fixes. What's left is narrower: nothing
    parses the raw query up front to know which facets are REQUIRED, so the Planner is only
    prompted ("keep each slot single-facet," PLANNER_INSTRUCTIONS) to self-police it, and any drop
    is caught only after the fact via task-naming convention + coverage checks.

    Deliberately conservative, mirroring check_requested_count_shortfall's own construction:
    `_extract_required_facets` only ever returns a non-empty result when the query UNAMBIGUOUSLY
    enumerates 2+ facets via an explicit cue phrase ("X vs Y", "compare X and Y", "for each of A,
    B, C", "both X and Y") -- a query that merely mentions several proper nouns in passing, with no
    such cue, never engages this check at all, so the false-positive risk (nudging the Planner to
    redundantly split a legitimately single-facet plan) is structurally bounded at the extraction
    layer, not left to runtime heuristics here.

    Compares each required facet (a token set, e.g. {'Mexico', 'City'}) against the UNION of
    `_instruction_entities` extracted from every dispatched task's own name + instructions so far.
    A facet token counts as covered by an exact match OR a shared >=4-char PREFIX in either
    direction -- live-caught 2026-08-29, NOT a hypothetical: a real gpt-oss:20b run genuinely
    covering both "Germany" and "Japan" phrased its task instructions with the demonym adjective
    ("German regulation", "Japanese policy") rather than the bare country noun, and a strict exact
    match would have wrongly fired "missing" on a run that was actually fully covered -- exactly
    the false-positive class this check exists to avoid. A prefix match (not full fuzzy/edit-
    distance matching, which both extractors' looseness makes too risky to stack) directly covers
    this common country/demonym relationship ("German" is a prefix of "Germany", "Japan" is a
    prefix of "Japanese") while staying conservative: the >=4-char floor keeps a short facet token
    from spuriously prefix-matching an unrelated word.
    `_EXCLUSION_CUE_RE.sub` strips an exclusion clause from each task's text first, the same call
    `_dispatch_tasks_batch` already makes before its own exclusion-topic check -- otherwise a task
    that restates the missing facet only to rule it out of scope ("Focus on Lisbon; Mexico City is
    out of scope here") would wrongly count as "covering" it.

    Not Builder/FindingsWriter-fixable -- delegating a task for the missing facet is a Planner-only
    action (delegate_tasks isn't in either writer role's toolset), same reasoning as
    check_requested_count_shortfall. Capped via the shared _capped helper for the same reason: a
    facet that's genuinely unresearchable must not starve every check below it forever."""
    if not config.get_setting("facet_coverage_check", {}).get("enabled", True):
        return None
    facets = _extract_required_facets(ctx.run_state.data.get("query") or "")
    if len(facets) < 2:
        return None
    dispatched = ctx.run_state.data.get("dispatched_tasks", [])
    if not dispatched:
        return None  # check_not_delegated already owns this state

    covered = set()
    for t in dispatched:
        text = _EXCLUSION_CUE_RE.sub(" ", f"{t.get('task_name', '')} {t.get('instructions', '')}")
        covered |= _instruction_entities(text)

    def _token_covered(tok: str) -> bool:
        return any(
            tok == c or (len(tok) >= 4 and len(c) >= 4 and (tok.startswith(c) or c.startswith(tok)))
            for c in covered
        )

    missing = [f for f in facets if not all(_token_covered(tok) for tok in f)]
    if not missing:
        return None

    from engine.completion import _capped, _consecutive_occurrences
    missing_str = ", ".join(" ".join(sorted(f)) for f in missing)
    prior_same = _consecutive_occurrences(ctx.run_state, "missing_query_facet")
    if prior_same == 0:
        directive = (
            f"Your query names distinct required facets, but no delegated task's instructions "
            f"mention: {missing_str}. delegate_tasks for the missing facet(s) before finishing, "
            f"or explicitly state in the report why that facet is out of scope."
        )
    else:
        directive = (
            f"Still no delegated task mentions {missing_str} after a prior warning. If you have "
            f"genuinely determined it's out of scope, say so explicitly in the report as an "
            f"acknowledged gap rather than silently omitting it."
        )
    return _capped(ctx, "missing_query_facet", Verdict(
        "missing_query_facet",
        f"Query enumerates distinct facets but no delegated task mentions: {missing_str}. "
        f"Pushing agent to cover the missing facet(s).",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ))


def check_thin_coverage(ctx: Ctx) -> Optional[Verdict]:
    """ROADMAP Phase 5 ("Coverage accounting / ResearchMap") — distinct from every other check in
    this module: those all verify whether content that ALREADY EXISTS is grounded; this instead
    asks whether the Planner's own top-level research plan actually paid off, catching a report
    that could be perfectly grounded (every citation traces to a real fetch) yet still be thin
    because most of the Planner's own delegated angles came back with nothing usable and got
    quietly dropped rather than surfaced or retried. Reuses RunState.coverage() — see its own
    docstring for why this is built entirely from already-reliable, model-independent structural
    data (per-task fetch attribution, delegation depth) rather than a new Planner-authored schema.

    Conservative by construction, same philosophy as every other check here: fires when AT LEAST
    HALF of top-level tasks came back with no real source (ratio AT OR BELOW threshold, default
    0.5) AND there are enough of them for that ratio to mean something (min_tasks, default 2) — a
    single-task query (the common case for a simple factual lookup) that succeeded is 1.0
    regardless of "breadth" and never trips this; a single-task query that failed is caught by
    missing_findings/missing_artifact already, not this. Escalates like every other repeat-prone
    check here on a second consecutive occurrence — a nudge that already failed to move the ratio
    isn't worth repeating verbatim.

    At-or-below, NOT strictly-below (2026-07-23 fix; was strictly-below at ship time). Confirmed
    live: the most common non-trivial shape this check has to guard is exactly 2 top-level tasks
    (a query with 2 distinct facets) -- when one of the two comes back with zero sources, the
    ratio is EXACTLY 0.5, which the old strictly-below comparison let straight through. Real
    consequence, not theoretical: a run planned only 'background' (heuristic algorithms) and
    'colombian_culture' as its 2 tasks, 'background' got zero real sources, ratio landed exactly
    on the old threshold, this check stayed silent, and the final report ended up 100% about
    Colombian payroll with zero mention of the query's other half -- the run still reported
    'verified, no unresolved issues' the whole time. "Half your explicit tasks produced nothing"
    is already a real failure on its own terms; there's no principled reason 0.5 exactly should
    be treated as acceptable when 0.49 isn't.

    Capped via the shared _capped helper (2026-07-31, same landmine class as check_task_
    verification_flagged found the same night — this check is what motivated generalizing that
    check's own one-off cap into a shared mechanism; see ARCHITECTURE.md for the full incident
    writeup). This check sits ABOVE check_missing_findings/check_missing_artifact in
    COMPLETION_CHECKS and is not itself Builder/FindingsWriter-fixable — so without a cap, a run
    whose coverage never improves (a genuinely unfindable topic, not a model-capability problem)
    would starve the checks that actually dispatch a real writer role forever, exactly like
    task_verification_flagged did before its own fix, and this check's own escalated directive
    uses the identical broken-promise language ("say so explicitly in the report as an
    acknowledged gap... rather than silently omitting it") that only comes true if the pipeline
    actually reaches a writer dispatch. The iterative-deepening dispatch in run_completion_check
    (a genuine, self-resolving recovery attempt, up to max_deepening_rounds) still gets its own
    turn first — this cap only stops the CLASSIC Planner-nudge path once that budget and the
    redo/acknowledge cycle are both exhausted."""
    cov_cfg = config.get_setting("coverage_check", {})
    if not cov_cfg.get("enabled", True):
        return None
    threshold = cov_cfg.get("threshold", 0.5)
    min_tasks = cov_cfg.get("min_tasks", 2)
    coverage = ctx.run_state.coverage()
    if coverage["total"] < min_tasks or coverage["ratio"] > threshold:
        return None

    # untracked_delegation skip: same reasoning as check_task_verification_flagged's own counting
    # (2026-07-29 fix) -- this check's own redo directive says "reuse the exact same task_name...
    # do NOT invent a new task_name," so untracked_delegation firing in between is a direct symptom
    # of THIS check's own directive being violated, not an unrelated interruption. Uses the shared
    # _consecutive_occurrences instead of a hand-rolled loop (see that function's docstring).
    _tc_skip = frozenset({"untracked_delegation"})
    from engine.completion import _capped, _consecutive_occurrences
    prior_same = _consecutive_occurrences(ctx.run_state, "thin_coverage", _tc_skip)

    uncovered_list = ", ".join(f"'{t}'" for t in coverage["uncovered_task_names"][:5])
    if prior_same == 0:
        directive = (
            f"Only {coverage['covered']} of {coverage['total']} research tasks you delegated "
            f"actually turned up a real source ({uncovered_list} came back empty). Do NOT write "
            f"the final report around only the tasks that worked — delegate_tasks again for the "
            f"uncovered angles, phrased differently or with a narrower query if the first attempt "
            f"was too broad or too specific to find anything. Reuse the exact same task_name as "
            f"before for each angle you redelegate — only change the instructions/query wording, "
            f"do NOT invent a new task_name; a renamed task_name looks like a brand-new, "
            f"untracked angle to this system."
        )
    else:
        directive = (
            f"Coverage is STILL thin after a prior warning ({coverage['covered']}/{coverage['total']} "
            f"tasks with a real source). If you have already tried rephrasing and genuinely cannot "
            f"find sources for {uncovered_list}, say so explicitly in the report as an acknowledged "
            f"gap rather than silently omitting it — do not keep re-delegating the exact same query."
        )

    return _capped(ctx, "thin_coverage", Verdict(
        "thin_coverage",
        f"Only {coverage['covered']}/{coverage['total']} delegated research tasks produced a real source ({uncovered_list}). Pushing agent to cover the gap or acknowledge it.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ), skip_problems=_tc_skip)


def check_task_verification_flagged(ctx: Ctx) -> Optional[Verdict]:
    """The first genuinely task-scoped check in this pipeline (2026-07-26, VERIMAP-inspired, see
    _update_task_verification's own docstring for the full design rationale). Reads the ledger
    that function maintains on ctx.run_state.data["task_verification"] and fires when ANY task's
    every finding got excluded by _is_citable_finding -- distinct from check_thin_coverage (which
    only sees "zero real sources", i.e. a task that never produced anything at all) and from
    check_uneven_task_investment (which compares real-source COUNTS across covered tasks): this
    catches a task that produced findings, all of which turned out fabricated/off-topic/
    contradicted -- structurally indistinguishable from "genuinely uncovered" to every other check
    in this module, but a different failure with a different fix (redo with a different approach,
    not "delegate more"). Still one Verdict per attempt, same as every other check here (Phase 2 of
    the design -- actually independent per-task redispatch bypassing the Planner's own turn -- is
    explicitly deferred, see ROADMAP.md Pending) -- but the directive names the SPECIFIC flagged
    task(s) rather than nudging the whole run generically.

    Capped via the shared _capped helper (2026-07-31 live incident, gpt-oss AND Ornith-1.0-9B both
    hit this the same night; helper generalized the same night after a second, near-identical
    check -- check_thin_coverage -- turned out to have the exact same gap): this check sits ABOVE
    check_missing_findings/check_missing_artifact in COMPLETION_CHECKS and is not itself Builder/
    FindingsWriter-fixable (not in either _*_FIXABLE_PROBLEMS tuple) -- so as long as one task
    stays genuinely flagged, it wins first-match on EVERY attempt and permanently starves the
    checks that actually dispatch a real writer role. Confirmed live: the quota_exhausted branch's
    own directive text promises "the writer roles will note X as an acknowledged gap when they
    build the report" -- a promise this check's own priority position structurally prevented from
    ever coming true. findings.md never got written despite real, usable findings existing for
    every OTHER task; the run ended with a salvaged narration (or nothing) instead of a real
    report built from real evidence. Once this check has said its piece 3 times (redo, acknowledge,
    force_whole_rebuild's own one extra escalated attempt -- see CONSECUTIVE_SAME_PROBLEM_
    ESCALATION_THRESHOLD), _capped returns None so the pipeline falls through to missing_findings/
    missing_artifact -- see ARCHITECTURE.md for the full incident writeup and the standing test
    that enforces every non-self-resolving check in COMPLETION_CHECKS/GROUNDING_CHECKS calls
    _capped, not a hand-rolled equivalent."""
    cfg = config.get_setting("task_verification_check", {})
    if not cfg.get("enabled", True):
        return None
    ledger = ctx.run_state.data.get("task_verification", {})
    # gap_acknowledged (2026-08-16 live incident, see quota_exhausted branch below): once this
    # check has told the model to stop and accept a task as an unfixable gap, that decision must
    # stick even if a later completion-check retry's quota top-up makes quota_exhausted go back to
    # False -- excluded here so a re-flagged/still-flagged task already marked acknowledged never
    # re-enters the redo/stop directive cycle.
    flagged = sorted(
        name for name, entry in ledger.items()
        if entry.get("status") == "flagged" and not entry.get("gap_acknowledged")
    )
    if not flagged:
        return None

    # 2026-07-29 (live incident), generalized 2026-07-31: some interrupting problems are
    # themselves a DIRECT symptom of the model failing to comply with THIS check's own "stop
    # redelegating, reuse the exact task_name" directive (confirmed live: attempt 2's
    # untracked_delegation fired because the model tried redispatching the flagged task under a
    # new name instead of retrying it correctly) -- counting that as a genuinely different problem
    # breaks the streak and traps this check in its weakest "redo" wording forever instead of ever
    # escalating. Uses the shared _consecutive_occurrences (see its own docstring) instead of a
    # hand-rolled loop -- this exact loop used to be duplicated in run_completion_check's own
    # force_whole_rebuild counter, with its own independent copy of this same skip patch.
    _tvf_skip = frozenset({"untracked_delegation"})
    from engine.completion import _capped, _consecutive_occurrences
    prior_same = _consecutive_occurrences(ctx.run_state, "task_verification_flagged", _tvf_skip)

    # Quota-aware directive (2026-07-27, live regression): telling the Planner to "delegate_tasks
    # again" when its delegate_tasks quota is already exhausted is a directive it structurally
    # cannot follow. Confirmed live: with delegate_tasks tightened from 15 to 6 (to curb top-level
    # over-fanning, a separate fix), a real run hit exactly this collision — quota exhausted at 3/6
    # calls, this check kept firing "delegate_tasks again" for 4 more attempts, and the Planner
    # responded by narrating a full fake report as chat text instead of the tool call it couldn't
    # make (never written to disk, but 4 wasted attempts burned the whole 8-attempt completion-check
    # budget with zero report ever produced). Once quota is gone, the only honest instruction left is
    # to acknowledge the gap and stop — same acknowledged-gap language the STILL-flagged-after-a-
    # prior-warning branch already uses below, just reached one branch earlier.
    delegate_quota = (ctx.quotas or {}).get("delegate_tasks", {})
    quota_exhausted = delegate_quota.get("used", 0) >= delegate_quota.get("limit", float("inf"))

    flagged_list = ", ".join(f"'{n}'" for n in flagged[:5])
    subject = "this task" if len(flagged) == 1 else "these tasks"
    if quota_exhausted:
        # Mark these tasks as an accepted, permanent gap so a LATER retry_quota_topup-driven
        # quota refill can't flip quota_exhausted back to False and reissue "delegate_tasks
        # again" for a task this check already told the model to stop redelegating (2026-08-16
        # live incident: exactly that oscillation — stop, then redo, then stop again — burned an
        # 11-attempt completion-check budget with the model degrading into narrating instead of
        # calling tools, ending in an unverified salvage report).
        for name in flagged:
            if name in ledger:
                ledger[name]["gap_acknowledged"] = True
        directive = (
            f"Task(s) {flagged_list} produced ONLY fabricated, off-topic, or unverifiable sources, "
            f"but your delegate_tasks quota is exhausted — you cannot redelegate. Do NOT narrate a "
            f"report or findings content yourself. Say nothing further and stop; the writer roles "
            f"will note {flagged_list} as an acknowledged gap when they build the report from "
            f"whatever real results you already have."
        )
        # 2026-07-29 (live incident): this Verdict's own .warning field was a STATIC string
        # ("Pushing agent to redo them specifically") regardless of which branch actually fired --
        # a run whose delegate_tasks quota was exhausted, or whose model had already been told
        # TWICE to acknowledge the gap, still logged/recorded "redo them specifically" every time,
        # making the real cause of a stuck run harder to diagnose after the fact (confirmed while
        # investigating this exact run's _run_state.json). Each branch now states what it actually
        # told the model.
        warning = f"Task(s) {flagged_list} have only fabricated/unusable sources and delegate_tasks quota is exhausted — telling agent to stop and accept the gap."
    elif prior_same == 0:
        directive = (
            f"Task(s) {flagged_list} produced ONLY fabricated, off-topic, or unverifiable sources — "
            f"every result for {subject} was excluded from the real evidence base. The other "
            f"delegated tasks are fine, do not redo them. delegate_tasks again for {flagged_list}, "
            f"reusing the EXACT same task_name as before — try a different search approach or a "
            f"narrower query, but do NOT rename the task, before finishing."
        )
        warning = f"Task(s) {flagged_list} have only fabricated/unusable sources (task-level verification ledger). Pushing agent to redo them specifically."
    else:
        directive = (
            f"{flagged_list} STILL has no real usable source after a prior warning. If you have "
            f"genuinely tried and cannot find one, say so explicitly in the report as an "
            f"acknowledged gap for {flagged_list}, rather than silently omitting it."
        )
        warning = f"Task(s) {flagged_list} STILL have only fabricated/unusable sources after a prior warning — telling agent to acknowledge the gap instead of redoing again."

    return _capped(ctx, "task_verification_flagged", Verdict(
        "task_verification_flagged",
        warning,
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ), skip_problems=_tvf_skip)


def check_uneven_task_investment(ctx: Ctx) -> Optional[Verdict]:
    """check_thin_coverage's blind spot: a task that got AT LEAST ONE real source counts as fully
    "covered" there, regardless of whether that's 1 thin source or 6 rich ones. Confirmed live
    2026-07-23 (Ollama+gpt-oss, `i_want_documentation_on_heuristic_algoritms_for_de_20260723_185759`):
    the Planner split a query into 5 correctly-scoped single-facet tasks (no rabbit-holing, the
    single-facet-per-slot fix held) -- `background`/`top_5` (the "heuristic algorithms" half)
    were dispatched once, produced 1 usable source (a generic Wikipedia definition), and were
    never redispatched, while the other 3 tasks (Colombia culture/paydays/festivals) were
    redispatched repeatedly and ended with 6 rich sources between them. Both `background` and
    `top_5` counted as "covered" so thin_coverage never fired, and the resulting report silently
    answered only half the query with no acknowledged gap. This check catches that specific
    pattern: a covered-but-starved task sitting next to a richly-covered sibling.

    Deliberately only considers COVERED tasks (per_task_counts > 0) -- an uncovered task is
    thin_coverage's job, not this one; double-flagging the same underlying gap two different ways
    would just be redundant noise. Needs min_tasks covered tasks to compare (can't measure
    "uneven" with fewer than 2 data points) AND min_total_sources summed across covered tasks
    (default 4) -- guards against flagging a small, simple query where every task naturally has
    1-2 sources and any ratio between them looks "extreme" by construction; the absolute-volume
    gate is what tells a genuinely thin small query apart from real investment imbalance.

    REQUIRES BOTH findings.md AND ctx.req_artifact (final_report.md) to already exist
    (2026-07-23, two live regressions found the same day this check shipped, one after the
    other). This check reads ctx.run_state.coverage(), populated live during research
    independent of whether either file was ever actually WRITTEN. First regression: gating on
    findings.md alone still left this check ahead of check_missing_artifact in
    COMPLETION_CHECKS -- once findings.md existed but final_report.md didn't yet, it kept
    winning "first verdict wins" over check_missing_artifact, so the Builder never got
    dispatched at all. Confirmed live TWICE: one run ended with findings.md never written (fixed
    by the findings.md gate), the very next run then ended with findings.md written but
    final_report.md STILL never written (this second gate). Exact same regression class as
    check_untracked_delegation's earlier fix this session ("a hygiene nudge must never be able
    to block completion the way a real correctness gate does") -- the fix both times is
    requiring the artifacts this check cares about to already exist, same two-stage gate
    check_missing_findings/check_missing_artifact themselves enforce, positioning this
    conceptually alongside check_report_underuses_findings (which needs the same two artifacts)
    rather than check_thin_coverage (which deliberately runs before either exists). The
    escalate-after-3-consecutive/force_whole_rebuild machinery already caps how many attempts
    get burned once this check is actually allowed to fire; both bugs were about firing too
    EARLY, never about an unbounded retry count."""
    cov_cfg = config.get_setting("uneven_coverage_check", {})
    if not cov_cfg.get("enabled", True):
        return None
    if "findings.md" not in ctx.files or ctx.req_artifact not in ctx.files:
        return None
    coverage = ctx.run_state.coverage()
    counts = {name: n for name, n in coverage["per_task_counts"].items() if n > 0}
    min_tasks = cov_cfg.get("min_tasks", 2)
    if len(counts) < min_tasks:
        return None
    min_total_sources = cov_cfg.get("min_total_sources", 4)
    if sum(counts.values()) < min_total_sources:
        return None
    richest = max(counts.values())
    threshold = cov_cfg.get("threshold", 0.3)
    starved = sorted(name for name, n in counts.items() if n / richest < threshold)
    if not starved:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "uneven_task_investment":
            prior_same += 1
        else:
            break

    starved_list = ", ".join(f"'{n}'" for n in starved[:5])
    counts_summary = ", ".join(f"'{n}': {c}" for n, c in sorted(counts.items(), key=lambda kv: -kv[1]))
    if prior_same == 0:
        directive = (
            f"Some of your delegated tasks got MUCH less real research than others: {counts_summary} "
            f"(real sources per task). {starved_list} only found a thin/shallow source while other "
            f"tasks found several — do NOT let the well-researched tasks crowd this one out of the "
            f"final report. delegate_tasks again for {starved_list}, phrased differently or narrower "
            f"than the first attempt, before finishing. Reuse the exact same task_name as before for "
            f"each angle you redelegate — only change the instructions/query wording, do NOT invent "
            f"a new task_name; a renamed task_name looks like a brand-new, untracked angle to this "
            f"system."
        )
    else:
        directive = (
            f"{starved_list} is STILL thin relative to your other tasks after a prior warning "
            f"({counts_summary}). If you have genuinely tried and cannot find more, say so "
            f"explicitly in the report as an acknowledged gap rather than silently omitting that "
            f"part of the query."
        )

    return Verdict(
        "uneven_task_investment",
        f"Task(s) {starved_list} got far less research than their siblings ({counts_summary}). Pushing agent to reinforce the gap or acknowledge it.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    )


def check_findings_ungrounded(ctx: Ctx) -> Optional[Verdict]:
    """findings.md (Pass 1) was previously never grounding-checked at all — only
    final_report.md was. Confirmed live: a Planner that abandons real delegation partway
    through a run can fabricate the ENTIRE Pass-1 file from memory, and Pass 2 then
    treats it as ground truth (SESSION_STATUS.md tracked item #2). Checked BEFORE the
    missing-artifact/final-report gates because fabricated findings poison everything
    downstream — a final report rewritten from fabricated findings can never become
    grounded.

    Two gates, wholesale then per-entry. fully_ungrounded catches total fabrication ('no_urls'/
    'all_cited_urls_unverified'). partially_ungrounded (added 2026-07-19) additionally catches a
    findings.md that's only PARTLY fabricated — confirmed live: 6/15 entries citing an unfetched
    URL as their own primary source passed fully_ungrounded cleanly (9/15 were real), then Builder
    reacted to the untrustworthy mix by discarding almost all real content rather than risk keeping
    a fake entry, producing a nearly-empty final report despite 15 genuinely fetched sources. Only
    checks each entry's OWN heading URL, not every URL mentioned in a summary body — see that
    function's own docstring for why the original 'legitimately-mixed notes' tolerance still holds
    at the body-text level, just not for an entry's own claimed source."""
    gc_cfg = config.get_setting("grounding_check", {})
    if not (gc_cfg.get("enabled", True) and gc_cfg.get("check_findings", True)):
        return None
    if "findings.md" not in ctx.files:
        return None
    findings_content = get_workspace_file_content("findings.md") or ""
    findings_problem = fully_ungrounded(findings_content) or partially_ungrounded(findings_content)
    if not findings_problem:
        return None
    # This text is the Planner-facing FALLBACK only (used when no FindingsWriter is registered —
    # see run_completion_check's dispatch branch, which handles the normal case directly and never
    # shows this to the Planner at all). Must not tell the Planner to write anything itself — it
    # has no write_workspace_file tool as of 2026-07-14 (see PLANNER_INSTRUCTIONS).
    return Verdict(
        "findings_ungrounded",
        f"`findings.md` (Pass 1) fails the grounding check ({findings_problem}) — nothing in it traces to a source actually fetched this run. Pushing agent to rebuild it from real delegated results.",
        f"SYSTEM WARNING: 'findings.md' is not grounded in real research ({findings_problem}) — "
        + ("it contains no source URLs at all" if findings_problem == "no_urls"
           else "at least one finding's own claimed source doesn't match anything your Searcher(s) actually fetched this run" if findings_problem.startswith("unverified_entry_sources:")
           else "not one URL it cites matches anything your Searcher(s) actually fetched this run")
        + ". You cannot fix this yourself — you have no write_workspace_file tool. If you have not delegated enough real research yet, delegate it now with delegate_tasks. Otherwise stop calling tools entirely: a dedicated FindingsWriter role rebuilds findings.md automatically from your real delegated results once you stop.",
    )


def check_missing_findings(ctx: Ctx) -> Optional[Verdict]:
    """Pass-1 existence gate: the Planner's workflow is findings.md FIRST, final report
    second — but nothing structural enforced the first pass existing at all. Confirmed
    live twice (runs 10 and 11, 2026-07-11): the Planner skips findings.md, then
    "forgets" 29+ fetched files and writes an empty report claiming nothing was
    retrieved, or narrates the report as chat. Making Pass 1 structurally required
    gives the final report a real, on-disk substrate to be rewritten from.

    Escalates on repeat, same spirit as check_missing_artifact/check_no_urls — but confirmed
    live 2026-07-13 that this problem type's failure SHAPE differs from missing_artifact's: a run
    produced literally zero content (no tool call, no text) in response to this exact nudge for 6
    consecutive attempts, then genuinely self-corrected with real findings.md content on the 7th.
    Unlike missing_artifact (which never self-corrected without intervention), late recovery is
    real here — so this deliberately does NOT get the aggressive early-cutoff
    run_completion_check applies to missing_artifact; it only strengthens the wording and, on
    repeat, hands the model concrete proof real material already exists (its actual fetched
    URLs), mirroring check_no_urls's own escalation for the same reason."""
    if not config.get_setting("grounding_check", {}).get("check_findings", True):
        return None
    if "findings.md" in ctx.files:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "missing_findings":
            prior_same += 1
        else:
            break

    # This text is the Planner-facing FALLBACK only (used when no FindingsWriter is registered —
    # see run_completion_check's dispatch branch, which handles the normal case directly and never
    # shows this to the Planner at all). Must not tell the Planner to write anything itself — it
    # has no write_workspace_file tool as of 2026-07-14 (see PLANNER_INSTRUCTIONS).
    if prior_same == 0:
        directive = (
            "No 'findings.md' exists yet, and you have no way to write one yourself — you have no "
            "write_workspace_file tool. If you have not finished delegating all the research this "
            "query needs, delegate the remaining tasks now with delegate_tasks. If you believe you "
            "already have enough real delegated results, stop calling tools entirely: a dedicated "
            "FindingsWriter role builds findings.md automatically from what you've delegated, once "
            "you stop."
        )
    else:
        directive = (
            f"'findings.md' is STILL missing after {prior_same} prior warning(s). You cannot "
            f"write it yourself. If there is more research this query genuinely needs, delegate "
            f"it now with delegate_tasks. Otherwise stop calling tools entirely — the automatic "
            f"FindingsWriter step needs you to stop delegating, not to keep acting."
        )

    escalation = ""
    if prior_same >= 1:
        real_urls = get_fetched_urls()
        url_list = "\n".join(f"- {u['url']}" for u in real_urls[:20]) or "(none fetched yet)"
        escalation = f" For reference, the EXACT URLs actually fetched this run so far:\n{url_list}"

    return Verdict(
        "missing_findings",
        "`findings.md` (Pass 1) was never written — the two-pass discipline was skipped. Pushing agent to write it before the final report.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}{escalation}",
    )


def check_stale_findings(ctx: Ctx) -> Optional[Verdict]:
    """check_missing_findings's complement: that check is existence-only
    (`if "findings.md" in ctx.files: return None`), so once findings.md is written ONCE it can
    never be flagged again no matter how much MORE research the Planner delegates afterward.
    Confirmed live 2026-07-24 (`--resume-run` on
    `what_is_the_boiling_point_of_water_at_sea_level_an_20260724_141403`): the original run wrote
    a real 9-entry findings.md, then the resumed Planner kept delegating more research on its own
    initiative -- run_state's citable finding count grew well past what was on disk at write time,
    findings.md's mtime never changed, and nothing in COMPLETION_CHECKS/GROUNDING_CHECKS would
    ever have caught it: check_findings_ungrounded only re-validates the EXISTING content's own
    citations, never whether newer research is simply absent from it. Builder would then have
    built the final report from a stale substrate, silently dropping every finding gathered after
    the first write -- the same evidence-abandonment shape as check_report_underuses_findings, one
    stage further upstream.

    Deliberately a COUNT-based staleness marker (`findings_written_citable_count` on run_state,
    set by run_completion_check right after every successful FindingsWriter dispatch), NOT a
    set-difference against findings.md's own cited URLs the way check_report_underuses_findings
    works one stage downstream -- that comparison would also fire on _build_findings_source_material's
    own INTENTIONAL budget truncation (large evidence bases omit some findings by design, already
    surfaced to the model via its own omitted_note, not a bug), and this check would have no way to
    tell "genuinely never included" apart from "correctly deferred for budget reasons". Comparing
    against a count captured at write time sidesteps that entirely: it only fires when MORE real,
    distinct, citable findings exist now than existed at the moment findings.md was last
    (re)written, which is true staleness regardless of what budget truncation did within that
    earlier write. Uses the same _is_citable_finding/_dedupe_findings definitions
    _build_findings_source_material itself uses, so "citable" means the same thing everywhere in
    this module."""
    if not config.get_setting("stale_findings_check", {}).get("enabled", True):
        return None
    if "findings.md" not in ctx.files:
        return None  # check_missing_findings's job
    written_count = ctx.run_state.data.get("findings_written_citable_count")
    if written_count is None:
        return None  # findings.md predates this marker (e.g. hand-authored) -- nothing to compare
    from engine.completion import _dedupe_findings, _is_citable_finding
    current_count = len(_dedupe_findings(
        [f for f in ctx.run_state.data.get("findings", []) if _is_citable_finding(f)]
    ))
    new_count = current_count - written_count
    if new_count <= 0:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "stale_findings":
            prior_same += 1
        else:
            break

    if prior_same == 0:
        directive = (
            f"You have delegated {new_count} more real, citable finding(s) since 'findings.md' "
            f"was last written -- it is now out of date and missing that newer research. A "
            f"dedicated FindingsWriter role will refresh it automatically from ALL of your "
            f"current results once you stop delegating; do not write the final report from the "
            f"stale version."
        )
    else:
        directive = (
            f"'findings.md' is STILL missing {new_count} newer finding(s) after a prior warning. "
            f"If you have finished delegating, stop calling tools entirely so the automatic "
            f"FindingsWriter refresh can run."
        )

    return Verdict(
        "stale_findings",
        f"`findings.md` is stale -- {new_count} real finding(s) delegated since it was last written are missing from it. Pushing agent to refresh it before the final report.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    )


def _findings_facet_coverage(ctx: Ctx) -> tuple[dict[str, set], list[str]]:
    """by_task: {task_name: {real URLs run_state.data["findings"] recorded for that task}},
    dropped: sorted task names among by_task with ZERO of their URLs cited anywhere in
    findings.md. Factored out of check_findings_underuses_evidence (2026-08-01) so its verdict
    and _dispatch_per_facet_findings_writer_fix's real per-facet scoping can never drift onto two
    different notions of "dropped" -- one computation, read twice, the same relationship
    _facet_coverage already has with check_report_underuses_evidence/
    _dispatch_per_facet_builder_fix one layer downstream.

    `_is_citable_finding` exclusion (2026-08-16, sibling fix to `_facet_coverage`'s own -- see that
    function's docstring for the full live incident): without it, a task whose ONLY finding is
    fabricated/off-topic (a `[SYSTEM WARNING...]` marker) still counts here as "real evidence that
    needs findings.md coverage" -- but `_build_findings_source_material` (FindingsWriter's actual
    evidence blob) already excludes that same finding via `_is_citable_finding` and routes it into
    its "these tasks have nothing citable" note instead. Without this exclusion, this check could
    tell FindingsWriter it "dropped" a task that was never handed to it as real citable material in
    the first place -- an unwinnable, contradictory nudge."""
    from utils.grounding import extract_cited_urls, _urls_prefix_match
    from engine.completion import _is_citable_finding
    findings_urls = {u.rstrip('/') for u in extract_cited_urls(get_workspace_file_content("findings.md") or "")}
    by_task: dict[str, set] = {}
    for f in ctx.run_state.data.get("findings", []):
        if f.get("depth") != 1:
            continue
        name = f.get("task_name")
        url = (f.get("source_url") or "").strip()
        if not name or not url.startswith("http"):
            continue
        if not _is_citable_finding(f):
            continue
        by_task.setdefault(name, set()).add(url.rstrip('/'))
    dropped = sorted(
        name for name, urls in by_task.items()
        if not any(u in findings_urls or any(_urls_prefix_match(u, f) for f in findings_urls) for u in urls)
    )
    return by_task, dropped


def check_findings_underuses_evidence(ctx: Ctx) -> Optional[Verdict]:
    """check_report_underuses_findings' own diagnosis, one stage further upstream: that check
    compares final_report.md against findings.md, but findings.md itself can already have
    silently dropped an entire real, delegated research task before Builder ever gets a turn --
    a gap this project already named as a known risk (2026-07-24, "worth a
    check_findings_underuses_evidence-shaped check if this recurs... no check currently exists for
    this specific gap") but left unbuilt, since that session's only observed instance was a single
    dropped finding (12/13 kept) inside otherwise-successful FindingsWriter output.

    Confirmed live 2026-07-26, a far more severe recurrence:
    `explain_the_health_benefits_of_green_tea_and_separ_20260726_113029` delegated two clean,
    balanced top-level tasks (7 real green-tea sources, 5 real Roman-Empire sources --
    `run_state.coverage()` correctly showed both `covered`, ratio 1.0, so check_thin_coverage/
    check_uneven_task_investment both correctly stayed silent, there was no research-volume
    problem). FindingsWriter then wrote a 30-line `findings.md` titled "Green Tea Health
    Findings" containing ZERO mention of the Roman Empire task -- not thin, not truncated, an
    entire covered task's real evidence vanished outright. `final_report.md` then correctly
    built from what findings.md gave it (0 unused findings.md URLs, so check_report_underuses_
    findings had nothing to catch either) and the run's own final verdict never surfaced this --
    just `Retry budget exhausted (uncited_claims)`, an unrelated problem. Half the original
    two-facet query silently disappeared with no check anywhere naming it.

    Deliberately per-TASK, not a flat citation-count ratio (unlike check_report_underuses_
    findings): a ratio comparison of findings.md's own citations against itself is vacuous --
    findings.md trivially "uses" 100% of whatever it happens to contain. The only way to see a
    whole task go missing is to compare against run_state's real, independent research record
    (the same source check_thin_coverage/check_uneven_task_investment already trust), checking
    whether each COVERED top-level task has AT LEAST ONE of its real fetched URLs cited anywhere
    in findings.md -- exactly the binary signal this incident needed. Same
    "genuinely never included vs. correctly deferred for budget reasons" concern
    check_stale_findings' own docstring raises for a similar-looking set-difference design does
    NOT apply here: a task with ZERO of its real URLs surviving isn't a partial/budget-truncated
    inclusion (_build_findings_source_material keeps whole entries, never truncates one
    mid-way -- see its own docstring), it's total omission."""
    cfg = config.get_setting("findings_evidence_check", {})
    if not cfg.get("enabled", True):
        return None
    if "findings.md" not in ctx.files:
        return None
    by_task, dropped = _findings_facet_coverage(ctx)
    min_tasks = cfg.get("min_tasks", 2)
    if len(by_task) < min_tasks:
        return None
    if not dropped:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "findings_underuses_evidence":
            prior_same += 1
        else:
            break

    dropped_list = ", ".join(f"'{n}'" for n in dropped[:5])
    if prior_same == 0:
        directive = (
            f"'findings.md' has NO entries at all for delegated task(s) {dropped_list}, even "
            f"though real research results exist for them this run. This looks like an entire "
            f"research angle was dropped while consolidating, not just thinly covered. Rebuild "
            f"'findings.md' from ALL current results, making sure every delegated task with real "
            f"sources gets at least one entry — do not let one topic crowd another out entirely."
        )
    else:
        directive = (
            f"'findings.md' STILL has no entries for {dropped_list} after a prior warning. Do not "
            f"just lightly edit the existing draft — add real entries for these tasks' sources too."
        )

    from engine.completion import _capped
    return _capped(ctx, "findings_underuses_evidence", Verdict(
        "findings_underuses_evidence",
        f"'findings.md' has no entries at all for delegated task(s) {dropped_list}, despite real research results existing for them. Pushing agent to rebuild it with everything included.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    ))


def check_missing_artifact(ctx: Ctx) -> Optional[Verdict]:
    """A model that already has real delegated research results in its own context but still
    hasn't written the artifact tends to respond to a generic nudge by re-delegating again
    (a real failure mode observed in testing: it satisfies "take a real action" with
    delegate_tasks instead of write_workspace_file). Naming and forbidding that specific
    wrong action, rather than only naming the right one, measurably changes behavior on
    small models — same principle as the existing Anti-Looping prompt rules, applied
    structurally here since the prompt-level rule alone didn't hold under a nudge.

    Also escalates on repeat failures — confirmed live 2026-07-12: a run with 24 real fetched
    URLs and a fully-populated findings.md still got this exact nudge 5 times in a row, and the
    model responded each time with confident "Task completed, no further action required" prose
    without ever once attempting write_workspace_file. Two changes address that: (1) the nudge's
    wording escalates with each consecutive occurrence instead of repeating verbatim (a small
    model may get stuck in a rut on an identical system message), and (2) findings.md's actual
    content is quoted directly in the nudge — the prior wording's "use whatever findings you
    already have" assumed the model could still recall them amid several turns of accumulated
    quota-error clutter; showing them removes that assumption."""
    if ctx.req_artifact in ctx.files:
        return None
    forbid_redelegate = (
        " You already have research results above from your delegated task(s) — do NOT call "
        "delegate_tasks again. Your ONLY next action must be write_workspace_file."
        if ctx.delegated else ""
    )

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "missing_artifact":
            prior_same += 1
        else:
            break

    # Only two tiers, deliberately kept in lockstep with run_completion_check's
    # CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD (currently 3): with that threshold, a retry
    # nudge only ever gets BUILT for occurrences 1 and 2 of this problem — the 3rd consecutive
    # occurrence is cut off before a nudge is even constructed (see that threshold's own comment).
    # So whichever wording tier fires on occurrence 2 (prior_same == 1) is the LAST thing the
    # model will ever see for this problem — it must already be the strongest framing, not a
    # middle step that implies more chances are coming.
    if prior_same == 0:
        directive = (
            f"You are attempting to finish the task, but the required final artifact "
            f"'{ctx.req_artifact}' is missing from the workspace. Writing your answer as a "
            f"chat message does NOT complete the task."
        )
    else:
        directive = (
            f"'{ctx.req_artifact}' is STILL missing after a prior warning ({prior_same + 1} "
            f"consecutive checks now). A text response claiming the task is done does not "
            f"count — only a file that actually exists on disk does. This is your last "
            f"realistic chance before the run ends and whatever partial content already "
            f"exists is used instead. Do not respond with another text-only message."
        )

    findings_excerpt = ""
    if "findings.md" in ctx.files:
        raw = get_workspace_file_content("findings.md") or ""
        excerpt = raw[:2500]
        if len(raw) > 2500:
            excerpt += "\n...[truncated — the full content is already on disk in findings.md]"
        findings_excerpt = (
            f"\n\nHere is the ACTUAL content of findings.md, verbatim, so there is no ambiguity "
            f"about what real material you already have to write from:\n---\n{excerpt}\n---"
        )

    return Verdict(
        "missing_artifact",
        f"Required artifact `{ctx.req_artifact}` is missing from the workspace. Pushing agent to create it.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}{forbid_redelegate} Call write_workspace_file(filename='{ctx.req_artifact}', content=...) right now, using whatever findings you already have — an imperfect report that exists beats a perfect one that doesn't.{findings_excerpt}",
    )


def check_academic_citation_style_abandoned(ctx: Ctx) -> Optional[Verdict]:
    """Structural check (COMPLETION_CHECKS tier — needs only ctx.content/report_style, not the
    expensive real_grounding_problem scan): catches a report that was instructed to write in
    ACADEMIC style (in-text `(Author, Year)` + a References section) but abandoned that format
    entirely, reverting to STANDARD style's inline `[Title](URL)` links instead.

    Confirmed live (2026-08-25, the 6th run of the academic_citation_existence_problem live-
    exercise effort — see session_status/CURRENT.md): a Builder draft correctly used
    `(Vaswani et al., 2017)` inline but had no References section, so check_non_url_citation fired
    correctly (the citation was genuinely unresolved). The NEXT draft didn't add the missing
    References section — it deleted every `(Author, Year)` citation from the text instead, keeping
    only bracket-link citations, because a real URL sitting right on the line trivially satisfies
    check_no_urls/check_non_url_citation/check_uncited_claims/academic_citation_existence_problem
    (parse_academic_references finds nothing to flag when there's nothing academic-shaped left to
    parse). Every one of those checks verifies "is what's here grounded", never "is what's here in
    the INSTRUCTED format" — so abandoning the style entirely was, from the model's own
    cost-minimizing perspective, the cheapest way to stop getting nudged, and it worked: the run
    that surfaced this reached a clean completion-check pass on a report using zero academic
    citations while report_style was "academic". This check closes that gap directly instead of
    relying on prompt wording alone (the user's own call: "we cannot allow the model to do what it
    wants").

    Scoped to "academic" only: "answer" style's own required shape already embeds a bracket link
    inside `(Source: [Title](URL))`, so there's no equivalent "abandoned the style entirely" signal
    to detect there yet — a report has to have SOME citation activity already (extract_cited_urls
    finds at least one real URL) for this to fire at all; a report with zero citations of any kind
    is check_no_urls'/check_missing_findings's problem, not this one.

    Uses parse_academic_references (a real, resolvable numbered References entry — the same
    ground truth academic_citation_existence_problem itself relies on), not a loose in-text
    `(Name, Year)`-shaped regex: a first attempt at this check used
    utils.grounding._ACADEMIC_CITATION_ANYWHERE_RE (designed for a deliberately permissive
    section-exemption gate elsewhere, see that regex's own docstring) and it false-matched "WMT
    2014" — a benchmark name followed by a bare year, no citation at all — as a real citation on
    the EXACT failing report this check exists to catch, producing a false negative on live
    verification before this ever shipped. parse_academic_references' own structural requirement
    (an actual References-section entry) doesn't have that false-positive surface."""
    if ctx.report_style != "academic" or not ctx.content:
        return None
    if parse_academic_references(ctx.content):
        return None  # a real, resolvable References section exists somewhere
    if not extract_cited_urls(ctx.content):
        return None  # no citations of ANY kind yet — check_missing_findings/check_no_urls own this
    return Verdict(
        "report_style_violation",
        f"`{ctx.req_artifact}` was instructed to use academic `(Author, Year)` citations but "
        f"contains none anywhere — it has silently reverted to standard-style URL links instead.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' was instructed to cite "
        f"sources as `(Author, Year)` with a matching numbered References section (this run's "
        f"citation format is ACADEMIC style), but the current draft contains ZERO citations in "
        f"that format anywhere — every citation is now a plain `[Title](URL)` link instead. "
        f"Deleting the required citation format to dodge a prior warning is NOT a valid fix. "
        f"Rewrite '{ctx.req_artifact}' so EVERY claim carries an in-text `(Author, Year)` citation "
        f"(first author's surname) immediately after the claim, with a matching numbered "
        f"References entry at the end for each one (`N. Author, A. (Year). Title. <the real URL "
        f"you fetched>`) — keep the content, restore the citation format, and do not remove these "
        f"citations again.",
    )


def check_untracked_delegation(ctx: Ctx) -> Optional[Verdict]:
    """Distinct from check_not_delegated (which catches ZERO delegation): the Planner dispatches a
    task via delegate_tasks BEFORE ever writing it into _todos.md -- PLANNER_INSTRUCTIONS step 2
    says to write todos "before dispatching any of them," but nothing enforced that order.
    Confirmed live 2026-07-22: a 'background' task was dispatched (burning 14 web_search calls
    chasing a source that didn't exist) with no corresponding write_todos entry ever written; the
    Planner then wrote a real, todo-tracked plan that re-covered the same ground under
    'background_heuristics' -- pure wasted duplication of the run's shared web_search quota.

    Model-independent structural signal, same philosophy as check_thin_coverage: a top-level
    (depth==1) dispatched task_name that never appears anywhere in the CURRENT _todos.md content.
    Gated on write_todos having been called at least once THIS run -- PLANNER_INSTRUCTIONS step 1
    explicitly sanctions skipping write_todos entirely for a simple single-task query, and this
    must never flag that intended fast path. Excludes engine-driven deepening-round tasks
    (task_name always prefixed "Follow-up: ", see _select_deepening_tasks) -- the Planner never
    chooses those names itself, so they were never meant to be in its own todos.

    Placed LAST among the pre-grounding checks (after missing_findings/missing_artifact): this is
    a process-efficiency nudge, not a correctness gate -- a run with a more urgent problem should
    fix that first, this can wait a cycle.

    Fires AT MOST ONCE per run, deliberately NOT escalating/repeating like every other check here
    (live-confirmed regression, 2026-07-22): a run that kept renaming and redispatching the same
    angle across retries (a SEPARATE, real problem in its own right) produced a new untracked
    variant on every single attempt, so this check kept firing, kept consuming the retry budget,
    and the run ended "Retry budget exhausted... could NOT be fully verified" over a hygiene
    nudge — even though final_report.md itself may have been perfectly fine. Wasted delegate_tasks
    quota is real but low-severity; it must never be strong enough to block a run's completion the
    way a genuine correctness gate (missing_artifact, not_grounded, ...) is meant to."""
    todos_used = (ctx.quotas or {}).get("write_todos", {}).get("used", 0)
    if todos_used == 0:
        return None
    prior_attempts = ctx.run_state.data.get("completion_check_attempts", [])
    if any(a.get("problem") == "untracked_delegation" for a in prior_attempts):
        return None
    todos_text = (get_workspace_file_content("_todos.md") or "").lower()
    if not todos_text:
        return None
    top_level_names = {
        f.get("task_name") for f in ctx.run_state.data.get("findings", [])
        if f.get("depth") == 1 and f.get("task_name") and not f["task_name"].startswith("Follow-up: ")
    }
    # Word-boundary, NOT plain substring: "background" is a plain substring of the unrelated
    # "background_heuristics" (confirmed live -- that's the EXACT pair this check exists to catch),
    # and a naive `in` test would call it "tracked" on that coincidence alone. "_" counts as a
    # \w character, so \b sees no boundary inside "background_heuristics" and correctly treats it
    # as one distinct token, not a match for the shorter name.
    untracked = sorted(
        n for n in top_level_names
        if n and not re.search(r'\b' + re.escape(n.lower()) + r'\b', todos_text)
    )
    if not untracked:
        return None

    untracked_list = ", ".join(f"'{n}'" for n in untracked[:5])
    directive = (
        f"You dispatched {untracked_list} via delegate_tasks, but {'it' if len(untracked) == 1 else 'they'} "
        f"never appear in your own _todos.md -- you delegated before writing your plan, not after. "
        f"If a later slot already covers the same ground, do NOT dispatch yet another duplicate task "
        f"for it; just note in your plan that this angle is already covered. This is a one-time "
        f"reminder for future runs -- it will NOT block this run from finishing."
    )
    return Verdict(
        "untracked_delegation",
        f"Delegated task(s) {untracked_list} were never added to the written plan — likely duplicate/wasted effort. Pushing agent to stop redispatching untracked angles.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    )

