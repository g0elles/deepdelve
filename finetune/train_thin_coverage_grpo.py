"""
Real GRPO fine-tune of qwen3:4b (base: Qwen/Qwen3-4B) against thin_coverage_response_reward —
the single sole fine-tuning target agreed for this project (see ROADMAP.md "Scoped fine-tuning
plan"). LoRA only, no quantization needed (bf16 4B model comfortably fits this card's ~16GB VRAM
budget alongside LoRA's small trainable footprint, and LoRA lets GRPO derive its reference model
by disabling the adapter — no second full model copy needed).

Training data: finetune/data/thin_coverage_synthetic_prompts.jsonl (78 real-nudge-text prompts,
see finetune/generate_synthetic_prompts.py for why these are genuinely real, not fabricated,
despite being "synthetic" — the SITUATION is synthetic, the nudge-generation code is real
production code). The 6 real extracted examples in thin_coverage.jsonl are reward-function
calibration data, not training data (too few, and inconsistent formatting since they were mined
from real logs rather than generated fresh) — held out for post-training evaluation instead.

Reward function: finetune/reward.py::thin_coverage_response_reward, imported directly — the exact
same scoring logic already verified against real base-model completions (see reward.py's own
comments on the "narrates intent without acting" bug found and fixed before this script was
written).

Usage:
  python finetune/train_thin_coverage_grpo.py --max-steps 30 --out-dir /mnt/nuevovol/llm-models/qwen3-4b-thin-coverage-lora
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import thin_coverage_response_reward  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from trl import GRPOConfig, GRPOTrainer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "thin_coverage_synthetic_prompts.jsonl")

# Same schema DeepDelve's real Planner sees for delegate_tasks (src/prompts.py's tool
# registration) — training against the identical tool definition the live system uses, not a
# simplified stand-in, so the learned behavior transfers to the real deployment.
DELEGATE_TASKS_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate_tasks",
        "description": "Delegate multiple independent tasks to specialized sub-agents to be executed concurrently. Pass a list of dictionaries, each with 'task_name', 'instructions', and 'agent_id'.",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_name": {"type": "string"},
                            "instructions": {"type": "string"},
                            "agent_id": {"type": "string", "enum": ["WebSearcher", "AcademicSearcher", "DocumentAnalyzer", "DataAnalyzer"]},
                        },
                        "required": ["task_name", "instructions", "agent_id"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
}

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_completion(text: str) -> tuple[dict | None, str]:
    """Mirrors extract_dataset.py::_parse_task_call's normalization, adapted for Qwen3's real
    <tool_call>{...}</tool_call> chat-template convention (confirmed live against the actual
    tokenizer, not assumed) instead of the OpenAI SDK's structured tool_calls field — at raw
    generation time there's no SDK layer, just the model's own text output."""
    m = _TOOL_CALL_RE.search(text)
    if not m:
        return None, text.strip()
    try:
        call = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None, text.strip()
    return call, ""


def load_dataset(tokenizer) -> Dataset:
    rows = []
    with open(DATA_PATH, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[DELEGATE_TASKS_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                # GRPOTrainer passes every other dataset column through to the reward function as
                # a kwarg list aligned with `completions` — this is how prior_task_instructions
                # reaches the reward function without a global/closure lookup.
                "prior_task_instructions": ex["prior_task_instructions"],
            })
    return Dataset.from_list(rows)


def thin_coverage_reward_fn(completions, prior_task_instructions, **kwargs):
    rewards = []
    for completion, prior in zip(completions, prior_task_instructions):
        tool_call, text = parse_completion(completion)
        rewards.append(thin_coverage_response_reward(prior, tool_call, text))
    return rewards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--num-generations", type=int, default=4)
    parser.add_argument("--max-completion-length", type=int, default=350)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--out-dir", default="/mnt/nuevovol/llm-models/qwen3-4b-thin-coverage-lora")
    args = parser.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    dataset = load_dataset(tokenizer)
    print(f"Loaded {len(dataset)} training prompts from {DATA_PATH}")

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
        reward_funcs=thin_coverage_reward_fn,
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
