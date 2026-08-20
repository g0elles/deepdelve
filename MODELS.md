# Model Choice — Local-Model Bake-Off

Summary of every local candidate tried against DeepDelve's real multi-agent roles (Planner/Searcher/
Analyzer/Builder/FindingsWriter), evaluated against two live benchmarks (13-run Colombia B2B rubric,
`eval/colombia_b2b_benchmark.md`; sales-forecasting/heuristic-algorithms rubric,
`eval/sales_forecasting_benchmark.md`), same reliability bar throughout: **passing an isolated
tool-call schema test is not sufficient evidence a model behaves reliably in the real role** — every
candidate below was run through the actual pipeline, not just a smoke test. Full evidence trail,
live-run detail, and ongoing trials are in `ROADMAP.md`'s "Local-model bake-off" entry; this file is
the current-state summary only. Verdicts follow the project's own [Model Evaluation Standard]
(`ROADMAP.md`, top-level section) — a discard needs more than one run, backend/serving-layer bugs are
named explicitly rather than blamed on the model, etc.

Moved out of `README.md` into this dedicated file (2026-07-29) because the per-candidate detail had
outgrown what a plain Markdown table renders legibly — each row's notes were paragraph-length,
wrapping badly in table cells. Format here: one subsection per candidate, grouped by verdict.

**21 candidates tried as of this writing.** `gpt-oss:20b` is still the only one with a full benchmark
pass on both standing benchmarks.

---

## Passed / Default

### `gpt-oss:20b`
- **Size/VRAM**: 13GB
- **Best result**: **7/10** (Colombia B2B); real grounded report on every sales-forecasting re-run
- **Verdict**: **Default.** The only candidate with a full benchmark pass on both standing
  benchmarks. High run-to-run variance, but bad runs are honest-empty, not fabricated. ~15-20
  min/run. Its own chain-of-thought can't be fully disabled either (see the Qwen3-family
  think-mode note below for the general issue), but Ollama keeps it in a separate `reasoning`
  field DeepDelve's client never reads as the model's actual output, so this is benign here.
- **Standing, still-open content-quality gap, reconfirmed live 2026-07-31 after that session's
  completion-pipeline starvation fixes shipped**: on the multi-facet sales-forecasting benchmark,
  the model reliably abandons the HARDER half of a two-facet query rather than fabricating or
  refusing — same pattern first logged 2026-07-14/18 (dropped the Colombia cultural-context half
  that session), still present after every structural pipeline bug found that night was fixed. The
  2026-07-31 run's `final_report.md` was real, honestly caveated, and correctly grounded (no
  fabrication) but delivered neither the query's own "top 5 heuristic algorithms" ask nor any
  Colombia cultural-pattern integration — the Colombia/heuristic-algorithm sources it fetched sat
  unused in the References list, `check_report_underuses_findings`/`_evidence` correctly flagged
  this every attempt, and the model still hadn't fixed it by the time the wall clock ran out.
  **This is a genuine model-capability gap, not a pipeline bug** — the completion-check machinery
  is now working exactly as designed (real convergence, no starvation, honest output), and the
  gap it keeps correctly flagging is the model's own unwillingness/inability to actually act on
  "cover every facet" feedback within a bounded retry budget. See ROADMAP.md's Pending for the
  literature-research angle on this (multi-facet task abandonment under iterative self-correction).
  **Follow-up live test, same night**: an explicit `edit_workspace_file`-routing directive fix
  (commit `67e4b00`, the smallest literature-grounded attempt) changed Builder's TOOL choice
  correctly but did not fix the underlying gap — a subsequent re-test's report went from
  "answers ~1/3 of the query" to answering **zero** of the ML/heuristics half (100% Colombian-
  festivals content, the deep-learning facet dropped entirely), despite the directive explicitly
  saying "do not touch any other part of the report." Confirms this is genuinely the self-
  correction blind spot / aggregator noise the literature describes, not a tool-choice gap —
  prompt-level fixes have hit their ceiling; per-facet Builder dispatch is the justified next
  attempt (see ROADMAP.md Pending).

---

## Disqualified

