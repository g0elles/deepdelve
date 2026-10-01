import sys
sys.path.insert(0, "src")
from pipeline.run import quote_in_source, chunks

SRC = "Japan's FIT Act was enacted in 2011.\n  It took effect  in July 2012, after the disaster."


def test_quote_match():
    assert quote_in_source("It took effect in July 2012, after the disaster.", SRC)  # whitespace-insensitive
    assert not quote_in_source("It took effect in July 2013, after the disaster.", SRC)  # altered fact
    assert not quote_in_source("too short", SRC)  # below min length


def test_chunks_capped():
    assert len(chunks("x" * 100000)) == 4


def test_markdown_markup_ignored():
    src = 'system for most [technologies](https://x.org/t "tech") which finished with EEG 2017.[[3]](#cite_note-4) The EEG first'
    assert quote_in_source("system for most technologies which finished with EEG 2017.", src)
    assert not quote_in_source("system for most technologies which finished with EEG 2018.", src)


def test_bm25_sentences_are_literal():
    from pipeline.run import sentences, bm25_top
    src = ("Intro line.\n\nThe [Renewable Energy Sources Act](https://x/eeg \"EEG\") introduced feed-in tariffs in 2000.[[1]](#c1) "
           "Cats are small domestic animals that enjoy sleeping in the afternoon sun.\n")
    top = bm25_top(sentences(src), "feed-in tariffs Renewable Energy Sources Act", 1)
    assert top and "feed-in tariffs in 2000" in top[0][1]
    assert quote_in_source(top[0][1], src)  # candidate is always a literal quote


def test_cited_lines_parse():
    from pipeline.dataset import cited_lines
    assert cited_lines("- fact (lines 6‑7)") == {6, 7}
    assert cited_lines("- fact (line 4) and more (lines 10-11)") == {4, 10, 11}
    assert cited_lines("- fact with no citation") == set()


def test_link_and_crossref_residue_dropped():
    from pipeline.run import sentences
    src = "Results are summarized in Table 1 \u2023 Lethe \u2023 Lethe: Layer-Adaptive KV Cache Pruning for LLM Serving. Real sentence about caches that is long enough to pass the filter here. See the rules...](/en/geo/1) for details on the rules of export."
    assert sentences(src) == ["Real sentence about caches that is long enough to pass the filter here."]


def test_bibliography_lines_dropped():
    from pipeline.run import sentences
    src = "Topham (1987), 'Benefit-Cost Rules for Urban Transit Subsidies', Journal of Transport Economics and Policy, 21(1), pp. 15-30. The 2010 decree created the federal data protection law for private parties."
    assert sentences(src) == ["The 2010 decree created the federal data protection law for private parties."]


def test_entity_and_specificity():
    from pipeline.run import facet_entities, entity_ok, specificity, query_entities
    q = "Average commute times in Bogota, Lima and UK cities"
    assert query_entities(q) == {"bogota", "lima", "uk"}
    assert query_entities("What does Kenya's Data Protection Act 2019 require?") == {"kenya"}  # title words aren't entities; the country regex (d2c579c) still finds Kenya
    assert {"mexico", "chile"} <= query_entities("\u00bfC\u00f3mo se regula en M\u00e9xico y Chile?")  # also carries regex-prefix dups ("mexic")
    fs = [{"id": "f1", "name": "Lima commute", "questions": ["Lima average commute"]},
          {"id": "f2", "name": "All cities", "questions": ["Bogota Lima UK commute"]},
          {"id": "f3", "name": "Safety Events", "questions": ["general commute"]}]
    ents = facet_entities(fs, q)
    assert ents["f1"] == {"lima"} and ents["f2"] == set() and ents["f3"] == set()
    assert entity_ok("f1", "Commutes in Lima average 90 minutes.", "", ents)
    assert not entity_ok("f1", "Commutes in Bogota average 60 minutes.", "", ents)
    assert not entity_ok("f1", "Commutes average 90 minutes.", "", ents)  # names no entity: rejected
    assert entity_ok("f3", "Anything at all.", "", ents)
    assert specificity("The fine is 320 days of pay for firms that break the data law under this rule.") > specificity("Modal share is an important part of transport.")


