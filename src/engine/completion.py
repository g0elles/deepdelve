# Completion-check verdict engine, extracted from engine/tui.py (2026-07-12).
#
# WHY THIS SHAPE: the old run_completion_check was a ~250-line if/elif chain of giant
# triple-assignment f-strings. Twice (bd307f4, and again on run 13's regulation branch) an
# inserted branch silently swallowed the next `elif` header — both bodies merged, the later
# assignment won, and the file still parsed. The checks were fine; the container was the hazard.
# Now each problem type is one function returning a Verdict (or None), walked in an ordered list:
# first verdict wins, and there are no elif headers left to swallow. Adding a check = one function
# + one list entry. test_structural_checks.py's verdict matrix pins every problem's routing.
#
# The check_* functions themselves, Ctx/Verdict, and their 4 dedicated helpers
# (_findings_facet_coverage/_facet_coverage/find_duplicate_report_sections/_redelegate_directive)
# moved to engine/completion_checks.py (2026-08-24, group A of the completion.py decomposition
# plan -- see session_status/CURRENT.md and ROADMAP.md's completion.py entry). Imported back here
# because COMPLETION_CHECKS/GROUNDING_CHECKS (the routing tuples) and the rest of this module's
# dispatch/starvation machinery still reference them by name -- re-exported below the same way
# engine/tui.py re-exports _restore_quarantined_draft from engine.artifact_salvage via this module.
import re
import time
from typing import Callable, Optional

import config
from agent_framework import Message
from tools import tool_quotas_ctx, get_workspace_files, get_workspace_file_content
from utils.run_state import get_fetched_urls, get_search_health
from utils.grounding import (
    real_grounding_problem,
    cheap_grounding_problems,  # noqa: F401 — re-exported for test_structural_checks.py
)
from engine.orchestrator import (
    topup_quota_pool, available_sub_agents_ctx,
)
from engine.artifact_salvage import (
    _quarantine_artifact, _content_unchanged_since_last_quarantine, _restore_quarantined_draft,
    _salvage_narrated_report,
    _ensure_writer_quota_headroom, _ensure_reader_quota_headroom,
)
from engine.completion_checks import (  # noqa: F401 — re-exported for test_structural_checks.py/finetune/*
    Ctx, Verdict,
    check_not_delegated, check_requested_count_shortfall, _extract_requested_item_range,
    check_missing_query_facet,
    check_thin_coverage, check_task_verification_flagged,
    check_findings_ungrounded, check_missing_findings, check_stale_findings,
    check_findings_underuses_evidence, check_missing_artifact,
    check_academic_citation_style_abandoned, check_uneven_task_investment,
    check_untracked_delegation, check_report_underuses_findings, check_report_underuses_evidence,
    check_duplicate_report_sections, check_missing_specific_item_per_facet, check_claim_unsupported, check_no_urls,
    check_regulation_unsupported, check_specific_figure_unsupported, check_quote_paraphrased,
    check_non_url_citation, check_stub_source, check_nli_unsupported, check_topical_mismatch,
    check_editorializing_content,
    check_uncited_claims, check_excluded_topic, check_cross_source_contradiction,
    check_propagated_ungrounded_content, check_not_grounded,
    find_duplicate_report_sections, find_duplicate_heading_text, _findings_facet_coverage, _facet_coverage,
)
# Findings evidence-assembly (group B, 2026-08-24) -- see engine/findings_evidence.py's own header.
# Re-exported below the same way group A's completion_checks import above is: completion.py's own
# starvation/dispatch machinery (staying here) still calls several of these by name, and
# completion_checks.py's own local (function-body) imports of _capped/_dedupe_findings/
# _is_citable_finding/etc from `engine.completion` depend on this re-export still existing here.
from engine.findings_evidence import (  # noqa: F401 — re-exported for test_structural_checks.py/finetune/*
    _CUTOFF_ONLY_SUMMARY_RE, _is_citable_finding, _verification_warning_targets_url,
    _update_task_verification, _dedupe_findings, _collapse_multi_url_task_findings,
    _uncited_task_names, _reorder_findings_for_position_bias, _find_propagated_bad_content,
    _build_findings_source_material,
)

