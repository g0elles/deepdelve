"""Scholarly tools (tools/scholar.py): JATS conversion, OpenAlex abstract rebuild, content-level labels, nothing saved when nothing real was obtained. Offline: _http_get is patched."""
import asyncio, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
from tools import scholar as sc, fs
from utils.run_state import fetched_urls_ctx

config.load_config()
config.cfg["settings"]["workspace"] = {"type": "memory", "dir": "."}
config.cfg["settings"]["scholar"] = {"email": "t@example.org", "auto_fetch_top": 2}
PROSE = ("Prime editing writes new sequence into a target site without a double strand break and is now in its first clinical trials. " * 14)
JATS = (b'<?xml version="1.0"?><article><front><article-meta><title-group><article-title>Edit <italic>in vivo</italic></article-title></title-group>'
        b'<abstract><p>Short abstract.</p></abstract></article-meta></front><body><sec><title>Methods</title><p>We edited cells <xref>[1]</xref> in the liver.</p>'
        b'<table-wrap><caption><p>Doses</p></caption><table><tr><td>A</td><td>5 mg</td></tr></table></table-wrap></sec></body></article>')
REC = {"title": "Prime editing for Wilson disease", "authorString": "Doe J", "pubYear": "2026", "doi": "10.1234/x", "pmcid": "PMC123", "isOpenAccess": "Y",
       "inPMC": "Y", "license": "cc by", "abstractText": "<h4>Abstract</h4>An abstract.", "citedByCount": 3}


def reset():
    fs._IN_MEMORY_FS.clear()
    fetched_urls_ctx.set([])


def fake(routes):
    def get(url, params=None, as_json=True):
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise RuntimeError("unexpected url " + url)
    return get


# 1. JATS -> markdown keeps title/abstract/body/table, drops the in-text reference marker
md = sc._jats_to_markdown(JATS)
assert "# Edit in vivo" in md and "Short abstract." in md and "## Methods" in md and "[1]" not in md and "Table: Doses" in md and "A | 5 mg" in md, md
# 2. OpenAlex inverted abstract is rebuilt in order
assert sc._abstract_from_index({"a": [0, 2], "b": [1], "c": [3]}) == "a b a c" and sc._abstract_from_index(None) == ""
# 3. Europe PMC record: html stripped from the abstract, open access needs BOTH flags
r = sc._from_epmc(REC)
assert r["abstract"] == "Abstract An abstract." and r["oa"] and not sc._from_epmc(dict(REC, inPMC="N"))["oa"]
# 4. search saves the open-access full text, labelled FULL TEXT, and records the fetch
reset()
sc._http_get = fake({"/search": {"resultList": {"result": [REC]}}, "fullTextXML": JATS.replace(b"Short abstract.", PROSE.encode())})
out = asyncio.run(sc.scholar_search.func(query="prime editing"))
assert "Full text saved" in out and "Prime editing for Wilson disease" in out, out
saved = [e for e in fetched_urls_ctx.get()]
assert saved and saved[0]["url"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/" and not saved[0].get("stub"), saved
body = fs._IN_MEMORY_FS[next(iter(fs._IN_MEMORY_FS))]
assert "Content-Level: FULL TEXT" in body and body.startswith("Source-URL: https://pmc.ncbi.nlm.nih.gov/articles/PMC123/")
# 5. a paywalled hit is listed with its abstract and NOT saved
reset()
sc._http_get = fake({"/search": {"resultList": {"result": [dict(REC, isOpenAccess="N", inPMC="N", pmcid="")]}}})
out = asyncio.run(sc.scholar_search.func(query="x"))
assert "paywalled: abstract only" in out and not fs._IN_MEMORY_FS and not fetched_urls_ctx.get(), out
# 6. scholar_fetch with no full text anywhere saves the abstract and says ABSTRACT ONLY
reset()
sc._http_get = fake({"/search": {"resultList": {"result": [dict(REC, isOpenAccess="N", inPMC="N", pmcid="")]}}, "unpaywall": {"oa_locations": []}})
out = asyncio.run(sc.scholar_fetch.func(identifier="https://doi.org/10.1234/x"))
assert "ABSTRACT" in out and next(iter(fs._IN_MEMORY_FS.values())).count("Content-Level: ABSTRACT ONLY") == 1, out
# 7. nothing obtainable: error, nothing saved or recorded (a stub must never become a citable source)
reset()
sc._http_get = fake({"/search": {"resultList": {"result": []}}, "openalex": RuntimeError("down"), "unpaywall": {"oa_locations": []}})
out = asyncio.run(sc.scholar_fetch.func(identifier="10.9999/none"))
assert out.startswith("Error") and "nothing was saved" in out and not fs._IN_MEMORY_FS and not fetched_urls_ctx.get(), out
# 8. an unusable identifier is refused before any network call
sc._http_get = fake({})
assert "needs a DOI" in asyncio.run(sc.scholar_fetch.func(identifier="hello"))
# 9. API failure becomes a tool error, not a crash
sc._http_get = fake({"/search": RuntimeError("503")})
assert asyncio.run(sc.scholar_search.func(query="q", source="europepmc")).startswith("Error")
# 10. evidence-only runs refuse the scholarly tools too
from tools.core import _EVIDENCE_ONLY_BLOCKED_TOOLS
assert {"scholar_search", "scholar_fetch"} <= _EVIDENCE_ONLY_BLOCKED_TOOLS
print("test_scholar: ok")

if __name__ == "__main__":
    pass
