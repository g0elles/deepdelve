# Literature leads: Builder fixes that do not stick (2026-10-22)

**Status: SEARCH-SUMMARY LEVEL ONLY. No paper below has been read. None is marked verified, none may be cited as
load-bearing evidence, and every number here is as reported by a search summary. The full read is scheduled for
2026-10-23 (starred papers first); a paper gets a verified mark only after `pdfinfo` page count and a read of every
section that could matter, per CLAUDE.md.** Context: ARCHITECTURE.md §2 "Builder fixes do not stick".

## Cause 1: a Builder pass that writes nothing (silent no-op)
| Paper | Reported finding (unverified) | Relevance |
|---|---|---|
| * Look Before You Leap: Pre-Action Verification for LLM Agents, arXiv 2609.11957 | Content-anchored edits (search/replace, diff) fail cleanly; location-anchored edits fail silently (line numbers corrupt 99.1% of files under a one-line shift). Deterministic pre-action check turns silent failures recoverable. | Supports comparing the artifact before/after a Builder pass and reacting. |
| * Understanding Agent-Reactive Bugs at the Model-Harness Boundary, arXiv 2607.15684 | Harness-level bug taxonomy; includes an agent retrying the same failed edit until the iteration cap. | Same shape as repeated read-only Builder passes. |
| When Tools Fail, Agents Fabricate (NeurIPS 2026, neurips.cc/virtual/2026/173038) | Agents accept faulted tool data on first contact; a verification-first monitor lifted recovery only 42.5% to 48.0%. | Argues for a deterministic harness check, not model self-noticing. |
| SilentProbe, arXiv 2609.00035 | Of 94 retried cases, 11 retried with a different value, 0 of 11 recovered a non-zero result. | Retry with a CHANGED directive, not an identical one. |

## Cause 2: a fix for problem A lost when rewriting for problem B
| Paper | Reported finding (unverified) | Relevance |
|---|---|---|
| * SCREWS, Microsoft Research (microsoft.com/en-us/research/?p=974454) | Revisions can introduce new mistakes; rolling back to the earlier result can be better. | Rollback to the last draft that passed a check. NEW option not in the first brainstorm. |
| LLMs as Packagers of HPC Software, arXiv 2511.05626 | Repair loops oscillate between error states; authors justify a ~5-15 attempt cutoff. | Matches replay 2 and 3 behaviour. |
| ICML 2026 paper on refinement history, icml.cc/virtual/2026/82772 | Compares including full refinement history vs not (only the setup was seen). | Should the Builder see its earlier drafts? |
| Gap | Nothing found testing constraint preservation across multi-turn edits. | Needs its own search. |

## Cause 3: weak gap-disclosure detection
| Paper | Reported finding (unverified) | Relevance |
|---|---|---|
| GRACE, arXiv 2601.04525 | RL for grounded response and abstention when evidence is insufficient. | Abstention is hard for models. |
| LIT-RAGBench, arXiv 2603.06198 | Current LLMs still struggle to recognise insufficient evidence in RAG. | Same. |
| Prompt-based abstention study (as summarised; source not identified) | Abstention works when context is absent, fails when it is misleading. | Penetration figures standing in for adoption rates are the misleading case. |
| Gap | Nothing on disclosing gaps in LONG-FORM reports; all results are short-answer QA. | Needs its own search. |

## Small-model self-correction (background)
* Small Language Model Can Self-Correct, arXiv 2401.07301 (AAAI): prompt-only self-correction hard for small models; fine-tunes instead.
* arXiv 2604.13759 claims prompt-only correction is unreliable below ~13B, citing Huang et al. SECONDARY CLAIM: verify against the original.
* arXiv 2506.15894 argues intrinsic self-correction may be stronger than commonly shown (counter-evidence; read both sides).

## Effect on the plan (provisional, pending the full read)
Supports a deterministic harness check (fix 1A) and a structural gap section (1C/2B) over model self-correction, and adds a
rollback-to-last-passing-draft option. Does not change the cause analysis, which rests on the replay logs, not on these papers.
