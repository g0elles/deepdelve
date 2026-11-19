"""Minimal pairs for the compiled writer's meaning rules (publish path design, Phase 2.4). Offline, no model.
Negatives: written sentences of the compiled arms (A3, A4, A3b, A4b) in stage-1 reports that no stage-1 label quotes. Positives: one controlled
edit of a negative per rule shape. Prints, per rule: positives built and rejected, negatives and false alarms, and the units that would now fail.
Usage (repo root): python eval/writer_ab/minimal_pairs.py"""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(HERE + "/../../src"))
from writer import compiled as wc  # noqa: E402

# two_source_comparison was measured here on 2026-11-14 (364/364 positives, 74/529 false alarms = 14%) and removed from compiled.check per the
# design's "a rule that misses its numbers is removed, not tuned"; its generator stays so a redesign can be re-measured.
CODES = ("figure_context", "two_source_comparison", "hedge_dropped", "stale_year")
_SENT = re.compile(r"(.+?[.!?])((?:\[\d+\])+)(?=\s|$)")
_FIG = re.compile(r"\b(to|by) (\$?\d[\d,.]*\d%?)")


def units(md):
    """[(unit name, qmap, [(text, cites), ...])] for one compiled report: the overview reads the first two sources' quotes, as the stage-1 compose() did (these reports predate the bridging-first pool)."""
    sections = re.split(r"^## ", md, flags=re.M)[1:]
    qmap = {}
    for m in re.finditer(r'^- "(.*)" \[(\d+)\]', md, flags=re.M):
        qmap.setdefault(int(m.group(2)), []).append(m.group(1))
    qmap = {n: " ".join(v) for n, v in qmap.items()}
    out = []
    for sec in sections:
        name, _, body = sec.partition("\n")
        if name.strip() in ("Evidence gaps", "Sources", "Where sources differ", "Not covered"):
            continue
        prose = body.split("**Evidence**")[0]
        sents = [(m.group(1).strip(), [int(n) for n in re.findall(r"\d+", m.group(2))]) for m in _SENT.finditer(prose)]
        q = dict(list(qmap.items())[:2]) if name.strip() == "Answer in brief" else qmap
        out.append((name.strip(), q, sents))
    return out


def codes(text, cites, q):
    return {d.split(":")[0] for d in wc.check({"cites": cites, "text": text}, q, "") if d.split(":")[0] in CODES}


def positives(text, cites, q):
    """{code: edited (text, cites, qmap)} for the shapes this sentence admits."""
    out = {}
    cited = " ".join(q[c] for c in cites if c in q)
    qfig = wc._figure_tokens(cited)
    for m in _FIG.finditer(text):  # only a figure the cited quote frames with the same preposition: otherwise the swap is undecidable
        key = wc._NUM.search(m.group(2)).group().replace(",", "")
        if not wc._YEAR.fullmatch(key) and any(k == key and p == m.group(1) for k, p, _ in qfig):
            out["figure_context"] = (text[:m.start(1)] + {"to": "by", "by": "to"}[m.group(1)] + text[m.end(1):], cites, q)
            break
    h = wc._HEDGE.search(text)
    if h and wc._HEDGE.search(cited):
        t2 = text[:h.start()] + "causes" + text[h.end():]
        if not wc._HEDGE.search(t2):  # a second hedge left in the sentence means the hedge was not dropped
            out["hedge_dropped"] = (t2, cites, q)
    ys = wc._YEAR.findall(text)
    if ys and wc._STATUS.search(text):
        newest = max(int(y) for y in wc._YEAR.findall(" ".join(q.values())) + ys)
        q2 = dict(q)
        other = next((n for n in q2 if n not in cites), next(iter(q2)))
        q2[other] = q2[other] + f" In {newest} the work continued."
        out["stale_year"] = (re.sub(re.escape(min(ys, key=int)), str(newest - 1), text, count=1), cites, q2)
    figs = [f for f in wc._NUM.findall(text) if len(wc._digits(f)) >= 2 and not wc._YEAR.fullmatch(f)]
    other = next((n for n in q if n not in cites and any(len(wc._digits(f)) >= 2 and not wc._YEAR.fullmatch(f) for f in wc._NUM.findall(q[n]))), None)
    if figs and other:
        f2 = next(f for f in wc._NUM.findall(q[other]) if len(wc._digits(f)) >= 2 and not wc._YEAR.fullmatch(f))
        out["two_source_comparison"] = (text.rstrip(".") + f", versus {f2}.", cites + [other], q)
    return out


def main():
    amap, labels = {}, {}
    for f in glob.glob(f"{HERE}/out/blind_map*.json"):
        amap.update({i["id"]: i for i in json.load(open(f))})
        lab = f.replace("blind_map", "labels_seed0")
        if os.path.exists(lab):
            labels.update(json.load(open(lab)))
    pos = {c: [0, 0] for c in CODES}
    neg = {c: 0 for c in CODES}
    n_neg = n_units = failing_units = 0
    for rid, item in amap.items():
        if item["arm"] not in ("A3", "A4", "A3b", "A4b") or rid not in labels or not os.path.exists(item["file"]):
            continue
        frags = [f.casefold() for d in labels[rid].get("defects", []) for f in re.findall(r"'([^']{8,})'", d)]
        for _, q, sents in units(open(item["file"]).read()):
            n_units += 1
            unit_fail = False
            for text, cites in sents:
                if any(f[:40] in text.casefold() for f in frags):
                    continue
                n_neg += 1
                fired = codes(text, cites, q)
                unit_fail |= bool(fired)
                for c in fired:
                    neg[c] += 1
                for c, (t2, c2, q2) in positives(text, cites, q).items():
                    pos[c][0] += 1
                    pos[c][1] += c in codes(t2, c2, q2)
            failing_units += unit_fail
    print(f"compiled-arm units {n_units}, unlabelled sentences (negatives) {n_neg}")
    for c in CODES:
        print(f"{c:24s} positives {pos[c][0]:4d}  rejected {pos[c][1]:4d}   negatives {n_neg}  false alarms {neg[c]}")
    print(f"units with a sentence any new rule rejects (would now need repair or fall back): {failing_units}/{n_units} = {failing_units / max(n_units, 1):.1%}")


if __name__ == "__main__":
    main()
