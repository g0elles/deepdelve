# W6. Pronoun "us" read as the United States in pipeline country detection (code only, no model, no GPU)

Found 2026-11-22 (W4 follow-up, `PLAN_W4.md`): gemma c4 r1 fetched a UK government page ("We bought 46,400 tonnes less sugar through soft
drinks that are subject to the levy ... a decrease of 34.3%"); on the UK facet it passed BM25 and the CE (3.56 vs min -4) and was dropped.
One cause: `src/pipeline/run.py:_COUNTRIES["us"]` contains `\bus\b`, matched on case-folded text, so "resulted in us buying" is tagged US.
(The other cause, sentence-only `entity_ok`, is a design trade-off and gets its own plan.)

## Blast radius (all in `src/pipeline/run.py`)
- `countries()` (:256) folds, then matches every `_COUNTRIES` pattern. Callers:
  - `jurisdiction_ok` (:261): a sentence naming only other countries than the query's is dropped. Pronoun "us" makes any English sentence
    with "us" a US sentence, so it is dropped on every non-US country query unless it also names a query country.
  - `source_off_jurisdiction` (:267): counts each pattern's matches on the folded page; pronoun "us" inflates the US count, so an English
    page can be dropped whole as US-dominated.
  - `_mentions` (:311) via `countries(text_folded)` and `countries(ent)`: a "United States"/"US" facet accepts any sentence with "us".
  - `select_ce` (:502) `qkeys = countries(query)`: a query containing "us" ("tell us ...") gets US as a query country.
  - `query_entities` (:340) iterates `_COUNTRIES` for typed entities.
- Not touched: `engine/completion_checks_grounding.py:_facet_mentions` (separate code, no `_COUNTRIES`).

## Change (v2, after EGM review 1: 1 blocker, 2 majors, 5 minors applied; review 2: 0 blockers, 0 majors, 3 minors applied)
1. `_COUNTRIES["us"]`: drop `\bus\b`; keep `\bunited states|\bestados unidos|\bu\.s\.`, add `\beeuu\b|\bee\.\s?uu\b` (Spanish).
2. `_US_ABBR = re.compile(r"\bUS(?:A)?\b(?!\$)")`, case-sensitive, on the RAW text (`(?!\$)`: "US$ 1.200 millones" is a currency, review m4).
   `countries(text)` adds "us" when it matches raw `text`. `source_off_jurisdiction` adds `len(_US_ABBR.findall(source))` to the US count.
3. `_mentions(text, ent)`: fold inside (idempotent) and call `countries` on the unfolded text, so callers can pass raw text.
   `entity_ok`, `facet_entities`, `_uncovered` pass raw text. Entities stay folded (planner entity :353, query words :338), so
   (review BLOCKER) `ck = countries(ent) or ({"us"} if ent in ("us", "usa", "eeuu") else set())`: a "US" entity still means the
   country, never the `\bus\b` word search at :323 that would keep every pronoun "us".
4. `query_entities` (:335-343): also add "us" when `_US_ABBR.search(query)` (review M3: a sentence-initial "US" was found only by the
   folded country loop).
   ponytail: ALL-CAPS headings ("CONTACT US") still read as US, and a lowercase typed query ("us vs uk tax") loses US; accepted.
   Same-class folded collisions left alone (rare here): `\beu\b` vs Portuguese "eu", turkey/chile(s)/panama/china as common nouns.
5. Tests (`tests/test_pipeline_quotes.py`). Fail on current code: "It has resulted in us buying less sugar" -> {}; "Let us look at
   Mexico" -> {"mx"}; "US$ 1.200 millones" -> {}; "EE.UU." / "USA" -> {"us"}; `jurisdiction_ok` keeps the sugar sentence on {mx, uk};
   `source_off_jurisdiction` keeps an English page with 10 pronoun "us" and 2 "UK" on {uk}; `entity_ok` on a facet with entity "us"
   rejects "tell us more". Regression guards (pass today): "The US levy", "Estados Unidos" -> {"us"}; `entity_ok` entity "us"
   accepts "United States exports rose" and "US exports rose"; `query_entities("US and UK solar subsidies compared")` contains "us".

## Acceptance (offline)
- Labelled sheet `eval/pipeline_relevance.jsonl` (17,565 sentences, 150 positive, 18 queries): `jurisdiction_ok` before vs after with
  each row's `countries(query)`. Report positives and negatives that flip kept/dropped. Bar: 0 positives newly dropped; every newly kept
  row printed. Caveat (review M2): no sheet query names the US, so the sheet can only show newly KEPT rows (pronoun removed) or rows
  newly tagged by "USA"/"EE.UU."/capital "US"; the US-query side is covered by the step-5 entity tests and the saved runs.
- "Before" = `eval/writer_defects/w6_replay.py before` run on the unchanged checkout (snapshot `w6_before.json`), "after" the same
  script after the change, `--diff` prints every flip.
- Saved pipeline runs with sources (`research_output/cmp2_*`, `research_output/w4ret_*`, `eval/heldout3_runs/*`, `eval/heldout4_runs/*`,
  `research_output/pipeline_*`): per page, `source_off_jurisdiction` before vs after against that run's query countries; per facet, the
  `select_ce` kept top-15 (`PER_JUDGE`; judge stubbed to keep all, so positions 16-20 of the judge pool are not compared) before vs after. Report pages and sentences that change; every
  changed page listed. The W4 sugar sentence is checked by the step-5 `jurisdiction_ok` test, not the replay (it still fails `entity_ok`, so it never reaches a
  facet pool; expected). Run dirs as in `w6_replay.py` (includes heldout4 `rerun2`, not `rerun`).
- Full test suite (41 scripts) passes; ruff clean. No live run: a filter fix with a labelled offline check.

## Result (2026-11-22, `w6_replay.py before` / `after` / `--diff`)
- Labelled sheet: 18 flips. **0 positives newly dropped (bar met)**, 0 positives newly kept, 16 negatives newly kept (all were tagged US
  only by pronoun or URL "us": "Contact us to know more", `/en-us/`, `a-us.storyblok`, `flags/us.svg`; relevance is the CE/judge's job, not
  jurisdiction's), 2 negatives newly dropped (bibliography entries "San Jose, CA, USA": correctly US now).
- Pages: 27 changed source_off_jurisdiction verdicts. 26 dropped->kept, of them 15 on c3 (Iceland/Ireland recovery: focus-economics
  Iceland, FRED Iceland GDP, grapevine.is unemployment, Brookings "Rise, Fall and Resurrection of Iceland") and 7 on h25/rerun2 h25
  (Vietnam), all previously dropped as US-dominated by pronoun counts. 1 kept->dropped: actualidad-rt beer/wine kidney study on c4
  (now counts its "EE.UU."; off-topic for a sugar-tax query).
- Facet pools (kept top-15): 9 of 217 changed. c3 Iceland facets gain policy/recovery sentences ("Iceland's economy rebounded strongly
  after the 2008 financial crisis", policy rate 8.50%); h25 gains Vietnam GDP per capita 2024 and internet penetration; c4 UK facet gains
  "studies on the success of sugary-drink taxes ... Mexico ... and the Reino Unido" in place of the beer/kidney sentence.
- Tests: `test_us_pronoun_is_not_the_united_states` (fails on old code), 41/41 scripts, ruff clean.