DEFAULT_MAX_COMPLETION_CHECK_ATTEMPTS = 3


def _ablation_disabled(name: str) -> bool:
    """Controlled-ablation switch (2026-08-17, RESEARCH.md §18f): every completion-check
    coordination mechanism this project has added was validated only by 'did the one live-observed
    symptom stop recurring,' never by a with/without comparison the way MAST's own causal
    intervention evidence (§2, this document) or the Illusion-of-Multi-Agent-Advantage paper's own
    audit methodology (§18f) do. `settings.ablation.disable_<name>` (default unset/False for every
    name -- current, full behavior, unaffected unless explicitly opted into) lets
    `eval/evaluate.py --runs 3` be pointed at the standing benchmark with a specific mechanism
    turned off, to measure whether it's genuinely load-bearing. Deliberately a single shared
    lookup (not a new config key per mechanism scattered across the file) so a NEW ablation
    candidate is a one-line call here, not a new plumbing pattern each time."""
    return bool(config.get_setting("ablation", {}).get(f"disable_{name}", False))

# Ordered: first verdict wins. GROUNDING_CHECKS only run once every pre-grounding check passes
# (delegation happened, findings.md exists and is grounded, the artifact exists) because
# real_grounding_problem is the one expensive fact and needs the artifact's content to exist.
# A new check is one function above + one entry here — and one row in the verdict matrix test.
COMPLETION_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_not_delegated,
    # Upstream of check_thin_coverage (below): "did you plan ENOUGH" before "did what you planned
    # succeed" -- see its own docstring (2026-08-28) for the live incident this closes.
    check_requested_count_shortfall,
    # Same family, same "did the Planner scope this correctly" question, different axis (facet
    # IDENTITY rather than facet COUNT) -- see its own docstring for the live capability gap this
    # closes (2026-08-29).
    check_missing_query_facet,
    check_thin_coverage,
    check_task_verification_flagged,
    check_findings_ungrounded,
    check_missing_findings,
    check_stale_findings,
    check_findings_underuses_evidence,
    check_missing_artifact,
    # Structural, not grounding -- needs only ctx.content/report_style, so it belongs here
    # (COMPLETION_CHECKS) rather than GROUNDING_CHECKS despite being citation-related; placed
    # right after check_missing_artifact (needs ctx.content to exist, same requirement as this
    # check) and before check_uneven_task_investment/check_untracked_delegation's hygiene checks,
    # since a report that deleted its own required citation format is closer to a correctness
    # problem than a hygiene one. See its own docstring for the live incident (2026-08-25) this
    # closes -- a report abandoning its instructed academic citation format entirely, which every
    # URL-grounding check trivially passes.
    check_academic_citation_style_abandoned,
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
    "not_delegated", "requested_count_shortfall", "missing_query_facet", "thin_coverage", "task_verification_flagged",
    "findings_ungrounded", "missing_findings", "stale_findings", "findings_underuses_evidence",
    "missing_artifact", "report_style_violation", "uneven_task_investment", "untracked_delegation",
})

