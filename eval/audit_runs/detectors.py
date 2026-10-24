"""Shadow detectors for the defect classes found in the 2026-10-24 audit (plan WP3a-f). Advisory scores only: nothing here is wired into the completion loop.
Each takes a replay_checks.Run and returns a list of finding strings. Numbers are normalised for the middle-dot decimals some sources use (6.08 written 6 middle-dot 08)."""
import json, os, re, time

ROOT = os.path.abspath(os.path.dirname(os.path.abspath(__file__)) + "/../..")
_STOP = frozenset("that this with from were been have will their there which about into over under more than also such only when what while where being each other these those they them then onto upon between during after before since most both many much some very would could should does done using used based include including report reports study studies results result".split())
GAP_MIN_COVER = 1.0  # fraction of a gap phrase's terms one numeric body paragraph must contain (0.75 gave 7 false hits on one report, replay 2026-10-24)
_GAP_HEAD = re.compile(r"^#{1,4} .*(evidence gaps?|data limitations?|limitations)", re.I | re.M)
_SRC_HEAD = re.compile(r"^#{1,3} Sources\b", re.M)


def split_report(rep):
    """-> (body without sources and gap section, gap section text, sources text)"""
    m = _SRC_HEAD.search(rep); srcs = rep[m.end():] if m else ""; body = rep[:m.start()] if m else rep
    g = _GAP_HEAD.search(body); gap = ""
    if g:
        nxt = re.search(r"^#{1,4} ", body[g.end():], re.M); end = g.end() + nxt.start() if nxt else len(body)
        gap = body[g.start():end]; body = body[:g.start()] + body[end:]
    return body, gap, srcs


def norm(t):
    return re.sub(r"(\d)·(\d)", r"\1.\2", t.replace(" ", " ").replace("‑", "-").replace("‐", "-"))


def toks(s, n=5):
    return {w[:n] for w in re.findall(r"[a-z]{4,}", s.lower()) if w not in _STOP}


# ---- WP3a stale status ------------------------------------------------------------------------------------------------------------------------------
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
_SEASON = {"spring": 4, "summer": 7, "fall": 10, "autumn": 10, "winter": 1, "early": 2, "mid": 6, "late": 11}
_FUT = re.compile(r"\b(?:expected|anticipated|scheduled|planned|due|will (?:be )?\w+|set to \w+)\b[^.\n]{0,60}?\b(?:in|by|for|during|before|from)\s+((?:(?:early|mid|late|spring|summer|fall|autumn|winter)[\s-]+)?(?:(?:%s)\s+)?)((?:19|20)\d{2})" % "|".join(m for m in _MONTHS), re.I)
_ASOF = re.compile(r"\bas of\s+(\d{1,2}\s+(?:%s)\s+(?:19|20)\d{2}|(?:%s)\s+\d{1,2},?\s+(?:19|20)\d{2})" % ("|".join(_MONTHS), "|".join(_MONTHS)), re.I)


def det_stale_status(run):
    out = []; now = float(run.state.get("started_at") or time.time()); t = time.gmtime(now); y, mo = t.tm_year, t.tm_mon
    body, _, _ = split_report(run.report)
    for m in _FUT.finditer(body):
        mod, yr = m.group(1).lower().strip(), int(m.group(2)); mon = next((i + 1 for i, n in enumerate(_MONTHS) if n in mod), None)
        if not mon: mon = next((v for k, v in _SEASON.items() if k in mod), 12)
        if yr < y or (yr == y and mon < mo):
            out.append(f"future-tense statement about {mod} {yr} written in {y}-{mo:02d}: {body[max(0, m.start() - 20):m.end() + 10]!r}")
    for m in _ASOF.finditer(body):
        d = re.sub(r",", "", m.group(1)); mt = re.search(r"(%s)" % "|".join(_MONTHS), d, re.I); yr = int(re.search(r"(?:19|20)\d{2}", d).group(0))
        if not mt: continue
        age_days = (y - yr) * 365 + (mo - (_MONTHS.index(mt.group(1).lower()) + 1)) * 30
        if age_days > 30: out.append(f"'as of {d}' is about {age_days} days before the run")
    return out


