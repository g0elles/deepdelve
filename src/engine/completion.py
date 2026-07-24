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
    find_cross_source_contradictions,
)
from engine.orchestrator import (
    topup_quota_pool, available_sub_agents_ctx, _extract_excluded_topics, get_context_budget,
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

    Conservative by construction, same philosophy as every other check here: fires only when a
    MAJORITY of top-level tasks came back with no real source (ratio below threshold, default
    0.5) AND there are enough of them for that ratio to mean something (min_tasks, default 2) — a
    single-task query (the common case for a simple factual lookup) that succeeded is 1.0
    regardless of "breadth" and never trips this; a single-task query that failed is caught by
    missing_findings/missing_artifact already, not this. Escalates like every other repeat-prone
    check here on a second consecutive occurrence — a nudge that already failed to move the ratio
    isn't worth repeating verbatim."""
    cov_cfg = config.cfg.get("settings", {}).get("coverage_check", {})
    if not cov_cfg.get("enabled", True):
        return None
    threshold = cov_cfg.get("threshold", 0.5)
    min_tasks = cov_cfg.get("min_tasks", 2)
    coverage = ctx.run_state.coverage()
    if coverage["total"] < min_tasks or coverage["ratio"] >= threshold:
        return None

    prior_same = 0
    for a in reversed(ctx.run_state.data.get("completion_check_attempts", [])):
        if a.get("problem") == "thin_coverage":
            prior_same += 1
        else:
            break

    uncovered_list = ", ".join(f"'{t}'" for t in coverage["uncovered_task_names"][:5])
    if prior_same == 0:
        directive = (
            f"Only {coverage['covered']} of {coverage['total']} research tasks you delegated "
            f"actually turned up a real source ({uncovered_list} came back empty). Do NOT write "
            f"the final report around only the tasks that worked — delegate_tasks again for the "
            f"uncovered angles, phrased differently or with a narrower query if the first attempt "
            f"was too broad or too specific to find anything."
        )
    else:
        directive = (
            f"Coverage is STILL thin after a prior warning ({coverage['covered']}/{coverage['total']} "
            f"tasks with a real source). If you have already tried rephrasing and genuinely cannot "
            f"find sources for {uncovered_list}, say so explicitly in the report as an acknowledged "
            f"gap rather than silently omitting it — do not keep re-delegating the exact same query."
        )

    return Verdict(
        "thin_coverage",
        f"Only {coverage['covered']}/{coverage['total']} delegated research tasks produced a real source ({uncovered_list}). Pushing agent to cover the gap or acknowledge it.",
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
    from utils.grounding import extract_cited_urls
    findings_urls = set(extract_cited_urls(get_workspace_file_content("findings.md") or ""))
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

    unused_list = ", ".join(unused[:5])
    if prior_same == 0:
        directive = (
            f"'{ctx.req_artifact}' only cites {len(findings_urls) - len(unused)} of "
            f"{len(findings_urls)} real sources actually present in findings.md — the rest "
            f"({unused_list}) are real, fetched, and available but never appear anywhere in the "
            f"report. Rewrite it to actually incorporate the neglected sources too, not just "
            f"whichever cluster was easiest to write about first — if findings.md covers multiple "
            f"distinct angles, the report must reflect all of them, not just one."
        )
    else:
        directive = (
            f"'{ctx.req_artifact}' STILL neglects real sources from findings.md after a prior "
            f"warning ({unused_list}). Do not just lightly edit the existing draft — actually add "
            f"sections covering these neglected sources."
        )
    return Verdict(
        "report_underuses_findings",
        f"'{ctx.req_artifact}' cites only {len(findings_urls) - len(unused)}/{len(findings_urls)} of findings.md's real sources ({unused_list} never cited). Pushing agent to incorporate the rest.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive}",
    )


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
    actually drew on, not yet a report-level grounding problem worth quarantining over."""
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
                    return Verdict(
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
                    )
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
    check_findings_ungrounded,
    check_missing_findings,
    check_missing_artifact,
    check_untracked_delegation,
]
GROUNDING_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_claim_unsupported,
    check_no_urls,
    check_stub_source,
    check_regulation_unsupported,
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
    check_not_grounded,  # generic catch-all: fires on ANY grounding problem — keep it LAST
]

# Problems whose bad draft gets quarantined (renamed aside) before the retry, and which count as
# "the check the quarantined draft actually failed" when restoring it at the final verdict.
# run_completion_check derives its quarantine branch from this tuple (findings_ungrounded
# quarantines findings.md instead of the artifact) — one list, no second copy to forget.
_QUARANTINE_PROBLEMS = ("not_grounded", "claim_unsupported", "non_url_citation",
                        "regulation_unsupported", "stub_source", "nli_unsupported",
                        "topical_mismatch", "findings_ungrounded")

# Problems fixable by rewriting `req_artifact` from the SAME findings.md, no new research needed —
# dispatched to a fresh-context Builder (+ PeerReviewer check) by run_completion_check's
# Write->Review->Fix loop instead of growing the Planner's own conversation. The complement
# (not_delegated) genuinely needs more/different research, which only the Planner can decide and
# delegate, so that one still falls through to the classic inject-into-Planner path below.
_BUILDER_FIXABLE_PROBLEMS = ("missing_artifact", "not_grounded", "claim_unsupported",
                             "non_url_citation", "regulation_unsupported", "stub_source",
                             "nli_unsupported", "topical_mismatch", "uncited_claims",
                             "excluded_topic_present", "cross_source_contradiction",
                             "report_underuses_findings")

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
_FINDINGS_WRITER_FIXABLE_PROBLEMS = ("missing_findings", "findings_ungrounded")


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


def _salvage_narrated_report(req_artifact: str, last_assistant_text: str) -> bool:
    """Structural fallback for a real, recurring pattern (documented in the reference project too,
    surviving multiple rounds of prompt-only fixes there): the model narrates a complete,
    well-formatted report as chat text instead of ever calling write_workspace_file, across the
    entire retry budget. Rather than throw away real content because a specific tool call didn't
    fire, auto-persist the model's own last substantial response — clearly marked as unverified
    salvage, not a substitute for the grounding check. Returns True if a salvage write happened.
    Two callers: `_dispatch_writer_review_fix` (immediately after each Write dispatch, so a
    narrating model gets salvaged on attempt 1 instead of burning the whole retry budget first —
    added 2026-07-18) and `run_completion_check`'s final-verdict path (the original, last-resort
    use, for the classic inject-into-Planner flow when no writer-role dispatch is configured)."""
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
        salvage = (
            "> **AUTO-RECOVERED DRAFT** — the model narrated this content as chat text instead of "
            "calling `write_workspace_file`, across the full retry budget. This has NOT passed the "
            "grounding check and its claims are UNVERIFIED. Review before trusting it.\n\n"
            + last_assistant_text.strip()
        )
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


def _ensure_reader_quota_headroom(pool: dict) -> None:
    """Mirror of _ensure_writer_quota_headroom, for `read_workspace_file` -- found live 2026-07-20,
    fixed 2026-07-21. A Write->Review->Fix cycle needs PeerReviewer to actually open the artifact
    (its 'REVIEW: CLEAN' is only trusted if the quota shows a real read happened -- see
    _dispatch_writer_review_fix's reads_before/reads_after gate above) plus the Fix pass often
    re-reading source content, against the SAME shared cumulative pool every role's
    read_workspace_file calls draw from. Unlike write_workspace_file, read_workspace_file has no
    entry in settings.retry_quota_topup by default, so nothing replenished it between remediation
    cycles at all. Confirmed live: 3 remediation cycles in one run (routing-classifier re-test,
    2026-07-20) exhausted a 30-call pool, and the final BuilderFix pass self-reported being unable
    to re-read its source ('Due to workspace tool quota limits...') and silently dropped an entire
    correctly-researched section rather than erroring loudly. Tops up ONLY the headroom this cycle
    actually needs, called fresh before EVERY cycle -- protects each one independently regardless
    of how many prior cycles already ran, same shape as the write-side helper."""
    entry = pool.get("read_workspace_file")
    if entry is None:
        return
    needed = 2  # PeerReviewer's mandatory read + one possible Fix-pass re-read
    headroom = entry["limit"] - entry["used"]
    if headroom < needed:
        entry["limit"] += (needed - headroom)


async def _dispatch_writer_review_fix(dispatch_task, writer_role: str, req_artifact: str,
                                       write_instructions: str, attempt: int, notify) -> None:
    """Write -> Review -> Fix, all fresh-context sub-agent dispatches, none of which touch the
    Planner's own conversation. Shared by both writer roles that exist for exactly this reason —
    Builder (writes/fixes final_report.md from findings.md) and FindingsWriter (writes/fixes
    findings.md from this run's real structured results, see _build_findings_source_material) —
    same loop shape, different writer role/artifact/source material. Caller
    (run_completion_check) is responsible for quarantine, quota top-up, and run_state bookkeeping
    around this call — this function only runs the dispatch sequence. Raises on any dispatch
    failure so the caller can fall back to the classic inject-into-Planner path for this cycle
    rather than silently doing nothing.

    Capped at 3 dispatches total (Write, Review, optional Fix) — no unbounded nesting."""
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
    # later run) if this one raises.
    gate_token = writer_gate_ctx.set({"write_done": False}) if writer_role == "FindingsWriter" else None
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
    gate_token = writer_gate_ctx.set({"write_done": False}) if writer_role == "FindingsWriter" else None
    try:
        await dispatch_task(f"{writer_role}Fix_attempt{attempt + 1}_reviewed", fix_instructions, writer_role)
    finally:
        if gate_token is not None:
            writer_gate_ctx.reset(gate_token)


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
    marker, AND isn't confirmed off-topic by the scope-relevance check. Shared predicate
    (2026-07-22) so _build_findings_source_material, _uncited_task_names, and
    _find_propagated_bad_content all agree on one definition.

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
    Scoped to RELEVANCE warnings only, not the two VERIFICATION-warning variants (citation-mismatch
    flags) -- a relevance-flagged finding is confirmed off-topic for the required scope with zero
    value regardless of its other content, whereas a verification warning flags a narrower
    citation mismatch that may still coexist with other real, usable content in the same finding."""
    src = f.get("source_url") or ""
    summary = f.get("summary") or ""
    if not (src.startswith("http") and not _CUTOFF_ONLY_SUMMARY_RE.match(summary)):
        return False
    return "[SYSTEM RELEVANCE WARNING" not in summary


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


def _build_findings_source_material(run_state: "RunState") -> str:  # noqa: F821 — utils.run_state.RunState, annotation only
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
    duplicates don't affect, and the raw list is the audit trail other tooling may want intact."""
    findings = run_state.data.get("findings", [])
    urls = run_state.data.get("fetched_urls", [])
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
    budget = get_context_budget()
    omitted_task_names = []
    if budget:
        kept = []
        used = 0
        for task_name, block in entries:
            if used + len(block) > budget:
                omitted_task_names.append(task_name)
                continue
            kept.append(block)
            used += len(block) + 2  # +2 for the "\n\n" join separator
        findings_block = "\n\n".join(kept) or "(no findings recorded yet)"
    else:
        findings_block = "\n\n".join(block for _, block in entries) or "(no findings recorded yet)"

    fetched_block = "\n".join(
        f"- {u.get('url')} (saved as {u.get('filename')})" for u in urls
    ) or "(no URLs fetched yet)"
    omitted_note = ""
    if omitted_task_names:
        omitted_list = ", ".join(f"'{n}'" for n in omitted_task_names[:20])
        omitted_note = (
            f"\n\n({len(omitted_task_names)} more finding(s) exist for this run but were omitted "
            f"here to stay within the model's context budget: {omitted_list}. Do NOT silently "
            f"drop these — if they matter for the report, note that this research exists but its "
            f"detail wasn't available to you, rather than pretending it doesn't exist.)"
        )
    uncited_note = ""
    if uncited_task_names:
        uncited_list = ", ".join(f"'{n}'" for n in uncited_task_names[:20])
        uncited_note = (
            f"\n\n({len(uncited_task_names)} dispatched task(s) produced no real fetched or "
            f"reference URL, so they have nothing citable: {uncited_list}. These are NOT source "
            f"entries above and must never be turned into one — do not invent a URL or title for "
            f"them. If they matter, note that this research was attempted but produced no "
            f"citable source, rather than fabricating one.)"
        )
    return (
        "REAL RESEARCH RESULTS FROM THIS RUN, one entry per dispatched Searcher/Analyzer task "
        "that returned a real citable source "
        "(this is your ENTIRE evidence base — you have no other memory of this run):\n\n"
        f"{findings_block}{omitted_note}{uncited_note}\n\n"
        "ALL URLS ACTUALLY FETCHED THIS RUN, for cross-reference — each file's full content is "
        "readable under its saved filename via read_workspace_file/grep_workspace_file if a "
        "summary above isn't detailed enough:\n"
        f"{fetched_block}"
    )


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


async def run_completion_check(query: str, current_input, run_state: "RunState", notify, last_assistant_text: str = "", dispatch_task=None, budget_deadline: float | None = None):  # noqa: F821 — utils.run_state.RunState, annotation only
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
    req_artifact = config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", None)
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

    try:
        while True:
            attempt = run_state.attempt
            if budget_deadline is not None and attempt < max_attempts and time.monotonic() > budget_deadline:
                notify(f"**System (final):** max_run_minutes exceeded mid-retry-chain — stopping "
                       f"further Write/Review/Fix dispatches and finishing with whatever exists.")
                attempt = max_attempts
            quotas = tool_quotas_ctx.get()
            files = get_workspace_files()
            ctx = Ctx(
                req_artifact=req_artifact,
                attempt=attempt,
                max_attempts=max_attempts,
                delegated=bool(quotas and quotas.get("delegate_tasks", {}).get("used", 0) > 0),
                files=files,
                content=get_workspace_file_content(req_artifact) if req_artifact in files else None,
                quotas=quotas,
                run_state=run_state,
            )

            # Detecting the problem (or lack of one) never consumes the retry budget —
            # only actually retrying does. Otherwise a success on the final allowed
            # attempt is never recognized as a success (it just falls through silently).
            verdict = next((v for check in COMPLETION_CHECKS if (v := check(ctx)) is not None), None)
            # grounding_check.enabled is the section's master switch — before this guard it was a
            # documented no-op (config_template.yaml shipped it, nothing read it; 2026-07-12 audit,
            # G2). The pre-grounding checks above are structural, not grounding, and still run.
            if verdict is None and config.cfg.get("settings", {}).get("grounding_check", {}).get("enabled", True):
                ctx.grounding_problem = await real_grounding_problem(ctx.content or "")
                verdict = next((v for check in GROUNDING_CHECKS if (v := check(ctx)) is not None), None)
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
            # unchanged -- never an unbounded loop.
            CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD = 3
            force_whole_rebuild = False
            if problem and problem != "missing_findings":
                consecutive = 0
                for a in reversed(run_state.data.get("completion_check_attempts", [])):
                    if a.get("problem") == problem:
                        consecutive += 1
                    else:
                        break
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
                            _ensure_reader_quota_headroom(pool)
                        try:
                            if force_whole_rebuild:
                                builder_instructions = (
                                    f"Multiple attempts to fix '{req_artifact}' the same way have not "
                                    f"worked -- do NOT just patch the specific issue again. Rewrite "
                                    f"'{req_artifact}' completely from scratch using findings.md, as if "
                                    f"writing it for the first time, reconsidering your whole approach "
                                    f"to this task rather than repeating the same local fix. The "
                                    f"specific problem previously flagged was:\n{verdict.inject}\n\n"
                                    f"Write the corrected file now via write_workspace_file."
                                )
                            else:
                                builder_instructions = (
                                    f"Rewrite '{req_artifact}' from findings.md, fixing this specific problem:\n"
                                    f"{verdict.inject}\n\nWrite the corrected file now via write_workspace_file."
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
                            else:
                                write_directive = "findings.md has never been written yet. Write it now from the real research results below."
                            findings_writer_instructions = (
                                f"{write_directive}\n\n{_build_findings_source_material(run_state)}\n\n"
                                f"Write the file now via write_workspace_file."
                            )
                            await _dispatch_writer_review_fix(dispatch_task, "FindingsWriter", "findings.md", findings_writer_instructions, attempt, notify)
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
                    notify(f"**System (final):** Retry budget exhausted with an unresolved issue ({problem}). "
                           f"`{req_artifact}` exists but could NOT be fully verified this run — treat its "
                           f"claims as unconfirmed. This was not silently accepted.")
                elif problem == "missing_artifact" and _restore_quarantined_draft(req_artifact, quarantine_reason):
                    notify(f"**System (final):** The model never rewrote `{req_artifact}` after its draft "
                           f"was quarantined ({quarantine_reason}) — restored the quarantined draft, "
                           f"loudly labeled with the unresolved check. A real draft that failed one "
                           f"known check beats salvaged narration; review the flagged claims before "
                           f"trusting it.")
                else:
                    # _find_last_substantial_text scans the TUI's session event history — lazy import,
                    # engine.tui imports this module at load time.
                    from engine.tui import _find_last_substantial_text
                    if problem == "missing_artifact" and _salvage_narrated_report(req_artifact, _find_last_substantial_text() or last_assistant_text):
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
