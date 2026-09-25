"""Build a relevance dataset from past runs: findings.md bullets cite '(lines a-b)' of a saved source.
Positive = a source line the agent cited; negative = other prose lines of the same source (noisy:
uncited != irrelevant, so evaluate by ranking, not absolute accuracy).
Usage: python -m pipeline.dataset [out.jsonl]"""
import json
import re
import sys
from pathlib import Path

HDR = re.compile(r"^###\s*\[(.*?)\]\((.*?)\)\s*\(saved as (sources/[^)]+)\)")
LINEREF = re.compile(r"\(\s*lines?\s*([^)]*)\)", re.I)
MIN_LEN = 60


def cited_lines(bullet: str) -> set[int]:
    out = set()
    for m in LINEREF.finditer(bullet):
        body = re.sub(r"[‐-―−]", "-", m.group(1))
        for a, b in re.findall(r"(\d+)(?:\s*-\s*(\d+))?", body):
            lo, hi = int(a), int(b or a)
            if 0 <= hi - lo <= 15:
                out.update(range(lo, hi + 1))
    return out


QUOTE = re.compile(r'["\u201c]([^"\u201c\u201d\n]{40,})["\u201d]')


def run_rows(run: Path) -> list[dict]:
    from pipeline.run import norm
    try:
        query = json.load(open(run / "_run_state.json"))["query"]
        text = (run / "findings.md").read_text()
    except Exception:
        return []
    quotes = [norm(q) for q in QUOTE.findall(text)]
    quotes = [q for q in quotes if len(q) >= 30]
    cites: dict[str, set[int]] = {}
    cur = None
    for ln in text.splitlines():
        m = HDR.match(ln)
        if m:
            cur = m.group(3)
            cites.setdefault(cur, set())
        elif cur and ln.lstrip().startswith("-"):
            cites[cur] |= cited_lines(ln)
    rows = []
    for f in sorted((run / "sources").glob("*.md")):
        rel = f"sources/{f.name}"
        src = f.read_text().splitlines()
        pos = set(cites.get(rel, ()))
        for i, ln in enumerate(src, 1):
            n = norm(ln)
            if len(ln.strip()) >= MIN_LEN and any(q in n for q in quotes):
                pos.add(i)
        if not pos:
            continue  # no evidence the agent used this source: negatives would be ambiguous
        for i, ln in enumerate(src, 1):
            t = ln.strip()
            if len(t) < MIN_LEN or t.startswith(("Source-URL:", "Title:")):
                continue
            rows.append({"run": run.name, "query": query, "source": rel, "line": i, "text": t, "label": int(i in pos)})
    return rows


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "eval/pipeline_relevance.jsonl")
    runs = sorted(p for p in Path("research_output").iterdir() if (p / "findings.md").exists() and (p / "sources").is_dir())
    n_pos = n = srcs = 0
    with open(out, "w") as fh:
        for r in runs:
            rows = run_rows(r)
            srcs += len({x["source"] for x in rows})
            for x in rows:
                fh.write(json.dumps(x) + "\n")
                n += 1
                n_pos += x["label"]
    print(f"runs={len(runs)} sources={srcs} rows={n} positives={n_pos} ({n_pos / max(n, 1):.1%})")
