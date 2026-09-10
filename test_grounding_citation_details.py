import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from utils.grounding import find_non_url_citations, find_uncited_claim_lines, extract_cited_urls
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

from engine.completion import (
    run_completion_check,
)
from tools.fs import _IN_MEMORY_FS
from utils.grounding import (
    real_grounding_problem, _is_null_finding_summary, claim_grounding_problem,
    decompose_claim_segments, find_unsupported_specific_figures, parse_academic_references,
    fully_ungrounded, partially_ungrounded,
)
from utils.run_state import RunState, run_state_ctx, record_verified_cache_url, verified_cache_urls_ctx

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
    # --- specific-figure grounding (live case, 2026-08-17 ablation smoke-test: a real income
    # figure was misattributed to the WRONG one of two genuinely-fetched, similar sources; the
    # URL gate and content_level_check's term-overlap gate both passed on nothing but a
    # coincidentally shared bare year) ---

    def _specific_figure_scenario():
        _orig_ws13 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://esimcard.example.com/mexico-visa", filename="sources/esim.md")
            _IN_MEMORY_FS["sources/esim.md"] = (
                "Source-URL: https://esimcard.example.com/mexico-visa\n\n"
                "Mexico does not offer an official digital nomad visa in 2026. Remote workers "
                "usually use the Temporary Resident Visa, whose income requirement varies by "
                "consulate, from $4,510 in San Diego to other amounts elsewhere."
            )
            # Misattributed figures: neither "300" nor "53" appears in this source at all ->
            # flagged, even though a coincidentally shared bare year ("2026") is present.
            bad = find_unsupported_specific_figures(
                "- Income requirement: 300 days of minimum wage; application fee $53. "
                "[esimcard](https://esimcard.example.com/mexico-visa)")
            assert any("300" in b for b in bad), bad
            assert any("53" in b for b in bad), bad
            # Supported figures: a source that DOES contain both numbers -> silent
            record_fetched_url("https://themexicohandbook.example.com/visa", filename="sources/handbook.md")
            _IN_MEMORY_FS["sources/handbook.md"] = (
                "Source-URL: https://themexicohandbook.example.com/visa\n\n"
                "You must demonstrate income equivalent to 300 days of the minimum wage. The "
                "consular processing fee is approximately 53 USD, payable by credit card."
            )
            assert find_unsupported_specific_figures(
                "- Income requirement: 300 days of minimum wage; application fee $53. "
                "[handbook](https://themexicohandbook.example.com/visa)") == []
            # A figure with no citation on the line, or an unfetched URL -> other gates' job, silent.
            assert find_unsupported_specific_figures("Pay a $53 fee before your interview.") == []
            assert find_unsupported_specific_figures(
                "Pay a $53 fee ([x](https://never-fetched.example.com/y))") == []
            # Single-digit figures are too generic a signal -> never flagged.
            assert find_unsupported_specific_figures(
                "- A $5 surcharge applies. [esimcard](https://esimcard.example.com/mexico-visa)") == []
            # Comma-formatting must not false-positive (caught live 2026-08-17 validating this
            # check against the real incident data): a genuinely-supported figure whose SOURCE
            # also happens to write it with a thousands-separator comma (not just the claim) must
            # still match -- comparing raw strings without normalizing the source's own comma too
            # silently broke this the first time it was implemented.
            record_fetched_url("https://budget.example.com/mexico-city", filename="sources/budget.md")
            _IN_MEMORY_FS["sources/budget.md"] = (
                "Source-URL: https://budget.example.com/mexico-city\n\n"
                "Utilities and internet typically run about $1,200 per month for a comfortable "
                "setup, separate from rent."
            )
            assert find_unsupported_specific_figures(
                "- Utilities cost $1,200/month. [budget](https://budget.example.com/mexico-city)") == []
            # Named-entity TOKEN generalization (the residual gap from the same live incident:
            # "MiConsulado" was misattributed alongside its numeric figures) -- a mixed-case
            # portal/program name absent from the cited source's content is flagged the same way.
            bad_token = find_unsupported_specific_figures(
                "- Interview via MiConsulado portal. "
                "[esimcard](https://esimcard.example.com/mexico-visa)")
            assert any("MiConsulado" in b for b in bad_token), bad_token
            record_fetched_url("https://portal.example.com/miconsulado", filename="sources/portal.md")
            _IN_MEMORY_FS["sources/portal.md"] = (
                "Source-URL: https://portal.example.com/miconsulado\n\n"
                "Schedule your interview through the MiConsulado portal at least two weeks in "
                "advance."
            )
            assert find_unsupported_specific_figures(
                "- Interview via MiConsulado portal. "
                "[portal](https://portal.example.com/miconsulado)") == []
            # A plain capitalized proper noun with no internal case switch (e.g. "Mexico") must
            # never fire -- only genuine mixed-case tokens are checkable this way.
            assert find_unsupported_specific_figures(
                "- Mexico requires proof of income. "
                "[esimcard](https://esimcard.example.com/mexico-visa)") == []
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws13 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws13

    contextvars.copy_context().run(_specific_figure_scenario)

    # --- find_unsupported_specific_figures' named-token check: two real false positives found
    # live 2026-08-25 (a real --style academic BERT/RoBERTa/ALBERT report against OpenRouter's
    # stealth/ox-alpha), both fixed the same day. ---
    def _named_token_false_positive_scenario():
        _orig_ws14 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            _albert_src = "https://arxiv.example.co/albert"
            record_fetched_url(_albert_src, filename="sources/albert.md")
            # Mirrors the real ALBERT abstract's own LaTeX-macro-leak artifact verbatim: the
            # source's raw scraped text says "\squad" (a \newcommand{\squad}{SQuAD}-style macro
            # left unexpanded by arXiv's plain-text extraction), not the correctly-capitalized
            # word the model naturally writes.
            _IN_MEMORY_FS["sources/albert.md"] = (
                "Source-URL: " + _albert_src + "\n\n"
                "Our best model establishes new state-of-the-art results on the GLUE, RACE, "
                "and \\squad benchmarks while having fewer parameters compared to BERT-large."
            )
            # Case 1 (fixed via re.IGNORECASE): a correctly-capitalized "SQuAD" citation to a
            # source whose own raw text renders it as "\squad" must NOT be flagged -- the claim
            # is completely accurate, only the source's scrape is corrupted.
            bad = find_unsupported_specific_figures(
                f"- ALBERT achieves SOTA on GLUE, RACE, and SQuAD. [albert]({_albert_src})")
            assert bad == [], (
                "a case-differing match against a source's own LaTeX-macro-leak artifact "
                "(\\squad vs SQuAD) must not be flagged as unsupported", bad)
            # Guard: a token genuinely absent from the source (in ANY case) must still be flagged
            # -- the case-insensitivity fix must only rescue a real case mismatch, never a
            # wholesale fabrication.
            bad = find_unsupported_specific_figures(
                f"- ALBERT achieves SOTA on GLUE, RACE, and FakeBenchmark. [albert]({_albert_src})")
            assert any("FakeBenchmark" in b for b in bad), (
                "a token genuinely absent from the source, in any case, must still be flagged", bad)

            # Case 2 (fixed via the any-fetched-source fallback for named tokens): a line NAMING
            # multiple papers while citing only ONE of them for the specific claim being made --
            # e.g. real report text "...the arXiv abstract pages for BERT (arXiv:1810.04805),
            # RoBERTa (arXiv:1907.11692), and ALBERT..." cites only the BERT source on that line
            # (a bare "arXiv:1907.11692" mention is not a resolvable citation), so "RoBERTa"
            # naturally never appears in BERT's own abstract even though no fact is being
            # asserted about RoBERTa there -- it's just named as one of the run's other subjects.
            _bert_src = "https://arxiv.example.co/bert"
            record_fetched_url(_bert_src, filename="sources/bert.md")
            _IN_MEMORY_FS["sources/bert.md"] = (
                "Source-URL: " + _bert_src + "\n\n"
                "arXiv:1810.04805 -- We introduce BERT, a new bidirectional Transformer language "
                "representation model that achieves state-of-the-art results on eleven NLP tasks."
            )
            _roberta_src = "https://arxiv.example.co/roberta"
            record_fetched_url(_roberta_src, filename="sources/roberta.md")
            _IN_MEMORY_FS["sources/roberta.md"] = (
                "Source-URL: " + _roberta_src + "\n\n"
                "RoBERTa is a replication study of BERT pretraining that measures the impact "
                "of key hyperparameters and training data size."
            )
            bad = find_unsupported_specific_figures(
                "This review covers the arXiv abstract pages for BERT (arXiv:1810.04805), "
                f"RoBERTa (arXiv:1907.11692), and ALBERT. [bert]({_bert_src})")
            assert bad == [], (
                "naming a DIFFERENT paper this run genuinely fetched, on a line citing only ONE "
                "other source, must not be flagged as unsupported", bad)
            # Guard: a named paper this run never fetched at all must still be flagged, even
            # though the fallback now checks every fetched source, not just this line's own.
            bad = find_unsupported_specific_figures(
                "This review covers the arXiv abstract pages for BERT (arXiv:1810.04805), "
                f"FakePaper (arXiv:9999.99999), and ALBERT. [bert]({_bert_src})")
            assert any("FakePaper" in b for b in bad), (
                "a named paper genuinely absent from EVERY fetched source this run must still "
                "be flagged -- the broadened fallback must not rescue real fabrications", bad)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws14 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws14

    contextvars.copy_context().run(_named_token_false_positive_scenario)

    # --- regulation-identifier grounding (live case run 12: 'Ley 1906 de 2021' cited to a real
    # fetched page that never mentions 1906 — passed both the URL gate and zero-overlap check) ---
    from utils.grounding import find_unsupported_regulation_ids

    def _regulation_scenario():
        _orig_ws4 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://mintic.example.gov.co/article", filename="sources/mintic.md")
            _IN_MEMORY_FS["sources/mintic.md"] = (
                "Source-URL: https://mintic.example.gov.co/article\n\n"
                "La Estrategia Nacional de Seguridad Digital 2025-2027 llega para proteger a Colombia. "
                "El Ministerio TIC presenta el plan de ciberseguridad nacional para infraestructura."
            )
            # Misattributed law number: page never says 1906 -> flagged
            bad = find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |")
            assert bad and "1906" in bad[0], bad
            # Supported identifier: page that DOES contain the number -> silent
            _IN_MEMORY_FS["sources/mintic.md"] += "\nTexto oficial de la Ley 1906 de 2021."
            assert find_unsupported_regulation_ids(
                "| Ley 1906 de 2021 | [Mintic](https://mintic.example.gov.co/article) |") == []
            # Identifier with an unfetched/no URL on the line -> other gates' job, silent here
            assert find_unsupported_regulation_ids("Decreto 9999/2015 obliga a todos.") == []
            assert find_unsupported_regulation_ids(
                "Decreto 9999/2015 ([x](https://never-fetched.example.com/y))") == []
            # Run 14's self-grounding case: the regulation number exists ONLY inside our own
            # injected Source-URL header line (the URL slug), not in the page content — the
            # check must strip that header before matching, or it verifies against itself.
            record_fetched_url("https://news.example.co/ley-1819-e-invoicing-dian",
                               filename="sources/eltiempo.md")
            _IN_MEMORY_FS["sources/eltiempo.md"] = (
                "Source-URL: https://news.example.co/ley-1819-e-invoicing-dian\n\n"
                "Suscríbete para leer el contenido completo de nuestras noticias y análisis del día."
            )
            bad2 = find_unsupported_regulation_ids(
                "| Ley 1819 de 2016 | [ET](https://news.example.co/ley-1819-e-invoicing-dian) |")
            assert bad2 and "1819" in bad2[0], (bad2, "Source-URL header slug must not self-ground")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws4 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws4

    contextvars.copy_context().run(_regulation_scenario)

    # --- quote-fidelity check (live case 2026-07-24: a report quoted a plausible-sounding
    # sentence attributed to a real fetched source whose actual text says something factually
    # equivalent but differently worded — content_level_check's term-overlap check passed since
    # the underlying claim was true, so only exact-text matching catches this) ---
    from utils.grounding import find_paraphrased_quotes

    def _quote_fidelity_scenario():
        _orig_ws10 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://en.wikipedia.example.org/rayleigh", filename="sources/wiki.md")
            _IN_MEMORY_FS["sources/wiki.md"] = (
                "Source-URL: https://en.wikipedia.example.org/rayleigh\n\n"
                "Rayleigh scattering results from the electric polarizability of the particles. "
                "The oscillating electric field of a light wave acts on the charges within a "
                "particle, causing them to move at the same frequency. The particle, therefore, "
                "becomes a small radiating dipole whose radiation we see as scattered light. "
                "Due to Rayleigh scattering, red and orange colors are more visible during sunset "
                "because the blue and violet light has been scattered out of the direct path."
            )
            # Fabricated "quote": factually adjacent to the real sunset sentence but not its
            # actual wording -> flagged.
            bad = find_paraphrased_quotes(
                '- "Sunset colors arise from selective removal of blue/violet as sunlight '
                'travels a longer path… leaving red/orange visible." '
                '[wiki](https://en.wikipedia.example.org/rayleigh)'
            )
            assert bad and "Sunset colors arise" in bad[0], bad
            # Genuine verbatim quote -> silent.
            assert find_paraphrased_quotes(
                '- "Rayleigh scattering results from the electric polarizability of the '
                'particles." [wiki](https://en.wikipedia.example.org/rayleigh)'
            ) == []
            # Legitimate ellipsis-joined quote: two REAL, non-adjacent sentences from the same
            # source, joined by "…" -- each segment matches on its own, so this must NOT be
            # flagged even though the whole isn't one contiguous source substring.
            assert find_paraphrased_quotes(
                '- "Rayleigh scattering results from the electric polarizability of the '
                'particles… The particle, therefore, becomes a small radiating dipole whose '
                'radiation we see as scattered light." '
                '[wiki](https://en.wikipedia.example.org/rayleigh)'
            ) == []
            # Short quoted span (<25 chars) -> below the noise-avoidance threshold, silent.
            assert find_paraphrased_quotes(
                '- "invented words" [wiki](https://en.wikipedia.example.org/rayleigh)') == []
            # No citation on the line at all -> not this check's job, silent.
            assert find_paraphrased_quotes(
                '- "This exact string is not in any real source at all here" (no link)') == []
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws10 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws10

    contextvars.copy_context().run(_quote_fidelity_scenario)

    # --- academic-style (Author, Year) citation dialect (eval/sales_forecasting_benchmark.md,
    # ROADMAP.md "academic output mode") — same grounding guarantees as the default
    # `- **[Title](URL)**` format, resolved through a parsed References-section map ---
    from utils.grounding import real_grounding_problem as _rgp

    def _academic_citation_scenario():
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc6 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        # Not testing NLI-specific behavior here -- the well-formed case below has genuine
        # term-overlap and would otherwise silently load the real HuggingFace model (see the
        # matrix's own nli_verify:False guard above for why that's undesirable in this suite).
        _config.cfg["settings"]["grounding_check"] = {"nli_verify": False, "topical_relevance_check": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://arxiv.org/abs/2511.00552", filename="sources/tft.md")
            _IN_MEMORY_FS["sources/tft.md"] = (
                "Source-URL: https://arxiv.org/abs/2511.00552\n\n"
                "Temporal Fusion Transformer achieves an R-squared of 0.9875 on 45 Walmart stores, "
                "integrating holiday, CPI, fuel price and temperature signals into a single model."
            )
            well_formed = (
                "## 3. Architectures\n\n"
                "TFT achieved R-squared of 0.9875 on Walmart data (Punati et al., 2025).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert parse_academic_references(well_formed) == {
                "punati,2025": "https://arxiv.org/abs/2511.00552"
            }
            # Well-formed academic report: real citation, real fetch, term overlap -> passes clean.
            assert _asyncio.run(_rgp(well_formed)) is None, _asyncio.run(_rgp(well_formed))

            # Fabricated in-text citation with no matching References entry -> non_url_citation,
            # same failure class a bare (Org, Year) pseudo-citation already triggers.
            fabricated = (
                "## 3. Architectures\n\n"
                "A DQN model achieves the highest accuracy in FMCG forecasting (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            problem = _asyncio.run(_rgp(fabricated))
            assert problem and problem.startswith("non_url_citation"), problem

            # A References entry that cites a paper by title/arXiv-ID text alone, no real URL —
            # exactly how the DeepSeek gold reference itself writes some entries — must NOT
            # silently resolve; the in-text citation stays ungrounded until a URL is added.
            no_url_entry = (
                "## 3. Architectures\n\n"
                "GA-DQN raised service level from 61% to 94% (Various Authors, 2025).\n\n"
                "## References\n\n"
                "1. Various Authors. (2025). GA-DQN hybrid. *Supply Chain Analytics Journal*.\n"
            )
            assert parse_academic_references(no_url_entry) == {}
            # No http URL anywhere in the whole report (in-text citation is parenthetical, and
            # the one References entry has none either) -> the hard "no_urls" gate fires first,
            # before non_url_citation_check is ever reached — even more direct than that check.
            problem2 = _asyncio.run(_rgp(no_url_entry))
            assert problem2 == "no_urls", problem2

            # Two citations on one line: an earlier REAL one must not mask a later FABRICATED
            # one — the exact class of bug caught while building this feature (only the first
            # regex match on a line was being resolved before this fix).
            two_on_one_line = (
                "TFT hit R-squared 0.9875 (Punati et al., 2025); a rival model claims higher "
                "accuracy still (Nobody, 2099).\n\n"
                "## References\n\n"
                "1. Punati, S. B., et al. (2025). Temporal Fusion Transformer. "
                "https://arxiv.org/abs/2511.00552\n"
            )
            assert find_non_url_citations(two_on_one_line), "unresolved 2nd citation must be caught"

            # Fresh audit, 2026-07-12: _PARENTHETICAL_CITATION_RE originally required every
            # token before the comma to start with an ASCII capital, so it silently failed to
            # even DETECT "et al."/"&"/"and"/accented-surname citations at all -- not a
            # false-positive, a total miss that broke grounding in both directions (a fabricated
            # multi-author citation went undetected; a genuinely well-formed one was wrongly
            # quarantined). Pin every form the academic-mode prompt actually tells the model to
            # use (prompts.py ACADEMIC_CITATION_FORMAT_INSTRUCTIONS: "et al. for 3+ authors").
            record_fetched_url("https://example.com/drl", filename="sources/drl.md")
            record_fetched_url("https://example.com/pso", filename="sources/pso.md")
            record_fetched_url("https://example.com/rbfnn", filename="sources/rbfnn.md")
            multi_author_forms = (
                "DRL achieves the highest accuracy for FMCG demand forecasting "
                "(Urgenc et al., 2025). PSO cut MAPE by 23 percent versus Transformer "
                "(Smith and Jones, 2024). RBFNN generalization improved significantly "
                "(Chen & Patel, 2020).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
                "2. Smith, J., and Jones, B. (2024). PSO Attention. https://example.com/pso\n"
                "3. Chen, L., & Patel, R. (2020). RBFNN Hybrid. https://example.com/rbfnn\n"
            )
            assert find_non_url_citations(multi_author_forms) == [], (
                "well-formed et al./and/& citations must all resolve, not be flagged")
            assert find_uncited_claim_lines(multi_author_forms) == [], (
                "sections carrying only et al./and/& citations must be exempted, same as http")
            assert _asyncio.run(_rgp(multi_author_forms)) is None, _asyncio.run(_rgp(multi_author_forms))

            # A fabricated multi-author citation with no matching References entry must still be
            # caught now that the detector actually sees "et al." citations at all.
            fabricated_multi_author = (
                "A DQN model achieves the highest accuracy in FMCG forecasting "
                "(Nobody et al., 2099).\n\n"
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). DRL Demand Forecasting. https://example.com/drl\n"
            )
            assert find_non_url_citations(fabricated_multi_author), (
                "fabricated et al. citation with no matching reference must be flagged")

            # Fresh audit, 2026-07-12: _academic_citation_key tried the unanchored in-text regex
            # BEFORE the anchored reference-entry regex, so a numbered reference whose own TITLE
            # happens to contain a (Word, YYYY)-shaped substring got mis-keyed to that inner
            # parenthetical instead of its real leading author/year.
            title_collision = (
                "## References\n\n"
                "1. Urgenc, S., et al. (2025). A study of trends (Preliminary, 1998) in demand "
                "forecasting. https://example.com/drl\n"
            )
            assert parse_academic_references(title_collision) == {
                "urgenc,2025": "https://example.com/drl"
            }, parse_academic_references(title_collision)

            # 2026-08-24 live incident: a real reference entry listing every co-author IN FULL
            # (not "et al.") can need well over 80 characters between the surname and "(Year)" --
            # _REFERENCE_ENTRY_RE's gap was widened 80 -> 300 after this exact paper (Attention Is
            # All You Need, 8 authors, 106 chars) silently failed to resolve on a real live
            # --style academic run, flagging every genuinely correct in-text (Vaswani et al.,
            # 2017) citation as an unresolved non_url_citation even though the model did
            # everything right. A 6-author entry (74 chars) already passed under the old cap --
            # this pins the specific 8-author case that didn't.
            many_authors = (
                "The Transformer architecture (Vaswani et al., 2017) replaces recurrent layers "
                "with self-attention.\n\n"
                "## References\n\n"
                "1. Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones, L., Gomez, A.N., "
                "Kaiser, Ł., & Polosukhin, I. (2017). Attention Is All You Need. "
                "https://arxiv.org/pdf/1706.03762.pdf\n"
            )
            assert parse_academic_references(many_authors) == {
                "vaswani,2017": "https://arxiv.org/pdf/1706.03762.pdf"
            }, parse_academic_references(many_authors)
            assert find_non_url_citations(many_authors) == [], (
                "a real, resolvable (Author, Year) citation for a many-author reference must "
                "not be flagged as unresolved")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc6 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc6

    contextvars.copy_context().run(_academic_citation_scenario)

    # --- answer mode (ROADMAP.md candidate from dzhng/deep-research's writeFinalAnswer,
    # 2026-07-12): a short direct-answer report shape, `(Source: [Title](URL))` inline citations.
    # Deliberately requires ZERO grounding.py changes — the format is just a different PLACEMENT
    # of the same `[Title](URL)` markdown link syntax the standard style already uses, and every
    # check here extracts URLs format-agnostically. This pins that compatibility claim.
    from prompts import PLANNER_INSTRUCTIONS, ANSWER_REPORT_STYLE_INSTRUCTIONS, ANSWER_CITATION_FORMAT_INSTRUCTIONS

    class _AnswerModeSafeDict(dict):
        def __missing__(self, key):
            return '{' + key + '}'

    _rendered = PLANNER_INSTRUCTIONS.format_map(_AnswerModeSafeDict(
        date="2026-07-12", workspace_dir="/tmp/ws", delegation_instructions="[DELEGATION BLOCK]",
        report_style_instructions=ANSWER_REPORT_STYLE_INSTRUCTIONS,
        citation_format_instructions=ANSWER_CITATION_FORMAT_INSTRUCTIONS,
        delegate_tasks_quota=10, write_workspace_file_quota=10, write_todos_quota=5,
    ))
    assert "{report_style_instructions}" not in _rendered
    assert "{citation_format_instructions}" not in _rendered

    _answer_text = (
        "Guido van Rossum created Python in 1991 "
        "(Source: [Wikipedia](https://en.wikipedia.org/wiki/Guido_van_Rossum))."
    )
    assert extract_cited_urls(_answer_text) == ["https://en.wikipedia.org/wiki/Guido_van_Rossum"]
    assert find_non_url_citations(_answer_text) == []
    assert find_uncited_claim_lines(_answer_text) == []

    # --- parenthesized URL extraction (live case 2026-07-12, NIM gpt-oss-20b run): a genuinely
    # fetched Wikipedia disambiguator URL — https://en.wikipedia.org/wiki/Heuristic_(computer_science)
    # — has a literal balanced '(...)' as part of its own path. The old regex excluded ')' entirely
    # from a URL match, truncating this exact citation mid-slug and false-flagging a real fetch as
    # unverified for 3 consecutive completion-check attempts (wasted retry budget chasing a bug in
    # the extractor, not the model). ---
    _paren_url = "https://en.wikipedia.org/wiki/Heuristic_(computer_science)"
    assert extract_cited_urls(f"See [Heuristic]({_paren_url}) for background.") == [_paren_url], (
        "a URL with its own balanced parens must not be truncated at the internal ')'")
    assert extract_cited_urls(f"See ({_paren_url}) for background.") == [_paren_url], (
        "same case without markdown link syntax, just parenthesized prose")
    # A URL with NO internal parens must still have the markdown link's own closing paren stripped.
    assert extract_cited_urls("See [Foo](https://example.com/page) now.") == ["https://example.com/page"]

    # --- trailing '**' stripped (live case 2026-07-13/14: Builder's own citation style is
    # `**[Title](URL)**` — the bold-close asterisks sat right after the URL's own closing ')',
    # which made the old rstrip's endswith(')') check false and left a literal '**' on every
    # extracted URL, so no Builder-written citation could ever match a real fetched URL) ---
    assert extract_cited_urls("- **[Guido van Rossum](https://en.wikipedia.org/wiki/Guido_van_Rossum)**") == [
        "https://en.wikipedia.org/wiki/Guido_van_Rossum"
    ], "trailing '**' from Builder's bold citation style must not survive extraction"
    # Same case, but the URL ALSO has its own internal balanced parens — both the bold '**' and
    # the correct balanced ')' must be handled together, in the right order.
    assert extract_cited_urls(f"- **[Heuristic]({_paren_url})**") == [_paren_url], (
        "bold citation style combined with a URL's own balanced parens must still resolve correctly")

    # --- trailing backtick stripped (live case 2026-07-26: a real Searcher/Analyzer summary style
    # is "**Source URL**\n`URL`" -- inline-code markdown -- and the old regex/rstrip set didn't
    # stop at '`', so the trailing backtick was captured as part of the URL. Confirmed live: this
    # false-flagged _build_findings_source_material's own real evidence text as
    # unverified_entry_sources purely from the mismatch, which would have defeated the new
    # deterministic-fallback salvage (engine/completion.py) on the exact real content it exists to
    # rescue. ---
    assert extract_cited_urls("**Source URL**  \n`https://example.com/page`") == [
        "https://example.com/page"
    ], "trailing backtick from inline-code citation style must not survive extraction"

    # --- percent-encoded citation of a genuinely-fetched non-ASCII URL (live case 2026-07-26,
    # real production run: "explain the main theories for the extinction of the dinosaurs" --
    # `fetch_url_to_workspace` records the fetched URL with its raw Unicode en-dash
    # ('Cretaceous–Paleogene_extinction_event'), but the model wrote the citation back
    # percent-encoded ('Cretaceous%E2%80%93Paleogene_extinction_event'). The old exact-string/
    # rstrip('/') comparison in real_grounding_problem treated these as two different URLs,
    # false-flagged a real, correctly-cited finding as hallucinated, and excluded it from
    # findings.md via _is_citable_finding -- the run's only two substantive findings both hit
    # this, and the final report came back with zero citable content: "No extractable findings
    # were identified in any of these sources." despite 27 real sources fetched.) ---
    import asyncio as _asyncio_pct
    from utils.grounding import real_grounding_problem as _real_grounding_problem_pct

    async def _percent_encoded_citation_scenario():
        reset_fetched_urls()
        record_fetched_url("https://en.wikipedia.org/wiki/Cretaceous–Paleogene_extinction_event",
                            "cretaceous_paleogene.md")
        content = ("- **[Wikipedia – Cretaceous–Paleogene extinction event]"
                   "(https://en.wikipedia.org/wiki/Cretaceous%E2%80%93Paleogene_extinction_event)**")
        return await _real_grounding_problem_pct(content)

    assert _asyncio_pct.run(_percent_encoded_citation_scenario()) is None, (
        "a percent-encoded citation of an actually-fetched non-ASCII URL must not be flagged as unverified")

    # --- URL case-sensitivity false-positive grounding rejection (live case 2026-07-29, Ornith-1.0-9B
    # run: a well-formed, correctly self-corrected final_report.md draft was rejected by
    # check_not_grounded solely because it cited '.../Public_holidays_in_colombia' (lowercase c)
    # against an actually-fetched '.../Public_holidays_in_Colombia' (capital C), forcing another
    # full rewrite cycle for a formatting artifact, not a real grounding failure. Per RFC 3986
    # §6.2.2.1, path IS case-sensitive by spec -- this fallback is a deliberate, narrow exception
    # for THIS fetched-vs-cited comparison only, not a general "URLs are case-insensitive" claim,
    # so a negative case is pinned alongside the positive one. ---
    async def _case_mismatch_citation_scenario():
        reset_fetched_urls()
        record_fetched_url("https://en.wikipedia.org/wiki/Public_holidays_in_Colombia",
                            "colombia_holidays.md")
        content = "- **[Public holidays in Colombia](https://en.wikipedia.org/wiki/Public_holidays_in_colombia)**"
        return await _real_grounding_problem_pct(content)

    assert _asyncio_pct.run(_case_mismatch_citation_scenario()) is None, (
        "a citation differing from the actually-fetched URL only by path case must not be "
        "flagged as unverified -- the fetch is real, only the model's cited casing drifted")

    async def _case_distinct_paths_scenario():
        # Negative case: two DIFFERENT real pages that happen to case-collide must NOT be treated
        # as equivalent -- the case-insensitive fallback must only rescue a genuinely-fetched URL,
        # never wave through a citation to a page that was never fetched at all.
        reset_fetched_urls()
        record_fetched_url("https://example.com/wiki/Article_One", "article_one.md")
        content = "- **[Fabricated](https://example.com/wiki/article_two)**"
        return await _real_grounding_problem_pct(content)

    assert _asyncio_pct.run(_case_distinct_paths_scenario()) is not None, (
        "a citation to a genuinely different (never-fetched) path must still be flagged, even if "
        "it happens to case-collide with something else -- the case-insensitive fallback must not "
        "make structurally different paths equivalent"
    )

    # --- stub-fetch detection (live case run 14: a model-invented URL answered by a 200
    # soft-404 — 5KB of subscription chrome — was recorded as a real fetch and passed the
    # hard URL gate) ---
    from tools.web import _stub_reason

    chrome = "\n".join(["[SUSCRÍBETE](https://news.example.co/sub)", "Inicia sesión",
                        "Noticias", "Deportes", "Política"] * 20)
    assert _stub_reason(chrome), "paywall chrome must flag as stub"
    assert _stub_reason("") == "empty page"
    assert _stub_reason("Página no encontrada\n\nError 404"), "tiny not-found page must flag"
    assert _stub_reason("Just a title\n\nAnd one short line."), "near-zero prose must flag"
    _para = ("Colombian exporters shipped record volumes of coffee and flowers this quarter "
             "according to the trade ministry figures released on Tuesday, with analysts "
             "noting sustained demand across European and North American markets overall.")
    real_article = "\n\n".join([_para] * 6 + ["Subscribe to our newsletter for updates"])
    assert _stub_reason(real_article) is None, "real prose mentioning 'subscribe' must NOT flag"

    # Adobe-Analytics-style tracking JS chrome (live gap, 2026-07-26:
    # sources/sciencedirect_kpg_age_2016.md was almost entirely this shape and scored above the
    # prose threshold, never flagged as a stub) — long assignment/JSON-key lines must not count
    # as prose even though they split into 15+ whitespace-separated "words".
    tracking_js = "\n".join([
        's.pageName = "Article Page"; s.channel = "science-direct"; s.prop1 = "elsevier"; '
        's.prop2 = "kpg-age-2016"; s.eVar1 = "segment-a"; s.eVar2 = "logged-out"; '
        's.events = "event1,event2,event3"; s.linkTrackVars = "prop1,prop2,eVar1,eVar2,events";'
    ] * 15)
    assert _stub_reason(tracking_js), "Adobe-Analytics-style tracking JS chrome must flag as stub"
    # Dialogue with a semicolon and a quote must not false-positive as code (only 1 token, not 3+).
    dialogue_para = ("\"Overall demand held up,\" she said; exports remained strong across every "
                      "major destination market this quarter according to ministry figures.")
    assert _stub_reason("\n\n".join([dialogue_para] * 8)) is None, (
        "real prose with a semicolon and a quote must NOT be treated as a code/tracking line")

    # --- _is_null_finding_summary (2026-08-01, RESEARCH.md Sec.16): a fetch that came back
    # stub/empty still gets a real findings.md entry, the Analyzer/Searcher just narrates that it
    # found nothing -- two-signal (short AND phrase-matched) so a real terse finding or a long
    # passage that happens to mention "could not find" isn't misclassified. ---
    assert _is_null_finding_summary(None)
    assert _is_null_finding_summary("")
    assert _is_null_finding_summary("   ")
    assert _is_null_finding_summary("No key findings extracted from this source during this research run.")
    assert _is_null_finding_summary("I was unable to find any relevant information on this page.")
    assert _is_null_finding_summary("Could not extract any useful data from the fetched content.")
    assert not _is_null_finding_summary(
        "Bolt's WR is 9.58s, set 16 Sep 2009 in Berlin, wind +1.0 m/s."
    ), "a real, if terse, finding must not be classified as null just for being short"
    _long_but_mentions_phrase = (
        "The Portugal D8 visa requires a minimum monthly income of EUR 3,680 and savings of "
        "EUR 11,040, with a rental agreement of at least four months for the temporary stay "
        "option. Processing time is approximately 90 working days. We could not find the exact "
        "AIMA office address, but every other requirement above is fully documented on the page."
    )
    assert not _is_null_finding_summary(_long_but_mentions_phrase), (
        "a long passage with real content must not be excluded just because it also mentions "
        "'could not find' about one minor sub-detail -- length is the second required signal"
    )

    # --- quota-blocked narration (2026-08-17, session_status 2026-08-16 item 3 follow-up, live
    # incident): a Searcher-tier dispatch that hits its OWN delegate_tasks quota before handing a
    # fetched file to an Analyzer narrates the block instead of a real finding -- no length gate,
    # since mentioning this project's own internal tool/role vocabulary is itself the safe signal. ---
    _quota_blocked_real_text = (
        "**Status Update – Research Task \"visa_requirements_mexico_city\"**\n\n"
        "I have completed the initial web-search phase and fetched three relevant documents into "
        "the workspace.\n\n**Next Step (Blocked by Quota)**\nThe next step would be to delegate "
        "each file to the appropriate Analyzer (DocumentAnalyzer) for a detailed extraction of key "
        "findings. However, I have already used the maximum allowed number of delegate_tasks calls "
        "for this task (6). The system has rejected any further delegation attempts.\n\n"
        "**Conclusion**\nI am unable to proceed with Analyzer processing due to the quota limit."
    )
    assert _is_null_finding_summary(_quota_blocked_real_text), (
        "a long multi-paragraph status report that never actually extracted anything -- just "
        "narrated its own delegate_tasks quota block -- must be excluded despite being way over "
        "the 300-char length gate"
    )
    assert not _is_null_finding_summary(_long_but_mentions_phrase), (
        "a real finding about an external topic must not be misclassified just because the quota-"
        "blocked regex exists -- it mentions none of this project's own internal tool vocabulary"
    )

    # --- context-budget cutoff null admission (2026-08-29, live incident): a sub-agent fetched 5
    # real sources but hit context_budget_chars before reading them, then narrated a false "found
    # nothing" -- padded past the 300-char gate by the engine-inserted banner itself plus injected
    # FOLLOW-UP DIRECTIONS text. No length gate needed here either, same reasoning as the
    # quota-blocked case: the bracketed banner is engine-inserted, never something the model's own
    # prose could produce incidentally. ---
    _context_budget_null_real_text = (
        "\n\n[SYSTEM: task 'Japan_renewable_energy_policy_2026_regulation' reached its context "
        "budget after gathering 5 real source(s) -- synthesis was wrapped up at that point. This "
        "is NOT necessarily incomplete; only dispatch a follow-up if a specific, named gap is "
        "evident, not as a default reaction to this marker.]I was unable to retrieve any "
        "authoritative or semi-authoritative web pages on the specific renewable-energy adoption "
        "regulation in Japan for 2026 before reaching the context budget limit, so I cannot "
        "provide concrete source URLs or exact figures at this time.\n\n**FOLLOW-UP DIRECTIONS**\n\n"
        "- Search the official Japanese Ministry of Economy, Trade and Industry (METI) website "
        "for regulations that reference a 2026 update.\n"
        "- Verify the name, date of enactment, and key provisions by locating the full text of "
        "the law or its official summary on a government portal."
    )
    assert _is_null_finding_summary(_context_budget_null_real_text), (
        "a context-budget cutoff whose own narration admits it found nothing must be excluded "
        "despite being padded well over the 300-char length gate by the banner and follow-up "
        "directions text"
    )
    _context_budget_real_content = (
        "\n\n[SYSTEM: task 'Germany_renewable_energy_policy_2026' reached its context budget "
        "after gathering 5 real source(s) -- synthesis was wrapped up at that point. This is NOT "
        "necessarily incomplete; only dispatch a follow-up if a specific, named gap is evident, "
        "not as a default reaction to this marker.]Germany's EEG funding regime expires at the "
        "end of 2026. The market premium under Section 20 EEG applies from 1 August 2026 to 31 "
        "January 2027, and the Mieterstromzuschlag rent-to-home subsidy under Section 21(3) EEG "
        "runs the same period."
    )
    assert not _is_null_finding_summary(_context_budget_real_content), (
        "a context-budget cutoff whose narration is genuine substantive content, not a null "
        "admission, must not be blanket-excluded just because the cutoff banner is present"
    )

    def _stub_gate_scenario():
        _orig_ws5 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        _config.cfg["settings"]["grounding_check"] = {"stub_detection": True, "live_http_verify": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://news.example.co/paywalled", filename="sources/stub.md",
                               stub="paywall marker")
            _IN_MEMORY_FS["sources/stub.md"] = "Source-URL: https://news.example.co/paywalled\n\nSUSCRÍBETE"
            report = "- dato [news](https://news.example.co/paywalled)"
            problem = _asyncio.run(real_grounding_problem(report))
            assert problem and problem.startswith("stub_source"), problem
            # Flag off -> the stub gate stands down (stub content still can't ground claims,
            # via _fetched_url_files' exclusion).
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = False
            assert _asyncio.run(real_grounding_problem(report)) is None
            _config.cfg["settings"]["grounding_check"]["stub_detection"] = True
            # Same URL later fetched for real (retry got the actual page) -> citation valid.
            record_fetched_url("https://news.example.co/paywalled", filename="sources/real.md")
            _IN_MEMORY_FS["sources/real.md"] = "Source-URL: https://news.example.co/paywalled\n\n" + _para
            problem2 = _asyncio.run(real_grounding_problem(report))
            assert not (problem2 or "").startswith("stub_source"), problem2
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws5 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws5
            if _orig_gc is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc

    contextvars.copy_context().run(_stub_gate_scenario)

    # --- rag_cache grounding exemption (2026-09-09 QA audit fix): search_verified_findings' own
    # instructions tell the Searcher to cite a cache hit's source_url directly without a fresh
    # fetch. Before this fix, every one of the three citation-verification gates
    # (real_grounding_problem, fully_ungrounded, partially_ungrounded) flagged that exact citation
    # as hallucinated, since it was never registered in fetched_urls. Confirms the URL is rejected
    # BEFORE record_verified_cache_url (proving the test is meaningful, not vacuous) and accepted
    # by all three gates after it. ---
    def _rag_cache_grounding_exemption_scenario():
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc6 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        # Isolate to just the URL-presence gate -- no NLI/topical/editorial model inference noise.
        _config.cfg["settings"]["grounding_check"] = {
            "live_http_verify": False, "nli_verify": False, "topical_relevance_check": False,
            "editorial_detection_check": False,
        }
        saved_fs = dict(_IN_MEMORY_FS)
        cache_url = "https://releases.rs/"
        report = f"- Rust 1.97.1 is the current stable release. [releases]({cache_url})"
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()

            # Never fetched this run, never cache-verified either -> all three gates must reject.
            problem = _asyncio.run(real_grounding_problem(report))
            assert problem and "unverified_urls" in problem, problem
            assert fully_ungrounded(report) == "all_cited_urls_unverified", fully_ungrounded(report)
            assert partially_ungrounded(report) is not None, partially_ungrounded(report)

            # Register it as a rag_cache hit search_verified_findings actually surfaced this run --
            # still never fetched, but now legitimately pre-verified.
            record_verified_cache_url(cache_url)

            problem2 = _asyncio.run(real_grounding_problem(report))
            assert problem2 is None, (
                f"a rag-cache-verified citation must pass real_grounding_problem, got {problem2!r}"
            )
            assert fully_ungrounded(report) is None, (
                "a rag-cache-verified citation must pass fully_ungrounded"
            )
            assert partially_ungrounded(report) is None, (
                "a rag-cache-verified citation must pass partially_ungrounded"
            )
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            verified_cache_urls_ctx.set([])
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc6 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc6

    contextvars.copy_context().run(_rag_cache_grounding_exemption_scenario)

    # --- URL prefix-match boundary (2026-07-12 audit G1: a genuinely fetched .../article
    # grounded an invented .../article-fake-2024 via bare string-prefixing) ---
    from utils.grounding import _urls_prefix_match

    assert not _urls_prefix_match("https://real.com/article-fake-2024", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article?utm=1", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article#s2", "https://real.com/article")
    assert _urls_prefix_match("https://real.com/article/annex", "https://real.com/article")
    # Bare-origin rule unchanged: a domain root never prefix-grounds a deep link.
    assert not _urls_prefix_match("https://real.com/deep/link", "https://real.com")

    # --- grounding_check.enabled master switch honored (2026-07-12 audit G2: the template
    # shipped it but nothing read it — an unhonored kill switch) ---
    def _enabled_off_scenario():
        from tools.core import tool_quotas_ctx as q_ctx
        _orig_ws6 = _config.cfg.get("settings", {}).get("workspace")
        _orig_gc2 = _config.cfg.get("settings", {}).get("grounding_check")
        _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
        _config.cfg["settings"]["grounding_check"] = {"enabled": False}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            # findings.md fabricated AND the report cites a never-fetched URL — with the master
            # switch off, neither grounding gate may fire (structural checks still pass: the
            # run delegated and both artifacts exist).
            _IN_MEMORY_FS["findings.md"] = "- todo de memoria, sin fuente"
            _IN_MEMORY_FS["final_report.md"] = "- x [g](https://never-fetched.example.com/y)"
            q_ctx.set({"delegate_tasks": {"used": 1, "limit": 5}})
            rs = RunState(tempfile.gettempdir())
            run_state_ctx.set(rs)
            msgs = []
            should_retry, _ = _asyncio.run(run_completion_check(
                query="q", current_input="q", run_state=rs, notify=msgs.append))
            recorded = rs.data["completion_check_attempts"][-1]["problem"]
            assert recorded is None and not should_retry, (recorded, should_retry, msgs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws6 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws6
            if _orig_gc2 is None:
                _config.cfg["settings"].pop("grounding_check", None)
            else:
                _config.cfg["settings"]["grounding_check"] = _orig_gc2

    contextvars.copy_context().run(_enabled_off_scenario)

    # --- A3 citation-format layer (run 14's format half: table + detached '### Source URLs') ---
    from utils.grounding import split_prose_from_sources

    # The detached-source-section heading variants must be stripped from prose...
    for heading in ("### Source URLs", "## Sources", "**Fuentes:**", "## Fuentes consultadas",
                    "References used:"):
        rep = f"- claim line\n{heading}\nhttps://x.co/a"
        assert "x.co" not in split_prose_from_sources(rep), heading
    # ...but a real content heading that merely starts with the word must NOT be.
    kept = split_prose_from_sources("## Sources of growth in Colombia\nhttps://x.co/a")
    assert "x.co" in kept

    _table_report = ("| Sector | Valor |\n"
                     "| Fintech | USD 3.5 mil millones en el mercado local en 2024 |\n"
                     "| Agro | 12% de crecimiento anual en exportaciones regionales |\n"
                     "| Salud | 2.300 empresas registradas en el sector durante 2023 |\n"
                     "\n### Source URLs\n- https://gov.example.co/page\n")
    assert len(find_uncited_claim_lines(_table_report)) >= 3
    # Properly formatted claim+citation lines never count, nor do short/heading/separator lines.
    _good_report = ("# Informe\n"
                    "- **[Fintech](https://gov.example.co/page)** USD 3.5 mil millones en 2024\n"
                    "- **[Agro](https://gov.example.co/page)** 12% de crecimiento anual\n"
                    "|---|---|\n")
    assert find_uncited_claim_lines(_good_report) == []
    # Run 15's live FALSE POSITIVE (2026-07-12): a per-niche '#### Sources' block under each
    # h3 section ties that section's claims to sources — held a correctly-grounded report
    # through 3 nudges. h4+ blocks must survive split_prose_from_sources, and a section
    # containing a URL exempts its own figure lines.
    _sectioned_report = (
        "## Research Objective\n"
        "Identify B2B technology opportunities in Colombia for 2026 where a small team could generate revenue.\n\n"
        "### 1. Cattle Traceability\n"
        "| **Regulation** | Ley 2585 de 2026 — trazabilidad ganadera obligatoria |\n"
        "| **Compliance Deadline** | Enacted 4 June 2026; implementation required by that date |\n"
        "| **Market Size** | 2.300 productores registrados en el sistema en 2025 |\n"
        "#### Sources\n"
        "- **[Ley 2585 de 2026](https://sidn.example.gov.co/ley_2585)**\n")
    assert "sidn.example.gov.co" in split_prose_from_sources(_sectioned_report)
    _hits = find_uncited_claim_lines(_sectioned_report)
    assert len(_hits) < 3 and not any("2585" in h for h in _hits), _hits

    # 2026-08-24 live incident: find_uncited_claim_lines' academic exemption used the strict
    # fully-parenthesized _PARENTHETICAL_CITATION_RE, which doesn't recognize two equally
    # legitimate academic citation shapes -- confirmed live, a real --style academic report's
    # Introduction (narrative style) and its own benchmarking table (bare table-cell style) both
    # got flagged as uncited despite clearly citing a real, resolvable reference, unchanged across
    # 3 consecutive completion-check attempts. Widened to _ACADEMIC_CITATION_ANYWHERE_RE (parens
    # now optional) -- these two cases must now be exempt.
    _narrative_citation_report = (
        "## Introduction\n"
        "The Transformer architecture introduced by Vaswani et al. (2017) eliminated recurrence "
        "and convolution from sequence models entirely.\n")
    assert find_uncited_claim_lines(_narrative_citation_report) == [], (
        "a narrative-style citation (author outside the parens, only the year inside) must "
        "exempt its section, same as the full (Author, Year) parenthetical form")

    _table_cell_citation_report = (
        "## Quantitative Benchmarking Summary\n"
        "| Model | Task | Metric | Value | Source |\n"
        "|-------|------|--------|-------|--------|\n"
        "| Transformer (original) | WMT 2014 En->De | BLEU | 28.4 | Vaswani et al., 2017 |\n")
    assert find_uncited_claim_lines(_table_cell_citation_report) == [], (
        "a bare table-cell citation (no parens at all, in a Source column) must exempt its "
        "section too")

    # A genuinely uncited section must still be caught -- this exemption only widened WHICH
    # citation shapes count, it must not make the check unable to catch a real run-14-shaped gap.
    assert len(find_uncited_claim_lines(_table_report)) >= 3, (
        "widening the academic-citation exemption must not stop catching a genuinely detached, "
        "uncited table (run 14's original shape)")

    # --- context-budget guard: stream char accounting (settings.context_budget_chars) ---
    from engine.orchestrator import stream_content_chars, get_context_budget

    class _C:
        def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
    class _U:
        def __init__(self, contents): self.contents = contents

    assert stream_content_chars(_U([_C(text="abcde")])) == 5
    assert stream_content_chars(_U([_C(arguments='{"q":1}'), _C(result="xyz")])) == 10
    assert stream_content_chars(_U([_C(result=12345)])) == 5   # non-str result stringified
    assert stream_content_chars(_U([])) == 0
    _orig_cb = _config.cfg.get("settings", {}).get("context_budget_chars")
    try:
        _config.cfg["settings"]["context_budget_chars"] = 50000
        assert get_context_budget() == 50000
        _config.cfg["settings"]["context_budget_chars"] = 0
        assert get_context_budget() == 0
        _config.cfg["settings"].pop("context_budget_chars")
        assert get_context_budget() == 0  # absent = off
    finally:
        if _orig_cb is None:
            _config.cfg["settings"].pop("context_budget_chars", None)
        else:
            _config.cfg["settings"]["context_budget_chars"] = _orig_cb

    # --- role-aware budget nudge (2026-07-21, live-caught): SUBAGENT_BUDGET_NUDGE's "do NOT call
    # any more tools, return as your final message" is correct for Searcher/Analyzer roles but
    # directly CAUSES the narrate-instead-of-write bug for writer roles (Builder/FindingsWriter),
    # whose entire success criterion IS calling write_workspace_file. Live case: a 25-finding run
    # pushed FindingsWriter's own generation over budget, the old nudge told it to stop calling
    # tools and narrate instead, and the narration itself then got salvaged as an unverified,
    # truncated draft (3 of 25 real findings) instead of a real file ever being written. ---
    from engine.orchestrator import (
        _select_budget_nudge, SUBAGENT_BUDGET_NUDGE, SUBAGENT_BUDGET_NUDGE_WRITER,
    )

    assert _select_budget_nudge("FindingsWriter") == SUBAGENT_BUDGET_NUDGE_WRITER
    assert _select_budget_nudge("Builder") == SUBAGENT_BUDGET_NUDGE_WRITER
    assert _select_budget_nudge("PeerReviewer") == SUBAGENT_BUDGET_NUDGE
    assert _select_budget_nudge("WebSearcher") == SUBAGENT_BUDGET_NUDGE
    assert _select_budget_nudge(None) == SUBAGENT_BUDGET_NUDGE
    # The writer nudge must actually tell the model to call write_workspace_file, not to narrate —
    # pin the actual behavioral difference, not just object identity.
    assert "write_workspace_file" in SUBAGENT_BUDGET_NUDGE_WRITER
    assert "do not call any more tools" not in SUBAGENT_BUDGET_NUDGE_WRITER.lower()

    # --- zero-synthesis retry predicate (2026-08-19, live incident via the ablation study: a
    # Searcher/Analyzer dispatch calls a tool then ends its turn with zero narration, permanently
    # landing a real fetched source with an empty finding summary; _run_single_task itself is an
    # untestable nested closure, so the trigger logic was extracted to _should_nudge_zero_synthesis
    # the same way _select_budget_nudge was). ---
    from engine.orchestrator import _should_nudge_zero_synthesis, SEARCHER_ANALYZER_SYNTHESIS_NUDGE

    # Fires: research-tier role, a tool was called, zero text produced, nothing else already
    # claimed this turn, not already nudged once.
    assert _should_nudge_zero_synthesis("WebSearcher", True, "", False, False)
    assert _should_nudge_zero_synthesis("DataAnalyzer", True, "   ", False, False)  # whitespace-only counts as empty
    assert _should_nudge_zero_synthesis(None, True, "", False, False)  # unrecognized agent_id still research-tier

    # Does NOT fire: no tool was called at all (a different problem, not this bug).
    assert not _should_nudge_zero_synthesis("WebSearcher", False, "", False, False)
    # Does NOT fire: real text was produced.
    assert not _should_nudge_zero_synthesis("WebSearcher", True, "some findings text", False, False)
    # Does NOT fire: something else already claimed another turn this iteration.
    assert not _should_nudge_zero_synthesis("WebSearcher", True, "", False, True)
    # Does NOT fire: already used its one nudge for this dispatch.
    assert not _should_nudge_zero_synthesis("WebSearcher", True, "", True, False)
    # Does NOT fire: writer roles have their own dedicated retry mechanism.
    assert not _should_nudge_zero_synthesis("Builder", True, "", False, False)
    assert not _should_nudge_zero_synthesis("FindingsWriter", True, "", False, False)
    assert not _should_nudge_zero_synthesis("PeerReviewer", True, "", False, False)

    assert "findings" in SEARCHER_ANALYZER_SYNTHESIS_NUDGE.lower()

    # --- C8 charset handling (run 14: the flagship 750KB DIAN law text was saved as mojibake —
    # 'Resolución'/'número' could never string-match, silently gutting every Spanish-term check) ---
    from tools.web import _decode_html_bytes, _strip_boilerplate_html, _meta_declared_encoding

    _latin_body = "<html><body><p>Resolución número 000042 de la DIAN sobre facturación electrónica en Colombia y sus efectos.</p></body></html>"
    # Charset only in the HTTP header
    assert "Resolución número" in _decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1")
    # Charset only in the document's own meta tag
    _latin_meta = ('<html><head><meta charset="iso-8859-1"></head><body>'
                   "<p>Resolución número 000042 de la DIAN.</p></body></html>").encode("latin-1")
    assert _meta_declared_encoding(_latin_meta) == "iso-8859-1"
    assert "Resolución número" in _decode_html_bytes(_latin_meta, None)
    # A LYING latin-1 header must not corrupt a UTF-8 page (strict UTF-8 self-validates first)
    assert "Resolución número" in _decode_html_bytes("<p>Resolución número</p>".encode("utf-8"), "iso-8859-1")
    # No declaration anywhere: cp1252 fallback still yields the accents, never mojibake
    assert "Resolución" in _decode_html_bytes("<p>Resolución</p>".encode("latin-1"), None)
    # The stale meta tag must not survive into the cleaned UTF-8 bytes (markitdown would honor
    # it and re-mojibake the content), and the full markitdown round trip must keep the accents.
    _cleaned, _cleaned_meta = _strip_boilerplate_html(_decode_html_bytes(_latin_meta, None))
    assert b"iso-8859-1" not in _cleaned and _cleaned.startswith(b'<meta charset="utf-8">')
    assert _cleaned_meta == {}, _cleaned_meta  # no <title>/meta author/date in this fixture
    from utils.parsers import convert_to_markdown
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="wb") as _tmp_html:
        _tmp_html_bytes, _ = _strip_boilerplate_html(_decode_html_bytes(_latin_body.encode("latin-1"), "iso-8859-1"))
        _tmp_html.write(_tmp_html_bytes)
        _tmp_html_path = _tmp_html.name
    try:
        _md = convert_to_markdown(_tmp_html_path)
        assert _md and "Resolución número 000042" in _md, _md
    finally:
        os.unlink(_tmp_html_path)

    # --- line-scoped claim grounding (review #2 item 4): the old WHOLE-report term overlap let
    # generic shared terms mask per-claim fabrication — run 12's flagship figure was absent from
    # its cited source but passed because other lines shared terms with that same source. ---

    # --- Phase 1.1/1.2 of the ROADMAP "Claim-level grounding upgrade": decompose_claim_segments
    # (pure segmentation, no fetched-source dependency) ---
    assert decompose_claim_segments("- Cacao: USD 265.1M [gov](https://x.co/a)") == [
        "- Cacao: USD 265.1M [gov](https://x.co/a)"], "single-citation line must decompose to itself unchanged"
    assert decompose_claim_segments("plain text, no citation at all") == ["plain text, no citation at all"]
    _segs = decompose_claim_segments(
        "- Cacao: USD 265.1M [gov](https://x.co/a), mientras Software genero USD 3.5B [tech](https://x.co/b)")
    assert len(_segs) == 2, _segs
    assert _segs[0] == "- Cacao: USD 265.1M [gov](https://x.co/a)", _segs
    assert _segs[1] == ", mientras Software genero USD 3.5B [tech](https://x.co/b)", _segs
    # Trailing uncited text stays attached to the last segment rather than becoming an orphan.
    _segs_trail = decompose_claim_segments(
        "- A [x](https://x.co/a), B [y](https://x.co/b), and an uncited closing remark")
    assert len(_segs_trail) == 2 and _segs_trail[1].endswith("uncited closing remark"), _segs_trail

    def _line_claim_scenario():
        _orig_ws8 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://gov.example.co/exportaciones", filename="sources/exp.md")
            _IN_MEMORY_FS["sources/exp.md"] = (
                "Source-URL: https://gov.example.co/exportaciones\n\n"
                "Las exportaciones de cacao de Colombia alcanzaron USD 265.1 millones en 2024, "
                "segun cifras oficiales de la entidad nacional de estadistica.")
            supported_line = "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones)"
            # A line whose own figure appears in its cited source -> silent
            assert claim_grounding_problem(supported_line) is None
            # THE masking case: a supported line + a fabricated figure citing the SAME source.
            # The whole-report version passed this (265.1/2024 overlapped report-wide).
            problem = claim_grounding_problem(
                supported_line
                + "\n- Software: USD 3.5 mil millones en 2023 [gov](https://gov.example.co/exportaciones)")
            assert problem and problem.startswith("claim_unsupported"), problem
            # A line with no checkable terms of its own -> skipped, never flagged
            assert claim_grounding_problem(
                "- el sector crece de forma sostenida [gov](https://gov.example.co/exportaciones)") is None
            # Unfetched citation -> the hard URL gate's job, silent here
            assert claim_grounding_problem(
                "- USD 9.9 mil millones [x](https://never-fetched.example.com/a)") is None

            # THE SAME-LINE citation-sharing/drift case (ROADMAP Phase 1 target): two claims on
            # ONE line, each with its OWN distinct citation. The genuinely-supported cacao claim
            # must not let its citation's overlap "cover for" the second, fabricated claim whose
            # OWN cited source doesn't support it at all -- the exact gap the old whole-line
            # union check had (a shared generic phrase between the two sources would have masked
            # this before decompose_claim_segments existed).
            record_fetched_url("https://gov.example.co/agro", filename="sources/agro.md")
            _IN_MEMORY_FS["sources/agro.md"] = (
                "Source-URL: https://gov.example.co/agro\n\n"
                "El sector agropecuario crecio 8% en el primer trimestre de 2025, impulsado "
                "por la demanda internacional de cafe.")
            same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras Software genero USD 3.5 mil millones en 2023 [tech](https://gov.example.co/agro)")
            problem = claim_grounding_problem(same_line)
            assert problem and problem.startswith("claim_unsupported"), (
                "a same-line second claim citing a source that doesn't support it must be caught "
                "even though the FIRST claim on the same line is genuinely supported", problem)
            assert "agro" in problem, (
                "the flagged citation must be the second claim's OWN (unsupporting) source, not "
                "the first claim's genuinely-supporting one", problem)
            # Same shape, but BOTH claims genuinely supported by their own distinct sources -> silent.
            clean_same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras el agro crecio 8% en 2025 [gov](https://gov.example.co/agro)")
            assert claim_grounding_problem(clean_same_line) is None, (
                "two claims on one line, each genuinely supported by its own distinct citation, "
                "must not be flagged")
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws8 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws8

    contextvars.copy_context().run(_line_claim_scenario)

    # --- claim_grounding_problem must skip citation-only sub-bullets, not treat their own anchor
    # text as a checkable claim (live false positive, 2026-07-14, Eiffel Tower smoke test): a
    # genuinely-grounded report using "claim on one line, '- Source: [Title](url).' on the next"
    # (this project's own Builder output shape, distinct from the inline "[gov](url)" same-line
    # style tested above) burned its ENTIRE retry budget (6 consecutive claim_unsupported verdicts)
    # because extract_salient_terms pulled "Official Eiffel Tower" out of the citation's own
    # editorialized anchor text "[Official Eiffel Tower website](url)" and flagged it for not
    # appearing verbatim in the source -- even though the actual claims (verbatim figures) WERE
    # genuinely present in the fetched source. Root-caused directly against the real failing run's
    # saved report + fetched source (research_output/what_year_was_the_eiffel_tower_completed_
    # 20260714_215912/), reproduced here as a minimal regression case.
    #
    # 2026-07-19 QA audit: this scenario used to hand-retype an approximation of the real report/
    # source pair instead of loading it from disk, even though the real run directory still exists
    # in-repo -- the exact "fixture encodes the same assumption as the fix, not the real data"
    # pattern that shipped the 2026-07-19 findings.md bug. Now loads the ACTUAL final_report.md and
    # ACTUAL fetched source verbatim from that run directory. ---
    def _citation_only_subbullet_scenario():
        _orig_ws12 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        run_dir = os.path.join(
            os.path.dirname(__file__),
            "research_output", "what_year_was_the_eiffel_tower_completed_20260714_215912")
        report_path = os.path.join(run_dir, "final_report.md")
        source_path = os.path.join(run_dir, "sources", "toureiffel_paris_history.md")
        if not (os.path.exists(report_path) and os.path.exists(source_path)):
            # Real run directory not present in this checkout (e.g. a fresh clone without
            # research_output/ committed) -- skip rather than silently fall back to a synthetic
            # fixture, which is exactly the gap this rewrite exists to close.
            return
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            with open(report_path, encoding="utf-8") as f:
                report = f.read()
            with open(source_path, encoding="utf-8") as f:
                source_body = f.read()
            record_fetched_url("https://www.toureiffel.paris/en/the-monument/history", filename="sources/tour.md")
            _IN_MEMORY_FS["sources/tour.md"] = (
                "Source-URL: https://www.toureiffel.paris/en/the-monument/history\n\n" + source_body)
            assert claim_grounding_problem(report) is None, (
                "the REAL final_report.md that live-triggered this bug (2026-07-14) must not be "
                "flagged against its REAL fetched source, now that the citation-only sub-bullet "
                "fix is in place")
            # Same fix, same shape, ported to _grounded_claim_pairs -- must not surface the
            # citation-only sub-bullet as a (window, claim, display) pair either.
            from utils.grounding import _grounded_claim_pairs
            pairs = _grounded_claim_pairs(report)
            for _, claim_text, _display in pairs:
                assert claim_text.strip() != "- Source: [Official Eiffel Tower website]", (
                    "a bare citation sub-bullet must never surface as a claim pair", pairs)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws12 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws12

    contextvars.copy_context().run(_citation_only_subbullet_scenario)

    # --- _grounded_claim_pairs (shared by nli_unsupported_problem/topical_relevance_problem) must
    # be SEGMENT-scoped via decompose_claim_segments, same fix class already shipped for
    # claim_grounding_problem above (ROADMAP "Residual note" on the Phase 1 claim-level grounding
    # upgrade, closed 2026-07-14) — a same-line multi-claim case must not let one claim's window
    # get attributed to the wrong citation, or one claim's own evidence get diluted by the other
    # claim's terms. Pure-function test (no NLI model load needed; _grounded_claim_pairs itself has
    # no model dependency, only its two callers do). ---
    def _grounded_claim_pairs_scenario():
        from utils.grounding import _grounded_claim_pairs
        _orig_ws11 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            record_fetched_url("https://gov.example.co/exportaciones", filename="sources/exp.md")
            _IN_MEMORY_FS["sources/exp.md"] = (
                "Source-URL: https://gov.example.co/exportaciones\n\n"
                "Las exportaciones de cacao de Colombia alcanzaron USD 265.1 millones en 2024, "
                "segun cifras oficiales de la entidad nacional de estadistica.")
            record_fetched_url("https://gov.example.co/agro", filename="sources/agro.md")
            _IN_MEMORY_FS["sources/agro.md"] = (
                "Source-URL: https://gov.example.co/agro\n\n"
                "El sector agropecuario crecio 8.3% en el primer trimestre de 2025, impulsado "
                "por la demanda internacional de cafe.")
            # Both segments' shared checkable term is a genuine figure (265.1 / 8.3), not a bare
            # year -- "2024"/"2025" alone would no longer anchor a pair after the 2026-08-25 fix
            # that excludes bare-year-only matches from _grounded_claim_pairs (see that fix's own
            # comment above _select_relevant_window).
            same_line = (
                "- Cacao: USD 265.1 millones en 2024 [gov](https://gov.example.co/exportaciones), "
                "mientras el agro crecio 8.3% en 2025 [gov](https://gov.example.co/agro)")
            pairs = _grounded_claim_pairs(same_line)
            assert len(pairs) == 2, (
                "a same-line two-claim, two-citation report must yield two separate pairs, not one "
                "merged whole-line pair", pairs)
            by_display = {display: (window, claim) for window, claim, display in pairs}
            assert "https://gov.example.co/exportaciones" in by_display
            assert "https://gov.example.co/agro" in by_display
            cacao_window, cacao_claim = by_display["https://gov.example.co/exportaciones"]
            agro_window, agro_claim = by_display["https://gov.example.co/agro"]
            # Each claim segment's own text must not bleed into the other's -- the cacao claim
            # text must not contain "agro"/"cafe" and vice versa (the exact drift the whole-line
            # version was vulnerable to: one segment's terms diluting or misattributing evidence
            # meant for the other).
            assert "cacao" in cacao_claim.lower() and "agro" not in cacao_claim.lower(), cacao_claim
            assert "agro" in agro_claim.lower() and "cacao" not in agro_claim.lower(), agro_claim
            # Each window must come from ITS OWN cited source, not the other claim's.
            assert "cacao" in cacao_window.lower() or "exportaciones" in cacao_window.lower(), cacao_window
            assert "agropecuario" in agro_window.lower(), agro_window
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws11 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws11

    contextvars.copy_context().run(_grounded_claim_pairs_scenario)

    # --- _grounded_claim_pairs must not let a BARE YEAR (from anywhere -- the citation's own
    # attribution, or the claim's own prose) anchor NLI evidence-window selection (2026-08-25 live
    # incident, a real --style academic BERT report). extract_salient_terms treats any bare
    # 4-digit year as salient, but a paper's own publication year is scattered through its
    # citation-metadata boilerplate (Anthology ID/Volume/Month/Year/BibTeX/MODS/RIS export blocks)
    # far more densely than through its actual substance -- a real Abstract paragraph routinely
    # never restates its own bare publication year at all. A year-only anchor therefore
    # systematically picks the wrong (boilerplate) window, and the NLI model judges a genuinely
    # accurate claim against a wholly unrelated premise. Confirmed live on 4 separate claims in
    # one real run: 3 anchored only by the citation's embedded year, 1 anchored by the claim's OWN
    # "in 2019..." prose -- both shapes are covered here, plus a positive control confirming a
    # claim with a REAL distinguishing term (not just a year) still produces a checkable pair. ---
    def _grounded_claim_pairs_bare_year_scenario():
        from utils.grounding import _grounded_claim_pairs, parse_academic_references
        _orig_ws13 = _config.cfg.get("settings", {}).get("workspace")
        _config.cfg["settings"]["workspace"] = {"type": "memory"}
        saved_fs = dict(_IN_MEMORY_FS)
        try:
            _IN_MEMORY_FS.clear()
            reset_fetched_urls()
            _src = "https://aclanthology.example.co/N19-1423"
            record_fetched_url(_src, filename="sources/bert.md")
            # Mirrors the real ACL Anthology page's shape: several metadata paragraphs repeating
            # "2019" many times, and a separate Abstract paragraph that never mentions the bare
            # year at all but DOES mention a real, distinguishing figure ("80.5").
            _IN_MEMORY_FS["sources/bert.md"] = (
                "Source-URL: " + _src + "\n\n"
                "Anthology ID: N19-1423\n\nVolume: Proceedings of the 2019 Conference\n\n"
                "Year: 2019\n\nMonth: June 2019\n\nAddress: Minneapolis 2019\n\n"
                "Abstract: We introduce a new language representation model called BERT, "
                "pushing the GLUE score to 80.5. BERT is designed to pre-train deep "
                "bidirectional representations from unlabeled text by jointly conditioning on "
                "both left and right context."
            )
            # References section comes AFTER the claim prose -- same shape every real report
            # uses, and required here: split_prose_from_sources strips everything FROM the first
            # References/Sources heading ONWARD, so a References block placed first would wipe
            # out the claim sentence entirely, and every case below would trivially pass with an
            # empty pairs list for the wrong reason (a fixture-ordering bug caught by the positive
            # control below failing loudly instead of silently).
            references = f"\n\n### References\n1. Devlin, J. (2019). BERT. {_src}\n"

            # Case 1: claim's only salient term comes from the CITATION's own embedded year.
            report_citation_year = (
                "BERT employs a stack of Transformer encoder layers that process the entire "
                "input sequence simultaneously (Devlin, 2019)."
            ) + references
            assert parse_academic_references(report_citation_year), "fixture References must resolve"
            pairs = _grounded_claim_pairs(report_citation_year)
            assert pairs == [], (
                "a claim whose ONLY salient term is its own citation's embedded year must yield "
                "zero pairs (skipped from NLI checking), not a window picked from unrelated "
                "citation-metadata boilerplate", pairs)

            # Case 2: claim's only salient term comes from its OWN prose, not the citation --
            # the citation-stripping fix alone cannot touch this shape; the general bare-year
            # exclusion is what closes it.
            report_prose_year = (
                "The release of BERT in 2019 marked a significant shift toward deep "
                "bidirectional language models (Devlin et al., 2019)."
            ) + references
            pairs = _grounded_claim_pairs(report_prose_year)
            assert pairs == [], (
                "a claim anchored only by a bare year in its OWN prose (not the citation) must "
                "also yield zero pairs", pairs)

            # Positive control: a claim with a REAL distinguishing term (the figure "80.5", which
            # genuinely and uniquely appears in the Abstract) must still produce a checkable pair
            # against the correct window -- the fix must not over-suppress legitimate matches.
            report_real_term = (
                "BERT achieved a GLUE score of 80.5 (Devlin et al., 2019)."
            ) + references
            pairs = _grounded_claim_pairs(report_real_term)
            assert len(pairs) == 1, ("a claim with a real distinguishing term must still be "
                                      "checked", pairs)
            window, claim_text, _display = pairs[0]
            assert "80.5" in window, ("the selected window must be the Abstract (which mentions "
                                       "80.5), not the year-heavy metadata block", window)
        finally:
            _IN_MEMORY_FS.clear()
            _IN_MEMORY_FS.update(saved_fs)
            reset_fetched_urls()
            if _orig_ws13 is None:
                _config.cfg["settings"].pop("workspace", None)
            else:
                _config.cfg["settings"]["workspace"] = _orig_ws13

    contextvars.copy_context().run(_grounded_claim_pairs_bare_year_scenario)



if __name__ == "__main__":
    main()
    print("test_grounding_citation_details OK")
