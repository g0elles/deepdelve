import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.run_state import record_fetched_url, reset_fetched_urls

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import asyncio as _asyncio
import config as _config
import contextvars
import tempfile

from utils.run_state import RunState, run_state_ctx

# Literal test fixtures reused across many topic files below (constants, not code) --
# hoisting these verbatim into every file's header is simpler than a separate shared-fixtures
# module for 4 short constants.
_SRC = "https://gov.example.co/page"
_STUB_SRC = "https://news.example.co/paywalled-article"
_SOURCE_TEXT = (
    "Source-URL: " + _SRC + "\n\n"
    "El pais avanza de forma sostenida segun cifras oficiales del gobierno, con datos "
    "verificados por organismos independientes."
)
_FINDINGS_OK = f"- hallado ({_SRC})"

def main():
    # --- verdict matrix: one row per completion-check problem type, asserting the RECORDED
    # problem name AND a phrase distinctive to that branch's corrective nudge. This is the pin
    # against the swallowed-elif bug class (bd307f4, run 13) that motivated engine/completion.py:
    # a verdict carrying the right detail under the wrong label/nudge fails its row instantly.
    # Live-case rows: missing_findings (runs 10/11), regulation_unsupported (runs 12/13). ---
    from engine.tui import run_completion_check

    _SRC = "https://gov.example.co/page"
    _STUB_SRC = "https://news.example.co/paywalled-article"
    _SOURCE_TEXT = ("Source-URL: " + _SRC + "\n\n"
                    + "Estrategia nacional de seguridad digital para infraestructura y sectores productivos. " * 3)
    _FINDINGS_OK = f"- hallado ({_SRC})"

    matrix = [
        # (row, delegated, workspace files, expected recorded problem, distinctive nudge phrase)
        ("not_delegated", False, {"final_report.md": f"- x [g]({_SRC})"},
         "not_delegated", "No `delegate_tasks` call was ever made"),
        # check_requested_count_shortfall (2026-08-28, gemma4:e4b bake-off live incident): query
        # explicitly asks for "4 to 6" distinct items, but only 2 distinct depth==1 tasks were ever
        # delegated (both with real, non-null-summary sources, so thin_coverage's own ratio is 1.0
        # and stays silent -- this check is what catches the plan itself being too narrow, one
        # level upstream of "did what was planned succeed"). Fires before findings.md even needs to
        # exist, like check_task_verification_flagged below.
        ("requested_count_shortfall", True, {},
         "requested_count_shortfall", "research tasks were delegated",
         "Identify 4 to 6 real, distinct B2B niches with evidence for each.", [], [
             {"task_name": "niche_healthcare", "source_url": "https://gov.example.co/health",
              "summary": "real content, no warning marker.", "depth": 1},
             {"task_name": "niche_manufacturing", "source_url": "https://gov.example.co/mfg",
              "summary": "real content, no warning marker.", "depth": 1},
         ]),
        # check_missing_query_facet (2026-08-29): query unambiguously enumerates two facets
        # (Lisbon, Mexico City) via "compare X and Y" -- only a Lisbon task was ever dispatched
        # (real, non-null-summary source, so check_not_delegated/check_requested_count_shortfall
        # both stay silent), so Mexico City is caught as never mentioned by any delegated task.
        ("missing_query_facet", True, {}, "missing_query_facet", "no delegated task mentions",
         "Compare rents in Lisbon and Mexico City.", [], [
             {"task_name": "lisbon_rent", "source_url": "https://gov.example.co/lisbon-rent",
              "summary": "real content, no warning marker.", "depth": 1},
         ],
         {"dispatched_tasks": [
             {"task_name": "lisbon_rent", "instructions": "Research rent prices in Lisbon."},
         ]}),
        # check_task_verification_flagged (2026-07-26, VERIMAP-inspired): fires before findings.md
        # even needs to exist -- reads the per-task ledger _update_task_verification maintains from
        # run_state.data["findings"] alone. 'task_kept' has a real citable finding (verified);
        # 'task_flagged' has only a finding excluded by _is_citable_finding (fabricated citation).
        ("task_verification_flagged", True, {}, "task_verification_flagged",
         "have only fabricated/unusable sources",
         "", [], [
             {"task_name": "task_kept", "source_url": "https://gov.example.co/real-page",
              "summary": "real content, no warning marker.", "depth": 1},
             {"task_name": "task_flagged", "source_url": "https://gov.example.co/bad-page",
              "summary": ("N-BEATS improved forecast accuracy by 23%.\n\n"
                           "[SYSTEM VERIFICATION WARNING: this summary cites a URL that does not "
                           "match anything actually fetched this run "
                           "(claim_unsupported:https://gov.example.co/bad-page).]"),
              "depth": 1},
         ]),
        ("findings_ungrounded", True, {"findings.md": "- todo de memoria, sin fuente alguna"},
         "findings_ungrounded", "fails the grounding check"),
        # Live case 2026-07-19: findings.md 40% fabricated (6/15 entries) passed the wholesale
        # fully_ungrounded gate cleanly (some real entries existed), then poisoned Builder's
        # rewrite. partially_ungrounded's per-entry-heading check catches the mix even though at
        # least one entry (_SRC) is genuinely real.
        ("findings_partially_ungrounded", True, {
            "findings.md": (f"### [Real Finding]({_SRC}) [PRIMARY]\n- Key Findings: real.\n\n"
                             "### [Fake Finding](https://never-fetched.example.com/fake) [SECONDARY]\n"
                             "- Key Findings: invented.")},
         "findings_ungrounded", "unverified_entry_sources"),
        ("missing_findings", True, {"final_report.md": f"- x [g]({_SRC})"},
         "missing_findings", "was never written — the two-pass discipline was skipped"),
        # check_stale_findings (2026-07-24): findings.md exists and is grounded (no other check
        # ahead of it fires), but findings_written_citable_count (set at the moment findings.md
        # was actually written) is behind the one real, citable finding recorded since -- the
        # exact gap that let a stale findings.md through undetected on a real --resume-run.
        ("stale_findings", True, {"findings.md": _FINDINGS_OK},
         "stale_findings", "delegated since it was last written", "", [],
         [{"task_name": "nueva_tarea", "source_url": "https://gov.example.co/new-page",
           "summary": "Nuevo hallazgo real disponible tras la primera escritura."}],
         {"findings_written_citable_count": 0}),
        # check_findings_underuses_evidence (2026-07-26): findings.md exists and cites a real
        # source (_SRC, from 'task_kept') -- passes check_missing_findings and check_stale_findings
        # cleanly -- but a SECOND real, delegated top-level task ('task_dropped') has a real fetched
        # source that never appears anywhere in findings.md. Live case: a balanced 2-facet run
        # (green tea + Roman Empire, both genuinely "covered") produced a findings.md covering only
        # one topic, with nothing catching the other topic's total disappearance.
        ("findings_underuses_evidence", True, {"findings.md": _FINDINGS_OK},
         "findings_underuses_evidence", "despite real research results existing for them",
         "", [], [
             {"task_name": "task_kept", "source_url": _SRC, "summary": "hallado real.", "depth": 1},
             {"task_name": "task_dropped", "source_url": "https://gov.example.co/dropped-page",
              "summary": "Otro hallazgo real que nunca llego a findings.md.", "depth": 1},
         ]),
        ("missing_artifact", True, {"findings.md": _FINDINGS_OK},
         "missing_artifact", "is missing from the workspace"),
        ("claim_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- Colombia exporto USD 3.5 mil millones en 2024 [gov]({_SRC})"},
         "claim_unsupported", "don't appear to come from that source's actual content"),
        ("no_urls", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "# Informe\nSin enlaces aqui."},
         "not_grounded", "zero hyperlinked sources"),
        ("regulation_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"| Ley 1906 de 2021 | [gov]({_SRC}) |"},
         "regulation_unsupported", "never mentions that regulation's number"),
        # Live case 2026-08-17 (ablation smoke-test): a real dollar figure was misattributed to the
        # WRONG one of two genuinely-fetched sources on the same narrow topic — _SRC's content
        # never mentions '53' anywhere.
        ("specific_figure_unsupported", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- Application fee is $53. [gov]({_SRC})"},
         "specific_figure_unsupported", "never mentions it"),
        # Live case 2026-08-17: an edit_workspace_file call retyped an existing section's content
        # while appending a new subsection next to it, producing two near-identical sections.
        ("duplicate_report_sections", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              f"### Mexico City\n- Average rent is 22,000-38,000 pesos per month. [g]({_SRC})\n\n"
              f"### Mexico City - Central Districts\n- Average monthly rent is 22,000-38,000 pesos "
              f"per month. [g]({_SRC})\n")},
         "duplicate_report_sections", "near-duplicate sections"),
        # check_missing_specific_item_per_facet (2026-08-30 live incident): query enumerates two
        # facets (Germany, Japan) AND explicitly requires "citing at least one specific regulation
        # for each" -- the report is fully grounded and both entities have their own heading-
        # delimited section, but only Germany's section names an actual regulation ("Renewable
        # Energy Sources Act (EEG)"); Japan's cites only a renewable-share target, no regulation.
        ("missing_specific_item_per_facet", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Germany's Renewable Energy Policy\n"
              "- Germany's Renewable Energy Sources Act (EEG) mandates increased funding for "
              "renewable energy generation nationwide. [gov](https://gov.example.de/eeg-act)\n\n"
              "## Japan's Renewable Energy Policy\n"
              "- Japan targets a 40 to 50 percent renewable energy share by 2040. "
              "[gov](https://gov.example.jp/2040-target)\n")},
         "missing_specific_item_per_facet", "requires a specific regulation per entity",
         "Compare Germany and Japan's renewable energy policy, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act", "sources/de_eeg.md",
              "Source-URL: https://gov.example.de/eeg-act\n\nGermany's Renewable Energy Sources "
              "Act (EEG) mandates increased funding for renewable energy generation nationwide."),
             ("https://gov.example.jp/2040-target", "sources/jp_target.md",
              "Source-URL: https://gov.example.jp/2040-target\n\nJapan targets a 40 to 50 percent "
              "renewable energy share by 2040, up from 22.9 percent in 2023."),
         ]),
        # check_missing_specific_item_per_facet's SHARED-section tier (2026-08-30, second
        # live-calibration fix): mirrors the actual incident's real shape -- a "## Introduction"
        # section mentions BOTH Germany and Japan (heading names neither, so it's NOT a dedicated
        # section) and states Germany's regulation there, never restated under Germany's own
        # heading. Pins two things at once: (1) a regulation living only in a shared section must
        # still be found (Germany must NOT be wrongly flagged), and (2) it must be attributed to
        # the entity it's actually adjacent to, not credited to Japan just because the same shared
        # section also discusses Japan (a real false-negative this check's second fix attempt
        # produced before nearest-preceding-entity attribution was added).
        ("missing_specific_item_per_facet_shared_section", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Introduction\n"
              "- **Germany**: Germany's Renewable Energy Sources Act (EEG) mandates increased "
              "funding for renewable energy generation nationwide. [gov](https://gov.example.de/eeg-act)\n"
              "- **Japan**: Japan's FIT policy guarantees long-term purchase of renewable "
              "electricity. [gov](https://gov.example.jp/2040-target)\n\n"
              "## Germany's Renewable Energy Policy\n"
              "- Germany continues to expand its renewable capacity under continued EEG support. "
              "[gov](https://gov.example.de/eeg-act)\n\n"
              "## Japan's Renewable Energy Policy\n"
              "- Japan targets a 40 to 50 percent renewable energy share by 2040. "
              "[gov](https://gov.example.jp/2040-target)\n")},
         "missing_specific_item_per_facet", "requires a specific regulation per entity",
         "Compare Germany and Japan's renewable energy policy, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act", "sources/de_eeg.md",
              "Source-URL: https://gov.example.de/eeg-act\n\nGermany's Renewable Energy Sources "
              "Act (EEG) mandates increased funding for renewable energy generation nationwide."),
             ("https://gov.example.jp/2040-target", "sources/jp_target.md",
              "Source-URL: https://gov.example.jp/2040-target\n\nJapan targets a 40 to 50 percent "
              "renewable energy share by 2040, up from 22.9 percent in 2023."),
         ]),
        # check_missing_specific_item_per_facet's match-embedded-entity fix (2026-08-30, third
        # live-calibration fix, found on a FOURTH real report): a regulation's own name often
        # carries the country as a leading adjective ("German Renewable Energy Sources Act") --
        # its markdown-link bullet has NO entity at all in its PRECEDING text, so
        # nearest-preceding-word attribution alone fell through past it to "Japan" from an
        # unrelated earlier sentence in the same shared section, wrongly crediting Japan again, a
        # different way than the previous row's bug. Fixed by checking the match's own text first.
        ("missing_specific_item_per_facet_match_embedded_entity", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Introduction\n"
              "This report covers renewable energy policy in Germany and Japan.\n\n"
              "- **[German Renewable Energy Sources Act](https://gov.example.de/eeg-act2)**\n\n"
              "## Japan's Renewable Energy Policy\n"
              "- Japan targets a 40 to 50 percent renewable energy share by 2040. "
              "[gov](https://gov.example.jp/2040-target2)\n")},
         "missing_specific_item_per_facet", "requires a specific regulation per entity",
         "Compare Germany and Japan's renewable energy policy, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act2", "sources/de_eeg2.md",
              "Source-URL: https://gov.example.de/eeg-act2\n\nGermany's Renewable Energy Sources "
              "Act (EEG) mandates increased funding for renewable energy generation nationwide."),
             ("https://gov.example.jp/2040-target2", "sources/jp_target2.md",
              "Source-URL: https://gov.example.jp/2040-target2\n\nJapan targets a 40 to 50 percent "
              "renewable energy share by 2040, up from 22.9 percent in 2023."),
         ]),
        # check_missing_specific_item_per_facet's law-FIRM-name false positive (2026-08-30,
        # fourth live-calibration fix, found on a FIFTH real report): _NAMED_REGULATION_RE's bare
        # "...Law\b" alternative matched a cited source's own title, "Borderless Business Law
        # Office Japan 2026" -> "Borderless Business Law", as if a law FIRM's name were a named
        # regulation -- wrongly satisfying Japan's requirement even though the report never named
        # an actual regulation for Japan. Fixed with a negative lookahead excluding "Law" followed
        # by a firm/office suffix (Office/Firm/Group/School/LLP/LLC/Partners/Associates).
        ("missing_specific_item_per_facet_law_firm_name", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Germany's Renewable Energy Policy\n"
              "- Germany's Renewable Energy Sources Act (EEG) mandates increased funding for "
              "renewable energy generation nationwide. [gov](https://gov.example.de/eeg-act3)\n\n"
              "## Japan's Renewable Energy Policy\n"
              "- Japan will exclude large ground-mounted solar from its FIT/FIP system starting "
              "fiscal 2027. [Borderless Business Law Office Japan 2026]"
              "(https://gov.example.jp/2040-target3)\n")},
         "missing_specific_item_per_facet", "requires a specific regulation per entity",
         "Compare Germany and Japan's renewable energy policy, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act3", "sources/de_eeg3.md",
              "Source-URL: https://gov.example.de/eeg-act3\n\nGermany's Renewable Energy Sources "
              "Act (EEG) mandates increased funding for renewable energy generation nationwide."),
             ("https://gov.example.jp/2040-target3", "sources/jp_target3.md",
              "Source-URL: https://gov.example.jp/2040-target3\n\nJapan will exclude large "
              "ground-mounted solar from its FIT/FIP system starting fiscal 2027."),
         ]),
        # check_missing_specific_item_per_facet's nested-subsection false positive (2026-09-07,
        # FIFTH live-calibration fix, first real incident from a genuinely DIFFERENT topic --
        # Canada/South Korea AI-safety regulation, not the original Germany/Japan renewable-energy
        # report): a dedicated "## Canada" heading with named laws nested one level deeper under
        # their own "### Safe Social Media Act (Bill C-34)"/"### Artificial Intelligence and Data
        # Act (AIDA)" subsection headings -- neither tier matched before the fix (parent heading
        # names Canada but its own pre-subsection body has no regulation; each child heading names
        # a regulation but no facet, so it fell into the shared tier with nothing in its own short
        # body to anchor proximity to). Must be a CLEAN PASS: both real named laws are right there.
        ("missing_specific_item_per_facet_nested_subsection", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## South Korea\n"
              "- The Framework Act on the Development of Artificial Intelligence and Establishment "
              "of Trust governs AI safety obligations. [gov](https://gov.example.kr/framework-act)\n\n"
              "## Canada\n"
              "Canada relies on several federal bills to address AI safety.\n\n"
              "### Safe Social Media Act (Bill C-34)\n"
              "- Requires AI chatbot services to disclose their nature and provide user controls. "
              "[gov](https://gov.example.ca/c34)\n\n"
              "### Artificial Intelligence and Data Act (AIDA)\n"
              "- Proposed in 2022 as a comprehensive AI statute, suspended in January 2025. "
              "[gov](https://gov.example.ca/aida)\n")},
         None, None,
         "Compare Canada and South Korea's approach to AI safety regulation, citing at least one "
         "specific law for each country.", [
             ("https://gov.example.kr/framework-act", "sources/kr_framework.md",
              "Source-URL: https://gov.example.kr/framework-act\n\nThe Framework Act on the "
              "Development of Artificial Intelligence and Establishment of Trust governs AI safety "
              "obligations."),
             ("https://gov.example.ca/c34", "sources/ca_c34.md",
              "Source-URL: https://gov.example.ca/c34\n\nRequires AI chatbot services to disclose "
              "their nature and provide user controls."),
             ("https://gov.example.ca/aida", "sources/ca_aida.md",
              "Source-URL: https://gov.example.ca/aida\n\nProposed in 2022 as a comprehensive AI "
              "statute, suspended in January 2025."),
         ]),
        # check_missing_specific_item_per_facet's entity-free-section fix (2026-09-08, live-repro
        # of the docstring's own prior "not exhaustive" note, SIXTH fix, not yet from a real
        # report): both country names appear once in "## Introduction", then a LATER
        # "## Regulations Table" section names both regulations in a bare two-column table with
        # no per-row country repetition at all -- nothing in that section to attribute either
        # match to under the old logic. Must be a CLEAN PASS: both real named laws are right there.
        ("missing_specific_item_per_facet_entity_free_table", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Introduction\n"
              "This report compares renewable energy law in Germany and Japan.\n\n"
              "## Regulations Table\n"
              "| Regulation | Source |\n"
              "|---|---|\n"
              "| Renewable Energy Sources Act | [gov](https://gov.example.de/eeg-act4) |\n"
              "| Feed-in Tariff Act | [gov](https://gov.example.jp/2040-target4) |\n")},
         None, None,
         "Compare renewable energy laws in Germany and Japan, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act4", "sources/de_eeg4.md",
              "Source-URL: https://gov.example.de/eeg-act4\n\nGermany's Renewable Energy Sources "
              "Act (EEG) mandates increased funding for renewable energy generation nationwide."),
             ("https://gov.example.jp/2040-target4", "sources/jp_target4.md",
              "Source-URL: https://gov.example.jp/2040-target4\n\nJapan's Feed-in Tariff Act "
              "guarantees long-term purchase of renewable electricity."),
         ]),
        # check_missing_specific_item_per_facet's acronym-initials fix (2026-09-09, SEVENTH fix,
        # live incident): a real Germany/Japan run named Germany's regulation as "Erneuerbare-
        # Energien-Gesetz (EEG)" (with real Unicode dashes, U+2013/U+2011, exactly as the live
        # model output used them) directly under its own dedicated heading -- neither
        # _NAMED_REGULATION_RE nor _REGULATION_ID_RE recognizes German "Gesetz", so the section
        # matched nothing even though the entity plainly named a real law right there. Must be a
        # CLEAN PASS: pins both the language-agnostic acronym-initials match AND that the
        # Unicode em-dash between "Germany" and the regulation name doesn't pollute the initials
        # count with "Germany" itself (a real risk this fix's own dash-normalization handles).
        ("missing_specific_item_per_facet_foreign_language_acronym", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (
              "## Germany – Erneuerbare‑Energien‑Gesetz (EEG)\n"
              "- Germany's Renewable Energy legal framework, the EEG, mandates increased funding "
              "for renewable energy generation nationwide. [gov](https://gov.example.de/eeg-act5)\n\n"
              "## Japan's Renewable Energy Policy\n"
              "- Japan's Feed-in Tariff Act guarantees long-term purchase of renewable "
              "electricity. [gov](https://gov.example.jp/2040-target5)\n")},
         None, None,
         "Compare renewable energy laws in Germany and Japan, citing at least one specific "
         "regulation for each.", [
             ("https://gov.example.de/eeg-act5", "sources/de_eeg5.md",
              "Source-URL: https://gov.example.de/eeg-act5\n\nGermany's Renewable Energy legal "
              "framework (known as the EEG) mandates increased funding for renewable energy "
              "generation nationwide."),
             ("https://gov.example.jp/2040-target5", "sources/jp_target5.md",
              "Source-URL: https://gov.example.jp/2040-target5\n\nJapan's Feed-in Tariff Act "
              "guarantees long-term purchase of renewable electricity."),
         ]),
        # Live case 2026-07-24: a report quoted a plausible-sounding sentence and attributed it to
        # a real, fetched source -- the underlying claim can be true and traceable while the exact
        # wording still never appears there, which content_level_check's term-overlap check alone
        # cannot catch (see utils/grounding.py::find_paraphrased_quotes).
        ("quote_paraphrased", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f'- dato "esto es una cita inventada que jamas aparece en la fuente real" [gov]({_SRC})'},
         "quote_paraphrased", "doesn't match its cited source's actual text"),
        ("non_url_citation", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato uno [gov]({_SRC})\n- **Fuente:** Ministerio de Salud, informe interno"},
         "non_url_citation", "isn't a real URL"),
        # Live case run 14: citation to a really-fetched URL whose fetch was a 200 soft-404
        # (paywall shell) — hollow even though the URL gate sees a real fetch.
        ("stub_source", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato [news]({_STUB_SRC})"},
         "stub_source", "paywall/not-found stub"),
        # Live case run 14 (format half): claims as a figure table + detached Source URLs list —
        # every line-scoped check passes vacuously; nothing ties any figure to any source.
        ("uncited_claims", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": ("| Sector | Valor estimado |\n"
                              "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                              "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                              "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                              f"\n### Source URLs\n- {_SRC}\n")},
         "uncited_claims", "carry no citation of their own"),
        ("not_grounded", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "- x [g](https://never-fetched.example.com/y)"},
         "not_grounded", "was never actually fetched this run"),
        # Live case (ROADMAP "Findings from live testing"): delegate_tasks already skips
        # DISPATCHING a task on an explicitly-excluded topic, but nothing previously stopped that
        # topic showing up as its own section in the final report anyway — confirmed live twice.
        # Heading-scoped: the excluded topic ("agritech") appears as its own "## Sector Agritech"
        # section, not just mentioned in passing prose.
        ("excluded_topic_present", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})\n\n"
                               f"## Sector Agritech\n- el sector agritech crecio de forma notable segun analistas [gov]({_SRC})\n")},
         "excluded_topic_present", "explicitly excluded",
         "Do a market research of Colombia, excluding Agritech."),
        # Live-motivated case (ROADMAP Phase 2, FEVER-style): the report's own citation genuinely
        # supports its claim (12%, from fintech_a), but a DIFFERENT fetched source (fintech_b,
        # never cited on this line) reports a conflicting figure (18%) for the SAME subject, and
        # the report never surfaces that disagreement anywhere.
        ("cross_source_contradiction", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": "- Sector Fintech grew 12% in 2024 [gov](https://gov.example.co/fintech-a)"},
         "cross_source_contradiction", "a DIFFERENT fetched source",
         "", [
             ("https://gov.example.co/fintech-a", "sources/fintech_a.md",
              "Source-URL: https://gov.example.co/fintech-a\n\nSector Fintech grew 12% in 2024 according to official figures."),
             ("https://gov.example.co/fintech-b", "sources/fintech_b.md",
              "Source-URL: https://gov.example.co/fintech-b\n\nSector Fintech grew 18% in 2024 according to a different analysis."),
         ]),
        # Clean pass: grounded findings, report cites the fetched source, no checkable claim
        # contradicting it -> no problem recorded, no retry.
        ("clean_pass", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})"},
         None, None),
        # Propagation-aware check (2026-07-22, PING taxonomy): run_state.data["findings"] carries
        # a task-name-fallback (uncited) sibling with real detailed content, and a citable sibling
        # (real URL) whose summary is near-identical -- the report cites the real URL with content
        # that textually matches its own fetched source (so no earlier check fires first), but the
        # propagation check flags it because the SAME content also exists under an uncited sibling
        # for the same task_name, suspicious regardless of which one has "more" detail.
        ("propagated_ungrounded", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": (f"- el pais avanza de forma sostenida segun cifras oficiales [gov]({_SRC})\n"
                               "- The Temporal Fusion Transformer improved forecast accuracy by 23% "
                               "over classical baselines in a 2024 benchmark study of Time Series "
                               "Models. [source](https://forecastio.ai/other)")},
         "propagated_ungrounded", "without independent verification", "", [
             ("https://forecastio.ai/other", "sources/other.md",
              "Source-URL: https://forecastio.ai/other\n\nThe Temporal Fusion Transformer improved "
              "forecast accuracy by 23% over classical baselines in a 2024 benchmark study of Time "
              "Series Models."),
         ], [
             {"task_name": "background_heuristics", "source_url": "background_heuristics",
              "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                         "classical baselines in a 2024 benchmark study of Time Series Models."},
             {"task_name": "background_heuristics", "source_url": "https://forecastio.ai/other",
              "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                         "classical baselines in a 2024 benchmark study of Time Series Models."},
         ]),
        # check_academic_citation_style_abandoned (2026-08-25 live incident, see
        # session_status/CURRENT.md): report_style is "academic" but the draft cites a real,
        # fetched URL using a plain bracket-link citation and contains NO `(Author, Year)`
        # citation anywhere -- the report silently reverted to standard style under pressure,
        # which every URL-grounding check (including academic_citation_existence_problem itself)
        # would otherwise pass trivially since there's nothing academic-shaped left to flag.
        ("report_style_violation", True, {"findings.md": _FINDINGS_OK,
          "final_report.md": f"- dato uno [gov]({_SRC})"},
         "report_style_violation", "silently reverted to standard-style URL links",
         "", [], [], {}, "academic"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        for _row_name, _delegated, _files, _expected, _phrase, *_rest in matrix:
            _query = _rest[0] if _rest else ""
            _extra_fetches = _rest[1] if len(_rest) > 1 else []
            _extra_findings = _rest[2] if len(_rest) > 2 else []
            _extra_run_state_data = _rest[3] if len(_rest) > 3 else {}
            _report_style = _rest[4] if len(_rest) > 4 else "standard"

            def _matrix_row():
                from tools.fs import _IN_MEMORY_FS
                from tools.core import tool_quotas_ctx as q_ctx
                _orig_ws3 = _config.cfg.get("settings", {}).get("workspace")
                _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
                # This matrix isn't testing NLI wiring (that's _nli_verify_scenario below, with a
                # mocked model) -- without disabling it here, any row whose content_level_check
                # passes would fall through to a REAL (unmocked) nli_unsupported_problem call,
                # silently loading the actual HuggingFace model and making this "fast structural
                # suite" depend on network access. Confirmed live: exactly this happened before
                # this line was added.
                _orig_gc3 = _config.cfg.get("settings", {}).get("grounding_check")
                _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
                _orig_style3 = _config.cfg.get("settings", {}).get("report_style")
                _config.cfg["settings"]["report_style"] = _report_style
                saved_fs = dict(_IN_MEMORY_FS)
                try:
                    _IN_MEMORY_FS.clear()
                    reset_fetched_urls()
                    record_fetched_url(_SRC, filename="sources/page.md")
                    _IN_MEMORY_FS["sources/page.md"] = _SOURCE_TEXT
                    # A stub fetch is on record in EVERY row (rows that don't cite it must not
                    # trip over its mere existence); only the stub_source row cites it.
                    record_fetched_url(_STUB_SRC, filename="sources/stub.md", stub="paywall marker")
                    _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: " + _STUB_SRC + "\n\nSUSCRÍBETE"
                    for _url, _fn, _content in _extra_fetches:
                        record_fetched_url(_url, filename=_fn)
                        _IN_MEMORY_FS[_fn] = _content
                    _IN_MEMORY_FS.update(_files)
                    q_ctx.set({"delegate_tasks": {"used": 1 if _delegated else 0, "limit": 5}})
                    rs = RunState(tmpdir)
                    rs.set_query(_query)
                    rs.data.update(_extra_run_state_data)
                    run_state_ctx.set(rs)
                    for _finding_kwargs in _extra_findings:
                        rs.add_finding(**_finding_kwargs)
                    msgs = []
                    should_retry, _ = _asyncio.run(run_completion_check(
                        query="q", current_input="q", run_state=rs, notify=msgs.append))
                    recorded = rs.data["completion_check_attempts"][-1]["problem"]
                    assert recorded == _expected, (_row_name, recorded, msgs)
                    assert should_retry == (_expected is not None), (_row_name, should_retry, msgs)
                    if _phrase:
                        assert _phrase in msgs[-1], (_row_name, _phrase, msgs)
                finally:
                    _IN_MEMORY_FS.clear()
                    _IN_MEMORY_FS.update(saved_fs)
                    reset_fetched_urls()
                    if _orig_ws3 is None:
                        _config.cfg["settings"].pop("workspace", None)
                    else:
                        _config.cfg["settings"]["workspace"] = _orig_ws3
                    if _orig_gc3 is None:
                        _config.cfg["settings"].pop("grounding_check", None)
                    else:
                        _config.cfg["settings"]["grounding_check"] = _orig_gc3
                    if _orig_style3 is None:
                        _config.cfg["settings"].pop("report_style", None)
                    else:
                        _config.cfg["settings"]["report_style"] = _orig_style3

            contextvars.copy_context().run(_matrix_row)



if __name__ == "__main__":
    main()
    print("test_completion_verdict_matrix OK")
