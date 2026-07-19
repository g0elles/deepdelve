"""
Real GRPO fine-tune of qwen3:4b (base: Qwen/Qwen3-4B) against citation_grounding_response_reward —
the citation-grounding counterpart to train_thin_coverage_grpo.py, same model/LoRA/VRAM budget
rationale (see that script's own docstring). Second GRPO round for this project, scoped per
session_status/CURRENT.md's "next concrete step."

Training data: finetune/data/citation_grounding_synthetic_prompts.jsonl (40 real-nudge-text
prompts across 20 topics, see finetune/generate_synthetic_citation_prompts.py for why these are
genuinely real, not fabricated — the SITUATION is synthetic, the check_not_grounded/
real_grounding_problem code that produced the prompt text is real production code). The 38 real
extracted examples in citation_grounding.jsonl are reward-function calibration/held-out-eval data,
not training data (report-level granularity, no matching tool-call shape to imitate) — same
held-out role thin_coverage.jsonl played for the first round.

Prompt shape differs from thin_coverage's: the corrective nudge here asks the writer role
(Builder/FindingsWriter) to REWRITE the artifact, i.e. call write_workspace_file(filename,
content) — not delegate_tasks. Reward function: finetune/reward.py::citation_grounding_response_reward,
scored against the SAME real_fetched_urls the scenario recorded (ground truth carried through the
dataset row, exactly like thin_coverage's prior_task_instructions column). A completion that
doesn't call write_workspace_file at all scores 0.0 outright — that's a DIFFERENT, already-covered
failure (writer_role_response_reward's job), but this single-reward training round still needs
some signal for it rather than silently treating a no-op as "no citations, so trivially grounded."

Usage:
  python finetune/train_citation_grounding_grpo.py --max-steps 30 --out-dir /mnt/nuevovol/llm-models/qwen3-4b-citation-grounding-lora
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import citation_grounding_response_reward  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from peft import LoraConfig  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402
from trl import GRPOConfig, GRPOTrainer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "citation_grounding_synthetic_prompts.jsonl")

# Matches src/tools/fs.py::write_workspace_file's real signature/docstring exactly (auto-generated
# by agent_framework's @tool decorator in the live system from that function's own type hints) —
# training against the identical tool definition the live Builder/FindingsWriter roles see.
WRITE_WORKSPACE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_workspace_file",
        "description": "Save content to your workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["filename", "content"],
        },
    },
}

_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse_completion(text: str) -> dict | None:
    """Same convention as train_thin_coverage_grpo.py::parse_completion (Qwen3's real
    <tool_call>{...}</tool_call> chat-template output), returning the parsed call dict or None —
    no fallback text return needed here since this reward doesn't score narrated text at all."""
    m = _TOOL_CALL_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def load_dataset(tokenizer) -> Dataset:
    rows = []
    with open(DATA_PATH, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            messages = [{"role": "user", "content": ex["prompt"]}]
            prompt_text = tokenizer.apply_chat_template(
                messages, tools=[WRITE_WORKSPACE_FILE_TOOL], add_generation_prompt=True,
                tokenize=False, enable_thinking=False,
            )
            rows.append({
                "prompt": prompt_text,
                # GRPOTrainer passes every other dataset column through to the reward function as
                # a kwarg list aligned with `completions` — this is how the real fetched-URL set
                # reaches the reward function without a global/closure lookup, same mechanism
                # train_thin_coverage_grpo.py uses for prior_task_instructions.
                "real_fetched_urls": ex["real_fetched_urls"],
            })
    return Dataset.from_list(rows)


def citation_grounding_reward_fn(completions, real_fetched_urls, **kwargs):
    rewards = []
    for completion, fetched in zip(completions, real_fetched_urls):
        tool_call = parse_completion(completion)
        if not tool_call or tool_call.get("name") != "write_workspace_file":
            rewards.append(0.0)  # didn't rewrite the artifact at all — a different, real failure
            continue
        content = (tool_call.get("arguments") or {}).get("content", "")
        rewards.append(citation_grounding_response_reward(content, fetched))
    return rewards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--num-generations", type=int, default=4)
    parser.add_argument("--max-completion-length", type=int, default=700)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--out-dir", default="/mnt/nuevovol/llm-models/qwen3-4b-citation-grounding-lora")
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
        reward_funcs=citation_grounding_reward_fn,
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
