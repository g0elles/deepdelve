"""Stage-graph prototype: plan -> gather -> extract -> evidence table. No report writing.
Usage (from src/): python -m pipeline.run "<query>" [--model M] [--out DIR] [--topk 3]"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import re
import time
from pathlib import Path

from pipeline.llm import call_json

PLAN_SCHEMA = {"type": "object", "required": ["facets"], "properties": {"facets": {
    "type": "array", "minItems": 2, "maxItems": 6, "items": {
        "type": "object", "required": ["id", "name", "questions"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"},
            "questions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "string"}}}}}}}
EXTRACT_SCHEMA = {"type": "object", "required": ["spans"], "properties": {"spans": {
    "type": "array", "maxItems": 6, "items": {
        "type": "object", "required": ["facet_id", "quote"], "properties": {
            "facet_id": {"type": "string"}, "quote": {"type": "string"}}}}}}
JUDGE_SCHEMA = {"type": "object", "required": ["keep"], "properties": {"keep": {
    "type": "array", "items": {"type": "integer"}}}}
CHUNK_CHARS, MAX_CHUNKS = 6000, 4
PER_SOURCE, PER_JUDGE, POOL = 4, 15, 30
CE_EN = "cross-encoder/ms-marco-MiniLM-L-6-v2"
CE_ML = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
CE_MIN = {CE_EN: 0.0, CE_ML: -4.0}  # ponytail: CE_ML threshold from one probe pair, calibrate on labeled data
_STOP = set("the a an of and or to in on for with by is are was were be as at from that this it its "
            "how what which who when where why compare comparison key differ".split())


def norm(s: str) -> str:
    """Comparison form: markdown links -> text, footnote refs dropped, alphanumerics only."""
    s = re.sub(r"\[\[[^\]]*\]\]\([^)]*\)", " ", s)  # [[3]](#cite_note-4)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [text](url "title")
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def quote_in_source(quote: str, source: str) -> bool:
    q = norm(quote)
    return len(q) >= 20 and q in norm(source)


def chunks(text: str) -> list[str]:
    return [text[i:i + CHUNK_CHARS] for i in range(0, len(text), CHUNK_CHARS)][:MAX_CHUNKS]


def clean(s: str) -> str:
    s = re.sub(r"\[\[[^\]]*\]\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n+", text):
        for sent in re.split(r"(?<=[.!?])\s+", clean(para)):
            if (40 <= len(sent) <= 400 and sum(c.isalpha() for c in sent) > 0.5 * len(sent)
                    and sent[-1] in '.!?"\u201d)' and not sent.startswith(("#", "|")) and "](" not in sent and "\u2023" not in sent):
                out.append(sent)
    return out


def toks(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", s.casefold()) if w not in _STOP]


def bm25_scores(sents: list[str], query: str, k1: float = 1.5, b: float = 0.75) -> list[float]:
    docs = [toks(x) for x in sents]
    if not docs:
        return []
    avg = sum(map(len, docs)) / len(docs) or 1
    q = set(toks(query))
    df = {t: sum(t in d for d in docs) for t in q}
    out = []
    for d in docs:
        sc = 0.0
        for t in q:
            f = d.count(t)
            if f:
                sc += math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        out.append(sc)
    return out


def bm25_top(sents: list[str], query: str, k: int, k1: float = 1.5, b: float = 0.75) -> list[tuple[float, str]]:
    scored = [(sc, x) for sc, x in zip(bm25_scores(sents, query, k1, b), sents) if sc > 0]
    return sorted(scored, key=lambda z: -z[0])[:k]


def judge(model: str, facet: dict, cands: list[tuple[str, str]]) -> list[int]:
    lst = "\n".join(f"{i}. {t}" for i, (_, t) in enumerate(cands))
    p = (f"Facet: {facet['name']}\nQuestions: {'; '.join(facet['questions'])}\n\nCandidate sentences:\n{lst}\n\n"
         f"Return the numbers of candidates that state a specific fact (a date, number, named law, "
         f"mechanism, or comparison) relevant to this facet. Skip vague or off-topic ones.")
    try:
        return [i for i in call_json(model, p, JUDGE_SCHEMA)["keep"] if 0 <= i < len(cands)]
    except Exception:
        return []


def plan(model: str, query: str) -> list[dict]:
    p = (f"Break this research request into 2-6 facets that together fully answer it. Each facet gets "
         f"an id (f1, f2, ...), a short name, and 1-3 specific web search questions. Every facet must "
         f"be answerable from a single web page. If the request compares things, make one facet per "
         f"side and aspect; do NOT make a facet for the comparison itself (it is done later).\n\nRequest: {query}")
    return call_json(model, p, PLAN_SCHEMA)["facets"]


def search(question: str, k: int) -> list[str]:
    from ddgs import DDGS
    try:
        return [r["href"] for r in DDGS().text(question, max_results=k + 2, backend="auto") if r.get("href")]
    except Exception:
        return []


def fetch(url: str) -> str | None:
    from tools.web import _fetch_raw, _stub_reason
    try:
        data = _fetch_raw(url, True)[0]
    except Exception:
        return None
    if not isinstance(data, str) or _stub_reason(data):
        return None
    return data


def extract(model: str, facets: list[dict], text: str) -> tuple[list[dict], int, int]:
    fl = "\n".join(f"{f['id']}: {f['name']}" for f in facets)
    emitted, kept, failed = 0, [], 0
    for ch in chunks(text):
        p = (f"Facets:\n{fl}\n\nFrom the SOURCE TEXT below, copy up to 6 sentences that directly state "
             f"facts relevant to a facet. Each quote must be copied EXACTLY, character for character, "
             f"from the text. Do not paraphrase. Return no spans if nothing is relevant.\n\nSOURCE TEXT:\n{ch}")
        try:
            spans = call_json(model, p, EXTRACT_SCHEMA)["spans"]
        except Exception:
            failed += 1
            continue
        for s in spans:
            emitted += 1
            if quote_in_source(s["quote"], text):
                kept.append(s)
    return kept, emitted, failed


def run(query: str, model: str, out: Path, topk: int, extractor: str = "ce") -> dict:
    t0, calls = time.time(), 0
    out.mkdir(parents=True, exist_ok=True)
    facets = plan(model, query)
    ids = {f["id"] for f in facets}
    evidence = {f["id"]: {} for f in facets}  # facet -> url -> [quotes]
    emitted_total = kept_total = failed_total = 0
    seen: dict[str, str | None] = {}

    def gather_round(qs: list[tuple[str, str]]):
        urls = []
        for _, q in qs:
            urls += [u for u in search(q, topk)[:topk] if u not in seen]
        urls = list(dict.fromkeys(urls))
        with cf.ThreadPoolExecutor(4) as ex:
            for u, txt in zip(urls, ex.map(fetch, urls)):
                seen[u] = txt
                if txt:
                    (out / f"src_{hashlib.sha1(u.encode()).hexdigest()[:8]}.md").write_text(txt)
        return [u for u in urls if seen.get(u)]

    def process_bm25(urls: list[str]):
        nonlocal emitted_total, kept_total, calls
        for f in facets:
            q = f["name"] + " " + " ".join(f["questions"])
            cands = []  # (score, url, sentence)
            for u in urls:
                cands += [(sc, u, t) for sc, t in bm25_top(sentences(seen[u]), q, PER_SOURCE)]
            cands = sorted(cands, key=lambda z: -z[0])[:PER_JUDGE]
            if not cands:
                continue
            keep = judge(model, f, [(u, t) for _, u, t in cands])
            calls += 1
            emitted_total += len(cands)
            kept_total += len(keep)
            for i in keep:
                evidence[f["id"]].setdefault(cands[i][1], []).append(cands[i][2])

    def process_ce(urls: list[str]):
        """No-LLM extractor: BM25 pool -> CPU cross-encoder rerank -> keep top by score."""
        nonlocal emitted_total, kept_total
        from pipeline.tune import ce_score
        ce_model = CE_ML if re.search(r"[\u00bf\u00a1\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]", query.lower()) else CE_EN  # ponytail: accent heuristic, use langdetect for more languages
        pools = {u: sentences(seen[u]) for u in urls}
        for f in facets:
            q = f["name"] + " " + " ".join(f["questions"])
            cands = []
            for u in urls:
                cands += [(u, t) for _, t in bm25_top(pools[u], q, POOL)]
            scored = sorted(((ce_score(q, t, ce_model), u, t) for u, t in cands), key=lambda z: -z[0])
            emitted_total += len(scored)
            for sc, u, t in scored[:PER_JUDGE]:
                if sc > CE_MIN[ce_model]:
                    kept_total += 1
                    evidence[f["id"]].setdefault(u, []).append(t)

    def process_llm(urls: list[str]):
        nonlocal emitted_total, kept_total, failed_total, calls
        for u in urls:
            kept, emitted, failed = extract(model, facets, seen[u])
            failed_total += failed
            calls += min(len(chunks(seen[u])), MAX_CHUNKS)
            emitted_total += emitted
            kept_total += len(kept)
            for s in kept:
                if s["facet_id"] in ids:
                    evidence[s["facet_id"]].setdefault(u, []).append(s["quote"])

    process = {"ce": process_ce, "bm25": process_bm25, "llm": process_llm}[extractor]
    process(gather_round([(f["id"], q) for f in facets for q in f["questions"]]))
    for _ in range(2):  # gap rounds
        gaps = [f for f in facets if len(evidence[f["id"]]) < 2]
        if not gaps:
            break
        process(gather_round([(f["id"], f"{f['name']} {query}") for f in gaps]))

    covered = sum(1 for f in facets if len(evidence[f["id"]]) >= 2)
    metrics = {
        "query": query, "model": model, "extractor": extractor, "facets": len(facets), "facets_covered_2src": covered,
        "coverage": covered / len(facets), "quotes_emitted": emitted_total, "quotes_kept": kept_total,
        "quote_match_rate": kept_total / emitted_total if emitted_total else 0.0,
        "sources_fetched": sum(1 for v in seen.values() if v), "extract_calls": calls, "extract_failed": failed_total,
        "sources_per_facet": {f["id"]: len(evidence[f["id"]]) for f in facets},
        "wall_s": round(time.time() - t0, 1),
    }
    (out / "evidence.json").write_text(json.dumps({"facets": facets, "evidence": evidence}, indent=1))
    (out / "metrics.json").write_text(json.dumps(metrics, indent=1))
    return metrics


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--model", default="deepdelve-gpt-oss:latest")
    ap.add_argument("--out", default="")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--extractor", choices=["ce", "bm25", "llm"], default="ce")
    a = ap.parse_args()
    out = Path(a.out or f"research_output/pipeline_{int(time.time())}")
    print(json.dumps(run(a.query, a.model, out, a.topk, a.extractor), indent=1))
