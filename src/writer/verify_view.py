"""final_report.html for compiled runs: the report with each evidence quote linked to its highlighted span in the saved source page.
Spans come from the pipeline's evidence.json (pipeline/run.py quote_span), matched to the report's `- "quote" [n]` bullets, so the
Markdown report the completion checks read carries no markers. Report and pages are rendered with HTML disabled and escaped."""
import html
import json
import re
from pathlib import Path

from markdown_it import MarkdownIt

_MARK = "⁣"  # invisible separator: survives Markdown rendering untouched, never typed by a writer
_BULLET = re.compile(r'^- "(.*)" \[(\d+)\]$')
_SOURCE = re.compile(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", re.M)
_SCRIPT = """<script>
document.addEventListener("click", function (e) {
  var a = e.target.closest("a.span"); if (!a) return;
  var m = document.getElementById(a.getAttribute("href").slice(1)); if (!m) return;
  e.preventDefault(); var d = m.closest("details"); if (d) d.open = true;
  m.scrollIntoView({block: "center"}); m.classList.add("hit"); setTimeout(function () { m.classList.remove("hit"); }, 2000);
});
</script>"""
_STYLE = """<style>
:root { --bg: #fff; --fg: #1d1d1f; --mark: #fff3a3; --hit: #ffb84d; --rule: #ddd; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #e8e8ea; --mark: #5a4d00; --hit: #a35a00; --rule: #333; } }
body { background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif; max-width: 52rem; margin: 0 auto; padding: 1rem; }
pre { white-space: pre-wrap; word-break: break-word; font-size: 0.85rem; border-top: 1px solid var(--rule); padding-top: .5rem; }
mark { background: var(--mark); color: inherit; } mark.hit { background: var(--hit); }
a.span { font-size: .8rem; margin-left: .25rem; } details { margin: .4rem 0; } summary { cursor: pointer; }
</style>"""


def _spans(evidence_dir: Path) -> dict[tuple[int, str], tuple[int, int]]:
    """{(source number, quote text as the report shows it): span} from evidence.json, numbered exactly as writer.compiled.build numbers it."""
    from writer.compiled import build
    ev = json.loads((evidence_dir / "evidence.json").read_text())
    return {(q.n, q.text.strip()): q.span for f in build(ev, "").facets for q in f.quotes if q.span}


def render_html(report_md: str, evidence_dir: Path, meta: dict[str, dict], out: Path) -> Path:
    from pipeline.bridge import source_filename
    evidence_dir = Path(evidence_dir)
    spans = _spans(evidence_dir)
    pages = {}
    for n, url in ((int(k), u) for k, u in _SOURCE.findall(report_md)):
        p = evidence_dir / source_filename(url)
        if p.exists():
            pages[n] = (url, p.read_text(errors="ignore"))
    used: dict[int, set[tuple[int, int]]] = {}
    lines = []
    for line in report_md.splitlines():
        m = _BULLET.match(line)
        sp = spans.get((int(m.group(2)), m.group(1).strip())) if m else None
        if sp and int(m.group(2)) in pages and 0 <= sp[0] < sp[1] <= len(pages[int(m.group(2))][1]):  # a span outside the saved page gets no link
            n = int(m.group(2))
            used.setdefault(n, set()).add(tuple(sp))
            line += f" {_MARK}SPAN:{n}:{sp[0]}:{sp[1]}{_MARK}"
        lines.append(line)
    body = MarkdownIt("commonmark", {"html": False}).enable("table").render("\n".join(lines))  # the commonmark preset turns raw HTML ON by default
    body = re.sub(_MARK + r"SPAN:(\d+):(\d+):(\d+)" + _MARK, r'<a class="span" href="#m-\1-\2">[\1]</a>', body)
    src_html = []
    for n, (url, text) in sorted(pages.items()):
        parts, end = [], len(text)
        for s, e in sorted(used.get(n, ()), reverse=True):  # from the end backwards, so earlier offsets stay valid; an overlapping span is skipped
            if e > end:
                continue
            parts.append(html.escape(text[e:end]))
            parts.append(f'<mark id="m-{n}-{s}">{html.escape(text[s:e])}</mark>')
            end = s
        parts.append(html.escape(text[:end]))
        md = meta.get(url) or {}
        doi = f" · doi:{html.escape(md['doi'])}" + "".join(f" · {html.escape(str(md[k]))}" for k in ("year", "venue") if md.get(k)) if md.get("doi") else ""
        src_html.append(f'<details id="src-{n}"><summary>[{n}] {html.escape(url)}{doi}</summary><pre>{"".join(reversed(parts))}</pre></details>')
    out = Path(out)
    out.write_text("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
                   f"<title>Report with sources</title>{_STYLE}</head><body>{body}<h2>Saved sources</h2>{''.join(src_html)}{_SCRIPT}</body></html>\n")
    return out