def test_entity_detection_heldout_regressions():
    from pipeline.run import query_entities, _mentions, _fold
    # h25: "Ghana's GDP" must not fuse into one "title" run (Ghana was silently dropped, no re-plan fired)
    assert {"portugal", "vietnam", "ghana"} <= query_entities("What were Portugal's, Vietnam's and Ghana's GDP per capita?")
    # h19/q02: sentence-initial verb must not fuse onto the first entity
    assert "finland" in query_entities("Compare Finland's and Estonia's digital services.")
    assert "postgresql" in query_entities("Compare PostgreSQL and MySQL for write-heavy workloads.")
    # h24: bare "EU" names the European Union; a region entity matches its member countries (UN M49)
    assert _mentions(_fold("The EU AI Act is delayed."), _fold("European Union"))
    assert _mentions(_fold("The largest numbers were in India."), "asia") and not _mentions(_fold("Deaths in France."), "asia")


def test_entity_filter_runs_before_pool_truncation():
    """h23 Asia/Africa: select_ce took the CE top-40 BEFORE the entity filter, so wrong-entity sentences ranked higher by CE
    filled the pool and the filter then left ~6 of them (vs 25 entity-first)."""
    import pipeline.tune as tune
    from pipeline import run as R
    facets = [{"id": "f1", "name": "Mortality figures in Asia", "entity": "Asia", "questions": ["Asia mortality deaths figures"]}]
    # 3 sources x 30 BM25 candidates = 90 > CE_POOL (40): truncation happens, so the wrong-entity block must not crowd out the right one
    src = {f"u{k}": " ".join([f"Europe reported mortality figures of {k}{i} thousand deaths in 1918 during the influenza outbreak, records show." for i in range(60)]
                            + [f"Asia reported mortality figures of {k} million deaths in 1918 during the influenza outbreak, records show."]) for k in range(3)}
    orig, tune.ce_score = tune.ce_score, (lambda q, t, m: 5.0 if t.startswith("Europe") else 1.0)
    try:
        ev, funnel = {"f1": {}}, {}
        R.select_ce("Asia mortality", facets, src, list(src), ev, {}, funnel, {"judge": None})
    finally:
        tune.ce_score = orig
    quotes = [q for qs in ev["f1"].values() for q in qs]
    assert quotes and all(q.startswith("Asia") for q in quotes), quotes


def test_pipeline_evidence_load_locks_research():
    """_load_pipeline_evidence is shared by run_cli, the TUI /pipeline-evidence command and the API field: loading evidence must flip
    run_state.data["evidence_only"] (tools.core.check_quota then refuses research tools), exactly once for all three surfaces."""
    import json, tempfile, pathlib
    from utils.run_state import RunState, run_state_ctx
    from engine.tui import _load_pipeline_evidence
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "evidence.json").write_text(json.dumps({
        "facets": [{"id": "f1", "name": "Timeline", "entity": "", "questions": ["q"]}],
        "evidence": {"f1": {"https://a.example/p": ["The Act entered into force on 1 August 2024 after publication."]}},
        "titles": {"https://a.example/p": "A real page title"}}))
    rs = RunState(tempfile.mkdtemp())
    tok = run_state_ctx.set(rs)
    try:
        assert rs.data["evidence_only"] is False
        assert _load_pipeline_evidence(rs, str(d)) == 1
        assert rs.data["evidence_only"] is True
    finally:
        run_state_ctx.reset(tok)


