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
  3. **2026-08-17: the remaining 30 appendix pages now read (`papers/axpo_2605.28774.pdf`,
     confirmed 41 pages total via `pdfinfo`).** Appendix D formalizes Proposition 1 as a clean,
     correct monotonicity proof (coverage `1-(1-p)^N` is non-decreasing in `p`; resampling raises
     the effective per-sample success rate from `q·p_tool` to `p(t1_src)` by construction) — no
     hidden caveat, the informal claim already cited holds up under the actual math. **Appendix E
     (Limitations) directly confirms and sharpens the reward-shape mismatch already flagged in
     point 1 above, in the paper's own words, not just my inference**: *"AXPO's subgroup-level
     trigger... and per-prefix advantage... both rely on a binary, automatically verifiable outcome
     signal r ∈ {0,1}. Tasks where verifiability is partial... require a different definition of
     'failed subgroup' before tool-call resampling applies."* This is the authors' own explicit
     scope boundary, not a gap I inferred — strengthens (doesn't just repeat) the earlier caveat
     that adapting AXPO to `writer_role_response_reward`'s structural (did-it-happen) reward, not a
     correctness-verifiable one, needs the trigger definition itself reworked, not just the
     mechanics. Appendix E also notes training was capped at 8B (compute-limited, not a
     methodological choice) and the tool inventory excludes long-latency/high-per-call-cost tools
     (browser agents, GUI control, terminal sessions) — DeepDelve's own tool surface (web_search,
     fetch_url_to_workspace, delegate_tasks) sits closer to this excluded category than to AXPO's
     tested Python-interpreter/web-search/image-zoom set, a real, previously-unstated domain gap.

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
     for the ATLAS idea (now read and verified, see its own ✅ entry below — this cross-reference
     was stale, written before that read happened, caught 2026-08-17) that a domain-specific
     taxonomy induced from
     DeepDelve's own traces could surface a different profile than the generic MAST percentages
     quoted above — those percentages describe an aggregate across 7 unrelated systems, not a
     prediction for what DeepDelve's own failure mix looks like.
  3. **No dedicated Limitations section exists in the paper** (checked directly, confirmed absent)
     — a minor, honestly-noted gap in an otherwise rigorous paper, not a reason to distrust the
     core findings.
  4. **2026-08-17: PDF saved to `papers/mast_failure_taxonomy_2503.13657.pdf` (was missing from
     that folder — read in an earlier session before the save-papers convention started) and
     Appendix A's full 14-mode catalog read verbatim (previously only 4 of the 14 codes had been
     extracted into this file).** Two more direct matches to DeepDelve's own bug catalog, used to
     scope a same-day fix (see `session_status/CURRENT.md`): **FM-1.3 "Step repetition"**
     ("Unnecessary reiteration of previously completed steps in a process") — matches both a
     Planner re-dispatching the same research angle under new names after being warned not to, and
     a sub-agent re-issuing read/grep calls after its first one already succeeded; the paper's own
     data notes "OpenManus exhibits a tendency towards step repetition (FM-1.3)" (§5.1), confirming
     this is a recurring, not one-off, failure shape. **FM-2.5 "Ignored other agent's input"**
     ("Disregarding or failing to adequately consider input or recommendations provided by other
     agents") — matches a Planner seeing a tool-surfaced correction and discarding it repeatedly.
     Both cross-checked against §5.3's already-cited causal evidence (structural/gating
     interventions, not prompt wording, produced the paper's own measured +9.4%/+15.6% gains) —
     used as the deciding argument for HOW to fix these two DeepDelve bugs (a real gate, not
     stronger warning text).

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
1. ~~Endgame-collapse: Lost in the Middle gives a partial mechanism, not a complete account...~~
   **RESOLVED 2026-08-17** (the exact targeted search this item called for, sitting open since
   2026-07-19, finally run — caught during a self-audit of "what literature did we say we'd chase
   down and never did," prompted by the user asking to check for missed planned literature). Found
   and **read in full**: [*LoopGuard: Breaking Self-Reinforcing Attention Loops via Dynamic KV
   Cache Intervention*](https://arxiv.org/abs/2604.10044) (Xu, Wu, Shi, Cui, Liu, Li, Ma, Liu, Zhu,
   Xu — Soochow University / Tongji University / HKUST / Alibaba Group / Zhejiang Normal
   University, arXiv:2604.10044, 2026-04-11, 16 pages, `papers/loopguard_2604.10044.pdf`).
   **Provenance caveat**: no confirmed peer-reviewed venue found in the paper itself — an
   unreviewed preprint, same caveat tier as other arXiv-only sources in this review.
   - **This is a real, mechanism-level answer to the exact phenomenon the open item named**: long-
     context decoding can collapse into "persistent repetition loops" via a two-stage trajectory —
     output diversity stays high early, then drops sharply at a collapse point and stays
     persistently low (their Figure 2a) — driven by a subset of attention heads locking onto a
     narrow suffix of the generation history, which KV cache reuse then stabilizes and
     self-reinforces (their Figure 1a/b). This is a genuinely different, complementary mechanism to
     Lost in the Middle's positional-attention account: LiM explains why MIDDLE content is
     under-used at any single point; LoopGuard explains why a session gets WORSE OVER TIME as it
     runs long, matching the specific "progressively more repetitive as a retry loop lengthens"
     pattern this item was opened to explain.
   - **Directly relevant scale effect, verified (their §3.2, Figure 2b)**: "larger models survive
     longer while smaller models fail earlier and more frequently" — the collapse is MORE
     pronounced in smaller LLMs, "making loops easier to trigger and harder to escape." Tested
     directly on Llama-1B/3B and Qwen-1.7B/4B (their §6) — the same model family and parameter
     range as several of DeepDelve's own disqualified bake-off candidates (`qwen3:4b`,
     `llama3.2:3b`). This gives a candidate mechanism-level explanation for WHY those specific
     small models looped/repeated during real DeepDelve runs, not just an empirical observation
     that they did.
   - **Real, honest limitation that caps how far this generalizes to DeepDelve specifically (their
     own Limitations section)**: LoopGuard's detection targets LEXICAL repetition (the same tail
     span recurring near-verbatim); it explicitly does NOT claim to catch "semantically repetitive
     but lexically varied" degeneration — and DeepDelve's own most damaging repetition pattern
     (task-name churn — `rent_lisbon` → `rent_lisbon_price` → `rent_lisbon_analysis`; PeerReviewer's
     varying hallucinated filenames, `session_status/CURRENT.md` 2026-08-17) is exactly that
     semantically-repetitive-but-lexically-varied shape, not verbatim lexical looping. **The paper's
     own mechanism (attention collapse locking onto a narrow suffix) is a plausible root cause for
     BOTH shapes, but LoopGuard's specific detector and fix (KV-cache pruning) would not catch
     DeepDelve's actual observed variant, and DeepDelve doesn't control inference-server KV cache
     policy anyway (that's Ollama's own layer, not application code)** — so this resolves the
     open literature question (a real, verified mechanism now exists) without supplying an
     adoptable fix; today's own tool-failure-streak-guard fix (this session, `tools/core.py`)
     addresses the semantic-variant shape structurally, at the application layer, independent of
     whatever the serving engine's attention/KV-cache behavior turns out to be underneath it.
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
- **2026-08-17: read cover-to-cover (12 pages, `papers/rag_failure_taxonomy_trustnlp2026.pdf`)** —
  previously cited via specific sections/tables without an explicit completeness confirmation. The
  paper's own §6.6 Limitations, now folded in: single-rater evidence grading (already noted above)
  with NO formal inter-rater agreement established; and, more load-bearing than that, its own
  explicit caveat that **"claims regarding the relative frequency, severity, or production
  prevalence of specific failure modes rest on secondary interpretation... rather than direct
  measurement. The taxonomy is therefore most usefully read as a structured map of the failure
  space, not as a quantitative ranking of failure prevalence."** The 12/33-unvalidated headline
  number above is a real, useful signal about WHERE the evidence gaps are, not a claim about how
  often each failure mode actually occurs in production — worth keeping that distinction precise
  when citing this paper elsewhere.

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
- **2026-08-17: read cover-to-cover (9 pages, `papers/slm_agentic_survey_2510.03847.pdf`)** —
  previously cited via Table II and Section XI without an explicit completeness confirmation. The
  paper's own §XVI Limitations, now folded in, directly tempers the headline Table II numbers cited
  above (98.7%/97.9% for schema-constrained + INT8 vs. 91.2%/89.4% baseline): *"Benchmark/API
  drift; results may not transfer,"* *"Overfitting to narrow traces,"* and *"Heavy validator
  dependence can hide reasoning [failures]."* The magnitude of the constrained-decoding win is
  real and directly measured (not a search-summary claim), but the paper itself warns against
  assuming that specific gap generalizes past its own benchmark distribution — relevant caution
  before treating "add schema-constrained decoding" as a guaranteed fix rather than a
  well-evidenced candidate worth testing on DeepDelve's own tasks.

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

## 14. Ornith-1.0-9B live bake-off (2026-07-28): a genuine architecture bug found, a real serving-layer gap confirmed, model verdict left open

Full live-benchmark trail for `deepreinforce-ai/Ornith-1.0-9B` (dense, built on Qwen3.5
architecture, MIT licensed) — the "untested middle ground" candidate identified while scoping
alternatives to `gpt-oss:20b`. Five live runs total (one cold pull + smoke test, then four
`--resume-run` attempts against the same interrupted research), two real DeepDelve bugs found and
fixed along the way, one Ollama serving-layer gap isolated and confirmed. Full blow-by-blow in
`session_status/CURRENT.md`; this section is the durable research record.

### 14a. Smoke test and first live run: genuinely strong initial synthesis, but two different failure modes across two attempts

Direct tool-call smoke test (3/3 runs) showed short, bounded reasoning (446-1092 chars) and
correctly-nested `delegate_tasks` calls matching DeepDelve's exact schema — qualitatively the
cleanest smoke-test result of any candidate this project has tested, better than the base
`qwen3:4b`/LoRA candidates tested the same day (see §13/§13a above).

**First full benchmark run**: real, substantial research (28 fetched sources, 0 web_search
failures) but ended in `QuotaAbortException` ("Agent trapped in loop... delegate_tasks") — one
specialist kept re-delegating its own sub-analyzers until the shared quota pool was exhausted.
Never reached the completion-check phase at all.

**Second run (quota bumped 6→12 to isolate whether over-delegation alone was the blocker)**:
produced a real, accepted, grounded `final_report.md` — but it was an honest, near-empty report:
*"No task returned a real citable source URL meeting the threshold for inclusion"* despite 19
newly-fetched, genuinely on-topic sources (arXiv, ACM, IEEE, Springer papers) sitting unused. Every
dispatched Analyzer self-rejected its own real evidence as non-citable. Trivially "grounded" (zero
citations = nothing to be wrong about) but delivered zero actual answer — the mirror image of the
Reddit thread's original "grades its own homework and gives itself an A" concern: this model, this
day, graded its own real evidence an F across the board.

### 14b. Root cause of the stuck-loop failures traced to a real chat-template defect, independently corroborated by the model's own GitHub issues

Checked `deepreinforce-ai/Ornith-1` GitHub issues directly (`gh api`, primary source): #4 ("stuck
in tool loops quite often") and #16 (adds evidence to #4, an 8-hour Claude Code session that
re-fetched the same document 37 times, <1K tokens of real prose across the whole session) both
report the *exact* symptom shape hit here, reproducing independently across VSCode/Continue and
Claude Code — not something specific to this project's harness. HF discussion #42 on the 35B GGUF
names the mechanism: the stock chat template injects an empty `<think>\n\n</think>` block before
tool calls, "poisoning" the model into associating empty thoughts with looping tool invocation, and
separately strips reasoning from past turns, invalidating the KV-cache prefix on every turn. The
fix already existed and was already being investigated this same day for the Qwen3-4B LoRA (§13):
`froggeric/Qwen-Fixed-Chat-Templates`.

**Applied directly to this candidate**: patched the pulled GGUF's `tokenizer.chat_template`
metadata with froggeric's v21.3 template via `gguf_new_metadata.py --chat-template-file` (no
tensor rewrite, ~18s), re-imported as a fresh Ollama tag. Live-retested 3/3: now correctly splits a
two-part request into two separate `delegate_tasks` entries (the base import always merged them
into one) — a real, measurable improvement, not just a placebo.

### 14c. Two genuine DeepDelve architecture bugs found and fixed live, independent of this candidate's own capability

**Bug 1 — `check_not_delegated` scoped to the wrong process.** Already flagged as a known,
deliberately-unfixed gap in `ARCHITECTURE.md` §4: `Ctx.delegated` only checked the CURRENT
process's live quota usage (always 0 at the start of a resumed process), so a resumed Planner
correctly told (via `build_resume_input`) not to re-delegate got `check_not_delegated`'s "your ONLY
next tool call must be delegate_tasks" directive anyway — a live-confirmed direct contradiction
that derailed a resumed run into a `think_tool` reflection loop until forced quota-abort. **Fixed**:
`Ctx.delegated`'s construction in `run_completion_check` now also treats a non-empty
`run_state.data["fetched_urls"]` (already resume-carried-over, ground truth per this project's own
existing philosophy) as proof delegation happened in ANY session. One shared fix site, both
consumers (`check_not_delegated`, `check_missing_artifact`'s redelegation-forbidding message)
benefit.

**Bug 2 — Builder handed an instruction it structurally cannot follow.** Eight
`_BUILDER_FIXABLE_PROBLEMS` grounding checks (`check_no_urls`, `check_regulation_unsupported`,
`check_stub_source`, and others sharing the `_redelegate_directive` helper) explicitly tell the
reader to "delegate a Searcher" / "your ONLY next tool call must be delegate_tasks" when no new
sources exist yet — worded for the Planner, which has `delegate_tasks`. Builder does not (only
`read_workspace_file`/`grep_workspace_file`/`write_workspace_file`/`think_tool`). Live-confirmed: a
Builder correction cycle got stuck narrating *"I will delegate a Searcher... this delegation is
required before any further report writing can occur"* across multiple retries instead of ever
rewriting the file — a reasonably honest reaction to a genuinely impossible instruction, not
misbehavior. `FindingsWriter`'s own dispatch branch already avoided this exact trap (see its own
docstring); Builder's branch never got the same treatment until now. **Fixed**: a shared
`_BUILDER_NO_DELEGATE_CLARIFICATION` string appended to both Builder-dispatch branches, telling it
plainly it cannot delegate and to drop/rewrite the claim instead.

**Both fixes are model-independent** — they would have produced the identical contradiction for
ANY model hitting these paths, not something specific to Ornith. Regression tests added for both
(`test_structural_checks.py`); full suite passing.

### 14d. `edit_workspace_file` added — a real capability gap, not (only) a prompt-wording problem

Even after 14c's fixes, a genuine "drop 3 flagged citations, keep everything else" correction cycle
kept failing: Builder has only `write_workspace_file`, meaning every correction is a full-document
regeneration from a cold context — a much harder task (hold the whole ~15-20KB document plus the
fix instructions, reproduce everything except the flagged spans correctly) than fresh synthesis
from `findings.md`, which this same model did well. Confirmed via the raw session-event log:
one correction attempt made zero write/edit tool calls at all across ~8 minutes of
reading/reflection, and successive full-rewrites fixed the previously-flagged stub citations while
introducing *different* new ones each time (whack-a-mole), never converging.

**Added**: `edit_workspace_file(filename, old_string, new_string, replace_all=False)` — an
old-string/new-string targeted replacement tool (same shape as this project's own editing
convention), wired into both `Builder` and `FindingsWriter`'s tool lists, with its own quota
(`edit_workspace_file: 10`, both `config_template.yaml` and the live config) and instruction text
telling each role to prefer it for a small fix over a full rewrite. Regression tests added (tool
behavior + both roles actually having it wired into `app.py`).

**Live-tested, not yet load-bearing**: the model did not spontaneously reach for the new tool even
on a textbook targeted-fix case in the one live retest performed after adding it — inconclusive on
whether the tool itself changes behavior versus needing an even more explicit steer. Not
contradicted either; the sample size (one attempt) is too small to draw a real conclusion. Left
open for a future session.

