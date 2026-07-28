"""
Evaluates base Qwen3-4B vs the combined LoRA adapter (train_combined_grpo.py) on held-out
prompts from ALL 7 task_types the combined round trains against. Real completions read directly,
not just scored, generated at a fixed temperature, N samples per prompt. This is the actual
overfitting check for a GRPO round trained on a small synthetic dataset (~301-302 rows): every
held-out prompt uses topics/scenarios NEVER seen during training (see each generator's own
HELD_OUT_SCENARIOS list), so a fine-tuned model that only memorized the training scenarios'
specific phrasing rather than genuinely learning the underlying behavior will show a smaller (or
negative) delta here than on the training-topic rewards reported live during training itself.

Held-out sets (all genuinely never seen during training, same real check-function pipeline as the
training data):
  - finetune/data/thin_coverage_heldout_prompts.jsonl (6 rows)
  - finetune/data/citation_grounding_heldout_prompts.jsonl (12 rows)
  - finetune/data/findings_evidence_heldout_prompts.jsonl (4 rows)
  - finetune/data/tool_name_heldout_prompts.jsonl (10 rows)
  - finetune/data/stale_findings_heldout_prompts.jsonl (2 rows)
  - finetune/data/uneven_investment_heldout_prompts.jsonl (2 rows)
  - finetune/data/task_verification_flagged_heldout_prompts.jsonl (3 rows)

Usage:
  python finetune/evaluate_combined.py --adapter /mnt/nuevovol/llm-models/qwen3-4b-combined-v2-lora
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reward import (  # noqa: E402
    thin_coverage_response_reward, citation_grounding_response_reward,
    writer_role_response_reward, findings_underuses_evidence_response_reward,
    real_tool_name_reward, stale_findings_response_reward, _tool_args,
)
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

TASK_TYPES = ("thin_coverage", "citation_grounding", "findings_evidence", "tool_name",
              "stale_findings", "uneven_task_investment", "task_verification_flagged")

_HELDOUT_FILES = {
    "thin_coverage": "thin_coverage_heldout_prompts.jsonl",
    "citation_grounding": "citation_grounding_heldout_prompts.jsonl",
    "findings_evidence": "findings_evidence_heldout_prompts.jsonl",
    "tool_name": "tool_name_heldout_prompts.jsonl",
    "stale_findings": "stale_findings_heldout_prompts.jsonl",
    "uneven_task_investment": "uneven_investment_heldout_prompts.jsonl",
    "task_verification_flagged": "task_verification_flagged_heldout_prompts.jsonl",
}


def load_rows():
    rows = []
    for task_type, filename in _HELDOUT_FILES.items():
        path = os.path.join(DATA_DIR, filename)
        with open(path, encoding="utf-8") as f:
            for line in f:
                ex = json.loads(line)
                rows.append({
                    "task_type": task_type,
                    "topic": ex.get("topic", ""),
                    "prompt": ex["prompt"],
                    "prior_task_instructions": ex.get("prior_task_instructions", []),
                    "real_fetched_urls": ex.get("real_fetched_urls", []),
                    "per_task_urls": ex.get("per_task_urls", {}),
                    "role": ex.get("role"),
                    "tools": ex.get("tools"),
                })
    return rows


def tool_for(row):
    if row["task_type"] == "tool_name":
        return row["tools"]
    if row["task_type"] in ("citation_grounding", "findings_evidence"):
        return [WRITE_WORKSPACE_FILE_TOOL]
    return [DELEGATE_TASKS_TOOL]  # thin_coverage, stale_findings, uneven_task_investment, task_verification_flagged


def score(row, completion):
    ttype = row["task_type"]
    tool_call, text = parse_completion(completion)
    if ttype in ("thin_coverage", "uneven_task_investment", "task_verification_flagged"):
        return thin_coverage_response_reward(row["prior_task_instructions"], tool_call, text)
    if ttype == "stale_findings":
        return stale_findings_response_reward(tool_call, text)
    if ttype == "tool_name":
        tool_name = tool_call.get("name") if tool_call else None
        return real_tool_name_reward(tool_name, role=row["role"])
    if ttype in ("citation_grounding", "findings_evidence"):
        wrote_file = bool(tool_call and tool_call.get("name") == "write_workspace_file")
        writer_score = writer_role_response_reward(wrote_file, text)
        if not wrote_file:
            return writer_score
        content = _tool_args(tool_call).get("content", "")
        if ttype == "citation_grounding":
            content_score = citation_grounding_response_reward(content, row["real_fetched_urls"])
        else:
            content_score = findings_underuses_evidence_response_reward(content, row["per_task_urls"])
        return 0.5 * content_score + 0.5 * writer_score
    raise ValueError(f"unknown task_type: {ttype!r}")


def evaluate(model, tokenizer, rows, num_samples: int, temperature: float, label: str):
    by_type_totals = {t: [] for t in TASK_TYPES}
    for row in rows:
        messages = [{"role": "user", "content": row["prompt"]}]
        prompt_text = tokenizer.apply_chat_template(
            messages, tools=tool_for(row), add_generation_prompt=True, tokenize=False, enable_thinking=False,
        )
        inputs = tokenizer(prompt_text, return_tensors="pt").to(model.device)
        scores = []
        for _ in range(num_samples):
            out = model.generate(
                **inputs, max_new_tokens=700, do_sample=True, temperature=temperature, top_p=0.95,
                pad_token_id=tokenizer.eos_token_id,
            )
            completion = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
            scores.append(score(row, completion))
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
    parser.add_argument("--adapter", default="/mnt/nuevovol/llm-models/qwen3-4b-combined-v2-lora")
    parser.add_argument("--num-samples", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.8)
    args = parser.parse_args()

    rows = load_rows()
    counts = ", ".join(f"{sum(1 for r in rows if r['task_type']==t)} {t}" for t in TASK_TYPES)
    print(f"Loaded {len(rows)} held-out prompts ({counts})\n")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    base_model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, device_map="cuda:0")

    print("=== BASE MODEL ===")
    base_results = evaluate(base_model, tokenizer, rows, args.num_samples, args.temperature, "base")

    print("=== FINE-TUNED (combined LoRA active) ===")
    peft_model = PeftModel.from_pretrained(base_model, args.adapter)
    ft_results = evaluate(peft_model, tokenizer, rows, args.num_samples, args.temperature, "fine-tuned")

    print("=== SUMMARY (overfitting check: a real, generalized improvement shows up here too, ===")
    print("=== not just in training-topic rewards logged live during training)                ===")
    for k in (*TASK_TYPES, "overall"):
        b, f = base_results.get(k), ft_results.get(k)
        if b is None or f is None:
            continue
        print(f"{k:28s} base={b:.3f}  fine-tuned={f:.3f}  delta={f-b:+.3f}")


if __name__ == "__main__":
    main()
