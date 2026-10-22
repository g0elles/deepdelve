# Literature: Builder fixes that do not stick (2026-10-22, read 2026-10-22/23)

Context: ARCHITECTURE.md §2 "Builder fixes do not stick". PDFs are in `papers/` (gitignored). Page counts are from `pdfinfo`.
Every paper below was read from the downloaded PDF text, not from a search summary. Read coverage is stated per paper; where a
section was not read it is named. The first version of this file (search-summary level) is superseded by this one; several of its
statements were wrong or overstated and are corrected in "Corrections to the earlier version".

## Read in full (main body, every section that could carry a claim used here)

### Huang et al., Large Language Models Cannot Self-Correct Reasoning Yet, arXiv 2310.01798 (ICLR 2024), 17 pp
Read: pp. 1-9 (all sections incl. conclusion, limitations); NOT read: references and Appendix A (full prompts/responses).
- Tested GPT-3.5-Turbo, GPT-4, GPT-4-Turbo, Llama-2-70B-chat on GSM8K, CommonSenseQA, HotpotQA. With oracle labels self-correction
  helps (GPT-3.5 GSM8K 75.9 to 84.3); without them accuracy drops (GPT-3.5 CommonSenseQA 75.8 to 38.1; Llama-2-70B GSM8K 62.0 to 43.5).
- Multi-agent debate no better than self-consistency at equal response count (Table 7).
- **Sec. 5 + conclusion: requirements should be in the INITIAL prompt, not only in the feedback prompt.** Moving the "include ALL
  concepts" requirement into the first prompt beat Self-Refine's gain (CommonGen-Hard 81.8 vs 75.1 after self-correction).
  They also say valid external feedback (code executors, tools, search) is where correction works.
- Scope limit stated by the authors: reasoning tasks only; style/safety self-correction may behave differently.
- **The paper contains no "below 13B parameters" claim** (searched the full text; no occurrence of 13B or a size threshold).
- Use for DeepDelve: supports fix 2A (standing requirements in the Builder instructions) and supports the harness design of
  deterministic external checks as the feedback source. Does not test report editing or small local models.

### Shridhar et al., SCREWS, arXiv 2309.13075, 22 pp
Read: pp. 1-15 (body, references, Appendix A token cost); Appendix B (prompts, pp. 16-22) only by section headings.
- ChatGPT (gpt-3.5-turbo-0301) mainly, GPT-4 for some runs; GSM8K, StrategyQA (490 train items), Big-Bench AutoDebugging (33 test items).
- Resampling alone can lower accuracy (StrategyQA CoT 77.18 to 74.54 with same-method resampling). A Selection module that can keep
  the earlier answer helped on all three datasets (e.g. AutoDebugging Answer-Only 73.52 to 88.23 with resample + select).
- The default "always keep the most recent revision" is the practice they argue against. Their selector is an LLM or majority
  vote and stays well below the oracle upper bound (Table 2, independent sampling: 74.90 achieved vs 85.36 possible; 81.34 vs 89.08).
- More revision rounds gave little (GSM8K 79.22 to 79.45 to 79.52). Resampling cannot fix facts the model lacks (needs tools).
- Use for DeepDelve: supports rollback/selection. Our selector would be the deterministic gap check, which avoids the paper's
  weak-selector problem. Small test sets (33 items for the debugging claim); reasoning tasks, not report editing.

### Chen et al., Understanding Agent-Reactive Bugs at the Model-Harness Boundary, arXiv 2607.15684, 12 pp
Read: all 12 pp.
- 255 manually labelled issues (Codex, Gemini-CLI, LangChain, CrewAI) out of 3,037 engaged, 8.4% of discussed issues; two annotators.
- Symptoms: silent errors 108, crash 71, error-in-output 34, retry loop 33, hang 9. Top trigger: task instruction non-compliance (INC) 67,
  **81% of INC cases surface as silent errors** because the model still emits fluent output that satisfies the harness's surface contract.
  Statement fabrication 20 (18 silent). Retry-loop example: Gemini-CLI edit with non-matching old_string, read-fail-retry until the cap.
- Merged harness fixes: validate tool calls before execution, tolerant parsing, prompt steering, registry checks, runtime loop guards.
  Developers attribute INC to the model 22 of 67 times and only 11 of 67 ended in a merged harness PR (mismatch with users).
