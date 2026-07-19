"""
Generates diverse `thin_coverage` PROMPTS (not responses) at zero GPU cost, for GRPO training —
distinct from extract_dataset.py's real-response mining, and never to be confused with it.

Key realization this project's own live pilot batch made concrete (2026-07-18): GRPO doesn't need
(prompt, correct_response) pairs the way SFT does. At training time the model generates its OWN
completions for a given prompt, and the reward function (finetune/reward.py) scores whatever comes
out — nothing here is a training TARGET. So the real bottleneck for `thin_coverage` isn't response
diversity (which needs a live model run to produce), it's PROMPT diversity — and a `thin_coverage`
prompt is 100% deterministic: it's whatever `engine/completion.py::check_thin_coverage` computes
from a `RunState`'s recorded findings. That function is real production code, not something to
reimplement or guess at — so this script builds varied but realistic `RunState` scenarios (many
topics, task counts, coverage ratios) and calls the REAL function directly, capturing its REAL
`Verdict.inject` text as the prompt. Zero fabrication: every prompt produced here is exactly what
DeepDelve's own engine would show a live model in the equivalent real scenario. What's synthetic is
only the SITUATION (which topics, which tasks happened to lack sources) — not the nudge-generation
logic itself, which stays real and unmodified.

Output is kept in a clearly separate file/format from extract_dataset.py's real-mined examples
(finetune/data/thin_coverage_synthetic_prompts.jsonl vs. thin_coverage.jsonl) specifically so a
real extracted example and a synthetic prompt-only scenario are never accidentally treated as the
same kind of data downstream.

Usage:
  python finetune/generate_synthetic_prompts.py --out finetune/data/thin_coverage_synthetic_prompts.jsonl
"""

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import config as app_config  # noqa: E402
from engine.completion import check_thin_coverage, Ctx  # noqa: E402
from utils.run_state import RunState  # noqa: E402

