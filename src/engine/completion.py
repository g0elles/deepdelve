# Completion-check verdict engine, extracted from engine/tui.py (2026-07-12).
#
# WHY THIS SHAPE: the old run_completion_check was a ~250-line if/elif chain of giant
# triple-assignment f-strings. Twice (bd307f4, and again on run 13's regulation branch) an
# inserted branch silently swallowed the next `elif` header — both bodies merged, the later
# assignment won, and the file still parsed. The checks were fine; the container was the hazard.
# Now each problem type is one function returning a Verdict (or None), walked in an ordered list:
# first verdict wins, and there are no elif headers left to swallow. Adding a check = one function
# + one list entry. test_structural_checks.py's verdict matrix pins every problem's routing.
import asyncio
import math
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional

import config
from agent_framework import Message
from tools import tool_quotas_ctx, get_workspace_files, get_workspace_file_content, writer_gate_ctx
from utils.run_state import get_fetched_urls, get_search_health
from utils.grounding import (
    fully_ungrounded, partially_ungrounded, real_grounding_problem, split_into_heading_sections,
    find_cross_source_contradictions, cheap_grounding_problems, _is_null_finding_summary,
)
from engine.orchestrator import (
    topup_quota_pool, available_sub_agents_ctx, _extract_excluded_topics, get_context_budget,
    _looks_like_renamed_task,
)

DEFAULT_MAX_COMPLETION_CHECK_ATTEMPTS = 3


class Verdict(NamedTuple):
    problem: str      # recorded in _run_state.json's completion_check_attempts
    warning: str      # shown to the user via notify()
    inject: str       # SYSTEM WARNING message appended to the model's input


@dataclass
class Ctx:
    """Facts every check reads. Built once per completion check, cheap by construction —
    grounding_problem is the one expensive fact, filled only if the pre-grounding checks pass."""
    req_artifact: str
    attempt: int
    max_attempts: int
    delegated: bool
    files: list
    content: Optional[str]
    quotas: Optional[dict]
    run_state: "RunState"  # noqa: F821 — utils.run_state.RunState, annotation only
    grounding_problem: Optional[str] = None  # set between the two check stages

    @property
    def last_chance_prefix(self) -> str:
        return "THIS IS YOUR FINAL ATTEMPT. " if (self.attempt + 1) >= self.max_attempts else ""


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
    cov_cfg = config.cfg.get("settings", {}).get("coverage_check", {})
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
    cfg = config.cfg.get("settings", {}).get("task_verification_check", {})
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
    cov_cfg = config.cfg.get("settings", {}).get("uneven_coverage_check", {})
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
    gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})
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
    if not config.cfg.get("settings", {}).get("grounding_check", {}).get("check_findings", True):
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
    if not config.cfg.get("settings", {}).get("stale_findings_check", {}).get("enabled", True):
        return None
    if "findings.md" not in ctx.files:
        return None  # check_missing_findings's job
    written_count = ctx.run_state.data.get("findings_written_citable_count")
    if written_count is None:
        return None  # findings.md predates this marker (e.g. hand-authored) -- nothing to compare
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
    cfg = config.cfg.get("settings", {}).get("findings_evidence_check", {})
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
    cov_cfg = config.cfg.get("settings", {}).get("report_coverage_check", {})
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
    if ratio >= threshold:
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
    # the file"). Literature (arXiv:2406.01297, the "self-correction blind spot": models are
    # measurably worse at fixing errors in their OWN prior output than identical errors framed as
    # external input) suggests a full write_workspace_file regeneration -- which the old wording
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
    cov_cfg = config.cfg.get("settings", {}).get("report_evidence_check", {})
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("claim_unsupported")):
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
    escalation = ""
    if no_urls_count >= 2:
        # Words alone didn't work the first time ("add real citation links" was
        # already said once) — handing back the exact URL list removes any excuse to
        # keep failing the same way. Confirmed live: a model that failed this same
        # check twice in a row, both times with real sources already sitting in its
        # own findings, never once copied one in on its own.
        real_urls = get_fetched_urls()
        url_list = "\n".join(f"- {u['url']}" for u in real_urls[:20]) or "(none fetched yet)"
        escalation = (
            f" This is the {no_urls_count}th time in a row you have written this report "
            f"with ZERO hyperlinked sources. Naming a source in prose (e.g. \"(World Bank, "
            f"2020)\") does NOT count as a citation. Here are the EXACT URLs actually "
            f"fetched this run — use these, copied verbatim, do not paraphrase or "
            f"invent your own:\n{url_list}\nEvery single claim must end with a real "
            f"markdown link `[Title](URL)` using one of the URLs above."
        )
    return Verdict(
        "not_grounded",
        f"`{ctx.req_artifact}` contains zero hyperlinked sources — no citations at all. Pushing agent to add real ones.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' does not contain a single `[Title](URL)` link anywhere — you named sources in prose but never actually cited them. The previous draft has been moved aside. Rewrite '{ctx.req_artifact}' using the exact format `- **[Title](URL)**` for every source, with real URLs your Searcher(s) actually returned in their findings.{escalation}{_redelegate_directive(ctx)}",
    )


def check_regulation_unsupported(ctx: Ctx) -> Optional[Verdict]:
    """The URL is real and fetched, but the specific regulation number attributed to it
    doesn't exist anywhere in that source's content — a misattributed or invented law
    number wearing a legitimate citation. Confirmed live (run 12): 'Ley 1906 de 2021'
    cited to a fetched Mintic page about the 2025-2027 strategy, no '1906' in it."""
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("regulation_unsupported")):
        return None
    return Verdict(
        "regulation_unsupported",
        f"`{ctx.req_artifact}` names a regulation whose own cited source never mentions that regulation's number ({gp}) — likely a misattributed or invented identifier.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attributes a specific regulation ({gp}) to a source whose content never mentions that number anywhere. Naming a law the cited source does not contain is fabrication even when the URL itself is real and was fetched. The previous draft has been moved aside. Either delegate a Searcher to fetch the regulation's actual text or official page and cite THAT for the identifier, or rewrite the claim using only what the cited source actually says — without a law number you cannot support.{_redelegate_directive(ctx)}",
    )


def check_quote_paraphrased(ctx: Ctx) -> Optional[Verdict]:
    """A quoted span (implying "this is the source's own exact words") that's actually a
    paraphrase — see utils/grounding.py::find_paraphrased_quotes for the live case that motivated
    this (2026-07-24): a report quoted a plausible-sounding "sunset colors" sentence and attributed
    it to a real, fetched source whose actual text says something factually equivalent but
    differently worded. The underlying claim was true and traceable, so no other grounding check
    catches it — this is specifically about textual exactness of something presented as exact."""
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("quote_paraphrased")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("non_url_citation")):
        return None
    return Verdict(
        "non_url_citation",
        f"`{ctx.req_artifact}` attributes at least one claim to something that isn't a real URL ({gp}) — pushing agent to fix it.",
        f"SYSTEM WARNING: '{ctx.req_artifact}' attributes at least one claim to a non-URL citation ({gp}) — e.g. a bare parenthetical like \"(DANE, 2020)\" or a \"Source: <description>\" line with no link. This is exactly as unverifiable as a fabricated URL — there is nothing to check it against. The previous draft has been moved aside. Every single claim must end with a real, hyperlinked `[Title](URL)` using a URL your Searcher(s) actually returned this run. If you don't have a real fetched URL for a specific claim, either delegate to get one or remove the claim entirely — do not attribute it to an organization name, a year, or a vague description instead.{_redelegate_directive(ctx)}",
    )


def check_stub_source(ctx: Ctx) -> Optional[Verdict]:
    """The URL was really fetched, but every fetch of it returned only a paywall/not-found
    shell (a 200 soft-404) — the citation is hollow even though the fetch 'succeeded'.
    Confirmed live (run 14, 2026-07-12): a model-INVENTED El Tiempo URL answered 200 with
    ~5KB of subscription chrome, was recorded as a real fetch, and passed the hard URL gate.
    Distinct correction from not_grounded: the model must find a genuinely different source
    (or the publisher's working URL), not just re-cite something it already fetched."""
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("stub_source")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("nli_unsupported")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("topical_mismatch")):
        return None
    return Verdict(
        "topical_mismatch",
        f"`{ctx.req_artifact}` cites a source that shares terms with the claim and isn't contradicted by it, but a topical-relevance check found the source is about a different subject entirely ({gp}).",
        f"SYSTEM WARNING: '{ctx.req_artifact}' cites a real, fetched source that shares words with a claim but appears to be about a DIFFERENT SUBJECT entirely, not the one the claim is actually about ({gp}). This is the acronym-collision pattern (e.g. a source about a place or organization that happens to share an abbreviation with the real subject). The previous draft has been moved aside. Re-check that the cited source is genuinely about the claim's real subject, not just sharing a term or acronym with it, and rewrite or drop the claim if it isn't.{_redelegate_directive(ctx)}",
    )