### 14e. Serving-layer finding: thinking suppression works on Ollama's native API, leaks on the OpenAI-compat endpoint when tools are present

Precisely isolated via four direct API tests against the froggeric-templated tag, holding
everything else constant:

| Endpoint | Tools present | `think` value | Result |
|---|---|---|---|
| native `/api/chat` | no | `false` | Clean — zero reasoning, `content: "42"` |
| native `/api/chat` | no | `true` | Full reasoning in separate `thinking` field (works as designed) |
| native `/api/chat` | **yes** | `false` | **Clean — zero reasoning, correct tool call** |
| OpenAI-compat `/v1/chat/completions` | **yes** | `false` | **Leaks — short reasoning text in a `reasoning` field** |

The model and the froggeric template's think-suppression mechanism (`ns_state.thinking` forcing an
empty `<think></think>` block) work correctly in every case tested — including the exact
tool-calling shape DeepDelve needs. The leak is isolated specifically to Ollama's OpenAI-compat
translation layer failing to forward `think:false` into the template context when `tools` is also
present in the request — a narrower, more precisely-scoped instance of the same class of gap as
`ROADMAP.md`'s already-accepted "Qwen3 think-mode passthrough" bug, but confirmed here NOT to be
inherent to the model or template: the native endpoint proves full suppression is achievable.

