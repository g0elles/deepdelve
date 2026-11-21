"""Raw /api/chat tool-call check: same cases on several model names (e.g. original vs jsonfix template). Per case: structured tool_calls
returned (vs JSON in content), right tool, exact arguments. Options = the model's settings.model_options (card sampling, max ctx); think not sent (qwen2.5 has no thinking).
Repo root: python eval/qwen_toolfix/raw_tools.py qwen2.5-14b-agent:latest qwen2.5-14b-agent-jsonfix:latest -> runs.jsonl + summary"""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
from pipeline.llm import model_options  # noqa: E402
HERE = os.path.dirname(os.path.abspath(__file__))
def fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}}
S, N = {"type": "string"}, {"type": "number"}
TOOLS = [fn("convert_currency", "Convert an amount between currencies.", {"amount": N, "from_currency": S, "to_currency": S}, ["amount", "from_currency", "to_currency"]),
         fn("get_weather", "Current weather for a city.", {"city": S, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, ["city", "unit"]),
         fn("write_workspace_file", "Write a text file to the workspace.", {"filename": S, "content": S}, ["filename", "content"]),
         fn("web_search", "Search the web.", {"query": S, "max_results": {"type": "integer"}}, ["query", "max_results"]),
         fn("book_flight", "Book a flight.", {"origin": S, "destination": S, "date": S, "passengers": {"type": "integer"}}, ["origin", "destination", "date", "passengers"])]
CASES = [("Convert 100 USD to EUR.", "convert_currency", {"amount": 100, "from_currency": "USD", "to_currency": "EUR"}),
         ("How much is 2500 JPY in GBP?", "convert_currency", {"amount": 2500, "from_currency": "JPY", "to_currency": "GBP"}),
         ("Convert 7.5 CHF to CAD.", "convert_currency", {"amount": 7.5, "from_currency": "CHF", "to_currency": "CAD"}),
         ("Weather in Lisbon in celsius?", "get_weather", {"city": "Lisbon", "unit": "celsius"}),
         ("What's the weather in Denver, in fahrenheit?", "get_weather", {"city": "Denver", "unit": "fahrenheit"}),
         ("Save a file named notes.md containing exactly: hello world", "write_workspace_file", {"filename": "notes.md", "content": "hello world"}),
         ("Write report.txt with the content: done", "write_workspace_file", {"filename": "report.txt", "content": "done"}),
         ("Search the web for 'heat pump COP cold climate' and return 5 results.", "web_search", {"query": "heat pump COP cold climate", "max_results": 5}),
         ("Find 3 web results for: Iceland unemployment 2010", "web_search", {"query": "Iceland unemployment 2010", "max_results": 3}),
         ("Book a flight from Madrid to Bogota on 2026-12-20 for 2 passengers.", "book_flight", {"origin": "Madrid", "destination": "Bogota", "date": "2026-12-20", "passengers": 2}),
         ("Book 1 passenger from Oslo to Reykjavik on 2027-01-05.", "book_flight", {"origin": "Oslo", "destination": "Reykjavik", "date": "2027-01-05", "passengers": 1}),
         ("Fly 3 people from Lima to Quito on 2026-12-01, please book it.", "book_flight", {"origin": "Lima", "destination": "Quito", "date": "2026-12-01", "passengers": 3})]
def norm(a):
    return {k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v).strip().lower()) for k, v in (a or {}).items()}
def call(model, prompt):
    body = {"model": model, "stream": False, "messages": [{"role": "user", "content": prompt}], "tools": TOOLS, "options": model_options(model) or sys.exit(f"{model} not in settings.model_options")}
    r = urllib.request.urlopen(urllib.request.Request("http://localhost:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=300)
    return json.load(r)["message"]
out = open(os.path.join(HERE, "runs.jsonl"), "a")
for model in sys.argv[1:]:
    tc = right = exact = 0
    for prompt, tool, args in CASES:
        m = call(model, prompt)
        calls = m.get("tool_calls") or []
        got = calls[0]["function"] if calls else {}
        row = {"model": model, "prompt": prompt, "structured": bool(calls), "tool": got.get("name"), "args": got.get("arguments"),
               "right_tool": got.get("name") == tool, "exact_args": norm(got.get("arguments")) == norm(args), "content": (m.get("content") or "")[:300]}
        out.write(json.dumps(row) + "\n"); out.flush()
        tc += row["structured"]; right += row["right_tool"]; exact += row["exact_args"]
    print(f"{model}: structured tool_calls {tc}/{len(CASES)}, right tool {right}/{len(CASES)}, exact args {exact}/{len(CASES)}", flush=True)
