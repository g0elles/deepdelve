"""Reader notes (utils/reader_notes.py): the three detectors, the marked block, strip/append idempotence, parser neutrality, fail-open."""
import os, sys, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from utils import reader_notes as rn
from utils.grounding import parse_numbered_sources, extract_cited_urls

pad = "word " * 250
SRC = {1: "The 2024 Digital Decade report gives Finland 90.6 out of 100 for digital public services, against an EU average of 79.4.",
       2: "Optimal congestion pricing would reduce the costs of achieving the same performance to $57 billion by reducing peak-period traffic volumes.",
       3: "Estonia scores 98.9 out of 100 for digital public services for businesses and 95.8 for citizens."}
ALL = dict(SRC, other="Estonia scores 98.9 out of 100 for digital public services for businesses and 95.8 for citizens in the Estonian Digital Decade report.")

# 1. a figure that is in another page but not in the cited one (wrong citation number); a supported figure stays silent
body = pad + "\n- Finland scores 90.6 out of 100 for the digitalisation of public services [1].\n- Estonia scores 98.9 out of 100 for digital public services for businesses [1].\n"
notes = rn.figure_context(body, SRC, ALL)
assert len(notes) == 1 and "98.9" in notes[0] and "may be wrong" in notes[0], notes
# 2. 'by' where the source says 'to' (the backwards $57 billion), and the supported 'to' wording is silent
assert any("57" in n and "'to'" in n for n in rn.figure_context("- Optimal congestion pricing could lower the costs of achieving the same performance by $57 billion [2].", SRC, ALL))
assert rn.figure_context("- Optimal congestion pricing would lower the costs of achieving the same performance to $57 billion [2].", SRC, ALL) == []
# 3. no inline citations; with citations, silent
rep = "# T\n" + pad + "\n\n## Sources\n1. **[a](https://a.org/x)**\n2. **[b](https://b.org/y)**\n"
assert len(rn.no_inline_citations(rep)) == 1
assert rn.no_inline_citations(rep.replace("word word", "word [1] word", 1)) == []
# 4. dated expectation: passed -> note; future or current -> silent
t_2026_10 = 1790900000
assert len(rn.stale_status("Topline data are expected in summer 2024.", t_2026_10)) == 1
assert rn.stale_status("Topline data are expected in summer 2027.", t_2026_10) == []
# 5. block: format, strip, idempotence, no URLs and no numbered lines in the block
blk = rn.format_block(["Dated expectation: x", "No inline citations: y"])
assert rn.BEGIN in blk and rn.END in blk and "unverified" in blk and "http" not in blk
full = "# T\nbody\n\n## Sources\n1. **[a](https://a.org/x)**\n" + blk
assert rn.strip_block(full) == "# T\nbody\n\n## Sources\n1. **[a](https://a.org/x)**\n", repr(rn.strip_block(full))
assert rn.strip_block(rn.strip_block(full)) == rn.strip_block(full)
assert rn.strip_block("no block here\n") == "no block here\n"
# 6. the citation / bibliography parsers read the report the same with and without the block
plain = rn.strip_block(full)
assert parse_numbered_sources(full) == parse_numbered_sources(plain) and extract_cited_urls(full) == extract_cited_urls(plain)
# 7. compute_notes caps and summarises; a clean report gets no notes
assert rn.compute_notes("# T\n" + pad + " clean sentence [1].\n\n## Sources\n1. **[a](https://a.org/x)**\n", {1: "clean sentence"}, {}, t_2026_10) == []
# 8. workspace glue: append -> strip round trip on a real file, fail-open on a missing file, the block is always present on a research run
d = tempfile.mkdtemp()
orig_path = rn._path
rn._path = lambda name: os.path.join(d, name)
try:
    assert rn.append_to_artifact("missing.md", [], 0) is None  # fail-open: nothing written, no claim that it was
    rp = os.path.join(d, "final_report.md")
    open(rp, "w").write("# T\n" + pad + "\n\n## Sources\n1. **[a](https://a.org/x)**\n")
    n = rn.append_to_artifact("final_report.md", [], t_2026_10)
    assert n == 1 and rn.BEGIN in open(rp).read()
    n2 = rn.append_to_artifact("final_report.md", [], t_2026_10)  # idempotent: replaced, not stacked
    assert n2 == 1 and open(rp).read().count(rn.BEGIN) == 1
    assert rn.strip_from_artifact("final_report.md") is True and rn.BEGIN not in open(rp).read()
    open(rp, "w").write("# T\n" + pad + " ok [1].\n\n## Sources\n1. **[a](https://a.org/x)**\n")
    # mandatory: even with nothing to report the block is present and says so (and that this proves nothing about correctness)
    assert rn.append_to_artifact("final_report.md", [], t_2026_10) == 0
    txt = open(rp).read()
    assert txt.count(rn.BEGIN) == 1 and rn.NO_HINTS in txt and "does not mean the report is free of errors" in txt
    assert rn.strip_from_artifact("final_report.md") is True and rn.BEGIN not in open(rp).read()
finally:
    rn._path = orig_path
# 9. wiring pins: the completion pipeline strips the block first, and the shared loop appends it (a refactor that drops either fails here)
import inspect
import engine.completion as _completion
import engine.run_loop as _run_loop
assert "reader_notes.strip_from_artifact" in inspect.getsource(_completion.run_completion_check)
assert "reader_notes.append_to_artifact" in inspect.getsource(_run_loop.run_agent_loop)
print("test_reader_notes OK")
