"""Smallest thing that fails if the structural-check heuristics break.
Run: venv/Scripts/python test_structural_checks.py (no framework needed).

Thin orchestrator (2026-09-07 split, session_status/CURRENT.md carried-forward TODO): this file
used to be one single 10,701-line `main()` holding all 199 test sections directly. Kept as one
giant function since 2026-07-12's very first version, it grew alongside the engine it tests until
it became the thing this project's own ARCHITECTURE.md warns about -- too large to hold in one
head. Split into 19 topic files below, one `main()` each, called here in the EXACT original order
(each is a pure move: verified via a sorted-line-set diff against the pre-split file showing every
non-blank line identical, same order, before this split was kept). This file's own name and
"`python test_structural_checks.py`" invocation are UNCHANGED -- CLAUDE.md's own standing rule
("must run test_structural_checks.py before commit") needs no update, only the internals moved.

Grouping is topical, not chronological (the original was chronological -- each section appended
next to whenever its feature landed, which is why cross-cutting themes like "starvation" or
"grounding detail" ended up scattered across thousands of lines). A few shared test fixtures
(`_SRC`/`_STUB_SRC`/`_SOURCE_TEXT`/`_FINDINGS_OK`, `RunState`/`run_state_ctx`/`Ctx`/`Verdict`/
`run_completion_check`/etc.) get defined identically in EVERY split file's own header instead of
being hoisted into a separate shared-fixtures module -- simpler for ~20 names than a 20th file, and
each is a real importable module symbol or a 4-line literal constant block, not test logic. A
static dependency check (every name a section uses that isn't bound within its own section or the
common header, traced back to whichever earlier section actually defines it) confirmed ZERO
cross-file dependencies before this grouping was finalized -- see `session_status/CURRENT.md`'s
2026-09-07 entry for the full methodology if this needs to be redone after future changes.

File-to-topic map (in call order below):
  test_orchestrator_extraction        -- orchestrator.py extraction/helper functions
  test_grounding_gates_and_quotas     -- findings/citation gates, quota refund/ring-fence/dedup,
                                          no-progress + tool-failure-streak guards, compaction
  test_tools_web_fetch                -- tools/web.py fetch_url_to_workspace, caps, scope gates
  test_resume_and_tui_qoe             -- --resume-run, TUI/CLI parity, TUI QoE widgets
  test_completion_verdict_matrix      -- the COMPLETION_CHECKS/GROUNDING_CHECKS verdict matrix
  test_completion_structural_boundary -- boundary-condition tests for individual structural checks
  test_completion_starvation          -- _yield_to_starved_check/_capped/_apply_starvation_yield
  test_grounding_checks_core          -- cross-source contradiction, NLI, editorializing, reranker
  test_coverage_and_thin_coverage     -- coverage accounting, thin_coverage/uneven_investment/
                                          task_verification_flagged wiring
  test_report_underuses               -- check_report_underuses_findings/_evidence + exclusions
  test_missing_artifact_and_deepening -- missing_artifact escalation, iterative deepening
  test_writer_dispatch_builder        -- Builder Write->Review->Fix, cross/within-tier starvation
  test_writer_dispatch_findings       -- FindingsWriter Write->Review->Fix, force_whole_rebuild
  test_dispatch_ablation_and_salvage  -- ablation switches, final-verdict salvage, quota headroom
  test_grounding_citation_details     -- per-citation-shape grounding edge cases (largest file)
  test_salvage_and_infra              -- quarantine restore, eval scorer, run-state diagnostics
  test_findings_source_material       -- _build_findings_source_material, writer_gate_ctx
  test_routing_cache_and_misc         -- routing classifier, RAG cache, standing audits
  test_create_local_agent_characterization -- create_local_agent / _run_single_task streaming loop
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import test_orchestrator_extraction
import test_grounding_gates_and_quotas
import test_tools_web_fetch
import test_resume_and_tui_qoe
import test_completion_verdict_matrix
import test_completion_structural_boundary
import test_completion_starvation
import test_grounding_checks_core
import test_coverage_and_thin_coverage
import test_report_underuses
import test_missing_artifact_and_deepening
import test_writer_dispatch_builder
import test_writer_dispatch_findings
import test_dispatch_ablation_and_salvage
import test_grounding_citation_details
import test_salvage_and_infra
import test_findings_source_material
import test_routing_cache_and_misc
import test_create_local_agent_characterization


def main():
    test_orchestrator_extraction.main()
    test_grounding_gates_and_quotas.main()
    test_tools_web_fetch.main()
    test_resume_and_tui_qoe.main()
    test_completion_verdict_matrix.main()
    test_completion_structural_boundary.main()
    test_completion_starvation.main()
    test_grounding_checks_core.main()
    test_coverage_and_thin_coverage.main()
    test_report_underuses.main()
    test_missing_artifact_and_deepening.main()
    test_writer_dispatch_builder.main()
    test_writer_dispatch_findings.main()
    test_dispatch_ablation_and_salvage.main()
    test_grounding_citation_details.main()
    test_salvage_and_infra.main()
    test_findings_source_material.main()
    test_routing_cache_and_misc.main()
    test_create_local_agent_characterization.main()
    print("All structural-check assertions passed.")


if __name__ == "__main__":
    main()
