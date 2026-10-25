import glob, json, sys
keys = {}
for f in sorted(glob.glob("eval/comprehension/keys/*.json")):
    k = json.load(open(f)); keys[k["id"]] = k["qs"]
arm = sys.argv[1]; n = int(sys.argv[3]) if len(sys.argv) > 3 else 520
for l in open(f"eval/comprehension/results/R_{arm}.jsonl"):
    r = json.loads(l)
    if r["paper"] not in sys.argv[2].split(","): continue
    q = keys[r["paper"]][r["i"]]
    a = r["answer"].replace("\n", " ")
    print(f"## {r['paper']}.{r['i']} [{q['t']}] {q['q'][:150]}\nKEY: {q['a'][:330]}\nANS({len(r['answer'])}): {a[:n]}\n")