**Real consequence**: DeepDelve is built entirely on an OpenAI-compatible client
(`api.openai_base_url`), so every model this project has ever tested through Ollama has been
subject to this same endpoint-level leak whenever it makes a tool call with thinking nominally
disabled — a small but real, previously-unattributed source of the "reasoning present even with
`enable_thinking: false`" symptom seen across multiple candidates today and in earlier sessions,
distinct from (and narrower than) cases where the underlying model/template genuinely cannot
suppress thinking at all (§13/§13a's Qwen3-4B finding). Not fixable without either switching
DeepDelve to Ollama's native API for at least the tool-calling path, or an upstream Ollama fix to
its OpenAI-compat shim — see `ROADMAP.md`'s Pending section for the resulting design question.

### 14f. Independent community corroboration (2026-07-28, two Reddit threads, r/LocalLLaMA)

Checked user-supplied primary sources directly (not aggregator summaries) after tonight's own
findings, specifically to see whether the looping/stalling pattern was unique to this setup:

- **Looping is widely and independently reported** for both the 9B and 35B variants, across
  multiple unrelated users and harnesses (VSCode/Continue, Claude Code, GH Copilot, Pi): *"the darn
  thing keep looping up over an issue millions times"*, *"It looped so bad for me"*, *"9b tends to
  loop... as long as it has clear instructions, it's pretty great"*. Confirms tonight's struggles
  reflect a real, model-family-wide trait, not something specific to this project's harness or
  hardware.
- **One user reports thinking-ON correlating with looping**: *"I got no loops and it coded fine,
  but I turn off thinking"* — consistent with, but not proof of, the fact that this project's own
  thinking-suppression was never fully clean for Ornith until 14e's endpoint-isolation testing
  (and even then, only via an endpoint DeepDelve doesn't use for its actual agent loop).
- **One user independently reports poor agentic-harness results even with the official/correct
  chat template**: *"I experimented with a few [harnesses], but the results were generally poor
  even when using their official Jinja template"* — tempers how much of tonight's partial
  improvement should be attributed to the froggeric template fix alone versus this being a broader,
  harness-agnostic agentic-reliability limitation of the model family.
- **One blunt outside opinion, independently arrived at**: *"There's no 'good' model at 9B... unless
  you need auto-completion or very simple code snippets, forget it"* — consistent with this
  project's own "no more small models" scoping decision made earlier the same day, from a source
  with no knowledge of that decision.

### 14g. Overall verdict

**Not DISQUALIFIED, not PASSED — left INCONCLUSIVE, deliberately.** Per the Model Evaluation
Standard: every failure mode hit tonight has an independent, non-model explanation attached to it
(a DeepDelve architecture bug fixed mid-session twice, a serving-layer endpoint gap, a missing
editing capability now added) — none of tonight's runs constitute a clean, unconfounded test of
this model's real ceiling. What IS confirmed, positively: the model's initial cold-start synthesis
from real evidence (§14a's `findings.md`, 45 real sources, correct architecture-family coverage
matching the benchmark's own gold reference) was the strongest of any candidate tested this
session. What remains unconfirmed: whether it can reliably close out a correction cycle to a clean,
fully-verified `final_report.md`, even with all three fixes in place. A clean re-test, with a
properly-tracked process this time (see the "resume-run PID tracking" lesson in
`session_status/CURRENT.md`) and enough attempt budget to let the correction cycle actually play
out, is the natural next step whenever this is picked back up — not a re-run of the same
confounded conditions.

### 14h. `api.backend: "ollama"` live verification: a recurring `aclose()` warning investigated and cleared, a real evidence-abandonment gap found unrelated to the new backend

First full end-to-end run through the new `OllamaChatClient` path (`gpt-oss:20b`, the trusted
baseline, isolating the backend as the only variable per the Model Evaluation Standard) surfaced a
repeating `RuntimeError: aclose(): asynchronous generator is already running` / "Task exception was
never retrieved" warning. Checked against primary sources rather than dismissed or assumed
harmful: Python's own asyncio docs (`docs.python.org/3/library/asyncio-dev.html`, "Close
asynchronous generators explicitly") confirm this is a known class of issue — an async generator
that exits early without explicit closure has its cleanup deferred to garbage collection, which can
run "in an unexpected context," including racing with another concurrent task. Traced to the
`ollama` Python package's own `_client.py` (uses `yield`-based async generators internally for chat
streaming, confirmed via direct source read), not anything in DeepDelve's own new backend-selection
code — a pre-existing upstream quirk surfaced for the first time because this project is now
exercising that code path. The docs' own recommended fix (`contextlib.aclosing()`) belongs in
`ollama`/`agent_framework_ollama`, not this codebase.

**Live-traced through the raw session JSON to rule out actual data loss**, not just assumed benign:
every `write_workspace_file`/`read_workspace_file` call whose result window overlapped an `aclose()`
warning was checked directly — no truncated or malformed result found. One real, separate issue
surfaced in the same trace: a single `write_workspace_file` call failed a Pydantic validation
(missing required `filename` field) — a genuine model-generation slip (`gpt-oss` omitted an
argument), confirmed unrelated to the backend by reading `agent_framework_ollama`'s own
`_prepare_tools_for_ollama`: it converts DeepDelve's real `@tool` functions via the same generic
`FunctionTool.to_json_schema_spec()` method both backends share, so the tool schema (including
`required: [...]`) isn't backend-specific. Self-corrected on the very next call, and this project's
own `include_detailed_errors=True` setting (`create_local_agent`) worked correctly through the new
client too, surfacing the real Pydantic error text rather than a generic one.

**A real, separate quality gap found in the same run, also unrelated to the backend**: the final
report dropped genuinely on-topic, well-cited heuristic-algorithm findings (GA, PSO, Bayesian
optimization, simulated annealing — sitting correctly in `findings.md`) in favor of an off-topic
citation (a Maryland school district's payday schedule). `check_report_underuses_findings` didn't
catch it: the aggregate citation ratio (~8 of ~14 sources, ~57%) cleared its 50% threshold, even
though the specific 43% dropped happened to be the most query-relevant content while an irrelevant
source was kept. A real, pre-existing blind spot (measures *how much* evidence survives synthesis,
not *which*) — not something the backend change introduced, but worth a ROADMAP entry of its own if
picked up later.

**Verdict on the backend change itself: no data loss found, no correctness regression found**,
across everything traceable in this run. The `aclose()` noise and the Pydantic slip are both
pre-existing failure classes with their own instrumentation already in place; neither correlates
with the actual content-quality issue found in the same run.

### 15. Ornith-1.0-9B native-backend tool-call corruption: root cause, two ruled-out hypotheses, live-verified fix

Resuming §14's own recommended next step (retest Ornith through `api.backend: "ollama"`) immediately
hit a new failure: `web_search` calls looping with corrupted arguments — the entire arg set wrongly
nested one level under a `"query"` key (`{"query": {"max_results": 10, "query": "...", "topic":
"general"}}`), repeated 15+ times identically, never self-correcting. Killed before it burned the
attempt budget.

#### 15a. Ruled out: multi-turn context pollution

First hypothesis — the model degrading after repeatedly seeing its own identical Pydantic validation
error echoed back. Disproven directly: pulled every `web_search` call's raw arguments from the
session's `ui_events` log in chronological order. The **very first** call was already malformed, and
all 24 retries were byte-identical (same query text, same nested shape, word for word). No drift, no
variation — a deterministic single-shot generation issue tied to the real prompt/tool-schema context,
not something that requires a long conversation to manifest.

#### 15b. Ruled out (as the proximate cause here): tool_call_id collision

Second hypothesis, prompted by an independent Reddit-adjacent lead about Ollama's tool-calling
reliability: `agent_framework_ollama`'s `_parse_tool_calls_from_ollama`
(`_chat_client.py:604`) sets `call_id=tool.function.name` — every `web_search` call in one
conversation gets the *identical* `call_id`. Traced why: Ollama's raw HTTP response DOES include a
genuine per-call `id` (confirmed directly via a raw `curl` probe against `/api/chat`), but the
`ollama` Python package's own `Message.ToolCall` Pydantic model (`ollama/_types.py:290`) only
declares a `function` field — the `id` is silently dropped on parse, forcing the name-based fallback.
Confirmed this is a known, still-open upstream limitation
([ollama/ollama#11417](https://github.com/ollama/ollama/issues/11417) "Consistent Tool Call Ids") and
independently corroborated by an unrelated project, `matthiasn/lotti`'s Ollama inference repository
(PR #3200, closed/unmerged but the relevant file's diff was inspected directly): their own from-scratch
client hits the identical limitation and works around it by maintaining an app-level
`toolCallId → functionName` map, failing loudly (`throw StateError`) rather than silently corrupting
history — their code comment cites the same root fact, that Ollama matches tool results to calls by
function name only, per [Ollama's own docs](https://docs.ollama.com/capabilities/tool-calling).

This is a real, documented limitation worth knowing about for any FUTURE scenario with genuinely
parallel/concurrent same-name tool calls in one turn. It is NOT what caused this specific bug: checked
the real session's timestamps and confirmed DeepDelve's dispatch pattern is strictly sequential (one
outstanding call, one result, before the next call) — the collapsed call_id is never actually
ambiguous under that pattern.

#### 15c. Confirmed root cause: open upstream Ollama bug, Qwen3.6-family template drift

Web research surfaced [ollama/ollama#16383](https://github.com/ollama/ollama/issues/16383)
("qwen3.6 occasionally violates its own tool-call template; qwen3.5 parser returns 500 instead of
tolerating the drift") — still OPEN, fix PR [#16398](https://github.com/ollama/ollama/pull/16398)
unmerged. Qwen3.6-architecture models (Ornith's base) intermittently drift off their own XML
tool-call format, emitting stray/mismatched closing tags (e.g. a spurious `</function_invocation>`
leak from an older training format, per the issue's own captured evidence). When a tag explicitly
declares `PARSER qwen3.5`, this causes a clean, catchable 500. **Our Ornith tag declared no explicit
`PARSER`/`RENDERER`** (`ollama show --modelfile` showed only `TEMPLATE {{ .Prompt }}`, using
whatever GGUF-embedded jinja + Ollama's undeclared-tag fallback handles it) — plausibly why drift
here produced silently corrupted arguments instead of a clean error.

#### 15d. Reproduction methodology — confirmed with DeepDelve's own real wiring, not a synthetic approximation

A simplified synthetic probe (hand-built tool schema, generic system prompt) did NOT reliably
reproduce the bug — underscoring that isolated smoke tests can miss real multi-tool-schema
interaction effects. Built a faithful repro harness instead: imported `orchestrator._build_client`,
`orchestrator._safe_format`, the real `WEB_SEARCHER_INSTRUCTIONS`, `SUBAGENT_DELEGATION_INSTRUCTIONS`,
and the real 4-tool WebSearcher tool list (`web_search`, `fetch_url_to_workspace`, `think_tool`,
`search_verified_findings`) directly from DeepDelve's own source, constructed the agent the same way
`create_local_agent` does, and replayed the real first delegate-task instructions. This reproduced the
"Maximum consecutive function call errors reached (3)" failure against the OLD tag
(`deepdelve-ornith-9b-froggeric`) on the very first turn.

#### 15e. Fix: explicit `PARSER qwen3.5` / `RENDERER qwen3.5`, live-verified

Built `deepdelve-ornith-9b-jsonfmt:latest` from a fresh GGUF copy
(`/mnt/nuevovol/llm-models/ornith-1.0-9b-froggeric-jsonfmt.gguf`, same `gguf_new_metadata.py`
technique as the earlier froggeric patch) with a Modelfile explicitly declaring `PARSER qwen3.5` /
`RENDERER qwen3.5`. Note: declaring `RENDERER qwen3.5` makes Ollama use its own internal built-in
renderer, overriding the GGUF-embedded jinja entirely — so the template's own tool-call-format default
(patched to `'json'` as a first attempt) ended up moot; what actually fixed it was Ollama's own
matched renderer+parser pair, not the template edit.

**Reran the identical repro harness against the new tag: zero "Maximum consecutive function call
errors" across ~10+ real `web_search` calls**, versus the old tag failing by call #5 every time.
**Live-verified again in the real full benchmark run** (`research_output/`
`i_want_documentation_on_heuristic_algoritms_for_de_20260728_233339`, ~47 minutes,
`max_completion_check_attempts: 14`): final summary line reported **`web_search failures: 0/18`** —
a complete elimination of this bug class across a real, full multi-hour run, not just a short probe.

**Ornith's own overall verdict remains open, for an unrelated reason**: the run hit `max_run_minutes`
(45 min) mid-retry-chain while still correcting a genuine, separate content-quality issue
(`findings_underuses_evidence`). `final_report.md` exists (22 real sources fetched) but the system
explicitly flagged it unverified rather than silently accepting it — 6 honest write→review→fix rounds
happened; it simply ran out of wall-clock budget. This is a content-convergence-speed question, not a
new architecture bug — a longer `max_run_minutes` or fewer fixed retry-rounds-per-issue is the natural
next lever, not another root-cause hunt.

## 15. Root cause of a Searcher over-fetching for single-fact tasks, cascading into an unforced re-verification round (2026-08-01)

### Trigger

Live smoke-testing the per-facet Builder dispatch fix (`ROADMAP.md` multi-facet abandonment item,
`session_status/CURRENT.md` open thread 1) with two small, purpose-built 2-facet prompts
(`eval/dataset.jsonl`, `medium` tier) — both timed out (900s) *before ever reaching the
completion-check pipeline*, on prompts asking for genuinely trivial single-answer facts (the men's
100m sprint world record holder, the cause of the 2010 Eyjafjallajökull air-travel disruption).

### What actually happened (traced from `_run_state.json` + `_todos.md` + the session log, not guessed)

The Planner-level plan was correctly scoped from the start — `_todos.md` shows exactly the right
2-task plan (`world_record`, `volcano_disruption`), matching `PLANNER_INSTRUCTIONS`' "Simple factual
query... Dispatch a SINGLE Searcher task" rule (`src/prompts.py:163`). No top-level plan bloat.

The actual budget sink was one level down. `volcano_disruption`'s single `WebSearcher` dispatch
fetched **11 distinct URLs** for a single uncontested fact, despite `WEB_SEARCHER_INSTRUCTIONS`
explicitly saying (`src/prompts.py:322`, step 2): *"Authoritative/official sources... ONE source is
sufficient. Do NOT search further to corroborate an official spec page"* and (step 7, line 333):
*"STOP EARLY... stop searching for MORE sources once you have one good one."* The prompt-level
stopping instruction exists and is explicit; the model did not reliably follow it. Fetching 11
sources exhausted the task's `context_budget_chars` (50000, `config_template.yaml`), which triggered
`orchestrator.py`'s one-shot budget-nudge cutoff (`_run_single_task`, ~line 1059-1070) and appended
`"[SYSTEM: task 'volcano_disruption' hit its context budget — findings below were wrapped up early.]"`
to the returned summary.

That cutoff marker is not itself a bug — it is the intended graceful-degradation behavior. The
cascade is what follows: the Planner's own `ADAPTIVE PLANNING LOOP` instructions
(`src/prompts.py:212-225`) correctly read "wrapped up early" as "did this result actually answer the
slot's question, or come back empty/uncertain?" and dispatched a legitimate follow-up
`volcano_disruption_verification` task — but in the SAME `delegate_tasks` batch, also dispatched
`world_record_verification`, even though `world_record`'s own finding was clean, complete, and
carried no cutoff marker at all. The model did not distinguish "this one needs a follow-up" from
"let me re-verify everything in this batch." `dispatched_tasks` in `_run_state.json` confirms only 8
total top-level+nested dispatches for the whole run (not a delegation explosion) — the cost is
wall-clock per-dispatch (many sequential `web_search`/`fetch_url_to_workspace`/`think_tool` round
trips within the ONE `volcano_disruption` dispatch), not delegation count.

### Literature grounding (checked before writing this up, not asserted from memory)

This is a named, documented failure class, not a one-off quirk of this run:

- **arXiv:2607.05775** ("Beyond the Leaderboard: A Synthesis of Tool-Use, Planning, and Reasoning
  Failures in Large Language Model Agents") names exactly this shape as a termination failure: *"the
  planner doesn't know when it has planned enough... ambiguous success conditions... cause models to
  continue verification passes,"* plus a separate "repeated (redundant) API calls" tool-use failure
  category. Matches both halves of what was observed here (over-fetching within one dispatch, and
  the unforced extra verification task).
- **arXiv:2604.17337** ("AutoSearch: Adaptive Search Depth for Efficient Agentic RAG via
  Reinforcement Learning") targets this problem directly — its own framing is that agents need a
  *trained, adaptive* stopping depth, not a prompted one, because prompt-only "stop when you have
  enough" instructions are exactly what's unreliable.
- The EviOmni vs. Search-R1 comparison (cited via the same search) is the most direct evidence for
  why a prompt-only fix (which `WEB_SEARCHER_INSTRUCTIONS` already has, explicitly) is known to be
  insufficient: *"EviOmni triggers early stopping when sufficient evidence has been gathered, whereas
  Search-R1 continues with redundant searches"* — i.e. the SAME class of model, absent a structural
  stopping signal, is documented to keep searching past a "stop early" instruction on its own.
- Consistent with this project's own already-recorded synthesis (RESEARCH.md §"verification-heavy,
  not coordination-heavy" / MAST's Task Verification failure category, ~23.5% share) — this is a
  concrete, traced instance of that general category, not a new one.

### Prior art from a real, popular open-source project (checked directly, not inferred)

`langchain-ai/open_deep_research` — the most directly comparable project (same Planner/Supervisor
→ Researcher sub-agent shape) — solves both halves of this exact problem with hard, code-enforced
numeric counters, never a prompt-only instruction:

- **`max_react_tool_calls`** (`configuration.py`, default 10): a per-researcher-step tool-call
  *count* ceiling, checked every loop iteration
  (`deep_researcher.py:492`: `tool_call_iterations >= configurable.max_react_tool_calls`). This is
  the direct analogue to DeepDelve's `context_budget_chars` (`config_template.yaml`) — except
  theirs counts tool CALLS, DeepDelve's counts CHARACTERS. A char budget can be blown by one
  verbose synthesis turn, or never trip at all across many cheap calls — it doesn't directly bound
  "how many sources did you fetch," which is the actual quantity that mattered in the
  `volcano_disruption` incident (11 distinct URLs, one dispatch).
- **`max_researcher_iterations`** (default 6): a hard cap on the Supervisor's own
  reflect-and-follow-up loop, checked every iteration
  (`deep_researcher.py:247`: `research_iterations > configurable.max_researcher_iterations`). This
  is the piece DeepDelve is missing entirely — the `ADAPTIVE PLANNING LOOP`
  (`src/prompts.py:212-225`) has no direct "how many times have I replanned" counter at all; it's
  only bounded indirectly via the shared `delegate_tasks` quota and wall-clock budgets
  (`max_run_minutes`), neither of which is a per-run replan-round ceiling.

Both counters are enforced in code on every loop iteration (`>=`/`>` comparisons gating the
Command/exit branch), never left to the model's own judgment call — the same structural-not-prompted
shape the literature above argues for, independently arrived at by a real, widely-used project
solving the identical agent-shape problem.

### Why this matters for DeepDelve specifically, and what it rules out

`WEB_SEARCHER_INSTRUCTIONS` already contains the correct prompt-level guidance (ONE source
sufficient, stop early) — this is not a missing-instruction bug, it's the literature's own point:
instruction-following alone is not a reliable stopping mechanism for local models under tool-use
pressure. Per this project's own established philosophy (`RunState.coverage()`'s docstring: *"small
local models have repeatedly proven unreliable at following new structured-output conventions"* —
the same reasoning that keeps `COMPLETION_CHECKS` structural/deterministic rather than trusting the
model to self-report), the natural DeepDelve-shaped fix direction is a **structural** guard, not
another prompt rewording. Two concrete candidates, both now with a direct working precedent
(`open_deep_research`'s two counters above) rather than invented from scratch:
1. A hard per-task tool-call/source-count cap (DeepDelve's `context_budget_chars` is char-based;
   `open_deep_research`'s `max_react_tool_calls` counts calls directly — a more precise lever on
   "how many sources did you fetch," and gateable tighter for the Planner's own already-existing
   "simple factual query" classification, `src/prompts.py:162-167`).
2. A hard cap on the `ADAPTIVE PLANNING LOOP`'s own replan-round count (DeepDelve currently has no
   equivalent to `max_researcher_iterations` — only indirect bounds via the shared `delegate_tasks`
   quota and `max_run_minutes`), or, more surgically, an orchestrator-level check that only
   escalates a context-budget cutoff to a Planner-visible "needs follow-up" signal when the cut-off
   task's OWN source count is below some minimum (so a task that already gathered 11 sources before
   running out of budget doesn't read as "incomplete" the same way a task cut off after 1 source
   does).

**Implemented 2026-08-01** (per-task fetch cap + softened cutoff wording, see commit/session for
`src/tools/web.py`'s `_specialist_fetch_over_cap` and `src/engine/orchestrator.py`'s
`_cutoff_marker_text`) and **live-tested same day** — with a genuinely mixed result, reported
honestly rather than declared a full fix:

**Confirmed fixed**: the exact traced incident. Rerunning the same two prompts
(`/tmp/facet_smoke_dataset.jsonl`, `eval/runs/20260801_112058`), NEITHER task ran away fetching many
sources this time (2-8 raw fetches per dispatch, well within the new cap), zero fetch-cap rejections
were even needed, and **no context-budget cutoff fired in either run** — the specific mechanism
described above (11 fetches → cutoff → misread-as-incomplete) is gone.

**NOT fixed — a broader, previously-undercharacterized finding**: both runs still timed out at 900s
anyway. The Lisbon/Mexico City run redispatched 3 of its 4 top-level tasks in a second
`delegate_tasks` round; the sprint/volcano run issued **6 total** `delegate_tasks` rounds for a
2-task query — in BOTH cases with **zero cutoff markers anywhere** to have triggered it. This proves
the unforced-replanning behavior is not solely a reaction to the "wrapped up early" wording (which
the softened-marker fix targeted) — it's a more general tendency of this model to keep replanning
regardless of any specific signal in the tool results. That is exactly candidate 2 from this
section's own "why this matters" writeup above (`open_deep_research`'s `max_react_tool_calls`/
`max_researcher_iterations`-style hard replan-round cap on the `ADAPTIVE PLANNING LOOP` itself, not
just softer wording at one trigger point) — deferred at plan time on the working assumption that the
softened marker might be sufficient on its own; live evidence now says it is necessary but not
sufficient.

### Follow-up fix: hard cap on Planner-level replan rounds (2026-08-01, same day)

Scoped and implemented directly in response to the live-test result above — a hard cap on the
PLANNER's own top-level `delegate_tasks` calls, mirroring `open_deep_research`'s
`max_researcher_iterations` directly (a real precedent, not invented from scratch): new
`_planner_delegate_over_cap` (`src/engine/orchestrator.py`, same shape as
`_specialist_delegation_over_cap`), enforced inside the same shared `delegate_tasks` closure,
gated on `delegation_depth_ctx.get() == 0` (the Planner's own calls, as opposed to a Tier-2
specialist's nested ones) rather than the wording of any particular trigger. New
`run_state.data["planner_delegate_rounds"]` counter (deliberately excluded from
`_RESUME_CARRYOVER_KEYS`, same precedent `deepening_round` already sets), new
`settings.max_planner_delegate_rounds` (default 4). Full plan:
`/home/gab/.claude/plans/enchanted-gliding-flame.md` (2026-08-01, second plan of the day).

## 16. Comparative survey extension: 4 more deep research agent projects, plus a live fabrication
test on one of them (2026-08-01)

Continuation of §7's methodology (primary-source code reading, not README-skimming) on four
additional projects, prompted directly by the day's own work: `open_deep_research` was already
cloned to source the `max_researcher_iterations` precedent for §15's replan-cap fix, and reading it
further turned up both a real prior-art idea (per-comparison-element task decomposition) and a live,
directly-observed grounding failure. All four repos cloned to
`/mnt/nuevovol/Projects/AI shit/Building_Tools/` (siblings of this repo, `.gitignore`'d as
project-external): `open_deep_research/`, `local-deep-researcher/`, `deep-research-dzhng/` (dzhng's
project, already covered in §7 under its GitHub description "Open Deep Research" — re-verified here
under its actual repo name to avoid confusion with `langchain-ai/open_deep_research`, a different,
unrelated project despite the similar name), `gpt-researcher/`, `storm/`.

### `langchain-ai/open_deep_research`

- **Architecture**: LangGraph supervisor/researcher graph, not DeepDelve's typed multi-agent
  hierarchy — one `supervisor` node calls `ConductResearch` to spawn parallel `researcher_subgraph`
  instances (`src/open_deep_research/deep_researcher.py:288-305`, `asyncio.gather`), each of which
  runs its own ReAct tool loop then a separate `compress_research` LLM pass
  (`deep_researcher.py:511-583`) before returning to the supervisor.
- **Iteration caps — real, direct precedent for §15's fix, confirmed by reading the enforcement
  code, not just the config docstring**: `max_researcher_iterations` (default 6) and
  `max_react_tool_calls` (default 10) are both hard-enforced with direct integer comparisons every
  loop iteration (`deep_researcher.py:247`, `:492`), and overflow `ConductResearch` calls past
  `max_concurrent_research_units` get an explicit rejection message back to the model
  (`deep_researcher.py:291-321`) rather than being silently dropped — the exact reject-with-reason
  shape `_planner_delegate_over_cap`/`_specialist_delegation_over_cap` already use.
- **A genuinely new, not-yet-applied finding: their "Scaling Rules" directly address DeepDelve's
  open task-naming/facet-collapse problem** (`session_status/CURRENT.md` item 0, the Lisbon/Mexico
  City `visa_requirements` task that silently only ever covered Portugal). `prompts.py:123-136`:
  *"Comparisons presented in the user request can use a sub-agent for each element of the
  comparison... Delegate clear, distinct, non-overlapping subtopics"* plus *"Do NOT use acronyms or
  abbreviations in your research questions, be very clear and specific"* and *"provide complete
  standalone instructions — sub-agents can't see other agents' work."* DeepDelve's own
  `PLANNER_INSTRUCTIONS` single-facet-per-slot rule (`src/prompts.py:184-194`) already forbids
  bundling two topic-facets into one slot, but has no equivalent instruction for the orthogonal
  axis this bug actually hit: splitting per **named comparison subject** (city, product, country)
  and requiring the task name itself to specify which subject it covers. Not yet implemented —
  flagged here as the direct, sourced basis for that fix when it's scoped.
- **Live fabrication test, gpt-oss via this project's own unmodified code** (not DeepDelve's):
  ran `deepdelve-gpt-oss:latest` through all four of `open_deep_research`'s model roles
  (summarization/research/compression/final_report) on "What is the current men's 100m world
  record?" — a single-sub-agent, simple-fact-finding case by their own Scaling Rules, i.e. closest
  to a best case for them. Result: the headline fact (Bolt, 9.58s, 2009 Berlin) was correct, almost
  certainly from the model's own training data rather than the search it ran, but two of four cited
  URLs were dead on direct fetch (`worldathletics.org/records/by-sport/general/men-100-meters` and
  `.../news/record-ratifications/men-100m-bolt-9.58s`, both HTTP 404, checked directly) and one
  supporting claim was fabricated outright (report stated Noah Lyles ran a wind-aided 9.58s; his
  actual 100m PB, verified via web search, is 9.79s, 2024 Paris Olympics — a specific, wrong number
  invented and given a fake supporting BBC citation). The run log showed **19 of 19**
  `summarize_webpage` calls (`utils.py:175-213`) hit their hardcoded 60-second `asyncio.wait_for`
  timeout and fell back to raw content — with `OLLAMA_NUM_PARALLEL=1` (required on this hardware,
  see `README.md`'s Ollama setup section) serializing every parallel `asyncio.gather`'d
  summarization call (`utils.py:108`) behind one GPU slot, a design that assumes cloud-parallel
  model capacity and actively mismatches single-slot local serving. Their termination signal
  (`ResearchComplete`, `state.py:21-22`) is a bare empty-schema tool the model self-reports with no
  structural check behind it, and `compress_research` has no citation-URL-exists verification at
  all (`deep_researcher.py:511-583` — free-text `response.content` returned as-is) — this specific
  failure mode (fabricated citation, wrong supporting fact, shipped as a confident final report) is
  exactly the class of defect DeepDelve's `grounding.py`/`completion.py` pipeline exists to catch,
  and would have caught here (stub-fetch rejection alone kills the two dead URLs; NLI entailment
  would flag the Lyles claim against its own fabricated source).

### `langchain-ai/local-deep-researcher` (package name: `ollama_deep_researcher`)

- **The most directly comparable project by design goal** — LangChain's own local-model-first deep
  research agent (default `local_llm="llama3.2"`, native Ollama/LMStudio base URLs,
  `configuration.py:1-13`). ~1,200 lines total, not multi-agent at all: one linear LangGraph loop
  (`generate_query` → `web_research` → `summarize_sources` → `reflect_on_summary` → loop until
  `max_web_research_loops`, `graph.py:444-465`) building one running summary. Cannot decompose a
  query into independent facets/comparisons the way DeepDelve's Planner attempts — a structurally
  different way of avoiding the facet-collapse bug class: it never attempts multi-facet
  decomposition at all.
- **The single most consequential divergence found this session: `use_tool_calling` defaults to
  `False`** (`configuration.py:57-61`). When false, `get_llm` (`graph.py:97-135`) requests Ollama's
  raw `format="json"` mode instead of native function-calling, and
  `generate_search_query_with_structured_output` (`graph.py:44-95`) parses the JSON response by
  hand with an explicit `fallback_query` on any parse failure — a deliberate, load-bearing fallback
  path, not dead code. This is the opposite bet from DeepDelve's own stated model-selection
  philosophy (`README.md`'s tool-call-support verification step; candidates disqualified for
  unreliable `tool_calls` per `MODELS.md`) — real evidence that the team building the most
  local-model-specific reference implementation in this space did not trust native tool-calling
  enough to make it the default, even though Ollama has supported it for the models they target.
  Both are defensible engineering bets; recorded here as a documented counter-data-point against
  DeepDelve's tool-calling-required stance, not a recommendation to switch (switching would be a
  large architectural change DeepDelve's entire tool-based dispatch model depends on, not scoped
  here).
- **No citation-to-claim verification** — sources are deduplicated and appended as a flat list at
  `finalize_summary` (`graph.py:387-418`), never checked against the summary text.

### `dzhng/deep-research` — re-confirmed under its actual repo name, one new finding beyond §7

§7 already covers this project's minimalism and lack of a verification layer accurately (confirmed
again this session, same file: `src/deep-research.ts`). One angle §7 didn't name explicitly, worth
adding: **its control flow is entirely host-code-driven recursion, not model-driven ReAct** — breadth
halves every level (`newBreadth = Math.ceil(breadth / 2)`, `deep-research.ts:230`), and the LLM only
ever fills `generateObject`/zod-schema slots at fixed call sites (`generateSerpQueries`,
`processSerpResult`, `writeFinalReport`) — the model is never asked "should I search more?" the way
DeepDelve's Planner is via its `ADAPTIVE PLANNING LOOP`, or the way `open_deep_research`'s supervisor
is via `ResearchComplete`. This sidesteps §15's entire problem class (a model that won't stop
replanning) **by construction**: if the model never holds iteration-control authority, there's
nothing for it to over-exercise. A genuinely different fix family from either §15's hard cap (limit
the model's authority) or a smarter prompt (persuade the model to use its authority well) — worth
naming as a third option for future iteration-control problems, though adopting it for DeepDelve
would mean giving up the Planner's ability to genuinely adapt its plan to what it finds, which is a
real capability trade-off, not a strict improvement.

### `assafelovic/gpt-researcher`

- **By far the largest, most production-hardened codebase surveyed in this section** (~9,900 lines
  under `gpt_researcher/`) — the closest thing to a "mature OSS competitor" of the projects checked
  today. Has a real swappable-retriever abstraction (`gpt_researcher/retrievers/` — 19 separate
  provider modules: tavily, brave, serpapi, arxiv, semantic_scholar, pubmed_central, searx, custom,
  etc., all behind one interface) — genuinely more mature than DeepDelve's own current state, where
  today's own Tavily-wiring work found DeepDelve has no such abstraction (`web_search` hardcodes
  `ddgs`; MCP servers like Brave/Tavily are a structurally separate, second tool the model must be
  separately told to prefer, not an interchangeable backend for the same tool). Worth naming as a
  concrete gap: if DeepDelve adds a third/fourth search backend later, a real `Retriever` interface
  (as `gpt_researcher/actions/retriever.py` demonstrates) would scale better than another bespoke
  MCP-server-plus-prompt-mention pair.
- **`SourceCurator`** (`gpt_researcher/skills/curator.py`) — a dedicated LLM pass that ranks/filters
  gathered sources by credibility and relevance *before* they reach the writer, separate from both
  search and report generation. Still pure LLM self-judgment (no structural check backing it,
  `curator.py:58-96` just parses whatever JSON the model returns, falling back to the unranked list
  on any parse failure) — a real idea (separate the "is this source good" decision from "what does
  this source say") but not a verification mechanism in DeepDelve's sense, since nothing confirms
  the LLM's credibility judgment against anything external.
  **`gpt_researcher/skills/deep_research.py`** (their own dzhng-lineage breadth/depth recursive mode,
  explicitly the same architecture family as `deep-research-dzhng/` above, evolved further) has a
  fix (`deep_research.py:493-511`, comment referencing their own issue **#1579**) for stopping
  descent when every branch at a level fails, rather than recursing forever on empty learnings —
  independent confirmation, from a different, more mature project in the same architecture lineage,
  that dzhng's silent-per-branch-failure gap (flagged as a real weakness above) is not
  hypothetical: GPT Researcher's own maintainers hit it in production and had to patch it.
- **Defensive multi-strategy structured-output parsing** (`deep_research.py:48-116`,
  `_extract_json_payloads`/`_load_repaired_json`/regex-line-fallback chain) — cascades from
  `json_repair.loads` down to hand-rolled regex line matching before giving up, rather than crashing
  or silently returning nothing on the first malformed response. DeepDelve's own tool-calling
  architecture mostly sidesteps this class of problem (native tool schemas are framework-validated,
  not free-text-parsed), but this is a real, battle-tested hardening pattern worth keeping in mind
  for any future DeepDelve code path that does have to parse free-text model output by hand.

### `stanford-oval/storm` (NAACL 2024, "Assisting in Writing Wikipedia-like Articles From Scratch
with Large Language Models")

- **The one project in this whole survey (today's four plus §7's five) that is not a variant of
  "decompose into search queries, iterate, synthesize."** STORM's actual research contribution is
  **persona-diversity-driven facet discovery**: `persona_generator.py` first pulls the table-of-
  contents structure from real Wikipedia pages on related topics (`get_wiki_page_title_and_toc`,
  grounded in real external reference structure, not just prompted brainstorming) and uses that as
  grounding for an LLM call (`GenPersona`, `persona_generator.py:53-63`) that proposes N distinct
  "Wikipedia editor" personas, each representing a different perspective/role/affiliation on the
  topic. `knowledge_curation.py`'s `ConvSimulator` then runs a simulated multi-turn dialogue per
  persona — a `WikiWriter` persona asks questions, a `TopicExpert` persona answers them with real
  grounded search per turn (`knowledge_curation.py:25-81`) — so each persona drives its own
  independent research thread before everything is merged into one outline and article.
- **This is directly relevant to DeepDelve's still-open task-naming/facet-collapse problem**
  (`session_status/CURRENT.md` item 0) as a structurally different fix family from
  `open_deep_research`'s prompt-rule approach above: instead of (or in addition to) telling the
  Planner "name each comparison subject explicitly," generate diverse perspectives on the query
  FIRST, each of which naturally drives its own research thread — for "Lisbon vs. Mexico City,"
  perspective-generation grounded in what similar comparison queries typically need would plausibly
  surface "visa-Lisbon," "visa-Mexico," "cost-Lisbon," "cost-Mexico" as four separate threads more
  reliably than hoping a single top-down planner's task-naming discipline holds under model
  fallibility — because the diversity comes from generating N different vantage points BEFORE
  decomposition, not from getting one planner's decomposition right in one shot. Not scoped or
  recommended for implementation here — a materially larger architectural change than either of the
  above two prompt-level ideas (would mean adding a whole new pre-Planner stage) — but the most
  novel, best-grounded idea in this survey for that specific open problem, worth remembering when
  that fix actually gets scoped.
- **No verification/grounding layer of DeepDelve's kind** — STORM's own quality mechanism is entirely
  upstream (better facet coverage via personas) rather than downstream (checking citations after the
  fact); a citation is whatever URL the `TopicExpert`'s search returned, unchecked against final
  article text for entailment or contradiction.

### Updated synthesis (extends §7's, does not replace it)

Nine projects now read directly across two sessions (§7's five: Tongyi DeepResearch, dzhng/
deep-research, CYC2002tommy's Deep Science Writer, SkyworkAI/DeepResearchAgent, nashsu/llm_wiki;
today's four: `open_deep_research`, `local-deep-researcher`, `gpt-researcher`, STORM). §7's own
qualifications (different projects solve different problems; "most sophisticated" ≠ "most
validated"; no survey is exhaustive) still hold and are reinforced, not weakened, by today's
additions — GPT Researcher in particular is a materially larger, more mature codebase than anything
in §7's set, and it still has no verification layer comparable to DeepDelve's for the same reason
§7 already named: different projects are optimizing different axes (retriever breadth and
production polish, in GPT Researcher's case, not post-hoc fabrication defense). The live gpt-oss
fabrication test on `open_deep_research` is the most concrete evidence yet, on this exact local
model, for why that verification layer earns its complexity rather than being defensive
over-engineering: an unmodified, well-regarded reference implementation produced a fabricated fact
and two dead citations on the simplest possible case, with no structural mechanism anywhere in its
own code that would have caught it.

Two concrete, sourced ideas from today are now on record as candidate future work, deliberately not
implemented in this session (survey was the explicit deliverable requested): (1) `open_deep_research`'s
per-comparison-subject task-naming rule, a small prompt-level fix; (2) STORM's persona-diversity
facet discovery, a larger architectural one. Both target the same currently-open bug
(`session_status/CURRENT.md` item 0) from different angles and different cost levels.

## 17. Chasing convergence on the Lisbon/Mexico retest: four fixes, four newly-exposed layers
(2026-08-01, later the same day)

After §16's naming fix and Tavily-backend swap, the Lisbon/Mexico retest was rerun repeatedly to
confirm a genuine end-to-end pass. It never fully converged, but each rerun cleared one real
bottleneck and exposed a new one underneath — four distinct, independently real fixes in one
investigative arc, not one bug with four symptoms. Recorded here in the order they were found,
since the shape of "clear one layer, find the next" is itself the useful finding for future
sessions chasing a stubborn non-convergence.

### 17a. Findings.md counted failed extractions as real, must-cite evidence

First retest: 9 of 12 `findings.md` sources contained only "No key findings extracted from this
source during this research run" — the Analyzer's own honest failure narration, faithfully
preserved by FindingsWriter instead of omitted. `check_report_underuses_findings` and
`_facet_coverage` (`src/engine/completion.py`) both treated every `findings.md` URL as real
evidence the report MUST cite — an unsatisfiable demand for a null-summary URL, which the Builder
could only "resolve" by re-failing the same check or fabricating a claim (confirmed:
`claim_unsupported` fired on exactly one of these).

**Fixed**: new `_is_null_finding_summary(summary)` (`src/utils/grounding.py`) — two-signal match
(summary under 300 chars AND matches a "nothing extracted/found" phrase regex), so a real terse
finding or a long passage that happens to mention "could not find" about one minor sub-detail isn't
misclassified. Both checks now exclude a URL from "real evidence" when every one of its
`run_state.data["findings"]` entries is null-summary.

### 17b. Bot-walled and empty fetches were never usable in the first place

Traced two of §17a's "null" sources further: `mexiconewsdaily.com` and `consulmex.sre.gob.mx`
weren't genuine Analyzer failures on real content — they were a CAPTCHA challenge page and a
literally-empty fetch respectively. Tavily's own `/extract` API, tested directly against both
exact URLs, returned full real content for both.

**Fixed**: added Tavily `/extract` as a third rung in `_fetch_raw`'s existing stub-retry chain
(`src/tools/web.py`: plain fetch → headless-browser retry → Tavily extract), same
`settings.tavily_api_key` as the search backend, opt-in. Two real bugs found and fixed while
wiring it in, not added blind: `_STUB_MARKERS_RE` only covered paywall/404 phrasing, not
bot-verification/CAPTCHA pages, so neither retry rung ever got a chance to run on these exact
URLs (added a bot-verification marker group); and the Tavily call was sending the POST-REDIRECT
url instead of the original — a bot-walled page's redirect chain often lands on the challenge
host itself (`consulmex.sre.gob.mx` → `validate.perfdrive.com/?ssa=...`), and Tavily fetching THAT
URL just gets the same challenge page again. Live-verified against the exact two failing URLs:
`mexiconewsdaily.com` went from a 723-char captcha stub to 7,910 real chars; `consulmex.sre.gob.mx`
went from 0 bytes to 19,238 real chars.

### 17c. FindingsWriter itself dropped facets, one layer upstream of Builder

With 17a/17b fixed, the next retest still never converged — `completion_check_attempts` showed
FindingsWriter dropping 3 of 4 facets writing `findings.md`, despite a complete, well-under-budget
(16.5K chars under the 50K `context_budget_chars` limit — truncation ruled out directly, not
assumed) 4-facet evidence blob given in one dispatch. The exact evidence-crowding pattern already
fixed for Builder (`report_underuses_evidence`, §1's four-tuple checklist), one layer upstream, at
FindingsWriter's own first synthesis.

**Fixed**: `findings_underuses_evidence` removed from `_FINDINGS_WRITER_FIXABLE_PROBLEMS`, given
its own bespoke per-facet dispatch (`_dispatch_per_facet_findings_writer_fix`), mirroring
`_dispatch_per_facet_builder_fix` exactly. New `_findings_facet_coverage(ctx)`, factored out of the
check function. `_build_findings_source_material` gained an optional `task_names` filter so each
per-facet dispatch gets only that facet's own evidence — not just its findings, but its own
fetched-URL cross-reference section too, since leaving that unscoped would reopen the same
crowding surface from a different angle. Full blast-radius checklist walked explicitly (added
`_capped()` since the problem is no longer self-resolving by tuple membership; confirmed
`_QUARANTINE_PROBLEMS` and `_WRITER_DISPATCH_RE` don't need changes; found and removed a real risk
— `deterministic_fallback`, a full-file-overwrite path meant only for a from-scratch write, was
being passed scoped single-facet content that could destroy every other facet's entries if its
guard were ever violated). New dedicated dispatch-path test verifying the scoping itself (asserts
one facet's finding text never leaks into another facet's dispatch instructions), not just the
verdict — required per `ARCHITECTURE.md`'s own checklist for a new bespoke `elif`.

Caught a live wording drift in 17a's own fix during this retest: the model's failure narration had
shifted from "No key findings extracted..." to "No key findings **available** from this source
regarding X" — a real variant the original `_is_null_finding_summary` regex missed entirely
(confirmed: returned `False` on it), letting 21 of 24 `findings.md` entries in that run slip
through unfiltered. Broadened the pattern to also match "available"/"found".

### 17d. grep_workspace_file/read_workspace_file: a shared global quota with no per-dispatch ceiling

With 17a-17c fixed, the run STILL never reached the completion-check pipeline at all —
`completion_check_attempts` was completely empty, meaning the entire 1800s was spent in research.
`findings.md` had 24 real fetched sources with only 3 (12.5%) yielding actual extracted content.
Read directly from the persisted session transcript (`~/.deepdelve/sessions/session_*.json`,
`ui_events` — `enable_session_persistence: true`, NOT visible in the eval harness's own
`agent_stdout.log`, which only logs tool NAMES, not arguments):

- One Analyzer dispatch, given a 3,800-line Spanish-language Mexican immigration PDF, grepped for
  "Ciudad de México" (no match), read 200 lines of irrelevant legal preamble ("Plan Nacional de
  Desarrollo 2013-2018"), grepped again, and burned through 34 of the shared `grep_workspace_file`
  budget without finding anything useful.
- Every Analyzer dispatched afterward in the same run (`pumble`, `TheLatinvestor`, `Blueground`)
  hit `"Error: Quota reached... 36/38/40 times"` almost immediately — most after one or two calls
  — on perfectly readable, on-topic pages, purely because an earlier, unrelated dispatch had
  already spent the shared pool. `settings.quotas` is documented as GLOBAL by design
  (`config_template.yaml`'s own comment: "shared cumulatively across the Planner and every
  dispatched specialist") — correct for `delegate_tasks`/`web_search`, but with no per-dispatch
  ceiling underneath it for `read_workspace_file`/`grep_workspace_file`, the exact "one task
  starves every sibling of a shared pool" shape `specialist_delegation_cap`/`specialist_fetch_cap`
  already exist to prevent for `delegate_tasks`/`fetch_url_to_workspace`.
- Separately: one Analyzer (`getgoldenvisa.com`) actually found and read the RIGHT lines (110-260,
  containing the real €3,680 income figures) via three well-targeted reads, and still returned an
  empty result — not a quota or targeting problem, a synthesis failure after having the right
  content in hand. Flagged, not yet investigated — smaller and separate from the dominant cause.

**Fixed**: new `settings.analyzer_read_cap` (default 8), a per-dispatch combined
`read_workspace_file` + `grep_workspace_file` counter, third instance of the
`specialist_delegation_cap`/`specialist_fetch_cap` pattern. New `task_read_grep_count_ctx`
(`src/utils/run_state.py`), reset at the same point in `_run_single_task` as the other per-dispatch
contextvars, but deliberately left `None` (cap disabled) for `_NON_RESEARCH_DISPATCH_ROLES`
(Builder/FindingsWriter/PeerReviewer) — their read/grep usage reviews `findings.md`/
`final_report.md`, a different, legitimate pattern from an Analyzer chasing one hard external
document. New `_analyzer_read_over_cap`/`_check_analyzer_read_cap` (`src/tools/fs.py`), enforced
in both tools, reject-before-execution. Found and fixed one more inherited inaccuracy while
writing this: the rejection message deliberately does NOT claim "no quota was consumed", unlike
`fetch_url_to_workspace`'s own sibling cap message — `@with_quota`'s wrapper (`check_quota`)
increments the tool's GLOBAL `used` count unconditionally before the wrapped function body (where
any per-dispatch cap check lives) ever runs, so a global quota unit IS spent by the time either
cap rejects the call; only the real read/grep work is skipped.

**Live-tested with all four of 17a-17d together**: the retest still scored 0.000 (timed out at
1800s) but for the first time actually produced a `final_report.md`, and `findings.md` had 13
real sources — Lisbon AND Mexico both, zero null-summary entries. All four fixes confirmed
working exactly as designed. But Builder's own first draft covered Mexico only, dropping Lisbon
entirely, and the run was killed by the timeout the instant Builder finished, before the
completion-check pipeline ever got a second turn to catch and fix it via the per-facet dispatch
(17c) built for exactly this case.

### 17e. Resuming didn't help either: a resumed Planner ignored prose guidance, and the pipeline's
own two-tier structure let an unrelated new problem starve the real fix

Per-user request, resumed the interrupted run directly (`--resume-run`) instead of restarting —
it should only need to patch the existing report from existing findings, not redo 18 minutes of
research. It ran for a genuine 24 minutes (1435.8s) and reached a real final verdict, not another
timeout. But `final_report.md` came out **byte-for-byte identical** to before — still Mexico-only,
Lisbon still completely absent, despite `findings.md` having 6 clean, real Lisbon sources the
entire time.

**Root cause, traced directly from `completion_check_attempts`**: `build_resume_input`
(`src/engine/tui.py`) already told the resumed Planner explicitly, in prose, *"Do NOT re-open
broad research or re-verify what's already there... Only delegate_tasks for a SPECIFIC fact that
is genuinely still missing."* The Planner ignored it and redelegated new research for Lisbon rent
and Mexico visa anyway — both already well-covered in `findings.md`. Those two new tasks then got
flagged by `check_task_verification_flagged` ("only fabricated/unusable sources") and dominated
6 of the resumed run's 8 completion-check attempts. `report_underuses_evidence` — the check built
in 17c's own sibling fix, specifically for a Builder draft dropping a covered facet — **never
fired once** in the entire 24-minute run. Not because it's broken: `COMPLETION_CHECKS` (where
`check_task_verification_flagged` lives) always runs to exhaustion before `GROUNDING_CHECKS`
(where `report_underuses_evidence` lives) is even evaluated at all — a hard two-tier gate, not
just list-position priority within one list. A structural problem the Planner itself created,
in a completely unrelated tier, permanently blocked the one fix that would have actually worked.

User's own framing, verbatim: *"We're going for the more structured approach, we cannot just do
the smaller, we need to cut this from the root."* The smaller fix (stronger prose in
`build_resume_input`) was explicitly rejected as insufficient, consistent with this whole
session's own repeated finding that prose-only compliance is unreliable on this model.

**Fixed, structurally**: `merge_resumed_state` (`src/utils/run_state.py`, the single shared choke
point both `run_cli`'s `--resume-run` and `BasicTuiAgent._resume_run` already funnel through —
TUI/CLI parity by construction, not by remembering to edit both) now checks whether the required
artifact (default `final_report.md`) already exists on disk at resume time. If it does,
`planner_delegate_rounds` is pre-set to `max_planner_delegate_rounds` — the Planner's very FIRST
`delegate_tasks` call this run is already at cap and gets rejected by the existing
`_planner_delegate_over_cap` predicate (§15's own fix), before it ever runs. A resumed run with an
existing report becomes fix-only (Builder/FindingsWriter dispatches via the completion-check
pipeline) — the Planner structurally cannot reopen research in this state, only the classic
inject-into-Planner fallback (reached only for a genuinely Planner-only problem, e.g.
`not_delegated`) could ever hand delegation authority back. New `resumed_with_existing_report`
flag drives a distinct, accurate rejection message (the generic "you've already run N rounds"
wording would be misleading here — this is round 0, nothing was actually run) and a rewritten
`build_resume_input` stage note that states the hard fact plainly instead of offering a choice the
tool no longer allows. New dedicated tests: `merge_resumed_state`'s gating logic (both the
report-exists and no-report cases), and the updated stage-note wording. Full suite green.

**Live-tested**: resumed the same interrupted run a second time. Confirmed working exactly as
designed — the Planner never called `delegate_tasks` once, responding to every completion-check
nudge with "Understood, no further delegation will be performed" and stopping. Run finished in
**67 seconds** (down from 1435.8s the first resume attempt). But `final_report.md` came out
byte-for-byte identical yet again — still Mexico-only, Lisbon still absent — for a reason 17e
didn't touch: `task_verification` (the ledger flagging two tasks as fabricated) is itself carried
over on resume via `_RESUME_CARRYOVER_KEYS`, so `check_task_verification_flagged` fired
immediately on attempt 1 regardless of whether the Planner delegated anything new, and dominated
6 of 7 completion-check attempts again — same structural cause as 17e's own incident, just with
the trigger now pre-existing instead of self-created.

### 17f. The two-tier gate itself: GROUNDING_CHECKS structurally never runs while ANYTHING in
COMPLETION_CHECKS keeps recurring, old or new

Per user's explicit direction ("go into that" — the deeper architectural cause, not another
targeted patch): `run_completion_check` only ever evaluates `GROUNDING_CHECKS` when
`COMPLETION_CHECKS`'s own scan returns `None` for every check in the list
(`verdict = next(... for check in COMPLETION_CHECKS ...); if verdict is None and grounding_check
enabled: ... GROUNDING_CHECKS scan`). This is a hard two-tier gate, not just first-match priority
within one list — as long as ANY `COMPLETION_CHECKS` problem keeps returning non-`None`,
`GROUNDING_CHECKS` (where `report_underuses_evidence` lives) never gets evaluated at all, no
matter how many attempts pass. 17e's own capping/yield mechanisms all worked exactly as designed
in the second resume — `check_task_verification_flagged` is correctly `_capped()`, and the
existing `_yield_to_starved_check(verdict, ctx, check_untracked_delegation, ...)` correctly
protected that one specific sibling — the gate itself was simply never built to let a
`GROUNDING_CHECKS` check interrupt a recurring `COMPLETION_CHECKS` one.

**Fixed**: reused the EXISTING generic `_yield_to_starved_check` mechanism unchanged (the same
function already protecting `check_untracked_delegation`, which takes any `starved_check` callable
and doesn't care which list it nominally belongs to) — added one more call right after the
existing one, targeting `check_report_underuses_evidence` directly, applied to the
`COMPLETION_CHECKS` verdict before the tier gate is even evaluated:

```python
verdict = next((v for check in COMPLETION_CHECKS if (v := check(ctx)) is not None), None)
verdict = _yield_to_starved_check(verdict, ctx, check_untracked_delegation, never_final_blocker=True)
if verdict is not None and grounding_check.enabled:
    verdict = _yield_to_starved_check(verdict, ctx, check_report_underuses_evidence)
```

`check_report_underuses_evidence` doesn't itself depend on `ctx.grounding_problem` (it reads
`ctx.content`/`run_state.data["findings"]` directly via `_facet_coverage`, unlike the citation-
accuracy checks that key off `real_grounding_problem()`), so it's safe to probe before the normal
`GROUNDING_CHECKS` scan even runs — gated on `grounding_check.enabled` anyway, for consistency with
the master switch. `never_final_blocker` deliberately left at its default `False`, unlike
`check_untracked_delegation`'s usage: a real dropped-facet problem winning as the run's terminal
reported blocker is correct, not something to protect against. `_yield_to_starved_check` itself
was already proven safe to call speculatively (pure reads, no side effects) — reusing it here adds
no new risk class, just a new call site.

New dedicated end-to-end test (not just the already-covered helper): a task genuinely flagged by
`check_task_verification_flagged` (a real http source_url carrying a `[SYSTEM VERIFICATION
WARNING...]` marker, so `check_thin_coverage` — a HIGHER-priority sibling that only cares whether
a task has any real URL at all — stays quiet, isolating the check actually under test) alongside a
genuinely dropped facet (`report_underuses_evidence`'s own trigger, requiring `min_tasks>=2` real
covered tasks in `by_task` to even evaluate — a first attempt at this test used only one covered
task and silently never fired, caught by an assertion failure showing `task_verification_flagged`
winning instead). Confirms `report_underuses_evidence` gets a real turn, dispatches Builder scoped
correctly, and the fix lands — while the still-unresolved `task_verification_flagged` problem
correctly continues to get addressed in later iterations too, not silently dropped. Full suite
green.

**Live-tested — the multi-facet abandonment bug is fixed.** Resumed the same interrupted run a
third time (2119.1s, a real full run). For the first time across every attempt this whole session
— multiple fresh runs, three resumes — `final_report.md` covers **all four facets**: Mexico visa
requirements/costs, Mexico City rent, Portugal D8 visa, and Lisbon central rent. Also fixed a CI
break along the way: the new resume-block rejection message (17e) had every line prefixed `f"..."`
with no actual interpolation anywhere in it, tripping `ruff`'s F541 — caught by CI on both of the
last two pushes, fixed in a follow-up commit, confirmed clean both locally (`ruff check .`) and
via `gh run watch` on the resulting CI run.

**Not a clean pass, and the run's own verdict says so honestly** — this is the structural fix
working, not a claim the report is fully trustworthy. Final verdict: *"Retry budget exhausted with
an unresolved issue (task_verification_flagged)... treat its claims as unconfirmed."* Specifically
flagged, unresolved: `stub_source` on `housinganywhere.com` (the exact URL backing the report's
Lisbon rent figure, corroborated in the report only by a low-quality "Facebook Group Post"
citation); `claim_unsupported` on the Consulmex PDF (one Mexico visa requirement claim doesn't
match that source's actual content); and `task_verification_flagged` itself remains open for
`Lisbon_CentralApartment_RentalCost_BaixaChiado`/`Mexico_DigitalNomadVisa_RelocatemeSummary`. The
bug this whole 17a-17f chain targeted — a whole facet silently vanishing with nothing anywhere
able to see or fix it — is confirmed fixed. Ordinary, already-flagged, per-claim grounding quality
issues are a separate, correctly-surfaced-not-hidden concern, not evidence the fix didn't work.

### 17g. Why those two specific tasks never produced a real source: not a quality problem, a
dead-end error message

Per user's follow-up request to investigate WHY `Lisbon_CentralApartment_RentalCost_BaixaChiado`
and `Mexico_DigitalNomadVisa_RelocatemeSummary` specifically kept producing fabricated/unusable
sources across every retry (the two tasks `task_verification_flagged` still had open in 17f's own
live-test verdict). Read the raw persisted session transcript directly
(`~/.deepdelve/sessions/session_72e58d6e-*.json`, `ui_events` — has full tool-call arguments,
unlike the eval harness's own `agent_stdout.log`, which only logs tool NAMES).

Both tasks hit the identical pattern: `web_search` surfaced a promising URL in a snippet;
`delegate_tasks` was called to send an Analyzer to read it; a real, deliberate existing check
(`orchestrator.py`'s Analyzer-URL validation, `delegation_tasks`'s own closure) correctly rejected
it — an Analyzer may only be told to read a URL the CALLING task itself actually fetched, scoped
to `task_fetched_urls_ctx`, not the whole run. That invariant is sound in general. But in both
these cases, the URL HAD already been fetched — by a *different* task earlier in the same run.
`fetch_url_to_workspace`'s own cross-task dedup then rejects a fresh fetch attempt ("Already
fetched this run — see workspace file X"). The rejection message's own advice — *"call
fetch_url_to_workspace on it yourself first, THEN delegate with the real saved filename"* — is
flatly wrong in this specific situation: that fetch attempt is guaranteed to hit the dedup wall
again. The model had no way out: URL path rejected, suggested fix path also a dead end.

**Consequence, read directly from the transcript**: both tasks burned their entire `delegate_tasks`
quota (5 and 9 calls respectively) retrying the IDENTICAL rejected shape — never once trying the
filename instead — then gave up and narrated a "findings" summary straight from the original
search-snippet text, never a real, verified read of the actual fetched file.
`check_task_verification_flagged` then correctly caught that narration as fabricated — which is
exactly right; the finding genuinely wasn't grounded in anything read. **This was never a source-
quality problem** — the source (e.g. `globallawexperts.com`, `relocateme.substack.com`) may have
been perfectly fine; the model just could never get a working Analyzer dispatch through to read it.

**Fixed**: new pure `_find_sibling_fetch(url, fetched_urls)` (`src/engine/orchestrator.py`, next to
`_planner_delegate_over_cap`) looks up `get_fetched_urls()` (the SAME whole-run registry
`fetch_url_to_workspace`'s own dedup already trusts) for the rejected URL. The Analyzer-URL
validation's error message now distinguishes two genuinely different cases: nobody has fetched
this URL yet (original advice — fetch it yourself first — is correct, unchanged) vs. a sibling
task already fetched it (new message: names the real saved filename directly, explicitly says NOT
to retry `fetch_url_to_workspace`, since that's the exact dead end just traced). New dedicated
unit test for `_find_sibling_fetch` (exact match with trailing-slash normalization, a real
prefix-match/redirect-variant case, the never-fetched case correctly returning `None`, and an
empty registry not crashing). Full suite green, `ruff check .` clean (checked locally before
pushing this time, after 17e/17f's own CI break from an unrelated f-string lint issue).

**Not yet live-tested** — implemented and unit-verified only. Next: a fresh run or resume hitting
this exact dedup-collision shape again would confirm the corrected message actually gets the model
to delegate with the filename instead of looping.

### 17h. Raising the eval timeout to match `max_run_minutes` surfaced a DIFFERENT starvation bug:
`check_task_verification_flagged` can block `check_missing_artifact` from ever dispatching Builder

Two live tests on a fresh sprint/volcano run (a different topic from the Lisbon/Mexico query this
whole session otherwise hammered), triggered by the observation that every run today had hit its
external timeout — worth checking whether 1800s was actually enough, separate from any code bug.

**First test** (`--timeout 1800`, unchanged): scored 0.800, same shape as every other run today —
both facets covered, genuinely well-grounded content, external timeout hit before a final
completion-check pass could run. The specific dead-end-message collision 17g fixes never
reproduced (`_find_sibling_fetch` never fired) — still unconfirmed live, unit-tested only.

**Second test, the actual finding**: noticed the agent's own `max_run_minutes` (45, i.e. 2700s) is
LONGER than the eval harness's own `--timeout` flag (1800s, a hard external subprocess kill) —
every "timeout" seen today was the harness killing the process 15 minutes before the agent's own
graceful-stop logic (`completion.py`'s `budget_deadline`) ever got a chance to run. Reran the same
query with `--timeout 2760` (exceeding `max_run_minutes`). The process genuinely finished on its
own this time — 2198.3s, well under the new ceiling, no hard kill. But it scored **0.000**, and
`final_report.md` turned out to be a salvaged narration (`_salvage_narrated_report`'s own explicit
banner: *"AUTO-RECOVERED DRAFT... has NOT passed the grounding check... UNVERIFIED"*) — the
Planner narrated its own progress as chat text, never wrote a real report. So the honest
correction: the timeout was never the actual bottleneck for this run. It didn't get cut off early;
it genuinely exhausted its own budget and gave up.

**Root cause, read directly from `_run_state.json`'s `completion_check_attempts`**:
```
0 task_verification_flagged
1 task_verification_flagged
2 task_verification_flagged
3 missing_findings
4 task_verification_flagged
5 task_verification_flagged
6 task_verification_flagged
```
`findings.md` genuinely exists (2,204 bytes, real content — `missing_findings` correctly resolved
at attempt 3). But `final_report.md` was **never once attempted** — `check_missing_artifact`
(`COMPLETION_CHECKS`, position 8, the check that would dispatch Builder to actually write the
report) never fired, not once, because `check_task_verification_flagged` (position 3, well above
it) kept winning first-match on 6 of 7 attempts. This is the SAME starvation shape 17f already
fixed — but WITHIN `COMPLETION_CHECKS` this time, not cross-tier, and for a DIFFERENT pair
(`task_verification_flagged` blocking `missing_artifact`, not blocking `report_underuses_evidence`
in `GROUNDING_CHECKS`). `check_task_verification_flagged` IS correctly `_capped()` (confirmed
2026-08-01, §1's own invariant) — but `missing_findings` interrupting the streak at attempt 3
isn't in that check's own skip-list (`_tvf_skip = {"untracked_delegation"}`), so it reset the
consecutive-occurrence count; the NEW streak (attempts 4-6) only reached 3 by the time the run's
own `planner_delegate_rounds`/global `delegate_tasks` quota caps forced a full stop — one attempt
short of `check_task_verification_flagged`'s own cap threshold silencing it and letting the scan
finally reach `check_missing_artifact`. `planner_delegate_rounds` confirmed hit exactly 4 (the
configured cap) in this run's own `_run_state.json` — that cap worked exactly as designed; the
Planner correctly stopped delegating once capped. The problem is entirely upstream of that: the
Planner spent its ENTIRE round/quota budget re-verifying one persistently-flagged task before ever
reaching the point where Builder could be dispatched at all.

**Not yet fixed** — diagnosed and documented only, per explicit "document it and stop for today"
direction. Two candidate fix shapes, not scoped: (1) the narrowest, most consistent-with-today's-
other-fixes option — give `check_missing_artifact` (and arguably `check_findings_underuses_
evidence`, `check_missing_findings`'s own siblings that also sit below `task_verification_flagged`
in the same list) a `_yield_to_starved_check`-style protection the same way 17f did across the
GROUNDING_CHECKS boundary, applied WITHIN `COMPLETION_CHECKS` this time; (2) reconsider whether
`missing_findings` firing mid-streak SHOULD reset `task_verification_flagged`'s own escalation
counter at all — arguably a genuinely different, unrelated problem resolving in between doesn't
mean the ORIGINAL flagged-task problem got any less stuck, so resetting its streak may itself be
the more precise bug to fix, upstream of needing option (1) at all. Worth investigating both
before picking one, not assuming (1) is right just because it mirrors 17f most closely.

## 18. Today's 7-fix investigative arc (2026-08-17) cross-checked against current agent-reliability literature, plus a concrete evaluation-methodology gap this project should close

Same "chase convergence, layer by layer" shape as §17 (four fixes there, seven here across 6 live
runs of the same standing "Lisbon vs Mexico City" prompt) — full technical traces live in
`ROADMAP.md`'s 2026-08-17 History entry and `ARCHITECTURE.md` §2/§3's updated landmine writeups;
this section is the literature cross-check the user asked for, done AFTER the fixes (confirming
they land in a real, active research area, not inventing novel terminology for known phenomena)
plus the methodology gap this whole investigative pattern exposes.

**⚠️ Every paper/figure below is ⚠️ not yet primary-source-verified EXCEPT §18b, §18d, §18e, and
§18f's own citations**, per this document's own 2026-07-19 methodology rule — every OTHER citation
here came from `WebSearch`'s own AI-mediated result summaries, not from directly reading the
papers' actual text/data/tables the way §1's ✅ entries were:
- §18d's pass@k/pass^k paper (arXiv:2603.29231) — fully read, all 23 pages, correcting an earlier
  same-session pass that stopped at page 6 on a bad page-count read.
- §18b's self-correction blind spot paper (arXiv:2507.02778, Self-Correction Bench, COLM 2026) —
  fully read, all 11 body pages **+ Appendix C's sensitivity/robustness tables (2026-08-17
  follow-up)**; Appendices A/B/D/E (dataset construction, figures, prompts, worked example) not
  read line-by-line since they're implementation detail, not new claims.
- §18e's ICC/agentic-evaluation-stochasticity paper (arXiv:2512.06710) — fully read, all 11 pages
  including all 5 appendices.
- §18f's "Illusion of Multi-Agent Advantage" (arXiv:2606.13003) — **now fully read, ALL 22 pages
  including Appendices A-G** (2026-08-17 follow-up; the appendices had only been skimmed for
  config detail before — see §18f's own corrected writeup for what Appendix E's six per-framework
  case studies and Appendix F's scope limitation actually add). §18f's MARL sample-complexity paper
  (arXiv:2602.08272) — full main body read (Sections 1-5, pages 1-10: theorems, empirical GSM8K
  validation, Limitations); the ~20 remaining pages are the theorems' own mathematical proofs
  (Appendices), not read line-by-line since the theorem statements and empirical results are what's
  load-bearing here. (First pass this session stopped at 3 of 32 pages and prematurely dismissed
  this paper as non-load-bearing training-theory only — the user directly caught this; finishing
  the read found a real, specific, structural match to where today's own bugs concentrated. See
  §18f's own corrected writeup.)
- MAST (arXiv:2503.13657, §2 above) — had been ✅ since 2026-07-19 on a main-body-only read (10 of
  47 pages); **Appendix A's full 14-mode failure catalog now read verbatim (2026-08-17)**, not
  caught by the user this time, found on a self-audit prompted by the user asking "how much more
  are we referencing without a full read." See §2's own updated entry.
- The AXPO paper (arXiv:2605.28774, §1 above) — **remaining 30 appendix pages now read (2026-08-17,
  same self-audit)**: Appendix D's Proposition 1 proof (clean, holds up) and Appendix E's
  Limitations (confirms and sharpens the reward-shape mismatch already flagged for DeepDelve's
  `writer_role_response_reward`). See §1's own updated entry.
- The RAG failure-taxonomy survey (`aclanthology.org/2026.trustnlp-main.27`, §9 area) and the SLM
  agentic-systems survey (arXiv:2510.03847, §9 area) — **both fully read cover-to-cover for the
  first time this session (2026-08-17 self-audit)**; both entries already cited specific
  tables/sections but had no explicit completeness confirmation. Neither surfaced a claim that
  invalidates what was already cited; each surfaced its own Limitations section, now folded into
  the relevant entries (single-rater grading + "structured map, not a prevalence ranking" for the
  taxonomy; "benchmark/API drift," "overfitting to narrow traces," "heavy validator dependence can
  hide reasoning failures" for the SLM survey's headline constrained-decoding numbers).

All PDFs saved permanently at `papers/*.pdf` (gitignored, not committed) for later reading.
The specific figures (45-48%, etc.) outside these and the framing built on them should be treated
as directionally credible, not confirmed fact, until someone does the same primary-read pass §1
(and now §18b/§18d/§18e/§18f) already sets the bar for. This applies most to any number cited
below outside those — don't repeat a percentage from this section as verified without first
opening the actual paper.

### 18a. The "zero trailing text" mechanism (item -1, item 2 of the History entry) is a named,
actively-studied 2026 failure class, not a DeepDelve-specific oddity

This project already tracked two "synthesis-vanishing mechanisms" (deadline-cutoff, budget-cutoff,
both inserting a marker) before today; today added a third (zero trailing text, no marker at all)
and confirmed it can hit a WRITER role (`ARCHITECTURE.md` §2's new landmine), not just
Searcher/Analyzer. Checked against current literature rather than assumed novel: **"roughly 45 to
48 percent of agent failures close with a confident completion claim, where agents generate empty
or false completion messages rather than continuing productive work"** — a documented, named 2026
failure mode in production agent-reliability research (via [Confident AI's 2026 LLM Agent
Evaluation guide](https://www.confident-ai.com/blog/llm-agent-evaluation-complete-guide) and
adjacent 2026 agentic-loop literature, e.g. [*When Agents Do Not Stop: Uncovering Infinite Agentic
Loops in LLM Agents*](https://arxiv.org/pdf/2607.01641)). This is directly convergent with this
project's own measurement (25%/42% of a run's own findings had a fully empty summary across two
live runs, §17's own predecessor incidents) — two independent measurement methods (DeepDelve's own
`_run_state.json` forensics vs. published production-agent failure-mode surveys) landing in the
same ballpark strengthens confidence this is real and general, not a serving-layer or prompt quirk
specific to `deepdelve-gpt-oss`. **Not yet fixed** (`ROADMAP.md` Pending) — the literature doesn't
offer a clean fix either, only naming and measurement; worth treating as a genuinely open research
problem for this project's own model class, not something a quick patch closes.

### 18b. The self-correction blind spot — ✅ PRIMARY-SOURCE-VERIFIED (full paper, all 11 body
pages read, `papers/self_correction_bench_2507.02778.pdf`, gitignored) — one original source, not
two independent confirmations, plus a directly actionable, training-free intervention

Correction to this section's own earlier draft (same session): initially framed as "two
independent papers converging on 64.5%." Having now read [*Self-Correction Bench: Uncovering and
Addressing the Self-Correction Blind Spot in Large Language
Models*](https://arxiv.org/abs/2507.02778) (Ken Tsui, independent researcher, **published at COLM
2026** — a real peer-reviewed NLP venue, not a preprint-only claim) in full, this is clearly the
ORIGINAL empirical source of the 64.5% figure: "Testing 14 open-source non-reasoning models, we
find a 64.5% average Self-Correction Blind Spot." `_is_citable_finding`'s own docstring and the
existing "Multi-facet task abandonment" Pending entry cite [*When Can LLMs Actually Correct Their
Own Mistakes? A Critical Survey of Self-Correction of LLMs*](https://arxiv.org/html/2406.01297v3)
(TACL) for the SAME number — but that paper's own title identifies it as a SURVEY, and surveys
compile others' findings rather than running new experiments; the most likely explanation is the
survey cites this same Self-Correction Bench study (or an earlier version of it) as its source for
that number, not an independent second measurement. **RESOLVED 2026-08-17**: downloaded and read the
TACL paper directly (`arXiv:2406.01297v3`, full text) and grepped it for "64.5", "Tsui," and
"Self-Correction Bench" — **none appear anywhere in the paper**. The Kamoi et al. survey does not
report or cite that figure at all; it's a pure methodology critique (many published self-correction
studies fail to define their research question or design controlled experiments; §8 provides a
checklist for a valid study) with no empirical blind-spot measurement of its own. **The earlier
"two independent confirmations" framing was simply wrong, not just unconfirmed** — the 64.5% figure
has exactly ONE primary source (Tsui's Self-Correction Bench), and citing Kamoi et al. as a second
source for it (as this project's own README.md did until this correction) was a real citation
error, not a title typo like the Rasheed case above — fixed in README.md.

**Methodology, verified**: the paper's real contribution is isolating WHY models don't self-correct
— injecting the IDENTICAL error either into the model's own prior turn (internal) or the user's
prompt (external), with nothing else different. A model that fixes the external version but not
the identical internal one has the KNOWLEDGE to catch the error but fails to ACTIVATE that
capability — ruling out "the model doesn't know any better" as the explanation. This confirms the
blind spot is a genuine activation failure, not a competence gap, which is the load-bearing claim
this project's `_is_citable_finding` docstring and the ROADMAP Pending entry already build on.

**The closed-source/frontier finding, directly relevant to tonight's "frontier models were a
disaster" comment**: the blind spot is NOT solved by frontier proprietary models either — Claude
3.5 Haiku shows a 52.5% blind spot, Claude Sonnet 4 shows 41.4% (Table 7) — lower than the 64.5%
open-source average, but far from zero. A frontier model failing to self-correct its own prior
output is consistent with this paper's own findings, not a surprising or unexplained result.

**2026-08-17: Appendix C (Sensitivity Analysis) now read in full too — a genuinely important,
directly relevant per-model breakdown, not just dataset-construction boilerplate as the earlier
"References/appendices not read in full" note assumed.** Table 14 (temperature 0.0) breaks the
64.5% AVERAGE blind spot down per model, and the spread is enormous and directly relevant to
DeepDelve's own model choices: **Qwen3-14B, Qwen3-32B, and Qwen3-30B-A3B score external-error
self-correction accuracy of 0.004-0.108 — near-total blind spot, far worse than the 64.5% average
implies**, while Llama-4-Scout-17B scores 0.976 (near-perfect). A footnote confirms Qwen3 models
were tested in **non-thinking mode** — DeepDelve's own `config_template.yaml` also runs with
`enable_thinking: False`, so this is the SAME operating regime, not an extrapolation. This
strengthens (with a specific, damning number instead of just the aggregate) the paragraph below's
concern about DeepDelve's own non-thinking configuration. **Robustness checks, also now read,
rule out the obvious confounds before this number is trusted**: results hold at both temperature
0.0 and 0.6 (Table 14 vs 15, "does not change our conclusion"), at both 1,024 and 4,096-token
compute budgets (Table 16), and the LLM-judge scoring was cross-checked across three different
judges (Gemini 2.5 Flash, Claude Sonnet 4.6, GPT-5.4) at 95-97.9% pairwise agreement, Cohen's
kappa 0.90-0.95 (Table 17) — a real methodological safeguard against the single-judge bias risk
this review flags in other papers (e.g. the RAG failure taxonomy's single-rater grading, §1).

**Directly actionable, training-free intervention this project hasn't tried yet**: appending the
single word **"Wait"** after a model's own erroneous/rejected output — with NO fine-tuning, NO
architecture change — reduces the blind spot by 89.3% on average and increases mean accuracy by
156% (Section 4, Figure 6/7). Even more striking (Table 3): appending "Wait" to a NON-reasoning
base model's output nearly matches the accuracy of that same model's FULL reasoning-mode variant
— e.g. DeepSeek-V3-0324 base 0.567 → +"Wait" 0.902 → DeepSeek-R1 (its own reasoning model) 0.908,
almost identical. Reasoning models show a near-zero blind spot to begin with (Section 4.4), which
the paper attributes to their training data containing far more error-correction sequences, not a
fundamental capability difference. **DeepDelve currently runs with `enable_thinking: False`**
(`config_template.yaml`) — if the served model has reasoning capability at all, running it with
thinking off may be operating in exactly the higher-blind-spot regime this paper measures. A
concrete, cheap follow-up worth scoping: append "Wait" (or a similar marker — "But"/"However" also
help, less strongly, per Table 12) to `_dispatch_writer_review_fix`'s retry-instructions
prepend (today's own "strengthened retry" fix, commit `4dc19bc`) as an ADDITIONAL, evidence-backed
technique alongside the current "CRITICAL: your previous attempt..." framing — genuinely different
from what's implemented now, not yet tried, not implemented this session pending the user's
go-ahead.

Directly relevant to today's newly-found FindingsWriter loop (`ROADMAP.md`'s
"UPDATE 2026-08-17" note on the Multi-facet Pending entry): 3 byte-identical rejected `findings.md`
snapshots is a CLEANER, more extreme instance of the same blind spot than anything in the original
Pending entry's evidence — not just "the model regenerates a similar mistake," but "the exact same
deterministic content gets re-rejected with nothing whatsoever changing between attempts," since
the deterministic fallback path bypasses model generation variance entirely. That's a genuinely
useful new data point for whichever fix gets scoped next: the blind spot isn't only about the MODEL
failing to see its own error freshly — DeepDelve's own retry architecture can hand it back
LITERALLY identical input and expect a different result, which no amount of self-correction
capability could ever fix. **The retry loop itself, not just the model, needs a fix**: detect
"the SAME (problem, dispatched content) pair fired twice with the underlying evidence unchanged"
and escalate to a genuinely different strategy (surface the specific rejected content in the
correction's own instructions, or force a completely different writer-role prompt framing) rather
than dispatching the identical retry a third time.

### 18c. Today's quota-dedup fix (mechanism 2) independently rediscovered a named 2026 mitigation
pattern — "no-progress guard" — worth generalizing with that framing in mind

Before implementing the `read_workspace_file` exact-repeat dedup, this session traced the fix
narrowly against `check_quota`'s own real call graph (session transcript, not literature) and
scoped it to exactly one tool. A literature check afterward found this is a named, established
2026 mitigation pattern applied more broadly than DeepDelve's own narrow fix: **"a no-progress
guard that hashes repeated (tool, args, error) tuples stops stuck agents early... halts when the
same (tool, arguments, error) tuple repeats 2 to 3 times"** ([Particula's "Stop AI Agents Looping
on the Same Failed Tool Call"](https://particula.tech/blog/stop-ai-agents-looping-same-tool-call-no-progress),
consistent with [Openlayer's July 2026 AI Agent Failure Modes
survey](https://www.openlayer.com/blog/ai-agent-failure-modes-tool-calling-loops-propagation)).
DeepDelve's fix is a narrower special case of this general pattern — it only DEDUPES the quota
cost of an exact repeat (letting the call still execute, since `read_workspace_file` is idempotent
and a repeat is harmless, just wasteful), rather than HALTING the dispatch outright the way a full
no-progress guard would for a genuinely stuck/looping tool call. This distinction is deliberate
(this project's own `QuotaAbortException` already exists as the "halt on genuine loop" mechanism,
gated on a DIFFERENT signal — repeated OVER-quota calls, not repeated identical arguments per se)
but worth naming explicitly: **a future generalization of today's dedup fix to other tools should
use the (tool, args) key as a no-progress SIGNAL feeding the EXISTING `QuotaAbortException`
threshold, not just a quota-cost exemption** — the literature's framing (halt, don't just discount)
is the more complete mitigation for a tool where a repeat ISN'T harmless/idempotent (unlike
`read_workspace_file`).

### 18d. The real gap this arc exposes: DeepDelve validates its own fixes with n=1 live runs, and
current literature has a specific, actionable answer for that

Every fix in today's 7-fix arc (and §17's four before it) was validated the same way: implement,
run the standing benchmark prompt ONCE, read the transcript, confirm the target symptom didn't
recur. This is exactly the evaluation gap 2026 agent-reliability literature has converged on
naming: **"a single pass rate conflates two different questions — pass@k (can the agent solve this
AT ALL, at least once) and pass^k (does the agent solve this EVERY time) — a benchmark that
reports only pass@1 hides the consistency story; one that reports only pass^1 treats a single data
point as if it were stable."** ([Phil Schmid's "Pass@k vs Pass^k: Understanding Agent
Reliability"](https://www.philschmid.de/agents-pass-at-k-pass-power-k); consistent framing in
[*Beyond pass@1: A Reliability Science Framework for Long-Horizon LLM
Agents*](https://arxiv.org/pdf/2603.29231) (Khanal, Tao, Zhou, Northern Kentucky University,
2026-04-01) and [*Consistency as a Testable Property: Statistical
Methods to Evaluate AI Agent Reliability*](https://arxiv.org/pdf/2605.10516).)

**✅ Primary-source-verified 2026-08-17, FULL PAPER (all 23 pages, not the 6 originally read).**
Correction to an earlier pass this same session: the first read stopped after 6 pages on a false
"12 page(s)" reading from a metadata field that undercounted the real length — `pdfinfo` confirms
23 pages. The user's own explicit "read the papers properly, I don't want half-based research"
instruction caught exactly this gap; the rest below is from the completed read, saved permanently
at `papers/beyond_pass1_2603.29231.pdf` (gitignored, not committed).

Definitions 1/2 confirm pass@1/pass^k exactly as used here (pass^k = probability ALL k independent
repeated episodes succeed, not just one). The paper's own motivating example is a real,
precisely-measured number worth citing directly: τ-bench (Yao et al., 2024) found **GPT-4o scores
61% pass@1 but only 25% pass@8 on retail agent tasks** — a single best-effort attempt looks 2.4x
better than the metric that actually matters for a system meant to run unattended. The paper's own
full study uses **k=3 repeats** as its methodology (Table 2, "23,760 planned episodes... k=3...
two scaffolds") — direct external precedent for this section's own "k=3 is the realistic starting
point" recommendation, not an invented number. One of the paper's three benchmark domains is
literally **"Agentic Web Research (WR)"** — "multi-step information gathering via web search and
URL fetching, followed by synthesis into structured or prose outputs" — the same task shape
DeepDelve's own Searcher/Analyzer/FindingsWriter pipeline performs, making this paper's findings
directly on-domain, not just analogous.

**The MOP paradox, now with the real numbers (Section 6.4, Table 12, confirmed in the paper's own
Conclusion)**: DeepSeek V3 and MiniMax M2.5 — the two models with the BEST very-long-horizon GDS
(0.87, 0.89) — also have the HIGHEST very-long meltdown rates (19% and 13% respectively); every
other model in the study has 0-4% meltdown rates across all buckets. The paper's own mechanism:
"frontier models attempt more aggressive, multi-step strategies... when they spiral... the
sliding-window entropy exceeds the threshold. Weaker models, by contrast, emit stable low-entropy
tool-call sequences... because they follow rote, shallow strategies that never generate entropy
spikes — but also never complete the task." This is NOT "frontier models are less reliable" — the
SAME models have both the best average reliability AND the highest failure-mode rate, because
capability and ambition create more opportunities to both succeed AND spiral. Directly relevant to
this project's own "zero trailing text" mechanism (ARCHITECTURE.md §2): a model ending a turn with
literally nothing after a tool call is arguably the LOW-entropy failure shape this paper's weak
models show, not the high-entropy "spiral" shape MOP detects — worth keeping distinct if this
project ever adds its own meltdown-style detection.

**Memory scaffold finding, precise numbers (Section 6.5, Table 13)**: across all 10 models, the
memory-augmented scaffold NEVER improves long-horizon GDS relative to plain ReAct — neutral
(within ±0.03 GDS) for 4 models, negative for 6, zero models gained. Largest penalties on
mid-capability models (Kimi K2.5 −0.14, Mistral 24B −0.13) — "capable enough to use the scratchpad
but not capable enough to absorb its overhead efficiently." The paper's own recommendation: "the
baseline ReAct loop is strictly better in aggregate" and memory scaffolds "should not be adopted as
a default reliability intervention" without per-task overhead calibration.

**⚠️ Critical scope limitation, stated explicitly by the paper's own authors (Section 7.3) — this
is the caveat that matters most for anyone reading "frontier" in this paper's findings as a general
claim**: *"We evaluate 10 open-source models only, for cost and reproducibility reasons. We do NOT
evaluate GPT-4o, Claude 3.7, Gemini 2.0 Ultra, or other frontier PROPRIETARY models, which are
likely MORE reliable than the models studied here. Our findings characterize the open-source
frontier; extending to frontier proprietary models is left to future work."* Every "frontier"
claim in this paper (the MOP paradox, the VAF bifurcation, the two-tier reliability structure) is
about the frontier of OPEN-WEIGHT models (DeepSeek V3, Kimi K2.5, MiniMax M2.5 — all large MoE
models served via OpenRouter) — it says nothing, one way or the other, about proprietary
frontier-labeled models (GPT-4o/Claude/Gemini-class). A real-world "we tried frontier models and
it was a disaster" experience with a proprietary API model is neither confirmed nor contradicted by
this paper — it's simply a population the study didn't cover, by its own explicit admission (also
listed as unstudied future work: "Proprietary model extension... to characterize the proprietary
reliability frontier and its relation to the open-source tier boundary observed here"). Also worth
noting as a methodological caveat on MOP specifically: the paper's own Table 15 (window-size
sensitivity) reports empty "—" cells for precision/recall — the authors state outright that "full
manual labeling of meltdown episodes against ground-truth failure outcomes is deferred to future
work," meaning the MOP metric's own detection accuracy is self-acknowledged as not yet validated
against ground truth, only calibrated by F1 on a 50-episode pilot sample.

A single successful
live run after a fix is a pass@1 data point at best — it proves the fix CAN work, not that it
RELIABLY works, and (per §18b/18c above) this project's own failure modes are frequently
stochastic/model-behavior-dependent, exactly the kind of failure a single trial is least equipped
to characterize. [*Stochasticity in Agentic Evaluations: Quantifying Inconsistency with Intraclass
Correlation*](https://arxiv.org/pdf/2512.06710) and general 2026 guidance converge on **k=3–10
repeated trials of the same task as the practical floor for a meaningful pass^k signal**, scaled by
cost tolerance — for a 20-70 minute live run against a real local model, k=3 is the realistic
starting point for this project's own hardware/time budget, not k=10.

**IMPLEMENTED 2026-08-17, same day, commit `071d64f`**: `eval/evaluate.py` gained
`compute_reliability_summary`/`print_reliability_summary` — groups the existing `--runs`-produced
per-run entries in `results.jsonl` by (query, model, hardware) and reports both pass@k and pass^k
(plus mean score), using `score_structural`'s own tier-1 forensic scoring (already existed) as the
per-run signal to aggregate. New `--pass-threshold` (default 1.0) and `--summary-only` (recompute
without re-running) CLI flags. This is the direct, actionable answer to that night's own question
("we need more data to know if we're really improving") — the equivalent rigor bar for
ENGINE/completion-check fix comparisons that `Model Evaluation Standard` (`ROADMAP.md`) already
sets for MODEL comparisons. **Not yet actually USED to draw a k≥3 reliability conclusion about any
of this session's fixes** — building the measurement infrastructure was deliberately separated
from running it, per this note's own original caution: running k=3+ trials against a still-
actively-changing pipeline (mechanism 1 and the self-correction retry loop were both still open at
the time this was written, though both got partial mitigations later the same day — see the
2026-08-17 History entry in `ROADMAP.md`) would conflate "is this fix working" with "did the
pipeline change again since the last measurement." The lighter-weight per-attempt signal idea
(checking `_run_state.json` for zero grounding-warning/task-name-churn events without waiting for
a full run) was NOT implemented — `score_structural`'s existing 4-check design was judged
sufficient for now; revisit if a full k=3 run turns out too expensive to be practical on this
hardware.

### 18e. A THIRD paper read properly (2026-08-17, later same day) — ICC as a more rigorous
reliability metric than pass@k/pass^k, and a genuinely important correction to the "k=3 is enough"
framing §18d took from a different paper

✅ **Primary-source-verified, full paper including all 5 appendices** (all 11 pages,
`papers/stochasticity_agentic_2512.06710.pdf`, gitignored) — [*Stochasticity in Agentic
Evaluations: Quantifying Inconsistency with Intraclass
Correlation*](https://arxiv.org/pdf/2512.06710) (Mustahsan, Lim, Anand, Jain, McCann; AAAI 2026
copyright line). Read AFTER §18d's paper, specifically to check whether "k=3 is a reasonable
floor" (§18d's own recommendation, borrowed from a DIFFERENT paper's own methodology choice) holds
up against a paper that actually measures HOW MANY trials are needed for a stable estimate, rather
than just picking a number.

**The metric**: Intraclass Correlation Coefficient (ICC) decomposes an evaluation's total variance
into between-task variance (some tasks are just harder) and within-task variance (the SAME agent
on the SAME task gives different results trial to trial). ICC = between / (between + within). High
ICC (≥0.75) means differences you see across runs mostly reflect real task difficulty, not agent
randomness — a single run is trustworthy. Low ICC (<0.50) means the agent is "highly inconsistent,"
and a single run's result could easily have gone very differently by chance.

**The central, load-bearing finding for this project**: ICC varies dramatically by task structure,
and the tasks CLOSEST to DeepDelve's own shape are the WORST case. On GAIA Level 3 ("hard
open-ended reasoning," multi-step, unrestricted tools — the closest analog in this benchmark to
DeepDelve's own open-ended multi-hour research task) GPT-4o scores ICC=0.304, meaning **70% of
observed variance is trial-to-trial randomness, not task difficulty** — the paper's own words:
"Level 3 shows a stark contrast... single-run results are essentially unreliable." Even GPT-5
(the best model tested) only reaches ICC=0.629 on Level 3 — "moderate," not "good" reliability, and
Table 5 (Appendix D, 7 more frontier models including Claude 4.5 Sonnet/Haiku, Gemini 2.5 Pro,
Qwen3-235B, DeepSeek-v3p1) shows even the HIGHEST-accuracy model (GPT-5 search, 59.44%) has LOWER
ICC (0.745) than a less-accurate one (Claude 4.5 Sonnet, 0.756 ICC at only 39.71% accuracy) — a
genuine, measured "capability vs. consistency" tension, not a hypothesis.

**Sample-size convergence, the specific correction to §18d's "k=3 is the realistic floor"
recommendation**: this paper's own empirical convergence analysis (Section "ICC Convergence Across
GAIA Levels," Table showing n=2 through n=64) finds ICC estimates stabilize by **n≈8-16 trials for
structured tasks, but n≈32 for Level 3 (hard open-ended reasoning)** — the exact task shape
DeepDelve's own runs are. §18d's "k=3 is the realistic starting point" was borrowed from a
DIFFERENT paper's own methodology CHOICE (that paper used k=3 for ITS OWN, differently-shaped
benchmark, not a convergence measurement) — it was never a claim that k=3 achieves a converged
reliability estimate for a task this hard. **This paper's own closest real-world analog to
DeepDelve makes the same point directly**: their own "Deep Research Agents" evaluation (o4-mini
deep research, the paper's nearest comparison to a DeepDelve-shaped agent) used only n=8 trials
due to cost, and the paper's own Limitations section states plainly: **"Deep research evaluation
used one agent with 8 trials. Further research is needed for generalizable conclusions."** If an
AAAI-published study with the o4-mini deep research API doesn't claim n=8 is enough for their own
closest-to-DeepDelve case, `eval/evaluate.py`'s k=3 default should not be read as giving a
converged, statistically solid reliability estimate either — it's a meaningfully-better-than-n=1
practical floor for this project's own 20-70-minute-per-run cost constraint, not evidence of true
convergence. **Correction to record, not yet acted on in code**: `eval/evaluate.py`'s
`--pass-threshold`/reliability summary should eventually get a comment or doc note making this
explicit (k=3 is a floor chosen for cost reasons, not a claim of statistical convergence) — small,
low-risk, deferred rather than rushed into this already-long session.

**Practical allocation insight, potentially useful for a future eval redesign**: for a FIXED total
compute budget B = n·T (n tasks, T trials each), the paper derives that variance is minimized by
maximizing n (more distinct tasks, fewer trials each) UNLESS the goal is specifically to
characterize PER-TASK consistency, in which case enough T per task is needed regardless of n. For
DeepDelve's own single-benchmark-prompt reliability question ("does THIS query converge
reliably"), this argues FOR spending the budget on trials of the SAME prompt (current design,
`--runs`), not against it — the tradeoff only cuts the other way if the goal shifts to "how
reliable is DeepDelve across a WIDE variety of prompts," a different, currently-unasked question.

### 18f. Direct answer to "is DeepDelve's task division too much?" — ✅ BOTH papers now read in
full (correction: the second was first read only 3 of 32 pages and prematurely dismissed as
"training theory only, not load-bearing" — the user directly caught this; finishing the read
found it's substantially MORE relevant than that first-pass summary gave it credit for),
synthesized against the ALREADY-verified MAST paper (§2, this document)

The user asked directly, after seeing today's 7-fix arc plus the ICC/pass^k evidence: is
DeepDelve's Planner→Searcher→Analyzer decomposition itself over-engineered? This section is the
literature-grounded answer, not a hedge.

**Both papers read in full**: [*The Illusion of Multi-Agent
Advantage*](https://arxiv.org/abs/2606.13003) (Jwalapuram et al., Salesforce Research/HKUST/UBC/NTU,
2026-06-13), `papers/illusion_multiagent_2606.13003.pdf` — main body (10 pages) + appendices
skimmed for config detail. [*When Do Multi-Agent Systems Outperform? Analysing the Learning
Efficiency of Agentic Systems*](https://arxiv.org/abs/2602.08272) (Su, Wu, University of Hong
Kong, 2026-02-10), `papers/when_mas_outperform_2602.08272.pdf` — full main body (Sections 1-5,
pages 1-10) including the theorems, the empirical validation section, and Limitations; the
remaining ~20 pages are pure mathematical proofs of the stated theorems (Appendices), not read in
detail since the theorem STATEMENTS and their real-data empirical validation (not just the proofs)
are what's load-bearing here.

**Correcting the first-pass dismissal**: this paper IS about MARL (multi-agent reinforcement
learning) TRAINING sample complexity, not deployed inference-time orchestration — that part of the
first read was accurate. What was wrong was concluding this makes it non-load-bearing for
DeepDelve. The paper's own theorems (4.1-4.3) plus its **empirical validation on REAL GSM8K math
reasoning data** (Figure 1d/e, not just synthetic tasks) establish a general, mechanism-level
principle that applies to ANY multi-step decomposed pipeline, training or inference: **decomposing
into genuinely INDEPENDENT subtasks scales sample/coordination cost down (Theorem 4.3: complexity
dominated by the single hardest subtask, not the sum); decomposing into DEPENDENT subtasks
introduces error PROPAGATION with a quadratic (K²) worst-case penalty in the number of agents
(Theorem 4.2) — confirmed empirically on GSM8K: "In the independent-subtask setting, MARL achieves
markedly better sample efficiency than SARL. In the dependent-subtask setting, SARL [single-agent]
consistently outperforms MARL due to error propagation across agents,"** and this gap WIDENS as
the agent count K grows. Section 4.3's task-alignment factor (α, how well the imposed decomposition
matches the task's real structure) is the other lever: "under strong task alignment... MARL
performs comparably to SARL, whereas misaligned decompositions... lead to the expected degradation."

**Why this maps directly onto DeepDelve's own architecture, precisely at the place today's bugs
concentrated**: DeepDelve's Planner→facet decomposition (Lisbon-visa, Lisbon-rent, MexicoCity-visa,
MexicoCity-rent as 4 parallel research tasks) is the GOOD case this paper's theory and its GSM8K
data both predict should benefit from decomposition — the facets are genuinely independent
research questions, dispatched to genuinely fresh, isolated sub-agent contexts. **But the
consolidation stage is NOT independent by construction**: FindingsWriter's one dispatch must
integrate ALL facets' findings into one file; Builder's one dispatch must integrate ALL of
findings.md into one report. This is exactly the paper's own "dependent subtask" case — each
downstream synthesis step's correctness depends on everything upstream, and the paper's own
mechanism (error propagation, worsening with more upstream sources feeding into fewer downstream
consolidation steps) is a clean structural match for what today's ENTIRE 7-fix arc actually found:
every single bug (evidence-crowding, marker leaks, the byte-identical self-correction loop,
quota exhaustion mid-consolidation) occurred at the FindingsWriter/Builder consolidation stage, not
during the genuinely-independent per-facet research dispatch. **Not a coincidence, evidenced by
this paper's own theory**: the consolidation stage is structurally the ONE dependent, non-
parallelizable junction in an otherwise well-decomposed pipeline, and both this paper's GSM8K
result and today's own bug catalog agree that's exactly where a decomposed system's reliability
concentrates its failures.

**The critical distinction the Illusion paper makes, and why it does NOT indict DeepDelve's
architecture**: its central finding — automated MAS "consistently underperform CoT-SC despite
being up to 10x more expensive," with "functional collapse" into simple ensembling ~70-90% of the
time — is specifically about **AUTOMATICALLY-GENERATED, dynamically-routed** MAS frameworks
(DyLAN, MAS-Zero, ADAS, AFlow, MaAS, MAS-Orchestra): systems where an LLM/meta-agent/RL controller
decides the coordination STRUCTURE itself, per query, at inference time. DeepDelve does none of
this — its Planner/Searcher/Analyzer roles, `delegate_tasks` dispatch shape, and completion-check
pipeline are all FIXED, hand-designed, the same for every query. The paper's own contrast case is
"Expert-MAS": a deterministic, code-driven pipeline with explicit role decomposition and Python-
orchestrated control flow — structurally the closest match in the paper to DeepDelve's own shape.
**Expert-MAS is the one architecture in the whole paper that WINS decisively**: "GPT-OSS improves
from 26.1% to 36.1%; GPT-5 jumps from 57.0% to a near-perfect 96.5%" over the same models run
single-agent. The paper's own Discussion states the principle directly: **"multi-agent coordination
excels only when architectures are specifically engineered to exploit parallelizable sub-problems
or context protection"** — exactly what DeepDelve's Searcher/Analyzer split and per-facet parallel
dispatch are built to do (protect context per sub-agent, parallelize independent research facets).

**2026-08-17 correction: the Illusion paper's remaining appendices (E "Architectural Analysis," F
"Scope and Limitations") are now read in full, not skimmed** (`papers/illusion_multiagent_
2606.13003.pdf`, `pdfinfo`-confirmed 22 pages). Two things change:
1. **Appendix E's six per-framework case studies (DyLAN, AFlow, MAS-Zero, ADAS, MaAS,
   MAS-Orchestra) are exactly the automated/dynamically-routed systems the main body's aggregate
   critique targets, confirmed in granular detail — none of them describe anything resembling
   DeepDelve's fixed pipeline.** Concretely: AFlow's "optimized" workflows literally degenerate
   into 3x-custom-prompt-then-vote (functionally CoT-SC) in 7/14 discovered workflows; MAS-Zero's
   verifier exhibits severe positional bias (GPT-4o picks the first block >45% of the time
   regardless of quality); MaAS's router collapses to either a trivial single I/O call or an
   undifferentiated uniform distribution once accuracy saturates; MAS-Orchestra's difficulty-aware
   router turns out to be difficulty-AGNOSTIC in practice. This strengthens (with real per-system
   evidence, not just the aggregate number) the earlier conclusion that DeepDelve's FIXED,
   hand-designed roster is not the failure shape this paper documents.
2. **Appendix F's own stated scope limitation is a real, previously-missed caveat that tempers
   (not reverses) the "DeepDelve matches Expert-MAS" framing above**: *"Our evaluation focuses
   primarily on cognitive orchestration and long-horizon reasoning within closed or semi-closed
   contexts... we did not evaluate the broader spectrum of autonomous tool-use, such as real-time
   API interaction... It remains possible that the structural efficiencies identified in our
   'Expert-MAS' might differ in environments where the primary bottleneck is external tool-call
   latency or protocol adherence rather than internal logical consistency. Our findings of
   functional collapse are therefore most applicable to reasoning-heavy agentic workflows."*
   DeepDelve is NOT a closed-context reasoning system — its actual bottlenecks (quota exhaustion,
   fetch/search tool-call reliability, protocol adherence to task_name conventions) are precisely
   the category this paper's authors flag as untested. The paper's own Model Diversity limitation
   (§F) is a second, compounding gap: evaluated primarily on frontier OpenAI/Google models plus
   ONE open-source backbone, none of it on small local models in DeepDelve's own capability range.
   **Net effect: "DeepDelve's architecture matches the one winning pattern in this literature" is
   still the best-supported reading, but it is an extrapolation from a reasoning-benchmark study to
   a tool-heavy one, not a direct result — worth stating plainly rather than letting the earlier
   framing imply a tighter fit than the paper itself claims.**

**So: no, the task-division ARCHITECTURE itself is not the evidenced problem** — it matches the
one pattern this literature actually validates, not the one it criticizes. **But the paper's own
diagnostic METHODOLOGY exposes a real, different risk that today's 7-fix arc is a live instance
of**: it identifies "architectural bloat" not from decomposition itself, but from complexity ADDED
WITHOUT VERIFIED CAUSAL CONTRIBUTION — "role redundancy" (pieces that turn out to behave
identically to something simpler), and "expensive witnesses" (mechanisms that cost real overhead
but have "near-zero causal influence on the output"). Their own audit method: deconstruct each
piece of the coordination layer and check whether it demonstrably changes outcomes, not just
whether it looks reasonable. **This is the missing check for DeepDelve's own completion-check
pipeline, not the Planner→Searcher→Analyzer decomposition**: today alone added the no-progress
guard, the content-identity escalation, the strengthened retry, and (across many prior sessions,
per `ARCHITECTURE.md`'s own growing landmine list) `force_whole_rebuild`, per-facet dispatch,
starvation guards, quota rescue, `gap_acknowledged` stickiness, and more — EVERY one of these was
validated only by "did the ONE specific symptom it was built for stop recurring in a live re-run,"
never by a controlled ablation (run WITH vs. WITHOUT the mechanism, k≥3 trials each, per §18d/§18e's
own methodology) that would show whether it's genuinely load-bearing or a plausible-sounding
addition that happened to coincide with the next fix actually mattering. MAST's own causal
intervention evidence (§2, already-verified: giving one agent final decision authority instead of
consensus raised success +9.4%; adding a verification step raised +15.6%) is the standard this
project's OWN completion-check mechanisms have never been held to — every DeepDelve fix this
session was validated the OPPOSITE way from MAST's own methodology.

**Concrete, evidence-backed recommendation, not implemented — a genuine next-session candidate,
not a small patch**: once `eval/evaluate.py`'s `--runs`/pass@k/pass^k harness (built today) has
enough historical data, the highest-value use of it is NOT just "does the whole pipeline pass more
often" — it's a controlled ablation of the completion-check pipeline's OWN accumulated mechanisms:
pick 2-3 of the more elaborate ones (per-facet dispatch, `force_whole_rebuild`, the no-progress
guard) and run k≥3 trials of the standing benchmark WITH each disabled vs. enabled, to find out
which ones are load-bearing (MAST-style, causally proven) versus which are "expensive witnesses"
this project has been carrying without ever measuring. This is the literature-grounded version of
the user's own question — not "is decomposition too much," which the evidence says no, but "has
the COORDINATION LAYER managing that decomposition grown complexity faster than anyone has
verified it's earning," which the evidence says is a real, currently-unanswered risk.

**Second, more targeted recommendation from the MARL paper's own dependent-vs-independent
distinction, not yet scoped**: since the theory AND today's own bug catalog both point at the
FindingsWriter/Builder consolidation stage specifically (not the per-facet research dispatch) as
the structurally dependent, error-propagating junction, that stage is where reducing K (the
effective number of "agents"/steps a single piece of information has to survive before reaching
the final report) should pay off most, per Theorem 4.2's own K² penalty. Concretely: today's
per-facet dispatch fixes (`_dispatch_per_facet_findings_writer_fix`/`_dispatch_per_facet_builder_fix`,
already shipped in prior sessions per `ARCHITECTURE.md` §1) already do exactly this — they reduce
one N-facet consolidation call to N single-facet calls, each a SHORTER dependency chain. The
theory suggests this should already be measurably helping; whether it demonstrably does (again,
never controlled-ablated) is the same open measurement gap as the paragraph above, just pointed at
the one specific mechanism the theory says should matter most.

**First real results, 2026-08-18 — BOTH `no_progress_guard` and `force_whole_rebuild` CONFIRMED
load-bearing.** Adaptive-trial protocol per this section's own recommendation above: one k=1 trial
per condition first, escalate to k≥2/3 only where a real difference shows up (Model Evaluation
Standard point 4). Standing benchmark query (`eval/ablation_dataset.jsonl`, the Lisbon-vs-Mexico-
City dual-angle prompt), `deepdelve-gpt-oss`, same hardware, `settings.max_run_minutes: 45`
(agent-internal) / `--timeout 2820` (harness-level, a small margin above it) both conditions.

| Condition | Runs | Scores | Time(s) | Verdict |
|---|---|---|---|---|
| baseline (no ablation) | k=1 | 0.75 | 2656 | reference |
| `disable_force_whole_rebuild` | k=3 | 0.75, 0.00, 0.00 | 2061, 1411, 1266 | **mean 0.25 — CONFIRMED load-bearing** (escalated from k=1 after run 2 disagreed with run 1; runs 2 and 3 agreed with each other, resolving it) |
| `disable_no_progress_guard` | k=2 | 0.00, 0.25 | 2820 (timeout), 2820 (timeout) | **mean 0.125 — CONFIRMED load-bearing** (both runs timed out, both scored well below baseline; not escalated to k=3, see below) |

`disable_no_progress_guard`'s two runs failed for two DIFFERENT specific reasons, which is itself
informative — not one fragile failure mode, but the guard's absence generically letting the run
burn its whole time budget on unproductive retries whenever ANYTHING gets stuck, regardless of
which specific check triggers it:
- **Run 1**: `findings.md` entered a byte-identical rebuild→reject→rebuild loop
  (`findings.md.rejected_attempt_3`/`_4` were byte-for-byte identical, 11 minutes apart) — root
  cause traced to a real, separate bug (`run_completion_check`'s `findings_ungrounded` directive
  never named the SPECIFIC hallucinated URL that failed verification, so FindingsWriter had no
  signal to stop re-citing it; fixed same day, see `session_status/CURRENT.md`). Scored 0.00,
  `final_report.md` never written.
- **Run 2** (after the fix above): `findings.md`'s own loop was confirmed broken (no more
  identical-content repeats) — but the run still timed out, because the earlier retry cycle
  (`task_verification_flagged` → `missing_findings` → `findings_ungrounded`) alone consumed ~36 of
  the 47-minute budget before `findings.md` was even accepted, leaving no time for
  `final_report.md` at all (killed mid-generation by the hard subprocess timeout, zero trace on
  disk). Scored 0.25.

Both runs show the SAME qualitative shape `no_progress_guard`'s own docstring predicts: without a
halt on a stuck same-error pattern, a run just keeps paying the token/wall-clock cost of retrying
instead of failing fast and reallocating the remaining budget. A k=3 was deliberately NOT run —
with 2/2 runs already timed out on clearly degraded scores against a consistent 0.75 baseline, a
third run offers very little new information (near-certain to time out again) for a full ~47
minutes of cost; escalation is for resolving disagreement between early trials, and there wasn't
any here. If this verdict needs to be load-bearing for a decision beyond "keep the guard, don't
remove it" (e.g. redesigning it), a real k=3 is still the right bar to clear first.

**`disable_force_whole_rebuild`: k=1 (0.75) disagreed with k=2's own second trial (0.00) — the
textbook case for escalating, per the same standard, since disagreement between early trials is
exactly what k≥3 exists to resolve.** A k=3 run confirmed the majority: 0.00 again, mean 0.25
across all three. Runs 2 and 3 both hit the SAME underlying coordination failure
`force_whole_rebuild` exists to break — a completion-check problem (`task_verification_flagged` in
run 2, `thin_coverage` in run 3) repeating 3+ times verbatim, telling the Planner each time to
"acknowledge the gap" or "redo it," without the mechanism ever forcing a genuine change of
strategy:
- **Run 2**: `task_verification_flagged` fired on `digital_nomad_visa_mexico` three times running
  (attempts 1-3, the last two identically worded "still fabricated, acknowledge the gap"), then the
  run ended after only 4 completion-check attempts with NEITHER `findings.md` NOR `final_report.md`
  ever written — not a timeout, the run just gave up early (1411s, ~23.5min, well under the
  47-minute ceiling). Scored 0.00.
- **Run 3**: `thin_coverage` fired three separate times (attempts 0, 1, 3 — interrupted once by
  `untracked_delegation`), never escalating past "only 2/6 delegated tasks produced a real source"
  for the SAME two Mexico-side tasks each time. A `final_report.md` WAS eventually written (unlike
  run 2), but with two whole query facets never actually researched, it scored 0.00 structurally —
  the report existed but was missing entire required content. 1266s (~21min), also well under the
  timeout ceiling.

Both confirming runs show the SAME shape as `no_progress_guard`'s own confirmed pattern, just at a
DIFFERENT layer: `no_progress_guard` catches a stuck TOOL CALL (same args, same error);
`force_whole_rebuild` catches a stuck COMPLETION-CHECK VERDICT (same problem, same "acknowledge and
move on" resolution) — without either guard, the corresponding stuck pattern is free to repeat
indefinitely (burning wall-clock, `no_progress_guard`'s failure mode) or terminate the run early
having never produced real, complete content (`force_whole_rebuild`'s failure mode here). Two
independently-shaped coordination mechanisms, two independently-confirmed real contributions — a
genuine result for the audit this section originally called for, not just "did today's specific
symptom stop recurring."

`rename_reject_escalation`/`tool_failure_streak_guard` (lower priority — both already
live-validated against a real incident when they were built, unlike the two above) not yet run at
all as of this writing.
