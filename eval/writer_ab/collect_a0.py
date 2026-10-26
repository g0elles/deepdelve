"""Copy the baseline arm's reports (eval/comprehension/run_writing.py, tag ab_<model>_s<seed>) into the same out/ layout as the other arms and
compute the same deterministic metrics, so blind labelling and the tables treat all five arms alike. Env: DD_MODEL, DD_SEEDS (default 0,1,2)."""
import glob, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_arms as ra                                    # noqa: E402

model = os.environ["DD_MODEL"]
tag = re.sub(r"\W+", "_", model)
for seed in [int(x) for x in os.environ.get("DD_SEEDS", "0,1,2").split(",")]:
    d = f"{HERE}/out/{tag}/A0_s{seed}"
    os.makedirs(d, exist_ok=True)
    for kf in sorted(glob.glob(ra.KEYS + "/*.json")):
        key = json.load(open(kf)); pid = key["id"]
        src = f"{ra.ROOT}/eval/comprehension/results/W_ab_{tag}_s{seed}_{pid}.md"
        if not os.path.exists(src):
            continue
        text = open(src).read()
        open(f"{d}/{pid}.md", "w").write(text)
        row = {"model": model, "arm": "A0", "seed": seed, "paper": pid, **ra.metrics(text, ra.findings_of(key), pid)}
        open(f"{HERE}/out/{tag}/metrics.jsonl", "a").write(json.dumps(row) + "\n")
print("collected A0 for", model)
