"""Pick the 10 diagnostic papers by the rule in PROTOCOL.md and save their full text locally (eval/comprehension/papers/, gitignored).
Uses the project's own scholarly tools. Output: papers/manifest.json (query, rank taken, DOI, words, why earlier ranks were skipped)."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src"))
import config  # noqa: E402
config.load_config()
from tools import scholar as sc  # noqa: E402
from tools.web import _fetch_raw  # noqa: E402

QUERIES = [  # (id, source, query)
    ("b1", "epmc", 'randomized controlled trial depression'),
    ("b2", "epmc", 'cohort study cardiovascular risk'),
    ("b3", "epmc", 'systematic review meta-analysis antibiotic'),
    ("b4", "epmc", 'genome editing efficiency'),
    ("b5", "epmc", 'deep learning medical imaging diagnosis'),
    ("n1", "oa", 'large language model evaluation benchmark'),
    ("n2", "oa", 'distributed systems consensus protocol performance'),
    ("n3", "oa", 'energy transition economics policy'),
    ("n4", "oa", 'quantum materials measurement'),
    ("n5", "oa", 'classroom learning experiment students'),
]
OUT = os.path.join(HERE, "papers")
os.makedirs(OUT, exist_ok=True)


def epmc_candidates(q):
    p = {"query": f'({q}) AND OPEN_ACCESS:y AND LICENSE:"cc by" AND PUB_YEAR:2026 AND (SRC:MED OR SRC:PMC)', "format": "json", "resultType": "core", "pageSize": 15}
    for r in sc._http_get(f"{sc.EPMC}/search", p).get("resultList", {}).get("result", []):
        rec = sc._from_epmc(r)
        if rec["pmcid"] and rec["oa"] and "by" in (rec["license"] or "").lower():
            yield rec, lambda rec=rec: sc._full_text_epmc(rec["pmcid"])


def oa_candidates(q):
    p = {"search": q, "per-page": 25, "filter": "open_access.is_oa:true,best_oa_location.license:cc-by,publication_year:2026,type:article",
         "select": "id,doi,title,publication_year,authorships,open_access,best_oa_location,primary_location,abstract_inverted_index,cited_by_count,type"}
    key = sc._setting("openalex_api_key", "")
    if key:
        p["api_key"] = key
    for w in sc._http_get(f"{sc.OPENALEX}/works", p).get("results", []):
        rec = sc._from_openalex(w)
        url = (w.get("best_oa_location") or {}).get("pdf_url") or (w.get("best_oa_location") or {}).get("landing_page_url")
        if url and rec["doi"]:
            def get(url=url):
                d = _fetch_raw(url, True)[0]
                return d if isinstance(d, str) else None
            yield rec, get


def main():
    manifest = []
    for pid, src, q in QUERIES:
        skipped = []
        for rank, (rec, get) in enumerate(epmc_candidates(q) if src == "epmc" else oa_candidates(q), 1):
            try:
                text = get()
            except Exception as e:
                skipped.append((rank, f"fetch failed: {type(e).__name__}"))
                continue
            words = len(re.findall(r"\w+", text or ""))
            if not text or not 3000 <= words <= 8000:
                skipped.append((rank, f"{words} words"))
                continue
            fn = os.path.join(OUT, f"{pid}.md")
            open(fn, "w", encoding="utf-8").write(f"Title: {rec['title']}\nDOI: {rec['doi']}\nLicense: {rec['license']}\n\n{text}")
            manifest.append({"id": pid, "query": q, "source": src, "rank": rank, "doi": rec["doi"], "pmcid": rec["pmcid"], "title": rec["title"], "year": rec["year"],
                             "license": rec["license"], "words": words, "skipped_before": skipped})
            print(pid, rank, words, rec["doi"], rec["title"][:70])
            break
        else:
            print(pid, "NO CANDIDATE", skipped[:5])
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
