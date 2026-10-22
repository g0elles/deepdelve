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
    assert len(active) == len(C.COMPLETION_CHECKS) - len(C._EVIDENCE_ONLY_SKIPPED_CHECKS) and len(C._EVIDENCE_ONLY_SKIPPED_CHECKS) == 6
    assert C.check_not_delegated not in active                                # zero-evidence run: its delegate_tasks remedy is refused
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
         "https://amb.example/a-b", "https://amb.example/ab", "https://en.wikipedia.org/wiki/Mercury_(planet)"]
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
        paren = f"[Mercury]({F[6]})"
        assert snap_urls_to_fetched(paren) == paren and snap_urls_to_fetched(F[6]) == F[6]  # a correct URL with its own ')' is not doubled
        assert snap_urls_to_fetched(f"a high{NB}risk system") == f"a high{NB}risk system"    # prose outside URLs is not rewritten
        write_workspace_file.func("findings.md", f"### [T]({sw[4:-1]})\nok")                 # the real write tool applies it
        assert F[0] in get_workspace_file_content("findings.md") and NB not in get_workspace_file_content("findings.md")
        edit_workspace_file.func("findings.md", "ok", f"see https://sentinel{NB}nexus.com/blog/eu{NB}ai{NB}act{NB}compliance{NB}guide")
        assert get_workspace_file_content("findings.md").endswith(F[0])                       # and the edit tool
        old = f"https://sentinel{NB}nexus.com/blog/eu{NB}ai{NB}act{NB}compliance{NB}guide"  # the model's own earlier damaged copy still matches
        edit_workspace_file.func("findings.md", old, "REPLACED", True)
        assert get_workspace_file_content("findings.md").endswith("see REPLACED")
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


def test_judge_separates_empty_verdict_from_error():
    """judge() -> [] is a real "nothing relevant" verdict (select_ce must keep nothing), None is a failed call (select_ce keeps the CE ranking)."""
    import pipeline.run as R
    facet = {"name": "f", "questions": ["q"]}
    real = R.call_json
    def boom(*a, **k): raise RuntimeError("ollama down")
    try:
        R.call_json = lambda *a, **k: {"keep": []}
        assert R.judge("m", facet, [("u", "t")]) == []
        R.call_json = lambda *a, **k: {"keep": [0, 7]}
        assert R.judge("m", facet, [("u", "t")]) == [0]
        R.call_json = boom
        assert R.judge("m", facet, [("u", "t")]) is None
    finally:
        R.call_json = real


def test_seed_evidence_reaches_memory_workspace():
    """In a memory workspace _get_safe_path returns a bare key, so the pipeline source copy must go to _IN_MEMORY_FS, not the cwd."""
    import tempfile, pathlib, hashlib, os
    import config as _config
    from tools.fs import _IN_MEMORY_FS, get_workspace_file_content
    from pipeline.bridge import seed_run_state_from_evidence
    from utils.run_state import RunState, run_state_ctx
    url = "https://a.example/p"
    fname = f"src_{hashlib.sha1(url.encode()).hexdigest()[:8]}.md"
    d = pathlib.Path(tempfile.mkdtemp()); (d / fname).write_text("SOURCE BODY")
    rs = RunState(tempfile.mkdtemp())
    saved_ws, saved_fs = _config.cfg["settings"].get("workspace"), dict(_IN_MEMORY_FS)
    _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
    tok = run_state_ctx.set(rs)
    try:
        assert seed_run_state_from_evidence(rs, [{"id": "f1", "name": "T", "questions": ["q"]}], {"f1": {url: ["A quote."]}}, d) == 1
        assert get_workspace_file_content(fname) == "SOURCE BODY" and not os.path.exists(fname)
    finally:
        run_state_ctx.reset(tok); _IN_MEMORY_FS.clear(); _IN_MEMORY_FS.update(saved_fs)
        if saved_ws is None: _config.cfg["settings"].pop("workspace", None)
        else: _config.cfg["settings"]["workspace"] = saved_ws


def test_evidence_only_refusals_are_capped():
    """check_quota's evidence-only refusal returns before quota accounting, so it needs its own loop hard stop."""
    import tempfile
    from tools.core import check_quota, QuotaAbortException, _EVIDENCE_ONLY_REFUSAL_LIMIT
    from utils.run_state import RunState, run_state_ctx
    rs = RunState(tempfile.mkdtemp()); rs.data["evidence_only"] = True
    tok = run_state_ctx.set(rs)
    try:
        for _ in range(_EVIDENCE_ONLY_REFUSAL_LIMIT):
            assert check_quota("delegate_tasks")
        try:
            check_quota("delegate_tasks")
            raise AssertionError("no QuotaAbortException past the refusal cap")
        except QuotaAbortException:
            pass
    finally:
        run_state_ctx.reset(tok)


