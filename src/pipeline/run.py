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
        "type": "object", "required": ["id", "name", "entity", "questions"], "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "entity": {"type": "string"},
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


_BIB = re.compile(r"\bpp\.|\bvol\.|Journal of|Proceedings of|accessed on|search terms|(?:should be|were) extracted|inclusion criteria|\(\d{4}[a-z]?\)[,'\u2018\u201c\"]", re.I)


def reflow(text: str) -> list[str]:
    """PDF-derived pages hard-wrap mid-sentence (even with blank lines between wraps). Join a line onto the previous one
    unless that one ended a sentence or either is a heading/list/table line. Found losing 'commuting durations in Santiago
    typically ranging between 40 and 60 minutes' as an unpunctuated fragment."""
    paras: list[str] = []
    for line in (ln.strip() for ln in text.split("\n")):
        if not line:
            continue
        structural = line.startswith(("#", "|", "*", "-", ">", "!")) or (paras and paras[-1].startswith(("#", "|", "*", "-", ">", "!")))
        if paras and not structural and not re.search(r"[.!?:;\"\u201d)\]]$", paras[-1]):
            paras[-1] += " " + line
        else:
            paras.append(line)
    return paras


_JUNK = re.compile(r"\u00e2\u20ac|\u00c3[\u0080-\u00bf]|\ufffd|[a-z]{28,}|\bretrieved \d{4}-\d\d|\bused for the (?:dates|death|figures?)")  # mojibake, run-together words, citation notes


def sentences(text: str) -> list[str]:
    out = []
    for para in reflow(text):
        for sent in re.split(r"(?<=[.!?])\s+", clean(para)):
            if (40 <= len(sent) <= 400 and sum(c.isalpha() for c in sent) > 0.5 * len(sent)
                    and sent[-1] in '.!?"\u201d)' and not sent.startswith(("#", "|")) and "](" not in sent and "\u2023" not in sent
                    and not _BIB.search(sent) and not _JUNK.search(sent)):
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
        return [i for i in call_json(model, p, JUDGE_SCHEMA, think="low", timeout=120)["keep"] if 0 <= i < len(cands)]
    except Exception:
        return []


CE_POOL = 40
MIN_OWN = 3
JUDGE_N = 20


def specificity(t: str) -> float:
    """Longer, number-bearing sentences were likelier real facts on the hand-labeled Spanish sheet (AUC 0.70 vs CE 0.53)."""
    return min(len(t), 250) / 250 + 0.3 * bool(re.search(r"\d", t))


