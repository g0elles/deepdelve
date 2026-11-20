"""Order-of-magnitude rate conflict probe (PREREG.md). Repo root: python eval/source_figure_probe/probe.py -> runs/fires.json"""
import glob, json, os, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "eval/writer_ab"))
UNITS = {  # surface form -> (dimension, SI factor)
    **{u: ("mass", 1.0) for u in ("kg", "kgs", "kilogram", "kilograms")}, **{u: ("mass", 1e-3) for u in ("g", "gram", "grams")},
    **{u: ("mass", 0.4536) for u in ("lb", "lbs", "pound", "pounds")}, **{u: ("mass", 1000.0) for u in ("tonne", "tonnes", "metric ton", "metric tons", "t")},
    **{u: ("mass", 907.2) for u in ("ton", "tons")},
    "wh": ("energy", 3.6e3), "kwh": ("energy", 3.6e6), "mwh": ("energy", 3.6e9), "gwh": ("energy", 3.6e12), "therm": ("energy", 1.055e8),
    "therms": ("energy", 1.055e8), "mmbtu": ("energy", 1.055e9), "million btu": ("energy", 1.055e9), "btu": ("energy", 1055.0),
    "gj": ("energy", 1e9), "mj": ("energy", 1e6), "kj": ("energy", 1e3),
    **{u: ("volume", 1e-3) for u in ("l", "liter", "liters", "litre", "litres")}, **{u: ("volume", 3.785e-3) for u in ("gallon", "gallons", "gal")},
    **{u: ("volume", 1.0) for u in ("m3", "m³", "cubic meter", "cubic metre")}, **{u: ("volume", 0.0283) for u in ("cubic foot", "cubic feet", "ft3", "cf")},
    "mcf": ("volume", 28.3), **{u: ("length", 1000.0) for u in ("km", "kilometer", "kilometre")}, **{u: ("length", 1609.0) for u in ("mile", "miles", "mi")},
    **{u: ("area", 1.0) for u in ("m2", "m²", "square meter", "square metre")}, **{u: ("area", 0.0929) for u in ("sq ft", "square foot", "square feet")},
    **{u: ("area", 1e4) for u in ("ha", "hectare", "hectares")}, **{u: ("area", 4047.0) for u in ("acre", "acres")},
}
U = "|".join(sorted(map(re.escape, UNITS), key=len, reverse=True))
RATE = re.compile(rf"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s?({U})\b(?:\s+(?:of\s+)?[\w₂\-]+){{0,3}}?\s*(?:per|/|a|an)\s+(?:1\s+|one\s+)?({U})\b", re.I)
STOP = {"that", "this", "with", "from", "than", "which", "their", "about", "more", "less", "each", "into", "over"}


def rates(text):
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        for m in RATE.finditer(sent):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            (dn, fn), (dd, fd) = UNITS[m.group(2).lower()], UNITS[m.group(3).lower()]
            if v == 0 or dn == dd and fn == fd:
                continue
            words = {w for w in re.findall(r"[a-z₂]{4,}", sent.lower()) if w not in STOP and w not in UNITS}
            out.append({"fig": m.group(0), "dims": (dn, dd), "si": v * fn / fd, "words": words, "sent": sent.strip()[:300]})
    return out


def main():
    import skeleton_replay as S
    dirs = [os.path.dirname(p) for p in S.DIRS] + [os.path.join(ROOT, "research_output/smoke_overview_slots_20261119")]
    fires = []
    for d in dirs:
        src = {os.path.basename(f): rates(open(f, errors="ignore").read()) for f in glob.glob(os.path.join(d, "src_*.md"))}
        ev = json.load(open(os.path.join(d, "evidence.json")))
        from pipeline.bridge import source_filename
        for by in ev["evidence"].values():
            for url, quotes in by.items():
                own = source_filename(url)
                for q in quotes:
                    for r in rates(q):
                        bad = [(fn, o) for fn, rs in src.items() if fn != own for o in rs
                               if o["dims"] == r["dims"] and o["words"] & r["words"] and max(o["si"], r["si"]) / min(o["si"], r["si"]) >= 5]
                        if bad:
                            fires.append({"dir": os.path.relpath(d, ROOT), "quote": q[:300], "fig": r["fig"],
                                          "others": sorted({f"{fn}: {o['fig']} | {o['sent']}" for fn, o in bad})[:6]})
    seen, uniq = set(), []
    for f in fires:
        if (f["dir"], f["fig"], f["quote"]) not in seen:
            seen.add((f["dir"], f["fig"], f["quote"])); uniq.append(f)
    os.makedirs(os.path.join(ROOT, "eval/source_figure_probe/runs"), exist_ok=True)
    json.dump(uniq, open(os.path.join(ROOT, "eval/source_figure_probe/runs/fires.json"), "w"), ensure_ascii=False, indent=1)
    print(len(uniq), "fires;", sum("smoke" in f["dir"] for f in uniq), "in smoke")


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "src"))
    main()