# Deliberately diverse across domains this project's own real run history has never touched
# (science, medicine, law, economics, history, technology) — each scenario is (topic label,
# [(task_name, covered_bool), ...]). `covered_bool=True` gets a real-looking source_url finding;
# False gets only the task_name fallback (RunState.coverage()'s own definition of "uncovered").
SCENARIOS = [
    ("CRISPR gene-editing regulatory status", [
        ("FDA approval status for CRISPR-based therapies", True),
        ("EU regulatory framework for gene-edited crops", False),
        ("China's clinical trial approvals for CRISPR therapies", False),
    ]),
    ("quantum error correction progress", [
        ("Surface code error rates in current superconducting qubits", True),
        ("Topological qubit experimental progress (Microsoft/Station Q)", False),
        ("Neutral-atom quantum error correction progress", False),
    ]),
    ("international space law for asteroid mining", [
        ("Outer Space Treaty interpretation for resource extraction", True),
        ("US Commercial Space Launch Competitiveness Act provisions", False),
        ("Luxembourg space resources law", False),
    ]),
    ("deep-sea mining environmental impact", [
        ("International Seabed Authority regulatory status", True),
        ("Polymetallic nodule extraction environmental studies", False),
        ("Deep-sea ecosystem biodiversity baseline studies", False),
    ]),
    ("central bank digital currency rollout", [
        ("China's e-CNY pilot program status", True),
        ("ECB digital euro timeline", False),
        ("Nigeria's eNaira adoption metrics", False),
    ]),
    ("permafrost carbon feedback loop research", [
        ("Siberian permafrost methane release measurements", True),
        ("Arctic carbon budget modeling updates", False),
        ("Permafrost thaw infrastructure risk assessments", False),
    ]),
    ("autonomous vehicle liability law", [
        ("US state-level AV liability frameworks", True),
        ("EU AI Act provisions for autonomous vehicles", False),
        ("Germany's autonomous driving law amendments", False),
    ]),
    ("mRNA vaccine platform for cancer immunotherapy", [
        ("Personalized cancer vaccine clinical trial results", True),
        ("Universal flu vaccine mRNA platform status", False),
        ("mRNA platform manufacturing cost trends", False),
    ]),
    ("desalination technology cost trends", [
        ("Reverse osmosis energy efficiency improvements", True),
        ("Solar-powered desalination pilot projects", False),
        ("Brine disposal environmental regulations", False),
    ]),
    ("historical consensus on Bronze Age collapse causes", [
        ("Sea Peoples invasion archaeological evidence", True),
        ("Climate-driven drought hypothesis evidence", False),
        ("Systems collapse theory (trade network breakdown) evidence", False),
    ]),
    ("solid-state battery commercialization timeline", [
        ("Toyota solid-state battery production timeline", True),
        ("QuantumScape pilot line status", False),
        ("Sodium-ion battery commercialization progress", False),
    ]),
    ("gut microbiome and neurological disease links", [
        ("Gut-brain axis research in Parkinson's disease", True),
        ("Microbiome links to Alzheimer's disease progression", False),
        ("Microbiome-targeted therapeutics clinical trial status", False),
    ]),
    ("global supply chain reshoring trends", [
        ("US CHIPS Act semiconductor reshoring progress", True),
        ("EU Critical Raw Materials Act implementation", False),
        ("Japan's economic security supply chain policy", False),
    ]),
    ("fusion energy net-gain milestones", [
        ("NIF inertial confinement fusion ignition results", True),
        ("Commonwealth Fusion Systems SPARC tokamak progress", False),
        ("Helion Energy pulsed fusion progress", False),
    ]),
    ("coral reef restoration technology effectiveness", [
        ("Coral gardening/transplantation success rates", True),
        ("Assisted evolution/heat-tolerant coral breeding programs", False),
        ("3D-printed reef structure deployments", False),
    ]),
    ("universal basic income pilot program outcomes", [
        ("Finland's UBI pilot program results", True),
        ("Kenya's GiveDirectly long-term UBI study outcomes", False),
        ("Stockton SEED UBI pilot outcomes", False),
    ]),
    ("lab-grown meat regulatory approval status", [
        ("US FDA/USDA cultivated meat approval status", True),
        ("Singapore cultivated meat market approvals", False),
        ("EU novel food regulation status for cultivated meat", False),
    ]),
    ("methane emissions satellite monitoring technology", [
        ("MethaneSAT satellite monitoring capabilities", True),
        ("Oil and gas methane leak detection regulatory requirements", False),
        ("Agricultural methane emission satellite tracking", False),
    ]),
    ("brain-computer interface clinical trial progress", [
        ("Neuralink human trial results and status", True),
        ("Synchron BCI clinical trial progress", False),
        ("Blackrock Neurotech clinical deployments", False),
    ]),
    ("vertical farming economic viability", [
        ("AeroFarms/Plenty vertical farming unit economics", True),
        ("Energy cost comparisons vertical vs traditional farming", False),
        ("Vertical farming crop yield studies beyond leafy greens", False),
    ]),
    ("perovskite solar cell commercialization", [
        ("Oxford PV tandem perovskite-silicon cell efficiency records", True),
        ("Perovskite cell long-term stability/degradation studies", False),
        ("Lead-free perovskite alternative chemistries progress", False),
    ]),
    ("wastewater-based epidemiology surveillance", [
        ("CDC wastewater surveillance network coverage", True),
        ("Wastewater monitoring for antibiotic resistance genes", False),
        ("EU wastewater surveillance directive implementation", False),
    ]),
    ("direct air capture cost and scale progress", [
        ("Climeworks Mammoth plant capacity and cost per ton", True),
        ("US DAC hub funding and construction timelines", False),
        ("Direct air capture energy source requirements debate", False),
    ]),
    ("antibiotic resistance new drug pipeline", [
        ("WHO priority pathogen antibiotic pipeline status", True),
        ("Bacteriophage therapy clinical trial progress", False),
        ("Novel antibiotic classes in Phase 3 trials", False),
    ]),
    ("satellite mega-constellation space debris risk", [
        ("Starlink collision avoidance maneuver statistics", True),
        ("Kessler syndrome risk modeling updates", False),
        ("Active debris removal mission status (ClearSpace, Astroscale)", False),
    ]),
    ("green hydrogen production cost trends", [
        ("Electrolyzer cost-per-kilowatt trend data", True),
        ("Green hydrogen production tax credit policy status (US/EU)", False),
        ("Hydrogen transport/storage infrastructure buildout", False),
    ]),
    ("long COVID biomarker research progress", [
        ("Persistent viral reservoir hypothesis evidence", True),
        ("Long COVID biomarker panel validation studies", False),
        ("Long COVID treatment clinical trial results", False),
    ]),
    ("AI copyright litigation legal status", [
        ("US AI training-data fair-use court rulings", True),
        ("EU AI Act copyright/text-and-data-mining provisions", False),
        ("Getty Images v. Stability AI case status", False),
    ]),
    ("offshore wind floating turbine technology", [
        ("Floating offshore wind pilot project capacity factors", True),
        ("Floating turbine mooring system cost trends", False),
        ("US West Coast floating wind lease auction status", False),
    ]),
    ("synthetic biology biosecurity governance", [
        ("DNA synthesis screening requirement policies", True),
        ("Gain-of-function research moratorium status", False),
        ("International biosecurity treaty negotiation status", False),
    ]),
    ("lithium extraction from geothermal brine", [
        ("Salton Sea direct lithium extraction project status", True),
        ("Direct lithium extraction technology cost comparisons", False),
        ("Geothermal lithium environmental impact studies", False),
    ]),
    ("AI chip export control policy", [
        ("US semiconductor export control rule updates", True),
        ("China domestic AI chip production capability status", False),
        ("Allied nations' export control alignment (Netherlands, Japan)", False),
    ]),
    ("plastic-eating enzyme recycling technology", [
        ("Carbios PET-degrading enzyme commercial scale-up", True),
        ("Novel plastic-eating enzyme discovery research", False),
        ("Enzymatic recycling cost vs. mechanical recycling", False),
    ]),
    ("mental health chatbot clinical efficacy", [
        ("Woebot/Wysa randomized controlled trial results", True),
        ("FDA regulatory pathway for AI mental health tools", False),
        ("AI chatbot crisis-detection safety incident reports", False),
    ]),
    ("nuclear small modular reactor deployment", [
        ("NuScale/Kairos Power SMR licensing status", True),
        ("SMR project cost overrun/cancellation history", False),
        ("China/Russia SMR deployment timelines", False),
    ]),
    ("gut virome (phage) research progress", [
        ("Human gut bacteriophage community composition studies", True),
        ("Phage therapy for gut dysbiosis clinical progress", False),
        ("Virome links to inflammatory bowel disease research", False),
    ]),
    ("space-based solar power feasibility", [
        ("Caltech space solar power demonstration mission results", True),
        ("Space solar power cost-per-kilowatt-hour projections", False),
        ("Wireless power transmission efficiency at scale studies", False),
    ]),
    ("algorithmic pricing antitrust enforcement", [
        ("US DOJ algorithmic pricing collusion case status", True),
        ("EU Digital Markets Act algorithmic pricing provisions", False),
        ("Rental market algorithmic pricing lawsuit outcomes", False),
    ]),
    ("permafrost infrastructure engineering adaptation", [
        ("Arctic pipeline thermosyphon cooling technology use", True),
        ("Permafrost foundation engineering standard updates", False),
        ("Trans-Alaska pipeline permafrost monitoring data", False),
    ]),
]


