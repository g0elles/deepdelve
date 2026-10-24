# Literature check for the open items (2026-10-23)

Scope: the open items that had no literature behind them: source quality, contradiction detection, dated claims, second labeller. Rollback/selection is already covered by SCREWS in `RESEARCH_builder_fix_durability.md`. PDFs are in `papers/`. Each entry states exactly what was read. Search-engine summaries were used only to find candidates.

## 1. Weak sources (marketing pages, academia.edu, aggregator sites cited in a report)

**SourceBench, arXiv 2602.16942 (UCSD, 15 pp).** Read in full: body, results, conclusion, appendix rubric; the appendix prompt templates skimmed.
- Eight 1-5 rubrics: content (relevance, factual accuracy, neutrality) and page signals (freshness, ownership accountability, author accountability, domain authority, layout clarity). 100 queries, 3,996 cited sources, 12 systems.
- Freshness is uncorrelated with every other metric; the three accountability metrics correlate with each other. Authority and freshness are independent axes.
- The rubric itself hard-codes shortcuts: gov and edu domains get the top authority score automatically, content farms and parasite-SEO pages score 2, and missing ownership information is an automatic downgrade.
- The claim "weight source credibility" rests on correlation (20 HotpotQA queries; a DeepSeek-plus-search-tool pairing test), not on an intervention that filters sources.
- Weak points: the human reference is two graduate students on 45 pages with no agreement statistic. Table 3's per-metric errors exceed the 0.5 the text says is never exceeded (relevance 0.56, freshness 0.91), and the table's average does not match its own columns.

**CONFACT, arXiv 2505.17762 (SUTD, 15 pp).** Read in full except the GPT-4o appendix table.
- The only intervention study found. Source background (Media Bias/Fact Check ratings, optionally an LLM-written background) helps most when given to the model at answer generation together with chain-of-thought or an explicit instruction to discount unreliable sources (Llama-3.1 +10 F1, Mistral +5 accuracy; Qwen-2 gained least).
- Filtering and credibility-weighted ranking were less effective and sometimes hurt: credibility and relevance are not aligned, and filtering removes counter-evidence.
- Automatic credibility estimation was no better than expert ratings alone (70.04% zero-shot on the Baly benchmark); a small human test (20 claims, four researchers) showed generated backgrounds misled people too.
- Error analysis of 50 failures: models follow the majority (three low-credibility sources beat one authoritative one), and wrong generated backgrounds mislead.
- Humans overrated mainstream news (95.8% rated credible; of MBFC-flagged sources, 69.8% rated trustworthy). Agreement on the conflict split was Krippendorff alpha 0.586.
- Stated limits: MBFC's own bias, credibility treated as static across topics and time, simplified baselines. Binary claim verification with 2024-era 7-8B models, not report writing.

**Whose Facts Win?, arXiv 2601.03746 (HITS, 30 pp).** Read: body, limitations, ethics; appendices skimmed for purpose only (prompts, prompt-robustness checks).
- 13 open-weight models, synthetic sources and synthetic conflicts, forced-choice questions. Consistent preference for government over newspaper over individuals, popularity helps, demographics barely matter.
- Repeating a claim from low-credibility sources flips the preference; telling the model to consider credibility reduces this but does not remove it. A LoRA fix on a 4B model cut repetition bias by up to 79.2% while keeping 72.5% of the preferences.
- Limits: synthetic setting, English and US-centric, forced choice, no generation.

**Implication.** Credibility information should reach the writer or judge as context and be flagged, not used to silently drop or demote sources. A deterministic authority list (gov, edu, intergovernmental) is a defensible cheap signal because SourceBench's own rubric does the same; everything else needs a model, and the models are only moderately reliable. Repeated claims across aggregator pages can outvote an authority, which matches our corpus where many pages copy each other.

## 2. Contradiction between a report's gap statements and its body

**ContraDoc, arXiv 2311.09182 (UT Austin and Grammarly, 15 pp).** Read in full, appendix prompts skimmed.
- 449 contradictory and 442 clean English documents of 100-2,200 tokens; one synthetic contradiction per document, two annotators each (88% agreement on the scope tag).
- Asking whether a document contains a contradiction scores about 50% for GPT-3.5/4, PaLM2 and Llama-2. GPT-4 is conservative (recall 19.6%) but gave correct evidence 92.7% of the time when it said yes. If the evidence sentence is given and the model only has to find what contradicts it, GPT-4 reaches 77.2% and GPT-3.5 51.6%. Humans on long documents did better than GPT-3.5, worse than GPT-4.
- Numeric and negation contradictions are easiest; opinion and emotion hardest. Document length and distance were not the main factor.
- Stated limits: injected contradictions do not mimic human mistakes, only 25 documents of one type, English only.

**Implication.** Do not run a generic "is this report self-contradictory" judge. The supported shape is a targeted pair check: for each gap statement, retrieve body sentences that share its entity and topic words and judge only those pairs. Keep it advisory.

## 3. Dated-status against the report's own timeline

**RecencyQA, arXiv 2603.16544 (SIGIR 2026, 8 pp).** Read in full. It classifies how fast a question's answer changes (12 classes); best model 52% strict accuracy, and on context-shift transitions only 14.8%. It does not address date consistency inside a generated report.

**HoH, arXiv 2503.04800 (USTC, 28 pp).** Read: abstract, construction, experiments, timeliness analysis, limitations; appendices (dataset construction details) not read.
- Outdated passages hurt far more than distracting ones: one outdated passage cut Llama-70B's overall score by more than 24%, while six distractors cost under 2%. Models are confidently wrong when only outdated text is retrieved, but cautious when nothing is retrieved.
- Retrievers and rerankers keep retrieving outdated text (hit rate above 50%); time-decay helps but costs recall. Passage order changes scores by up to 25 to 50 points depending on the model. Adding the current date and page modification dates to the prompt was the default and was not enough.
- Limits: Wikipedia snapshots only, single-article questions, homogeneous outdated corpus.

No paper found that addresses a report stating conflicting dates for the same obligation. The closest category is ContraDoc's numeric type, which models detect best. The earlier offline prototype (fires on every table and staged deadline) matches the difficulty. Stay unbuilt until a labelled set exists. HoH does support one cheap input: giving the writer the current date and each source's last-modified date.

## 4. Second labeller for the claim-support judge

**Essential-Web v1.0, arXiv 2506.14111 (46 pp).** Read: abstract, introduction, taxonomy and evaluation sections (pp. 1-15) and appendix A.9.3; the downstream dataset experiments were skimmed, throughput and the remaining appendices not read.
- Six people annotated 1,150 documents on four taxonomy categories; each person agreed more with two strong LLMs (GPT-4o, Claude 3.5 Sonnet) than with the other people. The authors use this to justify LLM reference annotators.
- Kappa between candidate labelers and the two strong LLMs: 0.80 for DeepSeek-V3, 0.74 for Qwen2.5-32B, and 0.53 for Qwen2.5-14B. Mid-size models label noticeably worse.
- Limits: subjective multi-class taxonomy where humans disagree a lot; says nothing about claim-support judgements; higher LLM-human than human-human agreement does not show the LLM is right.

**Implication.** The queued model second labeller (`eval/claim_support/second_labeller.py`, gpt-oss-20b) is a sanity check and a cheap disagreement finder, not the independent human the ROADMAP asks for, and a 20B model may sit nearer the 14B result than the 32B one. Report kappa with the confusion matrix and send disagreements to a human. CONFACT's human alpha of 0.586 is a reminder that even a human second labeller will not agree perfectly.
