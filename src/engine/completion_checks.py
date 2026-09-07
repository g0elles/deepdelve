# Shared completion-check core: Verdict/Ctx (every check function's shared vocabulary) and
# _citation_format_reminder (used by several GROUNDING_CHECKS members). Split 2026-09-07
# (ponytail-audit finding, session_status/CURRENT.md's carried-forward TODO) out of what was a
# single 2098-line engine/completion_checks.py into this small shared core plus two tier-specific
# files: engine/completion_checks_structural.py (COMPLETION_CHECKS members) and
# engine/completion_checks_grounding.py (GROUNDING_CHECKS members) -- both import Ctx/Verdict from
# here. Pure move, no behavior change.
#
# engine/completion.py still imports Ctx/Verdict/_citation_format_reminder from here (as before)
# and re-exports them under `engine.completion` for test_structural_checks.py/finetune/* exactly
# as before this split -- that external import surface does not change.
from dataclasses import dataclass
from typing import NamedTuple, Optional

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

