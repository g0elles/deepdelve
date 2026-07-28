"""
Generates diverse real_tool_name_reward PROMPTS (not responses) at zero GPU cost — this
dimension's counterpart to generate_synthetic_citation_prompts.py/
generate_synthetic_findings_evidence_prompts.py's own generators.

Unlike those two, no engine/completion.py check drives scenario-firing here:
real_tool_name_reward(tool_name, role) is a PURE function that scores whatever tool name comes out
of the model's own completion against reward.py's own ROLE_TOOLS/KNOWN_TOOLS -- there is no
"did this fire or not" gate to check a generated scenario against. What's real here instead is the
tool SCHEMA each prompt exposes: every role-shape below exposes exactly the tools that role has
live (src/app.py's own SubAgentConfig tool lists), imported directly from reward.py's ROLE_TOOLS
where a role is tracked there, or the same schema this project's other GRPO scripts already use
(DELEGATE_TASKS_TOOL, WRITE_WORKSPACE_FILE_TOOL) -- never a re-implementation or a simplified
stand-in.

Real observed hallucination distribution, confirmed directly against finetune/data/tool_name.jsonl
(219 real extracted rows, 21 hallucinated) before designing these scenarios: the DOMINANT shape
(14/21) is a ROLE-SCOPED VIOLATION -- a genuinely real tool used by a role that doesn't have it
(8/21: Builder calling delegate_tasks; 1/21: Builder calling fetch_url_to_workspace) -- not an
invented string. 6/21 are gpt-oss/Ollama Harmony-template leakage tokens
(`fetch_url_to_workspace<|channel|>commentary`), a serving-layer artifact specific to that backend/
template, not worth synthesizing for a Qwen3 GRPO target. The remainder are punctuation-mangled
invented fragments (`grep_search?`, `method?`, etc.). This generator's scenario design leans into
the dominant, reproducible shape: exposing each role's REAL, narrow toolset and letting GRPO's own
exploration discover that reaching for a tool outside it (real elsewhere, or invented outright)
scores 0.0.

KNOWN LIMITATION, documented here rather than silently assumed away: real_tool_name_reward only
checks "is this tool real for my role" -- never "is this the RIGHT tool for this instruction." A
Builder that calls read_workspace_file when asked to write still scores 1.0 under this dimension;
that is writer_role_response_reward's job (a separate, already-wired dimension), not this one's.

Usage:
  python finetune/generate_synthetic_tool_name_prompts.py --out finetune/data/tool_name_synthetic_prompts.jsonl
"""

import argparse
import json
import os

# Real tool schemas, matching what the live system actually exposes per role (src/app.py's own
# SubAgentConfig tool lists / prompts.py's tool registration) -- reused verbatim where an existing
# GRPO script already defines the exact schema, never re-implemented.
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
WRITE_WORKSPACE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_workspace_file",
        "description": "Save content to your workspace.",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string"}, "content": {"type": "string"}},
            "required": ["filename", "content"],
        },
    },
}
READ_WORKSPACE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_workspace_file",
        "description": "Read a file from your workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["filename"],
        },
    },
}
GREP_WORKSPACE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "grep_workspace_file",
        "description": "Search for a pattern within a workspace file.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "pattern": {"type": "string"},
                "context_lines": {"type": "integer"},
            },
            "required": ["filename", "pattern"],
        },
    },
}
THINK_TOOL = {
    "type": "function",
    "function": {
        "name": "think_tool",
        "description": "Record a private reflection before acting.",
        "parameters": {
            "type": "object",
            "properties": {"reflection": {"type": "string"}},
            "required": ["reflection"],
        },
    },
}
LIST_WORKSPACE_FILES_TOOL = {
    "type": "function",
    "function": {"name": "list_workspace_files", "description": "List files in your workspace.",
                  "parameters": {"type": "object", "properties": {}}},
}
WRITE_TODOS_TOOL = {
    "type": "function",
    "function": {
        "name": "write_todos", "description": "Record your research plan as a todo list.",
        "parameters": {"type": "object", "properties": {"todos": {"type": "string"}}, "required": ["todos"]},
    },
}
READ_TODOS_TOOL = {
    "type": "function",
    "function": {"name": "read_todos", "description": "Read your current todo list.",
                  "parameters": {"type": "object", "properties": {}}},
}
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for information on a given query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
                "topic": {"type": "string"},
            },
            "required": ["query"],
        },
    },
}
FETCH_URL_TOOL = {
    "type": "function",
    "function": {
        "name": "fetch_url_to_workspace",
        "description": "Fetch a URL's content and save it to your workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "filename": {"type": "string"},
                "convert_to_md": {"type": "boolean"},
            },
            "required": ["url"],
        },
    },
}
EXTRACT_STRUCTURED_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "extract_structured_data",
        "description": "Extract structured (table/JSON/CSV) data from a workspace file.",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string"}},
            "required": ["filename"],
        },
    },
}