# ---- WP3b query-part coverage -------------------------------------------------------------------------------------------------------------------------
def _facets(run):
    from runs import EVIDENCE
    p = EVIDENCE.get(run.key)
    if p and os.path.exists(os.path.join(ROOT, p, "evidence.json")):
        return [(f["name"] + " " + (f.get("entity") or "")) for f in json.load(open(os.path.join(ROOT, p, "evidence.json")))["facets"]]
    return []


def det_facet_coverage(run, min_frac=0.5):
    body, _, _ = split_report(run.report); facets = _facets(run); out = []
    if not facets: return out
    allt = [toks(f) for f in facets]
    from collections import Counter
    df = Counter(t for s in allt for t in s)
    paras = [toks(p) for p in re.split(r"\n\s*\n|\n(?=#+ )", body) if p.strip()]
    for name, ts in zip(facets, allt):
        dist = {t for t in ts if df[t] <= max(1, len(facets) // 2)} or ts  # tokens that tell this facet from its siblings
        if not dist: continue
        best = max((len(dist & p) / len(dist) for p in paras), default=0)
        if best < min_frac: out.append(f"facet '{name.strip()}': best paragraph covers {best:.0%} of its distinctive terms {sorted(dist)}")
    return out


# ---- WP3c figures: present near the claim's subject in the cited source, direction words agree ----------------------------------------------------------
_FIG = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?)\s?(%|percent|x|×|mg|kg|ms|billion|million|bn|m\b)?", re.I)


def _sources_by_number(run):
    _, _, srcs = split_report(run.report); fm = open(os.path.join(run.dir, "findings.md")).read() if os.path.exists(os.path.join(run.dir, "findings.md")) else ""
    u2f = {m.group(1).rstrip("/"): m.group(2) for m in re.finditer(r"\((https?://[^)\s]+)\) \(saved as (src_\w+\.md)\)", fm)}
    out = {}
    for m in re.finditer(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", srcs, re.M):
        f = u2f.get(m.group(2).rstrip("/"))
        if f in run.sources: out[int(m.group(1))] = f
    return out


def _claims(body):
    from utils.grounding import decompose_claim_segments
    for line in body.splitlines():
        if line.startswith("#"): continue
        for seg in decompose_claim_segments(line):
            cites = [int(n) for grp in re.findall(r"\[(\d+(?:\s*[,\-]\s*\d+)*)\]", seg) for n in re.findall(r"\d+", grp)]
            text = re.sub(r"\[\d+(?:\s*[,\-]\s*\d+)*\]", "", seg).strip()
            if cites and len(text) > 25: yield text, cites


def det_figure_context(run, radius=160):
    out = []; body, _, _ = split_report(run.report); n2f = _sources_by_number(run)
    for claim, cites in _claims(body):
        cl = norm(claim)
        for fm in _FIG.finditer(cl):
            fig, unit = fm.group(1).replace(",", ""), (fm.group(2) or "").lower()
            if len(fig.replace(".", "").lstrip("$")) < 2 or (not unit and not fig.startswith("$") and "." not in fig): continue  # bare small integers / years carry no figure semantics
            around = cl[max(0, fm.start() - 30):fm.start()].lower(); subj = toks(cl[max(0, fm.start() - 90):fm.start()] + " " + cl[fm.end():fm.end() + 60])  # the clause around the figure
            found_any = False; ok = False; reason = ""
            for n in cites:
                f = n2f.get(n)
                if not f: continue
                src = norm(run.sources[f]); low = src.lower()
                pos = [m.start() for m in re.finditer(r"(?<![\d.])" + re.escape(fig) + r"(?![\d])", src)]
                if not pos: continue
                found_any = True
                for p in pos:
                    ctx = src[max(0, p - radius):p + radius]
                    if len(subj & toks(ctx)) >= 2:
                        ok = True
                        pre = low[max(0, p - 12):p]
                        if re.search(r"\bby\s*\$?$", around) and re.search(r"\bto\s*\$?\s*$", pre) and not re.search(r"\bby\s*\$?\s*$", pre): ok = False; reason = "claim says 'by <figure>', source says 'to <figure>'"
                        break
                if ok: break
            if not found_any:
                elsewhere = [k for k, s in run.sources.items() if re.search(r"(?<![\d.])" + re.escape(fig) + r"(?![\d])", norm(s))]
                if elsewhere: out.append(f"figure {fig}{unit} not in cited source(s) {cites} but in {len(elsewhere)} other saved page(s): {claim[:90]!r}")
            elif not ok:
                out.append(f"figure {fig}{unit} in cited source but not near the claim's subject{(' (' + reason + ')') if reason else ''}: {claim[:90]!r}")
    return out


# ---- WP3d gap sentence vs body -------------------------------------------------------------------------------------------------------------------------
def det_gap_vs_body(run):
    body, gap, _ = split_report(run.report); out = []
    for m in re.finditer(r"No source in the available evidence directly states:\s*([^.\n]+)", gap):
        for phrase in [p.strip() for p in m.group(1).split(";") if p.strip()]:
            pt = toks(phrase)
            if len(pt) < 3: continue
            for para in re.split(r"\n\s*\n|\n(?=[-*] )", body):
                if _FIG.search(norm(para)) and len(pt & toks(para)) / len(pt) >= GAP_MIN_COVER:
                    out.append(f"gap says no source states {phrase!r}, body has a figure with {len(pt & toks(para))}/{len(pt)} of its terms: {para.strip()[:100]!r}"); break
    return out


# ---- WP3e citation integrity (structural) -------------------------------------------------------------------------------------------------------------
def det_citation_structure(run):
    out = []; body, _, srcs = split_report(run.report)
    listed = re.findall(r"^\s*(\d+)\.\s", srcs, re.M); cites = re.findall(r"\[\d+(?:\s*[,\-]\s*\d+)*\]", body)
    if listed and not cites and len(body.split()) > 200: out.append(f"no inline citations; {len(listed)} sources only listed")
    urls = re.findall(r"^\s*\d+\.\s.*?\((https?://[^)\s]+)\)", srcs, re.M); dup = {u for u in urls if urls.count(u) > 1}
    if dup: out.append(f"{len(dup)} source URL(s) listed more than once")
    for claim, cs in _claims(body):
        if len(set(cs)) >= 4: out.append(f"{len(set(cs))} citations stacked on one sentence: {claim[:80]!r}")
    return out


# ---- WP3f weak source types (advisory) ---------------------------------------------------------------------------------------------------------------
_WEAK = [(re.compile(r"linkedin\.com/(?:posts|pulse)|facebook\.com|twitter\.com|x\.com/|reddit\.com|quora\.com|medium\.com|substack\.com|stackoverflow\.com"), "social/forum/blog platform"),
         (re.compile(r"prnewswire|businesswire|globenewswire|einpresswire"), "press release wire"),
         (re.compile(r"gmat|gre-|testprep|critical-reasoning|studocu|coursehero|chegg"), "test-prep / homework site"),
         (re.compile(r"academia\.edu|researchgate\.net"), "upload mirror"),
         (re.compile(r"/blog/|/blogs/|blog\.|\.blogspot|github\.io"), "company or personal blog")]


def det_weak_sources(run):
    body, _, srcs = split_report(run.report); out = []
    cited = {int(n) for grp in re.findall(r"\[(\d+(?:\s*[,\-]\s*\d+)*)\]", body) for n in re.findall(r"\d+", grp)}
    for m in re.finditer(r"^\s*(\d+)\.\s.*?\((https?://[^)\s]+)\)", srcs, re.M):
        if int(m.group(1)) in cited:
            for rx, label in _WEAK:
                if rx.search(m.group(2).lower()): out.append(f"[{m.group(1)}] {label}: {m.group(2)[:70]}"); break
    return out


DETECTORS = {"stale_status": det_stale_status, "facet_coverage": det_facet_coverage, "figure_context": det_figure_context, "gap_vs_body": det_gap_vs_body,
             "citation_structure": det_citation_structure, "weak_sources": det_weak_sources}
DETECTOR_TARGETS = {"stale_status": {"C6"}, "facet_coverage": {"Q1", "Q2"}, "figure_context": {"C1", "C2"}, "gap_vs_body": {"G2"}, "citation_structure": {"C2"}, "weak_sources": {"S1"}}