### `qwen3.6` (35b-a3b) †
- **Size/VRAM**: 23GB
- **Best result**: 1/10
- **Verdict**: Researches well, synthesizes disastrously at scale (reconstructed 22/22 cited URLs
  from filenames).

### `mistral-nemo:12b`
- **Size/VRAM**: 7.1GB (Ollama) / ~8.3GiB (vLLM, `bitsandbytes` 4-bit)
- **Best result**: 2/10 (Ollama); `Report: NOT WRITTEN` (vLLM, `thin_coverage`)
- **Verdict**: Disqualified on both backends. Original Ollama score stood unconfirmed for a while
  after a real vLLM infra block (`chat_template_kwargs` rejected by Mistral's native tokenizer,
  since fixed via `settings.skip_chat_template_kwargs`). Re-tested with the fix: genuine
  engagement this time (13 sources, 0/7 search failures, 8 findings) but joins the
  `thin_coverage` non-convergence family already seen in `qwen3:4b`/`qwen3:8b` — 4/4
  completion-check attempts hit the identical problem, retry budget exhausted, no report.

### Gemma 4 12B (`SetneufPT/Gemma4-12B-IT-QAT_Q4_64K_16GB-GPU`)
- **Size/VRAM**: 7.2GB
- **Best result**: `Report: NOT WRITTEN`
- **Verdict**: Disqualified: reasoning-loop near the end, repeated `delegate_tasks` rejections.

### Gemma 4 12B (`yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF`)
- **Size/VRAM**: 7.4GB (Q4_K_M)
- **Best result**: `Report: NOT WRITTEN`, run twice, identical failure both times
- **Verdict**: Disqualified: a different, more severe failure than the other Gemma-4-12B
  candidate above — repeatedly calls `write_todos` with byte-for-byte identical arguments (a
  literal repetition loop, not incremental revision), continuing even after the tool explicitly
  errors "you MUST summarize what you've done and stop" — DeepDelve's own anti-loop quota is what
  ends the run, not the model recognizing completion. Confirmed via two runs, different queries
  (one deliberately simple). Not a serving-layer bug: nothink (needs `reasoning_effort:"none"`,
  not `chat_template_kwargs.enable_thinking`) and tool-calling were both confirmed clean via
  direct API test beforehand.

### Bonsai-8B (PrismML, 1-bit)
- **Size/VRAM**: 1.2GB
- **Best result**: `Report: NOT WRITTEN`
- **Verdict**: Disqualified for a worse reason than Gemma 4: skipped `write_workspace_file`
  entirely in writer roles despite research working fine.

### `qwen3:4b` †
- **Size/VRAM**: 2.5GB
- **Best result**: `Report: NOT WRITTEN` (8/8 retries exhausted on `thin_coverage`)
- **Verdict**: Disqualified: real research happens, but repeats a canned "research scope is
  complete" non-response instead of acting on the completion-check's corrective nudge — a
  non-convergence pattern also seen elsewhere (10x redundant identical `write_workspace_file`
  calls on a trivial query).

### `qwen3:8b`
- **Size/VRAM**: 8.8GB (fp8, vLLM)
- **Best result**: `Report: NOT WRITTEN` twice in a row, identical `thin_coverage` attempt
  sequence both times
- **Verdict**: Disqualified, double-confirmed clean of every prior excuse: retested on vLLM (not
  Ollama) with nothink mode and tool-calling both verified clean via direct API tests beforehand.
  Same failure class as `qwen3:4b`: once `thin_coverage` fires, repeats the same narrated summary
  verbatim across retries instead of acting on the corrective nudge. Four total occurrences of
  this exact non-convergence pattern now recorded across different backends/models.

### `llama3.2:3b`
- **Size/VRAM**: 2.0GB (Ollama) / 6.0GB bf16 (vLLM)
- **Best result**: fail (schema stage), both backends
- **Verdict**: Disqualified, double-confirmed clean of the "maybe it's Ollama's fault" excuse:
  retested on vLLM with a real gated HF checkpoint (not the Ollama GGUF), `llama3_json` tool
  parser — the exact same `#6155` stringified-array shape reproduced 4/4 times on a completely
  different serving stack. **Confirmed via vLLM's own official docs** ("Known issues" for Llama
  Models): "The model can generate parameters in an incorrect format, such as generating an array
  serialized as string instead of an array." This is Llama 3.2's own limitation, not an
  Ollama-specific bug — the original `#6155` framing is disproven for this candidate specifically.

