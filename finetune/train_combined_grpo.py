"""
Combined multi-objective GRPO fine-tune of qwen3:4b (base: Qwen/Qwen3-4B) — trains BOTH
thin_coverage_response_reward and citation_grounding_response_reward in ONE pass, off ONE LoRA,
from raw base. Replaces the two prior single-dimension rounds
(train_thin_coverage_grpo.py, train_citation_grounding_grpo.py), which were each trained
independently off raw Qwen/Qwen3-4B rather than stacked or combined.

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

Training data: finetune/data/thin_coverage_synthetic_prompts.jsonl (78 Planner-shaped prompts) +
finetune/data/citation_grounding_synthetic_prompts.jsonl (80 prompts: 40 Builder-shaped
"rewrite final_report.md" + 40 FindingsWriter-shaped "write findings.md fresh" — role gap closed
2026-07-19, see that script's own _findings_writer_prompt docstring) = 158 total, each row tagged
with its own task_type. Each row's prompt is built with ONLY the tool(s) its own real role
actually has (delegate_tasks for thin_coverage/Planner-shaped rows, write_workspace_file for
citation_grounding/Builder-or-FindingsWriter-shaped rows) — matching the real per-role toolset
each dispatch sees live, not a merged tool list. A reward router dispatches each completion to the
correct scorer by its row's task_type (role doesn't affect scoring — both roles are judged by the
identical citation_grounding_response_reward rubric).

Usage:
  python finetune/train_combined_grpo.py --max-steps 90 --out-dir /mnt/nuevovol/llm-models/qwen3-4b-combined-lora
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import thin_coverage_response_reward, citation_grounding_response_reward  # noqa: E402
from train_thin_coverage_grpo import DELEGATE_TASKS_TOOL  # noqa: E402
from train_citation_grounding_grpo import WRITE_WORKSPACE_FILE_TOOL  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from trl import GRPOConfig, GRPOTrainer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
THIN_COVERAGE_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "thin_coverage_synthetic_prompts.jsonl")
CITATION_GROUNDING_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "citation_grounding_synthetic_prompts.jsonl")

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
            })
    return Dataset.from_list(rows)


def combined_reward_fn(completions, task_type, prior_task_instructions, real_fetched_urls, **kwargs):
    """Routes each completion to the reward function matching its own row's task_type — a
    thin_coverage row is scored purely on delegate_tasks-shaped behavior, a citation_grounding row
    purely on write_workspace_file-shaped behavior. Never cross-scores one type against the
    other's rubric."""
    rewards = []
    for completion, ttype, prior, fetched in zip(completions, task_type, prior_task_instructions, real_fetched_urls):
        tool_call, text = parse_completion(completion)
        if ttype == "thin_coverage":
            rewards.append(thin_coverage_response_reward(prior, tool_call, text))
        elif ttype == "citation_grounding":
            if not tool_call or tool_call.get("name") != "write_workspace_file":
                rewards.append(0.0)
            else:
                content = (tool_call.get("arguments") or {}).get("content", "")
                rewards.append(citation_grounding_response_reward(content, fetched))
        else:
            raise ValueError(f"unknown task_type: {ttype!r}")
    return rewards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=90)
    parser.add_argument("--num-generations", type=int, default=4)
    parser.add_argument("--max-completion-length", type=int, default=700)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--out-dir", default="/mnt/nuevovol/llm-models/qwen3-4b-combined-lora")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    dataset = load_dataset(tokenizer)
    n_thin = sum(1 for t in dataset["task_type"] if t == "thin_coverage")
    n_cite = sum(1 for t in dataset["task_type"] if t == "citation_grounding")
    print(f"Loaded {len(dataset)} training prompts ({n_thin} thin_coverage, {n_cite} citation_grounding)")
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
        temperature=0.8,
        logging_steps=1,
        save_strategy="no",  # explicit save_pretrained at the end instead — this is a smoke-scale run
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

    trainer.train()

    os.makedirs(args.out_dir, exist_ok=True)
    trainer.save_model(args.out_dir)
    tokenizer.save_pretrained(args.out_dir)
    print(f"Saved LoRA adapter to {args.out_dir}")


if __name__ == "__main__":
    main()
