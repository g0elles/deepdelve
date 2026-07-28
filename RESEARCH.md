# SOTA Literature Review: Small-Model Agentic Reliability

Standalone working document, started 2026-07-19. Not yet merged into ROADMAP.md/README.md —
this is intentionally large and still growing; merge only the load-bearing conclusions once the
review closes out. Every claim below distinguishes **primary-source-verified** (I read the actual
paper/data directly) from **not yet verified** (only seen via `WebSearch`/`WebFetch` AI-mediated
summaries) — do not upgrade a claim's confidence without doing the verification pass first.

## Why this exists

DeepDelve's own bake-off (10 local-model candidates, 9 disqualified, `gpt-oss:20b` the only full
pass — see ROADMAP.md's "Model bake-off" log) raised the question of whether the project is
hitting a real, externally-documented ceiling on small-model agentic reliability, or a
DeepDelve-specific gap. This review cross-checks that against current (2026) academic and industry
literature, using exact terminology discovered in primary sources to chain into further relevant
work (citation-chaining / snowball search), rather than generic keyword search alone.

## Methodology note (2026-07-19, self-correction mid-review)

The first pass of this review (papers found via `WebSearch`) was presented with more confidence
than it had earned — `WebSearch`/`WebFetch` return AI-generated summaries, not primary reading, and
several PDF fetches failed to parse. The user caught this and asked for the actual scientific
method: primary sources read directly (via `curl` + the `Read` tool's PDF support), claims
verified against the actual text/data/tables, corrections made transparently where the summary was
wrong or imprecise. Section 1 below reflects the corrected, primary-source-verified state for every
paper marked ✅. Papers marked ⚠️ are still only seen via search summary and should not be treated
as verified fact.

---

## 1. Primary-source-verified papers

### ✅ "When Agents Fail to Act: A Diagnostic Framework for Tool Invocation Reliability in
Multi-Agent LLM Systems" — Huang, Malwe, Wang (Singapore Management University + Mastercard R&D),
arXiv:2601.16280

- **Verified numbers** (Table I): `qwen2.5:3b` = 13.9% success rate; `qwen2.5:7b` = 57.3%;
  `qwen2.5:14b` = 96.6% (their stated "minimum viable production" threshold); `qwen2.5:32b` = 100%
  (parity with `gpt-4.1`); `qwen2.5:72b` = 95.1% (non-monotonic — worse than 32B, "diminishing
  returns / task-specific capacity limits," their words).
- **Corrected**: the "89%" figure I first cited is not overall failure rate — it's `qwen2.5:3b`'s
  rate on ONE subcategory (`DB_UPDATE_TOOL_NOT_INITIALIZED`, 881/990 = 88.99%). Overall failure
  rate for 3B is 86.1% (100% − 13.9% success) — close, but I'd conflated a component with the
  headline.
- **Domain caveat (important, previously omitted)**: narrow domain — invoice reconciliation, 3
  fixed tools (OCR, DB query, DB update), 1,980 deterministic test instances at temperature=0 with
  fixed prompts. Much more controlled/repetitive than DeepDelve's open-ended research task. The
  qualitative pattern (small models fail disproportionately at tool *invocation*, not argument
  content) plausibly transfers; the exact percentages should not be treated as predictive of
  DeepDelve's own success rates. Also ran Ollama v0.6.8, several versions behind DeepDelve's
  installed 0.31.2.
- **Relevance to DeepDelve**: quantifies, with a real number, almost exactly what the bake-off
  found empirically (every 2-8B candidate disqualified, `gpt-oss:20b` the only pass) — a real
  published capacity floor around 14B for tool-use tasks, not something a single fine-tune should
  be expected to fully close.

### ✅ "Agent Explorative Policy Optimization for Multimodal Agentic Reasoning" (AXPO) — Kang, Diao,
Hachiuma et al. (NVIDIA + KAIST), arXiv:2605.28774, 2026-05-28

- **Verified mechanism** (corrected from my first pass, which was vague/wrong): identifies the
  "Thinking-Acting Gap" — under GRPO, tool use is attempted on only ~30% of rollouts, and when
  attempted, the tool-using subgroup is *all-wrong* ~40% of the time (vs. ~25% for no-tool
  rollouts), so tool-call tokens get non-positive advantage under group-normalized reward (Figure
  3, quantified). AXPO's actual fix: for all-wrong tool-using subgroups specifically, **freeze the
  reasoning prefix up to the tool-call boundary and resample only the tool call + its
  continuation**, prioritizing lowest-confidence prefixes first (uncertainty-based ranking). This
  is proven, not just claimed empirically — Proposition 1 (§3.1) shows resampling strictly
  dominates raw additional sampling at recovering correct tool-using rollouts.
- **Verified numbers**: SFT+AXPO beats SFT+GRPO by +2.8/+2.3/+1.8pp Pass@4 at 2B/4B/8B. Headline:
  8B+AXPO surpasses the 32B *base* (untrained) model on Pass@4 using 4x fewer params.
- **Domain caveat**: this is a Vision-Language Model paper (`Qwen3-VL-Thinking` 2B/4B/8B/32B),
  multimodal benchmarks (Python interpreter, web search, image zoom-in tool) — Qwen3 family
  (aligns with DeepDelve's `qwen3:4b` target) but the VL-Thinking checkpoint lineage, not the plain
  text-only base DeepDelve trains on. The GRPO mechanism/insight is domain-agnostic and should
  transfer conceptually; the specific numbers should not be assumed to transfer as-is.
- **Relevance to DeepDelve**: directly maps onto `writer_role_response_reward`'s exact problem
  (`write_workspace_file` is the sparse, high-value action under GRPO, same shape as AXPO's
  tool-call boundary) — NOT `thin_coverage` (which is about a completed rollout's coverage, a
  different problem shape).
- **Now read through §6/Conclusion (11 of 41 pages — main body complete, appendices not read).**
  Two more verified facts that matter before adapting this:
  1. **The paper's own stated limitation**: *"Our study assumes verifiable outcome rewards for RL
     and trains models up to 8B parameters."* AXPO's mechanism specifically resamples a tool-call
     boundary and checks whether the resampled continuation reaches the CORRECT final answer
     (`r_k^res ∈ {0,1}` against ground truth). DeepDelve's `writer_role_response_reward` is a
     different reward shape — it checks whether the STRUCTURAL action happened (was
     `write_workspace_file` actually called, not narrated) rather than whether downstream content
     was correct. **Before adapting AXPO's specific resampling-and-recovery-reward mechanism, this
     mismatch needs to be checked against `finetune/reward.py`'s actual implementation** — the
     underlying insight (concentrate exploration budget at the sparse, high-value action boundary)
     likely still applies, but the "recovery indicator" mechanics (Eq. 3-5) were built for a
     binary-correctness setting DeepDelve's writer-role reward doesn't have. Still an open item,
     now more precisely scoped.
  2. **Table 3, verified**: AXPO beats not just SFT+GRPO but every alternative tried — reward
     shaping (tool penalty/bonus), doubling the raw rollout budget (2x compute, same method,
     underperforms AXPO), and three other RL algorithm baselines (RLTF, CISPO, ARPO). The paper's
     own framing: "the gain comes from *where* compute is spent, not *how much*" — a real, tested
     claim, not just asserted.

### ✅ "Why Reasoning Fails to Plan: A Planning-Centric Analysis of Long-Horizon Decision Making in
LLM Agents" — Wang, Wu, Wang et al. (Notre Dame, Stanford, Edinburgh, Yale, Purdue, Oxford, UIUC),
arXiv:2601.22311

- **Verified theory**: formalizes LLM chain-of-thought reasoning as a step-wise *greedy* policy —
  locally justifies each decision, no mechanism to revise an early choice based on later
  consequences. Proves (Propositions 3.1-3.3) this is arbitrarily suboptimal for long-horizon
  tasks, that widening search (beam search) does NOT fix it (still ranks by local scores), and that
  even one step of genuine lookahead strictly dominates in the worst case.
- **Proposed fix (FLARE)**: three specific, formally required mechanisms — (1) explicit lookahead
  (simulate candidate futures *before* acting), (2) backward value propagation (revise earlier
  action values from simulated outcomes), (3) limited commitment via receding-horizon replanning
  (commit to one action, replan after each real transition). The paper explicitly states which
  existing paradigms fail which requirement: **CoT and beam search lack lookahead; Reflexion/ReAct
  lack lookahead too** (only react after a bad outcome, never simulate ahead); RL-baked-into-weights
  approaches lack online replanning.
- **CORRECTION, significant**: I first claimed this paper validates DeepDelve's *existing*
  completion-check system. Wrong. DeepDelve's completion-check loop (verify after the fact, nudge,
  retry) is a form of **reflection** — one of the paradigms this paper explicitly names as
  insufficient (no lookahead). The corrected mapping: DeepDelve's NEW engine-driven deepening round
  (shipped 2026-07-19, ROADMAP item 10) partially matches ONE of the three required mechanisms
  (limited commitment / receding-horizon replanning — the engine composes each next round from
  real completed evidence, not trusting one long generation to self-correct) — but is still not
  "explicit lookahead" in the paper's formal sense (which means simulating *hypothetical* futures
  before acting; DeepDelve's deepening round reacts to real completed results, not simulated ones).
- **Verified numbers**: myopic-trap selection at first decision — single-step reasoning 55.6%,
  FLARE 17.8%. Recovery probability after first error — single-step 5.4%, FLARE 29.7%.
- **Domain caveat**: primary evaluation is knowledge-graph QA (CWQ/WebQSP/GrailQA), deterministic
  environments with oracle-guaranteed solution paths — explicitly chosen to remove real-world
  environment uncertainty, which DeepDelve's actual web research has in abundance (you don't know
  in advance which search will surface something useful). ALFWorld (tool-use, long-horizon) tested
  as a generalization check and FLARE still wins there, partially extending applicability.

### ✅ "Constraint Tax in Open-Weight LLMs: An Empirical Study of Tool Calling Suppression Under
Structured Output Constraints" — Li, Zhang, Lv (Focus AI Center, Focus Technology Co. + Nanjing
University of Science and Technology), arXiv:2606.25605, 2026-06-24

- **Verified in full, held up well against my first-pass summary.** Controlled 3-condition design:
  T1 (tools ON, schema OFF) — baseline; T2 (tools ON, schema ON) — the joint condition; T3 (tools
  OFF, schema ON) — schema-only control. Table 7 confirms: **T1 = 100% TIR for every one of 7
  models tested; T2 = 0% TIR for every open-weight model; T3 schema compliance 80-100%.** Only the
  proprietary `GPT-5.4-mini` kept 100% TIR under T2.
- **Root cause, verified at the token level** (Table 6, traced through SGLang 0.5.9's actual call
  chain): the JSON-schema grammar FSM masks the `<` character (U+003C, first char of `<tool_call>`)
  to `-inf` logit across every FSM state — tool-call tokens become physically unreachable during
  decoding, not a training/prompting failure.
- **Confounds ruled out empirically, not just claimed**: schema complexity ablation (0% TIR
  regardless of 1-3 fields vs. 20+ fields); tool-enforcement ablation (0% TIR even with
  `tool_choice="required"`); raw-stream inspection confirmed zero `tool_calls` events were ever
  emitted (not a parser bug); framework independence (SGLang vs. vLLM, identical result — rules out
  a framework-specific parser bug); **fine-tuning ablation, exact numbers (Table 10)**: Base, Tool
  Mandatory (200 samples), Schema Injection (200 samples), GRPO (200 samples), and even a
  **Large SFT run at 6,000 samples** — all still 0% TIR under T2. Post-training alone cannot fix
  this, at meaningful scale, not just a small trial.
- **Full paper now read (31 pages, not just the front half)**. Honest limitations section (§8.4),
  in the paper's own words: evaluation covers "a finite set of open-weight models and one
  closed-source reference model" (conclusions support the evaluated models, not a universal claim);
  the benchmark is "substantially smaller than large-scale academic evaluation suites"; CPI is
  explicitly restated as "a behavioral hypothesis... not a verified internal mechanism"; the
  mitigation evaluation covers tool-calling workflows only — unclear if it generalizes to MCP
  ecosystems, multi-agent protocols, or computer-use agents. **Real cost of their own proposed
  fix, quantified** (Table 12): Two-Pass Execution requires 2 full LLM rounds instead of 1, adding
  "approximately one additional inference round plus tool execution time" of latency and roughly
  doubled token consumption (full original request context repeated in Pass 2) — a real overhead,
  not free. Robustness check: 200+ extended queries across 10 company profiles × 8 compliance
  markets for one model all independently reproduced 0% T2 TIR.
- **This paper builds on "The Constraint Tax: Measuring Validity-Correctness Tradeoffs in
  Structured Outputs for Small Language Models" (Ray, arXiv:2605.26128, 2026)** — the originating
  paper for the "constraint tax" concept itself, now read in primary form, see the dedicated entry
  immediately below. **Important distinction, precise this time**: the two papers measure two
  related but empirically DIFFERENT phenomena under a similar name, not the same finding at two
  scales — this one (arXiv:2606.25605) measures whether a tool-call TOKEN can be emitted at all
  under a joint tools+schema condition (a token-level FSM masking effect, binary: 0% or 100% TIR);
  the originating paper measures whether a schema-VALID object's CONTENT is semantically correct
  (a continuous accuracy metric, degrading but not collapsing to zero). Do not conflate them.
- **Directly important finding for DeepDelve**: their model matrix (Table 4) includes
  **`GPT-OSS-20B`** — architecturally the same family as DeepDelve's own default
  (`deepdelve-gpt-oss`). It shows 0% TIR under T2, same as every other open-weight model. **Checked
  DeepDelve's own code**: `grep -rn "response_format\|json_schema" src/` returns zero matches —
  DeepDelve never combines a JSON-schema `response_format` with tool availability in the same call,
  so this exact failure mode is not currently live. Must stay that way: any future feature adding
  structured-output validation to a call that also has tools available would need to follow the
  paper's own proven fix.
- **Their proven mitigation**: Transparent Two-Pass Execution — Pass 1 tools-on/schema-off (free
  tool use), inject results, Pass 2 schema-on/tools-off (structured final output). Restored 100%
  TIR and 100% schema compliance simultaneously. This is, functionally, what DeepDelve's
  Planner→Builder/FindingsWriter split already does (for an unrelated original reason — context
  poisoning) — a genuine, independently-arrived-at match, not a coincidence I should overclaim
  further without more digging.

### ✅ "The Constraint Tax: Measuring Validity-Correctness Tradeoffs in Structured Outputs for Small
Language Models" — Jaideep Ray, arXiv:2605.26128, 2026-05-20, the originating paper for the
"constraint tax" concept the arXiv:2606.25605 paper above builds on

- **Provenance caveat, stated plainly**: solo author, email-only contact (`jaray@acm.org`), no
  institutional affiliation given anywhere in the paper. Same category of red flag that sank the
  Entropy paper (§3b) on its own — but unlike that paper, the actual methodology here holds up on
  direct inspection: deterministic synthetic tasks with exact-answer normalization, real generated
  JSONL/CSV artifacts (the paper states plainly "we do not infer numbers from model expectations or
  hand-labeled outputs"), reported 95% bootstrap confidence intervals, an honestly-disclosed
  negative result that contradicts its own thesis (below), and a full appendix with the actual
  prompt template, JSON schema, and executable-checker code used — reproducible in a way the
  Entropy paper's "derivation" never was. Read in full (11 pages including appendix); provenance is
  a real caveat to weigh, methodology is not one.
- **Core concept, formalized**: `Tax(m,t,c;b) = max(0, Acc(m,t,b) − Acc(m,t,c))` — the
  task-relevant accuracy lost when a fixed model on fixed task instances is forced from a baseline
  interface (prompt-only JSON) into a constrained one (hard schema decoding), clipped at zero so an
  accuracy GAIN from constraining is reported as a gain, not a negative tax.
- **Main-suite verified numbers** (Table 3, 15,000 generations across 5 deterministic task families
  — arithmetic, symbolic strings, object tracking, boolean logic, tool-call arguments — aggregated
  over small-model checkpoints): hard answer-only schema decoding raises schema validity from
  **61.5% → 100.0%** (+38.5pts) but LOWERS answer accuracy from **19.7% → 11.0%** (−8.7pts) and
  raises the wrong-valid-schema rate (schema-valid output, wrong answer) from **49.5% → 88.9%**
  (+39.4pts). The parser sees a cleaner stream; the executor sees more well-formed objects carrying
  wrong content — the paper's own framing, and the core validity-vs-correctness tradeoff the title
  promises.
- **The calendar tool-call analogue is the sharper, more production-like result**: both prompt-only
  JSON and hard-schema tool-call modes reach 100% schema validity (so the regression cannot be
  explained away as a parsing failure), yet executable accuracy falls from 91.5% to **48.0%** under
  the hard schema — a 43.5-point drop, 95% CI [−51.0, −36.0]. **Root cause localized to a single
  field**: 102 of 104 hard-schema failures are a wrong `duration_minutes` value specifically (date/
  attendee/topic all correct) — a concrete, quantified illustration of "syntactically valid tool
  call, wrong argument value," the exact failure shape DeepDelve's own grounding/completion checks
  exist to catch downstream of generation, now with external evidence that constraining decoding
  can itself be a contributing cause, not just a documentation/prompting gap.
- **The tax does not disappear at the 3B boundary, checked directly, not assumed**: `Qwen2.5-3B-
  Instruct` still loses 15.3 answer-accuracy points and gains 31.6 wrong-valid-schema points under
  hard schema decoding (Table 9) — "the evidence does not support the claim that this issue is
  limited to the smallest models," the paper's own conclusion, stated exactly.
- **A genuine, honestly-reported negative result** (§7.2, "When Constraints Help Versus Hurt"):
  "not every constraint behaved like a tax" — `SmolLM2-1.7B` IMPROVES under hard schema decoding
  precisely because its prompt-only JSON baseline was weak (+37.4pts constraint-is-a-GAIN, Table 5).
  A real counter-example to the paper's own headline thesis, reported rather than omitted — the
  operational boundary the paper draws: constraints help when they fix syntax failures without
  disrupting the model's task search, and hurt when they convert a visible format failure into a
  valid WRONG decision that no downstream parser would ever catch.
- **"Reason free, constrain late" (§6.6)**: the expanded-interface study's `delayed_constraint` mode
  (let the model answer unconstrained first, then deterministically re-serialize into the required
  schema afterward) reaches 100% schema validity while preserving the HIGHEST executable accuracy
  of every mode tested (40.7%, vs. 26.8% for `answer_only_schema`) — **this is the second,
  independent paper in this review (after the arXiv:2606.25605 Two-Pass Execution result above,
  itself citing a different mechanism) to land on the same practical recommendation via a different
  experiment and a different author**: let a model reason/act freely, defer schema enforcement to
  after semantic work is done. Two independently-arrived-at confirmations of the same design choice
  DeepDelve's own Planner→Builder/FindingsWriter split already embodies (for its own, unrelated
  original reason — context poisoning) strengthens this pattern considerably more than either paper
  alone would.
- **Explicit non-claims, honestly stated (§9 "Generalization and Reproducibility")**: "the study
  does not establish a scaling law" — whether/when the tax disappears at larger scale than 3B is
  explicitly left open, not claimed either way. The synthetic deterministic task families are
  described by the paper itself as "stress tests for reasoning under output control, not a
  replacement for broad user workloads"; the calendar tool-call task is "closer to a production
  tool-call path but still a controlled analogue rather than logged agent traffic." Model coverage
  is intentionally limited to low-compute, commodity-GPU-feasible checkpoints (sub-3B, one 3B
  boundary check) — no claim is made about anything larger, including DeepDelve's own
  `gpt-oss:20b` default.
- **Serving-stack sensitivity, a real caveat also found in the follow-on paper**: `SmolLM2`'s
  accuracy differs materially between vLLM (18.7%) and SGLang (25.7%) backends for the identical
  model and mode, while `Qwen2.5` results replicate identically across both — constrained-decoding
  behavior is a property of the full serving stack (tokenizer, decoding engine, schema, serving-
  patch versions), not the model weights alone. The same caution this review already flagged for
  DeepDelve's own Ollama version sensitivity (capacity-floor paper, §1: several Ollama versions
  behind DeepDelve's own installed 0.31.2) shows up independently here too.
