"""Test W for AstaBrief (writing from perfect input, in the model's own trained prompt format).
AstaBrief was trained on one prompt: a query plus numbered reference quotes (the card warns that other formats degrade it). The prompt text is
CC BY-NC and stays local in papers/astabrief_sft_prompt.txt (gitignored). Each keyed finding becomes one reference quote [n]; the findings are the
same true-by-construction ones run_writing.py gives the other models. Output: results/W_<DD_TAG>_<id>.md. Env: DD_MODEL, DD_TAG, DD_CTX."""
import glob, json, os, time, httpx
from run_writing import sampling

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DD_MODEL", "hf.co/mradermacher/AstaBrief_8B-GGUF:Q8_0")
TAG = os.environ.get("DD_TAG", "astabrief")
MAN = {m["id"]: m for m in json.load(open(os.path.join(HERE, "papers", "manifest.json")))}
PROMPT = open(os.path.join(HERE, "papers", "astabrief_sft_prompt.txt")).read()


def references(key: dict) -> str:
    lines = []
    for n, q in enumerate(key["qs"], 1):
        if q["a"].startswith("NOT STATED"):
            text = f"The source does not state the answer to: {q['q']} ({q['a'].split('.', 1)[1].strip() if '.' in q['a'] else q['a']})"
        else:
            text = f"{q['q']} {q['a']}"
        lines.append(f'"[{n}]": "{text}"')
    return "\n".join(lines)


def main():
    for kf in sorted(glob.glob(os.path.join(HERE, "keys", "*.json"))):
        key = json.load(open(kf)); pid = key["id"]
        out = os.path.join(HERE, "results", f"W_{TAG}_{pid}.md")
        if os.path.exists(out):
            continue
        query = f"What did this study find, how solid is the evidence, and what does it leave unanswered? Study: {MAN[pid]['title']}"
        user = PROMPT.replace("[QUERY]", query).replace("[SECTION_REFERENCES]", references(key))
        body = {"model": MODEL, "stream": False, "messages": [{"role": "user", "content": user}],
                "options": sampling(4000)}
        t0 = time.time()
        r = httpx.post("http://localhost:11434/api/chat", json=body, timeout=900).json()
        open(out, "w").write(r["message"]["content"])
        print(pid, r.get("eval_count"), round(time.time() - t0, 1), flush=True)


if __name__ == "__main__":
    main()
