# W8b. Scholarly search, second A/B (distinct papers, planner held fixed, noise measured)

W8 A/B (PLAN_W8.md "Result", "Why it failed") failed on a URL-share metric that counted duplicates and could not separate the effect
from planner variance. Since then: same-paper dedupe (66c8014). v2 after EGM review 1 (0 blockers, 4 majors, 4 minors applied).

## Design
- Code: HEAD with 66c8014. ON = as is; OFF = `pr.scholar_search` returns []. ornith-1.5:9b at `settings.model_options` (the model of
  every W-series run; informational, no default model designated), config copy as W8; compiled writer; `--style academic` for acad.
- Planner fixed per pair: `pr.plan` called once per (query, pair), saved `research_output/w8b_<q>_r<k>.plan.json`, both arms reuse it.
  NOT fixed (review M1): DDGS results (`backend="auto"`), and sampled model calls (`judge`, `facet_unanswered`, gap-round choice,
  `reformulate_question`), whose inputs also differ by arm. So:
- Noise floor (A/A): 2 OFF-vs-OFF pairs with a fixed plan (c2 r3, pm25 r3). Reported next to the A/B.
- Queries (exact text). Scholarly class S (biomedical and not, review M4):
  - acad: the W8 Q-acad text. c2: the W8 c2 text.
  - vitd: "Does vitamin D supplementation reduce the risk of acute respiratory infections, and how strong is the evidence?"
  - cbti: "How effective is cognitive behavioural therapy for insomnia compared with sleep medication, according to clinical trials?"
  - pm25: "What does epidemiological research report about long-term air pollution exposure and dementia risk?"
  - minwage: "What does economic research find about the effect of minimum wage increases on employment?"
  Guard class G: leave (the W8 Q-leave text); heat: "How much does it cost to run a heat pump compared with a gas boiler in the UK?"
- 2 pairs per query (8 queries) + 2 A/A pairs = 36 runs, sequential, ON/OFF order alternated per pair. ~2.5 h. `rocm-smi` first,
  `ollama stop` after. Runner `eval/writer_defects/w8b_ab.py` (`w8_ab.py` + plan freezing + A/A), log `w8b_runs.tsv`.

## Measures (scorer `w8b_score.py`, committed before any run)
- (a) distinct peer-reviewed papers cited. Peer = host on `w8_score.PEER` extended with ehp.niehs.nih.gov, neurology.org,
  atsjournals.org, jcsm.aasm.org, ersjournals.com, ahajournals.org, pnas.org, science.org, aeaweb.org (nber.org and iza.org are working
  papers: not peer), and doi.org only when it is a scholarly source whose record names a journal that is not a preprint server (bioRxiv, medRxiv,
  Research Square, SSRN, Preprints, arXiv) (review M3: doi.org no longer counts by itself). Paper key: DOI from the URL; else PMCID
  from the URL, mapped to its DOI through one Europe PMC lookup at scoring time; else the URL itself. Never page text.
- (b) query parts answered (below), blind: report body with the list and markers stripped (`blind.py` cut), shuffled seed 7, arm hidden.
  A part is answered when the text states it with a cited figure or finding; a disclosed gap is not answered.
- (c) figures not in any saved source (`evaluate_run.py figs_unsupported`). (d) pipeline seconds.
- (e) G reports: every cited source that came from `scholar_search` (saved text starts "Content-Level:") must be on topic: the cited
  sentence states a statutory leave rule (days or pay) for France, Brazil or South Korea (leave), or a UK running cost, efficiency
  (COP/SCOP) or fuel price for heat pumps or gas boilers (heat). Rule-labelled by me, every such sentence listed for the user.

## Parts (fixed before any report is read)
acad: Chile effect on sugary-drink purchases with a figure; Mexico effect with a figure; comparison or a reasoned not-comparable statement.
c2: semaglutide effect on drinking in AUD; naltrexone effect; comparison of the evidence. vitd: effect on infection risk (direction and
size); strength of evidence. cbti: CBT-I effect; medication effect; comparison. pm25: association direction and size; evidence type or
causal caveat. minwage: employment effect finding; range or disagreement in the research. leave: days and pay rule for each of France,
Brazil, South Korea (6 parts). heat: heat-pump running cost; gas-boiler running cost; comparison.

## Bar (all; review M2: sign test, not "every pair")
1. S, (a): over the 12 S pairs, ties dropped, ON wins a one-sided sign test at p <= 0.05 (e.g. >= 9 of 10 untied, >= 8 of 8).
   And the median ON-OFF difference exceeds the largest A/A |difference| on (a); else verdict "inconclusive", not pass.
2. (b): total parts answered ON >= OFF over all 16 pairs, and no query where ON answers fewer parts in both pairs.
3. G: every (e) source on topic. 4. (c) = 0 in every ON report. 5. Median pipeline seconds ON <= OFF + 60.
Fail -> revert W8 code (scholar_search, scholar_text, dedupe; W9 stays), record why here. Inconclusive -> report to the user, no
change. No retuning after results are read.

FROZEN before any W8b run: sha256 d9fd65800aa32c8a9ecfb35f7d1e8f2bc899f07881e025fe08f73fad098b6ee0 (of the file above this line).
