"""references.bib DOI fields: tools.scholar.resolve_doi / resolution_enabled and utils.run_state.build_bibliography(meta=...). Offline: _http_get is patched."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from tools import scholar  # noqa: E402
from utils.run_state import build_bibliography  # noqa: E402

WORK = {"doi": "https://doi.org/10.1234/abc.5", "title": "T", "publication_year": 2021, "type": "article", "is_retracted": True,
        "primary_location": {"source": {"display_name": "Journal & Co"}}}


def test_resolve_doi_and_enabled():
    got, old = [], scholar._http_get
    scholar._http_get = lambda url, params=None, as_json=True: got.append(url) or WORK
    try:
        assert scholar.resolve_doi("https://x.org/10.1234/abc.5).", "") == {"doi": "10.1234/abc.5", "year": 2021, "venue": "Journal & Co", "type": "article", "retracted": True}
        assert got[-1].endswith("/works/doi:10.1234/abc.5")                                  # trailing ")." stripped
        assert scholar.resolve_doi("https://x.org/a", "Cite as doi:10.1234/abc.5, 2021")["doi"] == "10.1234/abc.5"   # found in page text
        assert scholar.resolve_doi("https://x.org/a", "no identifier here") == {}
        scholar._http_get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
        assert scholar.resolve_doi("https://doi.org/10.1234/abc.5", "") == {}
    finally:
        scholar._http_get = old
    env = {k: os.environ.pop(k, None) for k in ("DEEPDELVE_CONTACT_EMAIL", "OPENALEX_API_KEY")}
    old_setting = scholar._setting
    scholar._setting = lambda key, default=None: default
    try:
        assert scholar.resolution_enabled() is False
        os.environ["OPENALEX_API_KEY"] = "k"
        assert scholar.resolution_enabled() is True
    finally:
        scholar._setting = old_setting
        os.environ.pop("OPENALEX_API_KEY", None)
        os.environ.update({k: v for k, v in env.items() if v is not None})


def test_build_bibliography_meta():
    rep = "Claim [a](https://journal.example/10.1234/abc.5). Other [b](https://news.example/x)."
    fetched = [{"url": "https://journal.example/10.1234/abc.5", "title": "Paper {1}"}, {"url": "https://news.example/x", "title": "News"}]
    plain = build_bibliography(rep, fetched)
    assert "@article" not in plain and plain.count("@misc") == 2
    bib = build_bibliography(rep, fetched, {"https://journal.example/10.1234/abc.5": {"doi": "10.1234/abc.5", "year": 2021, "venue": "J & Co", "retracted": True}})
    assert "@article{" in bib and "doi = {10.1234/abc.5}" in bib and "year = {2021}" in bib and "journal = {J \\& Co}" in bib
    assert "title = {Paper \\{1\\}}" in bib and "RETRACTED according to OpenAlex" in bib and bib.count("@misc") == 1
    assert build_bibliography(rep, fetched, {}) == plain


if __name__ == "__main__":
    test_resolve_doi_and_enabled()
    test_build_bibliography_meta()
    print("test_bibliography_doi OK (2 tests)")
