"""
Generates diverse stale_findings PROMPTS (not responses) at zero GPU cost, the stale_findings
counterpart to generate_synthetic_findings_evidence_prompts.py's own generator (see that file's
docstring for the shared "real check code, synthetic situation" discipline this one follows too).

Each scenario is a set of real, citable findings already delegated this run, with
`findings_written_citable_count` (the marker engine/completion.py sets right after every
successful FindingsWriter dispatch) recorded LOWER than the current citable-finding count -- the
live 2026-07-24 failure shape (see engine/completion.py::check_stale_findings's own docstring: a
`--resume-run` on a boiling-point-of-water query kept delegating after findings.md was already
written once, and nothing caught the resulting staleness). Fed through the REAL
check_stale_findings check -- zero fabrication in the check logic itself, only the situation
(which topics, how many findings arrived after the write) is synthetic.

Only scenarios where check_stale_findings actually fires produce a prompt -- a scenario where
`findings_written_citable_count` already matches (or exceeds) the current count makes the check
return None (nothing to correct), matching every other generator's own scenario-filtering
convention.

Usage:
  python finetune/generate_synthetic_stale_findings_prompts.py --out finetune/data/stale_findings_synthetic_prompts.jsonl
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config as app_config  # noqa: E402
from engine.completion import check_stale_findings, Ctx  # noqa: E402
from utils.run_state import RunState  # noqa: E402

# Each scenario: (topic, [(url, finding_text), ...] written BEFORE the marker was set,
#                 [(url, finding_text), ...] delegated AFTER -- these are what makes it stale).
# Disjoint topics from every other generator's own SCENARIOS list.
SCENARIOS = [
    ("boiling point of water at various altitudes", [
        ("https://www.usgs.gov/special-topics/water-science-school/science/water-properties-boiling-point",
         "Water boils at 100C (212F) at sea level under standard atmospheric pressure."),
    ], [
        ("https://www.mountaineers.org/blog/altitude-boiling-point-cooking",
         "At 3000m elevation, water boils at approximately 90C due to reduced atmospheric pressure, affecting cooking times."),
    ]),
    ("history of the printing press", [
        ("https://www.britannica.com/technology/printing-press",
         "Johannes Gutenberg introduced movable-type printing to Europe around 1440."),
    ], [
        ("https://www.bl.uk/history-of-printing/articles/gutenberg-bible",
         "The Gutenberg Bible, printed around 1455, is considered the first major book printed with movable type in the West."),
        ("https://www.smithsonianmag.com/history/printing-press-spread-europe",
         "Printing technology spread rapidly across Europe within 50 years of Gutenberg's press, reaching over 200 cities by 1500."),
    ]),
    ("caffeine metabolism in the human body", [
        ("https://www.fda.gov/consumers/consumer-updates/spilling-beans-how-much-caffeine-too-much",
         "The average half-life of caffeine in the human body is about 5 hours, though it varies significantly by individual."),
    ], [
        ("https://www.ncbi.nlm.nih.gov/pmc/articles/caffeine-cyp1a2-metabolism",
         "The CYP1A2 gene variant significantly affects caffeine metabolism speed, with slow metabolizers clearing caffeine much more gradually."),
    ]),
    ("the domestication of the horse", [
        ("https://www.science.org/doi/horse-domestication-origins",
         "Genetic evidence points to horse domestication originating in the Pontic-Caspian steppe around 3500 BCE."),
    ], [
        ("https://www.nature.com/articles/horse-domestication-genomics-2021",
         "A large-scale genomic study traced modern domestic horse lineages to a single population expansion around 2200 BCE."),
        ("https://www.archaeology.org/news/horse-bit-wear-evidence",
         "Bit-wear analysis on horse teeth from Botai culture sites provides some of the earliest physical evidence of horse riding."),
    ]),
    ("the physics of lightning formation", [
        ("https://www.noaa.gov/jetstream/lightning/lightning-formation",
         "Charge separation within storm clouds, driven by collisions between ice crystals and graupel, creates the electric field that produces lightning."),
    ], [
        ("https://www.nature.com/articles/lightning-initiation-mechanism",
         "Recent research using high-speed cameras has clarified how the initial 'leader' channel of a lightning strike actually forms and propagates."),
    ]),
    ("the construction of the Suez Canal", [
        ("https://www.britannica.com/topic/Suez-Canal",
         "The Suez Canal opened in 1869 after roughly 10 years of construction, connecting the Mediterranean and Red Seas."),
    ], [
        ("https://www.history.com/topics/suez-canal-construction-labor",
         "Construction relied heavily on forced Egyptian peasant labor (corvee) in its early years before being replaced by mechanized dredging equipment."),
        ("https://www.jstor.org/stable/suez-canal-financing",
         "French and Egyptian financing dominated the canal's early ownership structure before Britain acquired a controlling stake in 1875."),
    ]),
    ("the biology of octopus camouflage", [
        ("https://www.pnas.org/doi/cephalopod-skin-chromatophores",
         "Chromatophores are controlled directly by motor neurons, enabling near-instantaneous color change in octopuses."),
    ], [
        ("https://www.cell.com/current-biology/octopus-camouflage-learning",
         "Recent studies suggest octopuses may learn and reuse specific camouflage patterns matched to individual hiding spots."),
    ]),
    ("the causes of the Bronze Age collapse", [
        ("https://www.jstor.org/stable/bronze-age-collapse-overview",
         "The Bronze Age collapse around 1200 BCE affected multiple eastern Mediterranean civilizations within a few decades."),
    ], [
        ("https://www.cambridge.org/core/journal-sea-peoples-migration",
         "Migration pressure from the so-called 'Sea Peoples' is one of several proposed contributing factors to the collapse."),
        ("https://www.science.org/doi/bronze-age-drought-evidence",
         "Paleoclimate evidence indicates a prolonged drought across the eastern Mediterranean coincided with the collapse period."),
    ]),
]

HELD_OUT_SCENARIOS = [
    ("the mechanics of tsunami wave propagation", [
        ("https://www.noaa.gov/education/resource-collections/tsunamis",
         "Tsunami waves travel at speeds exceeding 500 mph in deep ocean water but slow dramatically as they approach shallow coastal areas."),
    ], [
        ("https://www.usgs.gov/programs/tsunami-early-warning",
         "Modern tsunami early-warning systems rely on a network of deep-ocean pressure sensors (DART buoys) to detect wave propagation in real time."),
    ]),
]


def build_scenario_ctx(pre_urls, post_urls, attempt: int, max_attempts: int, tmpdir: str) -> Ctx:
    rs = RunState(tmpdir)
    rs.set_query("synthetic stale-findings scenario")
    for url, text in pre_urls:
        rs.add_finding(url, text, task_name="background", depth=1)
    # Marker set at the point findings.md was (hypothetically) last written -- BEFORE the
    # post_urls findings arrived, mirroring the real incident's exact ordering.
    rs.data["findings_written_citable_count"] = len(pre_urls)
    for url, text in post_urls:
        rs.add_finding(url, text, task_name="background", depth=1)
    return Ctx(req_artifact="final_report.md", attempt=attempt, max_attempts=max_attempts,
               delegated=True, files=["findings.md"], content=None, quotas=None, run_state=rs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/stale_findings_synthetic_prompts.jsonl")
    parser.add_argument("--held-out", action="store_true",
                         help="Generate from HELD_OUT_SCENARIOS instead (topics never used in training)")
    args = parser.parse_args()
    scenarios = HELD_OUT_SCENARIOS if args.held_out else SCENARIOS
    if args.held_out and args.out == parser.get_default("out"):
        args.out = "finetune/data/stale_findings_heldout_prompts.jsonl"

    app_config.cfg.setdefault("settings", {})
    app_config.cfg["settings"].setdefault("stale_findings_check", {})

    examples = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for topic, pre_urls, post_urls in scenarios:
            for attempt, max_attempts, escalated in ((0, 8, False), (7, 8, True)):
                ctx = build_scenario_ctx(pre_urls, post_urls, attempt, max_attempts, tmpdir)
                verdict = check_stale_findings(ctx)
                if verdict is None:
                    continue  # scenario didn't actually flag staleness -- skip, don't fake it
                examples.append({
                    "topic": topic,
                    "prompt": verdict.inject,
                    "escalated": escalated,
                    "source": "synthetic_scenario_real_check_stale_findings",
                })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_topics = len({ex["topic"] for ex in examples})
    print(f"Generated {len(examples)} synthetic stale-findings PROMPTS across {distinct_topics} "
          f"distinct topics (zero GPU cost -- real check_stale_findings code, synthetic scenarios).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
