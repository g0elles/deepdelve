# Engineering Reliable Local LLM Agents: Lessons from Building and Evaluating DeepDelve

**Gabri Elles**
*DeepDelve project, [github.com/g0elles/deepdelve](https://github.com/g0elles/deepdelve)*

## Purpose and Scope

This document is an engineering case study, not a claim of research novelty. It consolidates what
was learned building DeepDelve, a local-first, multi-agent research assistant, through a documented
history of live failures, model evaluation, infrastructure debugging, and fine-tuning experiments.
The individual techniques described here mostly have precedent elsewhere; `METHODOLOGY.md`'s own
novelty assessment addresses that in full (see References), and this document does not repeat it in
detail by design. The value of the project is in what building a working
system against real hardware and model constraints actually required, and in the discipline of
tracing every observed failure to its true layer, model, inference server, tool parser, chat
template, orchestration, or verification, before drawing a conclusion from it. Every specific number,
verdict, or incident cited below was checked directly against this project's own primary sources
(its Git history, `ROADMAP.md`, `METHODOLOGY.md`, and its GitHub Wiki's Changelog and Model-Bakeoff
pages) rather than against a summary of them.

## 1. The Problem

A coding agent asked to fix a bug has a built-in oracle: run the test suite, and it either passes or
it doesn't. A research agent asked an open-ended question, to explain the causes of a historical
event or synthesize the state of a technical field, has no equivalent. There is no test to run
against a claim about the fall of the Roman Empire. The only available signal is whether the agent's
own process was sound: whether every citation resolves to something it actually fetched, whether its
claims are supported by what it read, whether it addressed the whole question rather than a fraction
of it. By the time any of these checks fails, the error is already synthesized into prose that reads
as confident and complete.

Most published deep-research architectures address this by assuming a frontier-class generator, or
by treating reliability as a property of training rather than something the runtime enforces. GPT
Researcher, Stanford STORM, `dzhng/deep-research`, Tongyi DeepResearch, CrewAI, AutoGen/AG2,
CAMEL-AI, LangGraph's Corrective RAG pattern, and the published deep-research systems from Perplexity
and OpenAI were all built around, or evaluated against, capable hosted models. None targets the
regime this project operates in: a roughly 20B-parameter model, served locally on a single consumer
GPU with a 16-17GB VRAM budget, performing open-ended multi-hop research with no fallback to a larger
model mid-run.

This is a particularly consequential regime, not a uniquely broken one. DeepDelve's own hosted-model
comparisons (Section 5.4) found that a much larger, considerably more expensive hosted model, DeepSeek
V4, can exhibit the identical citation-fabrication failure the local baseline exhibits, sometimes
worse. The claim this document defends is narrower and more defensible than "only small local models
have this problem": in the sub-30B local regime specifically, the problem is sharper and more
frequent, and there is no larger fallback model to reach for when it appears.

## 2. DeepDelve Architecture

DeepDelve runs entirely against local, OpenAI-compatible model servers, defaulting to Ollama, with
`gpt-oss:20b` as the current validated baseline on a single 16-17GB-VRAM-class GPU. No component of
the architecture assumes access to a larger or hosted model at any point during a run.

The system is a typed, three-tier delegation hierarchy. A Planner agent plans in bounded, named slots
rather than an open-ended task list, delegates work to specialist agents, and adaptively replans when
results reveal gaps or contradictions. Two Tier-2 specialists, WebSearcher and AcademicSearcher,
conduct search and retrieval. Two Tier-3 leaf specialists, DocumentAnalyzer and DataAnalyzer, read and
extract from already-downloaded material; DataAnalyzer alone holds the tool for extracting structured
data such as tables and code. Two additional roles, FindingsWriter and Builder, are never dispatched
by the Planner. They are dispatched directly by the completion-check system described in Section 8,
in a fresh context, once the Planner's delegation phase has produced sufficient material.
FindingsWriter assembles `findings.md` from the run's structured, already-verified results; Builder
writes the final report from `findings.md`. Each output receives an independent review pass from a
separately instantiated PeerReviewer agent, with one corrective re-dispatch permitted if the review
flags a problem.

Tool access is deliberately withheld from each parent role so that it cannot short-circuit the chain
it is meant to delegate through. The Planner has no file-writing tool: it cannot produce
`findings.md` or the final report itself under any circumstance. This is the first instance of a
pattern that recurs throughout the system: rather than instruct a role not to do something, remove
its ability to do it.

This architecture also addresses a context-management problem specific to a Planner whose
conversation grows monotonically across a run, since the underlying agent framework performs no
compaction. Before the fresh-context FindingsWriter/Builder pattern was introduced, every
completion-check retry meant re-showing the Planner its own prior rejected drafts inside its own
growing conversation, a pattern we term context poisoning: attention degrading on a long,
self-referential history well before any hard token limit is reached. Dispatching a fresh-context
writer role directly, instead of feeding the retry back through the Planner's conversation, avoids
this.

## 3. The Development Method

DeepDelve's architecture is not the output of an upfront design; it is the accumulated result of a
specific investigative loop, repeated dozens of times across the project's history:

```
Observed behavior (a run fails, or produces a suspicious artifact)
        v
Hypothesis: is this the model, the runtime, the parser, the orchestration, or this project's own code?
        v
Isolate one variable at a time and re-run
        v
Root cause identified, at the actual layer responsible
        v
Fix applied at that layer, or the model is discarded/blocked with the layer named explicitly
        v
Regression test added, or a standing methodology rule updated
```

Two disciplines make this loop trustworthy rather than a series of one-off patches. First, a fix is
not treated as done until it is replayed against the actual historical run state that first exposed
the bug, not just a fresh synthetic test case, confirming the fix would have caught the real incident
and not merely a tidy approximation of it. Second, a "this model doesn't work" verdict is never
accepted from a single bad run without first checking whether the failure belongs to the model at
all; Section 6's Model Evaluation Standard exists specifically because this project caught itself
skipping that check twice.

## 4. Model Evaluation as a Full-Pipeline Problem

Twenty-one model candidates were evaluated against DeepDelve's real multi-agent pipeline, not against
an isolated tool-call smoke test. `gpt-oss:20b` remains the only candidate with a full pass on both
of the project's live benchmarks. This scale of evaluation is itself one of the project's more
transferable outputs: it demonstrates, empirically and repeatedly, that a model's advertised
tool-calling capability is not equivalent to reliable tool use inside a real multi-agent workload.

A model can fail at any of several distinct layers, each of which looks identical from the outside
("the run didn't work") but requires a completely different fix:

```
Model capability
        v
Tool-call syntax capability
        v
Tool-parser compatibility (Ollama, llama.cpp, vLLM each parse differently)
        v
Chat-template compatibility
        v
Reasoning-mode compatibility (does a "disable thinking" flag actually disable it?)
        v
Agent-prompt compatibility (does the model behave the same under a real, longer system prompt?)
        v
Workflow reliability (does it converge across multi-turn retries, not just one isolated call?)
        v
Research quality
        v
Synthesis quality (can it convert research into a written artifact?)
        v
Verified, grounded final artifact
```

A model can clear several of these layers and still fail at one further down. `Falcon3-10B-Instruct`
has real, TII-confirmed tool-calling capability but is only ~33% reliable in practice, traced to a
known Ollama bug where a tool-name mismatch produces a silent empty response instead of an error, a
parser-layer failure, not a capability failure. `hermes3:8b` passes an isolated smoke test cleanly but
fabricates fictional system-error text once placed under DeepDelve's real, longer prompt, an
agent-prompt-layer failure invisible to the smoke test. Ornith-1.0-9B (Section 5.3) clears every layer
through research quality and fails only at the very last one, converting genuine research into a
written artifact.

A large share of the disqualified candidates failed specifically on narrating a tool call instead of
invoking it, rather than on any other failure mode: `granite3.1-dense:8b` and `phi4-mini:3.8b`
narrate tool calls as literal text despite claiming support; `Phi-4` 14B (community tool tag) made
zero real tool calls across nine attempts, fully deterministic narration; `mistral:7b-instruct`
narrates its delegation call as markdown instead of invoking the tool; `Bonsai 8B` and MiniCPM5 1B
skip the relevant tool entirely in writer/delegation roles; `llama3-groq-tool-use:8b` was rejected at
the schema stage entirely. A separate cluster failed on the `thin_coverage` non-convergence pattern, a
research-then-stall loop repeating a canned "scope is complete" response instead of acting on a
corrective nudge (`qwen3:4b`, `qwen3:8b`, `mistral-nemo:12b`), which later became the target of the
project's first fine-tuning round (Section 7). The full candidate-by-candidate verdict table is the
Model-Bakeoff wiki page cited in the References; it is not reproduced here in full, per this
document's own principle of synthesizing rather than duplicating that record.

## 5. Failure Isolation Across the Stack

Two investigations illustrate the isolate-the-layer discipline in concrete detail: one that was
mostly an infrastructure fix once isolated, and one that turned out to be a genuine, confirmed model
failure even after every infrastructure confound was removed.

### 5.1 The Qwen3 think-mode bug

Several early Qwen3-family bake-off entries carry a dagger marker in the project's own verdict
table, flagging that they were very likely scored with uncontrolled reasoning leaking into their
actual output, not the clean output the score implied. Neither the OpenAI-compatible disable flag nor Ollama's own native `think: false`
field reliably suppressed Qwen3's reasoning; native `think: false` was actively worse than doing
nothing, dumping raw chain-of-thought directly into `message.content` with no separate field, while
`think: true` correctly isolated it into its own channel. This was confirmed to be an Ollama serving
bug rather than a model limitation, because the same model served through a real vLLM instance gave
clean, unpolluted output. A second, lower-effort fix was found afterward: `api.backend: "ollama"`
(the native `/api/chat` endpoint, rather than Ollama's OpenAI-compatibility layer) gives the same
clean isolation without a full backend migration, live-verified on `gpt-oss:20b` and Ornith-1.0-9B.
Only one already-disqualified candidate, `qwen3-4b-combined-v2-lora`, has so far been re-benchmarked
through this clean path; every other Qwen3-family verdict from before this fix still stands on its
original, likely-polluted score and is flagged as such rather than silently treated as settled.

### 5.2 Llama 3.2 3B's array-argument corruption

`llama3.2:3b` returns structured array arguments as JSON-encoded strings rather than real JSON
arrays. The natural first hypothesis, an Ollama serving bug, was tested directly: the same corruption
was confirmed independently on Ollama, `llama.cpp`, and vLLM. Because the failure reproduced across
three independent serving stacks with different parsers and different templating, it was classified
as model-side, not a specific known Ollama defect (`ollama/ollama#6155` was checked and ruled out as
the cause), and the candidate was disqualified on that basis rather than left in limbo pending a
serving-layer fix that would never actually resolve it.

### 5.3 Ornith-1.0-9B: eliminating confounders before trusting a verdict

Ornith-1.0-9B carried a long-standing inconclusive status because every earlier attempt to benchmark
it had an independent, non-model confound: a chat-template bug, a native-backend corruption bug, and
two separate DeepDelve bugs of its own. Rather than fold those confounds into a discard verdict, each
was fixed first. A clean re-test on 2026-08-19 then did real research cleanly: 17 sources, 22
findings, no corruption, no repetition loop. At the point where its delegation quota was exhausted,
the Planner narrated its wrap-up as chat prose instead of calling the write tool, the same
"narrate instead of write" failure class seen across most sub-14B candidates in this bake-off. A
targeted follow-up A/B test on a different, related instance of this failure class ruled out one
plausible prompt-structure explanation, a "deliberate before acting" prompt block, finding no
difference in tool-call rate with the block present or stripped (`METHODOLOGY.md` §3.6); the pattern
is treated here as a likely model capability limit rather than a known prompt artifact, though not
every instance of it has been isolated this rigorously. Ornith
is, on the strength of this clean run, the strongest research-quality candidate at this size the
project has tested; its ceiling is not research capability, it is converting that research into a
written artifact under completion pressure. The lesson generalizes: a model should not be classified
until known infrastructure confounders have been eliminated, and research capability and synthesis
capability are separable properties that must be measured separately.

### 5.4 DeepSeek V4: a hosted, larger, more expensive model with the same failure

DeepSeek V4 Flash and Pro were evaluated as hosted candidates across four runs. Two genuine harness
bugs were found and fixed first: a gap in hosted thinking-mode control, and a guardrail starvation
bug in the API server's own budget cutoff. Neither of these, once fixed, changed the outcome.
DeepSeek re-fabricated the same citations on repeated re-runs, a reproducible model-level failure
rather than an infrastructure artifact. Pro scored worse than Flash (0.0/1.0 versus 0.2/1.0 against
the local `gpt-oss:20b` baseline's 0.7) despite costing roughly three times as much. It was not
disqualified for tool-calling mechanics or for misunderstanding its own write permissions; both were
clean or accurate. A separate cross-model hosted benchmark (DeepSeek V4 Pro, Nemotron Super 49B, and
`gpt-oss-20b` via NVIDIA NIM) found none of the hosted candidates beat the local baseline: DeepSeek
crashed on an uncaught 429, Nemotron made zero real delegation calls and fabricated citations, and
the local `gpt-oss-20b` was the only clean pass, though a thin one, and even that pass was not
spotless: a later audit (recorded only in the project's internal working notes, not the public wiki,
and so not independently checkable by a reader the way most other claims in this document are) found
its one real citation carried a wrong paper title that slipped past this project's own grounding
check at the time, one input among the incidents that motivated building the NLI-entailment-based
grounding check described elsewhere in this project's history. The scoped, defensible conclusion
is not "larger models are worse." It is that in this specific workload, citation-grounded multi-agent
research under a real delegation and verification harness, model size and hosting cost did not
predict reliability, and the exact failure mode the local regime motivated this project to defend
against also appears, unprompted, in a frontier-class hosted system.

## 6. A Full-Stack Model Evaluation Standard

Two real fairness gaps, found on a critical re-read of the project's own past verdicts rather than
invented in the abstract, motivated a standing evaluation standard before any future verdict is
treated as settled. First, a heterogeneous-role-tiering result (`gpt-oss:20b` paired with a small
specialist model) was a foreseeable VRAM-thrashing outcome that should have been caught at design
time, not just measured after the fact. Second, MiniCPM5-1B's own FINAL VERDICT run had swapped the
Planner/Builder off `gpt-oss:20b` onto `mistral-nemo` to free VRAM, meaning the run never actually
isolated MiniCPM5-1B as the one variable under test; part of what got blamed on MiniCPM5-1B was
attributable to the swapped-in Builder instead. Neither gap was hidden, both were documented in their
original entries, but neither was caught before being treated as a concluded verdict.

The resulting standard requires, before any candidate is called discarded or adopted:

1. Confirm the operating mode (think/nothink, tool-calling format, context length) actually reaches
   the model via a raw API-level test, before running any full benchmark through it, never inferred
   from vendor docs alone. This point exists because skipping it is exactly what let the Qwen3-family
   think-mode passthrough bug (Section 5.1) go undetected until several models had already been
   scored under it.
2. Isolate the candidate as the only variable; every other pipeline role stays on the known-good
   `gpt-oss:20b` baseline unless it is the role under test. A VRAM-forced swap elsewhere makes the run
   informational only, never a clean verdict.
3. State the serving backend and version alongside every verdict, since a "disqualified" model may
   actually be a serving-layer bug; two independent Ollama bugs (the Qwen3 think-mode passthrough,
   and a nested-array stringification bug affecting `mistral-nemo`, `llama3-groq-tool-use`, and
   `llama3.2:3b`) were found exactly this way.
4. A discard claim needs more than one run; a clean pass can stand on one.
5. Keep a verdict changelog rather than silently overwriting a superseded entry.
6. A candidate that cannot fit the project's real minimum operating context on this hardware
   (~16K tokens) is discarded outright on hardware grounds, not proportionally rescaled to fit,
   unless the ceiling is the model's own permanent architectural limit rather than a hardware-forced
   squeeze, in which case it is tested at its true native ceiling instead.

This standard is itself an engineering artifact worth naming directly: a project that evaluates
models seriously needs a standing methodology for the evaluation process, not just a growing list of
verdicts, because the verdicts themselves are only as trustworthy as the fairness of the process that
produced them.

## 7. Fine-Tuning Experiments

DeepDelve did not only evaluate models; it attempted to modify one directly. A hardware feasibility
smoke test (2026-07-14) confirmed real GRPO training runs end to end on the project's own RX 9060 XT
(RDNA4, ROCm) after pinning `HIP_VISIBLE_DEVICES=0` to stop the trainer's device mapping from
sharding across the discrete GPU and the integrated one.

The first real training round targeted `thin_coverage` specifically, the research-then-stall pattern
several bake-off candidates shared. A 348-line dataset combining five real extracted examples with
synthetic scenarios generated by calling the project's own production `check_thin_coverage` function
directly was used to LoRA-tune `Qwen/Qwen3-4B` (rank 16, ~73 minutes, stable at 15.79 of 17.1GB VRAM).
Held-out evaluation showed real generalization: the base model passed 6 of 8 held-out prompts, the
fine-tuned model 8 of 8, including sensible tool calls on three genuinely unseen topics. Deployed
against the live benchmark that had originally exposed the failure, the targeted fix held completely:
zero `thin_coverage` stalls recurred anywhere in the run. A different, untouched failure mode then
dominated instead: 0 of 8 final citations were ever actually fetched, and the four correctly grounded
findings that did exist in `findings.md` were dropped from the final report entirely in favor of the
fabricated ones. Root-caused the same day: a finding-summary truncation step was silently slicing off
the verification warning meant to prevent exactly this, since both affected findings measured exactly
the truncation limit with the warning cut away. Fixed by reserving the warning's own length outside
the truncation budget; a re-test improved real citation grounding from 0 of 8 to 3 of 9, with the
remainder still fabricated specifically where the model had no real alternative source and cited a
flagged URL anyway.

A later, seven-dimension combined GRPO round (`qwen3-4b-combined-v2-lora`) improved the combined
overall held-out score from 0.747 to 0.926, genuine generalization by the same held-out-prompt
standard used above; the `citation_grounding` dimension specifically, the one tied most directly to
hallucination risk, improved more modestly, from 0.615 to 0.781, and did not reach ceiling. Citation
fabrication and writer-role convergence, dimensions the combined reward never targeted, remained
broken at this model size even with reasoning output cleanly isolated, confirmed on a second, clean
retest. The candidate was disqualified live.

The standing lesson from both rounds together: fine-tuning one targeted behavior can fully resolve
that behavior while leaving agent reliability, a genuinely multi-dimensional property, no more
reliable overall, because the model's remaining failure modes were never part of the reward signal.
This motivated a standing project rule: every new fine-tuning objective folds into one combined,
multi-objective GRPO retrain off the same base checkpoint, never a separate, isolated single-purpose
LoRA, since separately trained adapters cannot be cleanly merged or stacked. As of this writing, no
correctness gap blocks resuming fine-tuning under that rule; the harder blocker is that no small base
model candidate has yet passed a real live benchmark worth fine-tuning in the first place.

## 8. Deterministic Reliability Architecture

The project's repeated, hard-won finding is that prompt-only fixes do not survive contact with this
failure class at this model size. A model instructed not to cite a source it was never given cites it
anyway. A model told to stop delegating once research is sufficient loops past that point regardless.
A model warned, inline, in its own evidence base, that a specific citation does not match anything
fetched this run, cites it again on the very next independent dispatch. Each failure was first
addressed as a prompt fix, and each prompt fix was falsified by a live run before a structural one was
built instead. The organizing principle: where a prompt instruction can be silently ignored, remove
the capability that allows it to be ignored, or verify the output with code that runs regardless of
whether the instruction was honored.

At the core of the resulting system is a set of 27 independent, pure-function checks organized into
two tiers, 10 completion checks evaluated first and 17 grounding checks evaluated only once the
completion-check tier returns no problem, first-match-wins, exactly one verdict per attempt. Each
check targets one specific, previously observed failure mode against the run's actual structured
state, not a generic classification scheme applied uniformly.

This design introduced its own failure mode: starvation. A first-match-wins priority queue lets any
single check near the top permanently starve every check below it for a run's entire retry budget, if
that top check keeps re-firing on state that has not meaningfully changed between attempts. This
shape produced six live incidents in one night before a general fix was built: every non-self-resolving check now caps its own
consecutive-firing count through a shared helper, pinned by a standing audit test that fails
automatically if a newly added check omits the cap, drawing on operating-system scheduler aging and
the circuit-breaker pattern (Nygard, 2018) rather than an invented mechanism. That generalized fix was
itself later shown insufficient in a new shape, confirming starvation was a genuine recurring problem
class: a completion-check-tier problem recurring indefinitely could permanently prevent a
grounding-check-tier detector from ever receiving a turn, because the two tiers, not just the checks
within one list, needed the same yield mechanism at their boundary. The methodological lesson is not
just the mechanism itself: reliability mechanisms require their own reliability engineering, and a fix
scoped to one boundary does not automatically extend to a structurally similar boundary elsewhere
without being checked explicitly.

Several other structural mechanisms compose with the checks bank. FindingsWriter and Builder each
dispatch to a separately instantiated PeerReviewer with zero shared conversation history rather than a
same-context self-critique step, a distinction with literature backing (Kamoi et al., 2024) that this
project's own incident history forced before the literature comparison confirmed it. When a writer
dispatch produces a genuinely empty response twice in a row, observed live with FindingsWriter
returning empty on 6 of 8 attempts within a single run, the system assembles `findings.md` directly
from already-verified structured data with no further LLM involvement, rather than burning the retry
budget on a failure mode already shown persistent within that run. Any finding whose own summary
carries a verification or relevance warning marker is structurally excluded from ever reaching
FindingsWriter's evidence pool, rather than trusted to be honored as a warning; a model handed its own
embedded warning cited the flagged URL anyway across seven independent dispatches within a single run
before this was built. A per-task verification ledger is computed structurally in the engine from
signals that already exist in the run's state, rather than trusting a Planner-authored verification
function, because small local models have repeatedly proven unreliable at following new
structured-output conventions. And once fresh-context review (above) proved insufficient for a
distinct failure, a report mechanically passing every grounding check while silently answering only a
third of a multi-facet query because the missing facets were never fabricated, just never produced,
Builder is instead dispatched once per under-represented facet, each dispatch a genuinely independent
production call against only that facet's own verified findings, extending the fresh-context principle
from review to production itself.

## 9. Verification Failure and Starvation

The starvation discovery in Section 8 deserves its own emphasis as a distinct engineering lesson, not
folded silently into the checks-bank description. A verification system, introduced specifically to
make agent behavior more reliable, introduced a new failure mode of its own: a check correctly
detecting a real problem could, by re-firing on unchanged state, prevent every other check from ever
running. This produced six live incidents in one night before the general fix, plus 2 more instances a
systematic audit caught before they ever manifested live, and the general fix itself proved
insufficient once more at a structurally different boundary (the tier boundary rather than the
within-tier boundary). The progression from first incident to a standing, test-enforced invariant, and
then to a second incident that showed the first invariant's boundary was drawn too narrowly, is itself
the demonstrated skill: guardrails need guardrails, and a mechanism is not proven general just because
one instance of it shipped successfully once.

## 10. Coverage, Grounding, and Synthesis Are Different Properties

Three of this project's clearest findings are that a report can independently succeed or fail along
at least three distinct axes, and that conflating them produces a misleading evaluation.

Grounding asks whether the claims a report makes are actually supported by what was fetched. Coverage
asks whether the report answered everything it was asked, not whether what it did answer is
supported. A report can be perfectly grounded and still incomplete: the `gpt-oss:20b` baseline, for
example, can produce honest, fully grounded output while abandoning the harder half of a multi-facet
query rather than fabricating an answer to it, confirmed even after every pipeline bug affecting this
behavior was fixed, a genuine model capability limit rather than a bug. Synthesis asks whether
genuine, well-grounded research is successfully converted into a written artifact at all. Ornith-1.0-9B
(Section 5.3) is the sharpest demonstration that this is a separable property: a model can research
excellently and still fail specifically at the last step, calling the write tool under completion
pressure rather than narrating a summary instead.

The architecture described in Section 8 can detect and constrain each of these failure modes once it
occurs. It does not eliminate the underlying capability gap that produces them; the model still
determines how often each failure actually occurs.

## 11. Experimental Findings: A Controlled Ablation

Every mechanism in Section 8 was originally validated the same way: implement it, re-run the
benchmark that exposed the bug, and confirm the target symptom did not recur in one live trial. This
is a real check, but it does not distinguish a genuinely load-bearing fix from an addition that
happened to coincide with an unrelated convergence in that run, the audit gap Jwalapuram et al. (2026)
identify across multi-agent systems generally as "expensive witnesses": complexity that carries real
overhead but contributes near-zero measured effect on the outcome.

A controlled ablation was applied to two accumulated completion-check mechanisms, `force_whole_rebuild`
and a `no_progress_guard` added the day before, using an adaptive-trial protocol of one run per
condition, escalating to additional runs only when an early disagreement required resolution. The
benchmark score is produced by the project's own eval harness (`eval/evaluate.py`): a weighted-criteria
rubric scored in [0, 1] per run, where each criterion (an exact-match check, a regex check, or an
LLM-judge check, independently weighted) contributes its weight to the score if met, against a fixed
evaluation query held constant across every ablation run.

| Mechanism disabled | Mean benchmark score | Runs | Failure pattern |
|---|---|---|---|
| `force_whole_rebuild` | 0.25 (baseline 0.75) | 3 | First run showed no measurable effect (0.75, identical to baseline); the next two both hit the same underlying coordination failure the mechanism exists to prevent |
| `no_progress_guard` | 0.125 (baseline 0.75) | 2 | Both runs timed out, for two distinct specific reasons |

Both mechanisms were confirmed genuinely load-bearing rather than expensive witnesses. This is the
first controlled-ablation evidence for any mechanism in this project's completion-check pipeline, in
contrast to the single-live-trial validation the remaining mechanisms in Section 8 still rest on. The
larger remaining mechanisms, per-facet dispatch and the starvation guards themselves, have not yet
been put through the same ablation protocol; this is reported as a concrete, scoped, not-yet-executed
next step, not as a claim that the unablated mechanisms are unproven in every other sense, each still
has live-incident evidence behind it.

The adaptive-trial protocol itself is a limitation worth naming directly. With n=2 and n=3 runs per
condition and an escalation rule triggered by "an early disagreement," these are not pre-registered
sample sizes, and no variance or confidence interval is reported alongside the means. The 0.75-to-0.25
and 0.75-to-0.125 gaps are large enough to be confident the direction is real, but the ablation should
be read as strong suggestive evidence at this run count, not a statistically powered result.

## 12. Engineering Lessons

Ten lessons, each grounded in a specific incident described above rather than stated as abstract
values.

**Prompting is not enforcement.** Use prompts to guide behavior; use code to enforce an invariant the
model must not be allowed to violate (Section 8).

**A failure is not necessarily a model problem.** The Qwen3 think-mode bug (Section 5.1) and the
Falcon3 tool-name-mismatch bug were both serving-layer defects initially indistinguishable from model
failure until isolated. Always check model, runtime, parser, chat template, and orchestration before
attributing a failure to the model.

**Tool calling must be tested in a real agent context, not an isolated smoke test.** `hermes3:8b`
passed an isolated smoke test and failed under the project's real, longer prompt (Section 4).

**In this workload, model size and hosting cost did not predict agent reliability.** DeepSeek V4 Pro
cost roughly three times as much as Flash and performed worse; both failed on the same
citation-fabrication mode the local baseline was built to defend against (Section 5.4). This is
evidence from one hosted-model family across four runs, not a general claim about hosted models.

**Fine-tuning one capability does not solve agent reliability globally.** The `thin_coverage` round
fully fixed its target and immediately exposed an unrelated, previously-masked citation-fabrication
failure (Section 7).

**Verification mechanisms can introduce their own failures.** The starvation discovery (Section 9)
shows guardrails need their own guardrails, and that a fix's boundary must be checked explicitly, not
assumed correct because a version of it already shipped once.

**Grounding, coverage, and synthesis are different properties, and a system must evaluate all three.**
A report can be honestly grounded and still incomplete (the multi-facet abandonment behavior), or
well-researched and never written at all (Ornith, Section 5.3/10).

**Context is an architectural resource, not just a token budget.** Context poisoning, attention
degrading on a long, self-referential retry history, was observed and fixed well before any hard
token limit was reached (Section 2).

**A discard verdict needs more scrutiny than a pass.** The Model Evaluation Standard (Section 6) exists
because this project caught itself accepting two flawed discard verdicts on trust, not as a
hypothetical safeguard.

**Honest, labeled failure is the correct target when a model's capability ceiling is reached, not a
plausible-sounding fabrication.** This principle, stated explicitly in the verification layer's own
design (Section 8), is the throughline connecting every mechanism in this document.

## 13. What This Work Demonstrates

The technologies used across this project, LLM and agent evaluation, prompt engineering, tool-use
evaluation, GRPO and LoRA fine-tuning, reward design, multi-agent orchestration, deterministic state
machines, Ollama and vLLM serving, chat-template and tool-parser debugging, reasoning-mode behavior,
VRAM-constrained model selection, controlled ablation, and automated regression testing, are not
listed here as a technology inventory for its own sake. Each was used to diagnose and fix a real
failure in a functioning system under real hardware constraints, not exercised in isolation or as a
demonstration project. The distinction matters: this document's claim is about experience gained
solving real problems, not familiarity with a list of tools.

## 14. Limitations

We state these plainly rather than softening them, since the honesty of a verification layer's own
self-report is part of what such a layer is for.

This document is an internal synthesis, sourced from the project's own commits, tests, wiki, and a
dedicated but time-bounded research pass. It has not been externally peer-reviewed, and the entire
project was built solo, on one codebase, with no external team or CI gate independently validating any
of the claims above. The verified evidence trail (commit history, `eval/ablation_results.jsonl`, the
wiki's Changelog and Model-Bakeoff pages) is what a reader should check directly rather than trusting
this document's own retelling of it.

The empty-response tendency described in Section 8 is mitigated in its consequences but not resolved
at the model level; the salvage path prevents a doubly empty dispatch from costing a retry cycle, it
does not explain why a fresh dispatch sometimes returns nothing at all, and the frequency of this
behavior is unquantified.

The per-task verification ledger still produces exactly one whole-run verdict per completion-check
attempt; a flagged task's directive names the specific problem, but redelegating it still routes back
through the Planner's own turn rather than an independent, bypassing dispatch. A deeper version of
this mechanism was scoped, evaluated against real historical run data, and closed as a no-go: an audit
of every run where the ledger flag fired found no clean, unconfounded case of the fix genuinely
failing to resolve a recurring task, every observed recurrence traced back to an already-fixed,
independent root cause.

The verification layer adds real overhead this document does not quantify. Fresh-context PeerReviewer
dispatch, per-facet Builder dispatch, and 27 structural checks evaluated on every attempt all cost
additional tokens and wall-clock time on hardware that is already VRAM-constrained. The reliability
gain reported in Section 11 is not accompanied by a matched cost figure for this overhead.

Most fundamentally, the architecture described here is reactive rather than proactive. Every
mechanism fires after a problem has already been generated, catching it before it propagates further,
not preventing the underlying model from generating the problem in the first place. This matches a
finding stated repeatedly throughout this document: architecture and verification amplify a capable
model rather than rescue an incapable one. Below some model capability floor, no amount of downstream
checking produces a usable result. What the architecture can still guarantee at and below that floor
is an honestly labeled failure rather than a silently accepted bad one.

The 21-candidate model bake-off, while unusually thorough for a solo project, is not a controlled
academic benchmark; verdicts rely on the Model Evaluation Standard in Section 6 rather than
statistical significance testing, and several early verdicts (flagged explicitly in Section 5.1)
still stand on possibly-confounded scores pending a re-test through the corrected reasoning-mode
path.

## 15. Future Work

Extending the controlled-ablation protocol of Section 11 to the remaining, larger mechanisms
(per-facet dispatch, the starvation guards themselves) is the most immediate concrete next step this
work leaves open. Re-benchmarking the Qwen3-family candidates flagged in Section 5.1 through the
corrected reasoning-isolation path would resolve which of their scores are trustworthy. On the
evaluation side, repeated benchmark runs with reported variance, a larger and more diverse task suite,
and separately reported research/grounding/coverage/synthesis metrics rather than one blended score
would make future verdicts considerably more defensible. On the fine-tuning side, the standing
combined-multi-objective rule (Section 7) remains the plan for the next round, once a small base
model candidate first clears a real live benchmark worth fine-tuning.

## 16. Conclusion

DeepDelve's development demonstrates that reliable AI agents require more than a capable language
model. Model selection, inference infrastructure, tool interfaces, context management, orchestration,
verification, and failure recovery all independently influence whether an agent succeeds, and a
failure at any one of these layers is indistinguishable from a model failure until it is actually
isolated. The project's most durable lesson is that an instruction should not be trusted to carry a
guarantee that deterministic software can enforce more reliably. When a local model cannot
consistently obey an important invariant, the surrounding system should constrain its capabilities,
verify its output structurally, or both. This does not eliminate the model's underlying limitations;
it makes those limitations observable, bounded, and considerably less likely to propagate silently
into a final, confidently-worded artifact. That is the engineering standard this project was built
to meet, and the one its own incident history was used to hold it to.

## References

This document leads with an engineering narrative rather than a literature review, but the
underlying project genuinely drew on every source below while designing the mechanisms this paper
summarizes; they are listed in full rather than only named in passing, per this document's own
honesty-first stance.

Anthropic. (2025). *How we built our multi-agent research system.* Anthropic Engineering Blog.

Asai, A., Wu, Z., Wang, Y., Sil, A., & Hajishirzi, H. (2023). Self-RAG: Learning to retrieve,
generate, and critique through self-reflection. arXiv:2310.11511.

Cemri, M., Pan, X., Yang, S., et al. (2025). Why do multi-agent LLM systems fail? (MAST). *NeurIPS
2025.* arXiv:2503.13657.

Choi, Y. M., Guo, X., Fung, Y. R., & Wang, Q. (2025). CiteGuard: Faithful citation attribution for
LLMs via retrieval-augmented validation. arXiv:2510.17853.

Elkins, K., & Chun, J. (2026). Framing instability in LLM ethical stance: Auditing negation
sensitivity in moral dilemmas. arXiv:2601.21433.

Huang, D., Malwe, G., & Wang, Z. (2025). When agents fail to act: A diagnostic framework for tool
invocation reliability in multi-agent LLM systems. arXiv:2601.16280.

Jwalapuram, P., Lin, H., Li, C., Jiao, F., Wang, S., Ming, Y., Ke, Z., Qin, C., Carenini, G., & Joty,
S. (2026). The illusion of multi-agent advantage. arXiv:2606.13003.

Kamoi, R., Zhang, Y., Zhang, N., Han, J., & Zhang, R. (2024). When can LLMs actually correct their own
mistakes? *TACL.* arXiv:2406.01297.

Karmakar, R., & Chatterjee, J. (2026). AgentFloor: How far up the tool use ladder can small open-weight
models go? arXiv:2605.00334.

Madaan, A., Tandon, N., Gupta, P., et al. (2023). Self-Refine: Iterative refinement with self-feedback.
arXiv:2303.17651.

Mann, L., Saxena, N., Tandon, S., Sun, C., Toteja, S., & Zhu, K. (2025). Don't think of the white bear:
Ironic negation in transformer models under cognitive load. arXiv:2511.12381.

Nygard, M. T. (2018). *Release It!: Design and Deploy Production-Ready Software* (2nd ed.). Pragmatic
Bookshelf.

Ong, I., Almahairi, A., Wu, V., et al. (2024). RouteLLM: Learning to route LLMs with preference data.
arXiv:2406.18665.

Shinn, N., Cassano, F., Berman, E., Gopinath, A., Narasimhan, K., & Yao, S. (2023). Reflexion:
Language agents with verbal reinforcement learning. arXiv:2303.11366.

Song, T. (2026). Cross-Context Review. arXiv:2603.12123.

Xu, T., Zhang, D., Mitra, K., & Hruschka, E. (2025). Verification-aware planning for multi-agent
systems (VeriMAP). *EACL 2026.* arXiv:2510.17109.

Xu, Z., Zhu, S., Wang, J., Wang, J., Athiwaratkun, B., Wang, C., Zou, J., & Zhang, C. (2026). When
does divide-and-conquer work for long-context LLM? *ICLR 2026.* arXiv:2506.16411.

Yan, S.-Q., Gu, J.-C., Zhu, Y., & Ling, Z.-H. (2024). Corrective Retrieval Augmented Generation.
arXiv:2401.15884.

**Primary sources**: This document synthesizes and re-verifies material drawn directly from the
project's own `METHODOLOGY.md`, `ROADMAP.md`, and its GitHub Wiki, specifically the
[Model-Bakeoff](https://github.com/g0elles/deepdelve/wiki/Model-Bakeoff),
[Fine-tuning](https://github.com/g0elles/deepdelve/wiki/Fine-tuning), Changelog, and Completed
pages, which remain the canonical, more detailed record; this paper is a synthesis of that record,
not a replacement for it. `METHODOLOGY.md` retains the deeper technical treatment and full
in-context application of each source above, for readers who want the deeper academic framing this
document deliberately does not lead with.
