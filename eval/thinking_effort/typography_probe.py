"""Does gpt-oss itself emit non-breaking hyphens / narrow nbsp / zero-width chars, and inside URLs? Raw /api/chat, copy tasks, n reps per setting."""
import json, sys, urllib.request, collections
M = "deepdelve-gpt-oss:latest"
URLS = ["https://digital-strategy.ec.europa.eu/en/policies/enforcement-ai-act",
        "https://policy-insider.ai/latest-eu-ai-act-updates-tracking-delegated-acts",
        "https://kaelzhang.com/blog/eu-ai-act-deadline-extension-december-2027"]
P_COPY = "Copy these URLs exactly, character for character, one per line, nothing else:\n" + "\n".join(URLS)
P_FIND = ("Write a findings list in markdown. For each of these three sources write one bullet: a heading '### [Title](URL)' using the URL EXACTLY as given, "
          "then one sentence saying the high-risk deadline moved to 2 December 2027, with a 7% figure mentioned once.\n" + "\n".join(URLS))
BAD = {"‐": "U+2010", "‑": "U+2011", "‒": "U+2012", "–": "U+2013", "—": "U+2014", "−": "U+2212", " ": "U+00A0", " ": "U+202F", "​": "U+200B"}
def call(prompt, think):
    body = {"model": M, "stream": False, "options": {"num_predict": 4000}, "messages": [{"role": "user", "content": prompt}]}
    if think is not None: body["think"] = think
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=600))
    return r["message"].get("content") or ""
res = collections.defaultdict(lambda: collections.Counter())
n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
for setting, think in (("false", False), ("low", "low"), ("medium", "medium")):
    for name, prompt in (("copy", P_COPY), ("findings", P_FIND)):
        for _ in range(n):
            out = call(prompt, think); k = (setting, name); res[k]["runs"] += 1
            bad_in_text = {c for c in BAD if c in out}
            urls = [u for u in __import__("re").findall(r"https?://[^\s)\]]+", out)]
            if any(any(c in u for c in BAD) for u in urls): res[k]["runs_with_corrupted_url"] += 1
            if any(u not in URLS for u in urls): res[k]["runs_with_url_not_matching_input"] += 1
            for c in bad_in_text: res[k][BAD[c]] += 1
for k, v in res.items(): print(k, dict(v))
print("PROBE DONE")
