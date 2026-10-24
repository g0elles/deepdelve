# How agent harnesses are built: practices and what they mean for DeepDelve (2026-10-23)

Question: what do primary sources say about building a proper harness, and which of it applies to this project. Web articles were fetched as raw pages and read by me (not through a summarising fetch). The OpenAI page blocks scripted fetches and was read through the browser. PDFs are in `papers/`.

## Sources and what was read

| Source | Read |
|---|---|
| Anthropic, Effective harnesses for long-running agents (Nov 2025) | all |
| Anthropic, Harness design for long-running application development (Mar 2026) | all (appendix example spec skimmed) |
| Anthropic, Scaling Managed Agents: decoupling brain from hands (Apr 2026) | all |
| Anthropic, Effective context engineering for AI agents | all |
| Anthropic, How we built our multi-agent research system | all |
| Anthropic, Building effective agents | all |
| Fowler/Böckeler, Harness engineering for coding agent users (Apr 2026) | all |
| OpenAI, Harness engineering: leveraging Codex in an agent-first world (Feb 2026) | all |
| arXiv 2605.26112, Scaling the harness (14 pp, position paper) | all but the reference list |
| arXiv 2608.01964, LongHorizon-Harness (29 pp) | main body and benchmark appendix analysis; case-study figures and prompt appendices not read |
| arXiv 2605.21516, Harnesses as inference-time alignment (35 pp) | introduction, model summary, experiments, conclusion; proofs and appendices not read |
| arXiv 2407.01489, Agentless (25 pp) | introduction, method, ablations; appendix and threats section skimmed |
| arXiv 2405.15793, SWE-agent (118 pp) | pages 1-12 only; the remaining pages are appendix |
| EvoHarness-RL 2608.05446, HarnessBridge 2606.12882, OneDayAgent 2608.05013 | NOT read; leads only |

## What the sources agree on

1. **Agent = model + harness; the harness often matters as much as the model.** Interface design alone moved SWE-bench by 10.7 points with a fixed model (SWE-agent). A task-state layer lifted one model from 51.8% to 80.7% on WeaveBench and 69.7% to 77.2% on Terminal-Bench (LongHorizon-Harness, matched backbone; no component ablation reported).
2. **Separate the doer from the judge.** Agents praise their own work; a standalone evaluator is far easier to make skeptical than a self-critical generator (Anthropic, harness design). The evaluator must be tuned: read its logs, find where it disagrees with a human, fix the prompt, repeat. Out of the box it talks itself out of real findings. Use hard per-criterion thresholds and few-shot calibration.
3. **Verify against the environment, not the agent's own claim.** LongHorizon-Harness updates task state only from a read-only auditor's evidence; executor reports never change state. Anthropic's testing results and OpenAI's browser, log and metric access make the same point.
4. **Keep an explicit, durable task state outside the working context.** Feature list with pass/fail flags, progress file, git history (Anthropic); requirement/artifact/fact records marked completed, pending, blocked or untrusted (LongHorizon-Harness); the session as an append-only log outside the harness so the harness itself can crash and restart (Managed Agents). Fresh-context workers get only the state they need.
5. **Guides and sensors, cheap before costly.** Feedforward guides raise first-attempt quality; feedback sensors correct. Deterministic (computational) sensors run on every change, model-based (inferential) ones sparingly and later; push checks as far left as possible (Fowler). Sensor messages written for the model, with remediation text, work as steering (Fowler, OpenAI custom linters).
6. **Context is a scarce resource.** Smallest set of high-signal tokens; a short map plus progressive disclosure beats a giant instruction file, which rots and crowds out the task (Anthropic context engineering, OpenAI). Compaction, structured notes and sub-agents with condensed returns are the three long-horizon tools.
7. **Every harness component encodes an assumption about what the model cannot do; test them.** Remove one component at a time and measure; strip what is no longer load-bearing when the model changes (Anthropic, twice). The evaluator is worth its cost only when the task sits beyond what the model does reliably alone.
8. **Evaluate outcomes and process together, and for repeatability.** End-state evaluation over turn-by-turn; start with about 20 representative queries; a single rubric LLM judge was the most consistent; human testing still finds what evals miss (Anthropic). Report cost, tokens, retries and pass^k, not only success (scaling-the-harness paper, tau-bench via it).
9. **Observability and resumability are first-class.** Full tracing to diagnose non-deterministic failures; resume from the failure point, not restart; deterministic safeguards (retries, checkpoints) around model adaptability (Anthropic, multi-agent research).
10. **Simplest thing that works; more structure is not monotonically better.** Fixed workflow with sampled candidates and executable validation beat complex agents at far lower cost (Agentless, with real ablations). One paper argues partial harnesses can beat fully specified ones, but its evidence is a synthetic task plus one real case study, so treat it as a hypothesis.

## Warnings in the sources that match what we saw

- Guidance that does not follow the task evidence raises hallucination in a controlled test, and stronger guidance amplifies the effect (arXiv 2605.21516, controlled task). This is the mechanism behind our hypothesis that the underuse check pads reports with irrelevant sources.
- Anthropic's own testers saw research agents prefer SEO content farms over authoritative sources and fixed it with source-quality heuristics in the prompt (multi-agent research), which agrees with the earlier note: give the writer source context, do not filter.
- The model-written harness fixes that "do not stick" are the task-state loss problem in LongHorizon-Harness's terms: progress only counts if it is recorded outside the executor and re-verified.
- Checks without measurement are untested assumptions: Fowler asks for a way to evaluate harness coverage, and notes that sensors that never fire are ambiguous.

## What this means for DeepDelve

What we already have in this vocabulary: generator/evaluator separation (Builder / PeerReviewer), fresh-context writers, about 25 computational sensors with literal remediation text, persisted run state with resume, and an orchestrator-worker research stage.

Gaps against the practice above:
1. **No explicit verified-requirements ledger.** Standing requirements restate one invariant; there is no record of which requirements the last audited draft met, so later rewrites can silently drop them. This is the closest match to "task-state loss".
2. **No measured value for any component.** We know what each check costs (Step 0 Pareto) but not what it buys. Nothing has been ablated: not the Review-then-Fix pass, not individual checks.
3. **The evaluator is uncalibrated.** PeerReviewer has never been compared with human labels.
4. **Check order.** Cheap computational checks run after the costly model-based review pass, not before.
5. **Reporting.** Runs are compared on mean time; the sources ask for pass^k and process metrics together.

Proposed order (each step pre-registers its pass rule):
- Build the measurement harness first: frozen evidence sets, frozen clean drafts with injected single faults, a runner with per-component switches (each check, the review pass, the fix pass), outcome metrics (fixed in one pass, collateral damage) and process metrics (minutes per dispatch, now recorded). This is Step 1 of the existing plan and the sources support it as the way to ablate.
- Use it to rank components by value per minute: ablate the Review-then-Fix pass, then each expensive check.
- Calibrate PeerReviewer on injected faults and on hand labels.
- Only then consider structural changes (a requirements ledger audited by the existing checks; cheap checks before the review pass), each as its own experiment.
