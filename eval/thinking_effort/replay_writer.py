"""Replay the CAPTURED real FindingsWriter request (eval/thinking_effort/proxy_capture.jsonl, request id 3) N times per think setting, streaming.
Per run: the guard's own criterion (longest run of identical consecutive trimmed events over content OR thinking deltas, >100 aborts in Ollama 0.34.x),
the repeated token + what preceded it on any run with a long run or an error, and typography damage in the written tool call."""
import json, re, sys, time, urllib.request
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
OUT = "eval/thinking_effort/replay_writer.jsonl"
rows = [json.loads(l) for l in open("eval/thinking_effort/proxy_capture.jsonl")]
BODY = json.loads([r for r in rows if r["kind"] == "request" and r["id"] == 3][0]["body"])
BAD = "‑ ​­⁑‐‒"
def run(think):
    b = dict(BODY); b["think"] = think
    req = urllib.request.Request("http://localhost:11434/api/chat", json.dumps(b).encode(), {"Content-Type": "application/json"})
    t0 = time.time(); seq = []; err = None; calls = []; final = {}
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            for line in r:
                if not line.strip(): continue
                d = json.loads(line)
                if "error" in d: err = d["error"]; break
                m = d.get("message") or {}
                txt = (m.get("content") or "") or (m.get("thinking") or "")
                if txt != "": seq.append(txt.strip() if txt.strip() else "")
                if m.get("tool_calls"): calls += m["tool_calls"]
                if d.get("done"): final = d
    except Exception as e: err = f"{type(e).__name__}: {str(e)[:80]}"
    best = run_len = 0; last = None; best_end = 0
    for i, t in enumerate(seq):
        run_len = run_len + 1 if t == last else 1; last = t
        if run_len > best: best, best_end = run_len, i
    rec = {"think": think, "wall_s": round(time.time() - t0, 1), "events": len(seq), "eval_count": final.get("eval_count"), "max_identical_run": best,
           "run_token": seq[best_end] if seq else None, "error": err, "tool_calls": len(calls)}
    if best >= 30 or err: rec["context_before_run"] = seq[max(0, best_end - best - 8):best_end - best + 1]
    written = json.dumps([c["function"]["arguments"] for c in calls], ensure_ascii=False)
    rec["typography_in_written_file"] = {hex(ord(c)): written.count(c) for c in BAD if c in written}
    urls = re.findall(r"https?://[^\s)\]\"'\\]+", written)
    rec["urls_with_odd_chars"] = sum(1 for u in urls if any(c in u for c in BAD))
    return rec
with open(OUT, "a") as f:
    for i in range(N):
        for think in (False, "low"):
            rec = run(think); rec["rep"] = i; f.write(json.dumps(rec, ensure_ascii=False) + "\n"); f.flush()
            print(rec["think"], i, {k: v for k, v in rec.items() if k not in ("context_before_run",)}, flush=True)
print("REPLAY DONE", flush=True)
