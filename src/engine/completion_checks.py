# Completion-check functions, extracted from engine/completion.py (2026-08-24) -- group A of the
# completion.py decomposition plan (see session_status/CURRENT.md). Pure move: every check
# function here is unchanged from its prior body, plus a handful of local (function-body)
# `from engine.completion import ...` lines where a check calls a helper that stays in
# completion.py (_capped/_consecutive_occurrences/_dedupe_findings/_is_citable_finding/
# _uncited_task_names/_find_propagated_bad_content/_CUTOFF_ONLY_SUMMARY_RE) -- those helpers are
# also needed by the findings-evidence-assembly and starvation/capping machinery that remain in
# completion.py, and completion.py itself imports Ctx/Verdict/every check_* from this module, so a
# module-level import back would be circular. A local import deferred to call time isn't.
#
# COMPLETION_CHECKS/GROUNDING_CHECKS themselves, and everything after them (the routing tuples,
# findings-evidence-assembly, dispatch orchestration, the starvation/capping state machine,
# run_completion_check), deliberately stay in engine/completion.py for this pass -- see
# ROADMAP.md's completion.py entry for the rest of the plan.
import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple, Optional

import config
from tools import get_workspace_file_content
from utils.run_state import get_fetched_urls
from utils.grounding import (
    fully_ungrounded, partially_ungrounded, split_into_heading_sections,
    find_cross_source_contradictions, extract_cited_urls, parse_academic_references,
    excluded_topic_semantic_hit, _NAMED_REGULATION_RE, _REGULATION_ID_RE,
)
from engine.orchestrator import (
    _extract_excluded_topics, _content_word_overlap, _extract_required_facets,
    _instruction_entities, _EXCLUSION_CUE_RE, _extract_required_item_type,
)

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
    # settings.report_style at the time this Ctx was built (2026-08-24 fix — see
    # _citation_format_reminder's own docstring for the live bug this closes). Default "standard"
    # matches config_template.yaml's own default, so any caller that doesn't pass this explicitly
    # (e.g. an older test) gets the same behavior as before this field existed.
    report_style: str = "standard"

    @property
    def last_chance_prefix(self) -> str:
        return "THIS IS YOUR FINAL ATTEMPT. " if (self.attempt + 1) >= self.max_attempts else ""


def _citation_format_reminder(report_style: str) -> str:
    """The one-line citation-format reminder check_no_urls/check_non_url_citation/
    check_uncited_claims each append to their corrective directive, style-aware since 2026-08-24.

    Before this fix, all three hardcoded the STANDARD style's `- **[Title](URL)**` / inline
    `[Title](URL)` markdown-link format regardless of which settings.report_style was actually
    active — Ctx had no report_style field at all, so these checks structurally could not know.
    Confirmed live (2026-08-24, a real --style academic run): ACADEMIC_CITATION_FORMAT_
    INSTRUCTIONS (prompts.py) tells the model to cite as `(Author, Year)` plus a numbered
    References section, but every retry of these three checks told it to switch to inline
    `[Title](URL)` links instead -- directly contradictory system instructions mid-run for any
    non-standard style. The run oscillated between non_url_citation and claim_unsupported for
    ~18 completion-check attempts across two live runs (~93 minutes combined) and never
    converged; the model's own final draft showed a hybrid `(Title, Year)` citation style,
    plausibly from trying to reconcile both conflicting directives at once. See
    prompts.py's ACADEMIC_CITATION_FORMAT_INSTRUCTIONS/ANSWER_CITATION_FORMAT_INSTRUCTIONS for
    the real per-style formats this mirrors -- kept as a short reminder here, not the full
    multi-paragraph instructions block (these checks fire mid-rewrite, not at the start)."""
    if report_style == "academic":
        return (
            "an in-text `(Author, Year)` citation (first author's surname) immediately after the "
            "claim, with a matching numbered References entry at the end (`N. Author, A. (Year). "
            "Title. <the real URL you fetched>`) — NOT an inline `[Title](URL)` markdown link, "
            "that is the standard style's format, not this run's"
        )
    if report_style == "answer":
        return (
            "`(Source: [Title](URL))` at the end of the answer sentence, using a URL you actually "
            "fetched this run — this style has no separate References/Sources section"
        )
    return "the exact format `- **[Title](URL)**`"


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


