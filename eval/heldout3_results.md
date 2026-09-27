# Held-out set 3 (h10-h18), run once at tag `heldout3-frozen` (4b8cf6d; src/pipeline == 0c23bca), 2026-09-26

Config: plan (gpt-oss think=low, keyword queries) -> search -> reflow -> BM25 -> CE -> filters -> LLM judge. Wall 110-319 s/query.
Grades: read 2+ quotes per facet plus targeted greps (no rubric, single reader = Claude). OK = on-topic, specific, right entity.
Run logistics: h10-h14 (and part of h15/h16) ran with the headed-Chromium fetch fallback opening windows on the desktop; h15-h18 reruns/finish used a hidden display. h15 and h17 were killed and rerun from scratch.

| id | cat | facets >=2 src | grade | note |
|---|---|---|---|---|
| h10 | comparison | 4/4 | OK+ | per-country incentives + 2026 market-share figures |
| h11 | legal | 3/3 | OK+ | DE/SE/JP duration and pay with numbers (480 days, 65-67%) |
| h12 | technical | 6/6 | OK- | Paxos facets return Paxos Commit (a different algorithm); one garbled no-space quote; Raft facets good |
| h13 | scientific | 5/5 | OK | mechanism, phase 2, dosing, safety good; phase 3 = enrolment news only (honest gap) |
| h14 | historical | 4/5 | OK- | deaths/emigration/population good (emigration 12/15 on-topic); f2 "economic conditions" 1 off-topic quote; a citation-note quote; mojibake |
| h15 | news_recent | 6/6 | OK- | "latest" query but 2 of 72 quotes mention 2026, most 2024-25 (Busan INC-5); Geneva Aug-2025 collapse present |
| h16 | numeric | 6/6 | WEAK | Kenya missing entirely: planner made facets for Poland and Chile only (3 countries asked); Poland/Chile figures good |
| h17 | spanish | 5/5 | OK- | English quotes for a Spanish query; same sentence under f1 and f4; Moovit 2017 for bus usage |
| h18 | contested | 4/4 | WEAK | planner still splits by polarity ("increase", "deterioration"); those facets hold improvement/no-change evidence, so labels mislead; evidence itself is balanced and current (NYC 2026 Health Dept: no air-quality difference) |

Summary: 2 OK+, 1 OK, 4 OK-, 2 WEAK. Not better than heldout2 (5 OK+, 2 borderline, 3 WEAK): the four fixes did not visibly generalize.
Failure classes: (1) planner drops an entity named in the query (h16 Kenya); (2) polarity/stance facets persist under other names (h18, same class as h09);
(3) recency weak for "latest" queries, stale-year quotes not demoted (h15); (4) Spanish query returns mostly English quotes (h17); (5) adjacent-but-wrong topic (Paxos Commit, h12);
(6) meta/citation-note and mojibake sentences pass as quotes (h14). This set is now SEEN; any fix is validated on dev, then a fresh set (h19+).
