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
import asyncio
import math
import os
import re
import time
from typing import Callable, Optional

import config
from agent_framework import Message
from tools import tool_quotas_ctx, get_workspace_files, get_workspace_file_content, writer_gate_ctx
from utils.run_state import get_fetched_urls, get_search_health
from utils.grounding import (
    real_grounding_problem, cheap_grounding_problems, _is_null_finding_summary,
)
from engine.orchestrator import (
    topup_quota_pool, available_sub_agents_ctx, get_context_budget,
    _looks_like_renamed_task, _content_word_overlap,
)
from engine.artifact_salvage import (
    _quarantine_artifact, _content_unchanged_since_last_quarantine, _restore_quarantined_draft,
    _DETERMINISTIC_SALVAGE_BANNER, _salvage_narrated_report,
    _ensure_writer_quota_headroom, _ensure_reader_quota_headroom,
    _dispatch_task_retrying_transient_json_error,
)
from engine.completion_checks import (  # noqa: F401 — re-exported for test_structural_checks.py/finetune/*
    Ctx, Verdict,
    check_not_delegated, check_thin_coverage, check_task_verification_flagged,
    check_findings_ungrounded, check_missing_findings, check_stale_findings,
    check_findings_underuses_evidence, check_missing_artifact, check_uneven_task_investment,
    check_untracked_delegation, check_report_underuses_findings, check_report_underuses_evidence,
    check_duplicate_report_sections, check_claim_unsupported, check_no_urls,
    check_regulation_unsupported, check_specific_figure_unsupported, check_quote_paraphrased,
    check_non_url_citation, check_stub_source, check_nli_unsupported, check_topical_mismatch,
    check_uncited_claims, check_excluded_topic, check_cross_source_contradiction,
    check_propagated_ungrounded_content, check_not_grounded,
    find_duplicate_report_sections, _findings_facet_coverage, _facet_coverage,
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
    check_specific_figure_unsupported,
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
    # Self-consistency, not citation accuracy or breadth -- placed after both since a report with a
    # bad citation or a missing task should get those fixed first; still before the generic
    # catch-all so a genuinely duplicate-only report gets a specific, actionable nudge.
    check_duplicate_report_sections,
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
                             "nli_unsupported", "topical_mismatch", "uncited_claims",
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


# How many retries a writer dispatch gets after producing nothing usable (zero tool calls, zero
# trailing text) before falling back to deterministic salvage or raising -- see the retry loop
# inside _dispatch_writer_review_fix for the live incident that motivated raising this above 1.
_WRITER_EMPTY_RETRY_ATTEMPTS = 2


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

    Capped at 5 dispatches total (Write, up to _WRITER_EMPTY_RETRY_ATTEMPTS immediate Write-retries
    only if the prior attempt produced nothing usable, Review, optional Fix) — no unbounded
    nesting.

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
        write_result = await _dispatch_task_retrying_transient_json_error(dispatch_task, f"{writer_role}Fix_attempt{attempt + 1}", write_instructions, writer_role)
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
            # Bounded retry loop before giving up: an empty response is plausibly a transient
            # flake, not a persistent one -- confirmed live the first 3 empty responses that
            # motivated this fix were isolated, never two in a row for the same completion-check
            # attempt. A fresh dispatch (same instructions, same fresh context) has a real chance
            # of succeeding outright, which is strictly better than immediately giving up: the
            # alternative (raising right away) still costs a full completion-check attempt AND a
            # round-trip through the Planner (which has no write_workspace_file tool and can only
            # re-arrive at "dispatch FindingsWriter again" next cycle anyway) to reach the same
            # place a retry can reach directly, one dispatch later.
            #
            # _WRITER_EMPTY_RETRY_ATTEMPTS (2026-08-18, was a single hardcoded retry): a real run
            # (session_status/2026-08-17.md, "run6") showed the ORIGINAL one-shot retry itself
            # also returning nothing usable, on all 3 completion-check rounds that run hit this
            # path -- one retry alone doesn't reliably recover it. Bumped to a small bounded loop
            # instead of one fixed attempt; each retry reuses the SAME externally-reframed
            # instructions (already a different input from the original per the comment below, not
            # a verbatim repeat) rather than escalating wording further per round -- no live
            # evidence yet that a 2nd retry needs materially different phrasing from the 1st, and
            # each retry is a full sub-agent dispatch, so the cap stays small.
            for retry_num in range(1, _WRITER_EMPTY_RETRY_ATTEMPTS + 1):
                notify(
                    f"**System ({attempt + 1}):** {writer_role} returned nothing usable for "
                    f"`{req_artifact}` — retrying ({retry_num}/{_WRITER_EMPTY_RETRY_ATTEMPTS}) "
                    f"immediately before giving up."
                )
                retry_gate_token = writer_gate_ctx.set(
                    {"write_done": False, "recommended_tool": recommended_tool, "target_file": req_artifact}
                ) if writer_role == "FindingsWriter" else None
                # Strengthened, non-identical retry instructions for FindingsWriter specifically
                # (2026-08-17, live incident): a real transcript showed the original dispatch's
                # FIRST tool call violate the writer_gate_ctx block (called read_workspace_file on
                # a source file before ever writing) and the turn then ended immediately -- zero
                # further tool calls, zero trailing text (the same "zero trailing text"
                # synthesis-vanishing mechanism this project already tracks for Searcher/Analyzer
                # turns, ARCHITECTURE.md §2's own writeup, now confirmed to also hit a writer
                # role). Retrying with the exact SAME instructions gives the model no new signal
                # to avoid repeating the identical first move -- self-correction literature
                # (RESEARCH.md §18b) is specific that an unchanged retry mostly reproduces the
                # same output, while externally-reframed input measurably helps. `write_instructions`
                # already tells FindingsWriter not to read before writing (FINDINGS_WRITER_INSTRUCTIONS'
                # own Workflow step 2) -- this doesn't repeat that, it makes the retry's OWN input
                # genuinely different by leading with an unambiguous, un-missable directive before
                # the model reads anything else.
                retry_instructions = write_instructions
                if writer_role == "FindingsWriter":
                    retry_instructions = (
                        f"CRITICAL: your immediately PREVIOUS attempt at this exact task ended with "
                        f"nothing written, because it called a read/search tool before writing anything "
                        f"-- that call was rejected and the attempt was lost. Your VERY FIRST tool call "
                        f"in this response MUST be `{recommended_tool or 'write_workspace_file'}`, with "
                        f"no other tool call before it, no exceptions.\n\n{write_instructions}"
                    )
                try:
                    write_result = await _dispatch_task_retrying_transient_json_error(
                        dispatch_task, f"{writer_role}Fix_attempt{attempt + 1}_retry{retry_num}", retry_instructions, writer_role)
                finally:
                    if retry_gate_token is not None:
                        writer_gate_ctx.reset(retry_gate_token)
                # Re-check against the SAME think_before baseline (not re-snapshotted) so this
                # reflects "was think_tool used in ANY attempt so far" -- the question that
                # actually matters once a retry has happened, since think_tool_skipped feeds later
                # wording (the is_clean notify note, the Fix pass's think_tool_note).
                think_after = pool.get("think_tool", {}).get("used") if pool else None
                think_tool_skipped = think_before is not None and think_after == think_before
                if get_workspace_file_content(req_artifact) is not None:
                    break
                retry_text = write_result if isinstance(write_result, str) else str(write_result)
                if _salvage_narrated_report(req_artifact, retry_text):
                    notify(
                        f"**System ({attempt + 1}):** {writer_role} narrated `{req_artifact}` as "
                        f"chat text instead of calling `write_workspace_file` — auto-recovered its "
                        f"own content as the artifact (flagged unverified) instead of retrying blind."
                    )
                    break
            else:
                # Loop exhausted without a `break` -- every retry (not just the first) produced
                # nothing usable and nothing narrated to salvage.
                if deterministic_fallback and _salvage_narrated_report(
                        req_artifact, deterministic_fallback, banner=_DETERMINISTIC_SALVAGE_BANNER):
                    # Confirmed live 2026-07-26: the "isolated, never two in a row" assumption the
                    # original immediate-retry fix was built on does not always hold -- see this
                    # function's own docstring for the run that motivated this branch. Unlike the
                    # narrated-text salvage above, this content never came from the model at all --
                    # it's assembled deterministically from run_state's own real findings, so it's
                    # available even when the model produces nothing whatsoever, every attempt.
                    notify(
                        f"**System ({attempt + 1}):** {writer_role} produced nothing usable "
                        f"{_WRITER_EMPTY_RETRY_ATTEMPTS + 1} times in a row (original + "
                        f"{_WRITER_EMPTY_RETRY_ATTEMPTS} retries) — auto-recovered `{req_artifact}` "
                        f"directly from this run's real research data instead of losing the cycle "
                        f"entirely."
                    )
                else:
                    # Still nothing after every retry -- this used to fall through to dispatching
                    # PeerReviewer anyway, against an artifact that flatly doesn't exist.
                    # PeerReviewer then has no filename to review, and confirmed live it degrades
                    # into guessing wrong paths (burned its entire read_workspace_file quota on
                    # nonexistent filenames before giving up, in the exact run that surfaced this).
                    # Raising here treats "the write produced nothing usable every attempt" as the
                    # dispatch failure it actually is, per this function's own documented contract
                    # -- the caller's normal retry loop gets a fresh attempt next time instead of
                    # an entire PeerReviewer dispatch being wasted on a file that was never
                    # written. Uses get_workspace_file_content (backend-agnostic: disk or
                    # in-memory), NOT the os.path.exists check above (disk-only, always false for
                    # the in-memory workspace backend regardless of real content -- fine for the
                    # salvage decision above, an existing harmless quirk, but would falsely raise
                    # here on every in-memory write that already succeeded).
                    raise RuntimeError(
                        f"{writer_role} dispatch produced no '{req_artifact}' "
                        f"{_WRITER_EMPTY_RETRY_ATTEMPTS + 1} times in a row (original + "
                        f"{_WRITER_EMPTY_RETRY_ATTEMPTS} retries) and nothing narrated to salvage "
                        f"any attempt"
                    )

    # Snapshot read_workspace_file's usage count BEFORE dispatching PeerReviewer, so a fabricated
    # "REVIEW: CLEAN" that never actually opened the file can be caught below (see is_clean gate).
    # None (not 0) when the quota isn't tracked at all -- distinguishes "can't verify" from "verified
    # zero reads," so a config with this quota disabled doesn't get falsely distrusted. Reuses the
    # `pool` object already fetched above for the think_tool snapshot (same object, same run).
    reads_before = pool.get("read_workspace_file", {}).get("used") if pool else None

    review = await _dispatch_task_retrying_transient_json_error(
        dispatch_task,
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
        # "Wait." prefix (2026-08-17, RESEARCH.md §18b, Self-Correction Bench arXiv:2507.02778):
        # appending the single word "Wait" after a model's own erroneous/rejected output is the
        # paper's own single most actionable, training-free finding -- reduces the measured
        # self-correction blind spot by 89.3% on average with zero fine-tuning. The paper's own
        # experimental harness appends it mid-generation (a continuation cue); this dispatch is a
        # fresh turn, not a continuation, so the adaptation here is a deliberation cue at the very
        # start of the correction ask instead -- same intent (prime reconsideration before the
        # model reproduces its prior mistake), not a literal replication of the paper's exact
        # mechanism, worth noting honestly rather than overclaiming a precise reproduction.
        f"Wait. PeerReviewer critiqued your last draft of '{req_artifact}'. Fix every issue it "
        f"raised, using only the real source material below (never your own prior knowledge), "
        f"then rewrite the file:\n\n{review_text}\n\n"
        f"--- YOUR ORIGINAL TASK INSTRUCTIONS AND SOURCE MATERIAL (unchanged) ---\n{write_instructions}"
        f"{think_tool_note}"
    )
    gate_token = writer_gate_ctx.set(
        {"write_done": False, "recommended_tool": recommended_tool, "target_file": req_artifact}
    ) if writer_role == "FindingsWriter" else None
    try:
        await _dispatch_task_retrying_transient_json_error(dispatch_task, f"{writer_role}Fix_attempt{attempt + 1}_reviewed", fix_instructions, writer_role)
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
    retry that gets through as the FIRST rename match (advisory-only, not yet a hard reject -- see
    `_rename_match_escalates` in orchestrator.py, which only escalates on the SECOND match against
    the same target) still produces a genuinely SEPARATE finding under a different task_name, whose
    summary independently restates the same underlying research -- confirmed live: `final_report.md`
    carried two near-identical "Mexico City" rent sections citing the same sources with the same
    figures, from two dispatches of what was really one facet. Exact-key dedup above can't catch
    this (the two summaries are independently generated text, not byte-identical). Reuses
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
    gc_cfg = config.get_setting("grounding_check", {})
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