GROUNDING_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_claim_unsupported,
    check_no_urls,
    check_stub_source,
    check_regulation_unsupported,
    check_specific_figure_unsupported,
    check_quote_paraphrased,
    check_non_url_citation,
    check_nli_unsupported,
    check_topical_mismatch,
    # Fifth grounding layer, same tier as its two siblings above (term-overlap passed, not
    # contradicted, not topically unrelated) -- see its own docstring for the whack-a-mole
    # root-cause this closes (2026-08-29). UNVALIDATED, opt-in (config default False).
    check_editorializing_content,
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
    # Self-consistency, not citation accuracy or breadth -- placed after both since a report with a
    # bad citation or a missing task should get those fixed first; still before the generic
    # catch-all so a genuinely duplicate-only report gets a specific, actionable nudge.
    check_duplicate_report_sections,
    # Completeness of an EXPLICIT per-facet instruction, not citation accuracy or self-consistency
    # -- placed after duplicate-section (a different completeness axis) but before the generic
    # catch-all. See its own docstring for the live incident (2026-08-30) this closes.
    check_missing_specific_item_per_facet,
    check_not_grounded,  # generic catch-all: fires on ANY grounding problem — keep it LAST
]

# Problems whose bad draft gets quarantined (renamed aside) before the retry, and which count as
# "the check the quarantined draft actually failed" when restoring it at the final verdict.
# run_completion_check derives its quarantine branch from this tuple (findings_ungrounded
# quarantines findings.md instead of the artifact) — one list, no second copy to forget.
_QUARANTINE_PROBLEMS = ("not_grounded", "claim_unsupported", "non_url_citation",
                        "regulation_unsupported", "specific_figure_unsupported",
                        "quote_paraphrased", "stub_source", "duplicate_report_sections",
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
                             "non_url_citation", "regulation_unsupported",
                             "specific_figure_unsupported", "quote_paraphrased",
                             "stub_source", "duplicate_report_sections",
                             "nli_unsupported", "topical_mismatch", "editorializing", "uncited_claims",
                             "excluded_topic_present", "cross_source_contradiction",
                             "report_underuses_findings", "report_style_violation",
                             # Added 2026-08-29, live incident: previously had no fix path at all
                             # (nagged the Planner's own conversation, no writer dispatch) --
                             # exhausted a run's retry budget 5+ times before falling back to a
                             # stale, off-topic salvage. See check_propagated_ungrounded_content's
                             # own docstring for the fix reasoning.
                             "propagated_ungrounded",
                             # Added 2026-08-30, live incident: the fix is "cite an already-fetched
                             # source you forgot to use," which Builder can do directly from
                             # findings.md -- see check_missing_specific_item_per_facet's own
                             # docstring.
                             "missing_specific_item_per_facet")

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

# Dispatch orchestration (group C, 2026-08-24) -- see engine/completion_dispatch.py's own header.
# Re-exported below the same way groups A/B's imports above are: run_completion_check (staying
# here) still calls these by name, and _dispatch_per_facet_builder_fix's own local (function-body)
# import of _BUILDER_NO_DELEGATE_CLARIFICATION from `engine.completion` depends on THIS module
# still defining that constant above.
from engine.completion_dispatch import (  # noqa: F401,E402 — re-exported for test_structural_checks.py/finetune/*
    _WRITER_EMPTY_RETRY_ATTEMPTS, _dispatch_writer_review_fix, _MAX_FACET_DISPATCHES,
    _dispatch_per_facet_builder_fix, _dispatch_per_facet_findings_writer_fix,
    _select_deepening_tasks, _dispatch_deepening_round,
)


# Starvation/capping state machine (group D, 2026-08-24) -- see engine/completion_starvation.py's
# own header. Re-exported below the same way groups A-C's imports above are: run_completion_check
# (staying here) still calls these by name, and completion_checks.py's own local (function-body)
# imports of _capped/_consecutive_occurrences from `engine.completion` depend on this re-export
# still existing here.
from engine.completion_starvation import (  # noqa: F401,E402 — re-exported for test_structural_checks.py/finetune/*
    _consecutive_occurrences, _consecutive_tier_wins, _STARVATION_SKIP_THRESHOLD,
    CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD, _capped, _yield_to_starved_check,
    _STARVATION_YIELD_TARGETS, _apply_starvation_yield, _OTHER_ACTIVE_PROBLEMS_CAP,
    _collect_other_active_problems, _with_other_problems_addendum, _other_grounding_problems,
    _with_other_grounding_addendum,
)