def build_scenario_ctx(tasks: list[tuple[str, bool]], attempt: int, max_attempts: int, tmpdir: str) -> tuple[Ctx, RunState]:
    rs = RunState(tmpdir)
    rs.set_query("synthetic scenario")
    for i, (task_name, covered) in enumerate(tasks):
        if covered:
            rs.add_finding(f"https://example.org/real-source-{i}", f"Real finding for {task_name}",
                            task_name=task_name, depth=1)
        else:
            rs.add_finding(task_name, "", task_name=task_name, depth=1)
    ctx = Ctx(
        req_artifact="findings.md", attempt=attempt, max_attempts=max_attempts,
        delegated=True, files=[], content=None, quotas=None, run_state=rs,
    )
    return ctx, rs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/thin_coverage_synthetic_prompts.jsonl")
    args = parser.parse_args()

    app_config.cfg.setdefault("settings", {})
    app_config.cfg["settings"].setdefault("coverage_check", {})

    examples = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for topic, tasks in SCENARIOS:
            uncovered = [t for t, covered in tasks if not covered]
            # First occurrence (prior_same == 0): the real re-delegate-or-acknowledge nudge.
            ctx, _ = build_scenario_ctx(tasks, attempt=0, max_attempts=8, tmpdir=tmpdir)
            verdict = check_thin_coverage(ctx)
            if verdict is None:
                continue  # this scenario's ratio/min_tasks didn't actually trip the check — skip
            examples.append({
                "topic": topic,
                "prior_task_instructions": uncovered,
                "prompt": verdict.inject,
                "warning": verdict.warning,
                "escalated": False,
                "source": "synthetic_scenario_real_check_thin_coverage",
            })

            # Escalated occurrence (prior_same >= 1): the "acknowledge the gap" variant — needs a
            # real prior completion_check_attempts entry recorded first, same as a live run would.
            ctx2, rs2 = build_scenario_ctx(tasks, attempt=1, max_attempts=8, tmpdir=tmpdir)
            rs2.record_attempt(0, "thin_coverage", 0)
            verdict2 = check_thin_coverage(ctx2)
            if verdict2 is not None:
                examples.append({
                    "topic": topic,
                    "prior_task_instructions": uncovered,
                    "prompt": verdict2.inject,
                    "warning": verdict2.warning,
                    "escalated": True,
                    "source": "synthetic_scenario_real_check_thin_coverage",
                })

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_topics = len({ex["topic"] for ex in examples})
    print(f"Generated {len(examples)} synthetic thin_coverage PROMPTS across {distinct_topics} "
          f"distinct topics (zero GPU cost — real check_thin_coverage code, synthetic scenarios).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
