"""
Evaluates base Qwen3-4B vs the citation-grounding LoRA adapter on held-out prompts — same
methodology the thin_coverage round used (base vs fine-tuned, real completions read directly, not
just scored): generate N completions per prompt at a fixed temperature, score each with
finetune/reward.py::citation_grounding_response_reward, compare mean reward.

Held-out set: finetune/data/citation_grounding_heldout_prompts.jsonl (3 topics, 6 prompts, built
by finetune/generate_synthetic_citation_prompts.py --held-out — genuinely never seen during
training, same real check_not_grounded/real_grounding_problem pipeline as the training data).

Usage:
  python finetune/evaluate_citation_grounding.py --adapter /mnt/nuevovol/llm-models/qwen3-4b-citation-grounding-lora
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import citation_grounding_response_reward  # noqa: E402
from train_citation_grounding_grpo import WRITE_WORKSPACE_FILE_TOOL, parse_completion  # noqa: E402

os.environ.setdefault("HF_HOME", "/mnt/nuevovol/hf-cache")
os.environ.setdefault("HIP_VISIBLE_DEVICES", "0")

import torch  # noqa: E402
from peft import PeftModel  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

MODEL_ID = "Qwen/Qwen3-4B"
HELD_OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "citation_grounding_heldout_prompts.jsonl")


def load_held_out():
    rows = []
    with open(HELD_OUT_PATH, encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def evaluate(model, tokenizer, rows, num_samples: int, temperature: float, label: str):
    results = []
    for row in rows:
        messages = [{"role": "user", "content": row["prompt"]}]
        prompt_text = tokenizer.apply_chat_template(
            messages, tools=[WRITE_WORKSPACE_FILE_TOOL], add_generation_prompt=True,
            tokenize=False, enable_thinking=False,
        )
        inputs = tokenizer(prompt_text, return_tensors="pt").to(model.device)
        scores = []
        for _ in range(num_samples):
            out = model.generate(
                **inputs, max_new_tokens=700, do_sample=True, temperature=temperature, top_p=0.95,
                pad_token_id=tokenizer.eos_token_id,
            )
            completion = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
            tool_call = parse_completion(completion)
            if tool_call and tool_call.get("name") == "write_workspace_file":
                content = (tool_call.get("arguments") or {}).get("content", "")
                score = citation_grounding_response_reward(content, row["real_fetched_urls"])
            else:
                score = 0.0
            scores.append(score)
        mean = sum(scores) / len(scores)
        results.append({"topic": row["topic"], "scores": scores, "mean": mean})
        print(f"[{label}] {row['topic']!r}: scores={scores} mean={mean:.2f}")
    overall = sum(r["mean"] for r in results) / len(results)
    print(f"[{label}] OVERALL mean reward across {len(results)} held-out prompts: {overall:.3f}\n")
    return overall, results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="/mnt/nuevovol/llm-models/qwen3-4b-citation-grounding-lora")
    parser.add_argument("--num-samples", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.8)
    args = parser.parse_args()

    rows = load_held_out()
    print(f"Loaded {len(rows)} held-out prompts from {HELD_OUT_PATH}\n")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    base_model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    print("=== BASE MODEL ===")
    base_overall, base_results = evaluate(base_model, tokenizer, rows, args.num_samples, args.temperature, "base")

    print("=== FINE-TUNED (LoRA adapter active) ===")
    peft_model = PeftModel.from_pretrained(base_model, args.adapter)
    ft_overall, ft_results = evaluate(peft_model, tokenizer, rows, args.num_samples, args.temperature, "fine-tuned")

    print("=== SUMMARY ===")
    print(f"Base model mean reward:       {base_overall:.3f}")
    print(f"Fine-tuned model mean reward: {ft_overall:.3f}")
    print(f"Delta: {ft_overall - base_overall:+.3f}")


if __name__ == "__main__":
    main()
