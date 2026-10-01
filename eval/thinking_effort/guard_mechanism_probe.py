"""What exactly trips Ollama 0.34.4's token-repeat guard? Streams /api/chat for prompts that force repeated output and reports, per run: events,
max consecutive identical trimmed events (the guard's criterion per ollama#18374/#18609), whether/where it aborted, and the repeated content."""
import json, urllib.request
M = "deepdelve-gpt-oss:latest"
CASES = {
  "u2011_x500":  "Output the character ‑ (U+2011 non-breaking hyphen) exactly 500 times in a row with no spaces and nothing else.",
  "ascii_dash_x500": "Output the character - (ASCII hyphen) exactly 500 times in a row with no spaces and nothing else.",
  "letter_a_x500": "Output the letter a exactly 500 times in a row with no spaces and nothing else.",
  "alternating_e_u2011": "Output the two-character sequence e‑ (letter e then U+2011 non-breaking hyphen) repeated 300 times with no spaces and nothing else.",
  "newlines_x300": "Output 300 blank lines (just newline characters) and nothing else.",
}
def run(prompt, think):
    body = {"model": M, "stream": True, "think": think, "options": {"num_predict": 3000}, "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    events, err, last, run_len, best, best_tok = 0, None, None, 0, 0, None
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            for line in r:
                if not line.strip(): continue
                d = json.loads(line)
                if "error" in d: err = d["error"]; break
                c = (d.get("message", {}) or {}).get("content", "")
                if c == "" and (d.get("message", {}) or {}).get("thinking"): continue   # reasoning events are not content
                events += 1; t = c.strip()
                if t == last: run_len += 1
                else: last, run_len = t, 1
                if run_len > best: best, best_tok = run_len, last
    except Exception as e:
        err = f"HTTPERROR {type(e).__name__}: {str(e)[:80]}"
    return {"events": events, "max_identical_run": best, "run_token": repr(best_tok), "error": err}
for think in ("low",):
    for name, prompt in CASES.items():
        for rep in range(3):
            print(think, name, rep, run(prompt, think), flush=True)
print("MECH DONE")
