# SOTA Literature Review: Small-Model Agentic Reliability

Moved to the wiki, 2026-08-20:

**→ [Literature Review](https://github.com/g0elles/deepdelve/wiki/Literature-Review)**

**Correction, 2026-08-20**: this file previously claimed every `RESEARCH.md §N` reference in code
comments still points to the same section number on the wiki. That's no longer true, a later wiki
condensation pass replaced every numbered header with a descriptive one, so `§14e` etc. are not
literal anchors anymore. The content itself wasn't lost, just renamed and shortened. Use this
crosswalk to find where an old section number now lives:

| Old section | Now lives in | Section name |
|---|---|---|
| §1 | [Verified Papers](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Verified-Papers) | whole page |
| §3 | [Leads & Corrections](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Leads-And-Corrections) | "Downgraded, do not cite without re verifying" |
| §3b | [Leads & Corrections](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Leads-And-Corrections) | "Read and rejected" |
| §4 | [Leads & Corrections](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Leads-And-Corrections) | "Open questions, resolved" |
| §5 | [Leads & Corrections](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Leads-And-Corrections) | "What's merged into the main repo's docs" |
| §6 | [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis) | "A non generative routing layer for `delegate_tasks`" |
| §7 | [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis) | "Comparative survey" |
| §8 | [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis) | "RAG reconsideration" |
| §9 / §9a | [Architecture Synthesis](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Architecture-Synthesis) | "Is DeepDelve's verification architecture novel, or documented prior art?" + "A gap closing follow up" |
| §10 | [Hardware & Serving](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Hardware-And-Serving) | "Why a structural fix beats a textual warning for recurring citation fabrication" |
| §11 | [Hardware & Serving](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Hardware-And-Serving) | "Was ROCm the cause of a 9 candidate vLLM disqualification streak?" |
| §12 | [Hardware & Serving](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Hardware-And-Serving) | "Ollama and llama.cpp tuning for this hardware" |
| §13 / §13a | [Hardware & Serving](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Hardware-And-Serving) | "Qwen3 think suppression: fixable template gap, or unfixable serving bug?" |
| §14, §14e, §14f | [Bake off Findings I](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings) | "Ornith-1.0-9B live bake off", plus its "Native backend tool call corruption" and "The clean re test that finally settled it" subsections |
| §15 | [Bake off Findings I](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings) | "Root cause of a Searcher over fetching for single fact tasks" |
| §16, §17 | [Bake off Findings I](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings) / [II](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings-2) | §16 "Comparative survey extension" (Findings I); §17 "Chasing convergence on the Lisbon/Mexico retest" (Findings II) |
| §18, §18b-f | [Bake off Findings II](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings-2) | "The 2026-08-17 seven-fix arc, cross-checked against current literature" |
| §19 | [Bake off Findings II](https://github.com/g0elles/deepdelve/wiki/Literature-Review-Bakeoff-Findings-2) | "AgentFloor: a capability-threshold benchmark directly on topic for this project's model search" |

If a specific number/quote/table cited in a code comment needs to be re-verified against the exact
original wording, the pre-condensation version is still in git history: `git show 87e3ebb~1:RESEARCH.md`.
