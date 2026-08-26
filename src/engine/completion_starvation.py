# Starvation/capping state machine (group D of the completion.py decomposition plan, 2026-08-24)
# -- see session_status/CURRENT.md and ROADMAP.md's completion.py entry. Moved out of
# engine/completion.py after a coverage audit added direct characterization tests for _capped/
# _apply_starvation_yield/_collect_other_active_problems/_with_other_problems_addendum (the three
# gaps a static-analysis-only test had left uncovered), per this project's standing rule that this
# specific subsystem (ARCHITECTURE.md's flagged hazard -- 5 real bugs in one 2026-07-24 sitting)
# gets characterization tests BEFORE any mechanical move, not just after. Pure move, no rewrite --
# verified with a before/after sorted-line-set diff, same discipline as groups A-C.
from typing import Optional

import config
from utils.run_state import get_fetched_urls
from utils.grounding import cheap_grounding_problems
from engine.completion_checks import Ctx, Verdict, check_report_underuses_evidence


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
    see _other_grounding_problems_addendum below, the correct-layer version for that list).

    Gated by `settings.ablation.disable_other_problems_addendum` (2026-08-25, ReflexGrad
    arXiv:2511.14584 finding, fully read): their own ablation found merging multiple simultaneous
    corrective signals into one instruction "produced incoherent guidance" for a dual-process
    router -- the same shape as this addendum. Local import (not module-level): `_ablation_
    disabled` lives in engine.completion, which imports this module at load time, so a module-
    level import back would be circular -- deferred to call time is safe, same trick already used
    elsewhere in this decomposition (see completion_checks.py's own local imports)."""
    from engine.completion import _ablation_disabled
    if _ablation_disabled("other_problems_addendum"):
        return verdict
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
    _other_grounding_problems above instead of re-walking GROUNDING_CHECKS itself. Shares the same
    `disable_other_problems_addendum` ablation gate -- see that function's own docstring."""
    from engine.completion import _ablation_disabled
    if _ablation_disabled("other_problems_addendum"):
        return verdict
    others = _other_grounding_problems(ctx, verdict.problem)
    if not others:
        return verdict
    addendum = (
        " ALSO currently true in the same document (lower priority than the above -- do not undo "
        "it while fixing the above): " + "; ".join(others)
    )
    return verdict._replace(inject=verdict.inject + addendum)