### `qwen2.5:3b-instruct`
- **Size/VRAM**: 1.9GB (Ollama) / 6GB bf16 (vLLM)
- **Best result**: `Report: NOT WRITTEN` (Ollama, `missing_findings`); malformed tool-call JSON
  (vLLM, schema stage)
- **Verdict**: Disqualified on both backends, different failure modes each time — not a
  single-cause bug. Ollama run: passed the schema test cleanly, researched fine, but
  `FindingsWriter` never successfully called `write_workspace_file` across 8 attempts (same root
  cause as Bonsai-8B). vLLM retest: a nested-array `delegate_tasks`-shaped tool call consistently
  produces genuinely malformed JSON — closes the `arguments` object but omits the final closing
  brace for the outer wrapper, confirmed via 5+ reproductions and direct inspection of the
  parser's own extraction (the parser is correct; the model's own JSON is broken at the source,
  not a token-budget cutoff).

### `InternScience/Agents-A1-4B` †
- **Size/VRAM**: 5.2GB (Q8_0 GGUF, Ollama)
- **Best result**: `Report: NOT WRITTEN`, run twice, same fabrication signature both times
- **Verdict**: Disqualified: real research works fine (13 sources fetched, 0/12 search failures),
  but `FindingsWriter` repeatedly fabricates citations to real-sounding but never-fetched
  Wikipedia URLs from its own training knowledge, and `PeerReviewer` approves the rewrite anyway
  both times — only the separate grounding check catches it. Confirmed via vLLM on this candidate
  ended in a reproducible fixed-size OOM bug independent of context length (likely the
  multimodal vision-encoder profiling pass); switched to Ollama, where nothink mode is not
  honored (harmless: reasoning stays isolated, doesn't pollute content) but tool-calling is clean.

### `qwen3:4b` + GRPO fine-tune (`thin_coverage`) †
- **Size/VRAM**: 4.3GB (Q8_0 GGUF)
- **Best result**: ~1-2/10 both times; still `not_grounded`, retry budget exhausted
- **Verdict**: Disqualified, but the targeted fix worked: zero `thin_coverage` stalls in either
  run (the exact failure the fine-tune targeted is gone, confirmed 8/8 on held-out eval too).
  Fails on a second, untouched failure mode: citation fabrication + real content dropped from
  synthesis. A structural fix (a grounding-check warning was being silently truncated before
  reaching `findings.md`) measurably improved this on re-test — grounded citations went from 0/8
  to 3/9 — but didn't fully close it: the model still sometimes cites a URL its own source
  material explicitly flags as unverified when it has no real alternative. Only the training
  pipeline's own `enable_thinking=False` (applied directly via HF's chat template, no Ollama
  involved) is unaffected by the † caveat — the live Ollama benchmark run itself is not.

### `qwen3-4b-combined-v2-lora` (7-dimension combined GRPO, 2026-07-28) — DISQUALIFIED, clean
- **Size/VRAM**: 4.3GB (Q8_0 GGUF, merged+redeployed 2026-08-19 as `deepdelve-qwen3-4b-combined-v2`)
- **Best result**: held-out eval 0.615→0.781 (real generalization, not ceiling); TWO live
  benchmarks, both DISQUALIFIED
