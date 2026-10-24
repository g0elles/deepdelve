# Cheap validation on existing reports (2026-10-24)

Replaces the cancelled 10-report GPU batch (user: too slow). Eight saved reports, drawn by a fixed rule (seed 0) from the 22 saved runs that have a report and sources, excluding the 10 already audited. The draw came out Finland-heavy (six Finland and Estonia runs, one Raft, one EU AI Act), so independence from the first ten is partial: same queries, different report text. All eight were labelled **before** any detector ran on them, except `val_fi_20261023_202339`, which I had evaluated earlier in the session (marked "not blind" in its labels). Labels: `labels_validation/`. Replay: `AUDIT_SET=validation python eval/audit_runs/replay_checks.py`. Facet coverage could not be scored here: the evidence sets behind these older runs are not kept.

## The reports

59 defects in 8 reports, 14 of severity 3; **7 of 8 reports have at least one severity 3 defect** (first set: 10 of 10). Most frequent severity 3 type: mismatched comparison (C4), in 5 of the 8 reports (for example Finland's internet penetration set beside Estonia's share of services online, or the OECD index beside the UN index under one name). No detector exists for it yet.

## Detector results (every firing judged by hand)

| Detector | Firings | True | False | Precision | Read |
|---|---|---|---|---|---|
| figure_context | 16 (2 underlying defects) | 16 | 0 | 1.00 | Both defects were wrong citation numbers: a duplicated section whose second copy used numbers that do not match the Sources list, and a report where most numbers point at the wrong source. Inverted figures and invented doses did not occur in this set, so that class is untested here. Fewer than 5 distinct true defects: below the pre-registered bar for "advisory", so advisory only. |
| citation_structure | 3 | 2 | 1 | 0.67 | The "four or more citations on one sentence" rule fires on a legitimate range row; below 0.80. Keep only the no-inline-citations and duplicate-URL parts (both precise in the first set); drop the stacking rule. |
| weak_sources | 4 | 4 | 0 | 1.00 | Recall 2 of 4 reports with a labelled weak-source defect; it missed a Nigerian newspaper, a law-firm blog (URL path `/blog-detail/`) and a tech magazine. Hint list only. |
| stale_status | 1 | 0 clear | 1 borderline | n/a | A dated "as of 24 August 2026" in a report written five weeks later. Disclosed, so not a defect I would call severity 2. Too few cases to judge. |
| cross_source_contradiction (after WP2) | 0 | | | | No firings, no labelled instance of its class. |
| claim_unsupported and figure checks (after WP2b) | 4 | 3 | 1 | 0.75 | The false alarm: a bullet beginning "Using Raft indirectly" was flagged against a page that says exactly that; the sentence-initial word plus "Raft" forms a spurious two-word term. WP2b does not cover it. |

## What this changes

* The detectors that passed Phase C hold up on new reports of the same queries, but only for the wrong-citation-number class; the other classes lack positives in this set.
* A new, frequent defect class has no detector: comparisons on different measures (5 of 8 reports, severity 3). It is the next candidate for a detector, probably with a semantic judge, and needs its own labelled set (this set provides 7 examples).
* Citation numbering that does not match the Sources list (3 of the 18 reports across both sets) is caught well by figure_context.
* A further small false-alarm class in `claim_unsupported` (sentence-initial capital plus a proper noun) is now known; not fixed.
