# E1 grading rubric (frozen with PLAN.md; sha256 recorded in every labels header)

Labeller model: claude-opus-5-5 in fresh subagents, one context per packet batch, pass 1 and pass 2 independent (PLAN.md decision 2).

You grade one research report against reference answers, one query part at a time. The report is blind: you do not know which
system, model or arm wrote it. Grade only what the report text says, using the cited source excerpts supplied with it.

## Labels (exactly one per part)

| Label | When |
|---|---|
| ANSWERED | The rule for the part type below is met. |
| WRONG | The report answers the part, but the answer contradicts the reference and no cited saved source supports it; or it gives a wrong-period or wrong-entity figure as the answer. |
| MISSING | The part is not addressed, is explicitly disclosed as a gap ("not covered", "no evidence found"), or the answer has no citation. |

### Fact / figure parts: ANSWERED when all hold
1. The report states the value or statement for the named entity (and period, where the part or reference names one).
2. It is tied to a citation marker [N] (in the sentence or its evidence bullet).
3. It matches the reference `statement`/`value` within the reference's own stated precision, or matches an `alt_values` entry.
   A differing value still counts when the cited source excerpt states it verbatim, the source class is allowed (below), and it uses
   the same definition and period as the part asks for.
Partial coverage of a multi-element part (e.g. days but not pay) = MISSING unless the missing element is minor to the part text;
when unsure, MISSING.

### Comparison parts: ANSWERED when either holds
- Both sides are stated (each meeting the fact/figure rule for its own side) AND an explicit relation on the part's measure is
  stated (larger/smaller, stronger/weaker, similar, X times, etc.) that agrees with the reference `statement`; or
- A not-comparable statement that names its reason (different measure, period, population or design).
A table or two paragraphs with both sides but no stated relation = MISSING. A relation that contradicts the reference with no
cited support = WRONG.

### Allowed source classes
Peer-reviewed papers and preprints of named studies, official statistics, government and legal texts, clinical guidelines,
international organisations, recognised research or consumer organisations, and press that reports a named primary source.
Not allowed as sole support for a differing value: anonymous blogs, marketing pages, forums, AI-generated summaries.

### Procedure
- Read the part text and its reference first, then search the report for it. Headings are hints, not evidence.
- Do not reward length, style or confidence. Do not penalise extra content that does not contradict the part.
- Do not use outside knowledge to rescue or fail an answer; only the reference and the supplied source excerpts.
- For each label give `evidence_quote`: the report sentence(s) you relied on (verbatim, up to ~40 words), or "" for MISSING with
  nothing found.

## Labeller prompt (subagent)

You are grading blind research reports for the DeepDelve evaluation set. Read `eval/eval_set/rubric.md` in full, then the packet
file you are given. For every report in the packet and every part listed under it, output one JSON line:
`{"report_id": "<blind id>", "part_id": "<part id>", "label": "ANSWERED|WRONG|MISSING", "evidence_quote": "<verbatim report text or empty>"}`
Write all lines, nothing else, to the output path you are given. Grade each part independently. Do not look at any other files,
earlier labels, or the repository; do not guess which system wrote a report.
