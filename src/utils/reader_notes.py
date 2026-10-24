"""Reader notes: a short, clearly marked block of automatic hints appended to the END of a finished report.

Why: the 2026-10-24 report-defect audit (wiki page "Report Defect Audit") found defects the completion checks cannot see, and a few of them are
detectable with plain string work at high precision on the hand-labelled set. Three classes are used here, the ones with the best measured precision:
a figure that is not in the cited source but is in another saved page (wrong citation number), a body with no inline citations at all, and a
dated expectation that has already passed ("expected in summer 2024" written in 2026). They are hints, not verdicts: the block says so, never fails a
run, never feeds the completion checks (`strip_from_artifact` removes it before every check, so a resumed run does not see its own notes), and is
always present on a research run (mandatory, no setting): with no hints it says so and that this proves nothing about correctness.

Pure functions take text and dicts so they are unit-testable; `append_to_artifact` / `strip_from_artifact` are the only parts that touch the workspace.
Numbers are normalised for narrow no-break spaces and for the middle-dot decimals some sources use. The block holds no URLs and no numbered-list lines,
so the citation, bibliography and sources parsers read the report the same with or without it."""
import os
import re
import time

BEGIN = "<!-- reader-notes:begin -->"
END = "<!-- reader-notes:end -->"
HEADING = "## Reader notes (automatic, unverified)"
INTRO = ("These notes come from simple automatic checks run after the report was written. They can be wrong and no person has reviewed them; "
         "treat each as a prompt to look, not as a finding.")
MAX_NOTES = 5

_BLOCK_RE = re.compile(r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n*", re.S)
_SRC_HEAD = re.compile(r"^#{1,3} Sources\b", re.M)
_GAP_HEAD = re.compile(r"^#{1,4} .*(evidence gaps?|data limitations?|limitations)", re.I | re.M)
_STOP = frozenset("that this with from were been have will their there which about into over under more than also such only when what while where being each other these "
                  "those they them then onto upon between during after before since most both many much some very would could should does done using used based include "
                  "including report reports study studies results result".split())
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_SEASON = {"spring": 4, "summer": 7, "fall": 10, "autumn": 10, "winter": 1, "early": 2, "mid": 6, "late": 11}
_FUT = re.compile(r"\b(?:expected|anticipated|scheduled|planned|due|will (?:be )?\w+|set to \w+)\b[^.\n]{0,60}?\b(?:in|by|for|during|before|from)\s+"
                  r"((?:(?:early|mid|late|spring|summer|fall|autumn|winter)[\s-]+)?(?:(?:%s)\s+)?)((?:19|20)\d{2})" % "|".join(_MONTHS), re.I)
_FIG = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?)\s?(%|percent|x|×|mg|kg|ms|billion|million|bn|m\b)?", re.I)
_CITE_GROUP = re.compile(r"\[(\d+(?:\s*[,\-]\s*\d+)*)\]")


_RESTORE_BANNER_RE = re.compile(r"^> \*\*(?:QUARANTINED|AUTO-RECOVERED) DRAFT.*?\n\n", re.S)
_NO_CONTENT_RE = re.compile(r"not covered by this run|no data available|no relevant findings|no substantive conclusions|no quantitative data were found", re.I)

def norm(t: str) -> str:
    return re.sub(r"(\d)·(\d)", r"\1.\2", t.replace(" ", " ").replace("‑", "-").replace("‐", "-"))


def _toks(s: str) -> set:
    return {w[:5] for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in _STOP}


def split_report(rep: str):
    """-> (body without the sources list and without an evidence-gaps section, sources text)"""
    m = _SRC_HEAD.search(rep)
    srcs = rep[m.end():] if m else ""
    body = rep[:m.start()] if m else rep
    g = _GAP_HEAD.search(body)
    if g:
        nxt = re.search(r"^#{1,4} ", body[g.end():], re.M)
        end = g.end() + nxt.start() if nxt else len(body)
        body = body[:g.start()] + body[end:]
    return body, srcs


def strip_block(text: str) -> str:
    return _BLOCK_RE.sub("\n", text).rstrip("\n") + "\n" if BEGIN in text else text


NO_HINTS = ("No automatic hints were raised. This does not mean the report is free of errors: the checks cover only a few kinds of problem "
            "(a figure that is not in its cited source, a body with no inline citations, a date that has already passed).")


