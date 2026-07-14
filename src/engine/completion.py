# Completion-check verdict engine, extracted from engine/tui.py (2026-07-12).
#
# WHY THIS SHAPE: the old run_completion_check was a ~250-line if/elif chain of giant
# triple-assignment f-strings. Twice (bd307f4, and again on run 13's regulation branch) an
# inserted branch silently swallowed the next `elif` header — both bodies merged, the later
# assignment won, and the file still parsed. The checks were fine; the container was the hazard.
# Now each problem type is one function returning a Verdict (or None), walked in an ordered list:
# first verdict wins, and there are no elif headers left to swallow. Adding a check = one function
# + one list entry. test_structural_checks.py's verdict matrix pins every problem's routing.
import os
from dataclasses import dataclass
from typing import Callable, NamedTuple, Optional

import config
from agent_framework import Message
from tools import tool_quotas_ctx, get_workspace_files, get_workspace_file_content
from utils.run_state import get_fetched_urls, get_search_health
from utils.grounding import fully_ungrounded, real_grounding_problem
from engine.orchestrator import topup_quota_pool, available_sub_agents_ctx

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


def check_findings_ungrounded(ctx: Ctx) -> Optional[Verdict]:
    """findings.md (Pass 1) was previously never grounding-checked at all — only
    final_report.md was. Confirmed live: a Planner that abandons real delegation partway
    through a run can fabricate the ENTIRE Pass-1 file from memory, and Pass 2 then
    treats it as ground truth (SESSION_STATUS.md tracked item #2). Checked BEFORE the
    missing-artifact/final-report gates because fabricated findings poison everything
    downstream — a final report rewritten from fabricated findings can never become
    grounded. Uses the wholesale-fabrication gate (fully_ungrounded), not the strict
    per-URL one, so legitimately-mixed Pass-1 notes don't hard-fail a run."""
    gc_cfg = config.cfg.get("settings", {}).get("grounding_check", {})
    if not (gc_cfg.get("enabled", True) and gc_cfg.get("check_findings", True)):
        return None
    if "findings.md" not in ctx.files:
        return None
    findings_problem = fully_ungrounded(get_workspace_file_content("findings.md") or "")
    if not findings_problem:
        return None
    return Verdict(
        "findings_ungrounded",
        f"`findings.md` (Pass 1) fails the grounding check ({findings_problem}) — nothing in it traces to a source actually fetched this run. Pushing agent to rebuild it from real delegated results.",
        f"SYSTEM WARNING: your Pass-1 'findings.md' is not grounded in real research ({findings_problem}) — "
        + ("it contains no source URLs at all" if findings_problem == "no_urls" else "not one URL it cites matches anything your Searcher(s) actually fetched this run")
        + f". findings.md must be a verbatim consolidation of what your delegated Searchers/Analyzers actually returned, never written from your own memory. The fabricated file has been moved aside. Delegate real research tasks now if you haven't, then rebuild findings.md strictly from those real results — only after that, write '{ctx.req_artifact}' from it.",
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

    if prior_same == 0:
        directive = (
            "You never wrote 'findings.md'. The workflow is two passes: FIRST write findings.md "
            "as a verbatim consolidation of everything your delegated Searchers/Analyzers "
            f"actually returned (each claim with its real source URL), THEN write "
            f"'{ctx.req_artifact}' from it. You have real delegated results in your context "
            f"above — do NOT claim nothing was retrieved, and do NOT write '{ctx.req_artifact}' "
            f"directly."
        )
    else:
        directive = (
            f"'findings.md' is STILL missing after {prior_same} prior warning(s). A text "
            f"response or silence does not count — only a file that actually exists on disk "
            f"does. Do NOT claim nothing was retrieved: you have real fetched sources from this "
            f"run (see the exact URLs below if you've lost track of them)."
        )

    escalation = ""
    if prior_same >= 1:
        real_urls = get_fetched_urls()
        url_list = "\n".join(f"- {u['url']}" for u in real_urls[:20]) or "(none fetched yet)"
        escalation = (
            f" Here are the EXACT URLs actually fetched this run — write findings.md "
            f"summarizing what each one contains, using these verbatim:\n{url_list}"
        )

    return Verdict(
        "missing_findings",
        "`findings.md` (Pass 1) was never written — the two-pass discipline was skipped. Pushing agent to write it before the final report.",
        f"SYSTEM WARNING: {ctx.last_chance_prefix}{directive} Call write_workspace_file(filename='findings.md', content=...) right now.{escalation}",
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
    check_findings_ungrounded,
    check_missing_findings,
    check_missing_artifact,
]
GROUNDING_CHECKS: list[Callable[[Ctx], Optional[Verdict]]] = [
    check_claim_unsupported,
    check_no_urls,
    check_stub_source,
    check_regulation_unsupported,
    check_non_url_citation,
    check_nli_unsupported,
    check_uncited_claims,
    check_not_grounded,  # generic catch-all: fires on ANY grounding problem — keep it LAST
]

# Problems whose bad draft gets quarantined (renamed aside) before the retry, and which count as
# "the check the quarantined draft actually failed" when restoring it at the final verdict.
# run_completion_check derives its quarantine branch from this tuple (findings_ungrounded
# quarantines findings.md instead of the artifact) — one list, no second copy to forget.
_QUARANTINE_PROBLEMS = ("not_grounded", "claim_unsupported", "non_url_citation",
                        "regulation_unsupported", "stub_source", "nli_unsupported",
                        "findings_ungrounded")

# Problems fixable by rewriting `req_artifact` from the SAME findings.md, no new research needed —
# dispatched to a fresh-context Builder (+ PeerReviewer check) by run_completion_check's
# Build->Review->Fix loop instead of growing the Planner's own conversation. The complement
# (missing_findings, findings_ungrounded, not_delegated) genuinely needs more/different research,
# which only the Planner can decide and delegate, so those still fall through to the classic
# inject-into-Planner path below.
_BUILDER_FIXABLE_PROBLEMS = ("missing_artifact", "not_grounded", "claim_unsupported",
                             "non_url_citation", "regulation_unsupported", "stub_source",
                             "nli_unsupported", "uncited_claims")


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
    salvage, not a substitute for the grounding check. Returns True if a salvage write happened."""
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


def _ensure_builder_write_quota_headroom(pool: dict) -> None:
    """Build->Review->Fix can burn up to 2 `write_workspace_file` calls in a single completion-
    check retry (Builder's initial rewrite, plus one corrective Fix pass if PeerReviewer flags
    issues) — against the SAME shared cumulative pool the Planner's own `findings.md` writes draw
    from (see `build_quota_pool`'s docstring: one pool across every role, by design). The standard
    per-attempt `topup_quota_pool` (called just before this) already covers the default config
    fine — not currently starved in practice — but a config with a low `write_workspace_file`
    limit/topup would starve Builder specifically mid-cycle, degrading it to the same "narrate
    instead of write" failure the Planner used to be prone to, one level down. Rather than a
    separate reserved pool (a bigger structural change, and against the shared-pool design), this
    tops up ONLY the one tool this cycle actually needs, and only by the exact headroom it could
    need — not a blanket amount that would also quietly inflate the Planner's own budget."""
    entry = pool.get("write_workspace_file")
    if entry is None:
        return
    needed = 2  # Builder's initial rewrite + one possible corrective Fix pass
    headroom = entry["limit"] - entry["used"]
    if headroom < needed:
        entry["limit"] += (needed - headroom)


async def _dispatch_build_review_fix(dispatch_task, req_artifact: str, verdict: "Verdict", attempt: int, notify) -> None:
    """Build -> Review -> Fix, all fresh-context sub-agent dispatches, none of which touch the
    Planner's own conversation. Caller (run_completion_check) is responsible for quarantine,
    quota top-up, and run_state bookkeeping around this call — this function only runs the
    dispatch sequence. Raises on any dispatch failure so the caller can fall back to the classic
    inject-into-Planner path for this cycle rather than silently doing nothing.

    Capped at 3 dispatches total (Build, Review, optional Fix) — no unbounded nesting."""
    builder_instructions = (
        f"Rewrite '{req_artifact}' from findings.md, fixing this specific problem:\n"
        f"{verdict.inject}\n\nWrite the corrected file now via write_workspace_file."
    )
    await dispatch_task(f"BuilderFix_attempt{attempt + 1}", builder_instructions, "Builder")

    review = await dispatch_task(
        f"ReviewFix_attempt{attempt + 1}",
        f"Review '{req_artifact}' against findings.md for accuracy and coherence. "
        f"Start your response with exactly 'REVIEW: CLEAN' or 'REVIEW: ISSUES FOUND:'.",
        "PeerReviewer",
    )

    # Conservative parse: anything other than an explicit CLEAN verdict (including a missing
    # sentinel — the model didn't follow format) is treated as issues found, so a formatting slip
    # never lets an unreviewed report slip through.
    review_text = review if isinstance(review, str) else str(review)
    is_clean = "REVIEW: CLEAN" in review_text and "REVIEW: ISSUES FOUND:" not in review_text
    if is_clean:
        notify(f"**System ({attempt + 1}):** PeerReviewer found no issues with the rebuilt `{req_artifact}`.")
        return

    notify(f"**System ({attempt + 1}):** PeerReviewer flagged issues in the rebuilt `{req_artifact}` — dispatching one corrective Builder pass.")
    fix_instructions = (
        f"PeerReviewer critiqued your last draft of '{req_artifact}'. Fix every issue it raised, "
        f"using only findings.md as your source of facts, then rewrite the file:\n\n{review_text}"
    )
    await dispatch_task(f"BuilderFix_attempt{attempt + 1}_reviewed", fix_instructions, "Builder")


async def run_completion_check(query: str, current_input, run_state: "RunState", notify, last_assistant_text: str = "", dispatch_task=None):  # noqa: F821 — utils.run_state.RunState, annotation only
    """Runs the 3-tier completion check (delegated? artifact exists? really grounded?) plus the
    structural fixes: per-attempt quota top-up, artifact quarantine, run-state persistence, and
    (as a last resort) salvaging a narrated-but-never-written report instead of losing it.

    `dispatch_task`, when provided (see engine.orchestrator._run_single_task / create_local_agent's
    3-tuple return), enables the Build->Review->Fix loop for `_BUILDER_FIXABLE_PROBLEMS`: instead
    of injecting a nudge into the Planner's own `current_input` (which never shrinks across a
    run), a fresh Builder sub-agent rewrites the artifact and a fresh PeerReviewer checks the
    result, entirely outside the Planner's conversation. When `dispatch_task` is None (or the
    caller's registered sub-agents don't include both "Builder" and "PeerReviewer"), every problem
    falls back to the classic inject-into-Planner behavior unconditionally.

    Returns (should_retry: bool, new_current_input). Caller is responsible for looping while
    should_retry is True, same as before.
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

    attempt = run_state.attempt

    try:
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
        # already-exhausted approach further. check_missing_artifact's own escalating wording
        # (see its docstring) still gets one shot at each of these attempts first; this only
        # trims how many total attempts a provably-stuck pattern gets to burn.
        CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD = 3
        if problem == "missing_artifact":
            consecutive = 0
            for a in reversed(run_state.data.get("completion_check_attempts", [])):
                if a.get("problem") == "missing_artifact":
                    consecutive += 1
                else:
                    break
            if consecutive >= CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD:
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

            # Build->Review->Fix: for artifact-authoring problems, dispatch fresh-context Builder
            # (+PeerReviewer check) instead of nudging the Planner's own conversation — see
            # _dispatch_build_review_fix and run_completion_check's docstring. Defensive: requires
            # BOTH roles registered, and any dispatch failure falls back to the classic path for
            # this cycle rather than losing the retry entirely.
            if dispatch_task is not None and problem in _BUILDER_FIXABLE_PROBLEMS:
                caller_sub_agents = available_sub_agents_ctx.get()
                has_builder_pair = caller_sub_agents and any(c.name == "Builder" for c in caller_sub_agents) \
                    and any(c.name == "PeerReviewer" for c in caller_sub_agents)
                if has_builder_pair:
                    notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning} (dispatching Builder to rewrite, not the Planner)")
                    if pool is not None:
                        _ensure_builder_write_quota_headroom(pool)
                    try:
                        await _dispatch_build_review_fix(dispatch_task, req_artifact, verdict, attempt, notify)
                        run_state.save()
                        return True, current_input
                    except Exception:
                        notify(f"**System ({attempt + 1}/{max_attempts}):** Builder dispatch failed — falling back to asking the Planner directly.")

            notify(f"**System ({attempt + 1}/{max_attempts}):** {verdict.warning}")
            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
            new_inputs.append(Message("user", [{"type": "text", "text": verdict.inject}]))
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
