# W10 (DROPPED 2026-11-22, user). A figure caption chosen as the comparison overview's lead quote (code only, offline)

Found 2026-11-22 (academic run): the compiled "Answer in brief" opened with the PLOS caption "Fig 3 compares ... Chile's Law ... with ...
Mexico's 10% SSB tax". `src/writer/compiled.py:_overview_pool` (:137) puts up to 4 `_bridging` quotes (naming both sides) first; a caption
names both sides, so it ranks first, and it compares Chile's law with Mexico's TAX, not the asked comparison.
Size (offline, 2026-11-22): 1 of 42 saved compiled reports has a caption in its brief; 4 caption-shaped quotes in all saved evidence sets.

## Change
`_bridging(q, sides)` returns False for a caption-shaped quote: `_CAPTION = re.compile(r"^\W*(?:fig(?:ure)?\.?|table|tabla|cuadro|gr[aá]fico)\s*\d+", re.I)`.
The quote stays in its facet (it may carry data); it only loses the bridging slot. No pipeline change.
ponytail: English/Spanish caption words only (multilingual: add per language when seen).

## Tests (`tests/test_writer_compiled.py`)
A comparison build whose only two-side quote is "Fig 3 compares X in Chile with Y in Mexico." -> not in the bridging part of the pool (pool
starts with facet quotes); a non-caption two-side quote still leads. Fails on current code for the caption case.

## Acceptance
Skeleton replay (`eval/writer_ab/skeleton_replay.py`): 45 reports, 0 errors, no check fires; the academic run's evidence
(`research_output/acad_ornith_chmx_r1`) re-composed offline: the caption is no longer first in `_overview_pool`. Full suite + ruff.

## Decision: dropped (user, 2026-11-22)
EGM review: the quote is body text referring to a figure, not a caption; the real issue is that "names both sides" is not "compares them
on what was asked", and a "Fig N" filter only hides one surface form. 1 of 42 reports; the "comparison not verified" line already covers
the misleading case. Known limitation: a two-side quote comparing the wrong things can lead the brief (`compiled.py:_overview_pool`).