- Recommends trace-based oracles: check the agent's claimed behaviour against real tool-call records.
- Limits: observational GitHub issues, 4 projects, closed-source agents excluded, no measured fix effectiveness.
- Use for DeepDelve: closest description of our failure (Builder "finishes" fluently without a write). Supports fix 1A (compare the
  claim/outcome with the real tool-call or file record). Qualitative; not evidence of a fix rate.

### Althoubi, Look Before You Leap: Pre-Action Verification for LLM Agents, arXiv 2609.11957, 8 pp
Read: all 8 pp. The supplementary material it refers to is not in the PDF and was not read.
- Shell commands: 9,930 commands, 482 tools; verifier recall 0.958 at 10.0% FPR (7.0% with selective abstention); syntax + binary
  checks are oracle-exact (0 FP, recall 0.500).
- Edits: 640 edits, 224 files, 23,040 trials. Content-anchored formats (search/replace, diff) never silent; line ranges 99.1% silent under a
  one-line shift; whole-function edits hit the wrong function 12.7% of the time unperturbed. Robust-Apply: 1 silent misapplication in 8,320 trials.
- States that post-hoc repair triggers only after an observed error, which a silent failure does not produce.
- Limits stated by the authors: synthetic errors; real-model evidence is only 42 commands and 9 edits from one unspecified Claude version, all valid.
- Use for DeepDelve: supports the principle "cheap deterministic check, refuse when unsure, return a recoverable signal". It studies
  wrong-location edits, NOT a model that makes no edit, so support for 1A is by analogy only.

### Li et al., SilentProbe, arXiv 2609.00035, 12 pp
Read: all 12 pp (appendix tables skimmed).
- 2,501 public OpenAPI documents (721,320 parameters): 7.5% declare an enum, 15.2% any machine-checkable constraint, 40.1% of documents state a
  constraint in prose that the schema does not encode. 219 live perturbations over 27 vendors: machine-checkable constraints gave an honest
  error 111/111; prose-only failed silently 44/61 (p = 2e-13). 12 models, 8 families.
- A vocabulary shown only by example was missed 88/88; written out in full, used correctly 88-91%. Promoting it to an enum: 88/88 to 0/89.
- Agent loops that hit a silent zero-row result: 11/94 retried with a different value, 0/11 recovered. False negative to the user 41%,
  invented figure 12%.
- Limits: sample not random (endpoints reachable through one aggregation layer), invented value cases are a lower bound, authors affiliated with the aggregation layer (disclosed).
- Use for DeepDelve: a prose constraint can be ignored silently while a machine-checkable one is enforced. This is the same argument
  as "a prompt sentence is not enforcement". Different mechanism (third-party APIs, not model non-compliance). The 0/11 figure is
  about API vocabulary guessing, not about re-dispatching a writer.

### Melone et al., LLMs as Packagers of HPC Software (SpackIt), arXiv 2511.05626, 18 pp
Read: pp. 1-11 body; Appendix A (ablation table) and B (failure taxonomy) in full; Appendix C (case studies), D (metrics), E (configuration) NOT read in full.
- 308 CMake packages; GPT-5, GPT-4.1, Claude Sonnet 3.7, Mistral Large 2.1. GPT-5 install success 19.7% zero-shot, 82.9% best configuration within 5 repairs.
- Repair loop fed by deterministic build/audit output: GPT-4.1 38.9% to 60% with `spack audit` feedback (one configuration).
- k = 30 run (GPT-5, one similar reference): 74% by attempt 5, plateau about 83% after about 15. "Upon manual inspection, two packages
  oscillated between distinct error states, alternately fixing one issue while reintroducing another." The 5-15 attempt cutoff is the authors' inference.
- Use for DeepDelve: loops fed by deterministic feedback converge, with diminishing returns. The oscillation evidence is two packages.
  Frontier models, not small local ones.

### Zhao et al., GRACE, arXiv 2601.04525, 18 pp
Read: pp. 1-9 (method, experiments, conclusion, limitations); pp. 10-18 (references, appendices B-C: prompts, ablations, retrievers, case study) NOT read.
- Trains Qwen3-4B / Llama3.1-8B with RL (DAPO variant) to emit either an `<evidence>` or an `<llm>` (insufficient evidence + disclaimer) path.
  QASPER 1,451 test, HotpotQA 500 test. Prompt-only Qwen3-4B already scores 42.23 unanswerable-LJ on QASPER; SuRe collapses refusal to about 0-8%.