def test_source_dump_strip_keeps_topical_headings_and_subsections():
    from engine.completion_checks_grounding import _strip_source_dump_sections as S
    keep = "## Other Energy Sources\ncite [a](https://a.example)\n## 3. Additional Funding Sources\ncite [b](https://b.example)"
    assert S(keep) == keep                                                   # topical headings ending in "sources" are content
    assert "https://" not in S("## Neglected Sources\n[x](https://x.example)\n### Sub\n[y](https://y.example)\n## Next\nbody")  # sub-heading stays in the dump
    assert "body" in S("## Additional Sources Not Cited\n[x](https://x.example)\n## Next\nbody")


def test_merge_same_url_keeps_task_groups_whole():
    """A URL shared by two tasks must not split either task's multi-URL group (S1:{A,B}, S2:{A,C}) once the merge joins A's summaries."""
    from engine.findings_evidence import _merge_same_url_findings, _collapse_multi_url_task_findings
    f = lambda t, u, s: {"task_name": t, "source_url": u, "summary": s}
    merged = _merge_same_url_findings([f("T1", "https://a.example", "S1"), f("T1", "https://b.example", "S1"),
                                       f("T2", "https://a.example", "S2"), f("T2", "https://c.example", "S2")])
    groups = _collapse_multi_url_task_findings(merged)
    assert [[x["source_url"] for x in g["findings"]] for g in groups] == [["https://a.example", "https://b.example"], ["https://c.example"]]


def test_check_artifacts_from_2026_10_17_audit():
    """The replay audit over 335 saved reports found checks firing on text artifacts, not unsupported facts. One assert group per artifact class."""
    sys.path.insert(0, "src")
    from utils import grounding as G
    # sentence-initial words are not part of a name ("Both Finland" matched no source)
    terms = G.extract_salient_terms("Both Finland and Estonia lead. While Bitcoin uses proof-of-work. Provided Colombian payroll data.")
    assert not any(t.split()[0] in ("Both", "While", "Provided") for t in terms), terms
    # link anchors and bold labels are apparatus, not claims
    assert "Rust Releases" not in G.extract_salient_terms(G._strip_citation_apparatus("See [Rust Releases](https://releases.rs/) now. **Regional Variations**: Fiestas Patrias"))
    assert "Fiestas Patrias" in G.extract_salient_terms(G._strip_citation_apparatus("**Regional Variations**: Fiestas Patrias"))
    # bibliography entries / bare-URL rows / "Source URL:" lines are citation lines
    for l in ("5. Shaw, P., Uszkoreit, J., & Vaswani, A. (2018). Self-Attention with Relative Position Representations. https://arxiv.org/pdf/1803.02155",
              "4. Medium. (2020). *Baidu ERNIE 2.0 beats BERT*. https://medium.com/x", "| ACE eWaPub - Cultural Events | https://ace.ewapub.com/a.pdf | File not processed |",
              "Source URL: https://arxiv.org/abs/1811.07745"):
        assert G._is_citation_only_line(l), l
    assert not G._is_citation_only_line("Estonia launched e-Residency in 2014 [4].")
    # quote normalizer folds markdown emphasis and bracketed edits
    assert G._normalize_for_quote_match("completed in **1889**, on [SQuAD]") == G._normalize_for_quote_match("completed in 1889, on SQuAD")
    # NLI: chrome windows and numerically-verbatim claims are not handed to the classifier
    prose = " ".join(["The dominant sequence transduction models are based on complex recurrent networks and we propose a new architecture."] * 3)
    assert not G._is_prose_window("# About FIT\n\n## The Introduction of Japan FIT") and G._is_prose_window(prose)
    assert G._figures_verbatim_in_window("BLEU of 28.4 on WMT 2014", "reaching 28.4 BLEU on the WMT 2014 task") and not G._figures_verbatim_in_window("BLEU of 28.4", "BLEU of 25.0")
    assert not G._figures_verbatim_in_window("a claim with no figures", "any window")
    # uncited claims: fullwidth markers that resolve to a numbered source are citations
    ok = "## Body\nThe law sets a target of 80 % electricity from renewables by 2030\u30101\u3011 and more.\n\n## Sources\n1. [Law](https://a.example/law)\n"
    assert G.find_uncited_claim_lines(ok) == [] and G.find_uncited_claim_lines(ok.replace("\u30101\u3011", "\u30109\u3011"))