- **Verdict**: First live benchmark (2026-07-28) was confounded by the Qwen3 think-mode-passthrough
  Ollama bug (burned 2-3x more tokens on reasoning than intended, tripped an unrelated DeepDelve
  bug too — both root-caused and fixed the same day). **Clean re-test done 2026-08-19**: merged the
  still-on-disk LoRA fresh, deployed via `api.backend: "ollama"` with `enable_thinking: true`
  (confirmed via direct curl that `think: true` correctly isolates reasoning into its own field on
  this exact tag — `think: false` reproduces the bug, dumping raw CoT into `.content`). Result:
  score 0.25 (worse than the confounded run's 0.5), `final_report.md` is the deterministic-salvage
  banner — the model narrated in chat instead of ever calling `write_workspace_file` across its
  full writer-retry budget, and `findings.md` repeatedly failed grounding on the same fabricated
  URLs (`rentremote.com`, `nomadsembassy.com`) across consecutive rebuild attempts. **Confirmed
  disqualification, not confounded this time**: the fine-tune's targeted objectives held (the
  held-out gains are real), but citation fabrication and writer-dispatch convergence — untouched
  failure modes — are still broken at 4B scale even with reasoning cleanly isolated. Closes the
  "not yet done" clean-re-test caveat that stood since 2026-07-28.