def check_uncited_claims(ctx: Ctx) -> Optional[Verdict]:
    """The report's citations are all real, but its claims are structurally decoupled from
    them — figure-bearing claim lines with no citation on the line (e.g. a table of numbers
    plus a detached '### Source URLs' list, run 14's exact shape). Every line-scoped check
    passes vacuously on that format, so nothing ties any specific figure to any specific
    source. NOT quarantined (like no_urls, unlike the fabrication verdicts): the content may
    be fine — the fix is re-attaching citations, and the model needs its own draft visible
    to do that."""
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("uncited_claims")):
        return None
    return Verdict(
        "uncited_claims",
        f"`{ctx.req_artifact}`'s figures aren't tied to sources — claim lines carry no citation of their own ({gp}), so none of them can be verified against anything.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}'{ctx.req_artifact}' states specific figures on lines that carry no citation ({gp}). A separate list of source URLs does NOT tie any claim to any source — every claim line (including every table row) must carry its own `[Title](URL)` on the SAME line, using a URL your Searcher(s) actually fetched this run. Rewrite '{ctx.req_artifact}' keeping the content but attaching to each claim line the exact fetched URL that supports it; if no fetched source supports a figure, remove the figure rather than leaving it uncited.",
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
    X" is detected identically at both dispatch time and report-write time."""
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
    ARCHITECTURE.md -- not a live incident this time, the audit itself caught it first). This
    check's problem name ("propagated_ungrounded") is in NEITHER _BUILDER_FIXABLE_PROBLEMS nor
    _FINDINGS_WRITER_FIXABLE_PROBLEMS and had no escalation/cap logic at all -- positioned right
    before check_report_underuses_findings/evidence/check_not_grounded in GROUNDING_CHECKS, an
    unresolved propagated-content condition could permanently starve all three the same way
    check_task_verification_flagged and check_thin_coverage did before their own fixes."""
    if not ctx.content:
        return None
    findings = ctx.run_state.data.get("findings", []) if ctx.run_state else []
    if not findings:
        return None
    from utils.grounding import extract_salient_terms
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
                        f"re-verified. Do not simply repeat it in '{ctx.req_artifact}'; either confirm "
                        f"it against a real fetched source or omit it.",
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


# Ordered: first verdict wins. GROUNDING_CHECKS only run once every pre-grounding check passes
# (delegation happened, findings.md exists and is grounded, the artifact exists) because
# real_grounding_problem is the one expensive fact and needs the artifact's content to exist.
# A new check is one function above + one entry here — and one row in the verdict matrix test.
COMPLETION_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_not_delegated,
    check_thin_coverage,
    check_task_verification_flagged,
    check_findings_ungrounded,
    check_missing_findings,
    check_stale_findings,
    check_findings_underuses_evidence,
    check_missing_artifact,
    # Both require findings.md AND the final artifact to already exist (own docstrings explain
    # why -- two live regressions, 2026-07-23) -- placed after both existence checks above so
    # the list's own ordering documents that requirement, even though each check's internal
    # gate already enforces it regardless of position.
    check_uneven_task_investment,
    check_untracked_delegation,
]

# The problem names COMPLETION_CHECKS' own members can produce, one-to-one with the list above --
# used by the cross-tier starvation yield below to detect "COMPLETION_CHECKS as a WHOLE TIER kept
# winning" as distinct from "the SAME problem kept winning" (_consecutive_occurrences' narrower
# question). A new COMPLETION_CHECKS entry needs its problem name added here too (see that list's
# own "one row in the verdict matrix test" reminder -- this is the same kind of paired update).
_COMPLETION_TIER_PROBLEMS = frozenset({
    "not_delegated", "thin_coverage", "task_verification_flagged", "findings_ungrounded",
    "missing_findings", "stale_findings", "findings_underuses_evidence", "missing_artifact",
    "uneven_task_investment", "untracked_delegation",
})

GROUNDING_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_claim_unsupported,
    check_no_urls,
    check_stub_source,
    check_regulation_unsupported,
    check_quote_paraphrased,
    check_non_url_citation,
    check_nli_unsupported,
    check_topical_mismatch,
    check_uncited_claims,
    check_excluded_topic,
    check_cross_source_contradiction,
    check_propagated_ungrounded_content,
    # Breadth, not accuracy -- deliberately placed AFTER every citation-ACCURACY check above (no
    # point demanding more citations while the ones that already exist are still wrong) but BEFORE
    # the generic catch-all, so a report that both under-cites AND has one bad citation gets the
    # bad-citation problem fixed first.
    check_report_underuses_findings,
    # Same breadth-not-accuracy category as its sibling above, one layer more specific (per-TASK
    # zero-coverage, not a flat ratio) -- see its own docstring (2026-07-29, RESEARCH.md Sec.14h).
    check_report_underuses_evidence,
    check_not_grounded,  # generic catch-all: fires on ANY grounding problem — keep it LAST
]

# Problems whose bad draft gets quarantined (renamed aside) before the retry, and which count as
# "the check the quarantined draft actually failed" when restoring it at the final verdict.
# run_completion_check derives its quarantine branch from this tuple (findings_ungrounded
# quarantines findings.md instead of the artifact) — one list, no second copy to forget.
_QUARANTINE_PROBLEMS = ("not_grounded", "claim_unsupported", "non_url_citation",
                        "regulation_unsupported", "quote_paraphrased", "stub_source",
                        "nli_unsupported", "topical_mismatch", "findings_ungrounded")

# Problems fixable by rewriting `req_artifact` from the SAME findings.md, no new research needed —
# dispatched to a fresh-context Builder (+ PeerReviewer check) by run_completion_check's
# Write->Review->Fix loop instead of growing the Planner's own conversation. The complement
# (not_delegated) genuinely needs more/different research, which only the Planner can decide and
# delegate, so that one still falls through to the classic inject-into-Planner path below.
#
# CAVEAT (this comment's own assumption doesn't fully hold): several of these checks'
# `verdict.inject` text (check_no_urls, check_regulation_unsupported, check_non_url_citation,
# check_stub_source, plus the hallucinated-URL not_grounded message, all via the shared
# `_redelegate_directive` helper) explicitly tells the reader to "delegate a Searcher" /
# "Your ONLY next tool call must be delegate_tasks" when no new sources exist yet -- that
# instruction is worded for the PLANNER, which has `delegate_tasks`. Builder does NOT (its tools
# are read_workspace_file/grep_workspace_file/write_workspace_file/think_tool only), so embedding
# that text verbatim into a Builder dispatch hands it a genuinely impossible instruction.
# Live-confirmed 2026-07-28: a Builder correction cycle got stuck narrating "I will delegate a
# Searcher... this delegation is required before any further report writing can occur" across
# multiple retries instead of ever rewriting the file, because that's literally what its
# (wrong-audience) instructions told it to do. FindingsWriter's own dispatch branch already avoids
# this exact trap (see its docstring: "Deliberately NOT verdict.inject -- worded for the PLANNER
# fallback path... would be actively confusing to FindingsWriter, which has the opposite tool
# set") -- Builder's branch never got the same treatment until this fix.
# See _BUILDER_NO_DELEGATE_CLARIFICATION below for the fix.
_BUILDER_FIXABLE_PROBLEMS = ("missing_artifact", "not_grounded", "claim_unsupported",
                             "non_url_citation", "regulation_unsupported", "quote_paraphrased",
                             "stub_source", "nli_unsupported", "topical_mismatch", "uncited_claims",
                             "excluded_topic_present", "cross_source_contradiction",
                             "report_underuses_findings")

# Appended to every Builder-dispatch instruction (both the classic and force_whole_rebuild
# branches) right after verdict.inject -- a single shared clarification rather than hand-rewriting
# 13 separate problem messages, since the contradiction only matters for the subset that mention
# delegation at all and the clarification is a no-op (ignored) for the rest.
_BUILDER_NO_DELEGATE_CLARIFICATION = (
    "\n\nNOTE: you are the Builder role and do NOT have a delegate_tasks tool -- you cannot "
    "delegate new research no matter what the problem description above says. If it mentions "
    "delegating a Searcher or says your only next tool call must be delegate_tasks, that "
    "instruction does not apply to you: instead, remove or rewrite the specific unverifiable "
    "claim so the report no longer depends on it, using only what findings.md already contains.\n\n"
)

# Findings-authoring problems, fixable by a fresh-context FindingsWriter (+ PeerReviewer check)
# from this run's REAL structured results (see _build_findings_source_material) — the Planner
# itself no longer writes findings.md at all (2026-07-14 architecture change: giving the Planner
# that job meant a findings.md retry grew the Planner's OWN conversation exactly the way Builder
# was invented to prevent for final_report.md — confirmed live the same day, a benchmark run hit
# 4 consecutive findings_ungrounded retries before exhausting its budget with nothing written).
# Requires "FindingsWriter" registered as a sub-agent (see src/app.py) — when it isn't (or
# dispatch_task is None), both problems fall back to the classic inject-into-Planner path so an
# older/custom SubAgentConfig setup that hasn't added FindingsWriter doesn't just silently stop
# working.
#
# "findings_underuses_evidence" is deliberately ABSENT — same reasoning as
# "report_underuses_evidence"'s own absence from _BUILDER_FIXABLE_PROBLEMS one layer downstream
# (see that tuple's comment). The single combined-instruction version (routed through here until
# 2026-08-01) got a live-confirmed negative result: FindingsWriter given a complete, well-under-
# budget (16.5K chars under a 50K settings.context_budget_chars) 4-facet evidence blob in ONE
# dispatch wrote real content for only 1 of 4 facets — the identical evidence-crowding pattern
# already diagnosed for Builder, one layer upstream. Dispatched instead by its own bespoke
# per-facet branch in run_completion_check (_dispatch_per_facet_findings_writer_fix), each
# dispatch scoped to only ITS OWN facet's findings via _build_findings_source_material's
# task_names filter — see RESEARCH.md §16.
_FINDINGS_WRITER_FIXABLE_PROBLEMS = ("missing_findings", "findings_ungrounded", "stale_findings")


def _quarantine_artifact(req_artifact: str, attempt: int) -> None:
    """Rename the bad artifact out of the model's visible workspace instead of just telling it to
    'overwrite' it. A small model that still sees its own wrong prior draft in the workspace tends
    to re-condition on it rather than truly restart — this removes that anchor."""
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if path and os.path.exists(path):
            os.rename(path, path + f".rejected_attempt_{attempt}")
    except Exception:
        pass


def _restore_quarantined_draft(req_artifact: str, problem: str) -> bool:
    """Final-verdict fallback, tried BEFORE narration salvage: if the run ends with the artifact
    missing but a quarantined draft exists, restore the most recent draft with a loud header
    naming the unresolved check. A quarantined draft is a REAL report that failed exactly one
    known check — strictly more useful to a human than the model's meta-narration about rewriting
    it. Confirmed pattern (runs 11 and 13, 2026-07-11): after quarantine, the model narrated
    ABOUT the rewrite across the whole retry budget instead of doing it, so salvage kept
    delivering deliberation monologue while a complete draft sat in .rejected_attempt_N."""
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if not path or os.path.exists(path):
            return False
        rejected = sorted(
            (p for p in (f"{path}.rejected_attempt_{n}" for n in range(1, 10)) if os.path.exists(p)),
        )
        if not rejected:
            return False
        with open(rejected[-1], "r", encoding="utf-8") as f:
            draft = f.read()
        banner = (
            f"> **QUARANTINED DRAFT (restored)** — this draft failed the completion check "
            f"({problem}) and the model never produced a corrected rewrite. The flagged claims "
            f"are UNVERIFIED and at least one citation was found not to support what it is "
            f"attached to. Review before trusting.\n\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(banner + draft)
        return True
    except Exception:
        return False


_NARRATED_SALVAGE_BANNER = (
    "> **AUTO-RECOVERED DRAFT** — the model narrated this content as chat text instead of "
    "calling `write_workspace_file`, across the full retry budget. This has NOT passed the "
    "grounding check and its claims are UNVERIFIED. Review before trusting it.\n\n"
)

_DETERMINISTIC_SALVAGE_BANNER = (
    "> **AUTO-RECOVERED DRAFT** — FindingsWriter produced no usable output twice in a row "
    "(including one immediate retry). This is assembled directly and deterministically from this "
    "run's real research data (`RunState.findings`) — it was never written or reviewed by a "
    "model, so it is unorganized/unedited, but every entry traces to a source this run actually "
    "fetched.\n\n"
)


def _salvage_narrated_report(req_artifact: str, last_assistant_text: str, banner: str = _NARRATED_SALVAGE_BANNER) -> bool:
    """Structural fallback for a real, recurring pattern (documented in the reference project too,
    surviving multiple rounds of prompt-only fixes there): the model narrates a complete,
    well-formatted report as chat text instead of ever calling write_workspace_file, across the
    entire retry budget. Rather than throw away real content because a specific tool call didn't
    fire, auto-persist the model's own last substantial response — clearly marked as unverified
    salvage, not a substitute for the grounding check. Returns True if a salvage write happened.
    Two callers: `_dispatch_writer_review_fix` (immediately after each Write dispatch, so a
    narrating model gets salvaged on attempt 1 instead of burning the whole retry budget first —
    added 2026-07-18) and `run_completion_check`'s final-verdict path (the original, last-resort
    use, for the classic inject-into-Planner flow when no writer-role dispatch is configured).

    `banner` is overridable (2026-07-26) so `_dispatch_writer_review_fix`'s deterministic-content
    salvage path (see its own docstring) can reuse this exact write logic with an accurate banner
    instead of the misleading default text, which specifically claims the model narrated
    something — not true when the content being salvaged is `_build_findings_source_material`'s
    own deterministic evidence text, never model-authored at all."""
    if not last_assistant_text or len(last_assistant_text.strip()) < 200:
        return False
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if not path:
            return False
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        salvage = banner + last_assistant_text.strip()
        with open(path, "w", encoding="utf-8") as f:
            f.write(salvage)
        return True
    except Exception:
        return False


def _ensure_writer_quota_headroom(pool: dict) -> None:
    """A Write->Review->Fix cycle (Builder writing final_report.md, or FindingsWriter writing
    findings.md — see _dispatch_writer_review_fix) can burn up to 2 `write_workspace_file` calls
    in a single completion-check retry (the initial write, plus one corrective Fix pass if
    PeerReviewer flags issues) — against the SAME shared cumulative pool every role's
    `write_workspace_file` calls draw from (see `build_quota_pool`'s docstring: one pool across
    every role, by design). The standard per-attempt `topup_quota_pool` (called just before this)
    already covers the default config fine — not currently starved in practice — but a config
    with a low `write_workspace_file` limit/topup would starve a writer role specifically
    mid-cycle, degrading it to the same "narrate instead of write" failure the Planner used to be
    prone to, one level down. Rather than a separate reserved pool (a bigger structural change,
    and against the shared-pool design), this tops up ONLY the one tool this cycle actually needs,
    and only by the exact headroom it could need — not a blanket amount that would also quietly
    inflate every other role's budget."""
    entry = pool.get("write_workspace_file")
    if entry is None:
        return
    needed = 2  # the writer's initial write + one possible corrective Fix pass
    headroom = entry["limit"] - entry["used"]
    if headroom < needed:
        entry["limit"] += (needed - headroom)


def _ensure_reader_quota_headroom(pool: dict, needed: int = 2) -> None:
    """Mirror of _ensure_writer_quota_headroom, for `read_workspace_file` -- found live 2026-07-20,
    fixed 2026-07-21, sizing corrected 2026-07-29. A Write->Review->Fix cycle needs PeerReviewer to
    actually open the artifact (its 'REVIEW: CLEAN' is only trusted if the quota shows a real read
    happened -- see _dispatch_writer_review_fix's reads_before/reads_after gate above) plus the Fix
    pass often re-reading source content, against the SAME shared cumulative pool every role's
    read_workspace_file calls draw from. `settings.retry_quota_topup` now also replenishes this
    tool per completion-check attempt (2026-07-29 fix, see config_template.yaml) -- this per-cycle
    top-up is a SECOND, narrower guarantee on top of that, protecting each individual
    Write->Review->Fix cycle within one attempt regardless of how many prior cycles already ran.
    Confirmed live twice this pairs of fixes were still insufficient before this sizing correction:
    2026-07-20 (3 remediation cycles exhausted a 30-call pool; final BuilderFix pass self-reported
    'Due to workspace tool quota limits...' and silently dropped a whole section) and 2026-07-27
    (an 8-cycle escalation chain starved a late Builder Fix pass out of re-reading a 42-line
    findings.md, despite this guard already being in place -- the FIXED needed=2 default assumed
    only one reviewer read, but PEER_REVIEWER_INSTRUCTIONS (prompts.py) requires PeerReviewer to
    read BOTH final_report.md AND findings.md when reviewing the report, not just the target
    artifact -- 2 reviewer reads + 1 possible Fix re-read = 3, not 2). Callers now pass the real
    number of reads their specific writer role's review cycle requires instead of relying on the
    one-size-fits-all default."""
    entry = pool.get("read_workspace_file")
    if entry is None:
        return
    headroom = entry["limit"] - entry["used"]
    if headroom < needed:
        entry["limit"] += (needed - headroom)


