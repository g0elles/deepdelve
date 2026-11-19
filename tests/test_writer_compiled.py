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
# the overview's two "99%" sentences cite [1] and [2]: each is swapped for its own cited quote, no whole-unit fallback (design answer_quality rev 2)
assert stats["repairs"] == 2 and stats.get("fallbacks", 0) == 0 and stats["sentence_fallbacks"] == 2 and stats["units"] == 3 and stats["sentences"] == 4
assert written["f1"][0]["extractive"] is False
assert written["f2"][0]["text"] == "NO2 levels dropped by over 20 percent." and not written["f2"][0]["extractive"]
assert [(x["cites"], x["extractive"]) for x in written["overview"]] == [([1], True), ([2], True)]
assert written["overview"][0]["text"] == "Traffic fell 16% in the first eight weeks."
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
# meaning rules (publish path design, Phase 2)
QF = {1: "Annual costs fell to $57 billion by 2030."}
assert "figure_context:57" in wc.check({"cites": [1], "text": "It cut costs by $57 billion."}, QF, "q")
assert not any(d.startswith("figure_context") for d in wc.check({"cites": [1], "text": "Costs fell to $57 billion."}, QF, "q"))
assert not any(d.startswith("figure_context") for d in wc.check({"cites": [1], "text": "Spending rose 57 12."}, {1: "Spending fell to 57 billion and 12 more."}, "q"))
assert not any(d.startswith("figure_context") for d in wc.check({"cites": [1], "text": "Levels dropped by over 20 percent."}, {1: "Levels dropped more than 20 percent."}, "q"))
QH = {1: "Exposure to X may be associated with Y in adults."}
assert "hedge_dropped" in wc.check({"cites": [1], "text": "X causes Y in adults."}, QH, "q")
assert "hedge_dropped" not in wc.check({"cites": [1], "text": "X may cause Y in adults."}, QH, "q")
QY = {1: "Completion is expected in 2024.", 2: "By 2026 the line was still unfinished."}
assert "stale_year:2024" in wc.check({"cites": [1], "text": "Completion is expected in 2024."}, QY, "q")
assert not any(d.startswith("stale_year") for d in wc.check({"cites": [1], "text": "Completion is expected in 2024."}, {1: QY[1]}, "q"))
# build reads spans/dates/uncovered/comparison, and tolerates their absence (EV has none)
assert c.uncovered == [] and c.comparison is False and c.dates == {} and c.facets[0].quotes[0].span is None
EV2 = {**EV, "spans": {"f1": {"https://a.example/x": {"Traffic fell 16% in the first eight weeks.": [10, 52]}}},
       "dates": {"https://a.example/x": "2024-03-05"}, "uncovered": ["emissions"], "comparison": True}