- Limits stated: single run, no larger models, needs ground-truth evidence annotations.
- Use for DeepDelve: little. Training-based and short-answer QA; fine-tuning is deferred in this project. Confirms abstention behaviour varies hugely by method.

### Itai et al., LIT-RAGBench, arXiv 2603.06198 (LREC 2026), 12 pp
Read: body through conclusion; reference list skimmed.
- 114 Japanese questions (+ English translation); Abstention aspect split: Insufficient Evidence 34.6%, Contradictory 1.9%, Incomplete Chunk 1.9%.
  Abstention accuracy 0.60 (Llama-3.1-8B, ja) to 0.95 (Claude-Sonnet-4, ja). Over-abstention on answerable items 0.213 for Llama-3.1-8B, 0.259 for Claude-Sonnet-4.
- "Claude-Sonnet-4 detected inconsistencies in all tasks" rests on 3 questions. Authors list small sample size as their main limitation.
- Use for DeepDelve: little. Short-answer RAG; shows small models both under-abstain and over-abstain.

### Han et al., Small Language Model Can Self-Correct, arXiv 2401.07301 (AAAI 2024), 9 pp
Read: body through conclusion and references; Appendix (prompt examples, cases) NOT read.
- Fine-tunes 6B-13B models with Partial Answer Masking on about 15,000 samples. OpenBookQA/CommonsenseQA (multiple choice). Gains 0.0 to +5.6 points.
- Llama2-7B and Vicuna models are 97-99% confident and almost never modify an answer (R2R/W2R near 0), i.e. they do not self-correct even after training.
- The claim that prompt-only self-correction is hard for small models is stated in the introduction and not tested in the paper.
- Use for DeepDelve: confident small models leave their output unchanged, which resembles the read-only Builder pass. Different task and fine-tuned.

### Silver et al., Language Models can perform Single-Utterance Self-Correction of Perturbed Reasoning, arXiv 2506.15894, 28 pp
Read: pp. 1-4 (everything through limitations and ethics) and the start of the reference list; Appendices B-E (pp. 5-28, prompts, perturbation lists, examples) NOT read.
- Seven models, 7B to 671B, GSM8K / GSM-Symbolic / MATH-500, synthetic perturbations in a 100-token reasoning stub, single utterance.
- On-policy recovery on GSM8K: Command R7B 10.8% (direct 88.0), Nemo 12B 9.3% (87.7), Gemma 2 27B 12.1% (90.8), Qwen 2.5 72B 41.8%,
  LLaMA 3.3 70B 43.1%, QwQ 32B 66.4%, R1 89.3%. Models under 30B averaged a 78.1 point absolute drop; over 30B 41.7 (R1 excluded).
- The headline "stronger intrinsic self-correction than commonly shown" is driven by the large and reasoning models. For models in our size class the paper's own table shows recovery near 10%.
- Use for DeepDelve: do not read it as support for small-model self-correction. Math chain-of-thought, not text editing.

## Read, but NOT reliable enough to cite

### Khan and Khan, The Cognitive Companion, arXiv 2604.13759, 20 pp (read in full)
- Feasibility study: 35 proxy-labelled probe examples (the second collection had only 3 DEGRADED examples, CV AUROC undefined), quality judged
  by the same model, in-domain evaluation, 3 runs per condition. The authors say so themselves.
- **Reference list contains placeholder IDs ("arXiv:2501.xxxxx", "arXiv:2601.xxxxx") and generic author lists (e.g. "Pipis, Chen, Wang"; "Garcia, Patel, Rodriguez"),
  for the papers it relies on most (looping rates, INSPECTOR, STaSC, SpecRA).** These could not be verified and look machine-generated.
- It attributes "models below approximately 13 billion parameters struggle to reliably self-correct reasoning errors through prompting alone" to
  Huang et al. 2024. I read Huang et al. in full (above) and that statement is not in it.
- Verdict: the "below 13B" claim is unsupported by its cited source and must not be used. The paper is not citable.

## Not obtained / abstract level only (NOT read in full)
- Kaynar, Yan, Kingsford, "When Tools Fail, Agents Fabricate" (NeurIPS 2026, OpenReview HLCpv21u07). Only the conference page abstract was readable (OpenReview returned HTML, not a PDF).
  Reported there: about 2,100 fault-triggered runs, recovery 27-54%, confident fabrication 19-30%, monitor raised correct recovery 42.5% to 48.0% and cut fabrication 24.7% to 20.3%, removing chain-of-thought raised fabrication 7.7 points.
