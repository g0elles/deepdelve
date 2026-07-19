"""
Evaluates base Qwen3-4B vs the combined LoRA adapter (train_combined_grpo.py) on held-out
prompts from BOTH dimensions — thin_coverage and citation_grounding (Builder + FindingsWriter).
Same methodology as the prior single-dimension evals: real completions read directly, not just
scored, generated at a fixed temperature, N samples per prompt.

Held-out sets (all genuinely never seen during training, same real check-function pipeline as the
training data):
  - finetune/data/thin_coverage_heldout_prompts.jsonl (3 topics, 6 prompts)
  - finetune/data/citation_grounding_heldout_prompts.jsonl (3 topics, 12 prompts: Builder + FindingsWriter)

Usage:
  python finetune/evaluate_combined.py --adapter /mnt/nuevovol/llm-models/qwen3-4b-combined-lora
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import thin_coverage_response_reward, citation_grounding_response_reward  # noqa: E402
from train_combined_grpo import parse_completion  # noqa: E402
from train_thin_coverage_grpo import DELEGATE_TASKS_TOOL  # noqa: E402
from train_citation_grounding_grpo import WRITE_WORKSPACE_FILE_TOOL  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")

import torch  # noqa: E402
from peft import PeftModel  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
THIN_COVERAGE_HELDOUT = os.path.join(DATA_DIR, "thin_coverage_heldout_prompts.jsonl")
CITATION_GROUNDING_HELDOUT = os.path.join(DATA_DIR, "citation_grounding_heldout_prompts.jsonl")


def load_rows():
    rows = []
    with open(THIN_COVERAGE_HELDOUT, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            rows.append({"task_type": "thin_coverage", "topic": ex["topic"], "prompt": ex["prompt"],
                         "prior_task_instructions": ex["prior_task_instructions"], "real_fetched_urls": []})
    with open(CITATION_GROUNDING_HELDOUT, encoding="utf-8") as f:
        for line in f:
            ex = json.loads(line)
            rows.append({"task_type": "citation_grounding", "topic": ex["topic"], "prompt": ex["prompt"],
                         "prior_task_instructions": [], "real_fetched_urls": ex["real_fetched_urls"]})
    return rows


def score(task_type, completion, prior_task_instructions, real_fetched_urls):
    tool_call, text = parse_completion(completion)
    if task_type == "thin_coverage":
        return thin_coverage_response_reward(prior_task_instructions, tool_call, text)
    if not tool_call or tool_call.get("name") != "write_workspace_file":
        return 0.0
    content = (tool_call.get("arguments") or {}).get("content", "")
    return citation_grounding_response_reward(content, real_fetched_urls)


def evaluate(model, tokenizer, rows, num_samples: int, temperature: float, label: str):
    by_type_totals = {"thin_coverage": [], "citation_grounding": []}
    for row in rows:
        tool = DELEGATE_TASKS_TOOL if row["task_type"] == "thin_coverage" else WRITE_WORKSPACE_FILE_TOOL
        messages = [{"role": "user", "content": row["prompt"]}]
        prompt_text = tokenizer.apply_chat_template(
            messages, tools=[tool], add_generation_prompt=True, tokenize=False, enable_thinking=False,
        )
        inputs = tokenizer(prompt_text, return_tensors="pt").to(model.device)
        scores = []
        for _ in range(num_samples):
            out = model.generate(
                **inputs, max_new_tokens=700, do_sample=True, temperature=temperature, top_p=0.95,
                pad_token_id=tokenizer.eos_token_id,
            )
            completion = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
            scores.append(score(row["task_type"], completion, row["prior_task_instructions"], row["real_fetched_urls"]))
        mean = sum(scores) / len(scores)
        by_type_totals[row["task_type"]].append(mean)
        print(f"[{label}] [{row['task_type']}] {row['topic']!r}: scores={scores} mean={mean:.2f}", flush=True)

    results = {}
    for task_type, means in by_type_totals.items():
        if means:
            results[task_type] = sum(means) / len(means)
            print(f"[{label}] {task_type} OVERALL mean reward across {len(means)} prompts: {results[task_type]:.3f}")
    overall = sum(m for means in by_type_totals.values() for m in means) / sum(len(v) for v in by_type_totals.values())
    print(f"[{label}] COMBINED OVERALL mean reward: {overall:.3f}\n")
    results["overall"] = overall
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="/mnt/nuevovol/llm-models/qwen3-4b-combined-lora")
    parser.add_argument("--num-samples", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.8)
    args = parser.parse_args()

    rows = load_rows()
    print(f"Loaded {len(rows)} held-out prompts "
          f"({sum(1 for r in rows if r['task_type']=='thin_coverage')} thin_coverage, "
          f"{sum(1 for r in rows if r['task_type']=='citation_grounding')} citation_grounding)\n")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    base_model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    print("=== BASE MODEL ===")
    base_results = evaluate(base_model, tokenizer, rows, args.num_samples, args.temperature, "base")

    print("=== FINE-TUNED (combined LoRA active) ===")
    peft_model = PeftModel.from_pretrained(base_model, args.adapter)
    ft_results = evaluate(peft_model, tokenizer, rows, args.num_samples, args.temperature, "fine-tuned")

    print("=== SUMMARY ===")
    for k in ("thin_coverage", "citation_grounding", "overall"):
        b, f = base_results.get(k), ft_results.get(k)
        if b is None or f is None:
            continue
        print(f"{k:20s} base={b:.3f}  fine-tuned={f:.3f}  delta={f-b:+.3f}")


if __name__ == "__main__":
    main()