c2 = wc.build(EV2, QUERY)
assert c2.facets[0].quotes[0].span == (10, 52) and c2.dates and c2.uncovered == ["emissions"] and c2.comparison
md2 = wc.render(c2, {"f1": [{"cites": [1], "text": "Traffic fell 16%."}]})
assert '- "Traffic fell 16% in the first eight weeks." [1]\n' in md2 and "<!--" not in md2
assert "1. [Study A](https://a.example/x), 2024-03-05" in md2 and "2. [https://b.example/y](https://b.example/y)\n" in md2
assert md2.index("## Evidence gaps") < md2.index("## Not covered") < md2.index("## Sources")
assert "No facet covered these words of the request: emissions." in md2 and "Comparison asked for" in md2
crossed = wc.render(c2, {"overview": [{"cites": [1, 2], "text": "Traffic and air both improved."}]})
assert "Comparison asked for" not in crossed                                                  # a sentence cites sources from two facets
assert "## Not covered" not in wc.render(c, {})
# smoke 2026-11-14 regressions: abbreviation dots and hyphens fold away; of/at are not meaning prepositions; fallback picks the relevant quote
assert wc.check({"cites": [1], "text": "Population-weighted U.S. averages show reductions."}, {1: "the population weighted US averages show reductions"}, "q") == []
assert wc.check({"cites": [1], "text": "A furnace costs at $1,128 a year."}, {1: "an annual cost of $1,128"}, "q") == []
assert "figure_context:57" in wc.check({"cites": [1], "text": "Spending was cut by $57 billion."}, {1: "spending fell to $57 billion"}, "q")
fq = [wc.Quote(1, "Mild winters suit many homes.", "f"), wc.Quote(2, "Heat pumps cost less to run than gas furnaces.", "f")]
assert wc._most_relevant(fq, "heat pumps versus gas furnaces", "running cost").n == 2
assert wc._most_relevant(fq[:1], "zzz", "zzz").n == 1                                        # no overlap: plan order
# (d) overview pool: bridging quotes first, then each facet's two most relevant; sides are facet entities of a comparison request
EVC = {"facets": [{"id": "a", "name": "heat pump cost", "entity": "heat pump", "questions": []},
                  {"id": "b", "name": "gas furnace cost", "entity": "gas furnace", "questions": []}],
       "evidence": {"a": {"https://p.example": ["A heat pump costs $9,000 to install.", "Heat pumps last 15 years."]},
                    "b": {"https://g.example": ["A gas furnace costs $4,000.", "Electric furnaces and heat pumps differ in cost.",
                                                "A heat pump and a gas furnace cost about the same over 10 years."]}},
       "comparison": True}
cc = wc.build(EVC, "How do heat pumps compare with gas furnaces on cost?")
assert [e for e, _ in wc._sides(cc)] == ["heat pump", "gas furnace"] and dict(wc._sides(cc))["gas furnace"] == {"gas", "furnace"}
assert not wc._bridging(cc.facets[1].quotes[1], wc._sides(cc))                     # "electric furnaces" is not the gas-furnace side
pool, question = wc._overview_pool(cc)
assert pool[0].text.startswith("A heat pump and a gas furnace") and "heat pump and gas furnace compare" in question
assert {q.facet for q in pool} == {"a", "b"} and len(pool) == len({q.text for q in pool})
nc = wc.build({**EVC, "comparison": False}, "heat pump and gas furnace costs")
pool2, q2 = wc._overview_pool(nc)
assert q2 == wc.OVERVIEW_QUESTION and len(pool2) == 4 and wc._sides(nc) == []
# (f) sentence-level fallback: clean sentences stay, a failing one becomes its own cited quote, in quotation marks
UQ = [wc.Quote(1, "Heat pumps cost $9,000 to install.", "a"), wc.Quote(2, "Gas furnaces cost $4,000 to install.", "a")]
LONG = " ".join(["word"] * 50)
one_bad = lambda *a: {"sentences": [{"cites": [1], "text": "Heat pumps cost $9,000 to install."}, {"cites": [2], "text": LONG}]}
st = {}
out = wc.write_unit(one_bad, "heat pumps vs gas furnaces", "cost", "cost", UQ, 1, 3, 1, st)
assert [(x["cites"], x["extractive"]) for x in out] == [([1], False), ([2], True)] and out[1]["text"] == UQ[1].text
assert st["sentence_fallbacks"] == 1 and "fallbacks" not in st
assert "\u201cGas furnaces cost $4,000 to install.\u201d[2]" in wc._sentences(out)
dup_bad = lambda *a: {"sentences": [{"cites": [1], "text": "Heat pumps cost $9,000 to install."}, {"cites": [1], "text": LONG}]}
st = {}
out = wc.write_unit(dup_bad, "q", "cost", "cost", UQ, 1, 3, 1, st)   # its only source is cited by the kept sentence: dropped
assert len(out) == 1 and st["dropped_sentences"] == 1
all_bad = lambda *a: {"sentences": [{"cites": [7], "text": LONG}]}  # no valid cite, nothing survives: whole-unit fallback
st = {}
out = wc.write_unit(all_bad, "q", "cost", "cost", UQ, 1, 3, 1, st)
assert st["fallbacks"] == 1 and len(out) == 1 and out[0]["extractive"]
print("test_writer_compiled: ok")

if __name__ == "__main__":
    pass

