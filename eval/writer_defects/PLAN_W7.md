# W7. Entity filter admits entity-silent sentences from a page about the facet's entity (code only, no model, no GPU)

Found 2026-11-22 (W4 follow-up, W6 sibling): on c4 (gemma r1) the UK facet dropped "We bought 46,400 tonnes less sugar through soft
drinks that are subject to the levy in 2020 compared to 2015, a decrease of 34.3%" (foodfoundation.org.uk; page counts UK 6, EU 3, in 1)
because `src/pipeline/run.py:entity_ok` requires the SENTENCE to name the facet entity. Pages state their country once in a heading and
then write "we", "the levy", "the country". The rule exists for a real reason (2026-09-25, `session_status/2026-09-25.md:285,320`):
"coverage = >=2 sources" hit 5/6 on q21 with quotes about Europe/US, and Lima facets filled with Europe-wide stats. `entity_ok` already
receives `source` and ignores it.

## Change (v2, after EGM review 1: 0 blockers, 5 majors, 6 minors applied)
`entity_ok(fid, sent, source, ents)`: keep today's rule; additionally accept when ALL hold:
1. The facet has exactly one entity and the WHOLE folded entity is a country name or alias: `_COUNTRY_NAME.fullmatch(ent)` over the
   `_COUNTRIES` patterns, or "us"/"usa" (W6 fallback). "lima, peru", "mexico city" are not countries (review M3).
2. The sentence names no country, no `_REGIONS` key, and no entity of any other facet (union of the other facets' sets, `_mentions`).
3. The page is about that country: `_page_counts(source)` (text only, `functools.lru_cache`; NO ccTLD credit, review M2; US counted
   by spelled-out forms only, `_COUNTRIES["us"]` on folded text, never `_US_ABBR`, review M1 "CONTACT US") gives that country >= 3 and
   >= 2x every other country.
`source_off_jurisdiction` unchanged (it keeps its ccTLD and `_US_ABBR` counting). `select_ce` and `entity_ok` signatures unchanged.
Known gaps left to the labels (review m3): world words ("globally", "OECD", "Nordic") and foreign city names on a dominated page are not
detected; `_mentions` 5-letter prefix ("clima" ~ "lima") can only reject, never admit. A facet country the query does not name gets no
gain (its pages are already dropped by `source_off_jurisdiction`); c4 names "Reino Unido" (`countries(query)` = {mx, uk}).

## Tests (`tests/test_pipeline_quotes.py`, each fails on current code unless marked guard)
- UK facet, page "UK " x6 + "EU " x3, sentence "We bought 46,400 tonnes less sugar ... a decrease of 34.3%." -> accepted (2x boundary).
- Guards (pass today): same page "In France consumption fell 5%." rejected; page "UK " x3 + "France " x3 rejected (not dominant);
  {"lima"} on "Lima " x10 rejected; {"lima, peru"} and {"mexico city"} on Peru-/Mexico-dominated pages rejected; "Europe" sentence on a
  UK page rejected; US facet on a page "ABOUT US CONTACT US FOLLOW US" rejected; a UK-silent page on a .co URL rejected for a {"colombia"}
  facet; two-entity facet rejected.

## Acceptance (offline; pre-registered before any W7 replay is read)
- Script: `w6_replay.py` extended (tag-named output `<tag>.json` for tags starting "w7"; pools store [url, text]; `--diff A B` prints
  every added/removed row and writes `w7_rows.jsonl` for labelling). The W6 snapshots keep working. "Before" regenerated on HEAD
  f8f4f5b with the extended script (a copy of `w6_after.json` was made, then deleted unread for W7: text-only, no URLs).
- Labels, blind (shuffled; sentence + facet name/questions + page URL; no added/removed tag): every ADDED and every REMOVED row in ANY
  facet pool (review M4: displaced sentences and cross-facet demotion). Rubric: ON = the page's subject is the facet's entity (or, for an
  unconstrained facet, the query's scope) and the sentence reports something about it; OFF = other entity, world/multi-country aggregate;
  NOISE = boilerplate, navigation, citation, definition (counted separately, as OFF in the bar); UNCLEAR counts as OFF.
- Baseline (anchor for the bar, review m5): 40 random rows from today's single-country facet pools labelled in the same shuffle.
- Bar (all three): (a) ON / added >= 0.80 with >= 15 added rows (fewer -> report "insufficient", not pass); (b) ON added >= ON removed;
  (c) the c4 UK sugar sentence reaches the c4 UK pool, else report the stage that drops it (does not fail the bar alone).
- User spot-check: 20% of labelled rows (random, seed 7), not a fixed 10.
- Fail -> revert, record why here; no retuning of 3 / 2x after labels are read.
- Full test suite (41 scripts) passes; ruff clean. TUI/CLI/API: shared pipeline code, no flag or config key.

FROZEN before the W7 replay was read: sha256 5f265955a4b6babbbd954086fca5a8caf8048d03025e54a53592590f9c2a7491 (of the file above this line).

## Result (2026-11-22; `w6_replay.py w7_before|w7_after`, `--diff w7_before w7_after`, `w7_sheet.py`, labels `w7_labels.py`, `w7_score.py`)
- 56 of 217 facet pools change; 256 rows added, 253 removed, labelled blind with 40 baseline rows (549, shuffled seed 7).
- Added: ON 209 / 256 = 0.816 (95% Wilson CI 0.764-0.859), OFF 11, NOISE 22, UNCLEAR 14. Removed: ON 207 / 253 = 0.818,
  NOISE 31. Baseline (today's single-country pools): ON 37 / 40 = 0.925.
- Bar: (a) 0.816 >= 0.80 with 256 >= 15: PASS (CI lower bound below 0.80); (b) ON added 209 >= ON removed 207: PASS (net +2);
  (c) the c4 UK "46,400 tonnes less sugar ... 34.3%" sentence now reaches the c4 UK pool (gemma r1 f2): YES.
- Reading: pass as pre-registered, but on aggregate the change is a near-even swap (same ON rate in and out); the gain is targeted,
  entity-silent figures from country pages (c4 UK sugar purchases, h26/h20/h14 country pages). All 11 OFF and 14 UNCLEAR added rows sit in
  c4's unconstrained facet ("Efecto del impuesto ...": multi-country summaries, generic health claims), reached through cross-facet
  demotion (`select_ce` `owner`), not through the new rule into a country facet.
- User spot-check: `w7_spotcheck.md` (110 rows, 20%, seed 7) pending.