### `Ornith-1.0-9B` (deepreinforce-ai, dense, Qwen3.5-arch, MIT) — DISQUALIFIED, clean, 2026-08-19
- **Size/VRAM**: 9.5GB (Q8_0 GGUF, `deepdelve-ornith-9b-jsonfmt`)
- **Best result**: score 0.000, `final_report.md` is the deterministic-salvage banner
- **Verdict**: Closes the long-standing INCONCLUSIVE status (every prior run had an independent,
  non-model confound attached — see `RESEARCH.md` §14/§15 for the full trail: chat-template
  empty-`<think>`-injection defect, a native-backend tool-call corruption bug, two DeepDelve
  architecture bugs). All of those are fixed. This re-test used the fully-patched
  `deepdelve-ornith-9b-jsonfmt` tag (0/18 `web_search` failures in its own prior live-verification)
  via `api.backend: "ollama"` with `enable_thinking: false` — confirmed via direct curl that
  `think: false` is the CLEAN setting for this model on the native endpoint even with tools present
  (the opposite of what `qwen3-4b-combined-v2-lora` needed; verify per-model, don't assume).
  **Result**: real research happened cleanly (17 sources fetched, 22 findings, no tool-call
  corruption, no infinite loop) — but at the delegation-quota-exhaustion point, the Planner
  narrated its wrap-up as chat prose instead of ever calling `write_workspace_file`, exhausting the
  full writer-retry budget; `findings.md` never got accepted at all. Same "narrate instead of call"
  failure class this project has now seen across the large majority of sub-14B candidates tried
  (Bonsai-8B, `qwen2.5:3b-instruct`, `mistral:7b-instruct`, `mistral-nemo:12b`, `hermes3:8b`, this
  candidate too) — independently corroborated for this specific model family by two Reddit threads
  (§14f) as a harness-agnostic trait, not something a serving-layer or template fix reaches.
  **Genuinely the strongest research-quality candidate tested at this size** (matches its own
  earlier INCONCLUSIVE runs' cold-start synthesis strength) — the ceiling isn't research capability,
  it's converting research into a written artifact under real task-completion pressure.

### `granite3.1-dense:8b`, `phi4-mini:3.8b`
- **Size/VRAM**: 5.0GB / 2.5GB
- **Best result**: fail
- **Verdict**: Disqualified at the tool-call smoke test itself: both narrate the call as literal
  text despite each model card explicitly claiming function-calling support.

### `llama3-groq-tool-use:8b`
- **Best result**: fail
- **Verdict**: Rejected at the tool-call-schema stage.

### `hermes3:8b`
- **Size/VRAM**: ~5GB 4-bit (vLLM, `bitsandbytes`)
- **Best result**: `not_delegated`, two runs, different queries
- **Verdict**: Disqualified — passed the isolated tool-call smoke test cleanly (3/3, real
  structured array, no `#6155`-class bug), but under DeepDelve's real system prompt it narrates
  fake system text instead of ever calling a tool. Most severe case: fabricated an entirely
  fictional "context length exceeded" error message and a fake retry narrative (confirmed
  invented — vLLM's own server log shows no such error and KV cache usage was only 1-13% at the
  time), then repeated a similar fabrication pattern in a second, independent run on a different
  query.

### `mistral:7b-instruct`
- **Size/VRAM**: 14GB bf16 cache / ~5GB 4-bit runtime (vLLM, `bitsandbytes`)
- **Best result**: `not_delegated`, two runs, different queries, identical failure
- **Verdict**: Disqualified — but the original "rejected at schema stage" reason is now WRONG and
  superseded: retested on vLLM with `settings.skip_chat_template_kwargs` (new fix, unblocks all
  Mistral-family candidates from a permanent, by-design vLLM restriction on Mistral-tokenizer
  requests), isolated tool-call smoke test passed cleanly 3/3 (real structured array, no
  `#6155`-class bug). The real benchmark run reveals a genuine, different capability gap instead:
  the model consistently narrates its planned `delegate_tasks` call as literal markdown text
  instead of emitting a real tool call, even with `--enable-auto-tool-choice` — same "narrate
  instead of call" failure class as Bonsai-8B/`qwen2.5:3b-instruct`, here at the Planner's very
  first dispatch.

### MiniCPM5-1B (single-model replacement AND paired specialist)
- **Best result**: paired-specialist run confounded (VRAM-forced Planner/Builder swap off
  `gpt-oss:20b`); full single-model replacement run doubly corroborated — 0 `delegate_tasks`
  calls across two independent runs, `Report: NOT WRITTEN` both times
- **Verdict**: **Disqualified in both forms tested, final and doubly corroborated.** The paired
  form is confounded per the project's own Model Evaluation Standard (point 2 — isolate the
  candidate as the only variable) and not re-litigated per the user's own decision; the clean,
  isolated single-model form reproduced the identical core failure on an independent re-run (63
  events, zero real `delegate_tasks` calls). No further MiniCPM5-1B testing planned.

---

## Inconclusive / Blocked / Not Yet Viable

Not disqualifications — each hit a real infrastructure or evaluation-fairness blocker before the
model's actual capability was ever cleanly tested, per Model Evaluation Standard point 1.

### `qwen2.5-coder:14b-instruct`
- **Size/VRAM**: 9.9GiB weights (vLLM, `bitsandbytes` 4-bit)
- **Result**: **INCONCLUSIVE** — not a pass, not a disqualification
- **Detail**: An intermittent crash on first launch turned out to be transient, not
  deterministic (a clean retry got past it). First smoke test used the wrong parser (`hermes`) —
  this model was never trained on that convention; it uses `<tools>` tags, needing a community
  parser plugin (`hanXen/vllm-qwen2.5-coder-tool-parser`, reviewed and installed). With the
  CORRECT parser, isolated smoke test showed ~50% unreliable extraction (2/4 clean structured
  calls, 2/4 returned empty `arguments: "{}"` despite a normal completion-token count) — real
  capability was never cleanly established either way.

### MiniCPM3-4B (single-model candidate)
- **Result**: **BLOCKED, not disqualified and not re-testable as-is** — a real infrastructure
  hang, not a capability verdict
- **Detail**: Genuinely promising on paper (documented BFCL v2 71.6, Apache-2.0, native vLLM
  model support). A real hardware ceiling was found and correctly applied BEFORE benchmarking
  (~6144-token max feasible serving context on this GPU, under the project's ~16K floor) — the run
  was allowed to finish anyway as informational. Result: a real hang, not a clean pass or fail —
  zero visible progress for 16+ minutes, traced to OpenBMB's own reference tool-parser re-scanning
  the entire accumulated generation text with a catastrophic-backtracking-risk regex on every
  streamed token, not anything DeepDelve's own code touches. Never reached the point of testing
  actual research/delegation behavior, so this doesn't count as a settled discard — an open
  infrastructure question (the reference parser needs incremental parsing, not full-text
  re-scanning) rather than "MiniCPM3-4B discarded."

### MiniCPM4-MCP (specialist-role candidate)
- **Result**: real infrastructure built and kept; model itself not yet a stable candidate
- **Detail**: The tool-use SFT checkpoint's chat template emits a Python-code-block format
  (`<|tool_call_start|>func(arg=val)<|tool_call_end|>`), not OpenAI-style JSON `tool_calls` —
  Ollama's generic tool-calling support fails outright against it. Built and kept
  `finetune/minicpm_tool_proxy.py`, a translation proxy (verified against OpenBMB's own reference
  implementation after an initial pass missed two real gaps — anti-repeat-tool-call guidance, and
  keyword-collision/hyphenated-name argument parsing — both fixed and reverified). With both fixes
  in place, the specific bug that motivated the proxy (task-name-as-filename looping) did NOT
  recur, but the run surfaced different reliability problems in its place (search-quota
  exhaustion from excessive re-querying, a noisy-search topical mismatch the existing check
  correctly caught, and a real accuracy regression vs. an earlier run). Still not a stable
  specialist-role candidate as of this evaluation — reusable proxy infrastructure, inconclusive
  model verdict.

### `Tongyi-DeepResearch-30B-A3B` (`Alibaba-NLP/DeepResearch`)
- **Size/VRAM**: 18.6GB (Q4_K_M) / 13.5GB (IQ3_M)
- **Result**: two real benchmark attempts, both impractical
- **Detail**: 30B MoE / 3.3B active, trained specifically for long-horizon research — but a
  SINGLE fine-tuned model operating via ReAct/"IterResearch," not a multi-agent system in
  DeepDelve's sense (no Planner delegating to typed specialists with independent context), and no
  published runtime grounding/citation-verification layer comparable to DeepDelve's own.
  Chat-template/tool-call compatibility confirmed clean (real structured `tool_calls`, not raw XML
  text) and a `--depth quick` trial confirmed real delegation behavior. Q4_K_M: killed at 1h6min —
  genuinely computing the whole run, real progress happened, just far too slow to be practical
  (this is also the run that exposed and got a real `max_run_minutes` bug fixed). IQ3_M: worse on
  the real workload despite being faster on the trivial smoke test — 37+ minutes against the
  actual Planner prompt with ZERO progress (no `write_todos`, no `delegate_tasks` at all). Not
  recommended for further local benchmarking without a materially different quant or a
  context/prompt-length investigation into why the full system prompt specifically breaks it.

---

## Not Attempted

### `devstral:24b`
- **Size/VRAM**: ~47.1GB bf16 → ~17GB estimated at 4-bit
- **Verdict**: Discarded on hardware grounds before any pull: real weight footprint (confirmed
  via HF API, the repo's 94GB listing double-counts two packagings of the same weights) estimated
  at ~17GB even after 4-bit quantization, exceeding the entire 17.1GB VRAM card before KV
  cache/overhead. Same standard as `qwen3.6`.

### GLM-4.7-Flash, `Ornith-1.0-35B`
- **Verdict**: Ruled out on hardware grounds before any pull — smallest available quants
  (19GB/21.2GB) exceed this card's 17.1GB VRAM budget. No GPU time spent on either.

### Candidate shortlist, researched 2026-08-19 (web search)
Compiled after `qwen3-4b-combined-v2-lora`'s clean disqualification closed off the 4B fine-tune
path — looking for a lighter-than-`gpt-oss:20b` GENERAL-PURPOSE candidate, ranked by promise:
- ~~`Ornith-1.0-9B` clean re-test~~ **DONE 2026-08-19, DISQUALIFIED — see its own entry above.**
- **Ministral-8B-Instruct-2410** (Mistral AI, Apache-2.0 weights, Mistral Research License for the
  instruct checkpoint) — general-purpose, NOT a narrow function-calling finetune (unlike xLAM-2/
  watt-tool-8B below). Native long context (128k), interleaved sliding-window attention. Not in
  Ollama's core library — community GGUF tags exist (`nchapman/ministral-8b-instruct-2410`,
  `QuantFactory/Ministral-8B-Instruct-2410-GGUF`). Known quirk: vLLM's native Mistral-tokenizer
  mode has the same `chat_template_kwargs`-rejection restriction `mistral:7b-instruct` hit — this
  project already has the fix (`settings.skip_chat_template_kwargs`, 2026-07-26), so it's a known,
  already-solved cost, not a new blocker. Genuinely untested — never pulled this session.
- **Falcon3-10B-Instruct** (TII, Apache-2.0/Falcon license) — published BFCL 86.3, native Ollama
  tag (`falcon3:7b`/presumably a `10b` tag), state-of-the-art among sub-13B models on several
  published benchmarks (MMLU 73.1, MBPP 73.8). Closest published size to the ~14B capacity-floor
  evidence this project already has (arXiv:2601.16280) while staying meaningfully lighter than
  `gpt-oss:20b`. Never pulled this session.
- **Phi-4-Mini (14B variant, not the already-disqualified 3.8B)** — real caveat, not a fresh lead:
  `phi4-mini:3.8b` already failed this project's tool-call SMOKE TEST (narrates the call as literal
  text despite the model card claiming function-calling support) — a family-wide architectural
  trait is a real risk the 14B size doesn't automatically fix. Worth one cheap isolated tool-call
  smoke test before any full benchmark, not a priority pull.
- **Deprioritized, not re-litigating**: `xLAM-2-8b-fc-r`, `watt-tool-8B`, `ToolACE-8B` — narrow
  function-calling-specialist finetunes (Llama-3.1-8B base), already surfaced by a prior session's
  research pass (`ROADMAP.md`, 2026-07-18) and explicitly deprioritized then: "a real risk for the
  writer role per this project's own repeated lesson [narrow fine-tunes overfit to schema
  correctness at the cost of general instruction-following] — not worth GPU time until a
  general-purpose candidate looks more promising." Still true; the four candidates above are all
  general-purpose.
