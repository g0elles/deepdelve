# Phase C: replay verdicts on the 10 labelled reports (2026-10-24)

Replay harness: `replay_checks.py`; shadow detectors: `detectors.py`. No model, no network, no change to the product. Every firing below was judged by hand against the report and its saved sources. The detectors were tuned while looking at these same 10 reports, so these numbers are an upper bound; Phase D (new queries) is the unbiased test.

Rule used to move a detector to Phase D: precision at least 0.80 on replay, with at least one true hit per claimed class. The plan's promotion rules (5 true positives, held-out) apply later.

| Detector | Fired | True | False | Unverified | Precision | Phase D? | Notes |
|---|---|---|---|---|---|---|---|
| stale_status (WP3a) | 2 | 2 | 0 | 0 | 1.00 | yes | psilocybin "expected in summer 2024" (severity 3); AI Act "as of 24 August" 38 days before the run (severity 1). Only two firings. |
| facet_coverage (WP3b) | 8 | 7 | 1 | 0 | 0.88 | yes | true: psilocybin mechanism and dosing, GLP-1 dosing regimens, LSM production systems (both facets), flu consequences. False: "Americas" missed because the report says "United States". Found two real gaps I had not labelled (dosing). Recall on labelled unanswered parts: 3 of 4. |
| figure_context (WP3c) | 9 | 6 | 1 | 2 | 0.86 | yes | true: invented 5.4 mg dose, backwards $57 billion, three wrong-citation figures (14.9, 20.9, 14%). False: 2.4 mg. Unverified: 27.3 lbs and 12.3 kg against the NEJM paper. Recall on numeric-misplacement defects: 5 of 5, but all in two reports. |
| citation_structure (WP3e) | 5 | 4 | 1 | 0 | 0.80 | yes | true: no inline citations (severity 3), duplicate URLs (2), five citations on one sentence. False: four citations on one sentence that legitimately cites four sources. |
| weak_sources (WP3f) | 19 | n/a | n/a | n/a | n/a | advisory only | URL-pattern classifier; fires on 6 of the 8 reports with a labelled weak-source defect and on none of the others. Accuracy is not measurable without a source-quality label set; it is a hint list, not a verdict. |
| gap_vs_body (WP3d) | 11 at 0.75 coverage, 4 at 1.0 | 4 (all in one report) | 7 at 0.75, 3 at 1.0 | 0 | 0.36, then 0.25 | NO | Lexical overlap cannot tell "the body mentions the topic" from "the body answers it": every false hit was a body that discusses the topic without answering the gap. Needs a semantic judge (a model call), not a cheap detector. Reworking it is out of scope until a labelled set exists. |

## Repair of an existing check (WP2)

`find_cross_source_contradictions` read a decimal percentage such as "29.1 %" as the bare decimal 29.1 (its decimal pattern is tried before its percent pattern), then compared it with unrelated decimals on other pages ("0.1", "3.6 point"). Fix: a percent-first pattern used only by that check. Replay: the two false alarms in the psilocybin report disappear; no firing on any of the ten reports; the check's designed case (12% against 18%) and a new decimal-percentage case both still fire in the unit tests, and the new test fails without the fix. Committed on branch `wp2-cross-source-percent` (not merged). The replay could not show a retained true positive on these reports because none of the ten contains a true cross-source contradiction of that shape, so the retention evidence is the unit tests only.

## Second repair (WP2b)

`claim_grounding_problem` compares a claim's salient terms with its cited source by exact set match. The flu report's segment about India's toll had only two proper-noun terms, "Pacific Island" and "Saharan African", and the source wrote "Pacific Islands", so a supported claim was flagged (integers such as "17 million" are not salient terms at all). Fix: compare terms up to letter case, a trailing plural -s and apostrophe-s, in this check only. Replay: the flu false alarm disappears and the three true catches stay (Raft September, Finland and Estonia, GLP-1). The unit test fails without the fix. Committed on branch `wp2b-claim-term-plurals` (d3d18cd), not merged. Claim-unsupported on these reports after the fix: 3 firings, 3 true.

## What this does not show

* Precision was measured on the reports the detectors were built from.
* Recall is only against my own labels, which the second-labeller pass suggests undercount over-generalisation.
* The congestion pricing cross-source false alarm (15% speed gain against 22% pollution reduction) did not reproduce on the final report, so WP2 is not shown to fix it; the live firing was on an earlier draft.
* WP2b widens matching slightly, so it can only remove firings; its cost is a small loss of sensitivity to claims whose only shared term is an inflection variant, which the unit test's different-place case still catches.
