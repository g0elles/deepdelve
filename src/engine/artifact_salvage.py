# Artifact quarantine/restore/salvage helpers, extracted from engine/completion.py (2026-08-24,
# ROADMAP.md Pending "completion.py's mixed responsibilities"). Disk-touching quarantine/restore/
# salvage logic plus the writer/reader quota-headroom top-ups and the transient-Ollama-JSON-error
# retry wrapper -- all free-standing, no dependency on completion.py's Ctx/Verdict types or the
# check-list/starvation state machine (ARCHITECTURE.md section 1), which is why this group was
# chosen as the first, lowest-risk slice to split out: a straight move, not a behavior change.
# engine/completion.py imports these back at module level, so every existing external import path
# (e.g. engine/tui.py's `from engine.completion import _restore_quarantined_draft` re-export,
# consumed by test_structural_checks.py) keeps working unchanged.
import os
from typing import Optional


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


def _content_unchanged_since_last_quarantine(req_artifact: str, current_content: Optional[str]) -> bool:
    """True if `current_content` (about to be quarantined, i.e. rejected and discarded) is
    byte-identical to the MOST RECENT already-quarantined snapshot for this exact artifact.

    2026-08-17 live incident: a real run (`session_status/CURRENT.md`, "run6") produced 3
    byte-identical `findings.md.rejected_attempt_N` snapshots in a row, confirming the retry loop
    was re-offering the SAME content for rejection every time, not the model genuinely retrying
    and reproducing a fresh error (the self-correction blind spot literature describes,
    `RESEARCH.md` §18b) -- the deterministic fallback path (`_salvage_narrated_report`'s
    `deterministic_fallback` branch) bypasses model generation variance entirely when FindingsWriter
    itself returns nothing usable, so "try again" alone can never produce a different outcome.

    Deliberately a STRONGER, more precise signal than `_consecutive_occurrences`' problem-name
    counting: the problem name can legitimately ALTERNATE between attempts
    (`findings_ungrounded` -> `untracked_delegation` -> `findings_ungrounded`) while the
    UNDERLYING CONTENT never changes at all -- confirmed exactly this shape in the run that
    motivated this fix, which is why the existing 3-consecutive-same-problem threshold never
    fired even though the run was provably stuck. Content identity is ground truth regardless of
    what the intervening problem was named."""
    if not current_content:
        return False
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if not path:
            return False
        existing = sorted(
            (p for p in (f"{path}.rejected_attempt_{n}" for n in range(1, 10)) if os.path.exists(p)),
        )
        if not existing:
            return False
        with open(existing[-1], "r", encoding="utf-8") as f:
            prior = f.read()
        return prior == current_content
    except Exception:
        return False


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
    "> **AUTO-RECOVERED DRAFT** — FindingsWriter produced no usable output across its original "
    "attempt and every retry. This is assembled directly and deterministically from this run's "
    "real research data (`RunState.findings`) — it was never written or reviewed by a model, so "
    "it is unorganized/unedited, but every entry traces to a source this run actually fetched.\n\n"
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


def _is_transient_ollama_json_error(exc: Exception) -> bool:
    """ollama/ollama#6351 / #12064 (open, unfixed upstream, checked live 2026-08-20): a raw,
    unescaped newline the model emits inside a tool-call argument string can break Ollama's own
    JSON serialization on EITHER endpoint (native /api/chat or OpenAI-compat /v1) -- confirmed
    live via ministral-3:8b, 3 of 4 full runs hit this exact signature
    ("invalid character '\\n' in string literal", HTTP 500), regardless of endpoint, temperature,
    or context settings. Content-dependent, not deterministic: a fresh generation attempt has a
    real chance of not reproducing the exact same invalid byte sequence, unlike a genuine,
    reproducible failure -- this narrow string match deliberately does NOT swallow those."""
    msg = str(exc)
    return "invalid character" in msg and "string literal" in msg


async def _dispatch_task_retrying_transient_json_error(dispatch_task, *args, **kwargs):
    """Thin wrapper around dispatch_task: retries ONCE, unchanged, if the call raises the known
    transient Ollama JSON-serialization bug (see _is_transient_ollama_json_error) -- every other
    exception, and a second occurrence of this same one, propagates immediately so this can never
    mask a real, reproducible failure or loop unboundedly."""
    try:
        return await dispatch_task(*args, **kwargs)
    except Exception as e:
        if not _is_transient_ollama_json_error(e):
            raise
        return await dispatch_task(*args, **kwargs)
