"""Blind labelling packets for stage 1 (protocol: labelling plan). Usage (repo root): python eval/writer_ab/blind.py [seed=0]
Shuffles every (model, arm, paper) report of the seed into out/blind/R###.md: the numbered findings, then the report with the code-rendered
parts removed (quote bullets, gap bullets, Sources block), because those are verified by code and not labelled (denominator rule). The model,
seed and arm names are hidden; a compiled arm's headings can still betray its format, which the protocol lists as a limit. The private map is
out/blind_map.json: do not open it until the labels are written."""
import glob, json, os, random, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_arms as ra                                    # noqa: E402

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
items = []
for model_dir in sorted(glob.glob(f"{HERE}/out/*/")):
    for arm_dir in sorted(glob.glob(f"{model_dir}*_s{seed}")):
        arm = os.path.basename(arm_dir).split("_s")[0]
        for f in sorted(glob.glob(arm_dir + "/*.md")):
            items.append({"model": os.path.basename(os.path.dirname(model_dir)), "arm": arm, "paper": os.path.basename(f)[:-3], "file": f})
random.Random(7).shuffle(items)
os.makedirs(f"{HERE}/out/blind", exist_ok=True)
for i, it in enumerate(items, 1):
    it["id"] = f"R{i:03d}"
    key = json.load(open(f"{ra.KEYS}/{it['paper']}.json"))
    body = open(it["file"]).read()
    body = re.split(r"^#+\s*Sources\b.*$", body, flags=re.M | re.I)[0]    # the source list is not labelled (units.py cuts it for every arm)
    if it["arm"] in ("A3", "A4"):                        # only the compiled arms have code-rendered bullets; a free-text arm's bullets are the model's
        body = re.sub(r'^- (?:"|\*\*).*$\n?', "", body, flags=re.M)
    fs = "\n".join(f"[{n}] {t}" for n, t, _ in ra.findings_of(key))
    open(f"{HERE}/out/blind/{it['id']}.md", "w").write(f"FINDINGS (every entry is true; 'Gap' entries say what the source does not state)\n{fs}\n\nREPORT\n{body.strip()}\n")
json.dump(items, open(f"{HERE}/out/blind_map.json", "w"), indent=1)
print(len(items), "packets in", f"{HERE}/out/blind")
