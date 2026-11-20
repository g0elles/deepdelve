# Order-of-magnitude rate conflict probe: pre-registration (2026-11-19, written before the probe ran)
Question: can a deterministic rule catch a kept quote whose rate figure is an order of magnitude off what another fetched source states for
the same quantity, with precision high enough to act on? Trigger: 2026-11-19 smoke, source [22] "117 pounds of CO2 per therm" (true ~11.7)
while three other fetched sources said 5.31 kg per therm / 53.1 kg per million Btu. utils.grounding.cross_source_hits misses it: it needs a
2+-word proper-noun subject and compares figure strings without unit conversion.
Rule: in a kept quote (evidence.json, what the writer can use), a compound rate "<number> <unit> [of X] per|/ <unit>" with both units in a
small conversion table (mass, energy, volume, length, area; no money, no time denominators). Normalise to SI. Fire when a DIFFERENT fetched
source of the same run states a rate of the same dimension pair, sharing an attribute word of 4+ letters within its sentence (same as
cross_source_hits' ctx_words idea), whose value differs by a factor >= 5.
Corpus: the 33 evidence dirs (eval/writer_ab/skeleton_replay.DIRS) plus research_output/smoke_overview_slots_20261119 (known positive).
Unit of labelling: a fire = (quote figure, the other source's conflicting figures). TRUE if both state the same quantity (same substance,
same scope) so at least one side is wrong or the disagreement is real and a reader should be told; FALSE if they are different quantities
(different fuel, process, scope, lifecycle vs operational, per-unit-of-something-else) or the regex misread the number or unit.
Bar: build only if pooled precision >= 0.80 over >= 10 fires, and the smoke 117 lb/therm quote fires. < 10 fires on 34 dirs = too rare
to justify a rule now (report, do not build). Labeller: one (me); per-fire output kept in runs/fires.json.
