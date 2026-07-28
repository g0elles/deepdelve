"""
Generates diverse findings_underuses_evidence PROMPTS (not responses) at zero GPU cost, the
findings_underuses_evidence counterpart to generate_synthetic_citation_prompts.py's
citation-grounding scenarios (see that file's own docstring for the underlying GRPO-needs-
PROMPT-diversity principle).

Each scenario is a set of top-level delegated tasks, each with real fetched URLs, fed through the
REAL engine/completion.py::check_findings_underuses_evidence check against a "current" findings.md
draft that's missing one whole task's entries entirely -- the live 2026-07-26 failure shape (see
that check's own docstring: a balanced 2-facet green-tea/Roman-Empire run whose FindingsWriter
draft silently dropped an entire covered task). Zero fabrication in the check logic itself, only
the SITUATION (which topics, which task got dropped) is synthetic.

Only scenarios where check_findings_underuses_evidence actually fires produce a prompt -- a
scenario where every task is already represented makes the check return None (nothing to correct),
matching citation_grounding's own "if verdict is None: continue" convention.

Usage:
  python finetune/generate_synthetic_findings_evidence_prompts.py --out finetune/data/findings_evidence_synthetic_prompts.jsonl
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config as app_config  # noqa: E402
from engine.completion import check_findings_underuses_evidence, Ctx  # noqa: E402
from tools.fs import _IN_MEMORY_FS  # noqa: E402
from utils.run_state import RunState  # noqa: E402

# Each scenario: (topic label, {task_name: [(real_url, filename, one-line finding text), ...]}).
# Disjoint topics from the other two synthetic-prompt scripts' SCENARIOS lists. Every task has at
# least one real URL -- an uncovered task is check_thin_coverage's job, not this check's.
SCENARIOS = [
    ("green tea health benefits vs Roman Empire economy", {
        "green_tea_health": [
            ("https://www.ncbi.nlm.nih.gov/pmc/green-tea-catechins", "ncbi_green_tea_catechins.md",
             "Green tea catechins are associated with modest reductions in LDL cholesterol across several controlled trials."),
            ("https://www.hsph.harvard.edu/nutritionsource/green-tea", "harvard_green_tea.md",
             "Regular green tea consumption correlates with a small reduction in cardiovascular event risk in cohort studies."),
        ],
        "roman_empire_economy": [
            ("https://www.jstor.org/stable/roman-grain-trade", "jstor_roman_grain_trade.md",
             "The Roman grain trade relied on a state-subsidized shipping fleet (the annona) to supply Rome's population."),
            ("https://www.cambridge.org/core/roman-monetary-system", "cambridge_roman_monetary.md",
             "Debasement of the silver denarius accelerated markedly during the third-century crisis."),
        ],
    }),
    ("octopus cognition vs medieval siege warfare", {
        "octopus_cognition": [
            ("https://www.pnas.org/doi/octopus-problem-solving", "pnas_octopus_problem_solving.md",
             "Octopuses solve novel latch-and-lid puzzle boxes through trial-and-error without prior demonstration."),
        ],
        "medieval_siege_warfare": [
            ("https://www.jstor.org/stable/trebuchet-mechanics", "jstor_trebuchet_mechanics.md",
             "Counterweight trebuchets could exceed the range and payload of earlier traction trebuchets by a wide margin."),
            ("https://www.cambridge.org/core/medieval-siege-logistics", "cambridge_siege_logistics.md",
             "Prolonged sieges depended more on supply-line disruption than on direct wall breaching in most documented cases."),
        ],
    }),
    ("exoplanet detection methods vs Antarctic ice core climate records", {
        "exoplanet_detection": [
            ("https://www.nasa.gov/kepler-transit-method", "nasa_kepler_transit.md",
             "The transit method detects exoplanets via periodic dimming as a planet crosses its host star's disk."),
            ("https://www.eso.org/radial-velocity-survey", "eso_radial_velocity.md",
             "Radial velocity surveys measure stellar wobble caused by an orbiting planet's gravitational pull."),
        ],
        "antarctic_ice_cores": [
            ("https://climate.nasa.gov/evidence/ice-core-co2-record", "nasa_ice_core_co2.md",
             "Trapped air bubbles in Antarctic ice cores provide a direct atmospheric CO2 record extending back roughly 800,000 years."),
        ],
    }),
    ("honeybee colony collapse vs ancient Egyptian mummification chemistry", {
        "honeybee_ccd": [
            ("https://www.usda.gov/reports/colony-collapse-2025", "usda_colony_collapse.md",
             "Annual survey data show managed colony losses remain elevated compared to pre-2006 baseline rates."),
        ],
        "egyptian_mummification": [
            ("https://www.nature.com/articles/mummification-resin-analysis", "nature_mummification_resin.md",
             "Chemical analysis of embalming residues identifies a consistent multi-ingredient resin recipe across several dynastic periods."),
            ("https://www.jstor.org/stable/canopic-jar-contents", "jstor_canopic_jar.md",
             "Canopic jar residues indicate organs were treated with the same resin blend used on the body itself."),
        ],
    }),
    ("dark matter direct detection vs migratory bird magnetoreception", {
        "dark_matter_detection": [
            ("https://www.symmetrymagazine.org/xenon-nt-results-2025", "symmetry_xenon_nt.md",
             "The XENONnT experiment set a new exclusion limit on WIMP-nucleon cross-sections at its latest exposure."),
        ],
        "bird_magnetoreception": [
            ("https://www.nature.com/articles/cryptochrome-magnetoreception", "nature_cryptochrome.md",
             "Cryptochrome proteins in the retina are proposed to sense magnetic field orientation via a radical-pair quantum mechanism."),
            ("https://www.sciencedirect.com/science/article/robin-navigation", "sciencedirect_robin_navigation.md",
             "Field studies show migratory robins reorient correctly under artificial magnetic field manipulation."),
        ],
    }),
]

# Held out entirely -- for evaluate_combined.py-style base-vs-fine-tuned comparison on unseen topics.
HELD_OUT_SCENARIOS = [
    ("bioluminescent deep-sea fish vs trans-Saharan gold trade", {
        "bioluminescent_fish": [
            ("https://www.mbari.org/research/bioluminescence-counter-illumination", "mbari_counter_illumination.md",
             "Many mesopelagic fish use ventral photophores to match downwelling light and erase their silhouette from predators below."),
        ],
        "trans_saharan_gold_trade": [
            ("https://www.jstor.org/stable/ghana-empire-gold-routes", "jstor_ghana_gold_routes.md",
             "Archaeological trade-good distribution supports a well-established caravan route linking Ghana Empire gold fields to North African markets."),
            ("https://www.cambridge.org/core/journal-medieval-african-trade", "cambridge_salt_gold_exchange.md",
             "Contemporary Arab traveler accounts document consistent salt-for-gold exchange ratios across major trade centers."),
        ],
    }),
]


def _findings_block(tasks: dict) -> str:
    """Mirrors _build_findings_source_material's real shape (src/engine/completion.py) -- one
    ### Source entry per finding, grouped implicitly by task via the entries' own content."""
    entries = []
    for _task_name, urls in tasks.items():
        for url, filename, text in urls:
            entries.append(f"### Source: {url}\n{text} (saved as {filename})")
    return (
        "REAL RESEARCH RESULTS FROM THIS RUN, one entry per dispatched task (this is your ENTIRE "
        "evidence base -- you have no other memory of this run):\n\n" + "\n\n".join(entries)
    )