- Holloway and Amin, "Rapid Fixes, Gradual Failures" (ICML 2026 workshop poster, OpenReview QSO4fnmXfR). Abstract only. Reported: gains front-loaded, many non-recovered seeds repeat the same wrong program for ten attempts, full refinement history does not help and hurts on harder benchmarks. Directly relevant to the repeated-identical-output pattern if the full text confirms it.
- The "prompt-based abstention fails when context is misleading rather than absent" study from the first search was never identified; treat as unsourced.

## What this changes in the plan (CURRENT.md 2026-10-22 PLAN)
- Item 2 (standing requirements in every Builder directive): now directly supported by Huang et al. Sec. 5 (put requirements in the initial prompt, not only in the correction prompt).
- Item 1 (detect a write that did not happen): supported in principle by Chen et al. (trace-based oracles) and Althoubi (deterministic pre/post check), but neither tests this exact case. Keep, and measure it.
- Item 4 (deterministic fallback after repeated failures): the loop literature shows diminishing returns (SpackIt plateau) and that a model that repeats its output rarely escapes by itself (Han et al.; Holloway abstract). Keep.
- New option from SCREWS: keep the last draft that passed the gap check and restore it (or its disclosure section) when a later rewrite fails it. The deterministic check is a better selector than the paper's LLM selector. Not in the plan yet; decide after the user reviews.
- Nothing read supports relying on small-model self-correction, so the design should not depend on it.

## Part 2 (2026-10-23): beyond arXiv. Engineering docs, post-mortems, issue trackers, plus three more papers

Source classes differ in weight and are labelled. Peer-reviewed/preprint = measured. Vendor engineering blog = practitioner experience, usually no
numbers. GitHub issue = a reproducible report, not a study. Raw copies were fetched with curl and converted with pandoc/pdftotext, then read;
none of this is a search summary except where marked.

### Papers (arXiv), read
- **Jaroslawicz et al., How Many Instructions Can LLMs Follow at Once? (IFScale), arXiv 2507.11538, 24 pp.** Read pp. 1-9 (method, results, discussion, conclusion); appendices NOT read.
  500 keyword-inclusion instructions in a business-report task, 20 models via OpenRouter, 5 seeds. Reasoning models (o3, Gemini 2.5 Pro) stay near-perfect up to roughly 100-250 instructions;
  best models reach 68% at 500. Primacy bias: earlier instructions are satisfied more, peaking around 150-200 instructions and fading at extreme density. Omission is the dominant error.
  gpt-oss was not tested. Use: a standing-requirements footer of about 3 sentences is far below any density where degradation appears in this study; ordering effects are irrelevant at that size. Caveat: one synthetic task type.
- **Laban et al., LLMs Get Lost In Multi-Turn Conversation, arXiv 2505.06120, 36 pp.** Read pp. 1-12 (method, results, Section 7 implications); appendices NOT read.
  15 LLMs, 200,000+ simulated conversations, 6 generation tasks. Fully-specified single turn averages 90%; the same content revealed over several turns averages 65% (-39%). Putting every shard into ONE fresh prompt (CONCAT) keeps 95.1% of the single-turn score.
  Degradation is mostly unreliability (best-to-worst gap), and lowering temperature does not fix it in multi-turn settings. Repeating earlier information every turn (Snowball) recovers only 15-20% of the loss. Their advice: consolidate everything into one new prompt before retrying.
  Tasks most prone: generative, complex, and non-decomposable (a new requirement changes the whole output), which describes report rewriting. Use: supports Builder passes being fresh-context with ALL standing requirements in one instruction; it argues against piecemeal single-problem directives. gpt-oss not tested; underspecified user conversations, not tool-calling.
- **OPPO AI Agent Team, How Far Are We from Genuinely Useful Deep Research Agents? (FINDER, DEFT), arXiv 2512.01948, 34 pp.** Read pp. 1-10 (method, taxonomy, discussion) and the appendix examples for CSD and SCF; remaining appendices NOT read.
  ~1,000 reports from mainstream deep research agents, 14 failure modes in three groups. Most frequent: Strategic Content Fabrication 18.95% of failures, Insufficient External Information Acquisition 16.30%, Content Specification Deviation 10.73% (report omits explicitly required components), Verification Mechanism Failure 8.72%.
  Their conclusion: pre-constraints and post-verification must extend into the generation stage. No category covers an undisclosed evidence gap, so DeepDelve's `undisclosed_evidence_gap` check has no named counterpart in this taxonomy (closest: CSD and SCF). Annotation is human-LLM co-coded (Gemini 2.5 Flash).