def test_evidence_only_skips_research_remedy_checks():
    """In an evidence-only run the checks whose only remedy is more research must not run (the research tools are refused, so the verdict
    could never be satisfied), the writer-fixable checks must stay, and the Planner's "your ONLY next call must be delegate_tasks" text
    must go quiet. A normal run is unchanged. The skipped check is shown to genuinely fire on a normal run, so the skip is not vacuous."""
    import tempfile
    from engine import completion as C
    from engine.completion_checks_grounding import _redelegate_directive
    from utils.run_state import RunState
    rs = RunState(tempfile.mkdtemp())
    rs.data["query"] = "Identify 4 to 6 real, distinct B2B niches with evidence for each."
    rs.data["findings"] = [
        {"task_name": "niche_healthcare", "source_url": "https://gov.example.co/health", "summary": "real content, no warning marker.", "depth": 1},
        {"task_name": "niche_manufacturing", "source_url": "https://gov.example.co/mfg", "summary": "real content, no warning marker.", "depth": 1}]
    ctx = C.Ctx(req_artifact="final_report.md", attempt=0, max_attempts=10, delegated=True, files=[], content=None, quotas=None,
                run_state=rs, report_style="standard")
    assert C.check_requested_count_shortfall(ctx) is not None               # it really fires on a normal run
    assert C._active_completion_checks(rs) is C.COMPLETION_CHECKS
    rs.data["completion_check_attempts"] = [{"fetched_url_count": 0}]
    assert "delegate_tasks" in _redelegate_directive(ctx)                    # normal run: unchanged
    rs.data["evidence_only"] = True
    active = C._active_completion_checks(rs)
    assert len(active) == len(C.COMPLETION_CHECKS) - len(C._EVIDENCE_ONLY_SKIPPED_CHECKS) and len(C._EVIDENCE_ONLY_SKIPPED_CHECKS) == 5
    assert not any(c in active for c in C._EVIDENCE_ONLY_SKIPPED_CHECKS)
    assert C.check_missing_findings in active and C.check_missing_artifact in active and C.check_findings_ungrounded in active
    assert _redelegate_directive(ctx) == ""


def test_snap_urls_to_fetched_repairs_model_typography():
    """gpt-oss re-types fetched URLs with non-breaking hyphens, spliced hyphens + zero-width spaces, or an ellipsis truncation while writing, which made
    check_findings_ungrounded reject the first findings.md draft of every live run. Repair is evidence-based (unique match to a FETCHED url only)."""
    import config as _config
    from tools.fs import _IN_MEMORY_FS, write_workspace_file, edit_workspace_file, get_workspace_file_content
    from utils.grounding import snap_urls_to_fetched
    from utils.run_state import fetched_urls_ctx
    NB = "\u2011"
    F = ["https://sentinel-nexus.com/blog/eu-ai-act-compliance-guide",
         "https://aimagicpunch.com/eu-ai-act-summary-key-provisions-2026-timeline/",
         "https://policy-insider.ai/latest-eu-ai-act-updates-tracking-delegated-acts-and-ai-office-guidanc/",
         "https://en.wikipedia.org/wiki/Cretaceous\u2013Paleogene_extinction_event",
         "https://amb.example/a-b", "https://amb.example/ab"]
    tok = fetched_urls_ctx.set([{"url": u} for u in F])
    saved_ws = _config.cfg["settings"].get("workspace")
    _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
    saved_fs = dict(_IN_MEMORY_FS)
    try:
        sw = f"[T](https://sentinel{NB}nexus.com/blog/eu{NB}ai{NB}act{NB}compliance{NB}guide)"
        assert snap_urls_to_fetched(sw) == f"[T]({F[0]})"                                   # swapped hyphens, markdown parens kept
        assert snap_urls_to_fetched(f"https://aimagicpunch.com/\u200be{NB}u{NB}ai{NB}act{NB}summary{NB}key{NB}provisions{NB}2026{NB}timeline/") == F[1]  # spliced + zero-width
        assert snap_urls_to_fetched(f"https://policy{NB}insider\u2026?\u2026?") == F[2]    # ellipsis-truncated, unique prefix
        assert snap_urls_to_fetched(f"see https://sentinel{NB}nexus.com/blog/eu{NB}ai{NB}act{NB}compliance{NB}guide.") == f"see {F[0]}."  # trailing punctuation kept
        for untouched in (F[3],                                                              # a URL that really has an en dash is fetched: left alone
                          "https://nowhere.example/made-up-page",                            # hallucinated: left for the grounding checks
                          f"https://amb.example/a{NB}b",                                     # skeleton matches TWO fetched urls: ambiguous
                          "https://alicelabs.ai/\u200bre" + (NB + "e") * 3 + "\u2026",       # looped garbage: matches nothing
                          f"https://policy{NB}x\u2026"):                                     # truncation prefix too short to be trusted
            assert snap_urls_to_fetched(untouched) == untouched, untouched
        assert snap_urls_to_fetched(f"a high{NB}risk system") == f"a high{NB}risk system"    # prose outside URLs is not rewritten
        write_workspace_file.func("findings.md", f"### [T]({sw[4:-1]})\nok")                 # the real write tool applies it
        assert F[0] in get_workspace_file_content("findings.md") and NB not in get_workspace_file_content("findings.md")
        edit_workspace_file.func("findings.md", "ok", f"see https://sentinel{NB}nexus.com/blog/eu{NB}ai{NB}act{NB}compliance{NB}guide")
        assert get_workspace_file_content("findings.md").endswith(F[0])                       # and the edit tool
    finally:
        fetched_urls_ctx.reset(tok); _IN_MEMORY_FS.clear(); _IN_MEMORY_FS.update(saved_fs)
        if saved_ws is None: _config.cfg["settings"].pop("workspace", None)
        else: _config.cfg["settings"]["workspace"] = saved_ws


