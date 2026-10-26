"""Test R (reading ceiling): ask the agent's own model every key question with the whole paper in context. See PROTOCOL.md.
Usage: python run_reading.py A|B   (A: all questions, real analyzer role text; B: the U questions with an added 'say NOT STATED' sentence)
Output: results/R_<arm>.jsonl (local, gitignored: contains model output quoting paper text)."""
import glob, json, os, sys, time, httpx

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("DD_MODEL", "deepdelve-gpt-oss:latest")
TAG = os.environ.get("DD_TAG", "")  # "" for the agent's own model; otherwise results are R_<tag>_<arm>.jsonl
THINK = {"omit": None, "false": False, "low": "low", "medium": "medium", "high": "high"}[os.environ.get("DD_THINK", "false")]  # the agent sends false (enable_thinking: false)
ROLE = """You are the DocumentAnalyzer specialist for DeepDelve. Today is 2026-10-24.

# Role
You read and extract prose/article content from individual documents. The whole document is provided below.

# What to return
A concise answer to the research question, including:
- Key facts and data points extracted
- **Evidence, verbatim**: for each key finding, a short EXACT quote from the document, copied, not paraphrased, kept distinct from your own summary of it. A verbatim quote carries the source's own numbers/names, which is what lets the finding be verified downstream; a paraphrase strips exactly the tokens verification needs.
- Your assessment of the source quality and reliability
"""
EXTRA_B = "\nIf the document does not state the answer, say exactly 'NOT STATED' and do not guess.\n"



def sampling(predict_default: int) -> dict:
    """Per-model sampling from env (defaults reproduce the earlier runs: temperature 0.2, seed 42). DD_TEMP, DD_TOP_P, DD_TOP_K, DD_MIN_P, DD_PRESENCE, DD_REPEAT, DD_PREDICT."""
    o = {"temperature": float(os.environ.get("DD_TEMP", 0.2)), "num_ctx": int(os.environ.get("DD_CTX", 65536)), "seed": 42,
         "num_predict": int(os.environ.get("DD_PREDICT", predict_default))}
    for env, opt, cast in (("DD_TOP_P", "top_p", float), ("DD_TOP_K", "top_k", int), ("DD_MIN_P", "min_p", float), ("DD_PRESENCE", "presence_penalty", float), ("DD_REPEAT", "repeat_penalty", float)):
        if os.environ.get(env):
            o[opt] = cast(os.environ[env])
    return o

def ask(system: str, paper: str, question: str) -> dict:
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": f"<document>\n{paper}\n</document>\n\nResearch question: {question}"}]
    body = {"model": MODEL, "messages": msgs, "stream": False, **({"think": THINK} if THINK is not None else {}),
            "options": sampling(2000)}
    t0 = time.time()
    r = httpx.post("http://localhost:11434/api/chat", json=body, timeout=900).json()
    return {"answer": r["message"]["content"], "eval_count": r.get("eval_count"), "prompt_eval_count": r.get("prompt_eval_count"), "secs": round(time.time() - t0, 1)}


def main(arm: str):
    out = os.path.join(HERE, "results", f"R_{TAG + '_' if TAG else ''}{arm}.jsonl")
    done = {(j["paper"], j["i"]) for j in map(json.loads, open(out))} if os.path.exists(out) else set()
    for kf in sorted(glob.glob(os.path.join(HERE, "keys", "*.json"))):
        key = json.load(open(kf))
        pid = key["id"]
        paper = open(os.path.join(HERE, "papers", f"{pid}.md"), encoding="utf-8").read()
        for i, q in enumerate(key["qs"]):
            if arm == "B" and q["t"] != "U":
                continue
            if (pid, i) in done:
                continue
            res = ask(ROLE + (EXTRA_B if arm == "B" else ""), paper, q["q"])
            with open(out, "a") as f:
                f.write(json.dumps({"paper": pid, "i": i, "t": q["t"], **res}) + "\n")
            print(pid, i, q["t"], res["prompt_eval_count"], res["eval_count"], res["secs"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
