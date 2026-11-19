"""writer/verify_view.py: quote bullets link to highlighted spans in the saved page; report and page text are escaped; bad spans are skipped."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from pipeline.bridge import source_filename  # noqa: E402
from pipeline.run import quote_span  # noqa: E402
from writer import compiled as wc  # noqa: E402
from writer.verify_view import render_html  # noqa: E402

URL, URL2 = "https://a.example/x", "https://b.example/y"
PAGE = "Intro <script>alert(1)</script>. Traffic fell 16% in the first eight weeks of the scheme. End."
Q = "Traffic fell 16% in the first eight weeks of the scheme."


def test_verify_view():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / source_filename(URL)).write_text(PAGE)
        (d / source_filename(URL2)).write_text("short page")
        ev = {"facets": [{"id": "f1", "name": "Traffic", "questions": []}],
              "evidence": {"f1": {URL: [Q], URL2: ["a quote that is not on its page at all"]}},
              "spans": {"f1": {URL: {Q: list(quote_span(Q, PAGE))}, URL2: {"a quote that is not on its page at all": [500, 900]}}}}
        (d / "evidence.json").write_text(json.dumps(ev))
        c = wc.build(ev, "q")
        report = wc.render(c, {"f1": [{"cites": [1], "text": "Traffic fell <script>x</script>."}]})
        out = render_html(report, d, {URL: {"doi": "10.1/abc", "year": 2020}}, d / "final_report.html")
        h = out.read_text()
        assert h.count('class="span"') == 1 and 'href="#m-1-' in h
        s = quote_span(Q, PAGE)[0]
        assert f'<mark id="m-1-{s}">' in h and 'href="#m-1-%d"' % s in h
        assert "<script>alert" not in h and "&lt;script&gt;alert(1)" in h       # page text escaped
        assert "<script>x" not in h                                               # report text escaped (html disabled)
        assert "doi:10.1/abc · 2020" in h
        assert 'id="src-2"' in h and "m-2-" not in h                              # out-of-range span skipped, page still shown


if __name__ == "__main__":
    test_verify_view()
    print("test_verify_view OK (1 test)")