# Constant boundary marker both _with_other_problems_addendum/_with_other_grounding_addendum use
# to append their "ALSO currently true" tail -- shared here so force_whole_rebuild's own
# instruction-building (below) can strip it back off without duplicating the literal string.
_OTHER_PROBLEMS_ADDENDUM_MARKER = " ALSO currently true"


def _strip_other_problems_addendum(inject: str) -> str:
    """Removes a `_with_other_problems_addendum`/`_with_other_grounding_addendum` tail from an
    already-built `.inject` string, if present -- used only by force_whole_rebuild's own
    instruction-building (2026-08-25, ReflexGrad arXiv:2511.14584 finding): its own ablation
    explicitly tested merging multiple simultaneous corrective signals into one instruction and
    found it "produced incoherent guidance" for their fast/slow dual-process router, the same
    shape as bundling a lower-priority secondary problem onto the ONE expensive full-rewrite
    instruction force_whole_rebuild issues. A no-op when no addendum is present."""
    idx = inject.find(_OTHER_PROBLEMS_ADDENDUM_MARKER)
    return inject if idx == -1 else inject[:idx]


def _with_wait_prefix(verdict: "Verdict", run_state: "RunState") -> "Verdict":  # noqa: F821
    """Prepends a short explicit self-reflection cue to `verdict.inject` when this exact problem
    already fired on the immediately preceding attempt -- adapted from Tsui's self-correction
    blind-spot finding (COLM 2026, arXiv:2507.02778, fully read, saved to papers/): 14 open-source
    non-reasoning models tested show a 64.5% average blind spot specifically when asked to fix an
    error framed as their OWN prior turn (vs. the identical error framed as external input), and
    appending the single word "Wait" before the model continues cuts that blind spot by 89% in
    their decoding-time intervention. DeepDelve has no decoding-time hook into the model's own
    generation stream (each retry is a fresh system message to a fresh dispatch, not a token
    inserted into an in-progress completion), so this adapts the finding's SPIRIT rather than its
    literal mechanism: an explicit "Wait." cue at the start of the corrective system message,
    only on a genuine repeat (not the first time a problem is raised, where there's nothing yet
    to have blind-spotted). Uses the same `_consecutive_occurrences` definition every other
    escalation mechanism in this file shares, so this can never disagree with force_whole_rebuild
    or _capped about whether a problem has "already fired before." Gated by
    `settings.ablation.disable_wait_prefix` (default unset/False) for the same controlled-ablation
    protocol as `_ablation_disabled`'s other names."""
    if _ablation_disabled("wait_prefix"):
        return verdict
    if _consecutive_occurrences(run_state, verdict.problem) < 1:
        return verdict
    return verdict._replace(inject="Wait. " + verdict.inject)


