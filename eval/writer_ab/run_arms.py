"""Stage 1 of the writer A/B (wiki: Writer Role A/B Protocol): the ten keyed findings sets of the comprehension diagnostic, written by arms
A1 to A4 (A0 is eval/comprehension/run_writing.py, with DD_SEED). Every arm gets the same numbered findings, so every defect is the writer's.
    A1  H3 prompt only, free text, [n] citations in the prose
    A2  A1 plus citation ids restricted to the finding numbers by a JSON schema (cites before text)
    A3  compiled writer (src/writer/compiled.py), one draft, one repair, verbatim fallback
    A4  compiled writer, best of three drafts
Env: DD_MODEL (required), DD_ARMS (default A1,A2,A3,A4), DD_SEEDS (default 0,1,2), DD_PAPERS (comma list of ids), DD_CTX (default 65536).
Output: eval/writer_ab/out/<model>/<arm>_s<seed>/<paper>.md (reports, for blind labelling) and metrics.jsonl (deterministic measures per report).
GPU: one to a few model calls per paper per arm per seed."""
import glob, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT + "/src")
from writer import compiled as wc                 # noqa: E402
from pipeline.llm import call_json                # noqa: E402
from utils.thinking import minimal_think          # noqa: E402

KEYS = ROOT + "/eval/comprehension/keys"
_MANIFEST = ROOT + "/eval/comprehension/papers/manifest.json"   # local data (gitignored); the keys are tracked
MAN = {m["id"]: m for m in json.load(open(_MANIFEST))} if os.path.exists(_MANIFEST) else {}
MODEL = os.environ.get("DD_MODEL", "")
CTX = int(os.environ.get("DD_CTX", 65536))

H3 = ("You are writing a short report about one research study, using ONLY the numbered findings below. State the facts the findings give, "
      "the caveats the findings give, and the gaps the findings name. Rules: no number, name, abbreviation or expansion of an abbreviation "
      "that is not in the findings; no evaluation, praise or advice of your own (call a result solid or weak only if a finding says so); "
      "every sentence ends with the number(s) of the finding(s) it rests on, written [n]; at most 350 words. Write in the language of the request.")


def findings_of(key: dict) -> list[tuple[int, str, bool]]:
    """(n, text, is_gap): one numbered finding per keyed answer; a NOT STATED answer is a gap, kept as a finding the writer may cite."""
    out = []
    for n, q in enumerate(key["qs"], 1):
        if q["a"].startswith("NOT STATED"):
            why = q["a"].split(".", 1)[1].strip() if "." in q["a"] else q["a"]
            out.append((n, f"Gap: the source does not state the answer to: {q['q']} ({why})", True))
        else:
            out.append((n, f"{q['q']} {q['a']}", False))
    return out


def title_of(pid: str) -> str:
    return MAN.get(pid, {}).get("title", pid)


def request_of(pid: str) -> str:
    return f"What did this study find, how solid is the evidence, and what does it leave unanswered? Study: {title_of(pid)}"


def prompt_of(pid: str, fs) -> str:
    return H3 + "\n\nRequest: " + request_of(pid) + "\n\nFindings:\n" + "\n".join(f"[{n}] {t}" for n, t, _ in fs)


def llm_text(prompt: str, seed: int) -> str:
    import httpx
    body = {"model": MODEL, "stream": False, "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.2, "num_ctx": CTX, "seed": seed, "num_predict": 4000}}
    th = minimal_think(MODEL)
    if th is not None:
        body["think"] = th
    return httpx.post("http://localhost:11434/api/chat", json=body, timeout=900).json()["message"]["content"]


def arm_a1(pid, fs, seed):
    return llm_text(prompt_of(pid, fs), seed), {}


def arm_a2(pid, fs, seed):
    ids = [n for n, _, _ in fs]
    schema = {"type": "object", "required": ["paragraphs"], "properties": {"paragraphs": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
        "type": "object", "required": ["cites", "text"], "properties": {
            "cites": {"type": "array", "minItems": 1, "items": {"type": "integer", "enum": ids}}, "text": {"type": "string"}}}}}}
    obj = call_json(MODEL, prompt_of(pid, fs) + "\n\nReturn JSON: paragraphs, each with `cites` (finding numbers, first) and `text` (the paragraph, without citation numbers).",
                    schema, num_ctx=CTX, think="minimal", timeout=300, temperature=0.2, seed=seed)
    return "\n\n".join(p["text"].rstrip() + "".join(f"[{n}]" for n in sorted(set(p["cites"]))) for p in obj["paragraphs"]), {}