- **Yang et al., SWE-agent, arXiv 2405.15793 (NeurIPS 2024), 118 pp.** Read the design principles (Sec. 2), the ACI description (Sec. 3), the editing/guardrail ablations (Sec. 5.1) and the observation-template appendix; the 118-page appendix is otherwise NOT read.
  Four ACI principles incl. "guardrails mitigate error propagation and hasten recovery". Edit with a linter that discards invalid edits: 18.0% on SWE-bench Lite vs 15.0% without linting vs 10.3% with plain bash editing (single configuration each, GPT-4 Turbo).
  When a command produces no output the harness inserts an explicit message ("Your command ran successfully and did not produce any output") because bare silence confused the agent into extra commands; malformed generations trigger an error response and a re-ask until valid. Use: supports an explicit no-op notice to the writer and re-asking on an invalid pass.

### Vendor engineering documentation (primary, practitioner evidence, read in full)
- **Anthropic, Building effective agents (2024-12).** Agents get "ground truth" from the environment at each step; stopping conditions such as a maximum number of iterations; programmatic "gate" checks on intermediate steps; evaluator-optimizer fits when criteria are clear.
  ACI advice: give the model tokens to think before it writes itself into a corner, keep formats close to natural text, avoid formatting overhead, "poka-yoke" tools. No measurements.
- **Anthropic, Effective harnesses for long-running agents.** Reported failure modes: declares the job done too early; marks features done without testing. Fix: a structured feature list the agent may edit only by flipping a `passes` field. They report the model "is less likely to inappropriately change or overwrite JSON files compared to Markdown files". Anecdotal, no numbers.
- **Anthropic, How we built our multi-agent research system.** Deterministic safeguards (retry logic, checkpoints) combined with the model's adaptability; end-state evaluation over turn-by-turn; a single LLM judge with a rubric (factual accuracy, citation accuracy, completeness, source quality, tool efficiency) was the most consistent; early agents "continued when they already had sufficient results" and, without detailed task descriptions, "leave gaps".
- **Manus, Context engineering for AI agents.** "Recitation": rewriting a todo list so objectives sit at the end of the context, reported as reducing goal drift over about 50 tool calls. Function-calling modes Auto/Required/Specified implemented by response prefill or logit masking. "Keep the wrong stuff in": leave failed attempts in context. No measurements; the keep-failures advice conflicts with the Holloway abstract below.
- **Cognition (Devin), Don't Build Multi-Agents.** "Actions carry implicit decisions, and conflicting decisions carry bad results"; share full traces. It recounts the 2024 "edit apply model" pattern (one model decides, a small one rewrites the file) as faulty because the applier misreads the instruction. Opinion piece, no data.
- **Aider, Unified diffs make GPT-4 Turbo 3X less lazy.** 89 refactoring tasks; baseline 20%, unified diff 61%; lazy-comment tasks 12 to 4. Function/tool-calling edit formats did worse than plain-text formats for code ("stuffing source code into JSON is complicated and error prone"). Flexible harness-side application matters: disabling it gave a 9x increase in edit errors. The benchmark uses a deterministic AST-node-count test to catch elided code. GPT-4 Turbo (2024) only.
- **Claude Code hooks reference (Stop hook).** A completion gate that can block the stop and force continuation, with built-in loop protection: a `stop_hook_active` flag and an 8-consecutive-continuation cap that resets whenever the model calls a tool. This is a progress-based cap, not a raw attempt count.
- **OpenHands Stuck Detector (SDK docs).** Default-on detection of five patterns on semantic equality: 4+ identical action-observation pairs, 3+ identical errors, 3+ consecutive agent monologue messages, 6+ ping-pong alternations, repeated context-window errors.
- **OpenAI, A practical guide to building agents (guardrails, exit conditions) and Agents SDK guardrails docs.** Layered guardrails mixing rules-based and LLM checks; "set limits on agent retries or actions", escalate past a failure threshold. Mostly input/safety guardrails; little on output completeness.