async def _detect_verdict(req_artifact: str, attempt: int, max_attempts: int,
                           run_state: "RunState") -> tuple["Ctx", Optional["Verdict"]]:  # noqa: F821
    """Builds this attempt's `Ctx` and runs the full two-tier verdict scan (COMPLETION_CHECKS,
    then — only if the whole first tier came back clean — GROUNDING_CHECKS), including every
    starvation-yield/addendum wrapper each tier applies. Extracted from `run_completion_check`
    (2026-08-24, group E) as the one purely-sequential, no-early-exit phase of that function's
    loop body — detecting the problem (or lack of one) never consumes the retry budget or mutates
    loop state, only actually retrying does, so this has no `continue`/`return` of its own and is
    safe as a standalone step. See `run_completion_check`'s own body for what happens with the
    (ctx, verdict) pair this returns."""
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
        # 2026-08-24 fix: check_no_urls/check_non_url_citation/check_uncited_claims each need to
        # know the active citation format to give correct corrective guidance -- see
        # _citation_format_reminder's own docstring (completion_checks.py) for the live incident
        # this closes (a real --style academic run oscillated for ~18 completion-check attempts
        # across two live runs because these checks always told the model to switch to standard
        # style's `[Title](URL)` format, directly contradicting ACADEMIC_CITATION_FORMAT_
        # INSTRUCTIONS). Default "standard" (Ctx's own field default) matches config_template.yaml.
        report_style=config.get_setting("report_style", "standard"),
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
    if verdict is not None and config.get_setting("grounding_check", {}).get("enabled", True):
        verdict = _yield_to_starved_check(verdict, ctx, check_report_underuses_evidence,
                                           tier_problems=_COMPLETION_TIER_PROBLEMS)
    if verdict is not None:
        verdict = _with_other_problems_addendum(verdict, ctx, COMPLETION_CHECKS)
    # grounding_check.enabled is the section's master switch — before this guard it was a
    # documented no-op (config_template.yaml shipped it, nothing read it; 2026-07-12 audit,
    # G2). The pre-grounding checks above are structural, not grounding, and still run.
    if verdict is None and config.get_setting("grounding_check", {}).get("enabled", True):
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
    if verdict is not None:
        verdict = _with_wait_prefix(verdict, run_state)
    return ctx, verdict


def _compute_force_whole_rebuild(run_state: "RunState", problem: Optional[str], attempt: int,  # noqa: F821
                                  max_attempts: int, req_artifact: str) -> tuple[bool, int]:
    """Escalate early rather than granting the full attempt budget to a nudge that's already
    proven ineffective. Confirmed live 2026-07-12: missing_artifact repeated 5 times
    verbatim in one run — the model answered each one with confident "no further action
    needed" prose and never once attempted write_workspace_file, burning wall-clock and
    tool-call quota on retries that had already shown they don't work. Once the SAME
    problem has now fired this many times in a row, fall straight through to the
    final-verdict path (quarantine-restore or salvage) instead of granting more identical
    retries — it preserves whatever real content already exists rather than grinding an
    already-exhausted approach further. Each check's own escalating wording (see its
    docstring) still gets one shot at each of these attempts first; this only trims how
    many total attempts a provably-stuck pattern gets to burn.

    Generalized 2026-07-19 QA audit: originally hardcoded to problem == "missing_artifact"
    only. Live-confirmed exposure: thin_coverage burned a full 8-attempt budget on a
    verbatim-repeated narration with no guard at all; findings_ungrounded independently
    confirmed 4 consecutive identical retries in a benchmark run. Every OTHER problem type
    shares the same risk in principle (a Builder/Planner repeating an identical failed fix),
    so the guard now covers every problem except the one deliberately-excluded case:
    missing_findings — confirmed live (check_missing_findings's own docstring) to
    genuinely self-correct after 6 identical-looking failures, on the 7th attempt; an
    early cutoff here would have killed that exact run's real recovery.

    force_whole_rebuild (2026-07-22, ACM CAIS '26 planning-horizon paper, RESEARCH.md
    §1): single-step replanning (this project's own "ADAPTIVE PLANNING LOOP" shape) gets
    stuck in repetitive identical-action loops far more than full-horizon replanning,
    which instead regenerates the WHOLE plan on a repetition trigger and tends to revise
    strategy rather than keep re-patching the same failed local fix. Bounded to exactly
    ONE extra, more expensive attempt per problem type (whole_approach_retry_used_for,
    on run_state.data) before falling through to the pre-existing early-exit behavior
    unchanged -- never an unbounded loop. Threshold is the module-level
    CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD (2026-07-31, hoisted out of local
    scope) -- shared with _capped's own cap logic so the two can never silently disagree
    on the number again (they did, once, before this fix: _capped's own threshold was
    first set to 2, which pre-empted this exact escalation's one guaranteed shot).

    Returns (force_whole_rebuild, attempt) — attempt is forced to max_attempts once a run has
    already used its one force_whole_rebuild shot for this problem and is STILL stuck, the same
    mutation `run_completion_check`'s own loop used to apply to its local `attempt` variable
    inline. Extracted 2026-08-24 (group E) as a pure, no-early-exit computation over already-
    available state — safe as a standalone step, same discipline as `_detect_verdict` above."""
    if problem and problem != "missing_findings" and not _ablation_disabled("force_whole_rebuild"):
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
        stuck = consecutive >= CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD
        # Content-identity escalation (2026-08-17 live incident, see
        # _content_unchanged_since_last_quarantine's own docstring): the SAME evidence
        # producing the SAME rejected content is just as provably stuck as 3 consecutive
        # occurrences of the SAME problem NAME, even when an unrelated problem interrupts
        # the streak -- checked here (not just via the counter above) so this doesn't
        # depend on the problem name repeating consecutively at all.
        if not stuck:
            quarantine_target = "findings.md" if problem == "findings_ungrounded" else (
                req_artifact if problem in _QUARANTINE_PROBLEMS else None)
            if quarantine_target:
                stuck = _content_unchanged_since_last_quarantine(
                    quarantine_target, get_workspace_file_content(quarantine_target))
        if stuck:
            whole_approach_used = run_state.data.setdefault("whole_approach_retry_used_for", {})
            if not whole_approach_used.get(problem):
                whole_approach_used[problem] = True
                return True, attempt
            else:
                return False, max_attempts
    return False, attempt