def _nearest_preceding_facet(preceding_text: str, facets: list) -> Optional[int]:
    """Index into `facets` of whichever required facet's entity the LAST (closest, rightmost)
    capitalized word before a regulation match names -- used to attribute a regulation found in a
    SHARED/comparative section (one discussing more than one facet) to the specific entity it's
    actually about, instead of crediting every facet the section happens to mention. Confirmed
    necessary live: a real intro sentence ("Germany's Renewable Energy Sources Act (EEG) sets
    binding capacity targets, while Japan's FIT policy guarantees...") mentions BOTH entities, but
    only Germany's is adjacent to the actual regulation name -- "which entities does this section
    mention" (the naive first attempt) wrongly credited Japan too. Returns None if no capitalized
    word precedes the match, or the nearest one names no required facet."""
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
       section) requires PROXIMITY: each regulation match found in it is attributed to whichever
       facet's entity is the NEAREST preceding capitalized word (`_nearest_preceding_facet`), not
       every facet the section happens to discuss.

    Tier 2 exists because tier 1 alone (the first fix attempt: scan a section only if its HEADING
    names the facet) wrongly flagged Germany as missing -- its regulation name lived only in a
    shared "## Introduction" section's own prose, never restated under "## Germany"'s own heading.
    Widening tier 1 to "any section MENTIONING the facet, anywhere" (the second fix attempt) then
    overcorrected: that same Introduction section discusses both Germany and Japan, so Germany's
    actual regulation mention got wrongly credited to Japan too, silencing the check entirely on
    the same real report it was built to catch. Nearest-preceding-entity attribution is what
    actually distinguishes the two entities within one shared sentence.

    A facet mentioned in NO section at all (checked via `_facet_mentions` over the whole
    document) is skipped, not flagged -- that's check_missing_query_facet's job (total omission).

    Markdown emphasis (`*`/`_`) is stripped before all entity extraction: found live that a bold
    mention ("**Germany**") is one token to a plain capitalized-word regex, and a token starting
    with `**` never matches `[A-Z][a-zA-Z]{2,}` at all -- a heading's "## Introduction" only worked
    by accident, since the space there keeps "##" and "Introduction" as separate tokens.

    Still not exhaustive: a regulation named with no capitalized word of its entity anywhere
    earlier in the same shared section (e.g. a table cell with no lead-in sentence) stays
    unattributed. Calibrate against more real reports before fully trusting this (see
    session_status/CURRENT.md's own note on this open design risk).

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

    clean_content = re.sub(r'[*_]', '', ctx.content)
    mentioned = [_facet_token_match(f, _facet_mentions(clean_content)) for f in facets]
    if not any(mentioned):
        return None

    covered = [False] * len(facets)
    for sec in split_into_heading_sections(ctx.content):
        if not sec:
            continue
        clean_sec = re.sub(r'[*_]', '', "\n".join(sec))
        heading_facets = [
            i for i, f in enumerate(facets)
            if _facet_token_match(f, _facet_mentions(re.sub(r'[*_]', '', sec[0])))
        ]
        if len(heading_facets) == 1:
            i = heading_facets[0]
            if not covered[i] and (_NAMED_REGULATION_RE.search(clean_sec) or _REGULATION_ID_RE.search(clean_sec)):
                covered[i] = True
            continue
        for pattern in (_NAMED_REGULATION_RE, _REGULATION_ID_RE):
            for m in pattern.finditer(clean_sec):
                i = _nearest_preceding_facet(clean_sec[:m.start()], facets)
                if i is not None:
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("regulation_unsupported")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("specific_figure_unsupported")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("editorializing")):
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
    gp = ctx.grounding_problem
    if not (gp and gp.startswith("uncited_claims")):
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


