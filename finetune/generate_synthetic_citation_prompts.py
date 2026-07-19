"""
Generates diverse citation-grounding PROMPTS (not responses) at zero GPU cost, for GRPO training —
the citation_grounding counterpart to generate_synthetic_prompts.py's thin_coverage scenarios. See
that file's own docstring for the underlying principle (GRPO needs PROMPT diversity, not response
diversity — the model generates its own completions at train time, finetune/reward.py's
citation_grounding_response_reward scores whatever comes out).

A citation-grounding prompt is 100% deterministic too: it's whatever real_grounding_problem()
computes from a set of actually-"fetched" URLs plus a candidate report's citations, fed through
engine/completion.py::check_not_grounded's real Verdict.inject text. This script builds varied but
realistic fetch/citation scenarios (many topics, source counts, hallucination shapes) and calls
that REAL pipeline directly — zero fabrication in the check logic itself, only the SITUATION
(which topics, which claim lacks a real source) is synthetic.

REDESIGNED 2026-07-19 after the first version proved too easy: v1 gave the model 1-2 obviously-real
sources plus ONE explicitly-named-bad URL to avoid, worded as a bare "an additional claim cited to
X" bullet — training against it produced reward=1.0/std=0 on literally every one of the first 30
GRPO steps (zero gradient, nothing to learn; see session_status/CURRENT.md for the full diagnosis).
The real 2026-07-18 benchmark failure this fine-tune targets was harder than that: the unverifiable
claim was mixed in among many genuinely grounded findings, worded as a normal-looking finding
(SOMETHING to actually read and weigh), carrying orchestrator.py's own real
"[SYSTEM VERIFICATION WARNING...]" text attached to it — not called out separately — and for the
worst 6/9 cases there was NO alternative real source for that specific claim at all, creating a
genuine "cite the flagged one or admit a gap" dilemma instead of a trivial "don't use that one URL"
instruction. This version reproduces that shape: each scenario is a list of CLAIMS, each either
grounded (a real fetched URL) or unverifiable (a fabricated URL, with orchestrator's own real
warning text baked into its finding entry, and structurally no clean alternative for that claim).

Only genuinely UNGROUNDED scenarios produce a prompt here — a scenario where every citation is
real makes check_not_grounded return None (nothing to correct), so there is no corrective-nudge
text to capture. This mirrors thin_coverage's own scenario filtering (`if verdict is None: continue`).

Output is kept in a separate file from extract_dataset.py's real-mined examples
(citation_grounding_synthetic_prompts.jsonl vs. citation_grounding.jsonl), same separation
discipline as thin_coverage's two files.

Usage:
  python finetune/generate_synthetic_citation_prompts.py --out finetune/data/citation_grounding_synthetic_prompts.jsonl
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config as app_config  # noqa: E402
from engine.completion import check_not_grounded, Ctx  # noqa: E402
from utils.grounding import real_grounding_problem  # noqa: E402
from utils.run_state import RunState, record_fetched_url, reset_fetched_urls  # noqa: E402

# Same real warning text orchestrator.py:748-753 actually attaches to a finding whose specialist
# summary cited an unverified source — reused verbatim (not paraphrased) so the training prompt's
# "findings.md" block is byte-for-byte what the live system would have produced, not an
# approximation of it.
def _verification_warning(problem: str) -> str:
    return (
        f"\n\n[SYSTEM VERIFICATION WARNING: this summary attributes a claim to a source that does "
        f"not match anything actually fetched this run, or to something that isn't a real URL at "
        f"all ({problem}). Do not treat the associated claim as sourced when writing findings.md.]"
    )


# Deliberately diverse across domains, disjoint from thin_coverage's own SCENARIOS topic list.
# Each scenario: (topic label, [claims]), where a claim is
# (claim_label, real_url_or_None, filename_or_fabricated_url, one_line_finding_text).
# real_url_or_None is None for an UNVERIFIABLE claim — no clean alternative real source exists for
# it in this scenario, matching the real 2026-07-18 case's dominant failure shape (6/9 citations
# traced to a task with zero real fetch, not to a model choosing a flagged URL over an available
# clean one). Every scenario has at least one grounded claim (matching the real run's own mixed
# 9/16-warned-of-16-total shape, never a wholesale-fabricated report — that's fully_ungrounded's
# job, a different check).
SCENARIOS = [
    ("octopus distributed cognition research", [
        ("neural signal distribution across arms", "https://www.nature.com/articles/octopus-neural-distribution", "nature_octopus_neural.md",
         "Roughly two-thirds of an octopus's neurons reside in its arms rather than its central brain, enabling semi-autonomous local processing."),
        ("arm autonomy during exploratory reaching", "https://www.pnas.org/doi/octopus-arm-autonomy", "pnas_octopus_arm.md",
         "Severed arms retain coordinated reaching and grasping behavior for minutes after separation from the central brain."),
        ("subjective consciousness and self-awareness debate", None, "https://www.sciencedirect.com/science/article/octopus-consciousness-2024",
         "A 2024 mirror-test variant reportedly showed octopuses exhibiting self-directed investigative behavior consistent with self-awareness."),
    ]),
    ("volcanic eruption prediction models", [
        ("seismic precursor pattern recognition", "https://pubs.usgs.gov/volcano-forecast-2025", "usgs_volcano_forecast.md",
         "Machine-learning models trained on seismic swarm data improved short-term eruption forecast lead time at monitored volcanoes."),
        ("gas emission ratio monitoring", "https://agupubs.onlinelibrary.wiley.com/doi/eruption-precursors", "agu_eruption_precursors.md",
         "Shifts in SO2-to-CO2 emission ratios preceded several recent eruptions by days to weeks."),
        ("deep-learning satellite deformation forecasting breakthrough", None, "https://www.volcanodiscovery.com/predictive-model-breakthrough",
         "A new satellite InSAR deep-learning model reportedly predicted a stratovolcano eruption three weeks in advance."),
    ]),
    ("Maya hieroglyph decipherment progress", [
        ("emblem glyph phonetic reading advances", "https://www.mesoweb.com/articles/maya-decipherment-2025", "mesoweb_maya_decipherment.md",
         "Recent epigraphic work refined the phonetic reading of several previously ambiguous emblem glyphs."),
        ("comprehensive digital glyph database completion", None, "https://www.famsi.org/reports/emblem-glyph-database-update",
         "A newly completed digital database reportedly catalogs every known emblem glyph variant with confirmed phonetic values."),
    ]),
    ("deep-ocean hydrothermal vent ecosystems", [
        ("chemosynthetic bacterial symbiosis", "https://www.whoi.edu/research/hydrothermal-vent-biodiversity", "whoi_vent_biodiversity.md",
         "Tube worms at hydrothermal vents host chemosynthetic bacteria that oxidize hydrogen sulfide as an energy source."),
        ("vent ecosystem chemosynthesis pathway mapping", "https://www.science.org/doi/vent-chemosynthesis-2025", "science_vent_chemosynthesis.md",
         "Newly mapped metabolic pathways show vent bacteria fix carbon via a distinct pathway from photosynthetic organisms."),
        ("comprehensive vent species catalog update", None, "https://oceanexplorer.noaa.gov/vent-species-catalog-2025",
         "An updated catalog reportedly documents 40 newly identified vent-endemic species across three ocean basins."),
    ]),
    ("exoplanet atmospheric biosignature detection", [
        ("JWST transmission spectroscopy candidates", "https://www.jwst.nasa.gov/content/biosignature-candidates", "jwst_biosignatures.md",
         "JWST transmission spectra identified several sub-Neptune atmospheres with candidate biosignature gas absorption features."),
        ("confirmed atmospheric oxygen detection", None, "https://arxiv.org/abs/exoplanet-o2-detection-2025",
         "A preprint reportedly claims confirmed detection of free molecular oxygen in a temperate exoplanet's atmosphere."),
    ]),
    ("honeybee colony collapse disorder causes", [
        ("national colony loss survey data", "https://www.usda.gov/reports/colony-collapse-2025", "usda_colony_collapse.md",
         "Annual survey data shows managed colony losses remain elevated compared to pre-2006 baseline rates."),
        ("neonicotinoid exposure and mortality correlation", "https://onlinelibrary.wiley.com/doi/pesticide-bee-mortality", "wiley_pesticide_bee.md",
         "Field studies correlate sublethal neonicotinoid exposure with reduced forager return rates."),
        ("effectiveness of a recent neonicotinoid ban", None, "https://www.beeculture.com/neonicotinoid-ban-impact-study",
         "A regional ban reportedly produced a measurable rebound in colony survival rates within two seasons."),
    ]),
    ("ancient river civilization water management", [
        ("Indus Valley canal engineering", "https://www.jstor.org/stable/indus-valley-hydrology", "jstor_indus_hydrology.md",
         "Excavated canal networks show sophisticated gradient-controlled water distribution across Indus Valley settlements."),
        ("citywide drainage system discovery", None, "https://www.archaeology.org/harappan-canal-network-2025",
         "A newly excavated network reportedly reveals a previously unknown citywide drainage system at a major Harappan site."),
    ]),
    ("glacial retreat freshwater supply impact", [
        ("Himalayan glacier mass loss trends", "https://www.nature.com/articles/himalayan-glacier-retreat", "nature_himalayan_glacier.md",
         "Satellite-derived mass balance data show accelerating Himalayan glacier retreat over the past two decades."),
        ("cryosphere contribution to regional water supply", "https://www.ipcc.ch/report/cryosphere-water-supply", "ipcc_cryosphere_water.md",
         "Glacial meltwater contributes a majority share of dry-season river flow for several major Asian river basins."),
        ("Andean meltwater crisis projection", None, "https://www.glacierhub.org/andean-meltwater-crisis-2025",
         "A new projection reportedly warns of an imminent multi-city water crisis as Andean glaciers approach a tipping point."),
    ]),
    ("bat echolocation neural processing", [
        ("auditory cortex echo-delay mapping", "https://www.cell.com/neuron/bat-echolocation-cortex", "cell_bat_echolocation.md",
         "Neurons in the bat auditory cortex are tuned to specific echo-delay intervals corresponding to target distance."),
        ("real-time spatial mapping breakthrough", None, "https://www.jneurosci.org/content/bat-auditory-mapping-2025",
         "A new imaging study reportedly reconstructs a bat's full real-time spatial map from single-neuron recordings."),
    ]),
    ("Antarctic ice core paleoclimate records", [
        ("ice core dating methodology", "https://www.antarctica.gov/research/ice-core-dating", "antarctica_ice_core.md",
         "Layer-counting combined with volcanic ash markers provides annual-resolution dating for the upper ice core sections."),
        ("historical CO2 concentration record", "https://climate.nasa.gov/evidence/ice-core-co2-record", "nasa_ice_core_co2.md",
         "Trapped air bubbles in ice cores provide a direct atmospheric CO2 record extending back roughly 800,000 years."),
        ("800,000-year record extension claim", None, "https://www.pnas.org/doi/dome-c-800000-year-record-2025",
         "A new Dome C core reportedly extends the continuous climate record to 1.2 million years."),
    ]),
    ("crow tool-use cognitive studies", [
        ("New Caledonian crow tool manufacture", "https://www.pnas.org/doi/new-caledonian-crow-tools", "pnas_crow_tools.md",
         "New Caledonian crows manufacture hooked tools from twigs, shaping them before use rather than using found objects unmodified."),
        ("multi-step planning capability claim", None, "https://www.sciencedirect.com/science/article/corvid-planning-2025",
         "A study reportedly demonstrates corvids planning three sequential tool-use steps without trial-and-error."),
    ]),
    ("Roman concrete durability chemistry", [
        ("lime clast self-healing mechanism", "https://www.mit.edu/news/roman-concrete-self-healing", "mit_roman_concrete.md",
         "Lime clasts embedded in Roman concrete can dissolve and reprecipitate to seal cracks when exposed to water."),
        ("hot-mixing production technique evidence", "https://www.jacers.org/doi/lime-clast-mechanism", "jacers_lime_clast.md",
         "Chemical analysis supports a hot-mixing production process rather than the previously assumed slaked-lime method."),
        ("Pantheon dome longevity explanation", None, "https://www.archaeology.org/pantheon-dome-longevity-2025",
         "A new analysis reportedly attributes the Pantheon dome's 1,900-year survival primarily to a specific volcanic ash ratio."),
    ]),
    ("permafrost ancient virus revival risk", [
        ("permafrost virus viability studies", "https://www.cell.com/iscience/permafrost-virus-revival", "cell_permafrost_virus.md",
         "Several ancient virus lineages isolated from permafrost samples remained capable of infecting amoeba hosts in lab conditions."),
        ("infectivity risk to modern hosts claim", None, "https://www.nature.com/articles/paleovirus-infectivity-2025",
         "A new paper reportedly assesses several revived paleoviruses as posing a credible infection risk to modern mammals."),
    ]),
    ("mantle plume volcanic hotspot theory", [
        ("seismic tomography plume imaging", "https://www.science.org/doi/mantle-plume-seismic-imaging", "science_mantle_plume.md",
         "Seismic tomography reveals a continuous low-velocity conduit extending from the core-mantle boundary beneath several hotspots."),
        ("Hawaii hotspot track age progression", "https://agupubs.onlinelibrary.wiley.com/doi/hawaii-hotspot-track", "agu_hawaii_hotspot.md",
         "Radiometric dating of the Hawaiian-Emperor seamount chain shows an age progression consistent with plate motion over a fixed hotspot."),
        ("Yellowstone plume existence controversy resolution", None, "https://www.geosociety.org/yellowstone-plume-controversy-2025",
         "A new study reportedly settles the long-running debate by confirming a deep mantle plume source beneath Yellowstone."),
    ]),
    ("octopus camouflage neural control mechanisms", [
        ("chromatophore neural control pathway", "https://www.pnas.org/doi/cephalopod-skin-chromatophores", "pnas_cephalopod_chromatophores.md",
         "Chromatophores are controlled directly by motor neurons rather than hormonally, enabling near-instantaneous color change."),
        ("learned camouflage pattern selection claim", None, "https://www.cell.com/current-biology/octopus-camouflage-learning-2025",
         "A study reportedly shows octopuses learning and reusing specific camouflage patterns matched to individual hiding spots."),
    ]),
    ("Great Barrier Reef coral bleaching recovery", [
        ("regional bleaching recovery survey", "https://www.gbrmpa.gov.au/reports/bleaching-recovery-2025", "gbrmpa_bleaching_recovery.md",
         "Survey data show partial coral cover recovery in northern reef sections following a multi-year bleaching event."),
        ("thermal adaptation in surviving colonies", "https://www.science.org/doi/coral-thermal-adaptation", "science_coral_thermal.md",
         "Surviving coral colonies show measurable shifts in symbiont thermal tolerance compared to pre-bleaching populations."),
        ("restoration technique success rate claim", None, "https://www.aims.gov.au/reef-restoration-success-rate-2025",
         "A new report reportedly puts large-scale coral restoration success rates above 80 percent at trial sites."),
    ]),
    ("ancient Egyptian mummification chemistry", [
        ("embalming resin chemical analysis", "https://www.nature.com/articles/mummification-resin-analysis", "nature_mummification_resin.md",
         "Chemical analysis of embalming residues identifies a consistent multi-ingredient resin recipe across several dynastic periods."),
        ("Saqqara embalming workshop discovery claim", None, "https://www.jstor.org/stable/saqqara-embalming-workshop-2025",
         "A newly excavated workshop reportedly contained labeled vessels naming the specific substances used in each embalming stage."),
    ]),
    ("dark matter direct detection experiments", [
        ("XENONnT exclusion limit results", "https://www.symmetrymagazine.org/xenon-nt-results-2025", "symmetry_xenon_nt.md",
         "The XENONnT experiment set a new exclusion limit on WIMP-nucleon cross-sections at its latest exposure."),
        ("LZ experiment constraint update", "https://arxiv.org/abs/lz-experiment-constraints", "arxiv_lz_experiment.md",
         "The LUX-ZEPLIN experiment's latest run further constrained the same parameter space with independent detector technology."),
        ("annual modulation signal confirmation claim", None, "https://www.pnas.org/doi/dark-matter-annual-modulation-2025",
         "A paper reportedly claims independent confirmation of the long-disputed DAMA/LIBRA annual modulation signal."),
    ]),
    ("migratory bird magnetoreception mechanism", [
        ("cryptochrome radical-pair mechanism", "https://www.nature.com/articles/cryptochrome-magnetoreception", "nature_cryptochrome.md",
         "Cryptochrome proteins in the retina are proposed to sense magnetic field orientation via a radical-pair quantum mechanism."),
        ("robin navigation precision claim", None, "https://www.sciencedirect.com/science/article/robin-navigation-2025",
         "A study reportedly shows robins navigating with sub-kilometer precision using magnetic cues alone, without visual landmarks."),
    ]),
    ("Antikythera mechanism reconstruction studies", [
        ("gear train reconstruction from X-ray tomography", "https://www.antikythera-mechanism.gr/research/gear-reconstruction", "antikythera_gear.md",
         "X-ray tomography of the surviving fragments enabled a more complete reconstruction of the mechanism's gear train."),
        ("full internal structure imaging", "https://www.nature.com/articles/antikythera-x-ray-tomography", "nature_antikythera_tomography.md",
         "High-resolution tomographic imaging revealed previously undocumented internal gear teeth counts."),
        ("eclipse prediction dial function confirmation claim", None, "https://www.jstor.org/stable/antikythera-eclipse-prediction-2025",
         "A new analysis reportedly confirms the back dial precisely predicted both solar and lunar eclipses for an 18-year cycle."),
    ]),
]

# Held out from training entirely — used only by evaluate_citation_grounding.py to compare base vs
# fine-tuned on genuinely unseen topics/claims, same claim-shape convention as SCENARIOS above.
HELD_OUT_SCENARIOS = [
    ("bioluminescent deep-sea fish signaling", [
        ("counter-illumination camouflage function", "https://www.mbari.org/research/bioluminescence-counter-illumination", "mbari_counter_illumination.md",
         "Many mesopelagic fish use ventral photophores to match downwelling light and erase their silhouette from predators below."),
        ("species-specific flash pattern recognition claim", None, "https://www.deepseanews.org/flash-pattern-species-id-2025",
         "A new study reportedly shows anglerfish species recognizing potential mates purely from species-specific bioluminescent flash timing."),
    ]),
    ("medieval trans-Saharan gold trade routes", [
        ("Ghana Empire trade route archaeology", "https://www.jstor.org/stable/ghana-empire-gold-routes", "jstor_ghana_gold_routes.md",
         "Archaeological trade-good distribution supports a well-established caravan route linking Ghana Empire gold fields to North African markets."),
        ("salt-for-gold exchange ratio evidence", "https://www.cambridge.org/core/journal-medieval-african-trade", "cambridge_salt_gold_exchange.md",
         "Contemporary Arab traveler accounts document consistent salt-for-gold exchange ratios across major trade centers."),
        ("Mansa Musa pilgrimage economic impact claim", None, "https://www.historytoday.com/mansa-musa-cairo-gold-crash-2025",
         "A new economic analysis reportedly quantifies a multi-year gold price depression in Cairo directly attributable to Mansa Musa's 1324 pilgrimage spending."),
    ]),
    ("octopus short-term memory formation", [
        ("vertical lobe memory encoding studies", "https://www.jneurosci.org/content/octopus-vertical-lobe-memory", "jneurosci_vertical_lobe.md",
         "The octopus vertical lobe shows structural parallels to hippocampal memory encoding despite an independently evolved nervous system."),
        ("long-term maze learning retention claim", None, "https://www.biorxiv.org/content/octopus-maze-retention-2025",
         "A preprint reportedly demonstrates octopuses retaining a learned maze solution for over six months without reinforcement."),
    ]),
]


def _finding_entries(claims: list[tuple[str, str | None, str, str]]) -> list[str]:
    entries = []
    for _claim_label, real_url, filename_or_fabricated, finding_text in claims:
        if real_url:
            entries.append(f"### Source: {real_url}\n{finding_text} (saved as {filename_or_fabricated})")
        else:
            problem = f"unverified_urls:{filename_or_fabricated}"
            entries.append(f"### Source: {filename_or_fabricated}\n{finding_text}{_verification_warning(problem)}")
    return entries


def _findings_block(claims: list[tuple[str, str | None, str, str]], topic: str) -> str:
    """Mirrors _build_findings_source_material's real shape (src/engine/completion.py:819-860):
    one ### Source entry per claim. A grounded claim reads as ordinary real content; an
    unverifiable claim's entry carries the SAME finding-summary text an ungrounded specialist
    dispatch would produce, with orchestrator.py's real verification warning appended — the model
    has to actually notice and respect that warning per-entry, not just avoid one named-bad URL."""
    return (
        "REAL RESEARCH RESULTS FROM THIS RUN, one entry per dispatched task (this is your ENTIRE "
        "evidence base — you have no other memory of this run):\n\n" + "\n\n".join(_finding_entries(claims))
    )


def _findings_writer_prompt(claims: list[tuple[str, str | None, str, str]], findings_ungrounded: bool) -> str:
    """Real FindingsWriter dispatch shape (src/engine/completion.py:1005-1023 + 819-860's
    _build_findings_source_material) — role gap found live 2026-07-19: the prior version of this
    script only ever built Builder's "rewrite final_report.md" prompt. FindingsWriter shares the
    exact same write_workspace_file tool and citation_grounding_response_reward scoring, but its
    OWN real prompt is structurally different (a fresh write from raw research results, including
    the "ALL URLS ACTUALLY FETCHED" cross-reference block Builder's prompt never has) — leaving it
    untrained would mean the role that writes findings.md FIRST, and is just as able to cite an
    unverified source into it, never saw a single training example."""
    if findings_ungrounded:
        write_directive = (
            "The previous findings.md draft was fabricated or wholesale ungrounded and has been "
            "moved aside. Rebuild it now, strictly from the real research results below — never "
            "from your own prior knowledge."
        )
    else:
        write_directive = "findings.md has never been written yet. Write it now from the real research results below."
    fetched_block = "\n".join(
        f"- {real_url} (saved as {filename})" for _l, real_url, filename, _t in claims if real_url
    ) or "(no URLs fetched yet)"
    source_material = (
        "REAL RESEARCH RESULTS FROM THIS RUN, one entry per dispatched task (this is your ENTIRE "
        "evidence base — you have no other memory of this run):\n\n"
        + "\n\n".join(_finding_entries(claims))
        + "\n\nALL URLS ACTUALLY FETCHED THIS RUN, for cross-reference — each file's full content "
        "is readable under its saved filename via read_workspace_file/grep_workspace_file if a "
        f"summary above isn't detailed enough:\n{fetched_block}"
    )
    return f"{write_directive}\n\n{source_material}\n\nWrite the file now via write_workspace_file."


def build_scenario_ctx(claims: list[tuple[str, str | None, str, str]], attempt: int,
                        max_attempts: int, tmpdir: str) -> tuple[Ctx, str]:
    reset_fetched_urls()
    grounded = [(u, f) for _label, u, f, _text in claims if u]
    for url, filename in grounded:
        record_fetched_url(url, filename)

    # The "previous draft" that gets flagged: cites every claim's URL, grounded or not — exactly
    # the live shape (a report that's mostly grounded but includes the unverifiable claims too,
    # not a wholesale fabrication, which fully_ungrounded/check_findings_ungrounded already own).
    lines = ["## Report\n"]
    for _label, real_url, filename_or_fabricated, finding_text in claims:
        cited = real_url or filename_or_fabricated
        lines.append(f"- {finding_text} [source]({cited})")
    content = "\n".join(lines)

    rs = RunState(tmpdir)
    rs.set_query("synthetic citation-grounding scenario")
    ctx = Ctx(
        req_artifact="final_report.md", attempt=attempt, max_attempts=max_attempts,
        delegated=True, files=["final_report.md"], content=content, quotas=None, run_state=rs,
    )
    return ctx, content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/citation_grounding_synthetic_prompts.jsonl")
    parser.add_argument("--held-out", action="store_true",
                         help="Generate from HELD_OUT_SCENARIOS instead (topics never used in training)")
    args = parser.parse_args()
    scenarios = HELD_OUT_SCENARIOS if args.held_out else SCENARIOS
    if args.held_out and args.out == parser.get_default("out"):
        args.out = "finetune/data/citation_grounding_heldout_prompts.jsonl"

    app_config.cfg.setdefault("settings", {})
    app_config.cfg["settings"].setdefault("grounding_check", {})

    examples = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for topic, claims in scenarios:
            for attempt, max_attempts, escalated in ((0, 8, False), (7, 8, True)):
                ctx, content = build_scenario_ctx(claims, attempt, max_attempts, tmpdir)
                ctx.grounding_problem = asyncio.run(real_grounding_problem(content))
                verdict = check_not_grounded(ctx)
                if verdict is None:
                    continue  # matching didn't actually flag the unverifiable claim(s) — skip, don't fake it
                # Same template src/engine/completion.py:995-997 actually dispatches to Builder with,
                # findings.md's content inlined in place of the real read_workspace_file round-trip
                # this single-turn setup can't do (see _findings_block's docstring above).
                builder_instructions = (
                    f"Rewrite 'final_report.md' from findings.md, fixing this specific problem:\n"
                    f"{verdict.inject}\n\n{_findings_block(claims, topic)}\n\n"
                    f"Write the corrected file now via write_workspace_file."
                )
                real_fetched_urls = [real_url for _label, real_url, _f, _t in claims if real_url]
                unverifiable = [label for label, real_url, _f, _t in claims if not real_url]
                examples.append({
                    "topic": topic,
                    "role": "Builder",
                    "real_fetched_urls": real_fetched_urls,
                    "unverifiable_claims": unverifiable,
                    "prompt": builder_instructions,
                    "warning": verdict.warning,
                    "escalated": escalated,
                    "source": "synthetic_scenario_real_check_not_grounded",
                })

                # FindingsWriter-shaped counterpart, same claims/ground-truth, real different
                # prompt shape (see _findings_writer_prompt's docstring) — role gap fix, 2026-07-19.
                findings_writer_prompt = _findings_writer_prompt(claims, findings_ungrounded=escalated)
                examples.append({
                    "topic": topic,
                    "role": "FindingsWriter",
                    "real_fetched_urls": real_fetched_urls,
                    "unverifiable_claims": unverifiable,
                    "prompt": findings_writer_prompt,
                    "warning": None,
                    "escalated": escalated,
                    "source": "synthetic_scenario_findings_writer_dispatch",
                })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_topics = len({ex["topic"] for ex in examples})
    print(f"Generated {len(examples)} synthetic citation-grounding PROMPTS across {distinct_topics} "
          f"distinct topics (zero GPU cost — real check_not_grounded/real_grounding_problem code, "
          f"synthetic scenarios).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