async def _dispatch_writer_review_fix(dispatch_task, writer_role: str, req_artifact: str,
                                       write_instructions: str, attempt: int, notify,
                                       deterministic_fallback: Optional[str] = None,
                                       recommended_tool: str = "write_workspace_file") -> None:
    """Write -> Review -> Fix, all fresh-context sub-agent dispatches, none of which touch the
    Planner's own conversation. Shared by both writer roles that exist for exactly this reason —
    Builder (writes/fixes final_report.md from findings.md) and FindingsWriter (writes/fixes
    findings.md from this run's real structured results, see _build_findings_source_material) —
    same loop shape, different writer role/artifact/source material. Caller
    (run_completion_check) is responsible for quarantine, quota top-up, and run_state bookkeeping
    around this call — this function only runs the dispatch sequence. Raises on any dispatch
    failure so the caller can fall back to the classic inject-into-Planner path for this cycle
    rather than silently doing nothing.

    Capped at 4 dispatches total (Write, one immediate Write-retry only if the first produced
    nothing usable, Review, optional Fix) — no unbounded nesting.

    `deterministic_fallback` (2026-07-26): only ever passed by the FindingsWriter call site, as
    `_build_findings_source_material(run_state)`'s own raw output. Confirmed live
    (`explain_the_health_benefits_of_green_tea_and_separ_20260726_103135`, gpt-oss/Ollama): the
    prior isolated-empty-response assumption (see the immediate-retry fix's own comment a few
    lines below, "confirmed live all 3 empty responses ... were isolated, never two in a row")
    does NOT always hold — that same run hit SIX consecutive completion-check attempts (3-8)
    where FindingsWriter produced nothing usable on BOTH the original dispatch and its immediate
    retry, every single time, burning the entire remaining budget with `findings.md` never
    written even though 61 real findings existed the whole time. Unlike a final_report.md
    narration salvage (which needs the model to have said SOMETHING), this evidence text is
    already assembled deterministically and independently of the model producing anything —
    `_build_findings_source_material`'s own entries already use the exact heading format
    `findings.md` requires (`_heading_for`, see its docstring), so it's usable as `findings.md`
    content directly, not just as writer input. Builder never gets this treatment: its own draft
    can't be synthesized without the model, there is no equivalent non-LLM fallback for it."""
    # Snapshot think_tool's usage BEFORE the write dispatch (2026-07-22, PIVOT arXiv:2605.11225,
    # RESEARCH.md §1): both BUILDER_INSTRUCTIONS and FINDINGS_WRITER_INSTRUCTIONS already tell the
    # writer to use think_tool before finalizing ("<Show Your Thinking>"), but a prompt-only nudge
    # has a well-documented history in this project of being unreliable on small local models --
    # this is the same reads_before/reads_after quota-delta VERIFICATION pattern already proven a
    # few lines below for PeerReviewer's read_workspace_file, applied to the writer's think_tool
    # instead. Fetched once here (not re-fetched per snapshot) since tool_quotas_ctx is one shared
    # object for the life of this dispatch.
    pool = tool_quotas_ctx.get()
    think_before = pool.get("think_tool", {}).get("used") if pool else None

    # Structural gate (2026-07-22): FindingsWriter only -- see writer_gate_ctx's own docstring in
    # tools/core.py. Builder is deliberately never gated; its instructions correctly require
    # reading findings.md FIRST. Fresh per dispatch (write_done always starts False) and reset in
    # a finally so a gate never leaks into a sibling dispatch (PeerReviewer's read below, or a
    # later run) if this one raises. `recommended_tool` (2026-08-16 live incident, see check_
    # writer_gate's own docstring): the gate's block message must name whichever tool THIS
    # dispatch's own instructions actually told the model to use -- a per-facet ADD-ONLY dispatch
    # (recommended_tool="edit_workspace_file") whose block message still said "call
    # write_workspace_file now" left the model with no correct next step at all; confirmed live, it
    # just kept retrying blocked reads a few times then gave up with nothing written, rather than
    # trying the tool its own instructions actually named.
    gate_token = writer_gate_ctx.set(
        {"write_done": False, "recommended_tool": recommended_tool, "target_file": req_artifact}
    ) if writer_role == "FindingsWriter" else None
    try:
        write_result = await dispatch_task(f"{writer_role}Fix_attempt{attempt + 1}", write_instructions, writer_role)
    finally:
        if gate_token is not None:
            writer_gate_ctx.reset(gate_token)
    think_after = pool.get("think_tool", {}).get("used") if pool else None
    think_tool_skipped = think_before is not None and think_after == think_before

    # Immediate narration salvage (2026-07-18 bake-off finding): a writer role "Finishing" its
    # turn is NOT the same as it having called write_workspace_file — confirmed live twice this
    # session (qwen2.5:3b-instruct as FindingsWriter, same root cause already documented for
    # Bonsai-8B) that a model can narrate a complete, well-formatted draft as chat text and never
    # touch the tool, every single attempt, burning the FULL completion-check retry budget on
    # "still missing" before giving up. The project already had `_salvage_narrated_report` for
    # exactly this text pattern, but it only ran as a LAST-RESORT at final-verdict time, and only
    # for `missing_artifact` (final_report.md) — `missing_findings` (FindingsWriter/findings.md)
    # had no equivalent path at all, which is exactly the case that burned qwen2.5:3b-instruct's
    # entire budget (254.6s, 8/8 attempts, `findings.md` never written). Checking and salvaging
    # HERE, immediately after the Write dispatch, instead of waiting for the caller's final-verdict
    # fallback, means a narrating model gets a real (clearly flagged, unverified) draft on attempt
    # 1 — which then goes through the SAME PeerReviewer/Fix cycle and grounding checks a genuine
    # write would, rather than looping blind on a file that will never appear on its own. Shared by
    # both writer roles (Builder AND FindingsWriter) since this helper already is.
    from tools.fs import _get_safe_path
    path = _get_safe_path(req_artifact)
    if not (path and os.path.exists(path)):
        write_text = write_result if isinstance(write_result, str) else str(write_result)
        if _salvage_narrated_report(req_artifact, write_text):
            notify(
                f"**System ({attempt + 1}):** {writer_role} narrated `{req_artifact}` as chat text "
                f"instead of calling `write_workspace_file` — auto-recovered its own content as the "
                f"artifact (flagged unverified) instead of retrying blind."
            )
        elif get_workspace_file_content(req_artifact) is None:
            # Confirmed live 2026-07-24 (gpt-oss, 3 separate occurrences in one run): a writer
            # dispatch can return a genuinely EMPTY response -- zero tool calls, zero narrated
            # text, nothing for _salvage_narrated_report to work with either (it also declines
            # short text under 200 chars, e.g. a narrated one-line status update).
            #
            # One immediate retry before giving up: an empty response is plausibly a transient
            # flake, not a persistent one -- confirmed live all 3 empty responses that motivated
            # this fix were isolated, never two in a row for the same completion-check attempt. A
            # fresh dispatch (same instructions, same fresh context) has a real chance of
            # succeeding outright, which is strictly better than immediately giving up: the
            # alternative (raising right away) still costs a full completion-check attempt AND a
            # round-trip through the Planner (which has no write_workspace_file tool and can only
            # re-arrive at "dispatch FindingsWriter again" next cycle anyway) to reach the same
            # place this retry can reach directly, one dispatch later.
            notify(
                f"**System ({attempt + 1}):** {writer_role} returned nothing usable for "
                f"`{req_artifact}` — retrying once immediately before giving up."
            )
            retry_gate_token = writer_gate_ctx.set(
                {"write_done": False, "recommended_tool": recommended_tool, "target_file": req_artifact}
            ) if writer_role == "FindingsWriter" else None
            try:
                write_result = await dispatch_task(
                    f"{writer_role}Fix_attempt{attempt + 1}_retry", write_instructions, writer_role)
            finally:
                if retry_gate_token is not None:
                    writer_gate_ctx.reset(retry_gate_token)
            # Re-check against the SAME think_before baseline (not re-snapshotted) so this
            # reflects "was think_tool used in EITHER the original or the retry attempt" -- the
            # question that actually matters once a retry has happened, since think_tool_skipped
            # feeds later wording (the is_clean notify note, the Fix pass's think_tool_note).
            think_after = pool.get("think_tool", {}).get("used") if pool else None
            think_tool_skipped = think_before is not None and think_after == think_before
            if get_workspace_file_content(req_artifact) is None:
                retry_text = write_result if isinstance(write_result, str) else str(write_result)
                if _salvage_narrated_report(req_artifact, retry_text):
                    notify(
                        f"**System ({attempt + 1}):** {writer_role} narrated `{req_artifact}` as "
                        f"chat text instead of calling `write_workspace_file` — auto-recovered its "
                        f"own content as the artifact (flagged unverified) instead of retrying blind."
                    )
                elif deterministic_fallback and _salvage_narrated_report(
                        req_artifact, deterministic_fallback, banner=_DETERMINISTIC_SALVAGE_BANNER):
                    # Confirmed live 2026-07-26: the "isolated, never two in a row" assumption the
                    # immediate-retry fix above was built on does not always hold -- see this
                    # function's own docstring for the run that motivated this branch. Unlike the
                    # narrated-text salvage above, this content never came from the model at all --
                    # it's assembled deterministically from run_state's own real findings, so it's
                    # available even when the model produces nothing whatsoever, twice in a row.
                    notify(
                        f"**System ({attempt + 1}):** {writer_role} produced nothing usable twice "
                        f"in a row (including one immediate retry) — auto-recovered `{req_artifact}` "
                        f"directly from this run's real research data instead of losing the cycle "
                        f"entirely."
                    )
                else:
                    # Still nothing after a genuine retry -- this used to fall through to
                    # dispatching PeerReviewer anyway, against an artifact that flatly doesn't
                    # exist. PeerReviewer then has no filename to review, and confirmed live it
                    # degrades into guessing wrong paths (burned its entire read_workspace_file
                    # quota on nonexistent filenames before giving up, in the exact run that
                    # surfaced this). Raising here treats "the write produced nothing usable
                    # twice" as the dispatch failure it actually is, per this function's own
                    # documented contract -- the caller's normal retry loop gets a fresh attempt
                    # next time instead of an entire PeerReviewer dispatch being wasted on a file
                    # that was never written. Uses get_workspace_file_content (backend-agnostic:
                    # disk or in-memory), NOT the os.path.exists check above (disk-only, always
                    # false for the in-memory workspace backend regardless of real content --
                    # fine for the salvage decision above, an existing harmless quirk, but would
                    # falsely raise here on every in-memory write that already succeeded).
                    raise RuntimeError(
                        f"{writer_role} dispatch produced no '{req_artifact}' twice in a row "
                        f"(including one immediate retry) and nothing narrated to salvage either time"
                    )

    # Snapshot read_workspace_file's usage count BEFORE dispatching PeerReviewer, so a fabricated
    # "REVIEW: CLEAN" that never actually opened the file can be caught below (see is_clean gate).
    # None (not 0) when the quota isn't tracked at all -- distinguishes "can't verify" from "verified
    # zero reads," so a config with this quota disabled doesn't get falsely distrusted. Reuses the
    # `pool` object already fetched above for the think_tool snapshot (same object, same run).
    reads_before = pool.get("read_workspace_file", {}).get("used") if pool else None

    review = await dispatch_task(
        f"ReviewFix_attempt{attempt + 1}",
        f"Review '{req_artifact}' for accuracy and coherence. "
        f"Start your response with exactly 'REVIEW: CLEAN' or 'REVIEW: ISSUES FOUND:'.",
        "PeerReviewer",
    )

    # Conservative parse: anything other than an explicit CLEAN verdict (including a missing
    # sentinel — the model didn't follow format) is treated as issues found, so a formatting slip
    # never lets an unreviewed artifact slip through.
    review_text = review if isinstance(review, str) else str(review)
    is_clean = "REVIEW: CLEAN" in review_text and "REVIEW: ISSUES FOUND:" not in review_text

    # Confirmed live (Bonsai-8B bake-off, 2026-07-14): a model confident enough to fabricate the
    # sentinel currently defeats the review entirely -- it answered "REVIEW: CLEAN...well-structured
    # report..." for a findings.md it never opened and that never existed on disk. A real review
    # MUST have called read_workspace_file at least once; if the quota shows zero new reads despite
    # a CLEAN verdict, treat it exactly like an ISSUES FOUND verdict instead of trusting it.
    if is_clean and reads_before is not None:
        reads_after = pool.get("read_workspace_file", {}).get("used")
        if reads_after == reads_before:
            is_clean = False
            review_text = (
                "REVIEW: ISSUES FOUND: PeerReviewer claimed 'REVIEW: CLEAN' without ever calling "
                f"read_workspace_file on '{req_artifact}' -- a review with no evidence it actually "
                "read the file is not trustworthy. Re-read the file for real this time before "
                "judging it."
            )

    if is_clean:
        # think_tool_skipped is NOT a hard gate here, deliberately (2026-07-22): a fabricated CLEAN
        # review is a lie about a verification step that DID claim to happen (hence the hard
        # reads_before/reads_after gate above); a writer skipping think_tool makes no claim either
        # way, so the harm model differs -- gating on it risks burning retry budget on an otherwise
        # -fine draft. Surfaced for run-telemetry visibility only when the draft is otherwise clean.
        if think_tool_skipped:
            notify(f"**System ({attempt + 1}):** PeerReviewer found no issues with the rebuilt `{req_artifact}` (note: {writer_role} skipped its own required think_tool reasoning step before writing).")
        else:
            notify(f"**System ({attempt + 1}):** PeerReviewer found no issues with the rebuilt `{req_artifact}`.")
        return

    notify(f"**System ({attempt + 1}):** PeerReviewer flagged issues in the rebuilt `{req_artifact}` — dispatching one corrective {writer_role} pass.")
    # Fresh-context dispatch: this Fix pass shares NO conversation history with the Write pass
    # above, so `review_text` alone leaves it with no evidence base at all. Confirmed live
    # 2026-07-14: a FindingsWriter Fix pass told to use "the real source material you were given"
    # (worded for a Write-pass model that actually has it in-context) instead burned its whole
    # dispatch hunting read_workspace_file for guessed, nonexistent filenames
    # (task_results.json, research_results.json, instructions.md) — findings.md's source material
    # is a string assembled by _build_findings_source_material, never a workspace file, so there
    # was nothing for it to find. Builder's source (findings.md itself) IS a real file it could
    # have re-read, but re-including write_instructions here is correct for both roles and keeps
    # this function writer-role-agnostic.
    think_tool_note = (
        " Your last draft also skipped its own required think_tool reasoning step before "
        "writing — use think_tool this time to actually check each claim before finalizing."
        if think_tool_skipped else ""
    )
    fix_instructions = (
        f"PeerReviewer critiqued your last draft of '{req_artifact}'. Fix every issue it raised, "
        f"using only the real source material below (never your own prior knowledge), "
        f"then rewrite the file:\n\n{review_text}\n\n"
        f"--- YOUR ORIGINAL TASK INSTRUCTIONS AND SOURCE MATERIAL (unchanged) ---\n{write_instructions}"
        f"{think_tool_note}"
    )
    gate_token = writer_gate_ctx.set(
        {"write_done": False, "recommended_tool": recommended_tool, "target_file": req_artifact}
    ) if writer_role == "FindingsWriter" else None
    try:
        await dispatch_task(f"{writer_role}Fix_attempt{attempt + 1}_reviewed", fix_instructions, writer_role)
    finally:
        if gate_token is not None:
            writer_gate_ctx.reset(gate_token)


