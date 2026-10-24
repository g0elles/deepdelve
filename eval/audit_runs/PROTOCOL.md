# Defect audit protocol (written 2026-10-24 BEFORE any audited report was read)

Goal: a map of what goes wrong in DeepDelve reports, by task and by pipeline stage, as a scientist reader would experience it. No code changes come from this step.

## Sample (fixed before reading)
- Saved runs with final report AND saved sources, one per query by rule "most recent run, ended without budget exhaustion": EU AI Act (20261001_232456), Raft/Paxos (20260929_231541 is the most recent, 387 words; also take 20260929_224216), Finland/Estonia (20261023_181948).
- New reports written 2026-10-24 by the current code on saved evidence: h13 psilocybin, h22 GLP-1, h21 LSM vs B-trees, h12 Raft vs Paxos, h18 congestion pricing, h23 1918 influenza, h27 remote work. One run each, whatever the run produced (no reruns, no cherry-picking). A run that hit the hard cap is audited as is and flagged.

## Units
1. Claim: every sentence with a figure, a named entity, a comparison or a causal/status statement. Checked against the cited source file(s).
2. Question part: each part of the query, answered / partly / not.
3. Gap: each statement of missing evidence, plus each place the report should have disclosed one.
4. Source: each source carrying a key claim.

## Defect types (fixed list; anything else goes to OTHER with a description)
C1 claim not supported by its cited source (figure or fact absent or different)
C2 claim supported by a source other than the cited one (wrong citation)
C3 overstated or generalised beyond the source (scope, certainty, population)
C4 mismatched comparison (different quantities, years or populations placed side by side without saying so)
C5 internal contradiction (including a gap statement contradicted by the body)
C6 outdated or undated status presented as current
Q1 question part unanswered or only gestured at
Q2 relevant material in the saved sources omitted (a source answers a question part the report leaves thin or empty)
S1 key claim rests on a weak or non-primary source while a stronger one is in the saved sources
S2 irrelevant material included (padding)
G1 evidence gap not disclosed
G2 evidence gap disclosed that the sources actually answer (false gap)
F1 format or meta defect (pipeline language, broken structure)
OTHER

## Severity (reader side)
3 = misleading: a scientist acting on it would be wrong. 2 = weakens trust or needs rework. 1 = cosmetic.

## Stage attribution for each defect
E = evidence stage (search/fetch/extract never put the right material in the saved sources or findings), W = writing stage (material was available, report distorted or omitted it), J = judging/gap logic, H = harness artefact. Mark U when I cannot tell.

## Output
Per report: defect list (type, severity, stage, location, one-line basis). Pooled: counts per type and stage with Wilson 95% intervals (unit = claim for C-types, question part for Q-types), and for each defect type whether any current deterministic check would have flagged it (from evaluate_run and the completion checks).

## Checking the labels
All labels are by one LLM labeller (me). A blind second labeller (eval/claim_support/second_labeller.py logic) re-labels a random 30% of the C1/C2/C3 verdicts; agreement is reported. The user (a domain reader) is asked to review a small subset (the 10 highest-severity findings plus 10 random ones). Until then the map is provisional.

## Limits stated up front
Single labeller; 3 of the saved queries are not scientific; new reports come from one run each (variance between runs not measured here); evidence sets are from pipeline runs with their own nondeterminism.