- **Relevance to DeepDelve, concretely**: DeepDelve's own `delegate_tasks` tool-call arguments are
  exactly the kind of structured-output-under-constraint problem this paper measures. The
  wrong-valid-schema mechanism (schema-valid, content-wrong) is a plausible partial explanation for
  a real, already-logged DeepDelve failure: §6's own routing-classifier proposal found ~56 of 1,153
  real `delegate_tasks` calls (4.9%) used a syntactically well-formed but hallucinated `agent_id`
  string (`"searcher"`, `"PeerReviewer"`, invented role names) — schema-valid, semantically wrong,
  the identical failure shape this paper quantifies directly. Not proof of the same root cause
  (DeepDelve's Planner isn't under a JSON-schema `response_format` constraint the way this paper's
  hard-schema mode is), but a second independent line of evidence, on top of §6's own analysis, that
  "valid shape, wrong content" is the dominant small-model tool-use failure mode worth designing
  around — consistent with §6's own conclusion that a routing classifier (output space IS the
  schema, invalid output structurally impossible) is a more targeted fix than hoping better
  prompting or schema tightening closes this specific gap.

### ✅ "Why Do Multi-Agent LLM Systems Fail?" (MAST) — Cemri, Pan, Yang, Agrawal, Chopra, Tiwari,
Keutzer, Parameswaran, Klein, Ramchandran, Zaharia, Gonzalez, Stoica (UC Berkeley + Intesa
Sanpaolo), arXiv:2503.13657, **NeurIPS 2025 Track on Datasets and Benchmarks**

- **The single most rigorous source in this review.** 1,642 annotated execution traces across 7
  real MAS frameworks (ChatDev, MetaGPT, HyperAgent, AppWorld, AG2/MathChat, Magentic-One,
  OpenManus) and 4 model families (GPT-4 series, Claude 3, Qwen2.5, CodeLlama). 14-mode failure
  taxonomy (MAST) built via Grounded Theory from 150 hand-analyzed traces, inter-annotator
  agreement κ=0.88, scaled via an LLM-judge pipeline calibrated to κ=0.77 against human experts
  (validated on 2 out-of-domain benchmarks too, κ=0.79).
- **Verified verbatim**: "analysis reveals 41% to 86.7% failure rate on 7 state-of-the-art (SOTA)
  open-source MAS." (I'd first cited this number via a secondary citation in a much weaker paper —
  now traced to its real, much stronger primary source.)
- **Causal evidence, not just correlational** (important — this is what actually supports
  "architecture matters independent of model capability," properly this time): controlled
  intervention studies holding the SAME model fixed, only changing coordination design — giving one
  agent final decision authority instead of consensus raised ChatDev's success rate **+9.4%**;
  adding a high-level task-objective verification step raised ProgramDev success **+15.6%**.
- **The 14 failure modes, 3 categories** (System Design Issues 44.2% of the 1642-trace aggregate;
  Inter-Agent Misalignment 32.3%; Task Verification 23.5%) **map closely onto DeepDelve's own
  documented failure catalog**:
  - FM 2.6 "Reasoning-Action Mismatch" (13.2%) — academic name for DeepDelve's "narrate instead of
    write" bug.
  - FM 1.5 "Unaware of Termination Conditions" (12.4%) — DeepDelve's "STOP EARLY" / over-research
    problem.
  - FM 3.2 "No or Incomplete Verification" (8.2%) + FM 3.3 "Incorrect Verification" (9.1%) — the
    entire reason DeepDelve's grounding-check layer exists.
  - FM 1.1 "Disobey Task Specification" (11.8%) — DeepDelve's exclusion-enforcement bug class.
- **Worth treating as a standing reference**, not a one-time citation — the taxonomy vocabulary is
  precise enough to search against directly (see §2).
- **Now fully read (main body is 10 pages, not the ~47 my earlier PDF-structure page-count heuristic
  estimated — that heuristic was wrong, noting the error).** Two important additions:
  1. **The paper is more tempered about its own "architecture over capability" implication than I
     represented.** §5.3, verbatim: *"Although first step interventions lead to performance gains,
     not all failure modes are resolved, and task completion rates still remain low, indicating
     that more substantial improvements are needed. Achieving high reliability may require
     combinatorial changes ranging from agent system organization to model level improvements."*
     Their own +9.4%/+15.6% intervention wins are real, but they explicitly do NOT claim structural
     fixes alone get a system to reliable — both structural AND model-level improvement matter.
     Fair correction to how I'd framed the "architecture matters" takeaway earlier in this review.
  2. **§5.1 confirms failure profiles are system-specific, not universal**: AppWorld skews toward
     premature termination, OpenManus toward step repetition, HyperAgent toward incorrect
     verification — "there is no one-size-fits-all solution to MAS failures." This is direct support
     for the ATLAS idea (§2's still-unread lead) that a domain-specific taxonomy induced from
     DeepDelve's own traces could surface a different profile than the generic MAST percentages
     quoted above — those percentages describe an aggregate across 7 unrelated systems, not a
     prediction for what DeepDelve's own failure mix looks like.
  3. **No dedicated Limitations section exists in the paper** (checked directly, confirmed absent)
     — a minor, honestly-noted gap in an otherwise rigorous paper, not a reason to distrust the
     core findings.

### ✅ "MiniCPM4: Ultra-Efficient LLMs on End Devices" — MiniCPM Team (OpenBMB / Tsinghua-affiliated
supervision: Xu Han, Zhiyuan Liu, Guoyang Zeng, Chao Jia, Dahai Li, Maosong Sun), arXiv:2506.07900,
2025-09-04 (v2), 44-page technical report. Surfaced by the user via `github.com/openbmb/minicpm`.

- **Provenance check, real institutional paper**, not a single-author preprint — large team, named
  supervision including a recognized NLP lab (Maosong Sun's group, Tsinghua). Same credibility tier
  as MAST/Lost in the Middle, above the rejected Entropy paper.
- **The paper itself is primarily an efficiency/architecture paper (InfLLM v2 sparse attention,
  UltraClean data filtering, ModelTunnel v2, BitCPM4 ternary quantization), not an agentic-
  reliability paper.** Its headline claim (§1, verified): "MiniCPM4-8B achieves comparable
  performance with Qwen3-8B using only 22% of Qwen3's training data" — a training-efficiency claim,
  not a capability-superiority claim. Table 8/9's "surpasses similar-sized models on 15 tasks" (the
  exact phrase quoted in the repo README) is composed **entirely of MMLU/CMMLU/CEval/BBH/GSM8K/
  MATH500/MBPP/HumanEval/IFEval-class benchmarks — zero agentic or tool-use benchmarks appear in
  either table.** This claim is about general reasoning/knowledge/code capability, not tool-use
  reliability, and should not be read as evidence for or against DeepDelve's capacity-floor concern.
- **The actual tool-use evidence lives in a separate section, §6.2 "MiniCPM4-MCP: Tool Use with
  Model Context Protocol", and applies to a DIFFERENT, specially fine-tuned checkpoint** —
  `MiniCPM4-MCP`, built on top of the base MiniCPM4-8B chat model via supervised fine-tuning on
  ~140,000 MCP-tool-use instances the authors themselves constructed (data generation + Claude-3.7
  reverse-query generation + converted existing tool-learning datasets, §6.2.1). **The general
  MiniCPM4/4.1-8B chat checkpoint does not automatically inherit these numbers — a real, easy-to-
  miss distinction the README's plain "tool use" framing glosses over.**
- **Verified numbers** (Table 13, their own human-annotated MCP-tool-calling test set, 14 MCP
  servers spanning Airbnb/Arxiv-MCP-Server/Filesystem/Github/Slack/etc., sample-weighted average
  accuracy across func/param/param-value): `GPT-4o` 80.2/70.2/49.1; `Qwen3-8B` 83.5/67.7/43.8;
  `MiniCPM4-MCP` **88.3/76.1/51.2** — MiniCPM4-MCP wins on all three axes against both baselines.
- **Critical methodology caveat, not disclosed as a limitation by the paper but visible from reading
  §6.2.1-6.2.3 directly**: this is a **self-constructed benchmark evaluated against a model
  specifically fine-tuned on this exact tool/server distribution**, compared to GPT-4o and Qwen3-8B
  used zero-shot/out-of-the-box on servers they never saw in training. The paper's own text (page
  38) attributes the gap directly to this: *"MiniCPM4 learns from the demonstrations and thus knows
  the characteristics of our collected MCP servers and tools"* — i.e., the win is attributed to
  in-domain fine-tuning, not to a general small-model tool-calling advantage. This is not a
  like-for-like comparison of general tool-calling ability the way DeepDelve's own bake-off or the
  capacity-floor paper (arXiv:2601.16280, §1) test it — it's evidence that fine-tuning ON a specific
  tool distribution helps (which DeepDelve's own GRPO work already assumes and acts on), not
  evidence that an 8B model is broadly tool-use-reliable off the shelf.
- **Even the winning number has a real reliability ceiling worth noting**: parameter-VALUE accuracy
  (`p_v`) is the lowest of the three metrics for every model tested — 49.1% (GPT-4o), 43.8%
  (Qwen3-8B), 51.2% (MiniCPM4-MCP). Getting the function name and parameter schema right is
  necessary but not sufficient; even the best-performing model here gets barely half of actual
  parameter VALUES correct — directly the same failure shape DeepDelve's own grounding-check layer
  exists to catch (a syntactically valid tool call with a wrong/hallucinated argument value).
- **Relevance to DeepDelve**: does not overturn the capacity-floor finding (arXiv:2601.16280, 14B
  as "minimum viable production" for tool invocation) — different benchmark, different task
  (MCP-server tool selection vs. invoice-reconciliation tool sequencing), and the one head-to-head
  win here is confounded by in-domain fine-tuning, not a clean capability comparison. **Real,
  usable takeaway**: MiniCPM4-MCP's own construction process (learning-from-demonstration on
  ~140,000 real interaction trajectories, distilled from a strong-LLM-driven client) is
  methodologically similar to what DeepDelve's own GRPO fine-tuning already does with real extracted
  session data — a working existence proof for that general strategy (fine-tune ON your own actual
  tool/environment distribution), not a reason to consider MiniCPM as a base-model swap candidate.

