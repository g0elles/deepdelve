"""
Generates diverse uneven_task_investment PROMPTS (not responses) at zero GPU cost, the
check_uneven_task_investment counterpart to generate_synthetic_findings_evidence_prompts.py's own
generator (see that file's docstring for the shared "real check code, synthetic situation"
discipline this one follows too).

REUSES thin_coverage_response_reward as its scoring function -- deliberately does NOT add a new
reward function. Confirmed by reading both checks' source directly: check_uneven_task_investment's
directive language and correct/incorrect response shapes (materially-reworded re-delegation good,
near-duplicate bad, clean stop good, canned refusal/narrated-content bad) are structurally
identical to check_thin_coverage's -- only the TRIGGER differs (uneven counts across covered
tasks vs. zero-source tasks), not what a correct response to the resulting nudge looks like.
Writing a near-duplicate second reward function for this would be pure duplication for zero
behavioral difference.

Each scenario is a set of >=2 covered top-level tasks (one richly-sourced, one starved --
per-task real-source count below check_uneven_task_investment's own richest/starved ratio
threshold), fed through the REAL check. Requires BOTH findings.md and final_report.md in
ctx.files, per that check's own two-artifact gate (confirmed from source, see its own docstring
on the two live regressions that shipped this gate).

Usage:
  python finetune/generate_synthetic_uneven_investment_prompts.py --out finetune/data/uneven_investment_synthetic_prompts.jsonl
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config as app_config  # noqa: E402
from engine.completion import check_uneven_task_investment, Ctx  # noqa: E402
from utils.run_state import RunState  # noqa: E402

# Each scenario: (topic, richest_urls: list[str], starved_task_name, starved_url: str,
#                 starved_original_instructions: str). The richest task's own name doesn't need to
# be semantically meaningful (only distinct from starved_task_name), so it's a fixed generic
# "richest_task" across every scenario.
# Disjoint topics from every other generator's own SCENARIOS list. Richest task always gets 5
# sources, starved always gets 1 (ratio 0.2, below the 0.3 default threshold); total sources = 6,
# above the min_total_sources=4 gate.
SCENARIOS = [
    ("renewable energy storage vs grid-scale battery recycling", [
        "https://www.nrel.gov/docs/grid-scale-storage-overview",
        "https://www.iea.org/reports/battery-storage-deployment",
        "https://www.energy.gov/eere/grid-scale-storage-costs",
        "https://www.sciencedirect.com/science/article/lithium-ion-degradation",
        "https://www.nature.com/articles/flow-battery-scaling",
    ], "battery_recycling", "https://www.epa.gov/battery-recycling-overview",
       "Research current battery recycling infrastructure and recovery rates for grid-scale storage."),
    ("Mars rover geology findings vs Mars mission funding history", [
        "https://www.nasa.gov/perseverance-rock-samples",
        "https://www.jpl.nasa.gov/news/curiosity-clay-minerals",
        "https://www.science.org/doi/mars-sedimentary-layers",
        "https://www.nature.com/articles/jezero-crater-delta",
        "https://www.planetary.org/articles/mars-2020-geology",
    ], "mission_funding", "https://www.planetary.org/space-policy/mars-budget-history",
       "Research the funding history and budget trends for NASA's Mars exploration program."),
    ("global coffee supply chains vs coffee's health effects", [
        "https://www.ico.org/trade-statistics",
        "https://www.worldbank.org/en/topic/agriculture/coffee-trade",
        "https://www.fairtrade.net/product/coffee",
        "https://www.reuters.com/markets/commodities/coffee-supply-2025",
        "https://www.bloomberg.com/news/coffee-price-volatility",
    ], "health_effects", "https://www.health.harvard.edu/coffee-health-review",
       "Research the health effects of moderate coffee consumption on cardiovascular risk."),
    ("submarine cable internet infrastructure vs satellite internet competition", [
        "https://www.submarinecablemap.com/overview",
        "https://www.telegeography.com/products/submarine-cable-map",
        "https://www.itu.int/en/submarine-cable-report",
        "https://www.greenpeace.org/subsea-cable-environmental-impact",
        "https://www.ieee.org/subsea-cable-repair-logistics",
    ], "satellite_competition", "https://www.spacex.com/starlink-market-overview",
       "Research how satellite internet providers are competing with submarine cable infrastructure."),
    ("urban vertical farming economics vs traditional agriculture land use", [
        "https://www.agrilyst.com/vertical-farming-report",
        "https://www.forbes.com/vertical-farming-costs-2025",
        "https://www.sciencedirect.com/science/article/vertical-farm-energy",
        "https://www.nature.com/articles/vertical-farming-yield-comparison",
        "https://www.usda.gov/reports/controlled-environment-agriculture",
    ], "traditional_land_use", "https://www.fao.org/land-use-agriculture-overview",
       "Research land-use trends and efficiency metrics for traditional row-crop agriculture."),
    ("deep-sea mining regulations vs terrestrial rare-earth mining impact", [
        "https://www.isa.org.jm/deep-sea-mining-regulations",
        "https://www.pewtrusts.org/deep-sea-mining-policy",
        "https://www.nature.com/articles/polymetallic-nodule-ecology",
        "https://www.science.org/doi/seabed-mining-environmental-risk",
        "https://www.un.org/deep-sea-mining-treaty-status",
    ], "terrestrial_mining", "https://www.usgs.gov/rare-earth-mining-impact",
       "Research the environmental impact of terrestrial rare-earth element mining operations."),
    ("wildfire smoke health effects vs wildfire prevention technology", [
        "https://www.epa.gov/wildfire-smoke-health-effects",
        "https://www.cdc.gov/wildfire-smoke-respiratory-risk",
        "https://www.who.int/wildfire-smoke-air-quality",
        "https://www.nature.com/articles/wildfire-smoke-mortality",
        "https://www.thelancet.com/wildfire-smoke-cardiovascular",
    ], "prevention_technology", "https://www.fs.usda.gov/wildfire-prevention-tech",
       "Research emerging technologies used for early wildfire detection and prevention."),
    ("cryptocurrency mining energy use vs blockchain proof-of-stake adoption", [
        "https://www.cambridge.org/cbeci-mining-energy-index",
        "https://www.iea.org/reports/crypto-energy-consumption",
        "https://www.nature.com/articles/bitcoin-mining-carbon-footprint",
        "https://www.reuters.com/technology/crypto-mining-regulation",
        "https://www.bloomberg.com/news/mining-hardware-efficiency",
    ], "proof_of_stake_adoption", "https://www.ethereum.org/proof-of-stake-transition",
       "Research the adoption rate and energy savings of proof-of-stake blockchain networks."),
]

HELD_OUT_SCENARIOS = [
    ("offshore wind farm construction vs offshore wind bird-strike research", [
        "https://www.energy.gov/offshore-wind-construction-overview",
        "https://www.nrel.gov/offshore-wind-foundation-types",
        "https://www.iea.org/reports/offshore-wind-capacity-growth",
        "https://www.sciencedirect.com/science/article/offshore-turbine-installation",
        "https://www.nature.com/articles/offshore-wind-cost-trends",
    ], "bird_strike_research", "https://www.audubon.org/offshore-wind-bird-impact",
       "Research studies on bird-strike risk and mitigation at offshore wind installations."),
]


def build_scenario_ctx(richest_urls, starved_task, starved_url,
                        attempt: int, max_attempts: int, tmpdir: str) -> Ctx:
    richest_task = "richest_task"
    rs = RunState(tmpdir)
    rs.set_query("synthetic uneven-investment scenario")
    for i, url in enumerate(richest_urls):
        rs.add_finding(url, f"Real finding #{i+1} for {richest_task}.", task_name=richest_task, depth=1)
    rs.add_finding(starved_url, f"Real finding for {starved_task}.", task_name=starved_task, depth=1)
    return Ctx(req_artifact="final_report.md", attempt=attempt, max_attempts=max_attempts,
               delegated=True, files=["findings.md", "final_report.md"], content=None,
               quotas=None, run_state=rs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/uneven_investment_synthetic_prompts.jsonl")
    parser.add_argument("--held-out", action="store_true",
                         help="Generate from HELD_OUT_SCENARIOS instead (topics never used in training)")
    args = parser.parse_args()
    scenarios = HELD_OUT_SCENARIOS if args.held_out else SCENARIOS
    if args.held_out and args.out == parser.get_default("out"):
        args.out = "finetune/data/uneven_investment_heldout_prompts.jsonl"

    app_config.cfg.setdefault("settings", {})
    app_config.cfg["settings"].setdefault("uneven_coverage_check", {})

    examples = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for topic, richest_urls, starved_task, starved_url, starved_instructions in scenarios:
            for attempt, max_attempts, escalated in ((0, 8, False), (7, 8, True)):
                ctx = build_scenario_ctx(richest_urls, starved_task, starved_url,
                                          attempt, max_attempts, tmpdir)
                verdict = check_uneven_task_investment(ctx)
                if verdict is None:
                    continue  # scenario didn't actually flag unevenness -- skip, don't fake it
                examples.append({
                    "topic": topic,
                    "prior_task_instructions": [starved_instructions],
                    "prompt": verdict.inject,
                    "escalated": escalated,
                    "source": "synthetic_scenario_real_check_uneven_task_investment",
                })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_topics = len({ex["topic"] for ex in examples})
    print(f"Generated {len(examples)} synthetic uneven-investment PROMPTS across {distinct_topics} "
          f"distinct topics (zero GPU cost -- real check_uneven_task_investment code, synthetic "
          f"scenarios).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
