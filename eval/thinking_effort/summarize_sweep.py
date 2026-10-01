import json, statistics as st, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1] if len(sys.argv) > 1 else "eval/thinking_effort/raw_sweep.jsonl")]
g = collections.defaultdict(list)
for r in rows: g[(r["prompt"], r["setting"])].append(r)
order = ["false", "omitted", "low", "medium", "high"]
for prompt in ("prose", "tool"):
    print(f"\n== {prompt} prompt ({'tool call must write notes.md with >=40 words' if prompt == 'tool' else '250-word summary'})")
    print(f"{'setting':8s} {'n':>2s} {'tokens (min/med/max)':>24s} {'think chars (med)':>18s} {'wall s (med)':>13s} {'capped':>7s}" + ("  tool_ok" if prompt == "tool" else ""))
    for s in order:
        rs = [r for r in g.get((prompt, s), []) if "error" not in r]
        if not rs: continue
        tk = [r["eval_count"] for r in rs]
        line = f"{s:8s} {len(rs):2d} {min(tk):7d}/{int(st.median(tk)):6d}/{max(tk):6d} {int(st.median([r['thinking_chars'] for r in rs])):18d} {st.median([r['wall_s'] for r in rs]):13.1f} {sum(r['done']=='length' for r in rs):7d}"
        if prompt == "tool": line += f"  {sum(bool(r['tool_ok']) for r in rs)}/{len(rs)}"
        print(line)
errs = [r for r in rows if "error" in r]
print("\nerrors:", len(errs), [e["error"][:60] for e in errs[:3]])
