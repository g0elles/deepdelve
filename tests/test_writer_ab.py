"""eval/writer_ab/run_arms.py: findings numbering, the deterministic metrics and the compiled-arm adapter. Offline, fake model."""
import os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE + "/src"); sys.path.insert(0, HERE + "/eval/writer_ab")
import run_arms as ra

KEY = {"id": "t1", "qs": [{"q": "What effect?", "a": "SMD -0.51, 95% CI -0.93 to -0.09, p=0.02"}, {"q": "How many trials?", "a": "12 randomized trials"},
                          {"q": "Long-term follow-up?", "a": "NOT STATED. The review did not follow patients beyond 4 weeks."}]}
fs = ra.findings_of(KEY)
assert [(n, g) for n, _, g in fs] == [(1, False), (2, False), (3, True)] and fs[2][1].startswith("Gap: the source does not state")
assert "[1] What effect? SMD -0.51" in ra.prompt_of("t1", fs) and "at most 350 words" in ra.prompt_of("t1", fs)

# metrics: bad id, a figure nobody gave, an expansion, a coined name, an uncited sentence; code-rendered bullets are not counted
txt = ('The review found an SMD of -0.51 across 12 trials.[1][2] Effect sizes reached 45 percent in the Eastern Valley Cohort.[1] '
       'The Randomized Trial Group (RTG) agrees.[9] This sentence has no citation at all and is long enough.\n\n'
       '- "SMD -0.99 is quoted here as code" [1]\n- **Gaps:** No source states: 777.\n')
m = ra.metrics(txt, fs, "t1")
assert m["invalid_ids"] == 1 and "45" in m["figures_absent"] and "99" not in m["figures_absent"] and "777" not in m["figures_absent"]
assert "RTG" in m["abbreviations_absent"] and any("Eastern Valley" in n for n in m["names_absent"]) and m["uncited_sentences"] == 1
assert ra.metrics("The review found an SMD of -0.51 across 12 trials.[1][2]", fs, "t1")["figures_absent"] == []

# compiled adapter: findings become numbered quotes, gaps become the engine's gap sentence, a failing model degrades to verbatim quotes
c = ra.compiled_of.__wrapped__("t1", fs) if hasattr(ra.compiled_of, "__wrapped__") else None
ra.KEYS = os.path.join(HERE, "eval", "writer_ab", "_tmp_keys"); os.makedirs(ra.KEYS, exist_ok=True)
import json
json.dump(KEY, open(ra.KEYS + "/t1.json", "w"))
try:
    c = ra.compiled_of("t1", fs)
    assert [q.n for q in c.facets[0].quotes] == [1, 2] and c.facets[0].unanswered == ["Long-term follow-up?"] and [s[0] for s in c.sources] == [1, 2]
    def boom(*a, **k):
        raise RuntimeError("down")
    ra.call_json = boom
    text, stats = ra.arm_compiled(1)("t1", fs, 0)
    assert stats["fallbacks"] == 2 and stats["extractive_units"] == 2 and "No source in the available evidence directly states: Long-term follow-up?." in text
finally:
    os.remove(ra.KEYS + "/t1.json"); os.rmdir(ra.KEYS)
print("test_writer_ab: ok")

if __name__ == "__main__":
    pass