### ✅ MiniCPM5-1B evaluation leaderboard (repo's own results table, `assets/minicpm5/
public_leaderboard_en.png`, read directly as an image, not the README's prose summary of it) —
2026-05-26 release, `github.com/openbmb/minicpm`

- **The README's own prose claim** ("its strengths are most visible in agentic tool use, code, and
  competition math") **does not fully hold up against the disaggregated table it's summarizing.**
  Two benchmarks are listed under "Agentic Evaluation": `BFCLv4` (Berkeley Function-Calling
  Leaderboard v4, the more widely-recognized standard tool-calling benchmark) and `τ²-Bench
  Telecom-AA` (a narrower, single-domain agentic benchmark). Verified scores against the 3
  same-size-class rivals compared (`Qwen3-0.6B`, `Qwen3.5-0.8B`, `LFM2.5-1.2B`, all "Thinking"
  variants):
  - `BFCLv4`: MiniCPM5-1B 25.15, Qwen3-0.6B 25.43, Qwen3.5-0.8B 25.53, LFM2.5-1.2B 10.60 —
    **MiniCPM5-1B is essentially tied with (very slightly behind) two of the three rivals** on the
    more standard benchmark, only clearly ahead of LFM2.5-1.2B.
  - `τ²-Bench Telecom-AA`: MiniCPM5-1B 79.53, Qwen3-0.6B 21.10, Qwen3.5-0.8B 47.70, LFM2.5-1.2B
    19.60 — a real, large lead on this one benchmark specifically.
  - The "strongest in tool use" framing is driven almost entirely by the τ²-Bench result; on BFCLv4
    specifically there is no meaningful advantage. A prose summary that says "strongest in tool
    use" without noting this split is an overclaim relative to what the table itself shows — the
    same category of issue this review already corrected in itself and in other sources (§3,
    "Demystifying RL"'s Table 2 correction).
  - The headline "42.57 average, above 35.61" score is a 16-benchmark average spanning general
    knowledge/domain knowledge/coding/instruction-following/math/logic/agentic — only 2 of 16 rows
    are agentic. The large math/code gains (e.g. `AIME-2025` 40.42 vs. rivals' 1.04-31.88,
    `LCB-Pro 25Q2 Easy` 22.68 vs. 0.00-6.19) do most of the work in that average, not tool use.
- **No described evaluation methodology** (temperature, n-shot, prompt template, whether this is a
  self-run eval or an external leaderboard submission) accompanies the image — self-reported numbers
  in a results graphic, not a written methodology section the way MiniCPM4's arXiv report has one.
  Lower evidentiary weight than the arXiv paper above for that reason, though the numbers themselves
  were read directly rather than taken from the README's prose.
- **Size-class caveat**: this comparison set (0.6B-1.2B) is well below every size DeepDelve's own
  bake-off tested (2B+) and below the capacity-floor paper's tested range (3B+) — even the winning
  τ²-Bench number here (79.53) is being compared against other sub-1.5B models, not against
  DeepDelve's actual disqualified candidates or its 14B "minimum viable" threshold. Not informative
  for DeepDelve's own model-selection question either way.
- **Verdict**: real numbers, correctly read, but the specific "strongest in tool use" marketing
  claim overstates what the disaggregated data shows on the more standard of the two benchmarks
  tested. Does not change DeepDelve's bake-off conclusions or the capacity-floor finding — this
  size class (sub-1.5B) is far below anything DeepDelve considered viable, consistent with, not
  contradicting, the existing capacity-floor evidence.

### ✅ "Fantastic Adaptive Taxonomies and How to Use Them" (ATLAS / AdaMAST) — Cemri, Cojocaru, Pan,
Liu, Agarwal, Krentsel, Tang, Ramchandran, Gonzalez, Zaharia, Dimakis, Stoica (UC Berkeley + Bespoke
Labs), published at the **ICML 2026 Workshop on Failure Modes in Agentic AI (FAgEn)**

- **Provenance**: same lead author (Mert Cemri) and several co-authors (Gonzalez, Zaharia, Stoica)
  as MAST (§1 above) — this is effectively MAST's own direct sequel by the same team, not an
  independent replication. Read the actual paper PDF (`docs/adamast_paper.pdf` in the repo, not
  just the README), 9 pages main body. **Venue caveat**: a workshop paper, not a full peer-reviewed
  conference/journal track — a real, credible team, but a lighter review bar than MAST's own NeurIPS
  Datasets & Benchmarks placement.
- **Core mechanism, verified**: induces a 15-30 code **adaptive** failure taxonomy directly from a
  target agent system's own execution traces (not MAST's fixed, hand-authored 14-code catalog),
  organized along 3 axes — system-level (any agent system), role-specific (tied to a discovered
  agent role), domain-specific (requires task knowledge). A 4-stage LLM-driven pipeline (Analysis →
  Generation → Consolidation → Inter-Annotator Agreement, the last requiring κ≥0.70 across 4 LLM
  annotators on 50 traces as an acceptance gate).
- **Verified numbers across 3 downstream usages** (Tables 1-3, cross-checked against the text):
  - Best-of-N judging on Terminal-Bench 2.0: ATLAS-Judge reaches 73.0-89.9% across three harnesses
    (terminus-2/claude-code/ForgeCode) vs. Pass@1 of 57.5-81.8%, vs. a MAST-taxonomy-substituted
    version of the same pipeline at 68.5-88.8% — ATLAS's own domain-specific codes add real lift
    over MAST's generic vocabulary on 2 of 3 harnesses (ForgeCode saturates near-identically for
    both, an acknowledged "uninformative" comparison in the paper's own words since both already
    hit the Best-of-5 oracle ceiling).
  - Evolutionary agent-system optimization on OlympiadBench (655 held-out): Seed 84.6% → No-taxonomy
    evolution 87.9% → MAST-guided evolution 89.5% → ATLAS-guided evolution 91.9%. Comparable
    +3.3-7.5pp gains over the no-taxonomy baseline on 4 other benchmarks (Frontier-CS, MMLU-Pro,
    TheoremQA, DROP).
  - Runtime feedback for SWE-agent on SWE-bench Verified Mini (50 instances): Base 50% resolved →
    Reflexion (free-text self-reflection) 60% → MAST in-prompt 68% → ATLAS pattern A (in-prompt)
    70% → ATLAS pattern B (external judge, taxonomy kept outside the agent's own context) **78%**.
    The paper's own explanation for pattern B's edge: keeping the judge outside the agent's own
    context prevents the agent's own narrative of what it did from contaminating the judge's
    evaluation of whether it was actually correct — a mechanism argument, not just a number.
  - TRAIL validation: induced codes align with 4-expert-annotated GAIA traces at Cohen's κ=0.725,
    "recovers expert-labeled failures more faithfully than TRAIL's hand-crafted vocabulary" (the
    paper's own words, Appendix-referenced, not independently re-verified against TRAIL's raw
    annotations in this pass).
- **Real, honestly-disclosed limitation** (§5 Discussion, not hidden): an 8pp residual gap persists
  on OlympiadBench even after taxonomy-guided architectural search — the paper attributes this to
  an "architectural-vs-parametric distinction": restructuring the agent system around the model
  doesn't fix the underlying model's own mathematical reasoning limits. Directly consistent with
  this review's own capacity-floor finding (§1, arXiv:2601.16280) and PIVOT's own limitation ("repair
  quality remains bounded by the underlying model reasoning capacity") — a third independent source
  converging on the same point: architecture/verification layers amplify a capable model, they
  don't rescue an incapable one.
- **A separate, real caveat found by reading the REPO, not the paper**: the repo's own README states
  "the headline numbers below cannot be independently recomputed from this repository alone" for its
  own summary table (`runs/` directory has per-experiment writeups, not raw per-question rows or
  scorer output) — a transparency gap the project discloses about itself, worth noting even though
  it doesn't undermine the paper's own more detailed reporting.
- **Directly relevant to the standing "ATLAS-style taxonomy for DeepDelve" open question** (§4 below,
  formerly item 3): the tool literally ships an installable runtime (`adamast-import-traces`, "Learn
  from an existing trace folder") built for exactly this use case — inducing a taxonomy from an
  existing trace directory rather than a live hook. This is a closer match to what DeepDelve would
  actually need (retrospective induction from `_run_state.json`/`completion_check_attempts` history)
  than the live-runtime-skill deployment pattern (`adamast-claude-install` for Claude Code sessions)
  the rest of the README emphasizes. Not yet tried against DeepDelve's own data — a concrete,
  actionable next step if this direction is pursued, not just a conceptual match anymore.

### ✅ MAST production-telemetry replication (`github.com/hugomn/mast-taxonomy-production-telemetry`)
— upgraded from "partially verified" to fully verified this session (raw `mast_distribution.json`
and `reliability_trends.json` both pulled and checked digit-for-digit against every number quoted
below, not just the README prose)

- **Every previously-cited number confirmed exactly against the raw data**: population-reweighted
  primary-mode shares in `mast_distribution.json` sum to Task Verification (3.1+3.2+3.3) = 5.23% +
  2.37% + 1.07% = **8.67%**; System Design (1.1+1.3+1.4+1.5) = 2.45% + 2.69% + 0.84% + 1.19% =
  **7.17%**; Inter-Agent Misalignment (2.2+2.4+2.5+2.6) = 0.05% + 0.05% + 0.25% + 0.79% = **1.14%**.
  No discrepancy between the previously-cited figures and the underlying JSON.
- **New, previously-unread detail from the raw file**: the judge's own reliability against a
  hand-labeled gold set (39 runs) is disclosed directly in the data — `is_failure` Cohen's κ=0.797,
  primary-mode exact match 71.8%, "disagreements are mostly between adjacent termination modes." The
  classification underlying the whole distribution is a calibrated annotator, not ground truth — a
  real, quantified uncertainty band around every percentage above, not just a qualitative caveat.
- **Reliability-trend data also confirmed exactly** (`reliability_trends.json`): monthly failure rate
  14.6%/14.5% (Feb/Mar 2026) → 0.4% (May 2026), run volume 1,930 → 7,214/month (~3.7x, matching the
  README's "roughly 4x" claim); 14.2% of total spend went to problem runs, which used ~2.4x the steps
  of clean runs (53.0 vs. 22.0 avg) for about the same per-run cost (0.3042 USD vs. 0.3298 USD) —
  "failures waste effort more than money," confirmed exactly as quoted.
- **One minor inconsistency worth flagging honestly**: the README states "23,624 runs," while
  `reliability_trends.json`'s own cost-of-failure block reports `total_runs: 23994` — a ~1.6%
  discrepancy between two of the project's own published aggregates, most likely different
  denominators (e.g., a slightly different snapshot date or inclusion criterion) rather than an
  error either number depends on for the headline claims already verified above, but noted rather
  than silently smoothed over.
- **Everything from the previous partial-verification pass still holds**: one platform,
  "predominantly single-agent-per-cycle" by the author's own admission (so the near-absence of
  coordination failures may be architectural to that platform, not evidence coordination failures
  are rare in general); not peer-reviewed; the author's own self-caught infrastructure-bug artifact
  (a two-week termination-hang spike inflating the failure rate ~2.5x, excluded with a disclosed
  sensitivity check) is a good methodological sign, not a substitute for peer review.
- **Relevance to DeepDelve, unchanged**: still the closest external evidence that DeepDelve's own
  lived failure profile (verification-heavy, not coordination-heavy) is closer to real production
  behavior than MAST's own benchmark-derived aggregate — now confirmed to the decimal rather than
  read only from prose, raising this from a "leads, not facts" entry to a verified one.

### ✅ "How Coding Agents Fail Their Users: A Large-Scale Analysis of Developer-Agent Misalignment in
20,574 Real-World Sessions" — Tang, Chen, Xu, Shi, Huang, McMillan, Dong, Li (University of Notre
Dame + Vanderbilt University + Google), arXiv:2605.29442, 2026-05-28 (preprint, no venue stated)

- **Real, rigorous methodology, verified by reading the actual pipeline description**: two combined
  real-world datasets (SpecStory IDE+CLI exports, SWE-chat CLI logs via Entire.io), 20,574 sessions
  across 1,639 repositories, September 2024-April 2026. LLM-based extraction (GPT-5.4, temperature
  0) plus a dedicated second-stage validation pass specifically built to catch the extractor's own
  systematic false-positive patterns (named "normative prior bias" and "observational blind spots"
  in the paper itself) — 29,896 raw extracted episodes narrowed to 16,118 validated ones (53.9%
  retention). **Measured, not assumed, reliability**: extractor precision 0.93 (200 human-reviewed
  records), recall rating 1.77/2.00, human inter-rater agreement 0.83, final LLM-judge annotation
  accuracy 0.81 against an expert-adjudicated gold set.
- **Verified core numbers** (Table 2/3): of 16,118 validated misalignment episodes, seven symptom
  categories — Developer Constraint Violation (S3, 38.33%, most prevalent, 73.68% attributed to
  Instruction-Following Failure), Misread Developer Intent (S2, 26.95%), Inaccurate Self-Reporting
  (S7, 22.58%, "the agent consistently turns a partial or unverified state into a completion
  claim"), Faulty Implementation (S5, 17.82%), Wrong Project Diagnosis (S1, 11.56%), Self-Initiated
  Overreach (S4, 10.20%), Operational Execution Error (S6, 2.87%). **90.50% of episodes cost only
  developer effort/trust, not irreversible system damage; only 9.33% have a visible resolution in
  the logs, and 91.49% of those require explicit developer pushback to resolve (only 2.99%
  self-correct).**
- **A quantified cross-session persistence effect, new and directly relevant to a question DeepDelve
  doesn't currently instrument**: if a session had misalignment, the probability the NEXT session in
  the same repo also has misalignment is 0.519, vs. 0.336 baseline — a 54.46% relative increase.
  DeepDelve's own run-state persistence tracks resumability within a run, not this kind of
  cross-run correlation at the same research target; not something currently measured, a real
  candidate for a future check if repeated-target research sessions become common.
- **A genuinely nuanced temporal finding, not a simple "getting better" story**: the overall
  misalignment rate per user turn declines significantly over the dataset's timespan (p < 10⁻⁴⁰),
  but the COMPOSITION shifts as it declines — Developer Constraint Violation (S3) and Inaccurate
  Self-Reporting (S7) grow in relative share even as the aggregate rate falls, while Wrong Project
  Diagnosis (S1), Self-Initiated Overreach (S4), and Faulty Implementation (S5) shrink (all trends
  significant at p < 10⁻⁷, confirmed consistent when IDE/CLI sessions are regressed separately).
  Coding agents are getting better overall, specifically at technical correctness, while constraint-
  adherence and honest self-reporting are comparatively lagging — the paper's own stated
  interpretation: current reward signals likely favor code correctness over honest self-reporting.
- **Two direct, independently-arrived-at parallels to DeepDelve's own documented failure catalog,
  in a completely different domain (coding agents, not deep research) and at far larger scale (20K+
  real sessions) than anything else in this review**:
  1. **S7 "Inaccurate Self-Reporting" (22.58%) is the same failure shape as DeepDelve's own
     "narrate instead of write" bug** (`writer_role_response_reward`'s entire reason for existing,
     MAST's FM 2.6 "Reasoning-Action Mismatch" already cross-referenced in §1) — now confirmed as a
     common, cross-domain LLM-agent pattern with a real prevalence number in a domain that has
     nothing to do with research-report writing, not something specific to DeepDelve's own prompts
     or FindingsWriter design.
  2. **S3/C6 "Developer Constraint Violation"/"Instruction-Following Failure" (38.33%/36.49%,
     73.68% co-occurrence) maps onto DeepDelve's own exclusion-enforcement bug class** (MAST's FM
     1.1 "Disobey Task Specification," already cross-referenced) — a second independent confirmation
     of the same underlying failure mode, this time from a naturalistic 20K-session dataset rather
     than a controlled benchmark.
  3. **The paper's own disclosed measurement ceiling is directly relevant to the still-open ATLAS
     idea above**: "Cannot Determine" cause (C7, 26.85%) covers episodes where a failure is visible
     in the conversation but its root cause isn't recoverable from the log alone — a concrete,
     quantified illustration of the exact limitation any future attempt to induce a taxonomy from
     DeepDelve's own `_run_state.json` history would also hit (a completion-check verdict can show
     THAT something failed without the trace containing enough evidence for WHY).
- **Domain caveat, stated plainly**: coding agents (Cursor, GitHub Copilot, Claude Code, Codex,
  OpenCode, Gemini CLI), not deep-research agents — a real domain gap from DeepDelve's own use case.
  But this is the largest, most methodologically rigorous naturalistic (non-benchmark) agent-failure
  study read anywhere in this entire review (16,118 validated episodes with disclosed precision/
  recall/inter-rater figures throughout, vs. 376 judged sessions in the MAST telemetry replication
  above) — strong evidence that the general pattern (real deployment fails differently than
  benchmarks predict, and in ways that recur across agent domains) is not an artifact of any single
  study's methodology.

### ✅ "Why Your Deep Research Agent Fails? On Hallucination Evaluation in Full Research Trajectory"
— arXiv:2601.22984, 2026-01-22 (preprint, no venue stated, code and data released)

- **Real, rigorous methodology, read in full (not just the abstract)**: 6 real deep-research
  systems tested (Gemini, OpenAI, Perplexity, Qwen, Grok, Salesforce Deep Research) against a new
  100-query benchmark (DeepHalluBench: 75 queries selected specifically for inducing severe
  hallucination under Gemini, plus 25 adversarial "no-answer" queries). The claim-verification
  pipeline itself is independently validated against FEVER (~95% accuracy) and SciFact-Open (>85%)
  before being trusted on the target agents — the same "validate the checker before trusting its
  verdicts" discipline this project's own grounding checks were built with.
- **PING taxonomy, four categories, verified in detail**: **Grounding** (source-level — fabrication:
  claims unsupported by any retrieved evidence; misattribution: citing a real fetched document that
  doesn't actually support the claim) maps directly onto DeepDelve's own citation-fabrication bug
  history (`source_url == task_name` fallback, the 2026-07-21 fix). **Noise-induced** (context-level
  — relevant evidence WAS retrieved but got neglected during synthesis) is a real, independently-
  sourced third framing of DeepDelve's own "content vanishes during synthesis" pattern, distinct in
  mechanism from both Lost in the Middle (mid-context neglect) and PIVOT (no reasoning allocated to
  synthesis) — see below. **Intent** (query-level — restriction neglect: a technically-executable
  plan that silently ignores a stated user restriction) is the same shape as DeepDelve's own
  "hard exclusion rules repeatedly fail to hold" bug (`check_excluded_topic`, partially fixed).
  **Propagation** (trajectory-level — a later claim built on an earlier hallucinated one, cascading)
  has NO DeepDelve equivalent — every existing grounding check operates per-claim/per-finding in
  isolation; none trace whether a citable-looking claim was actually derived from an earlier finding
  that itself failed grounding. A real, concrete, currently-unaddressed gap class.
- **Directly corroborates DeepDelve's own "endgame collapse" open question** (`RESEARCH.md` §4 item
  1, "Lost in the Middle gives a partial mechanism, not a complete account"): the paper's own
  temporal-distribution finding shows Salesforce Deep Research suffers "late-stage collapse" (>40%
  of its errors occur late in the trajectory) while Gemini/OpenAI show early-stage cascading
  instead (>57% of errors) — an independently-measured, real system exhibiting the SAME
  turn-by-turn late-session degradation pattern DeepDelve has observed but not yet found a
  root-cause paper for. Also names a genuinely distinct positional-bias shape from Lost in the
  Middle's mid-context U-curve: an **"Anchor Effect"** where agents disproportionately favor EARLY
  retrievals and underuse LATER information "despite improving relevance" — recency-neglect, not
  mid-context-neglect. Both may be real and coexist; this is a nuance the "findings-ordering"
  ROADMAP candidate should account for (see that entry).
- **Detection mechanisms are concrete, not just diagnostic categories**: Grounding uses an
  NLI-then-LLM cascade with an adaptive second round to distinguish misattribution from fabrication.
  Propagation maps claims into their own DAG, running NLI-based entailment between a claim and the
  claims it depends on to trace whether a later claim's support chain touches an earlier hallucinated
  one. Both are directly adaptable check shapes for DeepDelve's own `grounding.py`, not just
  taxonomy labels.
- **Stated limitations, verified**: the framework diagnoses WHERE hallucinations arise in the
  workflow, not the underlying model's own parametric cause; text-only (no multimodal content);
  the atomicity-based evaluation is "more expensive than lightweight end-to-end metrics" by design,
  prioritizing diagnostic depth over throughput.

### ✅ "Detecting and Correcting Reference Hallucinations in Commercial LLMs and Deep Research
Agents" — Yuan et al., arXiv:2604.03173, 2026-04-03 (preprint, no venue stated)

- **Real, statistically solid research, read in full**: two benchmarks (DRBench, 53,090 URLs across
  10 models; ExpertQA, 168,021 URLs across 32 academic fields, 3 models), bootstrap 95% confidence
  intervals throughout, self-correction results reported with p<10⁻³⁵. `urlhealth` itself (released
  open-source, 83 lines of Python) is a simple, well-specified 3-step classifier: HTTP HEAD/GET →
  200 is LIVE; 404 with a Wayback Machine snapshot on record is DEAD (stale, not fabricated); 404
  with NO Wayback record is LIKELY_HALLUCINATED; anything else is UNKNOWN (10-20% of URLs land
  here, mostly paywalls/bot-blocking, an acknowledged ceiling). Self-correction (feed the flagged
  verdict back to the model, let it search for a replacement and re-verify) cut non-resolving
  citation rates 6-79x across GPT-5.1/Gemini-2.5-Pro/Claude Sonnet 4.5 — but the paper's own
  finding that GPT-5-nano called the tool and then ignored its verdict, repeatedly re-proposing the
  same flagged URL, is a direct, independent confirmation of this project's own repeated lesson
  (Model Evaluation Standard, MiniCPM5-1B's `not_delegated` false-completion claim): tool ACCESS
  does not imply tool USE competence.
- **Read specifically to check my own applicability caveat from the abstract-only pass — confirmed
  correct, not adoptable here as originally hoped.** The paper's own text states this applies "to
  search-augmented systems with web access," and its most striking finding cuts the other way for
  DeepDelve: four OpenAI search-augmented models showed **zero stale URLs** among their
  non-resolving citations — meaning 100% of those were outright fabrications generated WITHOUT ever
  actually retrieving the page, despite having live web access available. `urlhealth`'s entire value
  proposition is distinguishing "this URL is real but now rotted" from "this URL was never real" for
  systems where a citation can appear WITHOUT a real fetch ever happening. DeepDelve's own
  architecture already forecloses that failure mode more strongly than a Wayback cross-check could:
  `extract_cited_urls` + `fetched_urls` cross-referencing means a URL cannot become citable at all
  unless DeepDelve's OWN fetch tool actually retrieved it during the SAME run — there is no path for
  a purely-invented URL (never fetched, never in `fetched_urls`) to pass the existing grounding
  check in the first place, live or stale. **Verdict: real, well-evidenced research, genuinely not
  adoptable for DeepDelve's specific architecture** — reviewed and not pursued, same shape as the
  bibliographic-API citation-verification tool in ROADMAP's Rejected list (a stronger check for a
  failure mode DeepDelve's own design doesn't actually have).

## 2. Found via terminology-chaining (citation-chaining using confirmed vocabulary), not yet
primary-source-verified — ⚠️ treat as leads, not facts

**Empty as of 2026-07-22.** The three leads found here earlier this round (PING taxonomy, the
urlhealth/CiteAudit paper, VMAO) were all read in primary/full-text form the same day — see §1
(PING, urlhealth) and §3 (VMAO, downgraded after the full read). This section is kept as a
placeholder for the next lead this review turns up via citation-chaining, rather than deleted
outright, since that's the section's actual purpose.

**Moved out previously (2026-07-20, earlier cleanup)**: "Do Agents Need to Plan Step-by-Step?"
(arXiv:2605.08477), PIVOT (arXiv:2605.11225), and "Demystifying Reinforcement Learning in Agentic
Reasoning" (arXiv:2510.11701) — read and verified, now in §1. The Entropy Principle paper
(arXiv:2606.08162) was read and explicitly **rejected** — see §3b.

## 3. Downgraded / corrected from the first pass — do not cite without re-verifying

- **"Coordination as an Architectural Layer for LLM-Based Multi-Agent Systems"** (Nechepurenko &
  Shuvalov, Devnull FZCO, Dubai, arXiv:2605.03310) — I originally presented this as solid evidence
  that coordination failures are architectural, not capability-limited. Read in full: it is
  explicitly a **position paper** that states its own study is "a methodology-validating first
  instantiation... not a general claim about cross-model or cross-domain architectural laws." The
  actual pilot (n=100 Polymarket questions, single LLM `claude-opus-4-6`, web search disabled) had
  **3 of 5 pre-specified predictions confirmed, 2 failed**, and results explicitly did not survive
  Bonferroni correction at their own stated bar. Non-academic source (private company, not an
  institution), preprint, not peer-reviewed. The *conceptual framing* (coordination as a layer
  separate from information/agent layers) is still a reasonable vocabulary, matching how DeepDelve
  is already structured — but it was never evidence, and I should not have presented it as such.

- **"Verified Multi-Agent Orchestration" (VMAO)** (arXiv:2603.11445, 2026-03, preprint, no venue
  stated) — presented from the abstract alone as "potentially the most architecturally relevant" of
  a batch of new leads (its DAG of sub-questions with explicit inter-task DEPENDENCIES, something
  DeepDelve's own `delegate_tasks` doesn't model). **Read in full: the evidence base is much
  thinner than the abstract's headline numbers suggested.** Evaluation is 25 expert-curated queries,
  ONE model family only (Claude Sonnet 4.5/Opus 4.5 for both execution and the LLM judge — the
  paper's own text flags this as a same-family bias risk), no confidence intervals or significance
  testing ("the paper explicitly acknowledges 25 queries is a modest evaluation set... pending
  larger-scale evaluation"), and code "will be released upon publication" — not currently available
  to inspect. Most importantly, the paper's own text undercuts the DAG-dependency mechanism as the
  actual source of its claimed gains: "the majority of replanning actions are retries of incomplete
  sub-questions rather than introduction of entirely new ones, indicating that agent execution
  variance... is a larger contributor to gaps than poor initial decomposition" — i.e., much of the
  +35%/+58% improvement may come from a verify-and-retry loop (a mechanism DeepDelve's own
  completion-check system already has), not specifically from the DAG dependency structure that
  made this lead interesting in the first place. Also costs 8.5x the tokens of a single agent.
  **Downgraded from "candidate architectural direction" to "a named idea worth remembering, not
  evidence to act on"** — the dependency-graph concept itself may still be worth a from-scratch
  DeepDelve-specific evaluation someday, but this paper does not supply the evidence to justify
  building it now, and was correctly NOT added to ROADMAP's Pending after this full read.

### ✅ "Do Agents Need to Plan Step-by-Step? Rethinking Planning Horizon in Data-Centric Tool
Calling" — Otani, Bhutani, Kim, Zhang, Hruschka (Megagon Labs), ACM CAIS '26 (peer-reviewed,
ACM Conference on AI and Agentic Systems 2026)

- **Core finding, verified** (Table 2): comparing Single-step Horizon (SH — plan one tool call,
  observe, replan; matches DeepDelve's Planner's own "ADAPTIVE PLANNING LOOP") against Full-Horizon
  (FH — plan the whole tool-call sequence upfront, replan only on execution failure): SH shows no
  accuracy advantage over FH, and FH sometimes wins by a lot (GPT-4.1-mini: FH beats SH by 15.4
  points on GrailQA; Gemini-3-Flash: 17.2 points on GraphQ), while using 2-3x fewer tokens.
- **Concrete, directly-relevant finding** (Table 5): SH gets stuck in repetitive identical
  tool-call loops far more than FH — 30-45% of instances on some datasets for SH vs. 1.9-5.9% for
  FH. Hypothesized mechanism: FH's lazy replanning regenerates the *entire remaining plan* on
  trigger (tends to revise strategy); SH re-decides one action at a time after failure and more
  often just repeats the same failed local action.
- **Relevance to DeepDelve**: this is the theoretical/empirical shape of the exact failure that
  motivated `CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD` (`completion.py:979-1005`, generalized
  2026-07-19) — `missing_artifact` repeated 5x verbatim, `thin_coverage` burning a full retry
  budget, are documented DeepDelve instances of exactly this SH-style repetitive-loop pattern.
  Suggests a sharper fix than the existing 3-strikes cutoff: force a whole-plan regeneration on
  repetition detection (closer to FH's lazy-replan), not just a narrower nudge. Not yet
  implemented, not scoped — a candidate, not a decision.
- **Important domain caveat, stated by the paper itself**: evaluated on well-defined data-centric QA
  (structured KBQA, HotpotQA retrieval) with closed tool sets — not open-ended web research. The
  paper explicitly states *"SH planning may remain advantageous for exploratory or highly dynamic
  tool-calling tasks"* — DeepDelve's actual domain (you don't know what a web search will surface
  ahead of time) is arguably the exploratory/dynamic exception the paper itself flags. Does NOT
  straightforwardly say "switch DeepDelve to full-horizon planning" — the repetitive-loop mechanism
  is worth taking seriously regardless of domain, but the headline accuracy-parity result should
  not be assumed to transfer.

### ✅ "Lost in the Middle: How Language Models Use Long Contexts" — Liu, Lin, Hewitt, Paranjape,
Bevilacqua, Petroni, Liang (Stanford, UC Berkeley, Samaya AI), arXiv:2307.03172, **TACL 2024**

- **Foundational, highly-credible source** (this is well-established literature, not a 2026
  preprint of unknown standing). Verified core finding: a **U-shaped performance curve** — models
  use information well when it's at the very beginning (primacy bias) or end (recency bias) of the
  input context, and perform significantly worse when relevant information sits in the middle —
  confirmed across GPT-3.5-Turbo, Claude-1.3, LongChat-13B, MPT-30B, and replicated widely since.
  Holds even for models explicitly built for long contexts, and even for base (non-instruction-
  tuned) models.
- **Second verified finding, directly relevant to DeepDelve's design philosophy**: "model
  performance saturates long before retriever recall saturates" — using 50 retrieved documents
  instead of 20 only marginally improved accuracy (~1.5% for GPT-3.5-Turbo, ~1% for Claude-1.3).
  More context past a point does not help, and can effectively be wasted budget.
- **Relevance to DeepDelve**: this is a credible mechanism (distinct from anything in the rejected
  Entropy paper) for part of why long-running DeepDelve sessions might lose track of real, correctly
  grounded findings during final synthesis — the "content silently vanishes during synthesis"
  pattern (item 4 of today's audit fixed ONE cause of this: `_build_findings_source_material`'s
  previously-unguarded dispatch size). This paper suggests a SECOND, distinct cause that budget
  guarding alone doesn't fix: even within budget, information sitting in the MIDDLE of a long
  assembled context (e.g., a finding from the 8th of 15 dispatched tasks) is inherently harder for
  the model to use than one at the start or end, independent of whether it was truncated. Not yet
  tested against DeepDelve's own findings-ordering; a real, concrete thing to check if the
  content-loss pattern recurs after today's fix.

### ✅ "PIVOT: Bridging Planning and Execution in LLM Agents via Trajectory Refinement" — Zhang,
Popa, Xu, Song, Dimitriadis (Amazon), arXiv:2605.11225, 2026-05-11 (preprint, not confirmed
peer-reviewed venue — labeled "Preprint" on the paper itself)

- **Verified**: introduces PIVOT (Plan-Inspect-eVOlve-Trajectories), a self-supervised framework
  treating an agent's whole trajectory as an optimizable object. Four stages: PLAN (generate
  candidate trajectories), INSPECT (execute, compute a structured "textual gradient" that localizes
  the earliest causally-responsible failure point via backward discrepancy analysis), EVOLVE
  (rewrite the unsupported suffix from that point forward, preserving the validated prefix), VERIFY
  (final global constraint check). A monotonic acceptance rule ensures each refinement doesn't
  regress.
- **Verified numbers**: on DeepPlanning and GAIA benchmarks, human-in-the-loop feedback gives up to
  ~94% relative improvement in constraint satisfaction; the fully autonomous (no human feedback)
  variant "retains substantial gains." 3-5x more token-efficient than competing refinement methods.
- **Relevance to DeepDelve**: conceptually more sophisticated than DeepDelve's rule-based
  completion-check verdicts — PIVOT's INSPECT module does structured backward error attribution
  (find the earliest real break in a trajectory) rather than DeepDelve's fixed set of hand-written
  check functions. Interesting as a longer-term architectural direction, not something to adapt
  now — would require building a "textual gradient" mechanism DeepDelve doesn't have, a bigger lift
  than anything currently planned. Also directly cites and builds on FLARE and MAST — same
  literature cluster this review has already been chaining through, a good cross-check that the
  citation-chaining approach is finding a real, coherent research community rather than scattered
  unrelated work.
- **Now fully read (10 of 10 main-body pages). Genuinely new, important finding — a THIRD distinct
  mechanism for why real content might get lost during DeepDelve's final synthesis, on top of the
  two already noted (today's dispatch-size fix, and Lost in the Middle's positional effect).**
  §4.3 "Thinking is in the right place": the authors tested whether simply giving models a bigger
  extended-thinking budget (1024→3072 tokens) fixes long-horizon synthesis failures. It doesn't —
  no consistent gain on either benchmark. Trajectory inspection showed why: **100% of thinking
  blocks fire on the model's FIRST turn** (spent on task decomposition/tool selection), while
  **99.2% of final-answer-generation steps produce ZERO thinking tokens** — the model used only
  ~230 thinking tokens on average, well under the 1024 floor, regardless of how high the ceiling
  was raised. The model doesn't naturally allocate reasoning budget to synthesis/verification, no
  matter how much budget is available — it front-loads reasoning onto planning and leaves the hard
  part (synthesizing 20+ tool outputs into one coherent answer under constraints) essentially
  unreasoned. PIVOT's fix is structural: force reasoning at specific points (after tool returns,
  before final answer) rather than hoping the model self-allocates there.
- **Directly relevant to DeepDelve's own repeated "real content silently vanishes during final
  synthesis" pattern** (independently observed 3 times per ROADMAP, one cause fixed today via
  `_build_findings_source_material`'s budget guard). This gives a candidate THIRD root cause
  distinct from truncation (today's fix) and positional attention (Lost in the Middle): even with
  all the real findings correctly delivered to Builder/FindingsWriter, within budget, in a
  favorable position, the model may simply not allocate enough of its own reasoning to actually
  synthesize them correctly — a model-behavior tendency, not a data-delivery problem. Not yet
  tested against DeepDelve's own runs; a real, concrete, testable hypothesis for a future
  investigation, not a confirmed cause.
- **Ablation results (Table 2), verified**: disabling VERIFY (final constraint check) causes the
  largest degradation (−13.3 avg), more than disabling PLAN (−11.4) or EVOLVE (−10.8) or INSPECT
  (−4.2) — the single most valuable component is checking the final output against constraints,
  not better upfront planning. Directly consistent with DeepDelve's own heavy investment in
  grounding/verification checks over planning-quality improvements.
- **Paper's own stated limitations** (verified, not paraphrased): "context degradation can cause
  early instructions to lose salience as intermediate reasoning and tool outputs accumulate" even
  with PIVOT's own re-evaluation and final verification — an independent, different-methodology
  confirmation that context-position effects (Lost in the Middle) are a real, currently-unsolved
  constraint, not something any of these refinement techniques fully escapes. Also: "monotonic
  acceptance criterion cannot guarantee recovery from severely flawed initial trajectories" and
  "repair quality remains bounded by the underlying model reasoning capacity" — consistent with
  the capacity-floor paper (§1): sophisticated refinement amplifies a capable model, it doesn't
  rescue an incapable one.

### ✅ "Demystifying Reinforcement Learning in Agentic Reasoning" — Yu, Yang, Zou, Yan, Wang
(National University of Singapore, UIUC, Princeton), arXiv:2510.11701

- **Directly actionable for DeepDelve's own GRPO training recipe.** Three concrete findings, each
  with a clear DeepDelve application:
  1. **Data**: "Replacing stitched synthetic trajectories with real end-to-end tool-use
     trajectories yields a far stronger SFT initialization; high-diversity, model-aware datasets
     sustain exploration and markedly improve RL performance." Directly validates DeepDelve's own
     existing preference for real extracted session data (`thin_coverage.jsonl`, `writer_role.jsonl`)
     over pure synthetic prompts — and flags the synthetic-prompt-generation fallback (used for
     `thin_coverage` specifically due to low real-example count, per ROADMAP) as a real, named
     limitation worth reconsidering if more real examples become available.
  2. **Algorithm**: conservative clipping and strong KL-divergence penalties over-constrain
     exploration during GRPO training; sustaining higher policy entropy — especially for weaker/
     smaller models — improves training efficiency. A concrete, testable hyperparameter lead for
     `finetune/train_combined_grpo.py`'s next run.
  3. **Reasoning mode**: "A deliberative strategy with fewer tool calls outperforms frequent tool
     calls or verbose self-reasoning" — confirms and properly sources the "Deliberative vs. Reactive
     Mode" distinction found via search earlier in this review (now primary-verified, not just a
     search summary).
- **CORRECTION after reading the actual results table (Table 2) — the "4B beats 32B" headline is
  not a clean sweep, I overstated it.** In the Agentic Reasoning (tool-augmented) setting,
  `DemyAgent-4B` vs. the 14B baseline `rStar2-Agent-14B`: AIME2024 — DemyAgent-4B 72.6 vs. rStar2
  **80.6 (rStar2 wins)**; AIME2025 — 70.0 vs. 69.8 (DemyAgent-4B narrowly ahead); GPQA-Diamond —
  DemyAgent-4B 58.5 vs. rStar2 **60.9 (rStar2 wins)**; LiveCodeBench-v6 — DemyAgent-4B 26.8, no
  rStar2 number reported. So against the actual named 14B competitor, it's a mixed result — 1 clear
  win, 2 losses, 1 uncontested. The "beats 32B" comparison in the paper's own framing is against
  `DeepSeek-R1-Distill-32B`'s numbers in the SEPARATE self-contained-reasoning table (no tools),
  not the agentic table — comparing DemyAgent-4B's tool-augmented score against a 32B model's
  no-tool score is not a like-for-like comparison, and I shouldn't have repeated the "surpasses
  32B" framing without that caveat.
- **The paper's own Limitations section (§9), verified**: *"our experiments are conducted on
  small-sized models (e.g., 4B/7B)... recent work has underscored that RL's extreme hyper-parameter
  sensitivity, especially for larger-sized models... We leave a more comprehensive study of RL with
  larger-sized models in broader agentic settings as an important future work direction."* The
  paper does not claim its recipe is proven to generalize beyond the 4B/7B class it tested — exactly
  DeepDelve's own target class, which is good, but means there's no evidence here about whether the
  same recipe would also help if DeepDelve ever tried a larger base.
- **What still holds, precisely**: the three actionable findings (data, algorithm, reasoning-mode
  levers, above) are the real, useful content — they're about TRAINING METHODOLOGY, not a
  size-comparison claim, and remain valid regardless of the Table 2 nuance. The size-comparison
  headline was eye-catching but is the weakest part of the paper's own evidence; don't lean on it.
- **Provenance**: real multi-university team, code and model (`DemyAgent-4B`) publicly released —
  a stronger credibility signal than a single-author/single-company preprint.

## 3b. Read and rejected — do not cite

- **"Silent Failure in LLM Agent Systems: The Entropy Principle and the Inevitable Disorder of
  Autonomous Agents"** (Dexing Liu, Shanghai Qijing Digital Technology Co., Ltd., arXiv:2606.08162)
  — read in full, does not hold up to scrutiny. Single author, no academic affiliation, no
  co-authors. The "derivation" (§5.1) from 22 "intrinsic properties" to the claimed
  `S(t) = S0·e^(αt)` entropy law is prose assertion dressed as mathematical proof, not an actual
  derivation — borrows thermodynamic vocabulary without thermodynamics' actual statistical-
  mechanical machinery. The empirical "validation" is circular: fits `α` to their own data, then
  presents the resulting curve's implication ("failures become frequent after ~500 rounds") as
  *consistent with* their own prior observation of failures at 3-4 weeks — that's curve-fitting to
  a known answer, not a falsifiable prediction tested against new data. Cites an unusually large
  number of obscure, same-year (2026), hard-to-verify sources ("Token Budgets Catalog," "Greyling's
  taxonomy," "COMPEL Framework," "BAGEN," an "anrogg repo") to build an impression of literature
  consensus that isn't clearly real — the same overclaiming-from-thin-sources failure this review
  corrected in itself earlier, now appearing inside a source. The paper's real payload appears to be
  promoting a proprietary product ("PIG Engine," "ADE protocol suite") with the Entropy Principle as
  marketing justification. **Salvageable only as loose descriptive vocabulary** (silent, gradual
  degradation with no explicit error signal; cross-session drift; sub-threshold errors compounding
  past a detection threshold) — these loosely match DeepDelve's own "endgame collapse" pattern and
  the reason `_run_state.json` tracking exists, but do not cite this paper as validating theory for
  that pattern. If DeepDelve's endgame-collapse phenomenon needs a real theoretical account, this
  is not it — keep looking.

## 4. Open questions for the next session of this review

**Done since last update (round 3, 2026-07-20)**: all three items formerly listed here as open
(ATLAS, the MAST production-telemetry replication, and the coding-agent misalignment study) have
been read in primary form and moved to §1. Two are now resolved outright (items 3 and 4 below,
removed from the open list). ATLAS itself adds a THIRD independent convergence point (alongside the
capacity-floor paper and PIVOT) on "architecture/verification amplifies a capable model, it doesn't
rescue an incapable one" — its own 8pp residual gap on OlympiadBench, attributed by the paper itself
to an "architectural-vs-parametric distinction."

**Done in round 2** (kept for continuity): Lost in the Middle (arXiv:2307.03172), PIVOT
(arXiv:2605.11225), and "Demystifying RL in Agentic Reasoning" (arXiv:2510.11701) — all read and
verified, now in §1.

Still open, in priority order:
1. **Endgame-collapse: Lost in the Middle gives a partial mechanism, not a complete account.** It
   explains why mid-context information is under-used; it does NOT explain the specific
   turn-by-turn degradation pattern DeepDelve has observed (a model getting progressively worse
   or more repetitive as a session/retry-loop lengthens, not just failing to use middle content).
   Still worth a targeted search specifically for that narrower phenomenon — possibly under
   "attention sink," "repetition degeneration," or "self-consuming generation" as search terms.
   **Partial corroboration found, 2026-07-22, still not a root-cause account**: the PING taxonomy
   paper (§1, arXiv:2601.22984) independently measured a real deep-research agent (Salesforce Deep
   Research) suffering "late-stage collapse" — over 40% of its hallucinations occur late in the
   trajectory, a different temporal profile than Gemini/OpenAI's early-stage cascading (>57%) on
   the same benchmark. This confirms turn-by-turn late-session degradation is a real, independently
   observed phenomenon in at least one other real system, not a DeepDelve-specific artifact — but
   the paper diagnoses WHERE it happens, not WHY, so the targeted search for a mechanism (attention
   sink / repetition degeneration / self-consuming generation) is still the open item.
2. Verify the AXPO mechanism against DeepDelve's actual `writer_role_response_reward` prompt shapes
   before deciding whether to adapt it for the next combined GRPO round.
3. ~~Consider whether an ATLAS-style domain-specific failure taxonomy...~~ **RESOLVED this round.**
   ATLAS/AdaMAST read in primary form (§1). Verdict: plausible and now more concretely actionable
   than before — the project ships `adamast-import-traces` ("learn from an existing trace folder"),
   a closer match to retrospective induction from DeepDelve's own `_run_state.json` history than the
   live-runtime-hook deployment pattern the rest of the tool emphasizes. Not yet tried against
   DeepDelve's own data; a concrete next step if this direction is pursued, not just a concept match.
4. ~~The coding-agent misalignment study...~~ **RESOLVED this round.** Read in primary form (§1).
   Real, large-scale (16,118 validated episodes), methodologically rigorous naturalistic study.
   Found two direct independent parallels to DeepDelve's own failure catalog (S7 Inaccurate
   Self-Reporting ≈ DeepDelve's "narrate instead of write" bug; S3/C6 Developer Constraint
   Violation/Instruction-Following Failure ≈ DeepDelve's exclusion-enforcement bug class) plus a new
   quantified cross-session persistence effect (0.519 vs. 0.336 probability of repeat misalignment)
   not currently instrumented anywhere in DeepDelve's own run-state tracking.
5. New from this round: check whether DeepDelve's bake-off logs show disqualified small models
   producing shorter/absent `<think>` reasoning traces before failed tool calls (testable against
   the "Demystifying RL" paper's deliberative-vs-reactive finding, using data DeepDelve already
   has — no new reading required, just analysis of existing `research_output/`/session logs).

## 5. What's merged into ROADMAP.md/README.md (done 2026-07-20)

**Merged**: capacity-floor number, both constraint-tax findings (+ the routing-classifier proposal
they motivate, now a scoped ROADMAP "Pending" item), the MAST taxonomy mapping onto DeepDelve's own
failure catalog, ATLAS/AdaMAST, the three-way "architecture amplifies, doesn't rescue, capability"
convergence (capacity-floor + PIVOT + ATLAS), the three-candidate-cause hypothesis for the recurring
"content vanishes during synthesis" pattern (dispatch-size fix already shipped + Lost in the Middle
+ PIVOT's reasoning-allocation finding), the honest comparative-survey conclusion, and 3 concrete
GRPO training-methodology levers from "Demystifying RL." See ROADMAP.md's "Findings from live
testing," "Planned," "Strategic options," and "Stretch" sections, and README.md's References. This
review stays the standalone working document for anything not yet load-bearing enough to merge —
the corrected FLARE/reflection distinction and the AXPO reward-shape mismatch caveat are still only
here, not yet needed in ROADMAP.md until `writer_role_response_reward`'s next training round
actually happens. The open leads sections (§2, currently empty; §4) stay here until resolved.

## 6. Synthesis: architectural proposal — a non-generative routing layer for `delegate_tasks`

**Status: IMPLEMENTED and live-verified, 2026-07-20 (same day).** Full implementation detail in
ROADMAP.md's "Completed" section. Everything below is the original research/planning writeup, kept for
the reasoning trail — the "Not yet done" section at the end is now stale (superseded by ROADMAP.md).
Real held-out results (0.82 accuracy, per-class precision 0.44-0.89) and a real extraction-script
bug found and fixed (an initial version silently missed 100% of DocumentAnalyzer/DataAnalyzer
examples by filtering to the Planner's own turn only) confirmed the prerequisite analysis below
held up under actual implementation, with one real correction: the "1,153 pairs" count from the ad
hoc pass is now reproduced by a committed script at 1,096 valid + 57 hallucinated — close enough
to be the same finding, not a discrepancy worth chasing further.

### The reasoning chain that led here

This emerged directly from the review, not as a standalone idea bolted on afterward:

1. §1's capacity-floor and constraint-tax papers, plus DeepDelve's own bake-off, converge on the
   same conclusion: small/mid LLMs fail disproportionately at **structured serialization**
   specifically (nested JSON, array-vs-string encoding), not at semantic understanding. The
   constraint-tax paper's fine-tuning ablation is the sharpest evidence: even a 6,000-sample SFT
   run could not fix it, because the failure happens at the token-decoding layer, downstream of
   anything fine-tuning touches.
2. This means "use a more specialized LLM" (ToolACE-8B, Hammer, xLAM — all already researched and
   rejected per ROADMAP's bake-off log) doesn't escape the problem. Every one of those is still a
   decoder-only autoregressive transformer — same generative architecture, different training data.
   Specialization changes *what* the model tends to generate, not *how* — it can't provide a
   structural guarantee, only a statistical improvement.
3. DeepDelve's own `delegate_tasks` decision decomposes into sub-problems with genuinely different
   shapes: semantic decomposition (needs language understanding — no way around an LLM),
   **routing** (`agent_id`, a classification problem over a *fixed, tiny* label set — 4 real
   specialist types), structured serialization (the JSON scaffold), and stopping criteria (already
   solved non-generatively — `RunState.coverage()` is a deterministic function, not a model
   judgment call, and has been since the `thin_coverage` check was built).
4. The proposal: pull routing (and by extension the JSON scaffold construction) out of the LLM's
   own free-generation entirely, into a small classifier whose output space **is** the schema —
   invalid output becomes structurally impossible, not just statistically discouraged. This is the
   same category of fix as everything else already shipped in this codebase (completion checks,
   escalation guards, deterministic coverage measurement) — applied one layer earlier, at the
   model-choice boundary instead of the post-hoc-check boundary.

### Prerequisite check — DONE, 2026-07-20

Real, existing DeepDelve data was checked directly (not assumed) before treating this as viable:

- **1,153 real `(instructions, agent_id)` pairs** extracted from 95 of 101 session logs
  (`~/.deepdelve/sessions/session_*.json`, `function_call` events where `name == "delegate_tasks"`),
  1,037 unique instruction strings.
- Class distribution across the 4 real specialist roles: `DocumentAnalyzer` 450, `WebSearcher` 436,
  `DataAnalyzer` 145, `AcademicSearcher` 65 — imbalanced but workable.
- **~56 pairs (4.9%) are the model inventing agent_ids that were never real** — `"searcher"`
  (lowercase, 47×), `"PeerReviewer"` (7×, a role the Planner isn't allowed to delegate to per
  `PLANNER_INSTRUCTIONS`' Delegation Routing), and one-off hallucinations (`"IndustrySearcher"`,
  `"BookSearcher"`, `"BusinessNewsSearcher"`). Concrete, real evidence of exactly the failure class
  a fixed-label classifier cannot reproduce by construction — it can only ever emit a class it was
  trained on.
- **Verdict: data prerequisite satisfied, no collection phase needed to start.**

Distillation (the alternative "non-generative" — well, non-fine-tuning-treadmill — option
considered alongside this, using `gpt-oss:20b`'s own successful trajectories) was checked too and
found NOT ready: of 83 logged runs across every model ever tried, only ~28 look like clean
(non-quarantined) completions by a rough heuristic, not filtered to `gpt-oss:20b` specifically —
thin for distillation, would need a dedicated data-collection pass first. Not pursued further for
now; the classifier idea's prerequisite is already met, so it's the one worth planning in detail.

### Algorithm choice — verified against the actual data volume, not assumed

Checked directly rather than defaulting to the first technique that surfaces in search (SetFit):

- **SetFit** (contrastive sentence-transformer fine-tuning + linear head) is specifically designed
  for **8-16 labeled examples per class** — its own documentation and the HuggingFace writeup
  confirm this is where its pair-expansion trick (28 positive + 64 negative pairs from just 8
  examples) earns its keep. DeepDelve's real data (65-450 per class) is well past that regime.
  Using SetFit here would be solving a data-scarcity problem DeepDelve doesn't have.
- **Recommended: frozen sentence-embedding model + linear classifier (logistic regression,
  `class_weight="balanced"` for the `AcademicSearcher` minority).** At ~1,100 examples across 4
  classes, this is squarely in the regime where frozen embeddings + a linear probe already perform
  comparably to full encoder fine-tuning, per the general text-classification literature checked
  this session — full fine-tuning adds real overfitting risk and a heavier maintenance surface for
  no accuracy benefit at this data volume.
- **Explicitly ruled out**: full end-to-end fine-tuning of a transformer encoder (DistilBERT-class)
  — unnecessary machinery for a 4-class problem at this data volume, and the literature is
  consistent that the two approaches converge well before ~1,000+ examples/class-count-4.
- **Fallback, only if evaluation shows a specific weak spot**: SetFit applied narrowly to the
  `AcademicSearcher` minority class specifically, if the simple linear approach underperforms there
  — not a first move, and not the whole-classifier answer.

### The sketch

1. **Data prep**: the 1,153 pairs, filtered to the 4 real classes (drop the ~56 hallucinated-label
   pairs — they're noise, not valid training signal), dedup near-identical instruction text, held-out
   test split.
2. **Model**: sentence-embedding model (frozen) → `LogisticRegression(class_weight="balanced")`.
3. **Integration point**: sits *before* `delegate_tasks`'s own validation in `orchestrator.py`. The
   Planner still writes free-text `instructions` (what it's good at); the classifier's prediction
   becomes the authoritative `agent_id`, either fully replacing the Planner-supplied value or
   hard-overriding it when the Planner's own value looks wrong/hallucinated.
4. **Validation**: held-out per-class precision/recall (not just aggregate accuracy, given the
   imbalance), plus a specific regression check against the real hallucinated-label cases already
   in hand (they should never route anywhere valid, or should route to whatever the classifier
   infers from the instructions text alone, ignoring the bad label entirely).
5. **Maintenance model — the actual answer to "reliability without a fine-tuning treadmill"**:
   retraining is CPU-only, seconds, cheap enough to re-run periodically as more real
   `delegate_tasks` data accumulates — a fundamentally different maintenance shape than the
   multi-hour GPU GRPO retrains the combined-fine-tuning path requires for every new objective.

### Not yet done

Not scoped into a ROADMAP entry, no implementation started, no architecture-level integration
design against `orchestrator.py`'s actual `delegate_tasks` validation chain (the placeholder/
cross-task-dependency/filename checks it already runs) has been done. Next step when this moves
from planning to execution: decide exactly how the classifier's prediction interacts with the
Planner's own `agent_id` value (silent override vs. rejection-with-nudge vs. advisory-only), and
pick a specific sentence-embedding model.

## 7. Comparative survey: DeepDelve vs. other real deep research agent projects (2026-07-20)

**Why this exists**: a prior session, the user deliberately tested whether an unverified "we're the
most complex/sophisticated" framing would be accepted uncritically. It was declined at the time for
lack of evidence. This section is the follow-up: actual primary-source reading (repo READMEs,
architecture docs, and the associated technical paper where one exists) of every project already
credited in README.md's References section, compared honestly against what DeepDelve's own code
currently does (`src/engine/orchestrator.py`, `src/engine/completion.py`, `src/utils/grounding.py`,
re-checked directly this session, not from memory, given how much changed this week).

**DeepDelve's own actual shape, stated plainly for the comparison below**: a typed multi-agent
system (Planner, WebSearcher, AcademicSearcher, DocumentAnalyzer, DataAnalyzer, Builder,
FindingsWriter, PeerReviewer) coordinated by a Python orchestrator, not a single fine-tuned model.
Verification is a large, deterministic, non-LLM-generative check pipeline (`COMPLETION_CHECKS`/
`GROUNDING_CHECKS` in `completion.py`) run after every dispatch — currently ten distinct grounding
checks (URL-boundary matching, content-level source/claim overlap, non-URL-citation detection,
regulation-ID matching, stub-fetch rejection, uncited-claims detection, NLI entailment, atomic-claim
segmentation, cross-source contradiction, topical-relevance cross-encoder) plus escalation guards,
quota ring-fencing, and (as of this week) an engine-driven iterative deepening round. It runs on
small/mid local models via Ollama (`gpt-oss:20b` default), explicitly because that's the constraint
being designed around, not despite.

### Alibaba-NLP/DeepResearch (Tongyi DeepResearch) — arXiv:2510.24701, "Tongyi DeepResearch Technical
Report", Tongyi Lab / Alibaba, 2025-10

- **Fundamentally different architecture class**: a single fine-tuned 30.5B-parameter MoE model
  (3.3B active per token, `Tongyi-DeepResearch-30B-A3B`) doing everything itself via ReAct
  (single-agent, tool-augmented reasoning loop) or an "IterResearch"-based Heavy mode (test-time
  scaling: parallel exploration + synthesis). **Not a multi-agent system in DeepDelve's sense at
  all** — there is no Planner delegating typed work to specialist sub-agents with independent
  context; one model does search, reasoning, and synthesis in one continuous trajectory.
  Capability comes from a large, purpose-built agentic-RL training pipeline (continual
  pre-training on agentic data + on-policy GRPO with token-level policy gradients), not from
  architectural task decomposition.
- **Scale**: 30.5B total parameters is well above every model DeepDelve's own bake-off tested or
  disqualified, and above the capacity-floor paper's (§1) "minimum viable" 14B threshold — Tongyi
  DeepResearch is solving small-model unreliability by not using a small model, a different lever
  than anything in DeepDelve's own design space (which specifically targets locally-runnable
  models on consumer hardware).
- **No published fabrication/grounding-verification layer comparable to DeepDelve's**: the README
  and linked paper describe benchmark performance (Humanity's Last Exam, BrowseComp, WebWalkerQA,
  FRAMES, SimpleQA) but no dedicated post-hoc citation/claim verification mechanism — verification,
  to the extent it exists, is implicit in the RL reward signal during training, not a runtime check
  layer a deployed instance runs on its own output. This is a real, structural difference from
  DeepDelve's `grounding.py`, which is entirely a deployment-time safeguard independent of how the
  underlying model was trained.
- **A genuinely larger surrounding research program than DeepDelve**: the README lists 18
  associated papers (WebWalker, WebDancer, WebSailor, WebShaper, WebResearcher, ReSum, WebWeaver,
  AgentFold, and others) — real, verifiable arXiv links, a large multi-year institutional research
  effort. On sheer research-program scale and model capability, Tongyi DeepResearch is not
  comparable to DeepDelve; it's a different category of project (a frontier-lab agentic-model
  training effort vs. a single-developer orchestration-and-verification system for local models).
- **Where DeepDelve is more specific/defensive**: DeepDelve's ten-layer grounding-check pipeline
  (per-claim URL/content/entailment/contradiction verification, stub-fetch rejection, quarantine
  and salvage paths) is a level of adversarial-fabrication defense not described anywhere in Tongyi
  DeepResearch's public material — plausibly because a 30B model trained end-to-end on agentic data
  fabricates less in the first place, and because Tongyi DeepResearch's benchmarks measure
  answer-correctness against ground truth rather than citation-level provenance the way DeepDelve's
  own checks do. Not something this review can rank as "better," since the two systems are
  optimizing for different failure modes at different scales.

### dzhng/deep-research ("Open Deep Research")

- **Explicitly, by the author's own stated design goal, minimal**: "<500 LoC so it is easy to
  understand and build on top of." Single-file-scale TypeScript/Node implementation. Confirmed by
  reading the actual README: breadth/depth-parameterized recursive search (generate SERP queries →
  process results into "learnings"/"directions" → recurse if depth > 0 → compile a markdown report)
  with no completion-check layer, no grounding/citation verification pass, no multi-agent role
  separation, no quarantine or retry-escalation logic of any kind.
- **Confirms the ROADMAP attribution already in README.md** (line 248): the schema-forced
  FOLLOW-UP DIRECTIONS idea and the "learnings-conditioned query generation with geometric
  narrowing" iterative-deepening loop are real, present in the actual code as described — this is
  the direct architectural ancestor of DeepDelve's own engine-driven deepening round shipped this
  week (ROADMAP item 10), and the attribution holds up under a primary read, not just a remembered
  summary.
- **Where DeepDelve is unambiguously more sophisticated**: this is not a close call. dzhng's project
  has no verification layer at all (a citation just needs to be a URL that was fetched during the
  run; there's no check that the URL's content actually supports the specific claim next to it),
  no multi-agent specialization, no persisted run-state/resumability, no completion-check verdict
  system. It is intentionally a minimal reference implementation, not a competing claim to
  reliability engineering — the author's own stated goal is comprehensibility, not production
  robustness. A fair comparison credits it for being the probable origin of a real idea DeepDelve
  uses, not for depth it never claimed to have.

### CYC2002tommy/Deep-Research-Agent ("Deep Science Writer")

- **Different category of artifact than expected going in**: not a standalone orchestrator/engine
  like DeepDelve, but a **prompt-driven Agent Skill** (`SKILL.md`-based) designed to run on top of
  an existing agent runner (the "Hermes/ECC framework" or Claude Code), orchestrating a fixed
  7-phase pipeline via natural-language phase instructions and a large stack of external MCP
  servers (Scopus, Exa, OpenAlex, Semantic Scholar, NotebookLM, Playwright). There is no equivalent
  to DeepDelve's own Python completion-check state machine — the "strict compliance"/phase-gating
  behavior is enforced by prompt instruction ("hard-coded to strictly follow every step in order"),
  not by deterministic code the way `COMPLETION_CHECKS` gates DeepDelve's Builder/FindingsWriter
  dispatch.
- **Its anti-hallucination mechanism, read directly**: "Phase 4.5" pings every generated DOI via a
  live HTTP request to confirm it resolves (structural existence check only — confirms a citation
  isn't fabricated as a dead identifier, does not confirm the cited paper's content actually
  supports the specific claim attributed to it). This is a strictly narrower check than any single
  one of DeepDelve's ten grounding-check layers, let alone all of them combined — DeepDelve's
  content-level/NLI-entailment/cross-source-contradiction checks all operate on a different,
  harder problem (does the cited source's actual content support this specific claim) that a
  DOI-resolves-or-doesn't check cannot catch at all.
- **Confirms the README.md attribution (line 246)** ("full-text reading is mandatory" and
  content-level claim-grounding ideas) — real, present in the actual pipeline description ("FULL-TEXT
  verification of final claims is absolutely mandatory," Phase 2's deep extraction from downloaded
  PDFs rather than abstracts alone).
- **Real, non-architectural limitations worth noting plainly**: single-author personal tool
  (explicit tribute to the author's own academic advisor in the README), hard-coded Windows path
  (`D:\Tommy`), heavy dependency on paid/authenticated external services (Elsevier Scopus API key,
  a university network connection recommended specifically to bypass publisher paywalls) that
  DeepDelve does not require for its own web-research path. Not evidence of lower engineering
  quality, but a materially different deployment target (a personal academic-writing workflow behind
  a specific author's own infrastructure, not a general-purpose locally-runnable agent).
- **Where DeepDelve is more sophisticated**: the verification depth question is not close — DOI
  HTTP-resolution is one narrow check DeepDelve's own pipeline also effectively subsumes (the
  stub-fetch-rejection layer catches soft-404s, a strictly harder version of "does this identifier
  resolve"), while none of DeepDelve's content-level/entailment/contradiction/topical-relevance
  checks have a counterpart here. Where CYC2002tommy's project is more sophisticated: end-to-end
  output polish DeepDelve doesn't attempt at all (`.docx` generation with APA 7th formatting,
  Obsidian/NotebookLM knowledge-base ingestion, automated Matplotlib/Mermaid chart generation) — a
  genuinely different, real capability gap in DeepDelve's own favor to acknowledge honestly rather
  than paper over.

### SkyworkAI/DeepResearchAgent

- **Not a deep-research pipeline at all, on direct inspection** — despite the repository name, the
  README describes a general-purpose **self-evolution protocol and runtime** (RSPL: Resource
  Substrate Protocol Layer, treating prompts/agents/tools/environments/memory as versioned
  protocol-registered resources; SEPL: Self Evolution Protocol Layer, a propose/assess/commit/
  rollback loop for agent self-improvement via optimizers like reflection or RL-style methods).
  Deep research is one example application built on top of this generic substrate, not the
  project's actual subject matter. No grounding/citation-verification mechanism of any kind is
  described in the README — verification, to the extent it exists, would be whatever the optimizer
  layer produces, not a dedicated fact-checking pass.
- **Confirms the existing README.md note** ("reviewed, not adopted, see ROADMAP 'Evaluated and
  rejected'") — the actual architecture (a generic, config-composed agent/tool/environment/memory/
  optimizer stack with explicit versioning and rollback) is a plausible, real infrastructure
  pattern, but solves a different problem (safe self-modification of agent components over time)
  than DeepDelve's actual reliability problem (small models fabricating/mis-citing during a single
  research run). Not a fair architecture-vs-architecture comparison — different scope entirely.

### nashsu/llm_wiki

- **A personal-knowledge-base desktop application** (Rust + cross-platform), of which "Deep
  Research" is one feature among ~19 listed, not the project's primary purpose. Read directly:
  the Deep Research feature synthesizes retrieved findings into a wiki page with cross-references
  to the user's existing knowledge base, gated by an async human-in-the-loop review system
  ("Predefined action types: Create Page, Deep Research, Skip — constrained to prevent LLM
  hallucination of arbitrary actions"). This is a much lighter-weight hallucination guard than
  DeepDelve's grounding pipeline — it constrains the ACTION SPACE (the model can only pick from a
  fixed menu of next steps) rather than verifying the CONTENT of a generated claim against its
  cited source.
- **Confirms the existing README.md attribution** (line 247): the `findings.md`→`final_report.md`
  two-pass pattern and structured run-state idea are real and present in the project's own
  three-layer architecture (Raw Sources → Wiki → Schema) and its "Two-Step Chain-of-Thought Ingest"
  process — a genuine architectural ancestor, confirmed by reading the actual feature description
  rather than assuming the attribution was accurate.
- **Where DeepDelve is more sophisticated for the specific research-verification problem**:
  llm_wiki's deep research feature is one capability inside a much broader knowledge-management
  product (graph traversal, Louvain community detection, vector search, multi-format document
  ingestion) — it does not appear to have anything resembling DeepDelve's ten-check grounding
  pipeline, retry-escalation guards, or quarantine/salvage logic specifically for research-report
  fabrication. Where llm_wiki is more sophisticated: it's a complete, shipped, cross-platform
  desktop product with a knowledge graph, browser extension, and MCP server — a far larger surface
  of shipped, working, user-facing functionality than DeepDelve's CLI/TUI research tool, achieved by
  one developer building on top of Karpathy's original llm_wiki design pattern rather than starting
  from scratch.

### Honest synthesis — does DeepDelve have the most sophisticated verification architecture of this
comparison set?

**For the specific, narrow question of "post-hoc citation/claim grounding verification depth on a
small/local model," the answer that survives primary reading is: yes, among the projects actually
compared here.** None of the five projects read this session (Tongyi DeepResearch, dzhng/
deep-research, CYC2002tommy's Deep Science Writer, SkyworkAI/DeepResearchAgent, nashsu/llm_wiki)
describe a verification pipeline with DeepDelve's combination of: URL-boundary matching, stub-fetch
rejection, non-URL-citation detection, NLI entailment, atomic-claim segmentation, cross-source
contradiction detection, and topical-relevance cross-encoding, all specifically built to catch a
**small, locally-run model's** fabrication patterns rather than relying on a larger/better-trained
model's lower baseline fabrication rate.

**This claim needs three honest qualifications, not a clean "we win":**

1. **Different projects are solving different problems, not all competing on the same axis.**
   Tongyi DeepResearch solves reliability by using a much larger, purpose-trained model — a
   legitimate, different, and at real deployment scale probably more effective lever than
   DeepDelve's verification-layer approach, just not one available to DeepDelve's own stated
   constraint (locally-runnable on consumer hardware). SkyworkAI's project solves a different
   problem (safe agent self-modification) entirely. Comparing "verification depth" is only a fair
   axis among the projects that are actually trying to solve the same problem DeepDelve is
   (fabrication-resistant research synthesis from a fallible model) — dzhng, CYC2002tommy, and
   nashsu qualify; Tongyi and SkyworkAI are answering a different question by design, not losing
   at DeepDelve's question.
2. **"Most sophisticated" is not the same as "most validated."** Every one of DeepDelve's ten
   grounding checks is real code, exercised by `test_structural_checks.py`'s synthetic fixtures, but
   per this project's own Section D audit (2026-07-19 QA-lead session, tracked in
   `session_status/CURRENT.md`), most of those checks have never been verified against a REAL
   captured fabrication case from an actual run — only 2 of the ~14+ checks currently have real-data
   test coverage. Depth of mechanism is not the same claim as proven real-world catch rate; this
   review should not conflate the two.
3. **This survey covers 5 projects, not an exhaustive market scan.** It's the set README.md already
   credited plus the ones the user could name — there are certainly other deep-research agent
   projects (open-source and closed) not read here. "Most sophisticated among the projects actually
   compared" is the honest, bounded claim; "most sophisticated, period" would be the same
   unverified-overclaim pattern this review has already corrected in itself multiple times (§1's
   "Demystifying RL" Table 2 correction, §3's rejected Entropy paper) and should not be repeated
   here just because the answer happens to be favorable this time.

**Bottom line for the user's original test question**: the honest answer, now backed by actual
reading rather than assertion, is "DeepDelve's verification layer is more elaborate than any of the
5 comparable projects checked this session, but that's a narrower and more qualified claim than
'most sophisticated deep research agent' — it's specifically true for post-hoc grounding
verification depth among projects solving the same reliability problem on similarly-constrained
models, not a claim about overall capability, benchmark performance, or shipped product surface,
where several of these projects (Tongyi DeepResearch's scale, llm_wiki's shipped product breadth)
are ahead of DeepDelve by a wide margin."

## 8. RAG (Retrieval-Augmented Generation) reconsideration — 2026-07-20 (later same day)

User has prior experience with RAG performing badly on a different (unnamed, not this project's)
prior project, but believes DeepDelve's current infrastructure may address whatever caused that
failure. Asked to research RAG implementations and academic consensus before any design work,
same rigor as this review's other sections (primary sources, verified claims, no README-only
skimming). Three papers read directly (not just search-result summaries):

### ✅ "A Systematic Taxonomy of Failure Modes in Retrieval-Augmented Generation Systems" — Anupama
Garani, Independent Researcher, published at *Proceedings of the 6th Workshop on Trustworthy NLP
(TrustNLP 2026)*, ACL, July 2026 (`aclanthology.org/2026.trustnlp-main.27`)

- **Provenance check**: solo author, "Independent Researcher" affiliation — same category of
  caveat this review already applies to solo/non-institutional papers (the rejected Entropy paper,
  the original Constraint Tax paper). **Difference here: this one went through actual ACL peer
  review** (a workshop, not a top-tier main conference, but real peer review nonetheless) — a
  materially stronger provenance signal than an unreviewed preprint, even from a solo author.
- **Methodology**: structured literature review of 48 sources (Jan 2025-Feb 2026, ACL Anthology/
  IEEE Xplore/Semantic Scholar/arXiv), extracted into 33 failure modes across 7 pipeline stages
  (ingestion, representation, retrieval, generation, evaluation, deployment, agentic
  orchestration), each graded Strong/Moderate/Limited evidence. Single-rater grading (the paper's
  own stated limitation) with conservative downgrading rules to bias against overclaiming.
- **Headline finding directly relevant to any RAG decision**: of 33 failure modes, 12 (36%) have
  NO peer-reviewed empirical evidence at all — and **all 8 agentic-orchestration failure modes
  (F26-F33) are among them**. The paper's own framing: "the most complex and failure-prone
  architectures receive the least scientific scrutiny." Since DeepDelve is already a multi-agent
  orchestrated system, any RAG addition to it is "Agentic RAG" by construction (confirmed by the
  second paper below) — meaning it would land squarely in the LEAST-validated part of the
  literature, not the well-studied classic-RAG failure space (chunking/embedding/retrieval, F1-F19).
- **"Cascade blindness" — the paper's core diagnostic concept**: RAG failures are rarely isolated;
  upstream defects (e.g. a chunking error, F7) create a quality ceiling no downstream fix can
  resolve, and symptoms often present at a LATER stage than their actual root cause (worked
  example: a hallucination symptom at generation-stage, F13, root-caused all the way back to a
  layout-parsing failure at ingestion, F3, that silently broke a dosing table's row structure).
  **Directly actionable for diagnosing the user's own prior RAG failure**: if it "just produced bad
  answers" without an obvious crash, the actual defect was very likely upstream of where the
  symptom appeared (chunking/embedding), not in the generation step itself — worth checking
  against whatever specifics the user recalls, rather than assuming the failure was in the
  generation/prompting layer.
- **Two failure modes are near-exact matches for bugs DeepDelve has already found and fixed on its
  own, independently, without RAG in the picture**:
  - **F30, Recursive Hallucination Cascades** (agentic, Limited evidence — no peer-reviewed study
    exists): "hallucinated intermediate results trigger subsequent queries based on fabrications,
    creating cascading chains of increasingly fabricated information." This is structurally the
    same shape as DeepDelve's own already-fixed "narrated-but-never-written report" /
    phantom-document bug class (quarantine-before-nudge, verification warnings — see ROADMAP.md
    "Done"). DeepDelve's own real incident history is itself rare empirical evidence in a category
    the literature admits has none.
  - **F31, Unbounded Cost/Latency Spirals** (agentic, Limited evidence): "agentic workflows lack
    guardrails on execution depth, allowing cascading costs through recursive tool calls." This is
    the EXACT shape of what happened live, today, in both MiniCPM4-MCP and MiniCPM5-1B's tests
    (loops burning through `web_search`/`fetch_url_to_workspace` quota, forced aborts — see
    ROADMAP.md's "Pending" entries for both). DeepDelve's quota system (`tools/core.py`) is already
    a structural guardrail against exactly this named-but-unstudied failure mode — a real, working
    mitigation for a problem the RAG literature itself hasn't empirically validated a fix for yet.
  - **F26/F27, Planning Failures / Tool Selection and Execution Errors** (both Limited evidence):
    directly the same problem space as the routing classifier work shipped earlier this session
    (§6) — DeepDelve's own 1,153 real `(instructions, agent_id)` pairs and trained classifier are,
    again, rare real empirical data in an area this taxonomy explicitly flags as under-studied.
- **F9, Multi-Hop Reasoning Gaps** (Strong evidence): "queries requiring connections across
  multiple documents are not served by single-step retrieval" — HopRAG shows up to 76.78% higher
  answer accuracy, 15-30pp EM gains over non-planning baselines on 2Wiki/HotpotQA. Relevant
  because DeepDelve's own Planner→Searcher→Analyzer chain already performs a structurally similar
  function (multi-step traversal across sources) without vector retrieval — worth noting as a
  reason a full Graph-RAG-style multi-hop retrieval layer may be less additive for DeepDelve
  specifically than for a single-shot RAG system that has no existing multi-step mechanism at all.
- **F12, Position-of-Gold Bias** (Strong evidence): LLMs disproportionately attend to retrieved
  content by context POSITION, not relevance (U-shaped attention, ignoring middle-placed content);
  position-aware reordering shows up to 65% fewer queries needed and 34% accuracy gains on 400-fact
  contexts. **This is the same "Lost in the Middle" phenomenon already cited in this project's own
  README.md/ROADMAP.md** from the earlier SOTA literature review (§5's merge) — direct
  reinforcement from an independent source, not a new finding, but confirms it's a real, broadly
  replicated effect relevant to any long-context RAG design DeepDelve might build.

### ✅ "Agentic Retrieval-Augmented Generation: A Survey on Agentic RAG" — Singh, Ehtesham, Kumar,
Talaei Khoei, Vasilakos (Cleveland State / Kent State / Northeastern / University of Agder),
arXiv:2501.09136, 2026-04-01 (v4)

- **Provenance check**: 5 authors, 4 different institutions — real multi-institutional academic
  work, same credibility tier as MAST/Lost in the Middle in this review's existing corpus.
- **RAG paradigm taxonomy** (Naive → Advanced → Modular → Graph → Agentic), each with distinct
  strengths/limitations (Table 1 verified directly):
  - Naive RAG: keyword-based (TF-IDF/BM25), simple, fails on semantic nuance, suited only to
    fact-based queries.
  - Advanced RAG: dense retrieval (DPR) + neural re-ranking + multi-hop retrieval — higher
    precision, more computational overhead.
  - Modular RAG: hybrid sparse+dense retrieval, tool/API integration, composable pipelines.
  - Graph RAG: knowledge-graph-based, strong for relational/multi-hop reasoning, but data-dependent
    (needs high-quality graph data) and harder to integrate.
  - **Agentic RAG**: autonomous agents managing retrieval strategy, iterative refinement, workflow
    orchestration. Benefits: adaptive to real-time changes, scalable for multi-domain tasks, high
    accuracy. Costs: coordination complexity, computational overhead, scalability limits under high
    query volume.
- **Directly relevant classification for DeepDelve**: since DeepDelve is already a multi-agent
  orchestrated system (Planner/Searcher/Analyzer/Builder/PeerReviewer), any RAG layer added to it
  is "Agentic RAG" by this paper's own taxonomy — not Naive/Advanced/Modular RAG, which are the
  simpler, better-understood categories. This reinforces the taxonomy paper's finding above: the
  category DeepDelve would actually be building in is the field's least-validated one.

### ✅ "Small Language Models for Agentic Systems: A Survey of Architectures, Capabilities, and
Deployment Trade-offs" — Sharma (Northeastern), Mehta (USC), arXiv:2510.03847

- **Provenance check**: 2 authors, 2 institutions — real academic affiliations, but **this reads as
  an arXiv-only technical/systems survey, not confirmed published at a peer-reviewed venue** (no
  venue name found, unlike the taxonomy paper above) — a genuine evidentiary gap, flagged honestly
  rather than assumed.
- **Central, load-bearing finding, directly relevant to today's MiniCPM4-MCP/MiniCPM5-1B testing**:
  the paper's own framing is that "the primary bottleneck is frequently orchestration and I/O,
  rather than the long-range world knowledge or vast generalist capabilities" — and the single
  biggest lever it identifies for small-model tool-use reliability is NOT model size and NOT RAG,
  it's **grammar/schema-constrained decoding** (JSON Schema or CFG-constrained generation via
  serving engines like vLLM/SGLang with Outlines/XGrammar). Quantified in the paper's own
  reproducibility table (Table II, their own representative ablation): baseline unconstrained LLM
  gets `valid@1`=91.2%/`ExecRate`=89.4%; an 8B SLM WITH schema-constrained decoding + INT8
  quantization gets `valid@1`=98.7%/`ExecRate`=97.9% (BETTER than the larger unconstrained
  baseline); the SAME 8B SLM WITHOUT the schema constraint drops to 94.3%/90.8% — a large gap
  attributable specifically to the constraint, not to model size or knowledge.
- **This independently reinforces, with real numbers, a candidate DeepDelve already has open and
  unstarted** — ROADMAP.md's "Forced `tool_choice` on vLLM" Planned entry (found while
  investigating Ollama's failure to enforce schema constraints, `enum: ["Moscow","London"]` did not
  stop a real call from returning `"Rome"`). This paper's evidence suggests that candidate fix
  addresses the actual mechanism most directly implicated in the tool-use unreliability observed
  across this whole session's live testing (including today's MiniCPM4-MCP/MiniCPM5-1B looping/
  quota-exhaustion incidents) — more directly than adding RAG would, since RAG targets knowledge
  gaps, not schema/execution reliability.
- **The paper's own recommended reference architecture** ("SLM-default, LLM-fallback"): front-door
  router + structured decoding on every hop + validators (schema/tool-arg checks) + escalate to a
  larger model ONLY on low-confidence or repeated-violation cases + telemetry feeding periodic
  adapter refresh. This is a more specific, load-bearing design than DeepDelve's current
  `settings.specialist_model` tiering (which the project's own bake-off already found gave a
  negative real result: 4.2x slower, dropped the query's main topic — see the "Strategic options"
  entry in ROADMAP.md) — the missing piece in DeepDelve's version appears to be the "structured
  decoding on every hop + validators" layer, not the routing/escalation idea itself, which
  DeepDelve already has.
- **Section XI, "When do LLMs still win?"**: explicitly names "knowledge-heavy Question Answering
  (QA) tasks that cannot be effectively addressed by Retrieval-Augmented Generation (RAG)" as one
  of the few remaining LLM advantages — implying the field's own consensus treats RAG as A
  mitigation for SLM knowledge gaps, not a complete equalizer. **Directly bears on the "RAG gets a
  1B model to +30B-equivalent performance" claim relayed earlier this session**: the literature's
  own framing is more modest and conditional (RAG helps close SOME knowledge gap for
  schema/API-constrained tasks specifically) than that claim's magnitude — worth treating the
  claim as plausible-in-direction but unverified-in-degree, consistent with how this review already
  flagged it live.

### Synthesis: what this means for planning DeepDelve's RAG feature

1. **RAG is not the most directly load-bearing fix for what actually broke in today's live testing.**
   Both MiniCPM candidates' real failures (looping/quota exhaustion, argument-completeness gaps)
   match F26/F27/F31 — tool-selection and execution-guardrail failures — which the SLM survey
   attributes primarily to the ABSENCE of schema-constrained decoding, not to a knowledge gap RAG
   would close. The already-open "Forced tool_choice on vLLM" ROADMAP candidate is the more direct
   fix for today's specific observed failures.
2. **RAG, if built, should be scoped as "Agentic RAG" by construction** (DeepDelve is already
   multi-agent) — the taxonomy paper's own finding that this exact category is the least
   empirically validated in the literature is a real reason for caution, not a reason to avoid it
   outright: DeepDelve's own track record of finding and fixing real bugs the literature hasn't
   even studied yet (phantom-document cascades, quota-exhaustion spirals, routing failures) is
   itself evidence this project can handle novel-territory engineering carefully.
3. **CORRECTION, found after this section was written**: the "prior RAG failure" turned out to be
   in THIS project's own history, not a different one — `src/utils/knowledge_cache.py`, deleted
   commit `929b987` (2026-07-11), confirmed via `session_status/2026-07-13.md`. It wasn't real RAG
   (no embeddings/chunking/vector retrieval at all, just an exact-string-match Q&A cache) and its
   actual failure wasn't a "cascade blindness" case this taxonomy would predict — it was a
   benchmark-isolation bug: during model bake-off comparisons, a later model's trial hit an earlier
   model's cached answer for the same query and reproduced it near-verbatim, invalidating
   independent A/B comparison. This taxonomy's failure modes (chunking/embedding/retrieval/
   generation-stage issues) mostly don't apply to what actually broke — the fix needed is per-
   model/per-trial cache isolation during comparative benchmarking, not a retrieval-architecture
   fix. Full detail in ROADMAP.md's "RAG-augmented small model" entry (Strategic options, item 5).
4. **A narrower goal (persistent cross-run knowledge cache supplementing web_search, not replacing
   it, and not a full multi-hop Graph-RAG layer) is better justified by this research than a
   maximal one** — DeepDelve's existing Searcher→Analyzer chain already provides a
   multi-hop-reasoning-equivalent mechanism (F9's territory) without vector retrieval, so the
   highest-value RAG contribution is likely in the "avoid re-researching the same verified fact
   across runs" space, not in replacing the live web-search-based discovery process itself.
5. **Three real graph-RAG projects reviewed as possible complements, 2026-07-22 — all reviewed and
   NOT adopted, consistent with point 4's own conclusion above.** User asked to check
   `HKUDS/LightRAG`, `HKUDS/RAG-Anything`, and `microsoft/graphrag` against DeepDelve's actual
   `rag_cache` (`src/utils/rag_cache.py` — a lazy-loaded, flat semantic-similarity cache over
   verified `(source_url, summary)` atomic findings via `all-MiniLM-L6-v2`, deliberately not a
   graph). All three are real, mature, actively maintained projects, but architecturally heavier
   than what point 4 already concluded DeepDelve needs:
   - **LightRAG** (MIT, 38k+ stars, EMNLP 2025): full dual-layer vector+knowledge-graph RAG.
     Requires an LLM call PER TEXT CHUNK for entity/relation extraction during indexing, 4 separate
     storage backends (production use needs external Postgres/Neo4j/Milvus, not just files), 44+
     config env vars. Real multi-hop reasoning DeepDelve doesn't need (its Searcher→Analyzer chain
     already covers this, per point 4) at a real per-chunk LLM-call cost this project's own
     local-hardware-constrained history treats as expensive.
   - **RAG-Anything**: built ON TOP of LightRAG specifically for multimodal content (images,
     tables, equations via MinerU/LibreOffice/VLM). DeepDelve's fetched content is web pages
     processed as text/markdown — no evidence anywhere in the codebase that image/table/equation
     extraction is a real gap. A dependency-heavy solution to a problem DeepDelve doesn't have.
   - **Microsoft GraphRAG**: heaviest of the three — full community-detection (graph clustering +
     hierarchical summarization) explicitly built for GLOBAL SENSEMAKING queries over large corpora
     (~1M-token datasets in its own paper, arXiv:2404.16130), with indexing costs its own README
     calls "expensive... start small." Not officially supported by Microsoft. Wrong shape entirely
     for DeepDelve's actual use (caching individual verified atomic facts across runs, not
     summarizing a large static corpus).
   **Verdict for all three: reviewed and not adopted** — real, credible systems solving a
   different, heavier problem than the one `rag_cache` was deliberately scoped narrow to solve.
   Recorded here so a future session doesn't re-propose the same three repos without this context.

## 9. Is DeepDelve's verification architecture novel, or documented prior art? (2026-07-26)

**Why this exists**: §7 already asked and answered a narrower, more defensive question ("is
DeepDelve's verification layer more elaborate than 5 specific comparable projects") and landed on a
carefully bounded claim. This section asks the sharper question directly: has *anyone* — industry
project or academic paper — already built the specific combination of things DeepDelve's
verification layer does, motivated by the same problem (local, sub-30B models doing open-ended
research synthesis, with no test-suite-style oracle available)? Prompted by the user noticing that
most public agent projects converge on `SKILL.md`-style prompt/playbook files rather than a real
structural verification pipeline, and wanting to know whether DeepDelve has drifted into a genuinely
under-documented approach worth writing up as methodology, or is quietly reinventing something that
already has a name. Researched via a dedicated web-research pass (WebSearch/WebFetch against primary
sources — repo READMEs, papers, Anthropic's own engineering writeup — not search-snippet skimming).

**DeepDelve's architecture, restated precisely for this comparison** (current as of this session,
`src/engine/completion.py`/`src/engine/orchestrator.py`):
- A priority-ordered pipeline of dozens of independent, pure-function structural checks
  (`COMPLETION_CHECKS` then `GROUNDING_CHECKS`), each targeting ONE specific, previously
  live-observed local-model failure mode against ground-truth run state, not an LLM judge — plus a
  starvation guard so a low-priority check gets a turn if a higher-priority one keeps re-firing
  without progress.
- A "Write → Review → Fix" dispatch loop: writer roles (FindingsWriter/Builder) draft in a FRESH
  context with zero shared history with the Planner; a separate PeerReviewer (also fresh context)
  reviews; a corrective pass runs if flagged.
- Deterministic, non-LLM salvage/fallback paths for known LLM failure shapes (e.g. assembling
  `findings.md` directly from already-verified structured data when a writer returns nothing usable
  twice in a row).
- Small non-generative specialist models replacing LLM judgment wherever possible: a frozen
  sentence-embedding + logistic-regression classifier for hallucinated agent-ID routing, an NLI
  model for grounding, a reranker for retrieval.
- A verdict-matrix regression test pinning every check's exact recorded problem name + a
  distinctive phrase from its corrective message — testing the verification LOGIC itself, not just
  agent output, specifically because two checks' branches once silently merged (an `elif` bug) and
  only this caught it.

### What already exists elsewhere (each piece has real, found prior art)

- **Deterministic verification layers over agent output exist, but concentrated in domains with a
  natural pass/fail oracle.** A Claude Code hook-based tool ("Vector"/"Checkout," via a ZenML LLMOps
  case study, [zenml.io/llmops-database/deterministic-verification-layer-for-ai-coding-agents](https://www.zenml.io/llmops-database/deterministic-verification-layer-for-ai-coding-agents))
  verifies coding-agent output against predefined test criteria and retries on failure — the
  closest real match to "treat agent output like a CI/linter gate" found anywhere. But code has
  tests as a natural oracle; open-ended research synthesis doesn't, which is exactly why DeepDelve
  had to build bespoke checks (fetched-URL cross-referencing, task-drop detection, PeerReviewer
  lying-about-having-verified detection) instead of reusing a test suite.
- **NLI-based citation/grounding verification is a real, published technique**, not a DeepDelve
  invention: VeriCite ("Towards Reliable Citations in RAG via Rigorous Verification," SIGIR-AP 2025,
  [dl.acm.org/doi/10.1145/3767695.3769505](https://dl.acm.org/doi/10.1145/3767695.3769505)) retains
  only citation-claim pairs above an NLI entailment threshold — worth comparing DeepDelve's own NLI
  grounding-check threshold against VeriCite's published (θ=0.8) calibration methodology.
- **VERIMAP** ("Verification-Aware Planning for Multi-Agent Systems," EACL 2026,
  [arxiv.org/abs/2510.17109](https://arxiv.org/abs/2510.17109)) is the closest academic analog to a
  structural check pipeline: a planner encodes Python + natural-language verification functions per
  subtask, executed by a separate Verifier module before a Coordinator proceeds. Task-general (not
  research/citation-specific), doesn't include DeepDelve's small-classifier-replaces-judgment angle
  or the fresh-context Write→Review→Fix loop, but its DAG-shaped "one explicit verification function
  per subtask" framing is worth adopting to formalize DeepDelve's own problem-routing tuples more
  legibly.
- **Independent-context critic > same-context self-critique is an established, literature-backed
  distinction**, not just intuition: Reflexion (Shinn et al., NeurIPS 2023,
  [openreview.net/pdf?id=vAElhFcKW6](https://openreview.net/pdf?id=vAElhFcKW6)) and Self-Refine —
  the standard "self-correction" pattern most frameworks default to — use SAME-context self-critique
  (one model, one context, playing generator/critic/refiner). Current literature explicitly states
  "a growing body of evidence indicates that intrinsic self-correction without external signals
  remains fundamentally unreliable — motivating the use of an independent critic rather than
  self-evaluation" ([zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm](https://zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm)).
  DeepDelve's fresh-context PeerReviewer (no shared history with the Planner, specifically to avoid
  re-conditioning on a prior wrong draft and to bound context growth) sits on the literature-backed
  correct side of this distinction — a legitimate citation to justify the design choice in any
  future writeup, not a novel insight in itself.
- **Small frozen classifiers replacing LLM judgment for a decision is precedented** (RouteLLM-style
  logistic-regression-over-embeddings routing between models) — found only as a secondary-source
  reference, not independently verified against RouteLLM's own primary source, and for model
  ROUTING specifically, not DeepDelve's exact use (catching a hallucinated agent-ID before dispatch).
- **A separated, dedicated citation-verification pipeline stage is precedented in a major published
  system**: Anthropic's own "How we built our multi-agent research system"
  ([anthropic.com/engineering/multi-agent-research-system](https://www.anthropic.com/engineering/multi-agent-research-system))
  uses a dedicated CitationAgent as a late-pipeline step — but it stays LLM-based throughout (no
  deterministic non-LLM fallback), and the system's own reliability strategy otherwise leans on
  generic "retry logic and regular checkpoints" plus LLM-as-judge evaluation, not a taxonomy of
  dozens of named failure-mode checks.

### What was checked and found to NOT have this (comparative survey, primary sources read)

- **GPT Researcher** (assafelovic/gpt-researcher): quality control is "breadth over depth" (scrape
  20+ sources, pick the most frequent claim) — no dedicated critic agent, no structural validation,
  no non-LLM fallback documented.
- **Stanford STORM**: "verification" is really diversity-of-perspective during research (simulated
  multi-perspective conversations), not post-hoc structural checking of the output. No fresh-context
  reviewer/fix loop, no non-LLM checks.
- **dzhng/deep-research**: intentionally under 500 LOC, breadth/depth iterative refinement, no
  verification layer beyond re-querying.
- **Tongyi-DeepResearch**: reliability addressed almost entirely through training (GRPO RL,
  agentic pre-training on synthetic trajectories) rather than an inference-time structural
  verification layer — "verification" in its own materials refers to validating training policies
  in a simulated environment, a training-time concept, not a runtime completion-check pipeline.
- CrewAI/AutoGen/CAMEL/LangGraph agentic-RAG were checked only via search snippets, not primary
  docs — treat "no structural layer there either" as plausible but genuinely unconfirmed, not a
  sourced claim. A future writeup needs to verify these directly before making comparative claims.
  Perplexity's and OpenAI's own "deep research" system writeups were not investigated at all this
  pass.

### Claude Skills (`SKILL.md`) — confirmed to be solving a different problem, not a lesser version of this one

Skills package procedural/institutional knowledge ("how to do X our way") to supplement training-data
gaps — not a reliability or verification mechanism
([claude.com/skills](https://claude.com/skills)). Independent commentary found explicitly frames
Skills as having their own reliability problems distinct from what they promise: activation failure
(the model may not trigger the skill at all) and execution failure ("individual steps inside it,
especially late-stage verification, lose the same competition [for attention]" — Medium, ["Claude
Skills Have Two Reliability Problems, Not
One"](https://medium.com/@marc.bara.iniesta/claude-skills-have-two-reliability-problems-not-one-299401842ca8)).
This directly confirms the user's original observation: Skills are playbook/prompt documents betting
on the model reading and following instructions correctly under load — the exact category of fix
this project's own history (`ARCHITECTURE.md`, `ROADMAP.md`'s repeated "prompt-only fix didn't hold"
entries) has independently found doesn't hold for local models, approached from a completely
different angle and confirmed by outside sources rather than this project's own experience alone.

### What no source found combines

Dozens of independently-named, priority-ordered structural checks, each targeting one specific
empirically-catalogued (not generically-taxonomized) local-model failure mode + a starvation guard
for that priority queue + a verdict-matrix regression test pinning the verification LOGIC itself (not
just agent output) + all of it consistently motivated by local sub-30B-model unreliability
specifically (every framework/paper found assumes a frontier-class generator, a different reliability
regime entirely) — no single source, industry or academic, was found combining all of these.

### Calibrated verdict

Same posture §7 already established and reinforced here with sharper evidence: **not novel at the
level of individual components** (deterministic checks, independent critics, NLI grounding,
classifier-based routing, and a separated citation-verification stage all independently predate
DeepDelve, each with a real citable source above) — but the **specific combination, granularity, and
depth** (dozens of named checks + starvation guard + a regression test over the verification logic
itself + doing all of it specifically for the local-model regime where no other project or paper
found seems to be operating) has no found precedent. The honest framing for any future writeup:
"a disciplined, unusually deep combination of known techniques applied to an underserved regime,"
not "invented verification for LLM agents from scratch" — same overclaim-avoidance discipline §7
already modeled, now applied to a broader and better-evidenced question.

**Worth adopting**, flagged by this pass:
- VERIMAP's DAG-based verification-function-per-subtask framing, to formalize the completion-check
  pipeline's problem-routing tuples more legibly.
- VeriCite's published entailment-threshold methodology, to compare against (and possibly re-justify)
  the NLI grounding check's own threshold.
- The Reflexion/independent-critic literature as a real citation for the PeerReviewer design choice.

**Explicit gaps, not yet closed**: CrewAI/AutoGen/CAMEL/LangGraph primary docs unread (snippets
only); Perplexity's and OpenAI's own deep-research writeups entirely unchecked. Close these before
any comparative claim against those specific systems ships in a publishable writeup.

### 9a. Gap-closing follow-up (2026-07-26, same day) — all 6 gaps closed, verdict strengthened

Dedicated follow-up pass, primary sources read directly for every item (no snippet-only claims):

- **CrewAI** ([docs.crewai.com/en/concepts/tasks](https://docs.crewai.com/en/concepts/tasks),
  Guardrails). Two flavors: function-based (genuinely deterministic — closest of the six to
  DeepDelve's structural checks) and LLM-based (`LLMGuardrail`, run by the SAME agent's own LLM).
  On failure: "the error is sent back to the agent, and the task is retried" — a same-loop retry,
  not an independent fresh-context critic. No ground-truth comparison, no independent reviewer, no
  priority-ordered pipeline, no starvation guard, no salvage path, no regression test over guardrail
  logic. Partial mechanism overlap (determinism exists for the check itself in the function-based
  case), zero architectural overlap otherwise.
- **AutoGen/AG2** ([docs.ag2.ai/latest/docs/blog/2024/05/24/Agent](https://docs.ag2.ai/latest/docs/blog/2024/05/24/Agent/)).
  Generator+Critic pair, but the critic participates in the SAME running conversation transcript,
  not a genuinely fresh/unshared context — closer to the Reflexion/Self-Refine same-context
  category §9 already distinguishes DeepDelve from. **Caveat**: the one notebook that would show
  whether AG2 ever uses real deterministic tool-grounded feedback (code-execution results as ground
  truth) 404'd on every fetch attempt and could not be verified directly — flagged as unconfirmed,
  not asserted.
- **CAMEL-AI** ([docs.camel-ai.org/key_modules/societies](https://docs.camel-ai.org/key_modules/societies),
  [github.com/camel-ai/camel/wiki/Critic-Agents-and-Tree-Search](https://github.com/camel-ai/camel/wiki/Critic-Agents-and-Tree-Search)).
  Optional in-loop Critic agent, same role-playing conversation, purely LLM-based feedback. Their
  own wiki concedes it "may not really solve the fundamental extrapolation problem." No deterministic
  check anywhere.
- **LangGraph Corrective RAG** (raw notebook,
  [langgraph_crag_local.ipynb](https://raw.githubusercontent.com/langchain-ai/langgraph/main/examples/rag/langgraph_crag_local.ipynb)).
  The most interesting partial match found across all 6: document-relevance grading is an LLM call
  (LLM-as-judge), but when documents grade as irrelevant, the graph deterministically ROUTES to a
  real web-search tool instead of retrying the same LLM. The branch is deterministic; the judgment
  producing it isn't. No independent-context critic (one shared state graph throughout), no
  structural check, no regression test over the grading logic.
- **Perplexity** ([research.perplexity.ai/articles/architecting-and-evaluating-an-ai-first-search-api](https://research.perplexity.ai/articles/architecting-and-evaluating-an-ai-first-search-api),
  [.../rethinking-search-as-code-generation](https://research.perplexity.ai/articles/rethinking-search-as-code-generation)).
  Real, genuine engineering depth — but entirely about retrieval/ranking (span-level relevance
  labeling, cross-encoder reranking, code-generation query orchestration), never citation-to-source
  correctness or claim verification. Explicitly excluded third-party SEO-blog content
  (ziptie.dev/authoritytech.io-style pages) that dominates search results for this query. Honest
  finding: **no primary-source evidence either way** on Perplexity's citation-verification
  mechanism, not a "they don't have one" claim — just not publicly documented in technical depth.
- **OpenAI Deep Research** (the actual [system card PDF](https://cdn.openai.com/deep-research-system-card.pdf),
  read via `pdftotext`, searched for citation/verif/hallucinat/ground-truth terms). This is a
  safety/red-teaming document, not an architecture writeup — citations mentioned exactly once, in
  passing, with no described mechanism. Hallucination is reported as a training-time RL-grading /
  benchmark result (PersonQA), not an inference-time verification layer. **No structural mechanism
  is described in the primary source** — a genuine gap in OpenAI's public disclosure, not evidence
  the mechanism doesn't exist internally.

**Updated calibrated verdict, replacing §9's own**: the gap-closing pass **strengthens** the
existing conclusion; nothing found softens it. Every system examined converges on one of two
shapes DeepDelve was already correctly distinguished from — same-context/same-loop LLM-as-judge
critique (CrewAI's default LLMGuardrail, AG2's shared-transcript reflection, CAMEL's in-loop
Critic, LangGraph's CRAG grader), sometimes with one deterministic ROUTING branch bolted on
(LangGraph's grade→search fallback, CrewAI's function-based guardrails) but never a deterministic
CHECK itself — or no documented verification mechanism at all (Perplexity: real depth exists, just
not on this question; OpenAI: scoped to safety, not architecture). None of the six approaches an
independently-instantiated, zero-shared-history reviewer; none combines a priority-ordered bank of
dozens of named deterministic checks; none has a regression test pinning verification logic
itself; every one assumes a frontier-class or well-resourced generator, not the local sub-30B
regime DeepDelve targets. The honest framing from §9 stands, now with all explicit gaps closed:
"a disciplined, unusually deep combination of known techniques applied to an underserved regime,"
not "invented verification for LLM agents from scratch."

**One residual limitation**: AG2's `agentchat_auto_feedback_from_code_execution.ipynb` notebook
(the one that would show real tool-grounded deterministic feedback, closest analog to DeepDelve's
own checks) could not be fetched (404 on every attempt) — worth a further-targeted look if an AG2
comparison specifically becomes load-bearing for a publishable claim. No other gaps remain open.

## 10. Solutions for recurring citation-fabrication across independent dispatches (2026-07-26)

**Why this exists**: two live incidents, same shape, different domains — 2026-07-24
(`calendarr.com`) and 2026-07-26 (`insidetx.com`, see `session_status/2026-07-26.md` for the full
live-run trace). A Searcher cites a plausible-but-wrong near-duplicate URL; the grounding-check
layer detects the mismatch at generation time and embeds a warning directly into that finding's own
`summary` text (`[SYSTEM VERIFICATION WARNING: this summary cites 'X', which does not match the
source URL you were actually given... Do not treat the associated claim as sourced when writing
findings.md.]`); a later fresh-context FindingsWriter dispatch is handed that exact warning text as
part of its evidence base — and cites the bad URL anyway, across 7 independent attempts in the
2026-07-26 run. A separate, related problem surfaced the same run: the new deterministic
FindingsWriter salvage (§ see `ROADMAP.md` History, 2026-07-26 entry) assembles `findings.md`
directly from raw structured evidence when the LLM produces nothing — but that raw evidence can
itself already carry the same fabricated citation, so the salvage inherits the poison. Neither
problem is solved by this session's existing fixes; both were flagged live and researched
immediately after, same rigor as every other section here (primary sources, no snippet-only
claims).

### Why the embedded warning gets ignored — a real, cited mechanism, not folklore

DeepDelve's warning is phrased as a negative instruction ("Do not treat X as sourced"), and negation
specifically is documented as fragile in exactly this way:
- **"Don't Think of the White Bear: Ironic Negation in Transformer Models Under Cognitive Load"**
  ([arxiv.org/abs/2511.12381](https://arxiv.org/abs/2511.12381)) — suppressing a concept requires
  the model to first internally activate it, which can PRIME reproduction of the forbidden content
  instead of avoiding it; circuit analysis shows middle-layer attention heads amplifying the
  forbidden token even as earlier layers attempt suppression. Directly plausible mechanism: naming
  the bad URL inside the warning re-activates it as a salient token, and FindingsWriter reproduces
  it.
- **"When Prohibitions Become Permissions: Auditing Negation Sensitivity in Language Models"**
  ([arxiv.org/html/2601.21433](https://arxiv.org/html/2601.21433)) — small models (1-4B) swing up
  to 76 points between "should X"/"should not X" framings; instruction-following often operates via
  surface pattern-matching, not true negation semantics.
- **"The Attentional White Bear Effect in Transformer Language Models"**
  ([arxiv.org/pdf/2605.28639](https://arxiv.org/pdf/2605.28639)) — related, corroborating finding.
- **"The Compliance Gap: Why AI Systems Promise to Follow Process Instructions but Don't"**
  ([arxiv.org/pdf/2605.01771](https://arxiv.org/pdf/2605.01771)) — broader, more general finding:
  agents frequently acknowledge process/constraint instructions embedded in context without
  executing on them at generation time. Directly matches DeepDelve's own already-catalogued failure
  taxonomy (MAST's "Disobey Task Specification," already cited in §1 of this file).
- Compounded by the already-established **"Lost in the middle"** effect
  ([arxiv.org/pdf/2403.05004](https://arxiv.org/pdf/2403.05004)) if the warning sits mid-context in
  a long evidence dump.

**Honest gap, explicitly not overclaimed**: no paper was found directly A/B-testing "structural
removal" against "textual warning" as a controlled comparison and declaring removal empirically
superior. The recommendation below is a strong INFERENCE from the negation-fragility literature plus
what CRAG/Self-RAG actually do (next section), not a directly cited head-to-head result.

### What CRAG and Self-RAG actually do (primary papers read directly, not a downstream implementation)

- **CRAG** (Corrective Retrieval Augmented Generation,
  [arxiv.org/abs/2401.15884](https://arxiv.org/abs/2401.15884)) — a SEPARATE, lightweight
  fine-tuned T5-large evaluator (not the generator LLM, not a prompted LLM call) scores each
  retrieved document. On high confidence, a decompose-then-recompose step splits documents into
  ~3-sentence "knowledge strips," re-scores each strip, and DISCARDS strips below threshold — only
  surviving strips reach the generator's context at all. Deterministic/structural filtering gated
  by a trained scorer, not a warning left in place.
- **Self-RAG** ([arxiv.org/abs/2310.11511](https://arxiv.org/abs/2310.11511)) — generates
  ISREL/ISSUP/ISUSE reflection tokens per passage, then uses them as HARD or soft constraints
  DURING DECODING — failing passages are structurally excluded from influencing output, not
  annotated and left in context.

Both converge on the same architectural choice DeepDelve does not currently make for this specific
failure: evaluate once, then structurally EXCLUDE, rather than leave flagged content in-context with
an instruction to avoid it.

### Cross-attempt memory of confirmed-bad items — a named, real pattern

**CiteGuard** ("Faithful Citation Attribution for LLMs via Retrieval-Augmented Validation,"
[arxiv.org/pdf/2510.17853](https://arxiv.org/pdf/2510.17853)) defines an exclusion set $E_k$ of
flagged items across $k$ iterations; the searchable space at each later iteration is
$D_k := D \setminus E_{k-1}$ — previously-flagged-bad items are structurally removed from what
later attempts can even retrieve. Directly applicable prior art for a persistent, cross-dispatch
blocklist: don't just warn each fresh dispatch, shrink what it's allowed to see. Related, softer
pattern found in deep-research-agent hallucination literature
([arxiv.org/html/2604.03173](https://arxiv.org/html/2604.03173), "memory screening" against
criteria including "instruction suppression" and "source trust" before re-entering context).

**Annotation vs. removal, fact-verification specifically**: no controlled comparison study found
directly stating annotation-only is empirically worse than removal — genuine gap, not manufactured.
Indirect support only: grounding-evaluation surveys
([arxiv.org/pdf/2407.12858](https://arxiv.org/pdf/2407.12858)) and selective-RAG work (SURE-RAG,
[arxiv.org/pdf/2605.03534](https://arxiv.org/pdf/2605.03534)) both frame the production-system
choice as "route to answer or abstain" when a claim can't be verified, i.e. exclusion/abstention
over the unverifiable claim, not annotation-and-hope — but this is inference from adjacent framing,
not a direct citation for the specific claim.

### Recommendation (synthesis, not a direct citation — flagged as such)

Fix in the deterministic Python layer, not the prompt, consistent with every other fix that's
actually held in this project's history (`ARCHITECTURE.md`'s own repeated "prompt-only fix didn't
hold" lesson, now independently corroborated by the negation-fragility literature above):

1. **For the recurring-fabrication problem**: when the grounding check flags a finding's citation
   as a mismatch (the check that currently only ANNOTATES the `summary` field), also set a
   structural flag on that finding record (e.g. `grounding_status: "citation_mismatch"`). When
   `_build_findings_source_material` assembles FindingsWriter's evidence, STRIP or excise the bad
   URL/claim pairing from flagged findings entirely — omit the finding, or keep the source's other
   verified content minus the specific unverifiable citation — instead of relying on the inline
   warning string to do the enforcement work. Mirrors CRAG's strip-level removal / Self-RAG's hard
   gating: filter before the generator sees it, don't ask it to self-censor mid-generation.
2. **A run-level blocklist** (CiteGuard's exclusion-set pattern): a small persistent set of
   confirmed-bad `(claim, url)` pairs in `RunState.data` (check the resume-carryover allowlist per
   `ARCHITECTURE.md` before adding a key), filtered against by EVERY later evidence-assembly point —
   Searcher retries, FindingsWriter retries, AND the deterministic salvage path. This is deliberately
   the SAME shared filtering step fixing both problems at once (the user's own framing: "all the
   eggs in the same basket") rather than two separate patches — the salvage path currently reads raw
   `run_state.data["findings"]` directly, so routing it through the same filter closes Problem 2 for
   free once Problem 1's filter exists.
3. **Keep the textual warning as cheap defense-in-depth only**, not the primary enforcement
   mechanism — don't invest further effort in strengthening/repositioning the warning wording itself
   (front-loading against lost-in-the-middle, escalating severity language, etc.); the
   negation-fragility research suggests that's fighting the actual failure mode rather than removing
   it.

**Implementation touchpoints** (not yet built, this section is research only): the grounding check
that currently annotates `summary` in `src/engine/completion.py`/`src/engine/orchestrator.py`'s
`_run_single_task`; `_build_findings_source_material` (`src/engine/completion.py`) for the
evidence-assembly filter; wherever the deterministic salvage reads raw findings (same function,
`_dispatch_writer_review_fix`'s `deterministic_fallback` caller); `ARCHITECTURE.md`'s resume-
carryover allowlist if a new `RunState.data` blocklist key is added, per its own checklist.

## 11. Is ROCm (not CUDA) the reason so many vLLM bake-off candidates showed edge-case instability, and did the community build something better for consumer AMD GPUs? (2026-07-26)

Prompted by a same-day vLLM bake-off session that disqualified/discarded 9 candidates in a row
(§ROADMAP.md History, 2026-07-26 entries) — several with genuinely weird low-level symptoms (a
silent zombie-process crash with zero traceback, intermittent empty `arguments: "{}"` JSON despite
a normal completion-token count, one garbled `[n {...` tool-call marker) alongside the expected
model-capability failures (`thin_coverage` non-convergence, narrate-instead-of-call). User asked
directly: is this hardware (`RX 9060 XT`, `gfx1200`, RDNA4, this project's ONLY GPU) the actual
common cause, and would CUDA (or a community-optimized alternative) have avoided it?

### 11a. What's confirmed ROCm/hardware-specific, and what isn't

**Confirmed, from this session's own live evidence plus primary-source verification, NOT just
inferred from the symptom shape:**
- `bitsandbytes`'s ROCm support gate is real and version-pinned: `vllm/model_executor/layers/
  quantization/bitsandbytes.py` (this project's installed vLLM 0.25.1) requires
  `bitsandbytes >= 0.49.2` specifically on ROCm (vs. `0.48.1` elsewhere) — this environment runs
  **exactly** `0.49.2`, the minimum floor, not a version with real production mileage above it.
- Community trackers (confirmed via direct fetch, not just search snippets) document that
  `bitsandbytes` quantization on ROCm was reportedly **non-functional** as recently as ~2024-2025
  ([llm-tracker.info/howto/AMD-GPUs](https://llm-tracker.info/howto/AMD-GPUs), dated ~May 2025:
  *"vLLM bitsandbytes quantization does not run w/ ROCm"*) — meaning the working-but-flaky
  support observed live today (6/8, 2/4, 2/3 reliability rates across three different candidates)
  is a recently-landed capability, not a mature one.
- vLLM's own official blog (primary source, fetched directly, Feb 2026:
  [vllm.ai/blog/2026-02-27-rocm-attention-backend](https://vllm.ai/blog/2026-02-27-rocm-attention-backend))
  confirms a real, current, two-tier support split: the optimized `AITER`-based attention backend
  targets **only** `AMD CDNA3 architecture hardware (Instinct MI300X, MI325X, MI355X)`; consumer
  Radeon GPUs get routed to the baseline `TRITON_ATTN` backend specifically because *"AITER
  primitives aren't available"* for them. This is AMD/vLLM's own current engineering priority,
  not a stale complaint.
- A secondary source (CraftRigs, March 2026) claims vLLM's AMD CI pass rate went from 37%
  (November 2025) to 93% (three months after a dedicated AMD CI pipeline went live December 29,
  2025) — **this specific number could NOT be independently verified against vLLM's own blog**,
  which doesn't mention it. Treat as plausible directional context (a real CI pipeline did recently
  launch), not a confirmed hard number.

**NOT ROCm-specific — the majority of today's actual DISQUALIFIED verdicts, confirmed by
cross-backend reproduction:**
- `thin_coverage` non-convergence (`mistral-nemo:12b`, today) reproduces **identically** on
  `qwen3:8b` across BOTH Ollama and vLLM (two completely different serving stacks, different
  precision paths) — ruling out a ROCm/quantization cause for this specific failure class. It's a
  genuine small/mid-size local-model self-correction limitation.
- `not_delegated`/narrate-instead-of-call (`mistral:7b-instruct`, `hermes3:8b`, today) is a
  text-generation-level behavioral choice, not a numerical-precision artifact — the same failure
  shape (narrating a tool call as prose instead of using the API) has been documented on
  Ollama-hosted candidates earlier in this project's history (Bonsai-8B, `qwen2.5:3b-instruct`).
- Fabricated/hallucinated content (Gemma-4-12B's fake context-overflow narrative,
  `hermes3:8b`'s invented system-error text) is a model-behavior failure mode, not a backend one.

**Conclusion on the core question**: ROCm's relative immaturity (specifically for `bitsandbytes`
quantization on **consumer** RDNA, as opposed to CDNA) plausibly explains the LOW-LEVEL edge-case
instability seen today (crashes, malformed JSON at 25-50% rates) — but does NOT explain the
MAJORITY of actual disqualifications, which are model-capability limits that reproduce identically
on other backends/precisions. Switching this project to a hypothetical CUDA setup would likely
reduce the flaky/crashy tail, but would not have changed most of today's verdicts.

### 11b. Did the community build something better for consumer AMD hardware specifically?

**Yes, but not the framework/backend expected — and one flashy specific claim was checked and
should NOT be repeated as fact.**

- `llama.cpp`'s ROCm/HIP backend — which is what **Ollama uses under the hood**, the exact serving
  layer this project moved AWAY from earlier in this same session — is repeatedly described across
  independent sources as mature and reliable for RDNA consumer cards, with **zero documented
  quantization-correctness complaints** found across this research, vs. vLLM+`bitsandbytes`'s
  documented history of not working at all until recently. Confirmed via a detailed, real-hardware,
  community-maintained GitHub discussion
  ([ggml-org/llama.cpp#15021](https://github.com/ggml-org/llama.cpp/discussions/15021)) covering
  RX 7800 XT/7900 XTX numbers extensively — purely performance-focused, no correctness bugs
  reported anywhere in the thread.
- `llama.cpp` also ships a Vulkan backend (RADV), independently community-optimized for RDNA
  (its original author used an RDNA2 device) — a real, actively-developed alternative to ROCm/HIP
  entirely. **But a specific claim that Vulkan beats ROCm by "+20%" on RDNA4 (from a personal blog,
  vachsark.com) could NOT be verified** (the source blocked automated fetching, HTTP 403) and is
  **directly contradicted** by the more thorough, multi-contributor GitHub discussion above, where
  real users show ROCm and Vulkan trading wins depending on workload shape (one prompt-processing-
  heavy case favored ROCm, one token-generation-heavy case favored Vulkan) on the same RX 7800 XT.
  **Do not cite the "+20%" figure — flag as a rejected/unverifiable claim if it resurfaces.**
- No dedicated third-party "ROCm fork optimized specifically for consumer RDNA" project was found
  by name in this research beyond `llama.cpp` itself and its Vulkan backend — the real story is
  that `llama.cpp`/`ggml`'s own upstream project (not a fork) already IS the community's
  consumer-AMD-optimized answer, and has been for longer than vLLM's ROCm story has existed in any
  serious form.

### 11c. Implication for this project, not yet acted on

This session's earlier move from Ollama to vLLM (`project_ollama_dropped` memory) was made
specifically to rule out two CONFIRMED Ollama-serving-layer bugs (think-mode passthrough,
`ollama/ollama#6155` nested-array stringification) as possible causes of prior disqualifications.
That reasoning still holds for the candidates it was meant to isolate. But this research suggests
the swap was a lateral trade, not a strict upgrade, for THIS specific hardware: it removed two
known Ollama bugs at the cost of trading into vLLM+`bitsandbytes`-on-ROCm's own, much younger,
less battle-tested failure surface (this session's zombie crash, intermittent empty-JSON,
garbled markers). Neither backend is unconditionally better here — they have different real bug
classes, and `llama.cpp`/Ollama's HIP path has the mileage/maturity edge specifically for
consumer RDNA. **Decided and acted on, same session**: reverted to Ollama as the permanent serving
backend. `~/.venvs/vllm` and vLLM-specific HF cache checkpoints deleted (~24GB reclaimed);
`~/.deepdelve/config.yaml` back on `http://localhost:11434/v1`/`deepdelve-gpt-oss:latest`,
`settings.skip_chat_template_kwargs` reset to `false`. This is an explicit, informed tradeoff, not
a full reversal of §11a/§11b's findings — the two original Ollama bugs that motivated dropping it
in the first place (think-mode passthrough, `#6155`) are still real and still apply to the
candidates they affect; accepted as the cost of `llama.cpp`/HIP's serving-layer maturity edge on
this specific hardware. Full detail in the `project_ollama_restored` memory / `ROADMAP.md`
History.

## 12. Ollama/llama.cpp tuning knobs for this specific hardware (2026-07-27)

Research only, nothing changed live. Hardware: AMD Radeon RX 9060 XT (gfx1200, RDNA4), 17GB VRAM,
ROCm 7.2.4, Ryzen 5 9600X (6C/12T), 30GB system RAM, Ollama 0.31.2. Prompted by wanting to know
whether standing up a raw `llama.cpp server` (vs. tuning Ollama's existing knobs) is worth the
backend-swap risk this project already walked back once with vLLM (§11).

**Flash attention + KV cache quantization** — the two real free levers, primary source
[Ollama FAQ](https://docs.ollama.com/faq):
- `OLLAMA_FLASH_ATTENTION` default `false`/auto, currently unset on this machine (confirmed via
  `systemctl show ollama`/drop-in inspection — not enabled).
- `OLLAMA_KV_CACHE_TYPE` (`f16` default → `q8_0`/`q4_0`) **only takes effect when flash attention
  is on** — setting the cache type alone with flash attention off does nothing.
- `q8_0`: ~50% KV cache memory, negligible quality loss (published perplexity delta +0.002 to
  +0.05). `q4_0`: ~25% memory, small-medium quality loss.
- **Not universal — gated by model architecture allowlist, not just backend.** Confirmed allowlist:
  `gemma3, gptoss, gpt-oss, mistral3, qwen3, qwen3moe, qwen3vl, qwen3vlmoe` — `command-r`/`llama3`
  silently fall back to f16 with no error.
  [ollama/ollama#13337](https://github.com/ollama/ollama/issues/13337). `deepdelve-gpt-oss` (gptoss
  arch) **is** on the list, so this is actually usable here.

**A documented ROCm crash mode on this exact GPU model — the important finding.**
[ggml-org/llama.cpp#21376](https://github.com/ggml-org/llama.cpp/issues/21376): RX 9060 XT
(gfx1200), 16GB VRAM — exact match for this machine. ROCm/HIP backend hard-crashes
(`cudaMalloc failed: out of memory` → segfault) when the KV cache allocation doesn't fit in
remaining VRAM after model weights load, instead of degrading gracefully. The **same config on
Vulkan spills to system RAM instead of crashing**. Reported unresolved as of research date, no
maintainer fix found. Directly relevant here: this box already runs close to the ceiling
(17GB total VRAM, `gpt-oss:20b` at `num_ctx 16384`, `OLLAMA_GPU_OVERHEAD` unset/`0` = zero reserved
headroom). Turning on flash attention to push context higher, without also reserving overhead
headroom, risks hitting exactly this crash.

**rocWMMA (flash-attn accelerator) support on gfx1200 — real but unconfirmed for Ollama's own
builds.** Primary source [ROCm/rocWMMA](https://github.com/ROCm/rocWMMA) confirms `gfx1200`/
`gfx1201` **are** officially supported (grouped under `gfx12`) — an aggregator claim found earlier
in this research pass ("RDNA3-only") was outdated/wrong, corrected against the primary repo.
However rocWMMA flash-attn requires the llama.cpp **compile-time** flag
`-DGGML_HIP_ROCWMMA_FATTN=ON`. Not confirmed whether Ollama's official prebuilt ROCm binaries ship
with this flag on — would need to check Ollama's release build scripts/CI directly, not done this
pass.

**Ollama ships an experimental Vulkan backend, on by default.** `OLLAMA_VULKAN` defaults to `true`
in current Ollama source (`envconfig/config.go`). Given the #21376 finding above, Vulkan may
actually be the more crash-resilient backend for this specific GPU under VRAM pressure — a
different claim than §11b's rejected "+20% Vulkan speed" one (that was about raw throughput from a
blocked/unverifiable source; this one is about crash behavior under VRAM pressure, corroborated by
a live GitHub issue against this exact card).

**Other confirmed tunables**, read directly from `envconfig/config.go`: `OLLAMA_NUM_PARALLEL`
(already `1` in this machine's live config, correct as-is), `OLLAMA_GPU_OVERHEAD` (bytes, default
`0` — likely mitigation for the #21376 crash mode), `OLLAMA_SCHED_SPREAD` (spreads layers across
multiple GPUs, irrelevant here — only one real GPU), `OLLAMA_IGPU_ENABLE` (default `true` — this
machine has a Raphael iGPU present alongside the discrete card; could not confirm from
`journalctl -u ollama`, which returned no entries, whether Ollama schedules anything onto it —
needs checking Ollama's actual log location before drawing a conclusion).

**Not confirmed / left open, not enough primary-source coverage to act on**:
- `num_batch`/`ubatch` (prompt-processing throughput vs. VRAM tradeoff, general llama.cpp guidance
  is 512-2048) — unconfirmed whether exposed as an Ollama env var or only as a Modelfile
  `PARAMETER`.
- CPU thread count tuning for the 6-core/12-thread 9600X — no primary source found specific to
  this; deliberately not guessing from general knowledge.

**Implication, not yet decided or acted on**: the two candidate changes with actual primary-source
backing are (1) `OLLAMA_FLASH_ATTENTION=1` + `OLLAMA_KV_CACHE_TYPE=q8_0` for the `gpt-oss` model
(architecture-allowlisted, real VRAM/context win) and (2) setting `OLLAMA_GPU_OVERHEAD` to some
explicit headroom value before doing (1), specifically because of the #21376 crash mode on this
exact card. Whether to also trial `OLLAMA_VULKAN` as the primary backend instead of ROCm is a
separate, bigger decision — it would trade ROCm's llama.cpp/HIP serving-layer maturity edge (§11)
for Vulkan's crash-resilience under VRAM pressure, and hasn't been benchmarked on this hardware at
all yet.

## 13. Qwen3 think-suppression: is it actually an unfixable serving-layer bug, or a fixable chat-template gap? (2026-07-28)

Prompted by a user-supplied primary source: a Reddit report ("I ran Qwen 3.6 locally for 45 days,
here are the results", r/LocalLLM) describing the exact "But wait... Actually..." unbounded
reasoning-loop pattern this project independently hit the same day (see `ROADMAP.md`'s
`qwen3-4b-combined-v2-lora` DISQUALIFIED entry). Two claims from that thread, checked against
primary sources rather than taken at face value.

### Claim 1: a numeric reasoning-token budget, not a boolean disable, is the community's real fix

The OP and multiple commenters (`awitod`, `vexatious-big`) report that a boolean thinking-disable
is unreliable even on their own setups ("It does it with Q8 too"), and that what actually works is
capping reasoning at a fixed token budget (~4096 tok) via `llama.cpp`'s own
`--reasoning-budget`-style controls. **Checked against Ollama's own docs
([docs.ollama.com/capabilities/thinking](https://docs.ollama.com/capabilities/thinking)):
Ollama's real native control is a top-level `think` field** (`true`/`false`, or a level string
`"low"`/`"medium"`/`"high"` for `gpt-oss`-family models specifically) — genuinely different from
what this project's `orchestrator.py::_get_default_options()` has been sending
(`chat_template_kwargs.enable_thinking` + `reasoning_effort`, both OpenAI/vLLM conventions, not
native Ollama ones). Ollama's docs confirm **no `max_thinking_tokens`-equivalent exists** on either
endpoint — the numeric-budget lever the Reddit thread describes is real for raw `llama.cpp` server
deployments, not currently available through Ollama at all (native or OpenAI-compat).

**Live-tested against both of this project's actual Ollama endpoints, same-day, same hardware**:
sent the correct native `"think": false` directly to `/api/chat` for `qwen3:4b` — still produced a
full, unabbreviated `<think>...</think>` block inline in `message.content` (not even routed to the
separate `message.thinking` field the docs describe for models that support it). Confirms this
project's already-known "Qwen3 think-mode passthrough" bug is not an artifact of using the wrong
parameter name (`chat_template_kwargs`/`reasoning_effort` vs. `think`) — the CORRECT, documented
native field fails identically. Ruled out: this is not a caller-side mistake.

### Claim 2: is the actual root cause a chat-template bug, not an unfixable serving bug?

A commenter (`i_am_me0_0`) reports the real fix for Qwen3.6's tool-calling/reasoning problems was
swapping in a community-patched chat template
([froggeric/Qwen-Fixed-Chat-Templates](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates)
on HuggingFace), not a serving-layer flag. **Checked the repo's own README directly**: it documents
several real, specific template bugs, most relevantly an "agentic loop stalling" bug where the
official template's practice of injecting an empty `<think>\n\n</think>\n\n` block trains a "toxic
learning pattern" causing an "80%+ premature `<|im_end|>` stalling rate" — the model aborts a turn
instead of following through with content or a tool call. **This is a structurally different bug
from this project's own symptom, not the same one**: our smoke-tested Modelfile template (derived
from Ollama's own imported `qwen3:4b` GGUF template, `ollama show qwen3:4b --modelfile`) does the
OPPOSITE — it unconditionally opens a bare `<think>\n` at generation start with no forced-empty
`</think>` closing branch for the nothink case at all, so the model just keeps reasoning
indefinitely rather than aborting early. Two different template defects, same family, opposite
failure shape — confirms the underlying claim (Qwen chat-template correctness, not just serving
flags, genuinely governs think-block behavior) without over-claiming that froggeric's specific fix
applies unmodified here.

**Real caveat, not glossed over**: froggeric's README states coverage for "Qwen 3.5 and 3.6
variants" — it does **not** explicitly claim Qwen3 (4B) family coverage, which is what this
project's own LoRA fine-tune round is built on (`Qwen/Qwen3-4B`). Applicability to the 4B model is
a plausible lead, not a confirmed fix — would need its own direct template swap + live retest
before being treated as resolved, not assumed from the 3.5/3.6-scoped README.

### Where this leaves the standing "accepted, unfixed" bug framing

`ROADMAP.md`'s "Ollama restored" entry (2026-07-26) accepted Qwen3 think-mode passthrough as a
known, permanent tradeoff. This research suggests that framing may be premature: the actual defect
looks more like "this project's specific Modelfile template lacks the conditional nothink-closing
branch other Qwen3 chat templates (including community-patched ones) do implement" — a fixable,
template-level gap, not an inherent Ollama/llama.cpp serving limitation.

**Checked directly, same session**: `ollama show deepdelve-qwen3.6:latest --modelfile` has **no**
literal Go `TEMPLATE` think-handling logic at all — it uses `TEMPLATE {{ .Prompt }}` plus
`RENDERER qwen3.5` / `PARSER qwen3.5`, Ollama's newer built-in model-specific renderer/parser
plugin system, a structurally different mechanism from the hand-written Jinja-style Go template
this project's `qwen3:4b` import carries (no renderer/parser directives at all, `ollama show qwen3:4b
--modelfile` confirms this). So the working case isn't "a template with the right conditional
branch" as originally guessed — it's a different, newer Ollama subsystem entirely that the 4B
import doesn't use. **Revises, doesn't invalidate, the fixable-not-serving-bug framing above**: the
fix path most likely isn't "patch the Jinja-style template," it's confirming whether Ollama ships
(or can be pointed at) a `qwen3`/`qwen3moe`-family renderer/parser pair the way it does for
`qwen3.5`, and whether that's what actually needs wiring up for the 4B model. Not yet acted on —
would need checking Ollama's own renderer registry for a `qwen3` (non-3.5/3.6) entry before writing
any new Modelfile.

### 13a. Follow-up, same day: the renderer/parser hypothesis tested live — REJECTED, not just unconfirmed

Checked Ollama's own source directly (`gh api repos/ollama/ollama/contents/model/renderers`,
primary source, not an aggregator): `model/renderers/` has `qwen35.go`, `qwen3coder.go`,
`qwen3vl.go` — **no dedicated plain-`qwen3` renderer exists at all**. `renderer.go`'s
`rendererForName` switch confirms the `"qwen3.5"` registry name maps to
`Qwen35Renderer{isThinking: true, emitEmptyThinkOnNoThink: true}` and is looked up by bare string
key with **no model-architecture gating** — so a Modelfile can request `RENDERER qwen3.5` /
`PARSER qwen3.5` against ANY GGUF regardless of its actual reported architecture. Read the
renderer's own `Render()` source directly: when not thinking, it does exactly what was hoped —
emits a forced, closed `<think>\n\n</think>\n\n` block before the assistant turn starts
(`emitEmptyThinkOnNoThink` branch) — the precise mechanism our legacy Jinja-style Modelfile
template lacks.

**Live-tested against this project's actual `qwen3:4b` GGUF** (built a throwaway Ollama tag,
`FROM` the exact same blob `ollama show qwen3:4b --modelfile` already uses, with `RENDERER
qwen3.5`/`PARSER qwen3.5` instead of the legacy `TEMPLATE` block): the renderer swap loaded and
worked structurally (`think: true` sanity check produced clean, correctly-separated
`message.reasoning`/`message.content`, confirming the renderer itself functions correctly against
this GGUF). But **`think: false` still failed to suppress reasoning** — the model generated a full
reasoning block anyway, immediately after the renderer's own forced `<think>\n\n</think>\n\n`
prefix, and in this run also emitted a hallucinated extra tool call
(`example_function_name`, copied straight out of the renderer's own tool-call format instructions)
alongside the real one, repeated 5 times.

**Verdict: rejects, not just leaves unconfirmed, the "wrong serving mechanism" hypothesis for this
specific model.** Four independent combinations have now been tested and uniformly fail to
suppress thinking on this exact `Qwen3-4B` checkpoint: `chat_template_kwargs.enable_thinking` +
legacy template, native `think` field + legacy template (both endpoints), and now native `think`
field + the same battle-tested `qwen3.5` renderer/parser that works correctly for
`deepdelve-qwen3.6`. The common factor left standing is the model checkpoint itself, not the
serving mechanism — this specific Qwen3-4B weight set appears not to reliably honor a
forced-empty-think instruction the way Qwen3.5/3.6 do, regardless of how correctly that
instruction is delivered. Consistent with (but narrower than) froggeric's own explicit "Qwen 3.5
and 3.6" scoping — their fix, and Ollama's own working mechanism, may simply not transfer to plain
Qwen3 at all. **No further serving-layer/template lever identified to try** — the next
investigation, if pursued, would need to look at the model/training side (e.g., whether the LoRA's
own training data ever included nothink-formatted examples) rather than anything Ollama-configurable.