def test_figure_check_accepts_source_names_from_title_and_url():
    import config as _config
    from tools.fs import _IN_MEMORY_FS
    from utils import grounding as G
    from utils.run_state import fetched_urls_ctx
    url = "https://data.finstatglobe.com/finland/internet-adoption-statistics"
    entry = {"url": url, "filename": "src_a.md", "title": "Internet Adoption Statistics in Finland (2026) - FinStatGlobe"}
    saved_ws, saved_fs = _config.cfg["settings"].get("workspace"), dict(_IN_MEMORY_FS)
    _config.cfg["settings"]["workspace"] = {"type": "memory", "required_artifact": "final_report.md"}
    _IN_MEMORY_FS["src_a.md"] = "93.7% of the population in Finland used the internet in 2024, per World Bank data. " * 3
    tok = fetched_urls_ctx.set([entry])
    try:
        assert G.find_unsupported_specific_figures(f"FinStatGlobe records 93.7 % for 2024 ([x]({url}))") == []   # name is in the cited source's title
        assert G.find_unsupported_specific_figures(f"ZorbloxData records 93.7 % for 2024 ([x]({url}))") == ["ZorbloxData"]  # a name nowhere is still flagged
    finally:
        fetched_urls_ctx.reset(tok); _IN_MEMORY_FS.clear(); _IN_MEMORY_FS.update(saved_fs)
        if saved_ws is None: _config.cfg["settings"].pop("workspace", None)
        else: _config.cfg["settings"]["workspace"] = saved_ws


def test_stub_only_findings_are_not_citable_and_titleless_sources_get_a_readable_heading():
    from engine.findings_evidence import _is_citable_finding
    from pipeline.bridge import _fallback_title
    from utils.run_state import fetched_urls_ctx
    u = "https://www.researchgate.net/publication/1"
    f = {"source_url": u, "summary": "A real looking synthesis sentence about the topic."}
    tok = fetched_urls_ctx.set([{"url": u, "filename": "a.md", "stub": "empty page"}])
    try:
        assert not _is_citable_finding(f)
        fetched_urls_ctx.get().append({"url": u, "filename": "b.md"})   # later fetched cleanly: citable again
        assert _is_citable_finding(f)
    finally:
        fetched_urls_ctx.reset(tok)
    t = _fallback_title("https://www.europarl.europa.eu/RegData/etudes/BRIE/2022/739271/EPRS_BRI(2022)739271_EN.pdf?trk=x")
    assert t.startswith("europarl.europa.eu: ") and "RegData" not in t


def test_facet_unanswered_distinguishes_a_real_answer_from_a_related_but_different_figure():
    """facet_unanswered: the h19 live incident -- "citizen adoption rate" has to come back unanswered even though a quote about a
    DIFFERENT quantity (internet penetration) for the SAME entity is present; a quote that really answers a question must clear it."""
    import pipeline.run as R
    facet = {"name": "Finland digital adoption", "questions": ["Finland e-government usage rate 2024", "Finland digital ID adoption rate"]}
    real = R.call_json
    try:
        assert R.facet_unanswered("m", "q", facet, []) == [0, 1]  # no quotes at all: every question unanswered, no model call needed
        R.call_json = lambda *a, **k: {"answers": [{"q": 0, "quote": -1}, {"q": 1, "quote": 0}]}
        assert R.facet_unanswered("m", "q", facet, ["91% of residents have a digital ID."]) == [0]
        R.call_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        assert R.facet_unanswered("m", "q", facet, ["some quote"]) == []  # fails open: advisory, never blocks a run
    finally:
        R.call_json = real


def test_reformulate_question_falls_back_to_the_original_on_error():
    import pipeline.run as R
    real = R.call_json
    try:
        R.call_json = lambda *a, **k: {"queries": ["alt phrasing one", "alt phrasing two", ""]}
        assert R.reformulate_question("m", "Finland", "q") == ["alt phrasing one", "alt phrasing two"]
        R.call_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        assert R.reformulate_question("m", "Finland", "q") == []
    finally:
        R.call_json = real


if __name__ == "__main__":
    # This file is pytest-style (module-level test_ functions) and pytest is not installed: without this block `python test_pipeline_quotes.py`
    # ran NOTHING and exited 0. Run every test_ function, fail loudly on the first error.
    _tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    assert _tests, "no tests found"
    for _n, _f in _tests:
        _f()
    print(f"test_pipeline_quotes OK ({len(_tests)} tests)")
