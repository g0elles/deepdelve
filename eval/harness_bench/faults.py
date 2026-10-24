"""Fault injection for the harness measurement bench (experiment plan Step 1, session_status/CURRENT.md).

Each function takes a CLEAN final report (one that passed every completion check) and returns `(faulty_report, expected_problems)`: the report with exactly one
known defect, and the completion-check problem name(s) that should fire on it. Pure string functions, so they are unit-tested without a GPU
(test_harness_bench.py). `corpus` is the lowercased text of the run's saved sources, used to pick a figure that provably appears in none of them."""
import re

_SECTION = re.compile(r"^## ", re.M)
_CITE = re.compile(r"\[\d+(?:\s*[,\-]\s*\d+)*\]")


def _first_body_paragraph(rep: str) -> tuple[int, int]:
    """(start, end) of the first paragraph under a '## ' heading that has a [N] citation, so an injected sentence lands where a real claim would."""
    for m in _SECTION.finditer(rep):
        end_of_heading = rep.index("\n", m.start()) + 1
        nxt = _SECTION.search(rep, end_of_heading)
        block_end = nxt.start() if nxt else len(rep)
        for pm in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", rep[end_of_heading:block_end]):
            if _CITE.search(pm.group(0)):
                return end_of_heading + pm.start(), end_of_heading + pm.end()
    raise ValueError("no cited body paragraph found")


def unsupported_claim(rep: str, corpus: str) -> tuple[str, tuple]:
    """Append a confident figure that appears in NO saved source, carrying an existing citation, to the first cited paragraph."""
    s, e = _first_body_paragraph(rep)
    para = rep[s:e]
    cite = _CITE.findall(para)[0]
    for fig in ("83.7", "61.4", "47.9", "92.3", "38.6"):
        if fig not in corpus and fig not in rep:
            return rep[:e] + f" Adoption among adults reached {fig} % in 2019 {cite}." + rep[e:], ("specific_figure_unsupported", "claim_unsupported", "not_grounded")
    raise ValueError("every candidate figure already appears in the corpus")


def meta_language(rep: str, corpus: str = "") -> tuple[str, tuple]:
    """The report names the pipeline's own file (the failure seen in two live runs)."""
    s, e = _first_body_paragraph(rep)
    return rep[:e] + " This summary draws exclusively on the compiled findings in findings.md." + rep[e:], ("report_meta_language",)


def remove_gap_section(rep: str, corpus: str = "") -> tuple[str, tuple]:
    """Delete the 'Evidence gaps ...' section (only meaningful for a run that recorded evidence_gaps)."""
    m = re.search(r"^## [^\n]*(?:evidence gaps?|data limitations?)[^\n]*\n", rep, re.I | re.M)
    if not m:
        raise ValueError("report has no evidence-gaps section to remove")
    nxt = _SECTION.search(rep, m.end())
    return rep[:m.start()] + (rep[nxt.start():] if nxt else ""), ("undisclosed_evidence_gap",)


def strip_citations(rep: str, corpus: str = "") -> tuple[str, tuple]:
    """Remove every [N] marker from the first cited paragraph (claims left without any citation)."""
    s, e = _first_body_paragraph(rep)
    return rep[:s] + _CITE.sub("", rep[s:e]) + rep[e:], ("uncited_claims", "non_url_citation", "report_underuses_findings")


FAULTS = {"unsupported_claim": unsupported_claim, "meta_language": meta_language, "remove_gap_section": remove_gap_section, "strip_citations": strip_citations}