def _fold(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()


_COUNTRIES = {k: re.compile(v) for k, v in {
    "mx": r"\bmexic", "co": r"\bcolomb", "cl": r"\bchile", "ar": r"\bargentin", "pe": r"\bperu\b|\bperuan", "ec": r"\becuador",
    "ve": r"\bvenezuel", "bo": r"\bbolivia", "uy": r"\buruguay", "py": r"\bparaguay", "br": r"\bbrasil|\bbrazil",
    "es": r"\bespana|\bspain|\bspanish law", "fr": r"\bfrance|\bfrancia", "de": r"\balemania|\bgermany", "it": r"\bitaly|\bitalia",
    "pt": r"\bportugal", "us": r"\bunited states|\bestados unidos|\bu\.s\.|\bus\b", "uk": r"\bunited kingdom|\breino unido|\buk\b",
    "cn": r"\bchina\b", "jp": r"\bjapan|\bjapon", "in": r"\bindia\b", "ca": r"\bcanada", "ng": r"\bnigeria", "ke": r"\bkenya",
    "za": r"\bsouth africa", "au": r"\baustralia", "kr": r"\bsouth korea|\bcorea del sur", "tw": r"\btaiwan", "dk": r"\bdenmark|\bdinamarca",
    "no": r"\bnorway|\bnoruega", "se": r"\bsweden|\bsuecia", "nl": r"\bnetherlands|\bpaises bajos", "ru": r"\brussia|\brusia",
    "gt": r"\bguatemala", "cr": r"\bcosta rica", "pa": r"\bpanama", "cu": r"\bcuba\b", "do": r"\bdominican|\brepublica dominicana",
    "eg": r"\begypt", "tr": r"\bturkey|\bturquia", "ie": r"\bireland|\birlanda", "eu": r"\beuropean union|\bunion europea",
}.items()}


def countries(text: str) -> set[str]:
    f = _fold(text)
    return {k for k, r in _COUNTRIES.items() if r.search(f)}


def jurisdiction_ok(sent: str, qkeys: set[str]) -> bool:
    """A sentence about only OTHER countries than the query's is off-jurisdiction (Colombian law in a Mexico answer)."""
    got = countries(sent)
    return not qkeys or not got or bool(got & qkeys)


def source_off_jurisdiction(source: str, qkeys: set[str]) -> bool:
    """A page dominated by another country (>=5 mentions and 2x the query country's) is about the wrong jurisdiction.
    On the Spanish sheet: drops 14 negatives / 2 positives of 37 (n small)."""
    if not qkeys:
        return False
    f = _fold(source)
    c = {k: len(r.findall(f)) for k, r in _COUNTRIES.items()}
    top = max(c, key=c.get)
    return top not in qkeys and c[top] >= 5 and c[top] >= 2 * max(sum(c[k] for k in qkeys), 1)


_RECENT = re.compile(r"\b(recent|latest|current|currently|last (?:\w+ ){1,2}years?|newest|now|today|reciente|ultimos?|actual|vigente)\b", re.I)


_ES_W = set("el la los las de del que y en un una por con para es se al como su sus".split())
_EN_W = set("the of and to in is are was for that with as by on from this it its".split())


def _is_spanish(t: str) -> bool:
    w = re.findall(r"[a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]+", t.lower())
    return sum(x in _ES_W for x in w) >= sum(x in _EN_W for x in w)


def year_score(t: str, now: int) -> float:
    """Only for recency queries: +0.4 if the newest year cited is this year or last, -0.4 if it is 2+ years old (h15: 2024 quotes ranked as "latest" in 2026)."""
    ys = [int(y) for y in re.findall(r"\b(20[0-3]\d)\b", t)]
    if not ys:
        return 0.0
    return 0.4 if max(ys) >= now - 1 else -0.4


def _mentions(text_folded: str, ent: str) -> bool:
    ck = countries(ent)
    if ck:  # aliases: "United Kingdom" facet, "UK" in the sentence
        return bool(countries(text_folded) & ck)
    if " " in ent or "-" in ent:  # descriptive planner entities ("Mercosur-EU trade agreement", "EU Parliament") never appear verbatim
        toks = [t for t in re.findall(r"[a-z0-9]+", ent) if len(t) >= 4]
        if not toks:
            return ent in text_folded
        need = len(toks) if len(toks) <= 2 else -(-len(toks) * 2 // 3)
        return sum(t[:5] in text_folded for t in toks) >= need
    return bool(re.search(rf"\b{re.escape(ent)}\b", text_folded)) if len(ent) <= 3 else ent[:5] in text_folded


_CAP_RUN = re.compile(r"[A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]\w+(?:['\u2019]s)?(?:[ \t]+[A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]\w+(?:['\u2019]s)?)*")


def query_entities(query: str) -> set[str]:
    """Single capitalized words the user typed mid-sentence (Bogota, Lima, UK). Multi-word capitalized runs are titles
    ("Data Protection Act"), not entities that tell facets apart."""
    out = set()
    for m in _CAP_RUN.finditer(query):
        run = m.group().split()
        if len(run) == 1 and m.start() > (1 if query[:1] in "\u00bf\u00a1" else 0):
            out.add(_fold(re.sub(r"['\u2019]s$", "", run[0])))
    f = _fold(query)
    for r in _COUNTRIES.values():  # countries the capitalized-word rule drops (multi-word, or sentence-initial run "Compare Japan's")
        m = r.search(f)
        if m:
            out.add(m.group())
    return out - _STOP


def facet_entities(facets: list[dict], query: str = "") -> dict[str, set[str]]:
    """Per facet: the planner's `entity`; else the query's typed entities its name/questions single out.
    A facet naming all of them (or none) is unconstrained."""
    qe = query_entities(query)
    out = {}
    for f in facets:
        own = _fold(f.get("entity", "")).strip()
        if own:
            out[f["id"]] = {own}
            continue
        got = {e for e in qe if _mentions(_fold(f["name"] + " " + " ".join(f["questions"])), e)}
        out[f["id"]] = got if got != qe else set()
    return out


def entity_ok(fid: str, sent: str, source: str, ents: dict[str, set[str]]) -> bool:
    """An entity-specific facet only takes sentences that name its entity (else Europe-wide stats fill the Lima facet)."""
    mine = ents[fid]
    return not mine or any(_mentions(_fold(sent), e) for e in mine)


def _uncovered(facets: list[dict], query: str) -> list[str]:
    """Entities the user typed (countries, capitalized names) that no facet's name/questions/entity mentions."""
    text = _fold(" ".join(f"{f['name']} {f.get('entity', '')} {' '.join(f['questions'])}" for f in facets))
    return sorted(e for e in query_entities(query) if not _mentions(text, e))


_POLAR = set("rise rises rising decline declines declining increase increases decrease decreases improvement improvements deterioration "
             "reduction reductions benefit benefits harm harms advantage advantages disadvantage disadvantages pros cons positive negative "
             "gain gains loss losses".split())


def _merge_polar(facets: list[dict]) -> list[dict]:
    """Facets that differ only by a direction word (rise/decline, benefits/harms) are one topic: merge, keeping both facets' queries.
    Prompt bans alone did not stop the planner (h09, h18, q25); such a split also starves the narrower half."""
    out, by_core = [], {}
    for f in facets:
        toks = re.findall(r"[a-z0-9]+", _fold(f["name"]))
        core = tuple(sorted(t for t in toks if t not in _POLAR))
        if len(core) == len(toks):
            out.append(f)
        elif core in by_core:
            keep = by_core[core]
            keep["questions"] = (keep["questions"] + [q for q in f["questions"] if q not in keep["questions"]])[:3]
        else:
            f["name"] = " ".join(w for w in f["name"].split() if _fold(w) not in _POLAR) or f["name"]
            by_core[core] = f
            out.append(f)
    return out


def plan(model: str, query: str) -> list[dict]:
    return _merge_polar(_plan_covered(model, query))


def _plan_covered(model: str, query: str) -> list[dict]:
    facets = _plan(model, query)
    missing = _uncovered(facets, query)
    if missing:  # planner silently dropped a named entity (h16: Kenya); one re-plan naming it
        again = _plan(model, query, f"\n\nEvery one of these must be covered by at least one facet: {', '.join(missing)}. If that needs more than 6 "
                                     f"facets, make one facet per named entity that covers all the measures asked about it.")
        if len(_uncovered(again, query)) < len(missing):
            return again
    return facets


def _plan(model: str, query: str, hint: str = "") -> list[dict]:
    p = (f"Write facet names and search queries in the same language as the request. "
         f"Break this research request into 2-6 facets that together fully answer it. Each facet gets "
         f"an id (f1, f2, ...), a short name, and 1-3 web search queries. Each query is 4-10 words of keywords as typed "
         f"into a search engine, naming the specific things involved; no dates, parentheses, lists of examples, or "
         f"multi-part questions. Every facet must "
         f"be answerable from a single web page. Today is {time.strftime('%Y-%m-%d')}; use it only to interpret words like recent or latest, never write it into a query.  If the request compares things, make one facet per "
         f"side and aspect; do NOT make a facet for the comparison itself (it is done later). Name a law "
         f"or institution only if you are certain it exists in that jurisdiction; otherwise say "
         f"'the regulator' or 'the law' instead of guessing a name. Each facet must be a concrete topic whose answer is "
         f"stated as facts in web pages (a specific thing, measure, place, or period). Do NOT make meta facets "
         f"such as summary, limitations, gaps, population characteristics, or implications. Do NOT make facets for arguments "
         f"for or against, pros or cons, or evidence for one side: make facets for measurable topics or outcomes. "
         f"Never make two facets that are opposites of each other (increase vs decrease, improvement vs deterioration, "
         f"benefits vs harms): one facet per outcome, and the sources will show which direction it went. "
         f"Set `entity` to the one named place, country, organization, person, technology or method the facet is about "
         f"(e.g. a city, a country, a data structure), or \"\" if the facet is not about one specific named entity.\n\nRequest: {query}{hint}")
    return call_json(model, p, PLAN_SCHEMA, think="low")["facets"]  # gpt-oss: default reasoning + `format` can run >5 min and return empty JSON (q06, q21)


def search(question: str, k: int, stats: dict | None = None) -> list[str]:
    """Up to k+3 result URLs (extras cover fetch failures); one retry, since ddgs returns [] on throttling."""
    from ddgs import DDGS
    for attempt in range(2):
        try:
            urls = [r["href"] for r in DDGS().text(question, max_results=k + 3, backend="auto") if r.get("href")]
        except Exception:
            urls = []
        if urls:
            break
        time.sleep(1.5)
    if stats is not None:
        stats["searches"] = stats.get("searches", 0) + 1
        stats["empty_searches"] = stats.get("empty_searches", 0) + (not urls)
    return urls


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


def select_ce(query, facets, seen, urls, evidence, owner, funnel, cfg=None):
    """No-LLM extractor: BM25 pool -> CPU cross-encoder -> filters -> specificity rerank -> keep top.
    `cfg` flags (all default on) exist so filters can be ablated on saved sources; `funnel` records per-facet stage counts."""
    from pipeline.tune import ce_score
    cfg = {"entity": True, "juris": True, "dedup": True, "spec": True, **(cfg or {})}
    ce_model = CE_ML if re.search(r"[\u00bf\u00a1\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]", query.lower()) else CE_EN  # ponytail: accent heuristic, use langdetect for more languages
    ents, qkeys = facet_entities(facets, query), countries(query)
    es = ce_model == CE_ML
    recent, now = bool(_RECENT.search(_fold(query))), time.localtime().tm_year
    pools = {u: sentences(seen[u]) for u in urls}
    off = {u for u in urls if cfg["juris"] and source_off_jurisdiction(seen[u], qkeys)}
    emitted = kept = 0
    per_facet = {}
    for f in facets:
        q = f["name"] + " " + " ".join(f["questions"])
        cands = []
        for u in urls:
            cands += [(u, t) for _, t in bm25_top(pools[u], q, POOL)]
        scored = sorted(((ce_score(q, t, ce_model), u, t) for u, t in cands), key=lambda z: -z[0])
        emitted += len(scored)
        top = scored[:CE_POOL]
        pos = [z for z in top if z[0] > CE_MIN[ce_model]]
        ent = [z for z in pos if not cfg["entity"] or entity_ok(f["id"], z[2], seen[z[1]], ents)]
        jur = [z for z in ent if not cfg["juris"] or (jurisdiction_ok(z[2], qkeys) and z[1] not in off)]
        per_facet[f["id"]] = jur
        st = funnel.setdefault(f["id"], {"urls": 0, "cands": 0, "ce_pool": 0, "ce_pos": 0, "entity": 0, "juris": 0, "kept": 0})
        for k, v in zip(("urls", "cands", "ce_pool", "ce_pos", "entity", "juris"), (len(urls), len(scored), len(top), len(pos), len(ent), len(jur))):
            st[k] += v
    for f in facets:  # a quote already claimed by another facet is demoted, not dropped (dropping starved later facets)
        def key(z):
            spec = -specificity(z[2]) - (year_score(z[2], now) if recent else 0) if cfg["spec"] else -z[0]
            return (cfg["dedup"] and owner.get(z[2], f["id"]) != f["id"], es and not _is_spanish(z[2]), spec)
        ranked, dup = [], set()
        for z in sorted(per_facet[f["id"]], key=key):  # same sentence on two pages (mirrors, syndication) counts once per facet
            n = re.sub(r"\W+", " ", _fold(z[2])).strip()
            if n not in dup:
                dup.add(n)
                ranked.append(z)
        if cfg["dedup"]:  # a sentence another facet already kept fills this one only up to MIN_OWN (h17: same Metrocable sentence under two facets)
            own = [z for z in ranked if owner.get(z[2], f["id"]) == f["id"]]
            ranked = own + [z for z in ranked if z not in own][:max(0, MIN_OWN - len(own))]
        ranked = ranked[:JUDGE_N if cfg.get("judge") else PER_JUDGE]
        if cfg.get("judge") and ranked:
            # LLM judge: precision 0.67 / recall 0.68 vs CE-only 0.36 / 1.0 on the hand-labeled Spanish sheet. [] on error -> keep all.
            keep = judge(cfg["judge"], f, [(u, t) for _, u, t in ranked])
            ranked = [ranked[i] for i in keep] if keep else ranked
        for sc, u, t in ranked[:PER_JUDGE]:
            owner.setdefault(t, f["id"])
            kept += 1
            funnel[f["id"]]["kept"] += 1
            evidence[f["id"]].setdefault(u, []).append(t)
    return emitted, kept


def run(query: str, model: str, out: Path, topk: int, extractor: str = "ce", judge_on: bool = True) -> dict:
    t0, calls = time.time(), 0
    out.mkdir(parents=True, exist_ok=True)
    facets = plan(model, query)
    ids = {f["id"] for f in facets}
    evidence = {f["id"]: {} for f in facets}  # facet -> url -> [quotes]
    emitted_total = kept_total = failed_total = 0
    seen: dict[str, str | None] = {}

    gstats: dict = {}

    def gather_round(qs: list[tuple[str, str]]):
        """Per question keep the first `topk` URLs that actually fetch (a fetch failure no longer costs a slot)."""
        results = [search(q, topk, gstats) for _, q in qs]
        todo = list(dict.fromkeys(u for r in results for u in r if u not in seen))
        with cf.ThreadPoolExecutor(4) as ex:
            for u, txt in zip(todo, ex.map(fetch, todo)):
                seen[u] = txt
                gstats["fetch_ok"] = gstats.get("fetch_ok", 0) + bool(txt)
                gstats["fetch_fail"] = gstats.get("fetch_fail", 0) + (not txt)
                if txt:
                    (out / f"src_{hashlib.sha1(u.encode()).hexdigest()[:8]}.md").write_text(txt)
        picked = []
        for r in results:
            picked += [u for u in [u for u in r if seen.get(u)][:topk] if u in todo]
        return list(dict.fromkeys(picked))

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

    owner: dict[str, str] = {}  # sentence -> facet id that claimed it (a quote serves one facet)
    funnel: dict[str, dict] = {}

    def process_ce(urls: list[str]):
        nonlocal emitted_total, kept_total
        e, k = select_ce(query, facets, seen, urls, evidence, owner, funnel, {"judge": model if judge_on else None})
        emitted_total += e
        kept_total += k

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
    process(gather_round([(f["id"], q) for f in facets for q in f["questions"] + [f"{f['questions'][0]} {f['name']}"]]))  # question+name doubled good pages vs question alone (n=6 facets, noisy)
    for _ in range(2):  # gap rounds
        gaps = [f for f in facets if len(evidence[f["id"]]) < 2]
        if not gaps:
            break
        process(gather_round([(f["id"], f"{q} {f['name']}") for f in gaps for q in f["questions"][1:] or f["questions"]]))

    covered = sum(1 for f in facets if len(evidence[f["id"]]) >= 2)
    metrics = {
        "query": query, "model": model, "extractor": extractor, "facets": len(facets), "facets_covered_2src": covered,
        "coverage": covered / len(facets), "quotes_emitted": emitted_total, "quotes_kept": kept_total,
        "quote_match_rate": kept_total / emitted_total if emitted_total else 0.0,
        "sources_fetched": sum(1 for v in seen.values() if v), "extract_calls": calls, "extract_failed": failed_total,
        "sources_per_facet": {f["id"]: len(evidence[f["id"]]) for f in facets},
        "wall_s": round(time.time() - t0, 1), "funnel": funnel, "gather": gstats,
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
    ap.add_argument("--no-judge", action="store_true", help="skip the LLM relevance judge after the CE stage")
    ap.add_argument("--extractor", choices=["ce", "bm25", "llm"], default="ce")
    a = ap.parse_args()
    out = Path(a.out or f"research_output/pipeline_{int(time.time())}")
    print(json.dumps(run(a.query, a.model, out, a.topk, a.extractor, not a.no_judge), indent=1))