- **Literature lead, not yet read in full (per this project's own citation-verification rule —
  do NOT cite its findings as verified until it is)**: *AgentFloor: How Far Up the Tool Use Ladder
  Can Small Open-Weight Models Go?* (arXiv:2605.00334) — title is a direct match for this exact
  question. Worth a full read before committing further GPU cycles to this search, not just this
  session's web-search snippet.

---

## Hosted

### NVIDIA NIM (free tier)
- **Verdict**: Best discovery quality of anything tried, but the free-tier quota wall kills a
  multi-agent run at ~10 min. Needs a paid endpoint; this project is local-only for now.

### `deepseek-v4-flash` / `deepseek-v4-pro` (DeepSeek hosted API)
- **Size/VRAM**: N/A (hosted API)
- **Best result**: 0.2/1.0 (flash, llama_judge, Colombia B2B complex item); 0.0/1.0 (pro, same
  item) — both well below `gpt-oss:20b`'s 0.7/1.0 baseline on the same rubric.
- **Verdict**: **Disqualified, both variants** (2026-08-04, 4 total runs: 2x flash via web
  UI/`src/api.py`, 1x flash headless CLI, 1x pro headless CLI — clears the Model Evaluation
  Standard's 2+-run bar for a discard claim). Root cause is genuine model unreliability, not
  infra, confirmed only after two real harness bugs found along the way were fixed first:
  1. **`api.backend: "openai_hosted"` added** (`src/engine/orchestrator.py`'s
     `_get_default_options`/`_HOSTED_PROVIDER_THINKING_EXTRA_BODY`) — the existing `"openai"`
     backend's thinking-mode control (`chat_template_kwargs` + `reasoning_effort:"none"`) is a
     vLLM/local-serving convention DeepSeek's hosted API silently ignores, leaving thinking mode
     stuck at its own default (ON, effort `high`, per api-docs.deepseek.com/guides/thinking_mode).
     Confirmed via `_agent_session.json` (zero `reasoning_content` occurrences post-fix) that this
     genuinely disabled thinking — but it did NOT fix the failure (near-identical outcome/timing
     before and after), so verbose narration-as-content, not hidden reasoning, was the real driver
     of the next bug.
  2. **`check_task_verification_flagged` guardrail starvation fixed** (`src/api.py`'s
     `context_budget_chars` cutoff, `~L297-334`): the old unconditional force-jump to the
     terminal/salvage branch gave this check ZERO real retry attempts before cutting the run off
     — DeepSeek's narration volume alone blew the same 50000-char budget `gpt-oss:20b` never
     approaches. Brought to parity with `run_cli`'s existing two-stage nudge-then-cutoff (one
     wrap-up turn before the hard cutoff, not before).
  3. **With both fixed, the model still failed** — and confirmed via `subagent_invocations` that
     the reason is structural to the model's own behavior, not the harness: DeepSeek
     **re-fabricates the same citations on redo**. `check_task_verification_flagged` correctly
     never let the run advance to `check_missing_findings` (which dispatches the real
     `FindingsWriter`/`Builder` writer roles) because the SAME flagged task names kept recurring
     across retry attempts instead of resolving — a genuine reliability failure (repeatedly citing
     sources that don't match anything actually fetched, despite 32-51 real fetched sources
     sitting unused each run), not a config or budget artifact. `deepseek-v4-pro` scored WORSE
     than flash despite ~3x the price, ruling out "just use the bigger model" as a fix.
  - **Not disqualified for**: tool-calling mechanics themselves (zero tool-call schema errors in
    3 of 4 runs; the 4th run's 37 tool errors were sub-agents passing task names instead of
    filenames to `read_workspace_file`, a separate argument-binding weakness worth naming but not
    the deciding factor) or "doesn't have write access" confusion (the Planner role genuinely has
    no `write_workspace_file` tool by design — DeepSeek's own statement to that effect was
    accurate, not a hallucination; it just never earned its way to the role that does have it).