# Capped total, matching this project's "never unbounded" convention already used by
# _dispatch_writer_review_fix's own 4-dispatch cap and DISTINCT_PROBLEM_BONUS_CAP -- any dropped
# facets beyond this are left for check_report_underuses_evidence to catch again on a later
# completion-check attempt, since _facet_coverage recomputes `dropped` fresh every iteration.
_MAX_FACET_DISPATCHES = 4


async def _dispatch_per_facet_builder_fix(dispatch_task, dropped: list, by_task: dict,
                                           req_artifact: str, attempt: int, notify) -> None:
    """One Builder Write->Review->Fix cycle PER dropped facet (see _facet_coverage), run
    SEQUENTIALLY -- never via asyncio.gather like _dispatch_deepening_round: concurrent
    edit_workspace_file calls against the same req_artifact would race.

    The single combined-instruction version (commit 67e4b00, routed through the classic
    inject-into-Planner path since report_underuses_evidence was never Builder-fixable) got a
    clean negative live result (commit 1092add): asking Builder to fix every neglected facet in
    one turn reproduced the exact crowding pattern this check exists to catch in the first draft
    -- a report went from ~1/3 coverage of the harder facet to 0%. Isolating each facet into its
    own fresh-context dispatch, scoped to ONLY that facet's real URLs, removes the opportunity to
    crowd it out again. One facet's dispatch failure doesn't abort the rest -- each is independent.

    Deliberately reuses _dispatch_writer_review_fix unchanged (same Write->Review->Fix contract,
    same narration-salvage/empty-retry handling) rather than a bespoke loop.

    Naming collision, checked and deliberately left alone: _dispatch_writer_review_fix names its
    dispatch_task calls using only `attempt` (f"{writer_role}Fix_attempt{attempt + 1}", plus
    _retry/_reviewed suffixes), so looping it here reuses the same name across facets within one
    attempt. Do NOT add a per-facet suffix to disambiguate -- finetune/extract_dataset.py's
    _WRITER_DISPATCH_RE (^SubAgent_(Builder|FindingsWriter)Fix_attempt\\d+(_reviewed)?$) is
    strictly anchored with no facet slot; any new suffix shape would silently break
    _infer_role's classification of these dispatches for GRPO training-data extraction. The
    per-facet notify() messages below already name the facet in prose for human-readable log
    distinguishability -- that's sufficient."""
    for name in dropped[:_MAX_FACET_DISPATCHES]:
        urls = sorted(by_task.get(name, []))
        url_list = "\n".join(f"- {u}" for u in urls)
        instructions = (
            f"'{req_artifact}' has NO citations for task '{name}', even though findings.md has "
            f"real, surviving sources for it. Use edit_workspace_file to insert ONE new section "
            f"covering ONLY task '{name}', citing ONLY these real URLs (copied verbatim, do not "
            f"invent or paraphrase them):\n{url_list}\n\nDo not rewrite or touch any other part of "
            f"the report.{_BUILDER_NO_DELEGATE_CLARIFICATION}Write the corrected file now via "
            f"edit_workspace_file."
        )
        try:
            await _dispatch_writer_review_fix(dispatch_task, "Builder", req_artifact, instructions, attempt, notify)
        except Exception:
            notify(f"**System ({attempt + 1}):** Builder dispatch for facet '{name}' failed — continuing with remaining facets.")


