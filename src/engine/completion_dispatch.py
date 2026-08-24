# Dispatch orchestration, extracted from engine/completion.py (2026-08-24, group C of the
# completion.py decomposition plan -- see session_status/CURRENT.md and ROADMAP.md's completion.py
# entry). The Write->Review->Fix loop shared by Builder/FindingsWriter, its per-facet variants, and
# engine-driven iterative deepening -- all fresh-context sub-agent dispatches bypassing the
# Planner's own conversation, called by run_completion_check (still in engine/completion.py).
import asyncio
import math
import os
from typing import Optional

from tools import tool_quotas_ctx, get_workspace_file_content, writer_gate_ctx
from engine.artifact_salvage import (
    _DETERMINISTIC_SALVAGE_BANNER, _salvage_narrated_report,
    _dispatch_task_retrying_transient_json_error,
)
from engine.findings_evidence import _build_findings_source_material

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
    from engine.completion import _BUILDER_NO_DELEGATE_CLARIFICATION
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