def _notify_final_verdict(ctx: "Ctx", problem: Optional[str], req_artifact: str,  # noqa: F821
                           run_state: "RunState", notify, last_assistant_text: str,  # noqa: F821
                           find_substantial_text: Optional[Callable[[], str]]) -> None:
    """Retry budget is exhausted and a real problem still exists. The old project silently
    accepted whatever was left at this point with no indication to the user that the output
    is unverified or even absent — a genuinely observed failure mode in testing (both
    "wrote something ungrounded" and, separately, "never wrote anything at all" have been
    seen live), not a hypothetical one. Surfaces exactly which case this is instead of
    asserting a file exists when it might not, via `notify()` side effects only (this
    function returns nothing — extracted 2026-08-24, group E, from `run_completion_check`'s
    own terminal `if verdict:` branch, which unconditionally falls through to
    `run_state.set_plan()`/`run_state.save()`/`return False, current_input` regardless of what
    happens here, so this has no control-flow interaction with its caller beyond notify())."""
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
    max_attempts = config.get_setting(
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
    _configured_run_minutes = config.get_setting("max_run_minutes", 0) or 0
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
            ctx, verdict = await _detect_verdict(req_artifact, attempt, max_attempts, run_state)
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
            # strategy rather than keep re-patching the same failed local fix. See
            # _compute_force_whole_rebuild's own docstring for the escalation logic itself
            # (extracted 2026-08-24, group E — a pure, no-early-exit computation over
            # run_state/problem/attempt, safe as a standalone step).
            force_whole_rebuild, attempt = _compute_force_whole_rebuild(
                run_state, problem, attempt, max_attempts, req_artifact)

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
                                # Deliberately strips any "ALSO currently true" secondary-problem
                                # addendum (2026-08-25, ReflexGrad finding): this is the ONE
                                # expensive, full-rewrite attempt a provably-stuck run gets --
                                # diluting it with a lower-priority competing problem is exactly
                                # the "incoherent guidance from merged signals" shape their own
                                # ablation found harmful. The Wait prefix (_with_wait_prefix) is
                                # NOT stripped -- it's a general self-reflection cue, not a
                                # competing correction, and is exactly this attempt's situation
                                # (a problem that already fired before).
                                clean_inject = _strip_other_problems_addendum(verdict.inject)
                                builder_instructions = (
                                    f"Multiple attempts to fix '{req_artifact}' the same way have not "
                                    f"worked -- do NOT just patch the specific issue again. Rewrite "
                                    f"'{req_artifact}' completely from scratch using findings.md, as if "
                                    f"writing it for the first time, reconsidering your whole approach "
                                    f"to this task rather than repeating the same local fix. The "
                                    f"specific problem previously flagged was:\n{clean_inject}\n\n"
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
                        except Exception as e:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** Builder dispatch failed ({type(e).__name__}: {e}) — falling back to asking the Planner directly.")

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
                                # Name the SPECIFIC bad URL(s), not just "it was ungrounded"
                                # (2026-08-18, live incident: findings.md.rejected_attempt_3 and
                                # _4 were byte-identical, 11 minutes apart -- FindingsWriter kept
                                # re-citing the exact same already-rejected URL because this
                                # directive never told it WHICH source failed verification, only
                                # that something did, and it got the same source material both
                                # times. verdict.warning (not verdict.inject, which is worded for
                                # the Planner-fallback path) carries the raw partially_ungrounded
                                # detail, e.g. "unverified_entry_sources:https://...,https://...".
                                bad_urls_note = ""
                                if verdict is not None:
                                    m = re.search(r"unverified_entry_sources:([^)]*)", verdict.warning)
                                    if m:
                                        bad_urls_note = (
                                            f" The source(s) that failed verification last time: "
                                            f"{m.group(1).strip()} — do not attribute any finding "
                                            f"to these again; use only a URL that actually appears "
                                            f"in the real research results below."
                                        )
                                write_directive = (
                                    "The previous findings.md draft was fabricated or wholesale "
                                    f"ungrounded and has been moved aside.{bad_urls_note} Rebuild it "
                                    "now, strictly from the real research results below — never "
                                    "from your own prior knowledge."
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
                        except Exception as e:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** FindingsWriter dispatch failed ({type(e).__name__}: {e}) — falling back to asking the Planner directly.")

                elif dispatch_task is not None and problem == "thin_coverage":
                    # Engine-driven iterative deepening (ROADMAP item 10, dzhng/deep-research
                    # pattern): thin_coverage is the one existing signal that means "the plan's own
                    # breadth came back thin" — the exact shape iterative deepening targets.
                    # Deliberately NOT applied to every retrying problem (missing_findings/
                    # missing_artifact fire on nearly every run's first attempt by design; a
                    # deepening round there would contradict the Planner's own "STOP EARLY"
                    # instruction and this project's anti-over-research stance). A clean/sufficient
                    # run never reaches a completion-check retry at all, so this never fires on one.
                    max_deepening_rounds = config.get_setting("max_deepening_rounds", 1)
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
                        except Exception as e:
                            notify(f"**System ({attempt + 1}/{max_attempts}):** Deepening round dispatch failed ({type(e).__name__}: {e}) — falling back to the classic nudge.")
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
                            except Exception as e:
                                notify(f"**System ({attempt + 1}/{max_attempts}):** Per-facet Builder dispatch failed ({type(e).__name__}: {e}) — falling back to asking the Planner directly.")
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
                            except Exception as e:
                                notify(f"**System ({attempt + 1}/{max_attempts}):** Per-facet FindingsWriter dispatch failed ({type(e).__name__}: {e}) — falling back to asking the Planner directly.")
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
                    # Addendum stripped here too, same reasoning as the Builder-fixable branch
                    # above (2026-08-25, ReflexGrad finding) -- see _strip_other_problems_addendum.
                    f"{_strip_other_problems_addendum(verdict.inject)}"
                ) if force_whole_rebuild else verdict.inject
                new_inputs.append(Message("user", [{"type": "text", "text": inject_text}]))
                run_state.save()
                return True, new_inputs

            if verdict:
                _notify_final_verdict(ctx, problem, req_artifact, run_state, notify,
                                       last_assistant_text, find_substantial_text)

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
