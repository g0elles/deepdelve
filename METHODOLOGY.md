# DeepDelve: A Methodology for Reliable Open-Ended Research Agents on Local Models

This document is a standalone synthesis of DeepDelve's design methodology: why it's built the way
it is, what problem it's actually solving, and what, if anything, is genuinely new about it. It
draws on and cites three other documents rather than duplicating them: `README.md` (system overview,
agent topology), `ARCHITECTURE.md` (the completion-check/writer-dispatch engine's exact mechanics),
and `RESEARCH.md` (the literature review and prior-art survey this document's novelty claims are
grounded in, §9/§9a/§10 specifically). Every factual claim here traces to one of those three, or a
specific, dated incident in `ROADMAP.md`'s History section.

## 1. The problem

DeepDelve is a multi-agent deep-research assistant that runs entirely on local, consumer-hardware
LLMs, a single 16GB-VRAM-class GPU, `gpt-oss:20b` as the current baseline via Ollama, not a frontier
hosted model. That constraint is the entire reason this project's architecture looks the way it
does.

Every framework surveyed for `RESEARCH.md` §9/§9a (GPT Researcher, Stanford STORM,
`dzhng/deep-research`, Tongyi DeepResearch, CrewAI, AutoGen/AG2, CAMEL-AI, LangGraph's Corrective
RAG, Perplexity's and OpenAI's published deep-research systems) either assumes a frontier-class
generator or addresses reliability entirely through training, not a runtime verification layer.
None of them solve the problem DeepDelve actually has: a 20B-class local model doing open-ended,
multi-hop research and synthesis, with no natural pass/fail oracle the way code-with-tests has one.
A coding agent can run the test suite and know if it's wrong. A research agent asked to explain the
economic causes of the fall of the Roman Empire has nothing equivalent to check against, until it
fabricates a citation, drops half the query, or contradicts one of its own sources, and by then the
damage is already synthesized into prose.

This project's repeated, hard-won finding, stated plainly because it's the premise for everything
below, is that prompt-only fixes do not hold against this failure class on a local model.
`ARCHITECTURE.md` and `ROADMAP.md`'s History section document this same lesson relearned in
different clothes at least half a dozen times: a model told not to cite a source it wasn't given
does it anyway; a model told to stop delegating once research is sufficient loops; a model warned
inline, in its own evidence base, that a citation doesn't match anything fetched this run, cites it
anyway on the very next independent dispatch. Every durable fix in this project moved the
enforcement from the prompt into deterministic Python code that runs regardless of what the model
does.

## 2. System architecture

Full diagram and per-role description in `README.md`'s Architecture section. In summary: a typed,
three-tier delegation hierarchy. The Planner plans and delegates, structurally cannot write any
file itself. WebSearcher and AcademicSearcher (Tier 2) do web research. DocumentAnalyzer and
DataAnalyzer (Tier 3, leaf nodes) read and extract only. Two Planner-tier delegates are never
dispatched by the Planner itself: FindingsWriter writes `findings.md` from `RunState`'s structured
per-task results, not the Planner's own conversation, and Builder writes `final_report.md` from
`findings.md`. Each gets an independent review from a fresh-context PeerReviewer before being
accepted.

Tool access is deliberately withheld from each parent role so it is structurally forced to delegate
rather than short-circuit the chain, the first instance of a pattern that recurs throughout this
document: where a prompt instruction could be silently ignored, remove the capability that would let
it be ignored instead.

## 3. The verification layer: the actual methodological contribution

`RESEARCH.md` §9/§9a's dedicated prior-art survey concludes this has no found precedent combining
all of its pieces, anywhere, industry or academic. It's not one mechanism but seven, each
independently precedented per that survey's own sourced findings, but assembled and hardened
together in a way nothing else surveyed was.

### 3.1 A priority-ordered bank of structural checks, not an LLM judge

`src/engine/completion.py` runs 27 independent, pure-function checks, `COMPLETION_CHECKS` (10) then
`GROUNDING_CHECKS` (17), first-match-wins, exactly one `Verdict` per attempt, full mechanics in
`ARCHITECTURE.md` §1. Each targets one specific, previously observed failure mode against
ground-truth run state, a concrete incident with a concrete fix, not a generic taxonomy
classification.

**Starvation prevention** was generalized on 2026-07-31 after this exact bug shape recurred eight
times, then extended cross-tier on 2026-08-17 when it recurred a ninth time in a new shape. A
first-match-wins priority queue lets any check near the top permanently starve everything below it
for a run's entire retry budget if it keeps re-firing on unchanged state. Eight separate real
incidents hit this before the mechanism became structural: every non-self-resolving check now caps
its own consecutive-firing count via one shared helper, pinned by a standing audit test that fails
CI if a new check skips it. Grounded in prior art that predates this project: OS scheduler aging
(a starvation counter forcing a lower-priority task to get a turn past a threshold) and the circuit
breaker pattern (Nygard, *Release It!*, stop retrying a failing path after N attempts rather than
hammering it forever). The methodological lesson, not just the mechanism: a structural fix that
requires a test to enforce compliance beats one that trusts every future check author to remember
the convention. Two of the eight incidents were caught by the audit test before ever causing a live
failure, not after.

That generalized fix was itself insufficient once, confirming it was solving a real, recurring class
of problem rather than one specific bug. `run_completion_check` only evaluates `GROUNDING_CHECKS`
once `COMPLETION_CHECKS`'s own scan returns nothing, a hard two-tier gate, not just list-position
priority within one list. A live-traced incident (2026-08-17) showed a `COMPLETION_CHECKS` problem
recurring indefinitely could permanently prevent a dropped-facet detector in `GROUNDING_CHECKS` from
ever getting a turn, even though every individual check inside each tier was correctly capped. The
existing yield mechanism, already proven safe as a speculative probe, needed no new machinery, just
a second call site at the tier boundary itself. The lesson generalizes past this specific gate: a
structural fix scoped to "checks within one list" does not automatically cover "tiers of lists," and
the actual boundary of a starvation-prevention mechanism needs checking explicitly, not assumed from
the fact that a version of it already shipped once.

### 3.2 Fresh-context, independent review, not same-context self-critique

FindingsWriter and Builder each dispatch to a separately instantiated PeerReviewer with zero shared
conversation history, not a self-critique step in the same context. `RESEARCH.md` §9 found this
distinction is real and literature-backed, not a stylistic choice: Reflexion and Self-Refine, the
standard self-correction pattern most surveyed frameworks default to, all use same-context
self-critique, which current research states plainly is fundamentally unreliable, motivating an
independent critic over self-evaluation instead. DeepDelve's design landed on the literature-correct
side of that distinction before the literature review confirmed it, the review came after, as
verification, not as the design's origin.

### 3.3 Deterministic, non-LLM salvage for known LLM failure shapes

When a writer dispatch produces a genuinely empty response twice in a row, confirmed live on
2026-07-26 (FindingsWriter did this on 6 of 8 attempts in one run), the system assembles
`findings.md` directly from already-verified structured data, zero LLM involvement, instead of
losing the retry cycle. This mirrors CRAG's and Self-RAG's core mechanism (`RESEARCH.md` §10: both
filter or construct evidence structurally before a generator ever sees it, rather than annotate a
problem and hope a later generation step honors the annotation), independently arrived at from a
live incident, then confirmed against the primary papers afterward.

### 3.4 Structural exclusion over embedded warnings

The clearest single example of "don't trust the model to honor an instruction, remove the
possibility instead." `_is_citable_finding` now structurally excludes any finding whose own summary
carries a system verification or relevance warning marker from ever reaching FindingsWriter's
evidence, reversing an earlier, explicit 2026-07-22 decision that deliberately left flagged findings
in place, reasoning they "may still coexist with other real, usable content." That reasoning was
falsified live, twice: a model handed its own embedded warning cited the flagged bad URL anyway,
across 7 independent dispatches in one run. Researched before fixing, not guessed: naming forbidden
content inside a "do not cite X" instruction is documented to risk priming its reproduction rather
than preventing it (the "ironic rebound" effect, arXiv:2511.12381), and negated framing is separately
documented as producing large, unstable swings in small open-weight models' judgments, in an ethical-
stance framing study, not this project's own citation domain, but evidence in the same direction
(arXiv:2601.21433, small 1-4B models swung up to 76 percentage points between affirmative and
negated framings of the same question). The failure has a real, cited mechanism, not just an
anecdote.

### 3.5 A per-task verification ledger, engine-computed rather than model-authored

`RESEARCH.md` §9's closest academic analog, VERIMAP (arXiv:2510.17109), has a planner author an
explicit verification function per delegated subtask. DeepDelve's own established principle
(`RunState.coverage()`'s own documented reasoning: small local models have repeatedly proven
unreliable at following new structured-output conventions) argues directly against asking the
Planner to do that. The reconciliation, shipped 2026-07-26: the engine computes a per-task ledger
structurally, from signals that already exist, rather than trusting a new Planner-authored field.
This keeps VERIMAP's real contribution, a task has its own checkable, independently retriable
verification state, while dropping the part of its mechanism that this project's own hard-won lesson
argues against.

### 3.6 Fresh-context production per facet, not just fresh-context review

Section 3.2 established fresh-context review as necessary. A full marathon investigation (2026-08-01,
`RESEARCH.md` §16-17) found it insufficient on its own for a distinct failure shape: a report
mechanically passing every grounding check while still silently answering only about a third of a
multi-facet query, because the missing facets were never fabricated, just never produced. Ruled out
first, not assumed: stale sub-agent context (`_run_single_task` constructs a genuinely fresh dispatch
client per call, confirmed by reading it, not inferred). The literature review done before attempting
a fix, per this project's own standing rule, named the actual mechanism: the self-correction blind
spot (Kamoi et al., arXiv:2406.01297, models are measurably worse at correcting errors in their own
prior output than the identical error framed as external input; this document previously attached a
"64.5% across 14 open models" figure to this citation that does not appear anywhere in the paper's
full 23/24-page text, corrected 2026-08-22 after a direct full-text re-read, `grep`-checked for
"64.5", "fourteen", and "survive" with zero matches. The paper's own local copy is
`papers/kamoi_survey_2406.01297.pdf`; this was the same fabricated-attribution class of error CLAUDE.md's
citation-verification rule already names as a past incident, evidently never propagated back into this
specific paragraph). A first, smaller fix informed by that same literature
(an explicit `edit_workspace_file` directive naming exactly what to add, Song's Cross-Context Review
framing, arXiv:2603.12123) was tried and live-tested to a clean negative result before escalating:
the directive changed which tool Builder called, not what it produced, the citation ratio stayed
frozen, and by the run's end the report had gone from covering about a third of the query to covering
zero of one whole facet, despite an explicit "do not touch any other part of the report" instruction.
That negative result was itself methodologically load-bearing: it confirmed the failure was the
self-correction blind spot specifically, not a tool-choice or wording gap, before the larger
architectural fix was justified.

The fix that then worked: dispatch Builder once per under-represented facet, each a genuinely
independent, externally-scoped production call in the Cross-Context Review sense, not just review,
against only that facet's own real findings, extending §3.2's "review must be fresh-context"
principle to "production must be too" once a single generation call is asked to hold more independent
facets than it reliably can at once. A second literature match, found during scoping rather than
after: Xu et al.'s aggregator noise framing (arXiv:2506.16411), individual facts correct, a merge or
synthesis step drops whole clusters, named which of three distinct long-context failure modes this
was, confirming which of three plausible mechanisms was actually responsible rather than leaving it a
guess. Correction, 2026-08-22, after re-reading the paper directly rather than trusting the earlier
summary: that paper's own mitigation for aggregator noise is single-stage aggregation with carefully
designed prompts, not hierarchical decomposition; it does not recommend or test a hierarchical
approach. Per-facet dispatch is this project's own fix, independently justified by the self-correction
blind spot and Cross-Context Review literature above, not something Xu et al. themselves prescribe.

A follow-up, isolated A/B test (2026-08-19) sharpened rather than reopened the self-correction blind
spot diagnosis. A plausible, cheap-to-test candidate contributor to a different but related-looking
failure (a writer role narrating instead of calling `write_workspace_file`) was the `<Show Your
Thinking>` prompt block, since AgentFloor (arXiv:2605.00334) suggested a heavier version of the same
"deliberate before acting" pattern might push models toward "plan, then stop." A faithful isolated
harness, reusing the real evidence base and tool schema from a model already live-disqualified for
this exact failure, ran 9 reps with the block present versus stripped: 9 of 9 real tool calls in both
conditions, no difference. A genuine negative result, not left open: the block isn't implicated, and
since an isolated single-turn dispatch converges cleanly regardless of it, the actual variable has to
be something specific to a full run's multi-turn or retry dynamics, not the prompt content itself.
Methodologically this is the same discipline as the salvage and exclusion fixes above, applied to a
negative case: a plausible literature-suggested mechanism gets tested against the project's own real
data before being acted on or ruled out, rather than assumed correct because the analogy was
persuasive.

### 3.7 Controlled ablation over plausible-sounding mechanisms

Every mechanism in §§3.1-3.6 was originally validated the same way: implement it, re-run the
benchmark that exposed the bug, confirm the target symptom didn't recur in one live trial. That's a
real check, but it doesn't distinguish a genuinely load-bearing fix from a plausible-sounding
addition that happened to coincide with the run converging for an unrelated reason, precisely the
audit gap "The Illusion of Multi-Agent Advantage" (Jwalapuram et al., arXiv:2606.13003) identifies in
other multi-agent systems: complexity added without verified causal contribution, "expensive
witnesses" that cost real overhead but have near-zero measured influence on the outcome.

Applied to this project's own completion-check pipeline for the first time on 2026-08-18, using an
adaptive-trial protocol (one run per condition, escalating only when an early disagreement needs
resolving, per §5's own "a discard needs more than one run, a pass can stand on one" standard): two
accumulated mechanisms, `force_whole_rebuild` and a `no_progress_guard` added the day before, were
each run with and without on the standing benchmark. Disabling `force_whole_rebuild` dropped the mean
score from a 0.75 baseline to 0.25 across 3 runs, escalated after the first two disagreed, both
failing runs hitting the same underlying coordination failure the mechanism exists to break.
Disabling `no_progress_guard` dropped the mean to 0.125 across 2 runs, both timing out for two
different specific reasons, the guard's absence generically letting a run burn its whole time budget
on unproductive retries regardless of which specific check triggers it. Both mechanisms confirmed
genuinely load-bearing, not expensive witnesses, the first real controlled-ablation evidence for any
of this project's completion-check mechanisms, as opposed to the single-live-trial validation every
other mechanism in this document still stands on. The remaining, larger mechanisms (per-facet
dispatch, the starvation guards themselves) haven't yet been put through the same ablation, a
concrete, scoped-but-not-yet-executed next step, not a claim that everything else is unproven.

### 3.8 Academic citation existence verification, scoped proactively rather than incident-driven

Unlike §§3.1-3.7, this mechanism was not built in response to a live production failure this
project's own runs produced, it's the one deliberate exception to this document's own framing. It
was scoped 2026-08-22 during the project's own citation-verification pass on this very document (see
`WHITEPAPER.md`'s citation fixes the same day), where a real gap became visible by hand: the existing
grounding checks (§3.1) verify that an academic `(Author, Year)` citation resolves to a URL that was
actually fetched, but never that the citation's underlying identity, its claimed author and year,
corresponds to a real paper at all. A citation can pass every existing check (real URL, real fetch,
term-overlap, NLI entailment) while still attributing a genuinely fetched source to the wrong paper,
the "mashup fabrication" pattern documented in academic-integrity literature and found by hand
several times in this project's own reference lists this session, not yet caught by any structural
check.

The fix follows this document's own established principle rather than introducing a new one:
`utils/grounding.py::academic_citation_existence_problem` queries Semantic Scholar's public
paper-search API (no key required for this tier) for each academic citation that already has a
resolved URL, and wires into `real_grounding_problem`, the single function shared by both
per-dispatch verification (before a finding ever reaches `RunState.add_finding`) and final-report
checking, behind a new opt-in `grounding_check.academic_citation_verify` flag (default `false`, this
is the first grounding check with a genuine external-network dependency beyond URL-liveness). On a
flagged citation, exclusion is structural, not a warning left for the model to heed (§3.4's own
principle): the existing `[SYSTEM VERIFICATION WARNING]` + URL-scoped exclusion mechanism in
`_is_citable_finding` needed exactly one new recognized label,
`academic_citation_unverified`, no new exclusion code. Fails open on any network/timeout/HTTP
error, live-confirmed against the real API rather than only a mock: manual testing hit a genuine 429
rate-limit response from Semantic Scholar's unauthenticated tier, and the fail-open path correctly
returned no verdict rather than a false flag.

A live smoke test against the real API (2026-08-22, same day) found and fixed a real bug the mocked
unit tests could not have caught, because they only ever fed in already-correctly-shaped fake
responses. The initial matching logic compared a bare surname ("Vaswani") against a candidate's
*entire* name string ("Ashish Vaswani") via `difflib.SequenceMatcher`, scoring only ~0.67, below the
0.7 threshold, purely from the length mismatch, even though the surname is an exact match. Against
the real "Attention is All you Need" (Vaswani et al., 2017) response, this would have false-flagged a
completely genuine citation as unverified. Fixed by matching the surname against each individual name
TOKEN in a candidate's name, not the whole string. Also newly surfaced: the public unauthenticated
tier's rate limit is considerably tighter in practice than its documentation implies, repeated 429s
during testing, sometimes 8-14 retries before a single request cleared, confirming the fail-open path
fires correctly and often in real usage, not just in a contrived failure test, which is itself a
practical limitation worth naming: an opt-in check this likely to be rate-limited will silently skip
more often than it actually verifies, on the free tier, unless a paid/registered API key is used.

Not yet done: a full agentic run (a real AcademicSearcher dispatch through Ollama) with the flag
enabled, exercising the check through the orchestrator rather than by calling
`real_grounding_problem` directly, this remains a smaller, scoped gap than "unproven in production"
now that the actual matching logic has been validated against real API responses for both a genuine
and a fabricated citation.

## 4. Recurring design principles, evidenced

Each of these is stated here because a specific, dated incident in this project's own history
demonstrated it, not as an abstract value.

- **Structural enforcement beats prompted instruction, every time it's been tested.** The
  rename-nudge, the citation-exclusion fix, the tool-access withholding in §2, the empty-response
  salvage: every one of these exists because a prompt-only version of the same fix was tried or
  considered and either failed live or was predicted to fail based on cited literature (§3.4).
- **A mechanism firing correctly is not the same claim as the output being correct.** Confirmed the
  hard way on 2026-07-26: a report that mechanically passed every fired completion check was still
  100% about one half of a two-facet query, because the check that would have caught the other half
  vanishing didn't exist yet. Whether the pipeline ran correctly and whether the artifact is actually
  good are two separate questions; neither substitutes for the other.
- **Reuse before rebuilding.** The Analyzer-tier NLI-coverage fix (§3) turned out to need zero new
  code once the actual dispatch path was re-read; an earlier planning note had assumed new
  claim-extraction logic was required, and was simply wrong. Caught by re-reading the code, not by
  trusting the earlier note.
- **Verify against real historical data, not just a fresh unit test.** Several fixes were replayed
  directly against the actual `_run_state.json` of the run that originally exposed the bug, before
  being declared fixed, confirming the fix would have caught the exact real incident, not just a
  synthetic approximation of it.
- **A mechanism surviving one live re-run is not the same claim as a mechanism being load-bearing.**
  Every completion-check mechanism in §3 was validated by re-running the benchmark that exposed its
  bug and confirming the symptom didn't recur once, real evidence, but not a controlled comparison.
  The first genuine with-versus-without ablation (§3.7) confirmed two accumulated mechanisms were in
  fact causally responsible for the score difference they were credited with, not merely correlated
  with a run that happened to converge, the concrete demonstrated instance of this principle, not
  just an aspiration.
- **A discard verdict needs corroboration; a pass can stand on one clean run.** Formalized as the
  Model Evaluation Standard (§5) after two real fairness gaps were found on re-reading a past verdict
  critically. The standard exists because the project caught itself getting this wrong once, not as
  a hypothetical safeguard.

## 5. Model evaluation methodology

Every model bake-off verdict in `ROADMAP.md` must clear a 6-point standard before being treated as
settled, written after re-reading a past verdict critically surfaced two real fairness gaps that had
been sitting undetected in already-published conclusions:

1. Confirm the operating mode (think/nothink, tool format, context length) reaches the model via a
   raw API-level test before running a full benchmark, never infer from vendor docs alone.
2. Isolate the candidate as the only variable; every other pipeline role stays on the known-good
   baseline unless it is the role under test. A VRAM-forced swap elsewhere makes the run
   informational only, not a clean verdict.
3. State the serving backend and version alongside every verdict; a "disqualified" model may
   actually be a serving-layer bug (two independent Ollama bugs were found exactly this way).
4. A discard claim needs more than one run; a clean pass can stand on one.
5. Keep a verdict changelog, mark superseded entries and link the corrected retest, never overwrite
   history silently.
6. A candidate that can't fit the project's real minimum operating context on this hardware is
   discarded outright, not proportionally rescaled to fit, unless the ceiling is the model's own
   permanent architectural limit rather than a hardware-forced squeeze, in which case it's tested at
   its true native ceiling instead.

Full text and the incidents that motivated each point: `ROADMAP.md`'s "Model Evaluation Standard"
section.

## 6. Novelty assessment

Summarized from `RESEARCH.md` §9/§9a's dedicated research pass, primary sources read directly, repo
docs, papers, Anthropic's own published multi-agent-system engineering writeup, not search-snippet
skimming, including a follow-up pass that closed every gap the first pass left explicitly
unconfirmed.

**Not novel at the level of individual components.** Deterministic verification layers over agent
output exist (closest: a Claude Code coding-agent tool using deterministic test-pass/fail
verification, industry tooling, not academic). Retrieval-grounded citation attribution verification is
published and active research (CiteGuard, arXiv:2510.17853, not "VeriCite" as an earlier draft of this
document named it, corrected 2026-08-22 after re-reading the paper directly). Independent-context critics outperforming same-context
self-critique is an established literature finding (§3.2). Small frozen classifiers replacing LLM
judgment for a decision is precedented (RouteLLM-style routing). A separated citation-verification
pipeline stage exists in a major published system (Anthropic's own CitationAgent).

**The specific combination has no found precedent.** Every other system examined implements one of
two shapes: same-context or same-loop LLM-as-judge critique, sometimes with one deterministic
routing branch bolted on, as in LangGraph's Corrective RAG, but never a deterministic check itself,
or no documented verification mechanism at all for this exact problem (Perplexity has genuine
retrieval engineering depth but nothing published on citation verification specifically; OpenAI's
Deep Research system card is scoped to safety and red-teaming, not architecture). None combines
dozens of independently named, priority-ordered, empirically-motivated checks, a starvation guard for
that priority queue, and a regression test pinning the verification logic itself
(`test_structural_checks.py`'s verdict matrix, added specifically because two checks' branches once
silently merged and only a matrix test with real distinctive assertions caught it), all consistently
built for the local, sub-30B-model regime specifically, where every other system surveyed assumes a
frontier-class or well-resourced generator.

**Honest calibration**: the correct framing is "a disciplined, unusually deep combination of known
techniques applied to an underserved regime," not "invented verification for LLM agents from
scratch." That's still a real, citable contribution; nothing found in either research pass makes it
redundant. Explicit residual gap: one AG2 notebook, the one most likely to show genuine
tool-grounded deterministic feedback in that framework, returned a 404 on every fetch attempt during
research and was never independently verified, flagged rather than guessed at, worth a targeted
follow-up if an AG2 comparison specifically becomes load-bearing for any future external-facing
claim.

## 7. Known limitations, stated plainly

- **The empty-response tendency itself is unresolved at the model level.** The salvage path (§3.3)
  mitigates the consequence; it doesn't explain or fix why a fresh dispatch sometimes returns nothing
  at all. Frequency is still unknown, not re-observed as often on some runs as others.
- **The per-task verification ledger (§3.5) still only produces one whole-run `Verdict` per
  completion-check attempt.** A flagged task's directive names it specifically, but redelegating it
  still routes back through the Planner's own turn rather than an independent, bypassing dispatch.
  The deeper version (VERIMAP Phase 2) was scoped, evaluated against real data, and formally closed
  as a no-go on 2026-07-29, not left open pending more data: an audit of every real run where the
  ledger flag actually fired found zero clean, unconfounded cases of the same task recurring 3+ times
  under a normal retry budget with the directive-only fix genuinely failing to resolve it, every
  observed recurrence traced to an already-fixed, independent root cause instead. The directive-only
  version is the settled answer unless a future clean run reopens it under that same specific
  trigger.
- **The verification layer is reactive, not proactive, at its foundation.** Every check fires after a
  problem has already been generated, catching it before it reaches the next stage, not preventing
  the underlying model from generating it in the first place. This is a deliberate scope choice,
  matching this project's own repeatedly confirmed finding that verification and architecture
  amplify a capable model rather than rescuing an incapable one (`RESEARCH.md` §1's capacity-floor
  literature), not an oversight, but it means the approach has a ceiling: below some model capability
  floor, no amount of downstream checking produces a usable result, only an honestly labeled failure
  instead of a silently accepted bad one.
- **The academic citation existence check (§3.8) has been live-tested against the real Semantic
  Scholar API, not just mocks, but not yet through a full agentic run.** The live pass caught and
  fixed a real matching bug (surname-vs-full-name token comparison) the mocked unit tests could not
  have surfaced, and confirmed correct classification against real API responses for both a genuine
  and a fabricated citation. What remains is exercising it through an actual AcademicSearcher
  dispatch via the orchestrator rather than calling `real_grounding_problem` directly, a smaller gap
  than "unvalidated." Also confirmed live: the public unauthenticated tier's rate limit is tight
  enough in practice (repeated 429s, sometimes 8-14 retries before a request cleared) that the
  fail-open path will fire often on the free tier, worth knowing before relying on this check
  catching every fabricated citation in a given run. Unlike every other mechanism in §3, it was also
  scoped proactively rather than from a live incident this project's own runs produced.
- **This document itself has not been externally reviewed.** It's an internal synthesis, sourced
  from this project's own commits, tests, and a dedicated but necessarily time-bounded research pass,
  not a peer-reviewed claim.