async def _dispatch_per_facet_findings_writer_fix(dispatch_task, dropped: list, run_state, attempt: int, notify) -> None:
    """One FindingsWriter Write->Review->Fix cycle PER dropped facet (see _findings_facet_coverage),
    run SEQUENTIALLY -- the exact same shape as _dispatch_per_facet_builder_fix, one layer
    upstream. The single combined-instruction version (routed through _FINDINGS_WRITER_FIXABLE_
    PROBLEMS until 2026-08-01) got the identical live-confirmed negative result the Builder-level
    version already had (commit 1092add): FindingsWriter given a complete, well-under-budget
    (16.5K chars under a 50K settings.context_budget_chars — truncation ruled out directly, not
    assumed) 4-facet evidence blob in ONE dispatch wrote real content for only 1 of 4 facets. See
    RESEARCH.md §16 and _FINDINGS_WRITER_FIXABLE_PROBLEMS' own comment for the full incident.

    Differs from _dispatch_per_facet_builder_fix in WHAT gets scoped: Builder already has
    findings.md available to read (writer_gate_ctx doesn't apply to it), so its per-facet fix
    scopes which URLs to cite. FindingsWriter's evidence base IS its dispatch instructions (its
    first message, per ARCHITECTURE.md §2) — there is no "read a file to find the real content"
    step for it to narrow, so this scopes the EVIDENCE BLOB itself instead, via
    _build_findings_source_material's new task_names filter, giving each dispatch only that one
    facet's own findings (and only that facet's own fetched-URL cross-reference section) rather
    than the full multi-facet blob that caused the crowding in the first place.

    Deliberately does NOT pass deterministic_fallback (unlike the generic missing_findings/
    findings_ungrounded/stale_findings FindingsWriter call site) even though scoped_material would
    be a valid candidate for it. That fallback's own guard only fires when findings.md doesn't
    exist AT ALL yet (get_workspace_file_content(req_artifact) is None) — true for a from-scratch
    write, but this branch only ever runs when findings.md already exists (check_findings_
    underuses_evidence requires it), so the guard should never actually let it trigger here. Not
    passing it removes even the theoretical risk entirely rather than relying on that guard
    holding: if it ever fired anyway, the fallback IS the write content, and a single facet's
    scoped_material becoming the whole file would destroy every OTHER facet's already-correct
    entries — the exact evidence-loss failure this whole fix exists to prevent. Matches
    _dispatch_per_facet_builder_fix's own precedent, which never passes it either."""
    for name in dropped[:_MAX_FACET_DISPATCHES]:
        scoped_material = _build_findings_source_material(run_state, task_names={name})
        write_directive = (
            f"findings.md is MISSING task '{name}' entirely, even though real research results "
            f"exist for it below. Use edit_workspace_file to insert one or more new entries "
            f"covering ONLY task '{name}', from the real research results below. Do not rewrite "
            f"or touch any other part of the file."
        )
        instructions = f"{write_directive}\n\n{scoped_material}\n\nWrite the corrected file now via edit_workspace_file."
        try:
            await _dispatch_writer_review_fix(dispatch_task, "FindingsWriter", "findings.md", instructions, attempt, notify,
                                               recommended_tool="edit_workspace_file")
        except Exception:
            notify(f"**System ({attempt + 1}):** FindingsWriter dispatch for facet '{name}' failed — continuing with remaining facets.")


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
    bolted-on exclusion."""
    src = f.get("source_url") or ""
    summary = f.get("summary") or ""
    if not (src.startswith("http") and not _CUTOFF_ONLY_SUMMARY_RE.match(summary)):
        return False
    if _is_null_finding_summary(summary):
        return False
    return "[SYSTEM RELEVANCE WARNING" not in summary and "[SYSTEM VERIFICATION WARNING" not in summary


_WARNING_MARKER_RE = re.compile(r"\[SYSTEM (?:VERIFICATION|RELEVANCE) WARNING:.{0,160}")


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


def _dedupe_findings(findings: list) -> list:
    """Exact (source_url, summary) dedup, shared by _build_findings_source_material and
    check_propagated_ungrounded_content (2026-07-22) -- extracted so both stay in sync on the one
    definition of "duplicate" instead of drifting."""
    seen = set()
    deduped = []
    for f in findings:
        key = (f.get("source_url"), f.get("summary"))
        if key in seen:
            continue
        seen.add(key)
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
        findings = [f for f in findings if f.get("task_name") in task_names]
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
        block = f"{first_heading}\n{group['summary']}"
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


def _select_deepening_tasks(run_state: "RunState") -> list:  # noqa: F821 — utils.run_state.RunState, annotation only
    """Pick the real follow-up directions a deepening round should chase (ROADMAP item 10,
    engine-driven iterative deepening). Only from COVERED top-level (depth==1) findings — a real
    lead surfaced by a source that actually returned something, not invented for an uncovered
    task, which has no summary to draw a direction from at all (that gap is check_thin_coverage's
    own job, unchanged). Deduplicated against run_state.data["consumed_directions"] so a retry
    attempt never redispatches the same direction twice. Geometric narrowing: at most
    ceil(coverage total / 2) tasks (dzhng/deep-research's newBreadth = ceil(breadth/2)), capped by
    however many real, unconsumed directions actually exist — never invented to hit a target
    count. Returns delegate_tasks-shaped dicts, ready for dispatch_task."""
    coverage = run_state.coverage()
    if coverage["total"] == 0:
        return []
    consumed = set(run_state.data.get("consumed_directions", []))
    candidates = []  # (direction_text, agent_id) in finding order
    seen_directions = set()
    for f in run_state.data.get("findings", []):
        if f.get("depth") != 1:
            continue
        if not (f.get("source_url") or "").startswith("http"):
            continue  # uncovered -- no real source, nothing to deepen from
        for direction in f.get("follow_up_directions") or []:
            if direction in consumed or direction in seen_directions:
                continue
            seen_directions.add(direction)
            candidates.append((direction, f.get("agent_id") or "WebSearcher"))

    max_breadth = math.ceil(coverage["total"] / 2)
    selected = candidates[:max_breadth]
    return [
        {
            "task_name": f"Follow-up: {direction[:80]}",
            "instructions": direction,
            "agent_id": agent_id,
        }
        for direction, agent_id in selected
    ]


async def _dispatch_deepening_round(dispatch_task, run_state: "RunState", notify) -> bool:  # noqa: F821
    """Engine-driven iterative deepening (ROADMAP item 10): dispatch real follow-up directions
    directly via dispatch_task (== engine/orchestrator.py's _run_single_task), the SAME
    bypass-the-Planner mechanism _dispatch_writer_review_fix already uses for Builder/FindingsWriter
    retries — no new dispatch primitive needed. Returns False (dispatches nothing) when there are
    no real directions to act on, so the caller can fall back to the classic thin_coverage Planner
    nudge unchanged; this is additive, never a replacement for that check."""
    tasks = _select_deepening_tasks(run_state)
    if not tasks:
        return False

    directions = [t["instructions"] for t in tasks]
    names = ", ".join(f"'{t['task_name']}'" for t in tasks)
    notify(
        f"**System:** Engine dispatching a deepening round ({len(tasks)} follow-up task(s) from "
        f"real leads found in prior research): {names}"
    )

    results = await asyncio.gather(
        *(dispatch_task(t["task_name"], t["instructions"], t["agent_id"]) for t in tasks),
        return_exceptions=True,
    )
    for direction, result in zip(directions, results):
        if isinstance(result, Exception):
            # A single dispatch failing (timeout, malformed response) doesn't invalidate the
            # round -- mark it consumed anyway so it isn't retried into the same failure forever;
            # any OTHER real directions still on record remain available for a later round.
            notify(f"**System:** Deepening task for {direction[:80]!r} failed: {result}")
        run_state.data.setdefault("consumed_directions", []).append(direction)

    run_state.data["deepening_round"] = run_state.data.get("deepening_round", 0) + 1
    return True


def _consecutive_occurrences(run_state: "RunState", problem: str,  # noqa: F821
                              skip_problems: frozenset = frozenset()) -> int:
    """How many of the run's most recent completion-check attempts, counting backward from the
    end, recorded this exact problem consecutively. Shared by run_completion_check's own
    escalation counter (force_whole_rebuild), _yield_to_starved_check/_apply_starvation_yield
    below, and _capped's own cap logic, so all four stay in sync on one definition of "stuck on
    the same problem" instead of drifting.

    skip_problems (2026-07-31, generalized after the 2nd live incident of the same shape):
    some interrupting problems are themselves a DIRECT symptom of the model failing to comply
    with the CURRENT problem's own directive (e.g. untracked_delegation firing because the model
    renamed a flagged task instead of retrying it under the same name) -- counting that as a
    genuinely different problem breaks the streak and can trap a check in its weakest ("redo")
    wording forever instead of ever escalating. Each caller declares its own skip set; default
    empty preserves the original strict-consecutive behavior for callers that don't need it."""
    count = 0
    for a in reversed(run_state.data.get("completion_check_attempts", [])):
        p = a.get("problem")
        if p == problem:
            count += 1
        elif p in skip_problems:
            continue
        else:
            break
    return count


def _consecutive_tier_wins(run_state: "RunState", tier_problems: frozenset) -> int:  # noqa: F821
    """Sibling to `_consecutive_occurrences` above, but membership- not equality-based: how many of
    the run's most recent completion-check attempts, counting backward, recorded ANY problem from
    `tier_problems` (e.g. `_COMPLETION_TIER_PROBLEMS`) consecutively -- "has this whole TIER kept
    winning" rather than "has this exact problem kept winning".

    Exists because `_yield_to_starved_check`'s original starvation guard (2026-08-01) only
    protected against the SAME problem recurring: `_consecutive_occurrences(ctx.run_state,
    verdict.problem)` resets to 0 the instant a DIFFERENT COMPLETION_CHECKS problem wins next,
    even though GROUNDING_CHECKS is still just as starved either way (the hard two-tier gate in
    run_completion_check only ever evaluates GROUNDING_CHECKS when COMPLETION_CHECKS returns None
    for the WHOLE scan, regardless of which specific check is responsible). Live-confirmed
    2026-08-16: a run's winning problem changed every single attempt (missing_findings ->
    missing_artifact -> uneven_task_investment -> task_verification_flagged), so the same-problem
    counter never once reached _STARVATION_SKIP_THRESHOLD and GROUNDING_CHECKS (specifically
    check_report_underuses_evidence/check_report_underuses_findings, built to catch exactly this --
    a Builder draft that dropped 3 of 4 requested facets) never got a single turn in the whole run.
    The run's remaining budget was spent entirely re-researching one flagged sub-task while the
    already-catastrophically-incomplete report sat on disk unexamined."""
    count = 0
    for a in reversed(run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") in tier_problems:
            count += 1
        else:
            break
    return count


_STARVATION_SKIP_THRESHOLD = 2

# How many consecutive occurrences of the SAME problem earn a check's strongest escalation
# (force_whole_rebuild below) or, for a non-self-resolving check (see _capped's own docstring),
# the point past which it goes quiet instead of permanently starving whatever's next in
# COMPLETION_CHECKS/GROUNDING_CHECKS. ONE shared constant so a future check's own cap can never
# silently disagree with force_whole_rebuild's threshold the way it did once already this session.
CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD = 3


def _capped(ctx: "Ctx", problem: str, verdict: Optional["Verdict"],  # noqa: F821
            skip_problems: frozenset = frozenset()) -> Optional["Verdict"]:  # noqa: F821
    """For a check that is NOT Builder/FindingsWriter-fixable (cannot dispatch its own real fix
    via the Write->Review->Fix loop -- absent from both _BUILDER_FIXABLE_PROBLEMS and
    _FINDINGS_WRITER_FIXABLE_PROBLEMS): once it has fired CONSECUTIVE_SAME_PROBLEM_ESCALATION_
    THRESHOLD times in a row, go quiet instead of returning verdict again, so COMPLETION_CHECKS/
    GROUNDING_CHECKS' own first-match ordering can fall through to whatever check is next in the
    list. Without this, such a check wins first-match on EVERY attempt for as long as its
    underlying condition stays true, permanently starving every check below it -- confirmed live
    twice the same night (check_task_verification_flagged starving check_missing_findings/
    check_missing_artifact; check_thin_coverage, one priority slot higher, doing the same thing).
    See ARCHITECTURE.md's completion-check section for the full incident writeup and the standing
    test (test_structural_checks.py) that enforces every non-self-resolving check in either list
    calls this -- a future check that skips it fails the suite immediately instead of being found
    via a live incident."""
    if verdict is None:
        return None
    if _consecutive_occurrences(ctx.run_state, problem, skip_problems) >= CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD:
        return None
    return verdict


def _yield_to_starved_check(verdict: Optional[Verdict], ctx: Ctx, starved_check,
                             never_final_blocker: bool = False,
                             tier_problems: Optional[frozenset] = None) -> Optional[Verdict]:
    """First-verdict-wins normally, but a low-priority hygiene check placed deliberately last in
    its own list (check_untracked_delegation in COMPLETION_CHECKS — a "wait a cycle" check,
    explicitly documented as lower-priority-than-correctness, never meant to compete with a real
    problem, and protected regardless of WHICH problem is currently winning) can end up NEVER
    getting a turn at all on a long run where some OTHER problem keeps recurring every single
    attempt. Confirmed live 2026-07-24: an 8-attempt run cycled through stale_findings/
    uneven_task_investment/missing_artifact repeatedly and check_untracked_delegation never fired
    once — "wait a cycle" became "wait forever" in practice, not by design. (This function's OTHER
    former use, report_underuses_findings yielding to its own more specific sibling
    report_underuses_evidence, has been migrated to the declarative _STARVATION_YIELD_TARGETS +
    _apply_starvation_yield above — a genuinely different shape, one specific problem yielding to
    one specific sibling rather than a check protected regardless of what's winning; see that
    function's own docstring for why the hand-written-lambda version of this pairing was buggy.)

    Once the CURRENT winning problem has already fired _STARVATION_SKIP_THRESHOLD times in a row
    with no progress, give the starved check one direct extra shot instead of blindly re-selecting
    the same stuck problem again. Safe to call speculatively: both starved checks are pure reads of
    already-available state (confirmed — neither mutates run_state.data, unlike e.g. check_no_urls's
    own escalation counter), so invoking one here has no side effect to worry about. Falls back to
    the original verdict if the starved check has nothing to report, so a genuinely single-problem
    run is never worse off than before this existed.

    never_final_blocker (2026-07-28, live bug): check_untracked_delegation's own docstring is
    explicit that it "will NOT block this run from finishing" — but tui.py's context-budget/
    max_run_minutes/malformed-tool-call paths force run_state.attempt to 10**6 (jumping straight
    to run_completion_check's final-verdict branch) without any awareness of which problem is
    currently winning. Confirmed live: a run whose real, still-retriable problem was
    task_verification_flagged (persistent citation fabrication, only 2 of 8 attempts used) got its
    context budget blown by an unrelated Ollama think-mode-passthrough bug inflating every turn's
    token count; the forced-final cycle landed exactly on this function's starvation window and
    yielded to check_untracked_delegation, which then got reported as the run's terminal
    "unresolved issue" — precisely the outcome its own docstring promises never happens. Once
    ctx.attempt has already reached ctx.max_attempts (i.e. this cycle is going straight to the
    final branch no matter what), a check documented as never-blocking must not be allowed to
    become the reported blocker — keep the real verdict instead. This is the only remaining caller
    of this function (never_final_blocker defaults False for any future caller that carries a
    genuine correctness guarantee and doesn't need this protection).

    tier_problems (2026-08-16, live incident, see `_consecutive_tier_wins`'s own docstring for the
    full trace): the same-problem-only check above misses a run where the winning problem CHANGES
    every attempt but always comes from the same starving tier -- GROUNDING_CHECKS still never
    gets evaluated in that case, just for a subtler reason. When provided, the starvation window
    opens on EITHER the same problem recurring OR the whole tier winning consecutively, whichever
    threshold is reached first; None (the default) preserves the original same-problem-only
    behavior for callers (like check_untracked_delegation's) that don't need the broader check."""
    if verdict is None:
        return None
    if never_final_blocker and ctx.attempt >= ctx.max_attempts:
        return verdict
    same_problem_stuck = _consecutive_occurrences(ctx.run_state, verdict.problem) >= _STARVATION_SKIP_THRESHOLD
    tier_stuck = (
        tier_problems is not None
        and _consecutive_tier_wins(ctx.run_state, tier_problems) >= _STARVATION_SKIP_THRESHOLD
    )
    if not (same_problem_stuck or tier_stuck):
        return verdict
    alt = starved_check(ctx)
    if alt is not None and alt.problem != verdict.problem:
        return alt
    return verdict


# Declarative sibling-yield targets: problem name -> the more specific check that should get one
# direct probe once the winning check above has recurred _STARVATION_SKIP_THRESHOLD times in a
# row. Different shape from _yield_to_starved_check above (which protects a check regardless of
# WHICH problem is currently winning) -- this is for a SPECIFIC problem that should yield to a
# SPECIFIC sibling, e.g. report_underuses_findings' own flat citation-ratio check yielding to
# report_underuses_evidence's more specific per-task one. A dict entry, not a hand-written lambda
# -- see _apply_starvation_yield's own docstring for why that distinction matters.
_STARVATION_YIELD_TARGETS: dict = {
    "report_underuses_findings": check_report_underuses_evidence,
}


def _apply_starvation_yield(verdict: Optional[Verdict], ctx: Ctx) -> Optional[Verdict]:
    """Generic replacement for a hand-written `lambda c: A(c) or B(c)` (2026-07-31 live incident):
    that exact pattern was live-confirmed dead code, because `A` -- the SAME check already winning
    the main scan above -- gets evaluated first via `or`'s short-circuit, so `B` (the actually-
    starved, more specific sibling) never runs at all as long as A's own condition is still true,
    which it almost always still is (nothing changed between the main scan and this probe).
    Confirmed live: a run whose report dropped 4 whole tasks' worth of evidence (including the
    query's own Colombia angle) fired report_underuses_findings for 4+ consecutive attempts,
    crossing _STARVATION_SKIP_THRESHOLD multiple times, and never once yielded to
    report_underuses_evidence -- replaying report_underuses_evidence directly against that same
    run's saved output found the dropped tasks on the first try.

    Structurally cannot repeat that bug: looks up the winning verdict's OWN declared target in
    _STARVATION_YIELD_TARGETS and probes ONLY that target directly, never wrapped in an `or` with
    the check that's already winning."""
    if verdict is None:
        return None
    target = _STARVATION_YIELD_TARGETS.get(verdict.problem)
    if target is None:
        return verdict
    if _consecutive_occurrences(ctx.run_state, verdict.problem) < _STARVATION_SKIP_THRESHOLD:
        return verdict
    alt = target(ctx)
    if alt is not None and alt.problem != verdict.problem:
        return alt
    return verdict


_OTHER_ACTIVE_PROBLEMS_CAP = 3


def _collect_other_active_problems(ctx: Ctx, checks: list, exclude_problem: str) -> list[Verdict]:
    """`_yield_to_starved_check` above only protects two specifically-named low-priority checks
    from being permanently shadowed by "first verdict wins" -- confirmed live 2026-07-29 that the
    same shadowing shape recurs anywhere it ISN'T hand-wired: a real, MID-priority accuracy check
    (check_uncited_claims) sat independently, simultaneously true for 3 completion-check attempts
    behind a persistently-recurring check_stub_source, never got a turn, and was never disclosed
    even in the terminal "retry budget exhausted" message. Direct evidence: re-running
    find_uncited_claim_lines against that run's actual saved final_report.md returned 6 hits
    (above the 3-line firing threshold) on the very attempt that ended the run reporting only
    stub_source as "the" unresolved issue.

    Rather than hand-wiring a growing list of specific check pairs (how the two existing
    _yield_to_starved_check call sites came to exist -- one incident at a time), this runs the
    REST of whichever list just produced the winning verdict and surfaces EVERYTHING else
    currently true. Per arXiv:2607.01855 ("Regression Accumulation in Multi-Turn LLM Programming
    Conversations"): the dominant regression cause in iterative LLM correction (55.7%) is a later
    fix breaking an earlier, already-satisfied requirement through incompatibility, not simple
    forgetting -- confirmed here too (attempt 4 fixed 3 uncited-claim lines; attempts 5-6 then
    rewrote the document to fix stub_source and silently reintroduced new uncited-claim-shaped
    lines). Their validated mitigation is full re-verification every turn with every failing
    constraint made visible, not a persisted "history of what broke" or a rollback mechanism --
    this function is exactly that: cheap (pure functions of the already-built ctx, no new LLM or
    tool calls -- the one expensive fact, ctx.grounding_problem, is already computed once per
    attempt regardless of this) and stateless.

    Deliberately does NOT change which Verdict is "the" recorded problem (run_state.record_attempt,
    the escalation/bonus counters, the verdict-matrix tests all stay untouched) -- only the TEXT
    shown to the model/user gains an addendum, preserving the one-primary-directive-per-turn design
    this module was built around (completion.py's own top-of-file comment: a single clear
    instruction per turn is what replaced the bug-prone if/elif chain, not a wall of competing
    demands)."""
    out = []
    for check in checks:
        v = check(ctx)
        if v is not None and v.problem != exclude_problem:
            out.append(v)
            if len(out) >= _OTHER_ACTIVE_PROBLEMS_CAP:
                break
    return out


def _with_other_problems_addendum(verdict: Verdict, ctx: Ctx, checks: list) -> Verdict:
    """Wraps a winning verdict's `inject` text with a short, explicitly-secondary addendum naming
    any OTHER currently-active problem from the same check list -- see
    _collect_other_active_problems' docstring for the incident and literature this closes. A no-op
    (returns verdict unchanged) when nothing else is active, so a genuinely single-problem run's
    injected text is byte-identical to before this existed.

    ONLY valid for COMPLETION_CHECKS: its checks are genuinely independent of each other (each
    reads its own distinct fact off ctx/run_state). Do NOT call this with GROUNDING_CHECKS -- most
    of those checks key off the SINGLE shared ctx.grounding_problem string, which real_grounding_
    problem computes as only its own first hit; re-running a sibling grounding check against that
    same ctx can never reveal a second, different grounding problem (confirmed live 2026-07-29 --
    see _other_grounding_problems_addendum below, the correct-layer version for that list)."""
    others = _collect_other_active_problems(ctx, checks, verdict.problem)
    if not others:
        return verdict
    addendum = (
        " ALSO currently true (lower priority than the above -- do not undo it while fixing the "
        "above): " + "; ".join(f"{o.problem}: {o.warning}" for o in others)
    )
    return verdict._replace(inject=verdict.inject + addendum)


def _other_grounding_problems(ctx: Ctx, exclude_problem: str) -> list[str]:
    """The GROUNDING_CHECKS-list equivalent of _collect_other_active_problems, but at the correct
    layer: most GROUNDING_CHECKS functions key off the single ctx.grounding_problem string, which
    real_grounding_problem computes as only its OWN first hit (utils/grounding.py's ordered
    if-chain) -- re-calling a sibling check_* function against that same ctx can never reveal a
    second, simultaneously-true grounding problem, since the underlying fact was never computed.
    Confirmed live 2026-07-29: a real run's final_report.md had both a stub_source citation and 6
    uncited-claims lines (verified directly against the saved output); check_uncited_claims could
    never fire because ctx.grounding_problem stayed "stub_source:..." for the rest of the run.

    Calls utils.grounding.cheap_grounding_problems directly instead -- see its own docstring for
    exactly which sub-checks it covers (the pure string/regex ones) and which it deliberately
    excludes (NLI/reranker model inference, to avoid multiplying that cost every attempt)."""
    gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})
    raw = cheap_grounding_problems(ctx.content or "", gc_cfg, get_fetched_urls())
    return [p for p in raw if p.split(":", 1)[0] != exclude_problem][:_OTHER_ACTIVE_PROBLEMS_CAP]


