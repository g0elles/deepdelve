# Held-out set 2 (h01-h09), run once at tag `heldout2-frozen` (34cb258), 2026-09-26

Config: plan (gpt-oss think=low) -> search -> reflow -> BM25 -> CE -> filters -> LLM judge. Wall 118-301 s/query.
Grades are from reading 2 quotes per facet (no rubric, single reader = Claude): OK = on-topic, specific, right entity.

| id | cat | facets >=2 src | quotes (distinct) | grade | note |
|---|---|---|---|---|---|
| h01 | comparison | 4/4 | 29 (26) | WEAK | Japan MLIT quote under the Korea facet; planner set entity "" for country facets; 2 facets share a quote |
| h02 | legal | 3/3 | 15 (15) | OK | Argentina facet has one off-topic employment-law quote |
| h03 | technical | 6/6 | 89 (77) | OK- | topical, numeric; filter-specific facets get quotes about other filters (no entity for concepts) |
| h04 | scientific | 4/4 | 31 (31) | OK | |
| h05 | historical | 4/4 | 13 (8) | OK- | heavy duplication, thin (2 src/facet); India 17-18M deaths present |
| h06 | news_recent | 5/6 | 61 (60) | OK | recency working (2026 dates); f6 single source |
| h07 | numeric | 3/3 | 36 (36) | OK+ | per-country values with destinations |
| h08 | spanish | 6/6 | 50 (38) | WEAK | f6 quotes are Colombian (ADRES, billones de pesos); several 1998-99 documents |
| h09 | contested | 5/5 | 42 (33) | WEAK | country-labelled "negative evidence" facets get positive/other-country quotes; planner invents stance facets |

Summary: 5 OK-or-better, 1 borderline pair (h03,h05), 3 WEAK. "Facets >=2 src" was 9/9 queries except h06 (5/6) and hid all three weak ones.
Failure classes: (1) planner leaves `entity` empty for country/technology facets, so cross-entity quotes pass (h01,h09,h03);
(2) Colombian page slipped through the jurisdiction filter (h08); (3) planner invents stance facets (h09); (4) same sentence
kept across sources/facets (h05).
This set is now SEEN. Fixes must be validated on dev queries, then a new held-out set drawn (h10+).