def compiled_of(pid, fs):
    quotes = [wc.Quote(n, t, "f1") for n, t, gap in fs if not gap]
    facet = wc.Facet("f1", "Findings", quotes, [q["q"] for q in json.load(open(f"{KEYS}/{pid}.json"))["qs"] if q["a"].startswith("NOT STATED")])
    return wc.Compiled(request_of(pid), [facet], [(n, f"finding://{pid}/{n}", f"Finding {n}") for n, _, gap in fs if not gap])


def arm_compiled(drafts):
    def run(pid, fs, seed):
        c = compiled_of(pid, fs)
        llm = lambda prompt, schema, temperature, s: call_json(MODEL, prompt, schema, num_ctx=CTX, think="minimal", timeout=300, temperature=temperature, seed=seed * 1000 + s)
        written, stats = wc.compose(c, llm, drafts)
        stats["units"] = len(written)
        stats["extractive_units"] = sum(1 for u in written.values() for x in u if x.get("extractive"))
        return wc.render(c, written), stats
    return run


ARMS = {"A1": arm_a1, "A2": arm_a2, "A3": arm_compiled(1), "A4": arm_compiled(3)}


def metrics(text: str, fs, pid: str) -> dict:
    """Deterministic measures that need no labeller: citation ids outside the findings, figures, abbreviations and capitalised names that no
    finding (or the title) contains, uncited sentences, words. Code-rendered quote bullets and the Sources block are excluded: they are literal
    by construction (the protocol's denominator rule)."""
    body = re.split(r"^#+\s*(?:Sources|Evidence|Evidencia)\b.*$", text, flags=re.M | re.I)[0]
    body = re.sub(r'^- (?:"|\*\*).*$', "", body, flags=re.M)  # code-rendered quote bullets and gap lines (a free-text arm's own bullets stay in)
    src = " ".join(t for _, t, _ in fs) + " " + title_of(pid) + " " + request_of(pid)
    ids = [int(x) for grp in re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", body) for x in re.findall(r"\d+", grp)]
    allowed = {n for n, _, _ in fs}
    body_s = re.sub(r"\?(?=\s+[A-Z0-9-])", "?\u2063", body)   # finding text is "question answer": the question mark is not a sentence end
    sents = [s for s in re.split(r"(?<=[.!?\]])\s+(?=[A-Z0-9\"\u201c\u00bf\u00a1])|\n+", re.sub(r"^#+.*$", "", body_s, flags=re.M)) if len(s.split()) >= 5]
    prose = re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", "", re.sub(r"^#+.*$", "", body, flags=re.M))
    return {"words": len(text.split()), "cites": len(ids), "invalid_ids": sum(1 for i in ids if i not in allowed),
            "uncited_sentences": sum(1 for s in sents if not re.search(r"\[\d+", s)), "sentences": len(sents),
            "figures_absent": sorted(wc.numbers(prose) - wc.numbers(src)),
            "abbreviations_absent": sorted(a for a in set(wc._ACRONYM.findall(prose)) if a not in src),
            "names_absent": [n for ln in prose.splitlines() for n in wc.names(ln) if wc._fold(n) not in wc._fold(src)]}


def main():
    arms = os.environ.get("DD_ARMS", "A1,A2,A3,A4").split(",")
    seeds = [int(x) for x in os.environ.get("DD_SEEDS", "0,1,2").split(",")]
    only = set(filter(None, os.environ.get("DD_PAPERS", "").split(",")))
    tag = re.sub(r"\W+", "_", MODEL)
    for arm in arms:
        for seed in seeds:
            d = f"{HERE}/out/{tag}/{arm}_s{seed}"
            os.makedirs(d, exist_ok=True)
            for kf in sorted(glob.glob(KEYS + "/*.json")):
                key = json.load(open(kf)); pid = key["id"]
                if only and pid not in only or os.path.exists(f"{d}/{pid}.md"):
                    continue
                fs = findings_of(key)
                t0 = time.time()
                try:
                    text, stats = ARMS[arm](pid, fs, seed)
                except Exception as e:                       # a failed call is data, not a crash
                    text, stats = "", {"error": f"{type(e).__name__}: {e}"[:160]}
                open(f"{d}/{pid}.md", "w").write(text)
                row = {"model": MODEL, "arm": arm, "seed": seed, "paper": pid, "secs": round(time.time() - t0, 1), **stats, **metrics(text, fs, pid)}
                open(f"{HERE}/out/{tag}/metrics.jsonl", "a").write(json.dumps(row) + "\n")
                print(arm, seed, pid, {k: row[k] for k in ("secs", "words", "invalid_ids", "uncited_sentences")}, flush=True)


if __name__ == "__main__":
    main()