def _with_other_grounding_addendum(verdict: Verdict, ctx: Ctx) -> Verdict:
    """_with_other_problems_addendum's GROUNDING_CHECKS counterpart, built on the correct-layer
    _other_grounding_problems above instead of re-walking GROUNDING_CHECKS itself."""
    others = _other_grounding_problems(ctx, verdict.problem)
    if not others:
        return verdict
    addendum = (
        " ALSO currently true in the same document (lower priority than the above -- do not undo "
        "it while fixing the above): " + "; ".join(others)
    )
    return verdict._replace(inject=verdict.inject + addendum)


async def run_completion_check(query: str, current_input, run_state: "RunState", notify, last_assistant_text: str = "", dispatch_task=None, budget_deadline: float | None = None, find_substantial_text: Optional[Callable[[], str]] = None):  # noqa: F821 — utils.run_state.RunState, annotation only
    """Runs the 3-tier completion check (delegated? artifact exists? really grounded?) plus the
    structural fixes: per-attempt quota top-up, artifact quarantine, run-state persistence, and
    (as a last resort) salvaging a narrated-but-never-written report instead of losing it.

    `dispatch_task`, when provided (see engine.orchestrator._run_single_task / create_local_agent's
    3-tuple return), enables the Write->Review->Fix loop for BOTH `_BUILDER_FIXABLE_PROBLEMS` and
    `_FINDINGS_WRITER_FIXABLE_PROBLEMS`: instead of injecting a nudge into the Planner's own
    `current_input` (which never shrinks across a run), a fresh Builder or FindingsWriter
    sub-agent rewrites the relevant artifact and a fresh PeerReviewer checks the result, entirely
    outside the Planner's conversation. When `dispatch_task` is None (or the caller's registered
    sub-agents don't include the needed pair — "Builder"+"PeerReviewer" or
    "FindingsWriter"+"PeerReviewer"), that class of problem falls back to the classic
    inject-into-Planner behavior unconditionally.

    `budget_deadline` (time.monotonic()-based, optional): the SAME wall-clock ceiling
    settings.max_run_minutes gives the Planner's own stream (tui.py's budget_deadline) — passed
    down here because a Write->Review->Fix chain can loop through MANY attempts inside this one
    call without ever returning to the caller (see the docstring paragraph above), so a caller
    that only re-checks max_run_minutes BETWEEN calls to this function never gets a chance to
    catch a chain that blows the whole budget in a single call. Confirmed live 2026-07-23 (gpt-oss
    on vLLM, ~21.5 tok/s under --enforce-eager): a run sat mid-attempt past its configured
    max_run_minutes with no cutoff, because attempt 4's Write->Review->Fix chain simply hadn't
    returned yet when the outer between-calls check would have fired. Checked once per loop
    iteration below, same "attempt = max_attempts" short-circuit already used for the
    consecutive-same-problem escalation case, so it reuses the existing salvage/quarantine
    final-verdict path instead of a new bespoke cutoff. None (the default) preserves the TUI's
    existing unbounded behavior unchanged.

    `find_substantial_text` (optional callback, no args, returns str): scans the caller's own
    session-event history for the most recent substantial narrated text block, used only by the
    final-verdict narration-salvage path below. Injected by the caller (same pattern as `notify`)
    rather than imported directly — extracted 2026-07-29 to break a real circular import: this
    module used to lazy-import `_find_last_substantial_text` from `engine.tui` at call time
    (not at module load) specifically because engine.tui imports THIS module at load time, and a
    top-level cross-import here would have crashed on a partially-initialized module. Both
    tui.py callers
    (`run_agent`, `run_cli`) pass their own local `_find_last_substantial_text` function, which
    reads tui.py's own `_session_events` list — that data structure is tui-specific bookkeeping
    completion.py has no independent reason to know about, so callback injection is the right
    boundary here, not just an import-cycle workaround. None (the default) preserves the old
    behavior of falling back to `last_assistant_text` alone (a caller that doesn't pass this gets
    exactly what `_find_last_substantial_text() or last_assistant_text` would have done if the
    scan came up empty).

    Returns (should_retry: bool, new_current_input). Caller is responsible for looping while
    should_retry is True, same as before.

    A successful Write->Review->Fix dispatch (Builder or FindingsWriter) does NOT return control
    to the Planner — it `continue`s straight into the next completion-check iteration inside this
    same call, chaining through as many writer dispatches as the retry budget allows (e.g.
    FindingsWriter fixes findings.md -> immediately checks final_report.md -> dispatches Builder
    -> checks again -> clean -> returns). This is deliberate: the Planner has no memory of a fix
    cycle just running and would otherwise burn a real LLM turn re-deciding what to do, sometimes
    delegating more research for what was actually a downstream writer bug (confirmed live
    2026-07-14: a repeated Builder citation error cost 25 minutes/35 URLs of Planner-driven
    "more research" turns before the retry budget forced the existing salvage fallback to end it).
    Only the classic inject-into-Planner path, the final-verdict/salvage path, and the exception
    handler return control to the caller now. A persistently-failing chain now looks like one
    longer `run_completion_check` call instead of many short Planner round-trips — same
    `attempt < max_attempts` ceiling, no new infinite-loop risk.
    """
    req_artifact = config.get_required_artifact()
    if not req_artifact:
        return False, current_input

    # Configurable, not hardcoded — the fixed default of 3 was cutting runs off with real sources
    # sitting unused in findings.md, well before hardware was anywhere near a real constraint
    # (confirmed live: an 11-source run exhausted its budget at ~11% system memory usage while the
    # model still hadn't complied with two explicit "add real citation links" nudges in a row).
    # Raising this trades wall-clock time and tool-call quota for more chances to self-correct.
    max_attempts = config.cfg.get("settings", {}).get(
        "max_completion_check_attempts", DEFAULT_MAX_COMPLETION_CHECK_ATTEMPTS
    )
    # 2026-07-29: distinguish "still making genuine progress" (each retry fixes a DIFFERENT
    # problem) from "stuck on the same issue" (already handled below via the consecutive-
    # same-problem escalation, which forces a whole-rebuild then a hard stop). Confirmed live: the
    # Ornith-1.0-9B run did 6 honest write-review-fix rounds, each fixing a REAL, DIFFERENT problem
    # (a stub source, then an unrelated URL-case grounding false-positive -- see
    # utils/grounding.py's _url_is_grounded, fixed the same session) and was cut off by the flat
    # max_attempts ceiling despite never looping on any single issue -- a flat per-run ceiling
    # can't tell "6 different real fixes" from "6 repeats of one fix" apart on its own. Per
    # literature checked this session (arXiv:2606.04056's catalog of real agent budget-overrun
    # incidents confirms a hard ceiling is legitimate risk management; arXiv:2606.27009's semantic
    # early-stopping result argues the better lever distinguishes genuine progress from spinning,
    # rather than raising one flat number for every run alike): reuse the SAME per-attempt
    # `problem` field every check already records, no new tracking machinery beyond one bounded
    # counter. Capped total, not unbounded -- this rewards real sequential progress, it does not
    # disable the ceiling for a run that's actually stuck (that stays gated by the existing
    # CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD logic below, unchanged).
    DISTINCT_PROBLEM_BONUS_CAP = 4
    # Ornith's own cutoff was the WALL-CLOCK deadline (max_run_minutes), not the attempt-count
    # ceiling -- extending max_attempts alone wouldn't have helped that specific incident, since
    # budget_deadline is checked independently, below, regardless of how many attempts remain.
    # Each bonus attempt also earns a proportional slice of extra wall-clock time (this run's own
    # configured per-attempt pace), not a flat guess.
    _configured_run_minutes = config.cfg.get("settings", {}).get("max_run_minutes", 0) or 0
    _bonus_seconds_per_attempt = (
        (_configured_run_minutes * 60) / max_attempts if (_configured_run_minutes and max_attempts) else 0
    )

    try:
        while True:
            attempt = run_state.attempt
            recorded_attempts = run_state.data.get("completion_check_attempts", [])
            if (len(recorded_attempts) >= 2 and recorded_attempts[-1].get("problem")
                    and recorded_attempts[-1].get("problem") != recorded_attempts[-2].get("problem")):
                bonus_used = run_state.data.get("distinct_problem_bonus_used", 0)
                if bonus_used < DISTINCT_PROBLEM_BONUS_CAP:
                    run_state.data["distinct_problem_bonus_used"] = bonus_used + 1
                    max_attempts += 1
                    if budget_deadline is not None and _bonus_seconds_per_attempt:
                        budget_deadline += _bonus_seconds_per_attempt
            if budget_deadline is not None and attempt < max_attempts and time.monotonic() > budget_deadline:
                notify("**System (final):** max_run_minutes exceeded mid-retry-chain — stopping "
                       "further Write/Review/Fix dispatches and finishing with whatever exists.")
                attempt = max_attempts
            quotas = tool_quotas_ctx.get()
            files = get_workspace_files()
            _update_task_verification(run_state)
            ctx = Ctx(
                req_artifact=req_artifact,
                attempt=attempt,
                max_attempts=max_attempts,
                # 2026-07-28 resume fix (ARCHITECTURE.md §4's own documented, previously-accepted
                # gap): the live quota pool alone is always 0 at the start of a resumed process,
                # even when the interrupted run already delegated real research -- a resumed
                # Planner correctly told (via build_resume_input) not to re-delegate then gets
                # check_not_delegated's "your ONLY next tool call must be delegate_tasks" directive
                # anyway, a live-confirmed direct contradiction that derailed a resumed run into a
                # think_tool reflection loop. fetched_urls is carried over via the resume-carryover
                # allowlist (tui.py) and only ever gets populated by a real specialist dispatch, so
                # a non-empty list is proof delegation genuinely happened in ANY session (this one
                # or a resumed prior one) -- same "fetched_urls is ground truth" philosophy already
                # used for grounding checks on resume.
                delegated=bool(quotas and quotas.get("delegate_tasks", {}).get("used", 0) > 0)
                          or bool(run_state.data.get("fetched_urls")),
                files=files,
                content=get_workspace_file_content(req_artifact) if req_artifact in files else None,
                quotas=quotas,
                run_state=run_state,
            )

            # Detecting the problem (or lack of one) never consumes the retry budget —
            # only actually retrying does. Otherwise a success on the final allowed
            # attempt is never recognized as a success (it just falls through silently).
            verdict = next((v for check in COMPLETION_CHECKS if (v := check(ctx)) is not None), None)
            verdict = _yield_to_starved_check(verdict, ctx, check_untracked_delegation, never_final_blocker=True)
            # Cross-TIER starvation, not just within-list (2026-08-01, RESEARCH.md Sec.17f):
            # GROUNDING_CHECKS is only ever evaluated at all when COMPLETION_CHECKS returns None
            # for the whole scan below -- a hard two-tier gate, not just first-match priority
            # within one list. Live-confirmed: a resumed run's check_task_verification_flagged
            # (COMPLETION_CHECKS) recurred for 6 of 8 attempts on a genuinely real, still-unresolved
            # problem -- not a bug, _capped()/_yield_to_starved_check above both worked as designed
            # -- and report_underuses_evidence (GROUNDING_CHECKS, built specifically to catch a
            # Builder draft dropping a covered facet) never got evaluated ONCE in the entire run, no
            # matter how many attempts passed, because GROUNDING_CHECKS structurally never got a
            # turn while ANYTHING in COMPLETION_CHECKS kept returning non-None -- old, carried-over,
            # or brand new, it doesn't matter which. Reuses _yield_to_starved_check (same mechanism
            # already protecting check_untracked_delegation above), now passing tier_problems=
            # _COMPLETION_TIER_PROBLEMS (2026-08-16 follow-up incident: the ORIGINAL same-problem-
            # only version of this call still missed the case where the winning problem CHANGES
            # every attempt but always comes from COMPLETION_CHECKS -- a real run cycled
            # missing_findings -> missing_artifact -> uneven_task_investment -> task_verification_
            # flagged, never repeating, and report_underuses_evidence never got a single turn in
            # the whole run despite the report on disk having dropped 3 of 4 requested facets; see
            # _consecutive_tier_wins' own docstring for the full trace). never_final_blocker=False
            # (the default): unlike
            # check_untracked_delegation, a real dropped-facet problem winning as the run's
            # terminal reported blocker is correct, not something to protect against. Gated on
            # grounding_check.enabled for the same reason the real GROUNDING_CHECKS scan below is —
            # report_underuses_evidence only conceptually belongs to that tier by list membership,
            # it doesn't itself require ctx.grounding_problem, so it would otherwise bypass the
            # master switch entirely.
            if verdict is not None and config.cfg.get("settings", {}).get("grounding_check", {}).get("enabled", True):
                verdict = _yield_to_starved_check(verdict, ctx, check_report_underuses_evidence,
                                                   tier_problems=_COMPLETION_TIER_PROBLEMS)
            if verdict is not None:
                verdict = _with_other_problems_addendum(verdict, ctx, COMPLETION_CHECKS)
            # grounding_check.enabled is the section's master switch — before this guard it was a
            # documented no-op (config_template.yaml shipped it, nothing read it; 2026-07-12 audit,
            # G2). The pre-grounding checks above are structural, not grounding, and still run.
            if verdict is None and config.cfg.get("settings", {}).get("grounding_check", {}).get("enabled", True):
                ctx.grounding_problem = await real_grounding_problem(ctx.content or "")
                verdict = next((v for check in GROUNDING_CHECKS if (v := check(ctx)) is not None), None)
                # Both siblings share the same starvation risk (2026-07-24's finding, applied
                # 2026-07-29 to the newer per-task check too) -- neither is meant to compete with a
                # real correctness problem, but a run stuck on one OTHER recurring problem must not
                # starve either of them for its whole retry budget.
                #
                # 2026-07-31: was a hand-written `lambda c: A(c) or B(c)` here, live-confirmed dead
                # code (a run that dropped 4 whole tasks' worth of evidence, including the query's
                # own Colombia angle, fired report_underuses_findings 4+ consecutive times and never
                # once yielded, because `or` tried the already-winning check first and its condition
                # was still true). Replaced with the declarative _STARVATION_YIELD_TARGETS registry
                # + _apply_starvation_yield -- see that function's own docstring for why the dict
                # form structurally cannot repeat this ordering bug the way a hand-written lambda
                # could.
                verdict = _apply_starvation_yield(verdict, ctx)
                # 2026-07-29 (live incident, see _other_grounding_problems' docstring): check_
                # stub_source shadowed check_uncited_claims for 3 whole attempts, silently, because
                # both key off the single ctx.grounding_problem string real_grounding_problem
                # computes as only its own first hit. Surfaces (not swaps in) whatever else is
                # cheaply detectable in the same document.
                if verdict is not None:
                    verdict = _with_other_grounding_addendum(verdict, ctx)
            problem = verdict.problem if verdict else None

            run_state.sync_fetched_urls()
            # detail = the full human-readable verdict text (e.g. exactly which claim/URL failed),
            # not just the short problem label — previously only shown live via notify() and lost
            # once the terminal scrolled, so answering "why did attempt N fail" required re-parsing
            # the raw session-event JSON instead of just reading _run_state.json.
            run_state.record_attempt(attempt, problem, len(get_fetched_urls()),
                                      detail=verdict.warning if verdict else None)

            # Escalate early rather than granting the full attempt budget to a nudge that's already
            # proven ineffective. Confirmed live 2026-07-12: missing_artifact repeated 5 times
            # verbatim in one run — the model answered each one with confident "no further action
            # needed" prose and never once attempted write_workspace_file, burning wall-clock and
            # tool-call quota on retries that had already shown they don't work. Once the SAME
            # problem has now fired this many times in a row, fall straight through to the
            # final-verdict path (quarantine-restore or salvage) instead of granting more identical
            # retries — it preserves whatever real content already exists rather than grinding an
            # already-exhausted approach further. Each check's own escalating wording (see its
            # docstring) still gets one shot at each of these attempts first; this only trims how
            # many total attempts a provably-stuck pattern gets to burn.
            #
            # Generalized 2026-07-19 QA audit: originally hardcoded to problem == "missing_artifact"
            # only. Live-confirmed exposure: thin_coverage burned a full 8-attempt budget on a
            # verbatim-repeated narration with no guard at all; findings_ungrounded independently
            # confirmed 4 consecutive identical retries in a benchmark run (see the comment further
            # below, "a benchmark run already hit..."). Every OTHER problem type shares the same
            # risk in principle (a Builder/Planner repeating an identical failed fix), so the guard
            # now covers every problem except the one deliberately-excluded case:
            # missing_findings — confirmed live (check_missing_findings's own docstring) to
            # genuinely self-correct after 6 identical-looking failures, on the 7th attempt; an
            # early cutoff here would have killed that exact run's real recovery.
            # force_whole_rebuild (2026-07-22, ACM CAIS '26 planning-horizon paper, RESEARCH.md
            # §1): single-step replanning (this project's own "ADAPTIVE PLANNING LOOP" shape) gets
            # stuck in repetitive identical-action loops far more than full-horizon replanning,
            # which instead regenerates the WHOLE plan on a repetition trigger and tends to revise
            # strategy rather than keep re-patching the same failed local fix. Bounded to exactly
            # ONE extra, more expensive attempt per problem type (whole_approach_retry_used_for,
            # on run_state.data) before falling through to the pre-existing early-exit behavior
            # unchanged -- never an unbounded loop. Threshold is the module-level
            # CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD (2026-07-31, hoisted out of local
            # scope) -- shared with _capped's own cap logic so the two can never silently disagree
            # on the number again (they did, once, before this fix: _capped's own threshold was
            # first set to 2, which pre-empted this exact escalation's one guaranteed shot).
            force_whole_rebuild = False
            if problem and problem != "missing_findings":
                # 2026-07-31: uses the shared _consecutive_occurrences (same function _capped and
                # check_task_verification_flagged/check_thin_coverage's own caps use, see that
                # function's docstring) instead of a third hand-rolled copy of this loop -- this
                # exact duplication (this counter had its own independent untracked_delegation-skip
                # patch, added live the same night check_task_verification_flagged's own prior_same
                # loop got the identical patch, in DIFFERENT code) was itself one of the incidents
                # that motivated consolidating onto one shared definition. untracked_delegation is a
                # direct symptom of the model failing to comply with task_verification_flagged's own
                # "reuse the exact task_name" directive specifically, not an unrelated interruption
                # -- scoped narrowly to that one problem, not generically for every problem pair.
                skip = frozenset({"untracked_delegation"}) if problem == "task_verification_flagged" else frozenset()
                consecutive = _consecutive_occurrences(run_state, problem, skip)
                if consecutive >= CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD:
                    whole_approach_used = run_state.data.setdefault("whole_approach_retry_used_for", {})
                    if not whole_approach_used.get(problem):
                        whole_approach_used[problem] = True
                        force_whole_rebuild = True
                    else:
                        attempt = max_attempts

            if verdict and attempt < max_attempts:
                run_state.attempt = attempt + 1

                if problem == "findings_ungrounded":
                    _quarantine_artifact("findings.md", attempt + 1)
                elif problem in _QUARANTINE_PROBLEMS:
                    _quarantine_artifact(req_artifact, attempt + 1)

                # Per-attempt quota top-up: without this, a retry shares the same already-exhausted
                # pool as the failed attempt it's correcting (see plan doc diagnosis point 2) and
                # structurally can't recover on a complex query.
                pool = tool_quotas_ctx.get()
                if pool is not None:
                    topup_quota_pool(pool)

                # Write->Review->Fix: for artifact-authoring problems, dispatch a fresh-context writer
                # role (+PeerReviewer check) instead of nudging the Planner's own conversation — see
                # _dispatch_writer_review_fix and run_completion_check's docstring. Defensive: requires
                # BOTH roles registered, and any dispatch failure falls back to the classic path for
                # this cycle rather than losing the retry entirely.
                caller_sub_agents = available_sub_agents_ctx.get()
                has_peer_reviewer = caller_sub_agents and any(c.name == "PeerReviewer" for c in caller_sub_agents)

                if dispatch_task is not None and problem in _BUILDER_FIXABLE_PROBLEMS:
                    has_builder_pair = has_peer_reviewer and any(c.name == "Builder" for c in caller_sub_agents)
                    if has_builder_pair:
                        notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning} (dispatching Builder to rewrite, not the Planner)")
                        if pool is not None:
                            _ensure_writer_quota_headroom(pool)
                            # needed=3: PeerReviewer must read BOTH final_report.md and
                            # findings.md when reviewing the report (PEER_REVIEWER_INSTRUCTIONS),
                            # plus one possible Fix-pass re-read -- see docstring above, found live
                            # 2026-07-27 that the old needed=2 default was undersized for this path.
                            _ensure_reader_quota_headroom(pool, needed=3)
                        try:
                            if force_whole_rebuild:
                                builder_instructions = (
                                    f"Multiple attempts to fix '{req_artifact}' the same way have not "
                                    f"worked -- do NOT just patch the specific issue again. Rewrite "
                                    f"'{req_artifact}' completely from scratch using findings.md, as if "
                                    f"writing it for the first time, reconsidering your whole approach "
                                    f"to this task rather than repeating the same local fix. The "
                                    f"specific problem previously flagged was:\n{verdict.inject}\n\n"
                                    f"{_BUILDER_NO_DELEGATE_CLARIFICATION}"
                                    f"Write the corrected file now via write_workspace_file."
                                )
                            else:
                                builder_instructions = (
                                    f"Rewrite '{req_artifact}' from findings.md, fixing this specific problem:\n"
                                    f"{verdict.inject}\n\n{_BUILDER_NO_DELEGATE_CLARIFICATION}"
                                    f"Write the corrected file now via write_workspace_file."
                                )
                            await _dispatch_writer_review_fix(dispatch_task, "Builder", req_artifact, builder_instructions, attempt, notify)
                            run_state.save()
                            # Chained, not returned — see docstring. Loops straight into the next
                            # completion-check iteration instead of handing a turn back to the Planner.
                            continue
                        except Exception:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** Builder dispatch failed — falling back to asking the Planner directly.")

                elif dispatch_task is not None and problem in _FINDINGS_WRITER_FIXABLE_PROBLEMS:
                    has_findings_writer_pair = has_peer_reviewer and any(c.name == "FindingsWriter" for c in caller_sub_agents)
                    if has_findings_writer_pair:
                        notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning} (dispatching FindingsWriter to rewrite, not the Planner)")
                        if pool is not None:
                            _ensure_writer_quota_headroom(pool)
                            # needed=2 (the default): PeerReviewer reads only findings.md (the
                            # single target artifact here, no second file) + one possible Fix
                            # re-read.
                            _ensure_reader_quota_headroom(pool)
                        try:
                            # Deliberately NOT verdict.inject — that text is worded for the PLANNER
                            # fallback path (mentions delegate_tasks, "you have no write_workspace_file
                            # tool") and would be actively confusing to FindingsWriter, which has the
                            # opposite tool set (can write, can't delegate). FindingsWriter gets its
                            # own problem-appropriate directive plus its real evidence base instead.
                            if force_whole_rebuild:
                                write_directive = (
                                    "Multiple attempts to fix findings.md the same way have not "
                                    "worked -- do NOT just patch the specific issue again. "
                                    "Reconsider your whole approach and rebuild findings.md "
                                    "completely from scratch below, as if writing it for the "
                                    "first time, using strictly the real research results — never "
                                    "your own prior knowledge."
                                )
                            elif problem == "findings_ungrounded":
                                write_directive = (
                                    "The previous findings.md draft was fabricated or wholesale "
                                    "ungrounded and has been moved aside. Rebuild it now, strictly "
                                    "from the real research results below — never from your own "
                                    "prior knowledge."
                                )
                            elif problem == "stale_findings":
                                write_directive = (
                                    "More real research has been delegated since findings.md was "
                                    "last written -- it is now out of date. Rebuild it completely "
                                    "from ALL of the current real research results below (this "
                                    "includes everything from before, plus what's new), not just "
                                    "the newest additions."
                                )
                            else:
                                write_directive = "findings.md has never been written yet. Write it now from the real research results below."
                            findings_source_material = _build_findings_source_material(run_state)
                            findings_writer_instructions = (
                                f"{write_directive}\n\n{findings_source_material}\n\n"
                                f"Write the file now via write_workspace_file."
                            )
                            await _dispatch_writer_review_fix(
                                dispatch_task, "FindingsWriter", "findings.md", findings_writer_instructions,
                                attempt, notify, deterministic_fallback=findings_source_material,
                            )
                            # Staleness marker (check_stale_findings): record how many real,
                            # distinct, citable findings existed AT THIS WRITE, so a LATER
                            # completion check can tell whether more have been delegated since --
                            # see that check's own docstring for why a count comparison, not a
                            # findings.md-vs-report URL diff, is the right shape here.
                            run_state.data["findings_written_citable_count"] = len(_dedupe_findings(
                                [f for f in run_state.data.get("findings", []) if _is_citable_finding(f)]
                            ))
                            run_state.save()
                            # Chained, not returned — see docstring. Loops straight into the next
                            # completion-check iteration instead of handing a turn back to the Planner.
                            continue
                        except Exception:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** FindingsWriter dispatch failed — falling back to asking the Planner directly.")

                elif dispatch_task is not None and problem == "thin_coverage":
                    # Engine-driven iterative deepening (ROADMAP item 10, dzhng/deep-research
                    # pattern): thin_coverage is the one existing signal that means "the plan's own
                    # breadth came back thin" — the exact shape iterative deepening targets.
                    # Deliberately NOT applied to every retrying problem (missing_findings/
                    # missing_artifact fire on nearly every run's first attempt by design; a
                    # deepening round there would contradict the Planner's own "STOP EARLY"
                    # instruction and this project's anti-over-research stance). A clean/sufficient
                    # run never reaches a completion-check retry at all, so this never fires on one.
                    max_deepening_rounds = config.cfg.get("settings", {}).get("max_deepening_rounds", 1)
                    if run_state.data.get("deepening_round", 0) < max_deepening_rounds:
                        try:
                            dispatched = await _dispatch_deepening_round(dispatch_task, run_state, notify)
                            if dispatched:
                                run_state.save()
                                # Chained, not returned — same pattern as Builder/FindingsWriter
                                # above. Loops straight into the next completion-check iteration so
                                # coverage() is re-evaluated against the new findings before the
                                # next verdict is decided.
                                continue
                        except Exception:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** Deepening round dispatch failed — falling back to the classic nudge.")
                    # No real directions to act on, or round budget exhausted: fall through to the
                    # unchanged classic thin_coverage Planner nudge below — zero behavior change.

                elif dispatch_task is not None and problem == "report_underuses_evidence" and not force_whole_rebuild:
                    # Bespoke per-item dispatch, same shape as thin_coverage's deepening round
                    # above -- not a membership check against _BUILDER_FIXABLE_PROBLEMS, because
                    # this problem needs facet-scoped handling that tuple's single generic-rebuild
                    # dispatch doesn't support (see _dispatch_per_facet_builder_fix's own docstring
                    # for why one combined instruction already failed live). force_whole_rebuild is
                    # checked defensively but is effectively unreachable for this problem: _capped
                    # (used by check_report_underuses_evidence) silences the verdict at the SAME
                    # CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD force_whole_rebuild triggers on,
                    # so problem becomes None first in practice.
                    has_builder_pair = has_peer_reviewer and any(c.name == "Builder" for c in caller_sub_agents)
                    if has_builder_pair:
                        by_task, dropped = _facet_coverage(ctx)
                        if dropped:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning} "
                                   f"(dispatching Builder once per neglected facet, not the Planner)")
                            if pool is not None:
                                _ensure_writer_quota_headroom(pool)
                                # Scaled by facet count -- needed=3 alone (the _BUILDER_FIXABLE_
                                # PROBLEMS sizing) was calibrated for ONE Write->Review->Fix cycle;
                                # this branch runs up to _MAX_FACET_DISPATCHES of them, and an
                                # undersized quota makes PeerReviewer's later reviews look
                                # quota-starved (see _dispatch_writer_review_fix's own
                                # reads_before/reads_after fabricated-CLEAN guard).
                                _ensure_reader_quota_headroom(pool, needed=3 * max(1, min(len(dropped), _MAX_FACET_DISPATCHES)))
                            try:
                                await _dispatch_per_facet_builder_fix(dispatch_task, dropped, by_task, req_artifact, attempt, notify)
                                run_state.save()
                                # Chained, not returned — same pattern as Builder/FindingsWriter/
                                # thin_coverage above. Loops straight into the next completion-check
                                # iteration.
                                continue
                            except Exception:
                                notify(f"**System ({attempt + 1}/{max_attempts}):** Per-facet Builder dispatch failed — falling back to asking the Planner directly.")
                    # No Builder pair registered, no dropped facets (shouldn't happen — verdict
                    # already required dropped to fire), or dispatch failed: fall through to the
                    # unchanged classic inject-into-Planner nudge below.

                elif dispatch_task is not None and problem == "findings_underuses_evidence" and not force_whole_rebuild:
                    # Bespoke per-item dispatch, the same shape as report_underuses_evidence's own
                    # branch immediately above (one layer downstream) — not a membership check
                    # against _FINDINGS_WRITER_FIXABLE_PROBLEMS, because this problem needs
                    # facet-scoped handling that tuple's single generic-rebuild dispatch doesn't
                    # support (see _dispatch_per_facet_findings_writer_fix's own docstring for why
                    # one combined instruction already failed live, live-confirmed 2026-08-01).
                    has_findings_writer_pair = has_peer_reviewer and any(c.name == "FindingsWriter" for c in caller_sub_agents)
                    if has_findings_writer_pair:
                        _, dropped = _findings_facet_coverage(ctx)
                        if dropped:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning} "
                                   f"(dispatching FindingsWriter once per neglected facet, not the Planner)")
                            if pool is not None:
                                _ensure_writer_quota_headroom(pool)
                                # needed=2 (the _FINDINGS_WRITER_FIXABLE_PROBLEMS default): PeerReviewer
                                # reads only findings.md (one target artifact, not two like Builder's
                                # report+findings.md pair) — scaled by facet count for the same reason
                                # report_underuses_evidence's own branch scales its needed=3.
                                _ensure_reader_quota_headroom(pool, needed=2 * max(1, min(len(dropped), _MAX_FACET_DISPATCHES)))
                            try:
                                await _dispatch_per_facet_findings_writer_fix(dispatch_task, dropped, run_state, attempt, notify)
                                # Staleness marker, same as the generic FindingsWriter branch above —
                                # per-facet dispatch is still a real write to findings.md.
                                run_state.data["findings_written_citable_count"] = len(_dedupe_findings(
                                    [f for f in run_state.data.get("findings", []) if _is_citable_finding(f)]
                                ))
                                run_state.save()
                                # Chained, not returned — same pattern as every other bespoke branch
                                # above. Loops straight into the next completion-check iteration.
                                continue
                            except Exception:
                                notify(f"**System ({attempt + 1}/{max_attempts}):** Per-facet FindingsWriter dispatch failed — falling back to asking the Planner directly.")
                    # No FindingsWriter pair registered, no dropped facets (shouldn't happen —
                    # verdict already required dropped to fire), or dispatch failed: fall through
                    # to the unchanged classic inject-into-Planner nudge below.

                notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning}")
                new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                # No artifact-rebuild equivalent at the Planner level (nothing to dispatch a fresh
                # writer for here — this is the classic inject-into-Planner path, reached either
                # because dispatch_task is unavailable or the problem isn't writer-fixable) --
                # closest available equivalent to force_whole_rebuild is a reworded, stronger
                # directive telling the Planner to reconsider its whole approach instead of
                # repeating the same fix.
                inject_text = (
                    f"SYSTEM: The last {CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD} attempts to "
                    f"fix this the same way have not worked. Do not repeat the same fix again -- "
                    f"reconsider your whole approach to this task from scratch before retrying: "
                    f"{verdict.inject}"
                ) if force_whole_rebuild else verdict.inject
                new_inputs.append(Message("user", [{"type": "text", "text": inject_text}]))
                run_state.save()
                return True, new_inputs

            if verdict:
                # Retry budget is exhausted and a real problem still exists. The old project silently
                # accepted whatever was left at this point with no indication to the user that the output
                # is unverified or even absent — a genuinely observed failure mode in testing (both
                # "wrote something ungrounded" and, separately, "never wrote anything at all" have been
                # seen live), not a hypothetical one. Surface exactly which case this is instead of
                # asserting a file exists when it might not.
                # Name a sick search layer explicitly — confirmed live (2026-07-11): DDG throttling made
                # two different models' runs fail in ways that looked exactly like model fabrication.
                health = get_search_health()
                if health["calls"] >= 4 and health["failures"] * 2 >= health["calls"]:
                    notify(f"**System (final):** ⚠️ web_search failed {health['failures']}/{health['calls']} "
                           f"times this run (throttling or outage) — this failure is likely environmental, "
                           f"not a model problem. Re-run later before drawing conclusions about the model.")
                # The check the quarantined draft actually failed (the final-turn problem is usually
                # just missing_artifact — the model never rewrote after quarantine).
                quarantine_reason = next(
                    (a["problem"] for a in reversed(run_state.data.get("completion_check_attempts", []))
                     if a.get("problem") in _QUARANTINE_PROBLEMS), problem)
                if req_artifact in get_workspace_files():
                    # 2026-07-29 (live incident): this exact branch reported ONLY stub_source as
                    # "the" unresolved issue on a real run whose saved final_report.md ALSO had 6
                    # uncited-claims lines (check_uncited_claims never got a turn -- both key off
                    # the single ctx.grounding_problem string, see _other_grounding_problems'
                    # docstring). One-shot final branch, so recomputing both COMPLETION_CHECKS (a
                    # genuinely independent list, safe to re-walk directly) and the cheap grounding
                    # sub-checks (the correct layer for that list, NOT GROUNDING_CHECKS itself) here
                    # is cheap and honest about everything actually still wrong, not just whichever
                    # problem won last.
                    others_final = (
                        [o.problem for o in _collect_other_active_problems(ctx, COMPLETION_CHECKS, problem)]
                        + _other_grounding_problems(ctx, problem)
                    )[:_OTHER_ACTIVE_PROBLEMS_CAP]
                    others_note = (
                        " Also independently unresolved: " + "; ".join(others_final) + "."
                    ) if others_final else ""
                    notify(f"**System (final):** Retry budget exhausted with an unresolved issue ({problem}). "
                           f"`{req_artifact}` exists but could NOT be fully verified this run — treat its "
                           f"claims as unconfirmed. This was not silently accepted.{others_note}")
                elif problem == "missing_artifact" and _restore_quarantined_draft(req_artifact, quarantine_reason):
                    notify(f"**System (final):** The model never rewrote `{req_artifact}` after its draft "
                           f"was quarantined ({quarantine_reason}) — restored the quarantined draft, "
                           f"loudly labeled with the unresolved check. A real draft that failed one "
                           f"known check beats salvaged narration; review the flagged claims before "
                           f"trusting it.")
                else:
                    substantial_text = (find_substantial_text() if find_substantial_text else "") or last_assistant_text
                    # 2026-07-31: this salvage attempt used to be gated to a hardcoded tuple of
                    # problem names, widened three separate times in one session as new terminal
                    # problems were found unwritten-but-narrated (missing_artifact, then
                    # task_verification_flagged, then missing_findings). _salvage_narrated_report
                    # itself is generic and already has the real safety gate (refuses anything
                    # under 200 chars) -- it doesn't care WHY req_artifact is missing, only whether
                    # there's substantial narrated text to rescue. The tuple never added real
                    # protection, only a maintenance trap: every NEW problem that could legitimately
                    # end a run with req_artifact unwritten had to be remembered and added by hand,
                    # and each omission silently discarded a coherent narrated summary purely
                    # because a different check happened to be terminal. Now unconditional --
                    # applies whenever nothing else above handled it (req_artifact still doesn't
                    # exist, and the missing_artifact-specific quarantine-restore path didn't apply
                    # first) and there's real substantial text to salvage, for ANY problem.
                    if _salvage_narrated_report(req_artifact, substantial_text):
                        # Structural fallback, not another prompt nudge — see _salvage_narrated_report's
                        # docstring for why: nudging alone has proven insufficient for this exact pattern
                        # across two independent projects now.
                        notify(f"**System (final):** The model never called write_workspace_file despite "
                               f"repeated nudges, but had already narrated a substantial response. "
                               f"Auto-recovered it into `{req_artifact}`, clearly marked as unverified salvage "
                               f"content — this bypassed the grounding check entirely and MUST be reviewed "
                               f"before trusting it.")
                    else:
                        notify(f"**System (final):** Retry budget exhausted with an unresolved issue ({problem}). "
                               f"`{req_artifact}` was never written — no report was produced this run. This was "
                               f"not silently accepted as a success.")

            run_state.set_plan(get_workspace_file_content("_todos.md") or "")
            run_state.save()
            return False, current_input
    except Exception:
        # Deliberately non-fatal (a crashed CHECK must never kill a run that produced work), but
        # never silent again — this bare swallow hid a real completion-check crash on a live
        # benchmark run (2026-07-11), which then looked like a model that just stopped retrying.
        import traceback
        notify(f"**System:** completion check itself crashed — run ends unverified. This is an "
               f"engine bug, not a model failure:\n```\n{traceback.format_exc()}\n```")
        return False, current_input
