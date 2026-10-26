"""writer/compiled.py: the verifier, the fallback and the rendered report. Offline, the model call is a fake."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from writer import compiled as wc

EV = {
    "facets": [{"id": "f1", "name": "Traffic volume", "entity": "", "questions": ["q1"]},
               {"id": "f2", "name": "Air quality", "entity": "", "questions": ["q2"]},
               {"id": "f3", "name": "Cost", "entity": "", "questions": ["q3"]}],
    "evidence": {"f1": {"https://a.example/x": ["Traffic fell 16% in the first eight weeks."], "https://b.example/y": ["Speeds rose by 12% downtown."]},
                 "f2": {"https://b.example/y": ["NO2 levels dropped more than 20 percent."]},
                 "f3": {}},
    "titles": {"https://a.example/x": "Study A"},
    "unanswered_facets": {"f3": ["What does it cost?"]},
}
QUERY = "Does congestion pricing cut traffic and improve air quality?"
c = wc.build(EV, QUERY)
# numbering: by first appearance across facets, shared source keeps one number, title falls back to the url
assert [(n, u) for n, u, _ in c.sources] == [(1, "https://a.example/x"), (2, "https://b.example/y")]
assert c.sources[1][2] == "https://b.example/y" and c.sources[0][2] == "Study A"
assert [q.n for q in c.facets[0].quotes] == [1, 2] and [q.n for q in c.facets[1].quotes] == [2]
assert c.facets[2].unanswered == ["What does it cost?"]

# verifier
Q = {1: "Traffic fell 16% in the first eight weeks.", 2: "Speeds rose by 12% downtown. The WHO agrees."}
ok = {"cites": [1], "text": "Traffic fell 16% in the first weeks."}
assert wc.check(ok, Q, QUERY) == []
assert wc.check({"cites": [], "text": "x"}, Q, QUERY) == ["no_citation"]
assert wc.check({"cites": [9], "text": "x"}, Q, QUERY)[0].startswith("invalid_citation")
assert any(d.startswith("figure_not_in_cited_quotes:18") for d in wc.check({"cites": [1], "text": "Traffic fell 18%."}, Q, QUERY))
assert wc.check({"cites": [1], "text": "It fell 16,0 percent"}, Q, QUERY)  # digits 160 not in the quote
assert wc.check({"cites": [2], "text": "Speeds rose by 12%, the WHO notes."}, Q, QUERY) == []   # abbreviation present in the cited quote
assert any(d.startswith("abbreviation_not_in_cited_quotes:EPA") for d in wc.check({"cites": [2], "text": "The EPA agrees."}, Q, QUERY))
assert wc.check({"cites": [1], "text": "Traffic " + "very " * 50}, Q, QUERY) == ["too_long"]
assert wc.check({"cites": [1], "text": "A 3 fold change"}, Q, QUERY) == []                     # one-digit numbers are not checked

# compose: good draft passes, bad draft is repaired, unrepairable unit falls back to the verbatim quote
calls = []
def llm(prompt, schema, temperature, seed):
    calls.append(("repair" if "previous draft" in prompt else "draft", seed))
    if "Topic: Traffic volume" in prompt:
        return {"sentences": [{"cites": [1], "text": "Traffic fell 16% in the first eight weeks."}]}
    if "Topic: Air quality" in prompt:
        if "previous draft" in prompt:
            return {"sentences": [{"cites": [2], "text": "NO2 levels dropped by over 20 percent."}]}
        return {"sentences": [{"cites": [2], "text": "NO2 levels dropped 45 percent."}]}          # invented figure
    if "Topic: the whole request" in prompt:
        return {"sentences": [{"cites": [1], "text": "Traffic fell 99%."}, {"cites": [2], "text": "Air improved 99%."}]}   # never clean
    raise AssertionError(prompt)
written, stats = wc.compose(c, llm, n_drafts=1)
assert stats["repairs"] == 2 and stats["fallbacks"] == 1 and stats["units"] == 3
assert written["f1"][0]["extractive"] is False
assert written["f2"][0]["text"] == "NO2 levels dropped by over 20 percent." and not written["f2"][0]["extractive"]
assert written["overview"] == [{"cites": [1], "text": "Traffic fell 16% in the first eight weeks.", "extractive": True}]
assert "f3" not in written                                                                      # no quotes, no model call

# a model that always raises degrades to quotes, never crashes
bad = lambda *a: (_ for _ in ()).throw(RuntimeError("down"))
w2, s2 = wc.compose(c, bad)
assert s2["fallbacks"] == 3 and all(u[0]["extractive"] for u in w2.values())

# the rendered report: code owns headings, quotes, gaps and sources
md = wc.render(c, written)
assert md.startswith("# " + QUERY) and "## Answer in brief" in md and "## Evidence gaps" in md and "## Sources" in md
assert "- **Cost:** No source in the available evidence directly states: What does it cost?." in md
assert '- "Speeds rose by 12% downtown." [2]' in md and "1. [Study A](https://a.example/x)" in md
assert "No usable evidence was found for this topic." in md                                    # facet f3
# every number in the report is a number in some quote (the property the whole design is for)
allq = " ".join(q.text for f in c.facets for q in f.quotes)
assert wc.numbers(md.split("## Sources")[0]) <= wc.numbers(allq) | wc.numbers(QUERY)
# bot-wall titles never reach the Sources list
ev2 = {"facets": [{"id": "f1", "name": "T", "entity": "", "questions": ["q"]}], "evidence": {"f1": {"https://w.example/p": ["A real sentence about the topic here."]}},
       "titles": {"https://w.example/p": "Checking your browser before accessing. Just a moment..."}, "unanswered_facets": {}}
assert wc.build(ev2, "q").sources == [(1, "https://w.example/p", "https://w.example/p")]
# the model never sees more than MAX_QUOTE_CHARS of quotes, quotes are fenced as untrusted data, instructions stay outside the fence
seen = []
def spy(prompt, schema, temperature, seed):
    seen.append(prompt); return {"sentences": [{"cites": [1], "text": "Filler sentence one."}]}
big = wc.Compiled("q", [wc.Facet("f1", "T", [wc.Quote(i + 1, "x" * 4000 + f" quote number {i}", "f1") for i in range(10)])], [])
st = {}
wc.write_unit(spy, "q", "T", "T", big.facets[0].quotes, 1, 3, 1, st)
assert st["truncated_units"] == 1 and len(seen[0]) < wc.MAX_QUOTE_CHARS + 3000, len(seen[0])
assert "<quotes>" in seen[0] and "untrusted" in seen[0] and seen[0].index("</quotes>") < seen[0].index("Rules:")
assert "quote number 9" not in seen[0] and "quote number 0" in seen[0]
# skeleton-replay regressions in the shared grounding checks (2026-10-26)
from utils import grounding as g
rep = ('# R\n\n## T\n\n- "Life expectancy in Chile is 81.2 years, 9.2 years above the global average (World Bank, 2025)." [1]\n\n## Sources\n\n'
       '1. [Study](https://a.example/x)\n')
assert g.find_non_url_citations(rep) == []                                                     # resolved [N]: the parenthetical is the quote's own reference
assert g.find_non_url_citations(rep.replace("[1]\n\n## Sources", "[9]\n\n## Sources"))        # unresolved [N]: still flagged
gap = g.gap_disclosure_sentence({"facet": "Cost", "questions": ["What did 2023 cost in 5 cities?"]})
assert gap.startswith(g.GAP_DISCLOSURE_PREFIX) and "2023" in gap
body = ("## T\n\n- Estimated costs rose 12% in 2023 across the region, a large increase overall. [1]\n- **Cost:** " + gap + "\n- **Cost2:** " + gap + "\n- **Cost3:** " + gap + "\n")
assert not any(GAP in l for GAP in [g.GAP_DISCLOSURE_PREFIX] for l in g.find_uncited_claim_lines(body))
from engine.completion_checks_structural import gap_disclosure_sentence as engine_gap          # still importable from the engine, same function
assert engine_gap is g.gap_disclosure_sentence
# figure and token checks: magnitude suffixes and fused link text (replay: h16 "$330.3B", h24 "EUR 800K", h22 "LillyLLY")
assert g._figure_in_source("330.3", "B", "GDP $330.3B World Bank", "") and not g._figure_in_source("330.3", "M", "GDP $330.3B", "")
assert g._figure_in_source("330.3", "", "GDP 330.3 billion", "") and not g._figure_in_source("330", "", "GDP 3300 billion", "")
assert g._strip_md_links("[Lilly](https://x.example)[LLY](https://y.example) rose[[3]](#n3), then") == "LillyLLY rose, then"
assert g._normalize_for_quote_match("up by 25%[[35]](#_ftn35), with") == g._normalize_for_quote_match("up by 25%, with")
# claim support: a proper-noun phrase split by link syntax in the page is whole in the pipeline's link-stripped quote
import config as _cfg
from tools.fs import _IN_MEMORY_FS
from utils.run_state import record_fetched_url, reset_fetched_urls
_cfg.cfg["settings"]["workspace"] = {"type": "memory"}
reset_fetched_urls(); _IN_MEMORY_FS.clear()
record_fetched_url("https://t.example/trial", filename="src_t.md")
_IN_MEMORY_FS["src_t.md"] = ("Intro text about depression research here. " * 3 + "The [COMPASS Pathways](https://c.example) Phase 2b trial enrolled many patients and reported "
                             "[a 30% response](https://c.example/r) in this group of people overall.")
claim = ('# R\n\n## T\n\n- "The COMPASS Pathways Phase 2b trial enrolled many patients and reported a 30% response in this group." [1]\n\n'
         '## Sources\n\n1. [Trial](https://t.example/trial)\n')
assert g.claim_grounding_problem(claim) is None
reset_fetched_urls(); _IN_MEMORY_FS.clear()
# a page's own reference marker inside a quote is dropped, and the quote check still matches it against the raw source
ev3 = {"facets": [{"id": "f1", "name": "T", "entity": "", "questions": ["q"]}], "evidence": {"f1": {"https://a.example/x": ["Remission in 30% of patients at week 3 [4]."]}}, "titles": {}, "unanswered_facets": {}}
assert wc.build(ev3, "q").facets[0].quotes[0].text == "Remission in 30% of patients at week 3."
assert g._normalize_for_quote_match("remission at week 3 [4].") == g._normalize_for_quote_match("remission at week 3.")
assert g._normalize_for_quote_match("see [a link](https://x.example) now") == "see a link now"          # [text](url) is not a numeric marker
# deterministic fix for a cross-source disagreement: both figures and both source numbers appear, in code
from pipeline.bridge import source_filename
cd = wc.build(EV, QUERY)
hit = {"subject": "inflation reduction act", "figure": "30%", "other_figure": "100%", "cited_files": [source_filename("https://a.example/x")], "other_file": source_filename("https://b.example/y")}
assert wc.add_disagreements(cd, [hit, {**hit, "other_file": "src_unknown.md"}]) == 1                # a hit whose file is not a numbered source is skipped
mdd = wc.render(cd, {})
assert "## Where sources differ" in mdd and "- **Inflation reduction act:** 30% [1] but 100% [2]; the sources may be measuring different quantities." in mdd
assert wc.render(wc.build(EV, QUERY), {}).count("Where sources differ") == 0
# names: expansions and coined Title-Case terms must be in the cited quotes (replay with gemma/ornith, 2026-10-26)
assert wc.names("Las Entidades Promotoras de Salud (EPS) son clave, junto con Instituciones Prestadoras de Servicios de Salud (IPS).") == ["Las Entidades Promotoras de Salud EPS", "Instituciones Prestadoras de Servicios de Salud IPS"]
assert wc.names("The compaction strategy keeps the Write Amplification Factor under 5.") == ["Write Amplification Factor"]
assert wc.check({"cites": [1], "text": "In Toronto the rebates are large."}, {1: "Toronto rebates are large."}, "q") == []   # sentence-initial 'In' is grammar
QN = {1: "EPS and IPS are key actors. Compaction keeps write amplification low."}
assert any(d.startswith("name_not_in_cited_quotes:Write Amplification Factor") for d in wc.check({"cites": [1], "text": "Compaction keeps the Write Amplification Factor low."}, QN, "q"))
assert any(d.startswith("name_not_in_cited_quotes:Las Entidades") for d in wc.check({"cites": [1], "text": "Las Entidades Promotoras de Salud (EPS) son clave."}, QN, "q"))
assert wc.check({"cites": [1], "text": "EPS and IPS are key actors."}, QN, "q") == []
QN2 = {1: "Entidades Promotoras de Salud are the insurers."}
assert wc.check({"cites": [1], "text": "Las Entidades Promotoras de Salud are the insurers."}, QN2, "q") == []   # sentence-initial article is grammar
assert wc.check({"cites": [1], "text": "Compaction keeps write amplification low."}, QN, "q") == []
QM = {1: "Prevalence was 7.3% in 20 studies."}
assert "describes_the_quotes_not_the_topic" in wc.check({"cites": [1], "text": "The provided quotes do not state what is unanswered."}, QM, "x")
assert wc.check({"cites": [1], "text": "Prevalence was 7.3% in 20 studies."}, QM, "x") == []
print("test_writer_compiled: ok")

if __name__ == "__main__":
    pass