def format_block(notes: list) -> str:
    body = "\n".join(f"- {n}" for n in notes) if notes else NO_HINTS
    return f"\n\n{BEGIN}\n{HEADING}\n\n{INTRO}\n\n{body}\n{END}\n"


def source_numbers(report: str) -> dict:
    """{N: url} from "N. [title](url)" and "N. title (url)" source lists alike"""
    _, srcs = split_report(report)
    return {int(m.group(1)): m.group(2).rstrip("/") for m in re.finditer(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", srcs, re.M)}


def _claims(body: str):
    from utils.grounding import decompose_claim_segments
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        for seg in decompose_claim_segments(line):
            cites = [int(n) for grp in _CITE_GROUP.findall(seg) for n in re.findall(r"\d+", grp)]
            text = _CITE_GROUP.sub("", seg).strip()
            if cites and len(text) > 25:
                yield text, cites


def stale_status(body: str, started_at: float) -> list:
    out = []
    t = time.gmtime(started_at)
    y, mo = t.tm_year, t.tm_mon
    for m in _FUT.finditer(body):
        mod, yr = m.group(1).lower().strip(), int(m.group(2))
        mon = next((i + 1 for i, n in enumerate(_MONTHS) if n in mod), None) or next((v for k, v in _SEASON.items() if k in mod), 12)
        if yr < y or (yr == y and mon < mo):
            snippet = " ".join(body[max(0, m.start() - 25):m.end() + 5].split())
            out.append(f"Dated expectation: \"...{snippet}...\" was written in {y}-{mo:02d}; the date has passed, so it may be out of date.")
    return out


def _label(fig: str, unit: str) -> str:
    return f"{fig}{'' if unit in ('', '%') else ' '}{unit}"


def figure_context(body: str, n2text: dict, all_sources: dict, radius: int = 160) -> list:
    """A figure whose cited source does not hold it near the claim's subject. n2text: {N: source text}; all_sources: every saved page's text.
    One note per claim and kind, naming every figure of that kind in it."""
    grouped = {}  # (claim, kind, reason) -> {"cites": ..., "figs": [labels], "elsewhere": max count}
    norm_all = None
    for claim, cites in _claims(body):
        cl = norm(claim)
        for fm in _FIG.finditer(cl):
            fig, unit = fm.group(1).replace(",", ""), (fm.group(2) or "").lower()
            if len(fig.replace(".", "").lstrip("$")) < 2 or (not unit and not fig.startswith("$") and "." not in fig):
                continue
            around = cl[max(0, fm.start() - 30):fm.start()].lower()
            subj = _toks(cl[max(0, fm.start() - 90):fm.start()] + " " + cl[fm.end():fm.end() + 60])
            found_any = ok = False
            reason = ""
            for n in cites:
                txt = n2text.get(n)
                if txt is None:
                    continue
                src = norm(txt)
                low = src.lower()
                pos = [m.start() for m in re.finditer(r"(?<![\d.])" + re.escape(fig) + r"(?![\d])", src)]
                if not pos:
                    continue
                found_any = True
                for p in pos:
                    if len(subj & _toks(src[max(0, p - radius):p + radius])) >= 2:
                        ok = True
                        pre = low[max(0, p - 12):p]
                        if re.search(r"\bby\s*\$?$", around) and re.search(r"\bto\s*\$?\s*$", pre) and not re.search(r"\bby\s*\$?\s*$", pre):
                            ok = False
                            reason = "the source says 'to' where the report says 'by'"
                        break
                if ok:
                    break
            if ok:
                continue
            short = " ".join(claim.split())[:90]
            if not found_any:
                if norm_all is None:
                    norm_all = [norm(s) for s in all_sources.values()]
                elsewhere = sum(1 for s in norm_all if re.search(r"(?<![\d.])" + re.escape(fig) + r"(?![\d])", s))
                if not elsewhere:
                    continue
                g = grouped.setdefault((short, "absent", ""), {"cites": sorted(set(cites)), "figs": [], "elsewhere": 0})
                g["elsewhere"] = max(g["elsewhere"], elsewhere)
            else:
                g = grouped.setdefault((short, "far", reason), {"cites": sorted(set(cites)), "figs": [], "elsewhere": 0})
            g["figs"].append(_label(fig, unit))
    out = []
    for (short, kind, reason), g in grouped.items():
        figs = ", ".join(dict.fromkeys(g["figs"]))
        if kind == "absent":
            out.append(f"Citation check: not in the cited source {g['cites']} but found in {g['elsewhere']} other source(s) that were read: {figs} (\"{short}...\"); "
                       f"the citation number may be wrong.")
        else:
            out.append(f"Citation check: found in the cited source but not near this claim's subject: {figs} (\"{short}...\")"
                       + (f"; {reason}" if reason else "") + "; check the source.")
    return out


def no_inline_citations(report: str) -> list:
    body, srcs = split_report(report)
    listed = re.findall(r"^\s*(\d+)\.\s", srcs, re.M)
    if listed and not _CITE_GROUP.search(body) and len(body.split()) > 200:
        return [f"No inline citations: {len(listed)} sources are listed at the end, but no sentence is tied to one, so no claim can be traced to a source from the text."]
    return []


def unverified_or_empty(report: str) -> list:
    """A report that is an unverified restored draft (banner from engine/artifact_salvage.py) or says it holds no findings.
    The other detectors are silent on such a report, and 'no hints' would read as a clean bill of health."""
    notes = []
    head = report.lstrip()[:400]
    if "QUARANTINED DRAFT" in head or "AUTO-RECOVERED DRAFT" in head:
        notes.append("Unverified draft: this report failed the final check and was restored without a corrected rewrite (see the notice at its top). Treat every claim as unchecked.")
    text = _RESTORE_BANNER_RE.sub("", report)
    words = len(re.sub(r"^#+ .*$", "", text, flags=re.M).split())
    if len(_NO_CONTENT_RE.findall(text)) >= 2 or words < 40:
        notes.append("Placeholder report: it states that no findings were found or leaves its sections empty, so it does not answer the question.")
    return notes


def compute_notes(report: str, n2text: dict, all_sources: dict, started_at: float, max_notes: int = MAX_NOTES) -> list:
    """Highest-priority notes first (no citations, then citation checks, then dated expectations), capped, de-duplicated."""
    report = strip_block(report)
    body, _ = split_report(report)
    notes = unverified_or_empty(report) + no_inline_citations(report) + figure_context(body, n2text, all_sources) + stale_status(body, started_at)
    seen, out = set(), []
    for n in notes:
        if n not in seen:
            seen.add(n)
            out.append(n)
    if len(out) > max_notes:
        extra = len(out) - max_notes + 1
        out = out[:max_notes - 1] + [f"{extra} more similar note(s) not shown."]
    return out


# ---- workspace glue (the only part that touches files) -------------------------------------------------------------------------------------------------

def _path(req_artifact: str):
    from tools.fs import _get_safe_path
    return _get_safe_path(req_artifact)


def strip_from_artifact(req_artifact: str) -> bool:
    """Remove a previous run's notes block from the artifact on disk, so completion checks never read it. True if a block was removed."""
    try:
        path = _path(req_artifact)
        if not path or not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if BEGIN not in text:
            return False
        with open(path, "w", encoding="utf-8") as f:
            f.write(strip_block(text))
        return True
    except Exception:
        return False


def append_to_artifact(req_artifact: str, fetched_urls: list, started_at: float):
    """Compute and append the notes block (always, even with no hints); returns the number of hints in it, or None when nothing was written (no report, or any error: the
    report is then left untouched). Fail-open."""
    try:
        from tools.fs import get_workspace_file_content
        path = _path(req_artifact)
        if not path or not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            report = strip_block(f.read())
        url2file = {(e.get("url") or "").rstrip("/"): e.get("filename") for e in (fetched_urls or []) if isinstance(e, dict) and not e.get("stub")}
        all_sources = {}
        for fn in set(filter(None, url2file.values())):
            txt = get_workspace_file_content(fn)
            if txt:
                all_sources[fn] = txt
        n2text = {n: all_sources[url2file[u]] for n, u in source_numbers(report).items() if url2file.get(u) in all_sources}
        notes = compute_notes(report, n2text, all_sources, started_at or time.time())
        with open(path, "w", encoding="utf-8") as f:
            f.write(report.rstrip("\n") + format_block(notes))
        return len(notes)
    except Exception:
        return None