def test_jurisdiction_and_recency():
    from pipeline.run import countries, jurisdiction_ok, year_score
    q = countries("¿Cómo se regula la protección de datos en México?")
    assert q == {"mx"}
    assert not jurisdiction_ok("En Colombia la multa diaria es de 5.000 salarios mínimos.", q)
    assert jurisdiction_ok("La multa es de 100 a 320,000 días de salario mínimo.", q)  # names none: kept
    assert jurisdiction_ok("A diferencia de Colombia, en México la multa es mayor.", q)  # names query country too
    assert countries("UK, Denmark and Taiwan") == {"uk", "dk", "tw"}
    assert year_score("Inflation was 15.9% in June 2026.", 2026) > 0 > year_score("Inflation hit 17% in July 2022.", 2026)
    assert year_score("Inflation was high.", 2026) == 0


def test_source_off_jurisdiction():
    from pipeline.run import source_off_jurisdiction
    assert source_off_jurisdiction("https://x.org", "Chile " * 8 + "Mexico", {"mx"})
    assert not source_off_jurisdiction("https://x.org", "Mexico " * 8 + "Chile " * 8, {"mx"})
    assert not source_off_jurisdiction("https://x.org", "Chile " * 8, set())
    # h08: a Colombian institution page rarely says "Colombia" by name but is hosted on .co
    assert source_off_jurisdiction("https://www.adres.gov.co", "ADRES pesos.", {"cr"})
    assert not source_off_jurisdiction("https://www.ccss.sa.cr", "CCSS colones.", {"cr"})  # own ccTLD: unaffected


def test_hard_wrapped_sentences_rejoined():
    from pipeline.run import sentences
    src = "This aligns with earlier studies, which\n\nreported commuting durations in Santiago typically ranging between 40 and 60 minutes, the longest in\n\nChile.\n\n# Heading\n\nNext sentence here is long enough to be kept as a sentence of its own."
    out = sentences(src)
    assert any("commuting durations in Santiago typically ranging between 40 and 60 minutes" in x for x in out)
    assert not any(x.startswith("#") for x in out)


def test_planner_entity_takes_precedence():
    from pipeline.run import facet_entities, entity_ok
    fs = [{"id": "f1", "name": "Manitoba rules", "entity": "Manitoba", "questions": ["q"]},
          {"id": "f2", "name": "UK capacity", "entity": "United Kingdom", "questions": ["q"]},
          {"id": "f3", "name": "New Brunswick", "entity": "New Brunswick", "questions": ["q"]},
          {"id": "f4", "name": "General", "entity": "", "questions": ["q"]}]
    ents = facet_entities(fs, "Rules by province")
    assert entity_ok("f1", "Manitoba employers must keep a committee.", "", ents)
    assert not entity_ok("f1", "Nova Scotia employers must keep a committee.", "", ents)
    assert entity_ok("f2", "The UK has 14.7 GW installed.", "", ents)  # alias
    assert entity_ok("f3", "In New Brunswick a committee is required.", "", ents)
    assert not entity_ok("f3", "In New Zealand a committee is required.", "", ents)
    assert entity_ok("f4", "Anything.", "", ents)


if __name__ == "__main__":
    # This file is pytest-style (module-level test_ functions) and pytest is not installed: without this block `python test_pipeline_quotes.py`
    # ran NOTHING and exited 0. Run every test_ function, fail loudly on the first error.
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_pipeline_quotes OK ({len(_tests)} tests)")
