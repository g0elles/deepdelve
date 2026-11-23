"""W8 A/B mechanical scores (PLAN_W8.md measures a, c, d), written before any A/B report was read.
(a) peer-reviewed share of cited sources: a cited URL counts when its host is a journal/index host in PEER (fixed list below, decided
before the runs; official evaluations are not auto-detected and are labelled by hand in the blind sheet notes).
(c) figures not traceable to a saved source: scripts/evaluate_run.py `figs_unsupported`. (d) pipeline seconds from w8_runs.tsv.
    ~/.venvs/deepdelve/bin/python eval/writer_defects/w8_score.py"""
import os, re, sys
from urllib.parse import urlparse
ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
sys.path.insert(0, ROOT + "/scripts")
import evaluate_run as er  # noqa: E402

PEER = ("doi.org", "ncbi.nlm.nih.gov", "europepmc.org", "pubmed", "journals.plos.org", "thelancet.com", "sciencedirect.com", "bmj.com",
        "springer.com", "link.springer.com", "wiley.com", "nature.com", "jamanetwork.com", "academic.oup.com", "tandfonline.com",
        "frontiersin.org", "mdpi.com", "biomedcentral.com", "cambridge.org", "sagepub.com", "nejm.org", "cell.com", "karger.com",
        "scielo", "ijbnpa.biomedcentral.com", "annualreviews.org", "acpjournals.org", "psychiatryonline.org", "cochranelibrary.com")


def peer(url):
    h = urlparse(url).netloc.lower()
    return any(p in h for p in PEER)


def cited_urls(rep):
    m = re.search(r"^#{1,3} (?:Sources|References)\b", rep, re.M)
    if not m:
        return []
    body, lst = rep[:m.start()], rep[m.end():]
    used = set(re.findall(r"\[(\d+)\]", body))
    return [u for n, u in re.findall(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", lst, re.M) if n in used]


def main():
    rows = [ln.split("\t") for ln in open(os.path.join(ROOT, "eval/writer_defects/w8_runs.tsv")).read().splitlines()]
    print("q\tarm\tk\tpeer/cited\tfigs_unsupported\tpipeline_s\tscholar")
    for q, arm, k, ev, rep, secs, rc, stats in rows:
        if not rep:
            print(q, arm, k, "NO REPORT", rc, sep="\t")
            continue
        d = os.path.join(ROOT, rep)
        text = er._strip_notes(open(os.path.join(d, "final_report.md")).read())
        urls = cited_urls(text)
        r = er.score(d)
        print(q, arm, k, f"{sum(map(peer, urls))}/{len(urls)}", len(r["figs_unsupported"] or []), secs,
              re.search(r"scholar=\S+", stats).group(0), sep="\t")


if __name__ == "__main__":
    main()