### GitHub issues on the serving layer (Ollama / llama.cpp), read in full
- **ollama/ollama #17274 (open; reproduced on 0.32.1, 0.32.15, 0.34.0).** When a text-tag tool call (`<tool_call>`) fails to parse, Ollama discards everything: empty content, no tool_calls, `finish_reason: stop`, yet nonzero `eval_count`. The reporter corrected the original claim: the model had emitted the tool's description as its name, so the parser was right, but the drop is invisible to the caller. Shown for qwen2.5/qwen3, not gpt-oss.
- **llama.cpp #30377 (open).** gpt-oss-120b with tools + `response_format: json_schema` loops in the reasoning channel and never emits a tool call (tool_calls=0 until the token limit); each of tools-only and schema-only works. The reporter's mitigation is a two-pass design.
- **ollama/ollama #11867 (closed as duplicate), #12158 (open).** gpt-oss:20b with `format` returns empty content; reasoning field missing after a tool round trip.
- Search results (titles only, not read in full): ollama #19060 (empty gpt-oss output on a Vulkan backend), ollama-python #679 (gpt-oss tool call text left in content), a LangChain forum thread (gpt-oss tool call placed in the wrong channel), mantis #99 (empty message through LiteLLM only).
- Use for DeepDelve: these show that "the turn ended with no tool call" can be a serving-layer artifact as well as the model's choice. None of them reproduces our exact case (gpt-oss:20b, Ollama, no `format`, read tools then no write), so this is a hypothesis to test, not a cause.

### Abstract-level only (not read in full)
- NeurIPS 2026 "When Tools Fail, Agents Fabricate" and the ICML 2026 workshop "Rapid Fixes, Gradual Failures": see "Not obtained" above; unchanged.

### Looked for and not obtained
- ICD 203 (US intelligence analytic standards, which require identifying information gaps): every dni.gov/odni.gov/archive.dni.gov host returned an "Access Denied" bot-protection page to a plain fetch. Not bypassed. Cited only as a pointer; its wording is unverified.
- Nothing found, in any source class, that measures how often an LLM writer ends a repair turn without making the edit, or that studies disclosing unanswered sub-questions in long-form LLM reports.

### What Part 2 changes in the plan
1. **New first step: test the serving layer before building detection.** Replay the Builder's exact instructions K times against `/api/chat` (gpt-oss:20b, same tools) and classify each outcome: write call, read call, text only, or empty with `eval_count` > 0. This decides whether a read-only pass is model choice (re-dispatch, item 1) or a dropped call (fix at the client). GPU is idle; no code change needed.
2. **Item 1 gains an alternative (1D):** have the Builder return the finished report as plain text and let the harness write the file. Supported by analogy: Aider (tool-call formats worse than plain text for code), Anthropic ACI advice (avoid formatting overhead), SWE-agent (harness-side application and verification). DeepDelve already has narration salvage (`_salvage_narrated_report`) for the failure case; this would make it the primary path for this problem. Evidence is indirect.
3. **Item 4 (cap) should be progress-based:** Claude Code's cap resets when a tool is called; OpenHands detects identical consecutive outputs. A hash of the report text repeated across Builder passes is a direct no-progress signal that the current attempt counter lacks.
4. **Item 2 stays, with a boundary:** standing requirements are invariants, not a merge of several problems into one dispatch (that was tried and reverted, see memory). Laban supports one consolidated fresh prompt; Cognition supports passing decisions forward; Holloway's abstract says full history does not help. A compact "already satisfied, do not regress" list is the middle path and is untested.
5. **No-op feedback (SWE-agent):** when a pass changes nothing, say so explicitly in the next directive.
6. **Structured requirement list (Anthropic harness):** keep `evidence_gaps` as structured data the harness checks, rather than prose the Builder may delete. Already the case in state; the report side is the gap.
7. Nothing in Part 2 supports relying on the model to retain a fix across rewrites without an external check.

## Corrections to the earlier version of this file
- It said SilentProbe showed "retrying with different inputs often doesn't help". Precisely: 11 of 94 loops retried and 0 of 11 recovered; the setting is guessing an undisclosed API vocabulary.
- It presented LIT-RAGBench's Claude result as a general finding; it rests on 3 questions.
- It relayed "prompt-only self-correction is unreliable below ~13B" as a claim to verify. Verified: false attribution, see Cognitive Companion above.
- It listed 2506.15894 as counter-evidence that self-correction is stronger than believed; the paper's own table shows about 10% recovery for models under 30B.
- It marked nothing as read. The coverage above is now explicit; unread parts are named per paper.