def _dropped_findings_md(tasks: dict, dropped_task: str) -> str:
    """The 'current' findings.md draft the check runs against -- every task EXCEPT dropped_task
    gets a bullet citing its real URL, matching the live incident's shape (a plausible-looking,
    mostly-correct draft that silently omits one whole task rather than a wholesale fabrication)."""
    lines = []
    for task_name, urls in tasks.items():
        if task_name == dropped_task:
            continue
        for url, _filename, text in urls:
            lines.append(f"- {text} [source]({url})")
    return "\n".join(lines) or "(placeholder)"


def build_scenario_ctx(tasks: dict, dropped_task: str, attempt: int, max_attempts: int, tmpdir: str) -> Ctx:
    rs = RunState(tmpdir)
    rs.set_query("synthetic findings-evidence scenario")
    for task_name, urls in tasks.items():
        for url, _filename, text in urls:
            rs.add_finding(url, text, task_name=task_name, depth=1)
    _IN_MEMORY_FS["findings.md"] = _dropped_findings_md(tasks, dropped_task)
    return Ctx(req_artifact="final_report.md", attempt=attempt, max_attempts=max_attempts,
               delegated=True, files=["findings.md"], content=None, quotas=None, run_state=rs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/findings_evidence_synthetic_prompts.jsonl")
    parser.add_argument("--held-out", action="store_true",
                         help="Generate from HELD_OUT_SCENARIOS instead (topics never used in training)")
    args = parser.parse_args()
    scenarios = HELD_OUT_SCENARIOS if args.held_out else SCENARIOS
    if args.held_out and args.out == parser.get_default("out"):
        args.out = "finetune/data/findings_evidence_heldout_prompts.jsonl"

    app_config.cfg.setdefault("settings", {})
    app_config.cfg["settings"].setdefault("findings_evidence_check", {})
    orig_workspace = app_config.cfg["settings"].get("workspace")
    app_config.cfg["settings"]["workspace"] = {"type": "memory"}

    examples = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for topic, tasks in scenarios:
                for dropped_task in tasks:
                    for attempt, max_attempts, escalated in ((0, 8, False), (7, 8, True)):
                        _IN_MEMORY_FS.clear()
                        ctx = build_scenario_ctx(tasks, dropped_task, attempt, max_attempts, tmpdir)
                        verdict = check_findings_underuses_evidence(ctx)
                        if verdict is None:
                            continue  # didn't actually flag the dropped task -- skip, don't fake it
                        # Real FindingsWriter rebuild-dispatch shape, same template
                        # engine/completion.py's _dispatch_writer_review_fix actually sends.
                        findings_writer_prompt = (
                            f"{verdict.inject}\n\n{_findings_block(tasks)}\n\n"
                            f"Write the corrected 'findings.md' now via write_workspace_file."
                        )
                        per_task_urls = {name: [u for u, _f, _t in urls] for name, urls in tasks.items()}
                        examples.append({
                            "topic": topic,
                            "role": "FindingsWriter",
                            "dropped_task": dropped_task,
                            "per_task_urls": per_task_urls,
                            "prompt": findings_writer_prompt,
                            "escalated": escalated,
                            "source": "synthetic_scenario_real_check_findings_underuses_evidence",
                        })
    finally:
        _IN_MEMORY_FS.clear()
        if orig_workspace is None:
            app_config.cfg["settings"].pop("workspace", None)
        else:
            app_config.cfg["settings"]["workspace"] = orig_workspace

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_topics = len({ex["topic"] for ex in examples})
    print(f"Generated {len(examples)} synthetic findings-evidence PROMPTS across {distinct_topics} "
          f"distinct topics (zero GPU cost -- real check_findings_underuses_evidence code, "
          f"synthetic scenarios).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