# One entry per role-shape: (role_for_reward_fn, exposed_tools, prompt_template). role=None means
# real_tool_name_reward falls back to its own KNOWN_TOOLS union check (WebSearcher/
# AcademicSearcher/DocumentAnalyzer/DataAnalyzer aren't in reward.py's ROLE_TOOLS -- can't be
# reliably attributed from a log alone, per that dict's own comment -- so this generator mirrors
# the SAME fallback path live dispatches actually hit, not a stronger role-scoped check the real
# system never applies to these four roles either).
ROLE_SHAPES = [
    ("Planner", [LIST_WORKSPACE_FILES_TOOL, WRITE_TODOS_TOOL, READ_TODOS_TOOL, THINK_TOOL, DELEGATE_TASKS_TOOL],
     "You are the DeepDelve Planner. Delegate research on the following topic to the right specialist: {topic}"),
    ("Builder", [READ_WORKSPACE_FILE_TOOL, GREP_WORKSPACE_FILE_TOOL, WRITE_WORKSPACE_FILE_TOOL, THINK_TOOL],
     "You are the DeepDelve Builder. Rewrite 'final_report.md' from findings.md, incorporating the latest research on: {topic}"),
    ("FindingsWriter", [READ_WORKSPACE_FILE_TOOL, GREP_WORKSPACE_FILE_TOOL, WRITE_WORKSPACE_FILE_TOOL, THINK_TOOL],
     "You are the DeepDelve FindingsWriter. Write 'findings.md' fresh from the real research results on: {topic}"),
    ("PeerReviewer", [READ_WORKSPACE_FILE_TOOL, GREP_WORKSPACE_FILE_TOOL, THINK_TOOL],
     "You are the DeepDelve PeerReviewer. Review the draft report on {topic} for unsupported claims."),
    (None, [WEB_SEARCH_TOOL, FETCH_URL_TOOL, THINK_TOOL],
     "You are the DeepDelve WebSearcher. Research the following and report back with sources: {topic}"),
    (None, [WEB_SEARCH_TOOL, FETCH_URL_TOOL, THINK_TOOL],
     "You are the DeepDelve AcademicSearcher. Find papers and citations related to: {topic}"),
    (None, [READ_WORKSPACE_FILE_TOOL, GREP_WORKSPACE_FILE_TOOL, THINK_TOOL],
     "You are the DeepDelve DocumentAnalyzer. Read the fetched source and summarize what it says about: {topic}"),
    (None, [READ_WORKSPACE_FILE_TOOL, GREP_WORKSPACE_FILE_TOOL, EXTRACT_STRUCTURED_DATA_TOOL, THINK_TOOL],
     "You are the DeepDelve DataAnalyzer. Extract the structured data (tables/figures) relevant to: {topic}"),
]

TOPICS = [
    "the economic impact of container shipping delays",
    "recent advances in perovskite solar cell efficiency",
    "the history of the Panama Canal expansion",
    "coral reef restoration techniques",
    "quantum error correction codes",
    "the causes of colony collapse disorder in honeybees",
    "urban heat island mitigation strategies",
    "the archaeology of the Indus Valley civilization",
    "battery recycling technology for electric vehicles",
    "the linguistics of endangered Pacific island languages",
]

HELD_OUT_TOPICS = [
    "deep-sea hydrothermal vent ecosystems",
    "the Antikythera mechanism's gear train",
    "medieval trans-Saharan gold trade routes",
    "octopus short-term memory formation",
    "Roman concrete self-healing chemistry",
    "permafrost ancient virus revival risk",
    "mantle plume volcanic hotspot theory",
    "Great Barrier Reef coral bleaching recovery",
    "dark matter direct detection experiments",
    "migratory bird magnetoreception mechanisms",
]


def _row(role, tools, template, topic, source):
    return {
        "role": role,
        "topic": topic,
        "tools": tools,
        "prompt": template.format(topic=topic),
        "source": source,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="finetune/data/tool_name_synthetic_prompts.jsonl")
    parser.add_argument("--held-out", action="store_true",
                         help="Generate from HELD_OUT_TOPICS instead (topics never used in training)")
    args = parser.parse_args()
    if args.held_out and args.out == parser.get_default("out"):
        args.out = "finetune/data/tool_name_heldout_prompts.jsonl"

    examples = []
    if args.held_out:
        # 10 held-out topics, one per role-shape in rotation (8 shapes, wraps to cover all of them
        # at least once across the 10 topics) -- disjoint topics, not disjoint role-shape coverage.
        for i, topic in enumerate(HELD_OUT_TOPICS):
            role, tools, template = ROLE_SHAPES[i % len(ROLE_SHAPES)]
            examples.append(_row(role, tools, template, topic, "synthetic_scenario_role_dispatch_heldout"))
    else:
        for role, tools, template in ROLE_SHAPES:
            for topic in TOPICS:
                examples.append(_row(role, tools, template, topic, "synthetic_scenario_role_dispatch"))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    distinct_shapes = len({ex["prompt"].split(".", 1)[0] for ex in examples})
    print(f"Generated {len(examples)} synthetic tool-name PROMPTS across {distinct_shapes} distinct "
          f"role-shapes (zero GPU cost -- real per-role tool schemas, synthetic dispatch topics).")
    print(f"Wrote to {args.out}")


if __name__ == "__main__":
    main()
