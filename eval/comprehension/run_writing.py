"""Test W (writing from perfect input): build a hand-checked findings.md per paper from the key sheet, give it to the real Builder instructions, save the report.
Usage: python run_writing.py            -> results/W_<id>.md (local, gitignored) and results/W_findings_<id>.md
The findings are true by construction (they are my keyed answers), so any defect in the report is the writer's. See PROTOCOL.md."""
import glob, json, os, sys, time, httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src"))
import prompts  # noqa: E402

MODEL = os.environ.get("DD_MODEL", "deepdelve-gpt-oss:latest")
TAG = os.environ.get("DD_TAG", "")
THINK = {"omit": None, "false": False, "low": "low", "medium": "medium", "high": "high"}[os.environ.get("DD_THINK", "false")]  # the agent sends false (enable_thinking: false)
MAN = {m["id"]: m for m in json.load(open(os.path.join(HERE, "papers", "manifest.json")))}



def sampling(predict_default: int) -> dict:
    """Per-model sampling from env (defaults reproduce the earlier runs: temperature 0.2, seed 42). DD_TEMP, DD_TOP_P, DD_TOP_K, DD_MIN_P, DD_PRESENCE, DD_REPEAT, DD_PREDICT."""
    o = {"temperature": float(os.environ.get("DD_TEMP", 0.2)), "num_ctx": int(os.environ.get("DD_CTX", 65536)), "seed": 42,
         "num_predict": int(os.environ.get("DD_PREDICT", predict_default))}
    for env, opt, cast in (("DD_TOP_P", "top_p", float), ("DD_TOP_K", "top_k", int), ("DD_MIN_P", "min_p", float), ("DD_PRESENCE", "presence_penalty", float), ("DD_REPEAT", "repeat_penalty", float)):
        if os.environ.get(env):
            o[opt] = cast(os.environ[env])
    return o

def url(pid: str) -> str:
    m = MAN[pid]
    return f"https://pmc.ncbi.nlm.nih.gov/articles/{m['pmcid']}/" if m["pmcid"] else f"https://doi.org/{m['doi']}"


def findings(key: dict) -> str:
    pid = key["id"]
    lines = [f"### [{MAN[pid]['title']}]({url(pid)}) (saved as sources/{pid}.md)"]
    for q in key["qs"]:
        if q["a"].startswith("NOT STATED"):
            lines.append(f"- Gap: the source does not state the answer to: {q['q']} ({q['a'].split('.', 1)[1].strip() if '.' in q['a'] else q['a']})")
        else:
            lines.append(f"- {q['q']} Finding: {q['a']}")
    return "\n".join(lines) + "\n"


def system(task: str) -> str:
    quotas = {k + "_quota": 10 for k in ("read_workspace_file", "grep_workspace_file", "write_workspace_file", "edit_workspace_file")}
    return prompts.BUILDER_INSTRUCTIONS.format(date="2026-10-24", task_name=task, delegation_instructions="",
                                               report_style_instructions=prompts.STANDARD_REPORT_STYLE_INSTRUCTIONS,
                                               citation_format_instructions=prompts.STANDARD_CITATION_FORMAT_INSTRUCTIONS, **quotas)


def main():
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    for kf in sorted(glob.glob(os.path.join(HERE, "keys", "*.json"))):
        key = json.load(open(kf))
        pid = key["id"]
        out = os.path.join(HERE, "results", f"W_{TAG + '_' if TAG else ''}{pid}.md")
        if os.path.exists(out):
            continue
        f = findings(key)
        open(os.path.join(HERE, "results", f"W_findings_{pid}.md"), "w").write(f)
        task = f"Write final_report.md: a report on what this study found, how solid the evidence is, and what it leaves unanswered. Title of the study: {MAN[pid]['title']}"
        user = ("The contents of findings.md are below (you cannot call tools in this session, so treat it as already read). "
                f"Write the complete final_report.md now and reply with only its markdown text.\n\n<findings.md>\n{f}</findings.md>")
        body = {"model": MODEL, "stream": False, **({"think": THINK} if THINK is not None else {}), "messages": [{"role": "system", "content": system(task)}, {"role": "user", "content": user}],
                "options": sampling(4000)}
        t0 = time.time()
        r = httpx.post("http://localhost:11434/api/chat", json=body, timeout=900).json()
        open(out, "w").write(r["message"]["content"])
        print(pid, r.get("eval_count"), round(time.time() - t0, 1), flush=True)


if __name__ == "__main__":
    main()
