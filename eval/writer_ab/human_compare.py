"""Compares the human labeller's export with the first labeller's unit labels on the same sample (wiki: Human Labelling Guide).
Usage (repo root): python eval/writer_ab/human_compare.py labels_<name>.json [more labellers' files...]
Needs out/human_units.json, out/human_map.json (private) and out/mine_units.json = {"H01": {"defect_units": [unit indices]}, ...}
written by the first labeller BEFORE the human file was opened. Prints unit agreement, Cohen's kappa, per-cell rates under each labeller, and the units they disagree on."""
import collections, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)) + "/out"


def kappa(a, b):
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    units = json.load(open(f"{HERE}/human_units.json"))
    mine = json.load(open(f"{HERE}/mine_units.json"))
    amap = {m["id"]: m for m in json.load(open(f"{HERE}/human_map.json"))}
    for path in sys.argv[1:]:
        hum = json.load(open(path))
        print(f"=== {hum.get('labeller', path)}  ({path})")
        a, b, rows, dis = [], [], collections.defaultdict(lambda: [0, 0, 0]), []
        unfinished = [r for r, v in hum["reports"].items() if v["marked"] < v["units"]]
        for hid, us in units.items():
            h = hum["reports"].get(hid, {"labels": {}})["labels"]
            m = set(mine[hid]["defect_units"])
            for i, u in enumerate(us):
                if u["k"] != "u" or str(i) not in h:
                    continue
                x, y = int(i in m), int(h[str(i)]["d"])
                a.append(x)
                b.append(y)
                c = rows[(amap[hid]["model"][:6], amap[hid]["arm"])]
                c[0] += 1
                c[1] += x
                c[2] += y
                if x != y:
                    dis.append((hid, i, "first=defect, human=OK" if x else "first=OK, human=defect", u["t"][:110], h[str(i)].get("n", "") if y else ""))
        po = sum(x == y for x, y in zip(a, b)) / len(a)
        print(f"units compared {len(a)}; raw agreement {po:.1%}; Cohen's kappa {kappa(a, b):.2f}; defects first {sum(a)}, human {sum(b)}; both {sum(x and y for x, y in zip(a, b))}")
        print("reports not fully marked:", unfinished or "none")
        print(f"{'model':8s}{'arm':5s}{'units':>6s}{'first %':>9s}{'human %':>9s}")
        for (m_, ar), (n, x, y) in sorted(rows.items()):
            print(f"{m_:8s}{ar:5s}{n:6d}{x / n:9.0%}{y / n:9.0%}")
        print("\ndisagreements (to discuss after both sets are final):")
        for d in dis:
            print(" ", *d)


if __name__ == "__main__":
    main()