---

## Cross-cutting notes

**† Every Qwen3-family row above was very likely benchmarked with uncontrolled chain-of-thought
reasoning, not the clean output its score implies.** Discovered 2026-07-21 while auditing the same
question for MiniCPM5-1B: confirmed live via direct `curl` against Ollama 0.31.2 that neither
`chat_template_kwargs.enable_thinking: false` (OpenAI-compat) nor Ollama's own native `think: false`
field suppresses Qwen3's reasoning — and native `think: false` is actively worse than doing nothing,
dumping the raw unstructured chain-of-thought directly into `message.content` (no separate `thinking`
field at all), while `think: true` correctly separates it out. Since DeepDelve's client
(`agent_framework`) treats `.content` as the model's actual working output, every marked row was
almost certainly reasoning-polluted in its tool-call arguments and written text throughout the whole
benchmarked run — a real, previously-unknown contributing factor to these disqualifications (on top
of, not instead of, the capacity-floor evidence in `README.md`'s References section). **Confirmed via
a direct vLLM test that this is Ollama's bug, not a Qwen3 model limitation**: `enable_thinking: false`
against `Qwen/Qwen3-4B` on a real vLLM server (genuine chat-template evaluation) gives a clean answer
with zero `<think>` content and correct, unpolluted tool-calling. One row below (`qwen3-4b-combined-v2-lora`)
has now been re-benchmarked through this clean path — see its entry above; the rest of the rows
above still haven't, and their existing scores stand as the best available evidence, just not as
clean evidence as previously assumed. Full trace in `ROADMAP.md`. **Note discovered doing that
re-test (2026-08-19): "clean" via `api.backend: "ollama"` means `enable_thinking: true`, not
`false` — `think: false` on the native endpoint reproduces the identical bug (raw CoT dumped into
`.content`, confirmed via direct curl); only `think: true` correctly isolates reasoning into its
own field. The token-budget cost this implies (reasoning always on) is a real, accepted trade-off
for a clean measurement, not a bug to work around.**

**Lower-cost re-test path available since 2026-07-28**: at the time the think-mode bug above was
found, the only known fix was switching the whole serving stack to vLLM — a big step, since reverted
(`ROADMAP.md`'s "Ollama restored" entry). `api.backend: "ollama"` (`ARCHITECTURE.md` §6) now gives
the same clean nothink behavior directly through Ollama's native `/api/chat` endpoint, live-verified
for `gpt-oss` and `Ornith-1.0-9B` — no backend swap required. Re-testing any `†`-marked row through
this path is now a real, low-friction option; still not done, still an open call, but no longer
blocked on a bigger infrastructure decision the way it was on 2026-07-21.

**The meta-result holds across every run and model: no fabricated report has ever gotten past the
grounding gates unlabeled.** The defense layer is the validated product; model quality only
determines how often it has to fire. See `ROADMAP.md`'s bake-off entry for the full trial history
and untested candidates (Ministral-8B, two function-calling-specialist finetunes) noted for later.
