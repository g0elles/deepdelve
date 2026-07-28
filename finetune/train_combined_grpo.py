"""
Combined multi-objective GRPO fine-tune of qwen3:4b (base: Qwen/Qwen3-4B) — trains
thin_coverage_response_reward, citation_grounding_response_reward, writer_role_response_reward,
AND findings_underuses_evidence_response_reward in ONE pass, off ONE LoRA, from raw base.
Replaces the two prior single-dimension rounds (train_thin_coverage_grpo.py,
train_citation_grounding_grpo.py), which were each trained independently off raw Qwen/Qwen3-4B
rather than stacked or combined.

findings_underuses_evidence_response_reward added 2026-07-27 (see session_status/CURRENT.md):
its own reward function and check (engine/completion.py::check_findings_underuses_evidence,
shipped 2026-07-26 after a live incident dropped an entire delegated task's evidence from
findings.md) existed with no training data or wiring here. Uses
finetune/generate_synthetic_findings_evidence_prompts.py's synthetic FindingsWriter-rebuild
scenarios (finetune/data/findings_evidence_synthetic_prompts.jsonl) — same real-check-driven,
zero-fabrication generation discipline as the other two dimensions' own synthetic scripts. Scored
alongside writer_role_response_reward (0.5/0.5) exactly like citation_grounding's FindingsWriter
rows, since both dimensions share the same write_workspace_file tool surface.

writer_role_response_reward added 2026-07-21 (see session_status/CURRENT.md, "pending item
close-out"): its own reward function existed in reward.py and was exercised by
extract_dataset.py's real-run sanity check (finetune/data/writer_role.jsonl, 62 extracted
examples), but had never actually been trained into any round. Rather than mint a new synthetic
prompt set (writer_role.jsonl's rows are fixed historical completions, not GRPO-usable prompts,
and its own failure mode -- narrating an artifact as chat text instead of calling
write_workspace_file -- is already the exact same shape citation_grounding's 80 Builder/
FindingsWriter-role prompts exercise on the same tool), it's composed directly onto those SAME
rows: each citation_grounding-tagged row is now scored by both rubrics on the one completion it
produces (0.5 content-grounding + 0.5 "did you actually call the tool"), no new data fabricated.
thin_coverage rows are untouched -- that objective (re-delegation quality) has no writer-role
analogue.

Why this exists (2026-07-19 session): the citation-grounding-only LoRA was live-benchmarked
against the SAME query/orchestrator as the thin_coverage-only LoRA and scored WORSE on grounding
rate (0/8 grounded, 0%) than the thin_coverage-only model it was meant to improve on (8/19
grounded, 42%) — because it had zero protection against thin_coverage's own failure (only 4
sources fetched, 2 of them stubs, for a 4-6 angle query), a scarcity regime its own training
scenarios never covered. Two isolated single-purpose LoRAs trained from the same raw base are NOT
additive and are not both deployable as one model — this script fixes that by training one shared
model against both objectives together, so the model can't "forget" one behavior while doing well
on the other, and so a real run's actual failure mode (which is usually BOTH at once, not one in
isolation) is represented at training time. See session_status/CURRENT.md for the full incident.

Training data (7 task_types, ~301 rows total, comprehensive round prepped 2026-07-27):
  - thin_coverage_synthetic_prompts.jsonl (78, Planner/delegate_tasks-shaped)
  - citation_grounding_synthetic_prompts.jsonl (80: 40 Builder + 40 FindingsWriter, write_workspace_file-shaped)
  - findings_evidence_synthetic_prompts.jsonl (20, FindingsWriter-shaped)
  - tool_name_synthetic_prompts.jsonl (80, 8 role-shapes x 10 topics, each row's own real per-role tool schema)
  - stale_findings_synthetic_prompts.jsonl (16, Planner/delegate_tasks-shaped)
  - uneven_investment_synthetic_prompts.jsonl (16, Planner/delegate_tasks-shaped, reuses thin_coverage_response_reward)
  - task_verification_flagged_synthetic_prompts.jsonl (12, Planner/delegate_tasks-shaped, reuses thin_coverage_response_reward)
Each row tagged with its own task_type. Each row's prompt is built with ONLY the tool(s) its own
real role actually has, matching the real per-role toolset each dispatch sees live, not a merged
tool list. A reward router (combined_reward_fn) dispatches each completion to the correct scorer
by its row's task_type -- 5 distinct reward functions across 7 task_types (uneven_task_investment
and task_verification_flagged deliberately reuse thin_coverage_response_reward outright, see each
generator's own docstring for why no new reward function was written for either).

Usage:
  python finetune/train_combined_grpo.py --max-steps 260 --out-dir /mnt/nuevovol/llm-models/qwen3-4b-combined-lora
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import (  # noqa: E402
    thin_coverage_response_reward, citation_grounding_response_reward,
    writer_role_response_reward, findings_underuses_evidence_response_reward,
    real_tool_name_reward, stale_findings_response_reward, _tool_args,
)
from train_thin_coverage_grpo import DELEGATE_TASKS_TOOL  # noqa: E402
from train_citation_grounding_grpo import WRITE_WORKSPACE_FILE_TOOL  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")
# Real OOM crash, 2026-07-27, step 5/260: 11.94GiB allocated + 1.38GiB reserved-but-unallocated
# (fragmentation) left only 1.52GiB free when the backward pass needed 1.59GiB more, on this
# card's ~15.92GiB visible capacity. PyTorch's own OOM message names this exact mitigation.
os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from trl import GRPOConfig, GRPOTrainer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
THIN_COVERAGE_DATA = os.path.join(_DATA_DIR, "thin_coverage_synthetic_prompts.jsonl")
CITATION_GROUNDING_DATA = os.path.join(_DATA_DIR, "citation_grounding_synthetic_prompts.jsonl")
FINDINGS_EVIDENCE_DATA = os.path.join(_DATA_DIR, "findings_evidence_synthetic_prompts.jsonl")
TOOL_NAME_DATA = os.path.join(_DATA_DIR, "tool_name_synthetic_prompts.jsonl")
STALE_FINDINGS_DATA = os.path.join(_DATA_DIR, "stale_findings_synthetic_prompts.jsonl")
UNEVEN_INVESTMENT_DATA = os.path.join(_DATA_DIR, "uneven_investment_synthetic_prompts.jsonl")
TASK_VERIFICATION_DATA = os.path.join(_DATA_DIR, "task_verification_flagged_synthetic_prompts.jsonl")

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_completion(text: str) -> tuple[dict | None, str]:
    """Same convention both single-dimension scripts already used (Qwen3's real
    <tool_call>{...}</tool_call> output) — kept here rather than re-importing from either script
    since both would work identically; one shared copy avoids ambiguity about which is canonical."""
    m = _TOOL_CALL_RE.search(text)
    if not m:
        return None, text.strip()
    try:
        return json.loads(m.group(1)), ""
    except json.JSONDecodeError:
        return None, text.strip()


def load_dataset(tokenizer) -> Dataset:
    rows = []
    with open(THIN_COVERAGE_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[DELEGATE_TASKS_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "thin_coverage",
                "prior_task_instructions": ex["prior_task_instructions"],
                "real_fetched_urls": [],
                "per_task_urls": {},
                "role": None,
            })
    with open(CITATION_GROUNDING_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # Both roles (Builder rewriting final_report.md, FindingsWriter writing findings.md
            # fresh) share the write_workspace_file tool and citation_grounding_response_reward
            # scoring — only the prompt TEXT differs per ex["role"], already baked in by
            # generate_synthetic_citation_prompts.py. Role gap closed 2026-07-19: FindingsWriter
            # previously had zero training examples despite being just as able to cite an
            # unverified source into findings.md as Builder is into final_report.md.
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[WRITE_WORKSPACE_FILE_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "citation_grounding",
                "prior_task_instructions": [],
                "real_fetched_urls": ex["real_fetched_urls"],
                "per_task_urls": {},
                "role": None,
            })
    with open(FINDINGS_EVIDENCE_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # FindingsWriter dispatch, same tool/scoring surface as citation_grounding's
            # FindingsWriter rows -- writer_role_response_reward still gates "did it call
            # write_workspace_file at all" before findings_underuses_evidence_response_reward
            # judges whether every real task made it into the rewritten content.
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[WRITE_WORKSPACE_FILE_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "findings_evidence",
                "prior_task_instructions": [],
                "real_fetched_urls": [],
                "per_task_urls": ex["per_task_urls"],
                "role": None,
            })
    with open(TOOL_NAME_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # Each row carries its OWN real per-role tool schema (ex["tools"]) rather than one
            # fixed constant shared across the whole task_type -- unlike every other branch here,
            # the exposed toolset itself varies row-to-row (Planner/Builder/FindingsWriter/
            # PeerReviewer/4 role=None fallback shapes), so it's read directly from the data
            # generate_synthetic_tool_name_prompts.py already built, never redeclared here.
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=ex["tools"], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "tool_name",
                "prior_task_instructions": [],
                "real_fetched_urls": [],
                "per_task_urls": {},
                "role": ex["role"],
            })
    with open(STALE_FINDINGS_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # Planner-shaped, same delegate_tasks tool surface as thin_coverage -- continuing to
            # delegate is a valid response here too (see stale_findings_response_reward), only
            # narrating/refusing/stalling is penalized.
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[DELEGATE_TASKS_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "stale_findings",
                "prior_task_instructions": [],
                "real_fetched_urls": [],
                "per_task_urls": {},
                "role": None,
            })
    with open(UNEVEN_INVESTMENT_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # Reuses thin_coverage_response_reward outright (see
            # generate_synthetic_uneven_investment_prompts.py's own docstring for why no new
            # reward function was written) -- same delegate_tasks tool surface and
            # prior_task_instructions shape as thin_coverage rows, just a distinct task_type tag
            # so per-dimension counts stay separately reportable.
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[DELEGATE_TASKS_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "uneven_task_investment",
                "prior_task_instructions": ex["prior_task_instructions"],
                "real_fetched_urls": [],
                "per_task_urls": {},
                "role": None,
            })
    with open(TASK_VERIFICATION_DATA, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            # Also reuses thin_coverage_response_reward outright (see
            # engine/completion.py::check_task_verification_flagged's directive branches --
            # confirmed structurally identical to check_thin_coverage's own correct/incorrect
            # response shapes, including today's 2026-07-27 quota-aware "stop, don't redelegate"
            # branch).
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[DELEGATE_TASKS_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                "task_type": "task_verification_flagged",
                "prior_task_instructions": ex["prior_task_instructions"],
                "real_fetched_urls": [],
                "per_task_urls": {},
                "role": None,
            })
    return Dataset.from_list(rows)


def combined_reward_fn(completions, task_type, prior_task_instructions, real_fetched_urls,
                        per_task_urls, role, **kwargs):
    """Routes each completion to the reward function matching its own row's task_type — a
    thin_coverage/uneven_task_investment/task_verification_flagged row is scored purely on
    delegate_tasks-shaped behavior (the latter two REUSE thin_coverage_response_reward outright,
    see each generator's own docstring for why no new reward function was written for either),
    citation_grounding/findings_evidence rows purely on write_workspace_file-shaped behavior,
    stale_findings rows on delegate_tasks-or-clean-stop behavior (stale_findings_response_reward),
    and tool_name rows purely on whether the tool actually called is real for the calling role
    (real_tool_name_reward). Never cross-scores one type against another's rubric."""
    rewards = []
    for completion, ttype, prior, fetched, task_urls, ex_role in zip(
        completions, task_type, prior_task_instructions, real_fetched_urls, per_task_urls, role
    ):
        tool_call, text = parse_completion(completion)
        if ttype == "thin_coverage":
            rewards.append(thin_coverage_response_reward(prior, tool_call, text))
        elif ttype == "citation_grounding":
            wrote_file = bool(tool_call and tool_call.get("name") == "write_workspace_file")
            writer_score = writer_role_response_reward(wrote_file, text)
            if not wrote_file:
                # writer_role's own failure gate already covers this completion (0.0) -- no
                # content to score under citation_grounding without a real write_workspace_file
                # call, same as before this change.
                rewards.append(writer_score)
            else:
                content = _tool_args(tool_call).get("content", "")
                citation_score = citation_grounding_response_reward(content, fetched)
                rewards.append(0.5 * citation_score + 0.5 * writer_score)
        elif ttype == "findings_evidence":
            wrote_file = bool(tool_call and tool_call.get("name") == "write_workspace_file")
            writer_score = writer_role_response_reward(wrote_file, text)
            if not wrote_file:
                rewards.append(writer_score)
            else:
                content = _tool_args(tool_call).get("content", "")
                evidence_score = findings_underuses_evidence_response_reward(content, task_urls)
                rewards.append(0.5 * evidence_score + 0.5 * writer_score)
        elif ttype in ("uneven_task_investment", "task_verification_flagged"):
            rewards.append(thin_coverage_response_reward(prior, tool_call, text))
        elif ttype == "tool_name":
            tool_name = tool_call.get("name") if tool_call else None
            rewards.append(real_tool_name_reward(tool_name, role=ex_role))
        elif ttype == "stale_findings":
            rewards.append(stale_findings_response_reward(tool_call, text))
        else:
            raise ValueError(f"unknown task_type: {ttype!r}")
    return rewards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=260)
    parser.add_argument("--num-generations", type=int, default=4)
    parser.add_argument("--max-completion-length", type=int, default=700)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--out-dir", default="/mnt/nuevovol/llm-models/qwen3-4b-combined-lora")
    parser.add_argument("--resume", action="store_true",
                         help="Resume from the latest checkpoint in --out-dir instead of starting over")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    dataset = load_dataset(tokenizer)
    task_types = ("thin_coverage", "citation_grounding", "findings_evidence", "tool_name",
                  "stale_findings", "uneven_task_investment", "task_verification_flagged")
    counts = {t: sum(1 for tt in dataset["task_type"] if tt == t) for t in task_types}
    counts_str = ", ".join(f"{n} {t}" for t, n in counts.items())
    print(f"Loaded {len(dataset)} training prompts ({counts_str})")
    dataset = dataset.shuffle(seed=42)

    lora_config = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    training_args = GRPOConfig(
        output_dir=args.out_dir,
        max_steps=args.max_steps,
        num_generations=args.num_generations,
        per_device_train_batch_size=args.num_generations,  # one prompt's whole group per step
        gradient_accumulation_steps=1,
        max_completion_length=args.max_completion_length,
        learning_rate=args.learning_rate,
        # temperature left at trl's own default (1.0), not the previous explicit 0.8 override --
        # RESEARCH.md sec.1 (arXiv:2510.11701): "sustaining higher policy entropy, especially for
        # weaker/smaller models, improves training efficiency." This is DeepDelve's smallest
        # fine-tuning target; a lower-than-default temperature works against that recommendation.
        # epsilon_high=0.28 (DAPO paper's own published clip-higher value): trl 1.8.0's
        # loss_type="dapo" default silently collapses to symmetric +-0.2 clipping when
        # epsilon_high is left unset (confirmed in GRPOTrainer source: `self.epsilon_high =
        # args.epsilon_high if args.epsilon_high is not None else args.epsilon`) -- this actually
        # engages the asymmetric "clip-higher" exploration lever loss_type="dapo" implies but
        # doesn't get at trl's own defaults. beta (KL coefficient) is left unset -- trl already
        # defaults it to 0.0, already matching the paper's "don't over-constrain with a strong KL
        # penalty" recommendation, confirmed rather than assumed.
        epsilon_high=0.28,
        logging_steps=1,
        # Intermediate checkpointing added 2026-07-27 after TWO late-stage crashes on this exact
        # round (step 8/260 and step 250/260 -- the second one 96% through a ~95-minute run) with
        # the previous save_strategy="no" ("this is a smoke-scale run" no longer holds true for a
        # 7-dimension, 260-step round): every 50 steps, keep only the last 2 on disk so a crash
        # loses at most ~50 steps of progress instead of the whole run, and --resume lets a
        # relaunch pick up from the latest checkpoint instead of starting over.
        save_strategy="steps",
        save_steps=50,
        save_total_limit=2,
        bf16=True,
        report_to=[],
    )

    trainer = GRPOTrainer(
        model=model,
        reward_funcs=combined_reward_fn,
        args=training_args,
        train_dataset=dataset,
        peft_config=lora_config,
    )

    trainer.train(resume_from_checkpoint=args.resume)

    os.makedirs(args.out_dir, exist_ok=True)
    trainer.save_model(args.out_dir)
    tokenizer.save_pretrained(args.out_dir)
    print(f"Saved LoRA adapter to {args.out_dir}")


if __name__ == "__main__":
    main()
