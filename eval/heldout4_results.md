# Held-out set 4 (h19-h27), run once at src/pipeline == d3e791a + title plumbing (uncommitted), 2026-09-30

Config: same as heldout3 (plan gpt-oss think=low -> search -> reflow -> BM25 -> CE -> filters -> LLM judge). Wall 131-238 s/query. Fetch ran with DEEPDELVE_FORCE_VIRTUAL_DISPLAY=1 (h19 ran before it was set).
Grades: 3 quotes per facet read (one per source), NO targeted greps: shallower than heldout3's pass, so provisional. Single reader = Claude. OK = on-topic, specific, right entity.

| id | cat | facets >=2 src | grade | note |
|---|---|---|---|---|
| h19 | comparison | 4/4 | OK- | Estonia good; Finland "adoption" facet = internet-penetration stats, not digital-service use; one climate-strategy quote |
| h20 | legal | 6/6 | OK- | all 3 countries covered, days facets strong (25/30/15); "pay calculation" facets weak: Brazil f4 has maternity leave + exit costs, Korea f6 has no numbers |
| h21 | technical | 6/6 | OK | write-amp numbers, benchmarks, DB lists all on-topic; one dubious "SQLite 4.0" content-farm quote |
| h22 | scientific | 5/5 | OK | mechanism, ATTAIN-1/retatrutide/SURPASS-CVOT figures good; "phase 3 dosing/adverse events" facets hold Phase 2 and vendor-site quotes |
| h23 | historical | 5/6 | OK- | Europe (2.64M) and Americas good; Asia/Africa facets thin, Africa quotes are about missing data; f2 = 1 quote from brainly.com (honest gap) |
| h24 | news_recent | 5/6 | OK | recency works (Aug 2026 quotes, Dec-2027 delay); f1 "current status" got 0 sources; one quote says delays "are NOT law" (conflicts, stale) |
| h25 | numeric | 6/6 | WEAK | Ghana missing entirely (same class as h16 Kenya): facets only for Portugal and Vietnam; Portugal GDP mixes quarterly/projected/historic figures |
| h26 | spanish | 5/5 | OK- | quotes now Spanish (h17 class fixed); noise: casino-site, Bupa, Allianz, LinkedIn opinion; "principales actores" facet weak |
| h27 | contested | 3/3 | OK- | no opposite-direction facet pairs survived, evidence balanced (Gallup 2025, BBC, Bloom); but f3 "improves wellbeing" holds negative quotes (label mislead, h18 class) |

Summary: 0 OK+, 3 OK, 5 OK-, 1 WEAK (heldout3: 2 OK+, 1 OK, 4 OK-, 2 WEAK). No clear generalization gain; WEAK 2 -> 1, but no OK+.
Fixed on fresh data: recency (h24), Spanish-language quotes (h26), no polarity facet pairs (h27, label issue remains), Paxos-Commit class not re-tested (h21 has no variant trap).
Still failing: planner drops a named entity (h25 Ghana) even after the re-plan in ba7f50d; coverage metric cannot see it (h25 = 100%); "pay/adoption" style facets fall back to adjacent-topic quotes.
Titles: evidence.json "titles" captured for 56-113 of each run's fetched sources (bridge fix working).
This set is now SEEN.
