import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from engine.orchestrator import (
    _extract_excluded_topics, _lacks_concrete_subject, _extract_follow_up_directions,
    _strip_follow_up_directions,
    _ring_fenced_deadline, _looks_like_renamed_task, _extract_required_facets,
    _extract_required_item_type,
)
from engine.completion import (
    _CUTOFF_ONLY_SUMMARY_RE, _reorder_findings_for_position_bias, _find_propagated_bad_content,
    _is_citable_finding, _build_findings_source_material,
)
from utils.run_state import record_fetched_url, reset_fetched_urls

# noqa: F401 -- common test-infra names available to every split file below, regardless of
# whether a given file's own retained sections happen to use all of them (ruff prunes genuinely
# unused ones per file). Split 2026-09-07 out of the former single 10,701-line
# test_structural_checks.py (session_status/CURRENT.md carried-forward TODO) -- see
# test_structural_checks.py's own new header for the full split rationale and the file-to-topic
# map. Pure move: every assertion below is byte-identical to its prior body, just regrouped by
# topic into its own main(), all still called in original order from the new thin
# test_structural_checks.py orchestrator.
import contextvars
import tempfile

from engine.completion import (
    find_duplicate_report_sections,
)
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
    # --- excluded-topic extraction (live case: 4 excluded sectors researched anyway) ---
    topics = _extract_excluded_topics(
        "Do a market research of neglected markets in Colombia, excluding Agritech, "
        "HealthTech, EdTech and VR/AR-Education sectors."
    )
    assert "agritech" in topics and "healthtech" in topics and "edtech" in topics, topics
    assert any("vr/ar-education" in t for t in topics), topics
    assert _extract_excluded_topics("How to avoid mosquito bites in the tropics") == set()
    assert _extract_excluded_topics("Compare React and Vue") == set()

    # --- required-facet extraction (2026-08-29, feeds check_missing_query_facet): conservative,
    # cue-phrase-gated -- must fire on an unambiguous enumeration, stay silent on incidental
    # co-occurring proper nouns or a single-subject "compare ... to" phrasing. ---
    assert len(_extract_required_facets("Compare Lisbon and Mexico City rent prices.")) == 2
    assert len(_extract_required_facets("Lisbon vs Mexico City rent")) == 2
    assert len(_extract_required_facets("For each of Brazil, Colombia, and Peru, find GDP.")) == 3
    assert len(_extract_required_facets("Both Python and Rust have strong async support.")) == 2
    assert len(_extract_required_facets(
        "Compare the vector search capabilities of Elasticsearch and PostgreSQL's pgvector "
        "extension.")) == 2
    assert _extract_required_facets(
        "Research trends in tech hubs like Lisbon, Berlin, and Mexico City.") == [], (
        "incidental co-occurring proper nouns with no cue phrase must never be treated as "
        "required facets")
    assert _extract_required_facets(
        "How does inflation in Colombia compare to its own historical CPI data?") == [], (
        "a single-subject 'compare ... to' phrasing must not fabricate a second facet")
    assert _extract_required_facets("Who created the Python programming language?") == []

    # --- required-item-type extraction (2026-08-30, feeds check_missing_specific_item_per_facet):
    # narrow cue-phrase discipline like the facet extraction above -- must fire on the explicit
    # phrasing, stay silent otherwise. ---
    assert _extract_required_item_type(
        "Compare Germany and Japan, citing at least one specific regulation for each.") == "regulation"
    assert _extract_required_item_type(
        "Compare Germany and Japan, with a specific law for each.") == "law"
    assert _extract_required_item_type("Compare Germany and Japan renewable policy.") is None
    assert _extract_required_item_type("") is None
    assert _extract_required_item_type(None) is None

    # --- unresolved-referent detection (live case: 'its' resolved to Microsoft, not Python) ---
    assert _lacks_concrete_subject("Summarize its headline feature.")
    assert not _lacks_concrete_subject("Summarize Python 3.14's headline feature.")
    assert not _lacks_concrete_subject("Find the current stable version of Python.")
    assert not _lacks_concrete_subject("Find studies about coffee and how it affects sleep in Colombia.")
    # Long instructions are never flagged, whatever pronouns they use.
    assert not _lacks_concrete_subject("evaluate it against " + "criteria " * 30)

    # --- renamed-task-on-redispatch detection (2026-07-24 Planner redelegation-loop root cause:
    # 'background_heuristic_algorithms' -> '_refined' -> '_final', same angle, new name each time) ---
    prior = [{"task_name": "background_heuristic_algorithms",
              "instructions": "Research the top 5 heuristic algorithms used for retail sales forecasting."}]
    assert _looks_like_renamed_task(
        "background_heuristic_algorithms_refined",
        "Research the top 5 heuristic algorithms used for retail sales demand forecasting.",
        prior,
    ) == "background_heuristic_algorithms"
    # A genuinely different task on unrelated instructions must not be flagged.
    assert _looks_like_renamed_task(
        "colombia_holiday_spending",
        "Find data on Colombian holiday consumer spending culture.",
        prior,
    ) is None
    # Same task_name reused verbatim is the expected, legitimate retry shape — must not double-nudge.
    assert _looks_like_renamed_task(
        "background_heuristic_algorithms",
        "Research the top 5 heuristic algorithms used for retail sales forecasting, try again.",
        prior,
    ) is None
    # But same task_name AND byte-identical instructions is NOT a legitimate continuation -- it's
    # the same task dispatched twice verbatim (2026-08-29 live incident: Hermes-4-14B emitted two
    # tool_calls in the SAME turn, each calling delegate_tasks with 100%-identical task_name and
    # instructions; the old unconditional same-name carve-out let this straight through).
    assert _looks_like_renamed_task(
        "background_heuristic_algorithms",
        "Research the top 5 heuristic algorithms used for retail sales forecasting.",
        prior,
    ) == "background_heuristic_algorithms", (
        "a same-name dispatch with essentially identical instructions must be caught as a "
        "duplicate, not waved through as a legitimate continuation")

    # Entity-mismatch override (2026-08-16 live incident): two genuinely independent facets from a
    # multi-city comparison query, dispatched under DIFFERENT task_names from the start (never a
    # rename), share a parallel template differing only in city/neighborhood names -- raw difflib
    # ratio alone scores 0.89 (well over 0.6) and previously silently "superseded" Mexico City's
    # rent facet the moment Lisbon's got verified, permanently dropping it from findings.md and the
    # final report with no further nudge ever firing.
    city_prior = [{"task_name": "rent_lisbon_central_one_bedroom",
                   "instructions": ("Find the typical monthly rent cost for a one-bedroom apartment "
                                     "in a central neighborhood of Lisbon (e.g., Baixa, Chiado, "
                                     "Alfama). Include average price range and provide a real "
                                     "source URL that supports each claim.")}]
    assert _looks_like_renamed_task(
        "rent_mexico_city_central_one_bedroom",
        ("Find the typical monthly rent cost for a one-bedroom apartment in a central neighborhood "
         "of Mexico City (e.g., Polanco, Condesa, Roma). Include average price range and provide a "
         "real source URL that supports each claim."),
        city_prior,
    ) is None, "different named subjects sharing a template must not be treated as a rename"
    # But a genuine same-city rename (matching entity) must still be caught.
    assert _looks_like_renamed_task(
        "rent_lisbon_central_one_bedroom_v2",
        ("Find the typical monthly rent cost for a one-bedroom apartment in central Lisbon (e.g., "
         "Baixa, Chiado, Alfama), trying a narrower search this time. Provide a real source URL "
         "that supports each claim."),
        city_prior,
    ) == "rent_lisbon_central_one_bedroom"

    # Diacritic-folded entity extraction (2026-08-29 audit finding): the ASCII-only entity regex
    # used to silently DROP an accented proper noun entirely ("México" failed the capture,
    # "Mexico" didn't), which artificially lowered the entity Jaccard for a same-subject rename
    # that switches between the accented and unaccented spelling -- tripping the entity-mismatch
    # override into wrongly treating it as two different subjects.
    mexico_accented_prior = [{"task_name": "rent_mexico_city_central_one_bedroom",
                               "instructions": ("Find the typical monthly rent cost for a "
                                                 "one-bedroom apartment in a central neighborhood "
                                                 "of México City (e.g., Polanco, Condesa, Roma). "
                                                 "Include average price range and provide a real "
                                                 "source URL that supports each claim.")}]
    assert _looks_like_renamed_task(
        "rent_mexico_city_central_one_bedroom_v2",
        ("Find the typical monthly rent cost for a one-bedroom apartment in central Mexico City "
         "(e.g., Polanco, Condesa, Roma), trying a narrower search this time. Provide a real "
         "source URL that supports each claim."),
        mexico_accented_prior,
    ) == "rent_mexico_city_central_one_bedroom", (
        "an accented vs. unaccented spelling of the same place name must still count as the same "
        "entity, not be treated as a mismatch that falsely clears the rename override")

    # --- _content_word_overlap OR-trigger (2026-08-17 live incident): a genuine full-sentence
    # paraphrase (the model's actual, common rewrite style) scores near-zero on difflib's
    # char-level ratio despite being unambiguously the same angle reworded -- confirmed live via
    # this exact real (task_name, instructions) pair pulled from `_run_state.json`: difflib ratio
    # was 0.11, so the ORIGINAL char-ratio-only version of this function returned None for all 3
    # of this facet's task_name variants, and RunState.coverage()'s denominator kept growing
    # across "new" 0-content tasks that were actually retries. ---
    mexico_rent_prior = [{"task_name": "Mexico City central one-bedroom apartment rental cost",
                           "instructions": ("Find the typical monthly rent for a one-bedroom "
                                             "apartment in a central neighborhood of Mexico City "
                                             "(e.g., Polanco, Condesa, Roma). Provide an average "
                                             "figure and cite a real source URL.")}]
    assert _looks_like_renamed_task(
        "Mexico City central one-bedroom rental cost",
        ("Search for recent data on the average monthly rent of a one-bedroom apartment in a "
         "central Mexico City neighbourhood such as Polanco, Condesa or Roma. Provide a URL to a "
         "reputable real-estate listing site or cost-of-living report."),
        mexico_rent_prior,
    ) == "Mexico City central one-bedroom apartment rental cost", (
        "a genuine full-sentence paraphrase of the same facet must still be caught via content-word "
        "overlap even when difflib's char-ratio alone would miss it")
    # The entity-mismatch override must still reject a cross-city paraphrase that ALSO clears the
    # new word-overlap threshold (0.4 Jaccard on this exact live pair, per the incident's own
    # measurement) -- content-word overlap is additive, not a bypass of the existing safety net.
    assert _looks_like_renamed_task(
        "rent_mexico_city_central_one_bedroom",
        ("Find the typical monthly rent for a one-bedroom apartment in a central neighborhood of "
         "Mexico City (e.g., Polanco, Condesa, Roma). Provide an average figure and cite a real "
         "source URL."),
        [{"task_name": "rent_lisbon_central_one_bedroom",
          "instructions": ("Find the typical monthly rent for a one-bedroom apartment in a "
                            "central neighborhood of Lisbon (e.g., Baixa, Chiado, Alfama). "
                            "Provide an average figure and cite a real source URL.")}],
    ) is None, "cross-city template overlap must not be treated as a rename even via the word-overlap trigger"

    # --- multi-URL synthesis attribution (2026-08-27 live incident, Colombia B2B smoke test): a
    # dispatch that fetches several URLs in one turn but writes ONE shared synthesis about only
    # SOME of them must not let the others silently inherit that unrelated text. ---
    from engine.orchestrator import _synthesis_reflects_url_content, _select_unreflected_urls

    real_synthesis = (
        "Hospitals and IPS must have an electronic medical record system in place by 31 December "
        "2026 under Resolucion 1888 de 2025, per the AWNewsCenter habilitation article."
    )
    matching_page = (
        "Habilitacion hospitalaria en Colombia: hospitals and IPS must comply with the new "
        "electronic medical record requirement by 31 December 2026, Resolucion 1888 de 2025 sets "
        "the technical requirements."
    )
    unrelated_page = (
        "Estado actual de las exportaciones de software colombiano: the number of insurtech "
        "startups in the region grew 5% in 2024, reaching 502 companies, with organic growth of "
        "+15% and 70 new insurtech firms created."
    )
    assert _synthesis_reflects_url_content(real_synthesis, matching_page) is True, (
        "a page whose own content substantially overlaps the shared synthesis must be treated as "
        "reflected, not flagged")
    assert _synthesis_reflects_url_content(real_synthesis, unrelated_page) is False, (
        "a co-fetched page about a completely different topic (software exports vs. EMR "
        "compliance) must be flagged as NOT reflected by the shared synthesis")
    assert _synthesis_reflects_url_content(real_synthesis, "") is False, (
        "empty page content cannot support the synthesis")
    assert _synthesis_reflects_url_content("", matching_page) is True, (
        "an empty summary has nothing to check against -- don't invent a problem"
    )

    # --- explicit-citation override (2026-08-29 live incident): a synthesis that explicitly
    # cites URLs never fetched this run can coincidentally share enough generic topic vocabulary
    # with an unrelated co-fetched page to pass word-containment alone -- the explicit citation
    # must override that regardless of overlap. ---
    fabricated_synthesis = (
        "Germany's renewable energy policy for 2026 sets ambitious EEG targets, "
        "*Source:* [Yahoo Finance](https://finance.example.com/germany-renewable-article) and "
        "[Clean Energy Wire](https://cleanenergywire.example.com/other-article)."
    )
    coincidentally_similar_page = (
        "Germany renewable energy EEG policy overview 2026: this official government page "
        "discusses Germany's renewable energy targets and EEG funding reforms in detail."
    )
    assert _synthesis_reflects_url_content(
        fabricated_synthesis, coincidentally_similar_page,
        "https://iclg.com/practice-areas/renewable-energy-laws-and-regulations/germany",
    ) is False, (
        "a synthesis citing URLs never fetched this run must be flagged against an uncited "
        "co-fetched page even when word-containment alone would pass on shared topic vocabulary")
    assert _synthesis_reflects_url_content(
        fabricated_synthesis, coincidentally_similar_page,
        "https://finance.example.com/germany-renewable-article",
    ) is True, (
        "a page matching one of the synthesis's own explicit citations must still be treated as "
        "reflected")
    assert _synthesis_reflects_url_content(real_synthesis, matching_page, "") is True, (
        "no url passed (url='') must behave exactly as before -- pure word-containment, no "
        "citation check attempted")

    multi_urls = [
        {"url": "https://awnewscenter.example.co/habilitation", "filename": "aw.md"},
        {"url": "https://itsitio.example.co/exports", "filename": "itsitio.md"},
    ]
    unreflected = _select_unreflected_urls(
        real_synthesis, multi_urls,
        {"aw.md": matching_page, "itsitio.md": unrelated_page},
    )
    assert unreflected == ["https://itsitio.example.co/exports"], (
        "only the co-fetched URL whose real content the synthesis doesn't cover should be flagged")
    assert _select_unreflected_urls(real_synthesis, multi_urls[:1], {"aw.md": matching_page}) == [], (
        "a single-URL dispatch has nothing to disambiguate -- must never flag")

    # --- specialist per-task delegation cap (2026-07-26 live case: one WebSearcher task
    # delegated 6+ Analyzer sub-tasks for a trivial single-fact query, burning most of the run's
    # global delegate_tasks budget) ---
    from engine.orchestrator import _specialist_delegation_over_cap
    assert not _specialist_delegation_over_cap(current_count=0, batch_size=3, cap=3), (
        "a batch that exactly fills the cap must be allowed")
    assert not _specialist_delegation_over_cap(current_count=1, batch_size=2, cap=3), (
        "a batch that lands exactly on the cap after prior dispatches must be allowed")
    assert _specialist_delegation_over_cap(current_count=0, batch_size=4, cap=3), (
        "a single batch larger than the cap must be rejected")
    assert _specialist_delegation_over_cap(current_count=2, batch_size=2, cap=3), (
        "a batch that would push a task past its cap, even if the batch itself is small, must be rejected"
    )
    assert not _specialist_delegation_over_cap(current_count=0, batch_size=0, cap=3), (
        "an empty batch never exceeds any cap")

    # --- Planner-level replan-round cap (2026-08-01, sibling to the specialist cap above --
    # RESEARCH.md Sec.15: live-tested AFTER the fetch cap + softened cutoff wording, the Planner
    # still redispatched tasks repeatedly -- up to 6 total delegate_tasks rounds on a 2-task query
    # -- with no cutoff marker involved at all) ---
    from engine.orchestrator import _planner_delegate_over_cap
    assert not _planner_delegate_over_cap(current_count=0, cap=4), (
        "the Planner's very first delegate_tasks round must always be allowed")
    assert not _planner_delegate_over_cap(current_count=3, cap=4), (
        "a round below the cap must be allowed")
    assert _planner_delegate_over_cap(current_count=4, cap=4), (
        "a round already AT the cap must be rejected")
    assert _planner_delegate_over_cap(current_count=6, cap=4), (
        "a round already over the cap (the exact live-observed shape) must stay rejected")

    # --- specialist per-task fetch cap (2026-08-01, sibling fix to the delegation cap above --
    # RESEARCH.md Sec.15: one WebSearcher task fetched 11 distinct URLs for a single uncontested
    # fact, exhausting context_budget_chars and cascading into an unforced Planner re-verification
    # round) ---
    from tools.web import _specialist_fetch_over_cap
    assert not _specialist_fetch_over_cap(current_count=4, cap=5), (
        "a task below the cap must be allowed to fetch one more")
    assert _specialist_fetch_over_cap(current_count=5, cap=5), (
        "a task already AT the cap must be rejected on its next fetch")
    assert _specialist_fetch_over_cap(current_count=6, cap=5), (
        "a task already over the cap (shouldn't normally happen, but must stay rejected) must be rejected")
    assert not _specialist_fetch_over_cap(current_count=0, cap=5), (
        "a task's very first fetch must always be allowed")

    # --- _find_sibling_fetch (2026-08-01, RESEARCH.md Sec.17g): delegate_tasks' own Analyzer-URL
    # validation used to give the SAME "call fetch_url_to_workspace yourself first" advice whether
    # a URL was never fetched by anyone this run, or already fetched by a DIFFERENT task -- the
    # second case is a dead end (fetch_url_to_workspace's own cross-task dedup will just reject
    # that retry too), confirmed live to burn a task's entire delegate_tasks quota (5-9 calls)
    # retrying the identical rejected shape before giving up and narrating fabricated findings
    # from search-snippet text alone. This is the lookup that lets the rejection message tell the
    # two cases apart and give accurate advice. ---
    from engine.orchestrator import _find_sibling_fetch
    _fetched = [
        {"url": "https://example.co/a", "filename": "sources/a.md"},
        {"url": "https://example.co/b/", "filename": "sources/b.md"},  # trailing slash on record
    ]
    assert _find_sibling_fetch("https://example.co/a", _fetched) == _fetched[0], (
        "an exact match (modulo trailing slash) must resolve to its real saved filename")
    assert _find_sibling_fetch("https://example.co/a/", _fetched) == _fetched[0], (
        "the LOOKED-UP url's own trailing slash must not prevent a match either")
    assert _find_sibling_fetch("https://example.co/b?utm_source=x", _fetched) == _fetched[1], (
        "a real prefix-match variant (redirect/query-string drift) must still resolve")
    assert _find_sibling_fetch("https://example.co/never-fetched", _fetched) is None, (
        "a URL nobody has fetched this run must return None -- the ORIGINAL 'call "
        "fetch_url_to_workspace yourself first' advice is correct for this case, not a trap")
    assert _find_sibling_fetch("https://example.co/a", []) is None, (
        "an empty fetched-urls list (nothing fetched at all yet) must not crash or false-match")

    # --- analyzer per-dispatch read/grep cap (2026-08-01, third instance of the same recurring
    # shape as the two caps above -- RESEARCH.md Sec.16: one Analyzer dispatch burned 34-40 of the
    # shared, whole-run grep_workspace_file quota chasing a hard document, starving every OTHER
    # Analyzer dispatched afterward of the ability to search/read their own unrelated sources) ---
    from tools.fs import _analyzer_read_over_cap
    assert not _analyzer_read_over_cap(current_count=7, cap=8), (
        "a dispatch below the cap must be allowed one more read/grep")
    assert _analyzer_read_over_cap(current_count=8, cap=8), (
        "a dispatch already AT the cap must be rejected on its next call")
    assert _analyzer_read_over_cap(current_count=34, cap=8), (
        "a dispatch already far over the cap (the exact live-observed shape) must stay rejected")
    assert not _analyzer_read_over_cap(current_count=0, cap=8), (
        "a dispatch's very first read/grep call must always be allowed")

    # --- cutoff-marker wording (2026-08-01, RESEARCH.md Sec.15): a context-budget cutoff must not
    # get the alarming "wrapped up early" wording when the task already gathered real evidence --
    # the Planner's ADAPTIVE PLANNING LOOP reads this text to decide whether to dispatch a
    # follow-up, and treating every cutoff identically caused an unforced re-verification round for
    # a task that had already fetched 11 real sources. ---
    from engine.orchestrator import _cutoff_marker_text
    _below = _cutoff_marker_text("volcano_disruption", 1)
    assert "wrapped up early" in _below and "NOT necessarily incomplete" not in _below, _below
    _zero = _cutoff_marker_text("volcano_disruption", 0)
    assert "wrapped up early" in _zero, _zero
    _at_min = _cutoff_marker_text("volcano_disruption", 2)
    assert "NOT necessarily incomplete" in _at_min and "wrapped up early" not in _at_min, _at_min
    _above = _cutoff_marker_text("volcano_disruption", 11)
    assert "NOT necessarily incomplete" in _above and "11 real source" in _above, _above

    # --- FOLLOW-UP DIRECTIONS extraction (2026-07-19, engine-driven iterative deepening,
    # ROADMAP item 10) — matches WEB_SEARCHER_INSTRUCTIONS/ACADEMIC_SEARCHER_INSTRUCTIONS'
    # mandated trailing section format. ---
    summary_with_directions = (
        "- **[Rust 1.97](https://example.com/rust)**: current stable release.\n\n"
        "FOLLOW-UP DIRECTIONS:\n"
        "- Chase the async runtime RFC mentioned but not fetched.\n"
        "- Corroborate the release date against the official blog.\n"
    )
    directions = _extract_follow_up_directions(summary_with_directions)
    assert directions == [
        "Chase the async runtime RFC mentioned but not fetched.",
        "Corroborate the release date against the official blog.",
    ], directions
    assert _extract_follow_up_directions("no such section here") == []
    assert _extract_follow_up_directions("") == []
    # Case-insensitive header, per re.IGNORECASE — a model that varies casing must still be caught.
    assert _extract_follow_up_directions("stuff\nfollow-up directions:\n- one lead") == ["one lead"]

    # --- _strip_follow_up_directions (2026-08-17): grounding checks on a specialist summary must
    # not treat a FOLLOW-UP DIRECTIONS bullet's URL as a claim citation -- live incident:
    # MexicoCity_digital_nomad_visa correctly cited relocate.world/consulmex for its real findings,
    # then suggested citas.sre.gob.mx (never fetched) as a follow-up lead, and real_grounding_
    # problem flagged the WHOLE summary as ungrounded over a suggestion, not a citation. ---
    real_body = "- **[Relocate.World](https://relocate.world/visa)**: real cited finding.\n\n"
    with_directions = real_body + "FOLLOW-UP DIRECTIONS:\n- Check https://citas.sre.gob.mx for current status.\n"
    stripped = _strip_follow_up_directions(with_directions)
    assert "citas.sre.gob.mx" not in stripped, stripped
    assert "relocate.world/visa" in stripped, stripped
    assert stripped.rstrip() == real_body.rstrip(), stripped
    # No section present -> unchanged.
    assert _strip_follow_up_directions("no such section here") == "no such section here"
    assert _strip_follow_up_directions("") == ""

    # --- task_deadline ring-fence math (2026-07-21, "4th synthesis-vanishing mechanism" fix 1) ---
    # Normal case: extends by a full second sub_agent_timeout_minutes when the SDK ceiling has room.
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=3600,
    ) == 1200
    # The one real bug risk this whole fix carries: the extension must never be pushed past the
    # SDK client's own blunt timeout, or the previously-fixed "SDK wins the race" bug (see
    # _build_client's sdk_timeout comment) comes back. Confirm the cap actually bites.
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=700,
    ) == 640  # capped at sdk_timeout_ceiling_seconds - 60, not the full 1200
    # No room left at all under the SDK ceiling -> don't extend (caller falls through to cutoff).
    assert _ring_fenced_deadline(
        task_start=0, task_deadline=600, sub_agent_timeout_minutes=10, sdk_timeout_ceiling_seconds=650,
    ) is None

    # --- cutoff-only summary detection (2026-07-21, "4th synthesis-vanishing mechanism" fix 3) ---
    # Must match orchestrator.py's task_deadline marker text EXACTLY, both variants, and ONLY
    # when it's the entire summary -- real content followed by the marker must NOT match (a
    # partial synthesis is still worth showing to FindingsWriter).
    assert _CUTOFF_ONLY_SUMMARY_RE.match(
        "\n\n[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded.]"
    )
    assert _CUTOFF_ONLY_SUMMARY_RE.match(
        "\n\n[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded "
        "(stream produced no update before the deadline).]"
    )
    assert not _CUTOFF_ONLY_SUMMARY_RE.match(
        "N-BEATS and TFT are the two leading architectures.\n\n"
        "[SYSTEM: task 'top_heuristics' cut short -- sub_agent_timeout_minutes (10) exceeded.]"
    )
    assert not _CUTOFF_ONLY_SUMMARY_RE.match("Rust's current stable release is 1.97.1.")

    # --- findings-ordering positional-bias reorder (2026-07-22, "Lost in the Middle" +
    # PING's "Anchor Effect") --- pure positional zigzag, no value signal.
    entries6 = [(f"T{i}", f"E{i}") for i in range(6)]
    reordered = _reorder_findings_for_position_bias(entries6)
    assert reordered == [entries6[i] for i in (0, 5, 1, 4, 2, 3)], reordered
    # Invariants that must hold regardless of length -- nothing lost, nothing duplicated.
    for n in (0, 1, 2, 5, 7):
        sample = [(f"T{i}", f"E{i}") for i in range(n)]
        result = _reorder_findings_for_position_bias(sample)
        assert len(result) == n and set(result) == set(sample), (n, result)

    # --- propagation-aware hallucination check (2026-07-22, PING taxonomy) --- narrowed to the
    # documented split-brain pattern (ROADMAP History, 20260718_141225): the task-name-fallback
    # (uncited) sibling carries the genuinely detailed real content, a LATER redispatch produces a
    # different real URL whose summary closely paraphrases that same content without having
    # independently re-verified it against its own claimed source -- suspicious regardless of
    # which sibling has more detail, since a citable entry that just repeats an uncited sibling's
    # content likely didn't come from its own fetch.
    propagated_findings = [
        {"task_name": "background_heuristics", "source_url": "background_heuristics",
         "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                     "classical baselines in a 2024 benchmark study of Time Series Models."},
        {"task_name": "background_heuristics", "source_url": "https://forecastio.ai/other",
         "summary": "The Temporal Fusion Transformer improved forecast accuracy by 23% over "
                     "classical baselines in a 2024 benchmark study of Time Series Models."},
    ]
    flagged = _find_propagated_bad_content(
        propagated_findings, ["background_heuristics"]
    )
    assert flagged == ["background_heuristics"], flagged
    # Control: no term overlap between the uncited sibling and the citable one -- must NOT flag.
    control_findings = [
        {"task_name": "colombia_holidays", "source_url": "colombia_holidays",
         "summary": "The Dual Granularity Memory pattern was proposed in a 2026 paper on agentic "
                     "systems, unrelated to payroll timing."},
        {"task_name": "colombia_holidays", "source_url": "https://adp.com/payroll-calendar",
         "summary": "Payroll cycles in Colombia typically run twice monthly, aligned with the "
                     "Minimum Wage Disbursement Rules published in 2024."},
    ]
    assert _find_propagated_bad_content(control_findings, ["colombia_holidays"]) == []

    # --- _is_citable_finding excludes relevance-flagged AND verification-flagged findings ---
    # RELEVANCE (2026-07-21, live-caught): a finding confirmed off-topic by orchestrator.py's
    # scope-relevance check carries a real http URL and a non-cutoff summary, so it used to pass
    # as an ordinary citable entry even though it has zero real value for the query -- live case
    # was a Colombia-holidays task that fetched a New Zealand page.
    relevance_flagged = {
        "task_name": "colombia_holidays", "source_url": "https://publicholiday.co.nz/",
        "summary": ("I was unable to complete the research because I exceeded the web-search "
                    "quota. The only page fetched was a New Zealand holiday calendar.\n\n"
                    "[SYSTEM RELEVANCE WARNING: none of the sources fetched for this task "
                    "actually mention Colombia, despite the task instructions requiring it.]"),
    }
    assert _is_citable_finding(relevance_flagged) is False
    real_finding = {
        "task_name": "background", "source_url": "https://arxiv.org/abs/1234.5678",
        "summary": "N-BEATS improved forecast accuracy by 23% in a 2024 benchmark.",
    }
    assert _is_citable_finding(real_finding) is True
    # VERIFICATION (2026-07-26, REVERSING a 2026-07-22 decision -- see _is_citable_finding's own
    # docstring for the full reasoning): confirmed live TWICE (calendarr.com 2026-07-24,
    # insidetx.com 2026-07-26, RESEARCH.md Sec.10) that leaving a citation-mismatch-flagged finding
    # in the evidence and trusting FindingsWriter to heed its own embedded warning does not hold --
    # it kept citing the flagged bad URL anyway across 7 independent dispatches in one run. Must
    # now be excluded exactly like a RELEVANCE-flagged finding.
    verification_flagged = {
        "task_name": "background", "source_url": "https://arxiv.org/abs/1234.5678",
        "summary": ("N-BEATS improved forecast accuracy by 23% in a 2024 benchmark.\n\n"
                    "[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source "
                    "that does not match anything actually fetched this run.]"),
    }
    assert _is_citable_finding(verification_flagged) is False
    # NULL-FINDING (2026-08-17, evidence-crowding root cause, session_status 2026-08-16 item 3):
    # a real http URL with a genuine "nothing extracted" narration used to pass every condition
    # above (real URL, no cutoff marker, no warning marker) and render as an ordinary citable
    # source -- crowding FindingsWriter's evidence blob with placeholders on a busy multi-source
    # task. Must be excluded exactly like a relevance/verification-flagged finding.
    null_finding = {
        "task_name": "background", "source_url": "https://example.com/thin-page",
        "summary": "No key findings extracted from this source during this research run.",
    }
    assert _is_citable_finding(null_finding) is False
    # ACADEMIC CITATION EXISTENCE (2026-08-22): the academic_citation_unverified label added to
    # _VERIFICATION_FLAGGED_URLS_RE for academic_citation_existence_problem must be recognized and
    # URL-scoped by the same existing mechanism, not just wholesale-exclude every co-cited finding.
    ac_bad_url = "https://example.com/fabricated-paper"
    ac_real_url = "https://real.example.co/genuine-paper"
    ac_summary = (
        "Consolidated findings citing both a fabricated and a genuine academic reference.\n\n"
        f"[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that does not "
        f"match anything actually fetched this run, or to something that isn't a real URL at all "
        f"(academic_citation_unverified:{ac_bad_url}). Do not treat the associated claim as sourced "
        f"when writing findings.md.]"
    )
    assert _is_citable_finding({"source_url": ac_bad_url, "summary": ac_summary}) is False
    assert _is_citable_finding({"source_url": ac_real_url, "summary": ac_summary}) is True, (
        "a co-cited real reference must not be swept up by another citation's fabricated-attribution flag")

    # --- _build_findings_source_material renders a real title when available (2026-07-21,
    # closing the format gap with FINDINGS_WRITER_INSTRUCTIONS' own required output shape), falls
    # back to the plain "### Source: url" shape when no title was extracted. ---
    def _title_rendering_scenario():
        reset_fetched_urls()
        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            run_state_ctx.set(rs)
            record_fetched_url("https://arxiv.org/abs/1234.5678", filename="sources/arxiv.md",
                                title="N-BEATS: A Neural Basis Expansion Approach")
            record_fetched_url("https://example.com/no-title", filename="sources/notitle.md")
            rs.add_finding("https://arxiv.org/abs/1234.5678", "Real finding with a title.", task_name="t1")
            rs.add_finding("https://example.com/no-title", "Real finding without a title.", task_name="t2")
            material = _build_findings_source_material(rs)
            assert "### [N-BEATS: A Neural Basis Expansion Approach](https://arxiv.org/abs/1234.5678)" in material, material
            assert "### Source: https://example.com/no-title" in material, material
        reset_fetched_urls()

    contextvars.copy_context().run(_title_rendering_scenario)

    # --- near-duplicate finding dedup (2026-08-17, live incident: a task-name-renamed retry that
    # got through as the FIRST rename match -- advisory-only, not yet a hard reject -- produced a
    # SEPARATE finding restating the same research under a different task_name; final_report.md
    # carried two near-identical "Mexico City rent" sections as a result). ---
    def _near_dup_finding_scenario():
        reset_fetched_urls()
        with tempfile.TemporaryDirectory() as tmpdir:
            rs = RunState(tmpdir)
            run_state_ctx.set(rs)
            record_fetched_url("https://airbnb.example.com/mexico-city", filename="sources/airbnb.md")
            record_fetched_url("https://numbeo.example.com/mexico-city", filename="sources/numbeo.md")
            rent_summary = (
                "Average rent for a one-bedroom apartment in Mexico City is 22,000-38,000 "
                "Mexican pesos per month (~$1,200-$2,050 USD)."
            )
            rs.add_finding("https://airbnb.example.com/mexico-city", rent_summary,
                            task_name="rental_cost_mexico_city_central")
            # Same underlying research, independently synthesized (not byte-identical) under a
            # DIFFERENT, renamed task_name -- exact-key dedup alone would keep both.
            rs.add_finding("https://numbeo.example.com/mexico-city",
                            rent_summary.replace("Average rent", "The average rent"),
                            task_name="rental_cost_mexico_city_central_districts")
            # A genuinely different facet must survive even though it shares topical vocabulary
            # ("Mexico City") with the near-duplicate pair above.
            rs.add_finding("https://esimcard.example.com/mexico-visa",
                            "Mexico's digital nomad visa requires proof of steady monthly income.",
                            task_name="visa_requirements_mexico")
            material = _build_findings_source_material(rs)
            assert material.count("22,000") == 1, (
                "the near-duplicate rent finding must be collapsed to one entry", material)
            assert "digital nomad visa" in material, (
                "a genuinely different facet must not be swept up by the near-dup guard", material)
        reset_fetched_urls()

    contextvars.copy_context().run(_near_dup_finding_scenario)

    # --- duplicate report sections (2026-08-17, live incident: a Builder Fix-pass's own
    # edit_workspace_file call retyped an existing section's content while appending a new
    # subsection next to it, producing two near-identical "### Mexico City" /
    # "### Mexico City – Central Districts" sections). ---

    _DUP_REPORT = """# Report

## Rental Costs

### Mexico City
- Average rent for a one-bedroom apartment: 22,000-38,000 Mexican pesos per month (~$1,200-$2,050 USD). - [Airbnb](https://airbnb.example.com/mx)

### Mexico City – Central Districts
- Average monthly rent for a one-bedroom apartment: 22,000-38,000 Mexican pesos per month (~$1,200-$2,050 USD). - [Airbnb](https://airbnb.example.com/mx)

### Lisbon
- Average rent for a one-bedroom apartment is 650 EUR in the city centre. - [Nestpick](https://nestpick.example.com/lisbon)
"""
    dups = find_duplicate_report_sections(_DUP_REPORT)
    assert dups and "Central Districts" in dups[0], dups
    # A genuinely different section (Lisbon) sharing topical vocabulary ("rent", "one-bedroom
    # apartment") with Mexico City must not be swept up.
    assert not any("Lisbon" in d for d in dups), dups
    # No duplicates at all -> silent.
    assert find_duplicate_report_sections("# R\n\n### Mexico City\n- a\n\n### Lisbon\n- b\n") == []

    # --- duplicate HEADING text, the inverse shape (2026-08-28, live incident: gemma4:e4b
    # bake-off produced a report with "## 2. Key Findings" appearing twice, each followed by
    # DIFFERENT, non-overlapping subsections — find_duplicate_report_sections' content-similarity
    # comparison never fires on this since the content genuinely differs, and it's also scoped to
    # h3+ only while this duplicate landed at h2). ---
    from engine.completion import find_duplicate_heading_text

    _DUP_HEADING_REPORT = """# Report

## 1. Introduction

Some intro text.

## 2. Key Findings

### A. Data Privacy
- claim one - [Source](https://a.example.com)

## 2. Key Findings

### B. Environmental Reporting
- a completely unrelated claim about ESG mandates - [Source](https://b.example.com)

### D. Advanced Manufacturing
- claim two - [Source](https://d.example.com)
"""
    heading_dups = find_duplicate_heading_text(_DUP_HEADING_REPORT)
    assert heading_dups and "Key Findings" in heading_dups[0], heading_dups
    # The genuinely different h1/h2 headings ("Introduction", "Data Privacy" subsections used only
    # once at h3) must not be swept up.
    assert not any("Introduction" in d for d in heading_dups), heading_dups
    # Renumbered-but-same-title still counts (leading list-prefix stripped before comparing).
    assert find_duplicate_heading_text("# R\n\n## 2. Key Findings\nx\n\n## 3. Key Findings\ny\n") != []
    # No duplicates at all -> silent.
    assert find_duplicate_heading_text("# R\n\n## Findings\nx\n\n## Sources\ny\n") == []
    # find_duplicate_report_sections alone would miss this (different content, wrong level) --
    # confirms check_duplicate_report_sections' own fallback-to-heading-text path is load-bearing.
    assert find_duplicate_report_sections(_DUP_HEADING_REPORT) == []



if __name__ == "__main__":
    main()
    print("test_orchestrator_extraction OK")
