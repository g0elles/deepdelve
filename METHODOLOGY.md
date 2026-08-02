# DeepDelve: A Methodology for Reliable Open-Ended Research Agents on Local Models

This document is a standalone synthesis of DeepDelve's design methodology — why it's built the way
it is, what problem it's actually solving, and what (if anything) is genuinely new about it. It
draws on and cites three other documents in this repository rather than duplicating them:
`README.md` (system overview, agent topology, structural-fixes changelog), `ARCHITECTURE.md`
(the completion-check/writer-dispatch engine's exact mechanics), and `RESEARCH.md` (the literature
review and prior-art survey this document's novelty claims are grounded in — §9/§9a/§10
specifically). Where this document makes a factual claim, the source of truth is one of those three,
or a specific, dated incident from `ROADMAP.md`'s History section — nothing here is asserted without
a traceable origin.

## 1. The problem

DeepDelve is a multi-agent deep-research assistant that runs entirely on local, consumer-hardware
LLMs (a single 16GB-VRAM-class GPU, `gpt-oss:20b` as the current baseline via Ollama) — not a
frontier hosted model. That constraint is the entire reason this project's architecture looks the
way it does.

Every framework surveyed for `RESEARCH.md` §9/§9a (GPT Researcher, Stanford STORM, dzhng/deep-research,
Tongyi-DeepResearch, CrewAI, AutoGen/AG2, CAMEL-AI, LangGraph's Corrective RAG, Perplexity's and
OpenAI's published deep-research systems) either assumes a frontier-class generator or addresses
reliability entirely through training (RL/agentic pre-training), not a runtime verification layer.
None of them are solving the problem DeepDelve actually has: a 20B-class local model doing open-ended,
multi-hop research and synthesis, with no natural pass/fail oracle the way code-with-tests has one.
A coding agent can run the test suite and know if it's wrong. A research agent that's asked "explain
the economic causes of the fall of the Roman Empire" has nothing equivalent to check against —
until it fabricates a citation, drops half the query, or contradicts one of its own sources, and by
then the damage is already synthesized into prose.

This project's repeated, hard-won finding — stated plainly because it's the premise for everything
below — is that **prompt-only fixes do not hold** against this failure class on a local model.
`ARCHITECTURE.md` and `ROADMAP.md`'s History section document this same lesson relearned in
different clothes at least half a dozen times: a model told "do not cite a source you weren't given"
does it anyway; a model told "stop delegating once research is sufficient" loops; a model warned
inline, in its own evidence base, that a citation doesn't match anything fetched this run, cites it
anyway on the very next independent dispatch. Every durable fix in this project moved the
enforcement from the prompt into deterministic Python code that runs regardless of what the model
does.

## 2. System architecture

Full diagram and per-role description: `README.md`'s Architecture section. Summary: a typed,
three-tier delegation hierarchy — **Planner** (plans, delegates, structurally cannot write any file
itself) → **WebSearcher/AcademicSearcher** (Tier 2, web research) → **DocumentAnalyzer/DataAnalyzer**
(Tier 3, leaf nodes, read/extract only) — plus two Planner-tier delegates that are never dispatched
by the Planner itself: **FindingsWriter** (writes `findings.md` from `RunState`'s structured
per-task results, not the Planner's own conversation) and **Builder** (writes `final_report.md` from
`findings.md`), each independently reviewed by a fresh-context **PeerReviewer** dispatch before being
accepted.

Tool access is deliberately withheld from each parent role so it is *structurally* forced to
delegate rather than short-circuit the chain — this is the first instance of a pattern that recurs
throughout the rest of this document: where a prompt instruction could be silently ignored, remove
the capability that would let it be ignored instead.

## 3. The verification layer — the actual methodological contribution

This is the part `RESEARCH.md` §9/§9a's dedicated prior-art survey concludes has no found precedent
combining all of its pieces, anywhere — industry or academic. It is not one mechanism but six,
each independently precedented (per §9/§9a's own sourced findings) but assembled and hardened
together in a way nothing else surveyed was:

### 3.1 A priority-ordered bank of structural checks, not an LLM judge

`src/engine/completion.py` runs 25 independent, pure-function checks (`COMPLETION_CHECKS` then
`GROUNDING_CHECKS`, first-match-wins, exactly one `Verdict` per attempt — full mechanics in
`ARCHITECTURE.md` §1). Each targets one specific, previously *observed* failure mode against
ground-truth run state — not a generic taxonomy classification, a concrete incident with a concrete
fix.

**Starvation prevention, generalized 2026-07-31 after this exact bug shape recurred eight times.**
The mechanism this subsection originally described (`_yield_to_starved_check` giving one
specific low-priority check a probe after a fixed attempt count) was a per-instance patch, not a
structural fix — and it kept needing to be re-applied: a first-match-wins priority queue lets any
check near the top permanently starve everything below it for a run's entire retry budget if it
keeps re-firing on unchanged state, and eight separate real incidents hit this before it got a
shared mechanism instead of a ninth patch. The generalized fix (`_consecutive_occurrences`,
`_capped`, `CONSECUTIVE_SAME_PROBLEM_ESCALATION_THRESHOLD`, `_yield_to_starved_check`,
`_STARVATION_YIELD_TARGETS`+`_apply_starvation_yield` — all `completion.py`, full mechanics
`ARCHITECTURE.md` §1) requires every non-self-resolving, non-self-clearing check to cap its own
consecutive-firing count via one shared helper, pinned by a standing audit test that fails CI if a
new check skips it. Grounded in prior art that predates this project (not invented for it, per
`ARCHITECTURE.md` §1's own citations): OS scheduler **aging** (a starvation counter forcing a
lower-priority task to get a turn past a threshold) and the **circuit breaker** pattern (Nygard,
*Release It!* — stop retrying a failing path after N attempts rather than hammering it forever).
The methodological lesson, not just the mechanism: **a structural fix that requires a test to
enforce compliance beats a structural fix that trusts every future check author to remember the
convention** — two of the eight incidents were caught by the audit test before ever causing a live
failure, not after.

### 3.2 Fresh-context, independent review — not same-context self-critique

FindingsWriter and Builder each dispatch to a **separately instantiated PeerReviewer with zero
shared conversation history** — not a self-critique step in the same context. `RESEARCH.md` §9
found this distinction is real and literature-backed, not a stylistic choice: Reflexion and
Self-Refine (the standard "self-correction" pattern most surveyed frameworks default to, including
CrewAI's default LLM-based guardrail, AG2's shared-transcript reflection, and CAMEL's in-loop
Critic) all use SAME-context self-critique, which current research states plainly is
"fundamentally unreliable... motivating the use of an independent critic rather than
self-evaluation." DeepDelve's design landed on the literature-correct side of that distinction
before the literature review confirmed it was correct — the review came after, as verification, not
as the design's origin.

### 3.3 Deterministic, non-LLM salvage for known LLM failure shapes

When a writer dispatch produces a genuinely empty response twice in a row (confirmed live,
2026-07-26: FindingsWriter did this on 6 of 8 attempts in one run), the system now assembles
`findings.md` directly from already-verified structured data — zero LLM involvement — instead of
losing the retry cycle. This mirrors CRAG's and Self-RAG's core mechanism (`RESEARCH.md` §10: both
filter/construct evidence structurally before a generator ever sees it, rather than annotate a
problem and hope a subsequent generation step honors the annotation), independently arrived at from
a live incident, then confirmed against the primary papers afterward.

### 3.4 Structural exclusion over embedded warnings

The clearest single example of the "don't trust the model to honor an instruction, remove the
possibility instead" principle. `_is_citable_finding` (`src/engine/completion.py`) now structurally
excludes any finding whose own summary carries a `[SYSTEM VERIFICATION WARNING` or
`[SYSTEM RELEVANCE WARNING` marker from ever reaching FindingsWriter's evidence — reversing an
earlier, explicit 2026-07-22 decision that deliberately left flagged findings in place, reasoning
they "may still coexist with other real, usable content." That reasoning was falsified live, twice
(`calendarr.com` 2026-07-24, `insidetx.com` 2026-07-26): a model handed its own embedded warning
cited the flagged bad URL anyway, across 7 independent dispatches in one run. Researched before
fixing, not guessed: naming forbidden content inside a "do not cite X" instruction is documented to
risk *priming* its reproduction rather than preventing it (the "ironic rebound" effect,
arXiv:2511.12381), and negation-following is separately documented as unreliable specifically in
smaller models (arXiv:2601.21433) — the failure has a real, cited mechanism, not just an anecdote.

### 3.5 A per-task verification ledger, engine-computed rather than model-authored

`RESEARCH.md` §9's closest academic analog, VERIMAP (arXiv:2510.17109), has a planner author an
explicit verification function per delegated subtask. DeepDelve's own established principle
(`RunState.coverage()`'s own documented reasoning: "small local models have repeatedly proven
unreliable at following new structured-output conventions") argues directly against asking the
Planner to do that. The reconciliation, shipped 2026-07-26: the **engine** computes a per-task
ledger (`task_verification`, `_update_task_verification`) structurally, from signals that already
exist (`_is_citable_finding`), rather than trusting a new Planner-authored field. This keeps
VERIMAP's real contribution — a task has its own checkable, independently-retriable verification
state — while dropping the part of its mechanism that this project's own hard-won lesson argues
against.

### 3.6 Fresh-context PRODUCTION per facet, not just fresh-context review

§3.2 established fresh-context review as necessary; a full marathon investigation (2026-08-01,
`RESEARCH.md` §16-17, `ROADMAP.md` History) found it insufficient on its own for a distinct failure
shape: a report mechanically passing every grounding check while still silently answering only
~1/3 of a multi-facet query, because the missing facets were never fabricated, just never
produced. Ruled out first, not assumed: stale sub-agent context (`_run_single_task` constructs a
genuinely fresh dispatch client per call, confirmed by reading it, not inferred). The literature
review done *before* attempting a fix (per this project's own standing rule) named the actual
mechanism — the **self-correction blind spot** (Kamoi et al., arXiv:2406.01297: models are
measurably worse at correcting errors in their OWN prior output than the identical error framed as
external input, ~64.5% of self-generated errors survive self-checking across 14 open models) — and
a first, smaller fix informed by that same literature (an explicit `edit_workspace_file` directive
naming exactly what to add, Song's Cross-Context Review framing, arXiv:2603.12123) was tried and
**live-tested to a clean negative result** before escalating: the directive changed which tool
Builder called, not what it produced — the citation ratio stayed frozen, and by the run's end the
report had gone from covering ~1/3 of the query to covering 0% of one whole facet, despite an
explicit "do not touch any other part of the report" instruction. That negative result is itself
methodologically load-bearing: it confirmed the failure was the self-correction blind spot
specifically (a model re-examining and "fixing" its own prior draft), not a tool-choice or wording
gap, before the larger architectural fix was justified. The fix that then worked: dispatch Builder
once **per** under-represented facet, each a genuinely independent, externally-scoped production
call in the Cross-Context Review sense (not just review), against only that facet's own real
findings — extending §3.2's "review must be fresh-context" principle to "production must be too,
once a single generation call is asked to hold more independent facets than it reliably can at
once." A second literature match, found during scoping rather than after: Xu et al.'s **aggregator
noise** framing (arXiv:2506.16411) — individual facts correct, a merge/synthesis step drops whole
clusters — named which of three distinct long-context failure modes this was, and confirmed
hierarchical decomposition (what per-facet dispatch is a form of) as the literature's standard
mitigation for that specific mode, not a guess at one of three.

## 4. Recurring design principles, evidenced

These are not abstract values — each is stated here because a specific, dated incident in this
project's own history demonstrated it, cited inline.

- **Structural enforcement beats prompted instruction, every time it's been tested.** The
  rename-nudge, the citation-exclusion fix, the tool-access withholding in §2, the empty-response
  salvage — every one of these exists because a prompt-only version of the same fix was tried or
  considered and either failed live or was predicted to fail based on cited literature (§3.4).
- **A mechanism firing correctly is not the same claim as the output being correct.** Confirmed the
  hard way, 2026-07-26: a report that mechanically passed every fired completion check was still
  100% about one half of a two-facet query, because the check that would have caught the OTHER half
  vanishing didn't exist yet. Two separate verification steps — did the pipeline run correctly, and
  is the artifact actually good — are both required; neither substitutes for the other.
- **Reuse before rebuilding — check what already exists before writing new machinery.** The
  Analyzer-tier NLI-coverage fix (§3, `real_grounding_problem`) turned out to need zero new code
  once the actual dispatch path was re-read; an earlier planning note had assumed new
  claim-extraction logic was required, and was simply wrong. Caught by re-reading the code, not by
  trusting the earlier note.
- **Verify against real historical data, not just a fresh unit test.** Several fixes this session
  were replayed directly against the actual `_run_state.json` of the run that originally exposed
  the bug, before being declared fixed — confirming the fix would have caught the exact real
  incident, not just a synthetic approximation of it.
- **A discard verdict needs corroboration; a pass can stand on one clean run.** Formalized as the
  Model Evaluation Standard (§5) after two real fairness gaps were found on re-reading a past
  verdict critically — the standard exists because the project caught itself getting this wrong
  once, not as a hypothetical safeguard.

## 5. Model evaluation methodology

Every model bake-off verdict (adopt or discard) in `ROADMAP.md` must clear a 6-point standard
before being treated as settled — written after re-reading a past "verdict" critically surfaced two
real fairness gaps that had been sitting undetected in already-published conclusions:

1. Confirm the operating mode (think/nothink, tool format, context length) reaches the model via a
   raw API-level test *before* running a full benchmark — never infer from vendor docs alone.
2. Isolate the candidate as the only variable; every other pipeline role stays on the known-good
   baseline unless it IS the role under test. A VRAM-forced swap elsewhere makes the run
   informational only, not a clean verdict.
3. State the serving backend and version alongside every verdict — a "disqualified" model may
   actually be a serving-layer bug (two independent Ollama bugs were found exactly this way).
4. A discard claim needs more than one run; a clean pass can stand on one.
5. Keep a verdict changelog — mark superseded entries and link the corrected retest, never
   overwrite history silently.
6. A candidate that can't fit the project's real minimum operating context on this hardware is
   discarded outright, not proportionally rescaled to fit — unless the ceiling is the model's own
   permanent architectural limit rather than a hardware-forced squeeze, in which case it's tested at
   its true native ceiling instead.

Full text and the incidents that motivated each point: `ROADMAP.md`, "Model Evaluation Standard"
section.

## 6. Novelty assessment

Summarized from `RESEARCH.md` §9/§9a's dedicated research pass (primary sources read directly —
repo docs, papers, Anthropic's own published multi-agent-system engineering writeup — not
search-snippet skimming), including a follow-up pass that closed every gap the first pass left
explicitly unconfirmed.

**Not novel at the level of individual components.** Deterministic verification layers over agent
output exist (closest: a Claude Code coding-agent tool using deterministic test-pass/fail
verification, arXiv-adjacent industry tooling, not academic). NLI-based citation verification is
published and active research (VeriCite, arXiv:2510.17853). Independent-context critics outperforming
same-context self-critique is an established literature finding (§3.2). Small frozen classifiers
replacing LLM judgment for a decision is precedented (RouteLLM-style routing). A separated
citation-verification pipeline stage exists in a major published system (Anthropic's own
CitationAgent).

**The specific combination has no found precedent.** Every other system examined implements one of
two shapes: same-context/same-loop LLM-as-judge critique (sometimes with one deterministic *routing*
branch bolted on, as in LangGraph's Corrective RAG — but never a deterministic *check* itself), or no
documented verification mechanism at all for this exact problem (Perplexity has genuine retrieval
engineering depth but nothing published on citation verification specifically; OpenAI's Deep
Research system card is scoped to safety/red-teaming, not architecture). None combines: dozens of
independently-named, priority-ordered, empirically-motivated checks; a starvation guard for that
priority queue; a regression test pinning the verification LOGIC itself (`test_structural_checks.py`'s
verdict matrix — added specifically because two checks' branches once silently merged and only a
matrix test with real distinctive assertions caught it); and all of it consistently built for the
local, sub-30B-model regime specifically, where every other system surveyed assumes a frontier-class
or well-resourced generator.

**Honest calibration**: the correct framing is "a disciplined, unusually deep combination of known
techniques applied to an underserved regime" — not "invented verification for LLM agents from
scratch." That is still a real, citable contribution; nothing found in either research pass makes it
redundant. Explicit residual gap: one AG2 notebook (the one most likely to show genuine
tool-grounded deterministic feedback in that framework) returned a 404 on every fetch attempt during
research and was never independently verified — flagged rather than guessed at, worth a targeted
follow-up if an AG2 comparison specifically becomes load-bearing for any future external-facing
claim.

## 7. Known limitations, stated plainly

- **The empty-response tendency itself is unresolved at the model level.** The salvage path (§3.3)
  mitigates the consequence; it does not explain or fix why a fresh dispatch sometimes returns
  nothing at all. Frequency is still unknown — not re-observed as often on some runs as others.
- **The per-task verification ledger (§3.5) still only produces one whole-run `Verdict` per
  completion-check attempt** — a flagged task's directive names it specifically, but redelegating it
  still routes back through the Planner's own turn rather than an independent, bypassing dispatch.
  **Update, 2026-07-29: the deeper version (VERIMAP Phase 2) was scoped, evaluated against real
  data, and formally closed as a no-go**, not left open pending more data — an audit of every real
  run where the ledger flag actually fired found zero clean, unconfounded cases of the same task
  recurring 3+ times under a normal retry budget with the directive-only fix genuinely failing to
  resolve it; every observed recurrence traced to an already-fixed, independent root cause instead
  (quota exhaustion, a stale-task-rename loop). The directive-only version is the settled answer
  unless a future clean run reopens it under that same specific trigger (`ROADMAP.md`'s own
  Completed entry for the full evidence trail) — not an open question this document should keep
  describing as unresolved.
- **The verification layer is reactive, not proactive, at its foundation.** Every check here fires
  after a problem has already been generated, catching it before it reaches the next stage — not
  preventing the underlying model from generating it in the first place. This is a deliberate
  scope choice (matches this project's own repeatedly-confirmed finding that verification/
  architecture amplifies a capable model rather than rescuing an incapable one — see `RESEARCH.md`
  §1's capacity-floor literature), not an oversight, but it means the approach has a ceiling: below
  some model capability floor, no amount of downstream checking produces a usable result, only an
  honestly-labeled failure instead of a silently-accepted bad one.
- **This document itself has not been externally reviewed.** It is an internal synthesis, sourced
  from this project's own commits, tests, and a dedicated (but necessarily time-bounded) research
  pass — not a peer-reviewed claim.
