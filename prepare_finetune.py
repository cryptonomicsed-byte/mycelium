#!/usr/bin/env python3
"""
Convert mycelium traces + findings → OpenAI chat JSONL for fine-tuning.

Output: finetune_dataset.jsonl  (chat format expected by GPU.ai / OpenAI /v1/files)

Each trace becomes one training example teaching the model to predict
correct agent behavior given context (agent_role, kind, target_kind) → (action, outcome).
"""
import json, pathlib, sys

SRC = pathlib.Path(__file__).parent

SYSTEM_PROMPT = (
    "You are a sovereign agent operating inside the Ọmọ Kọ́dà hive. "
    "Given an agent role and a task context, select the correct action "
    "and predict the outcome."
)

def trace_to_example(row: dict) -> dict:
    user = (
        f"Agent: {row['agent_role']}\n"
        f"Task kind: {row['kind']}\n"
        f"Target: {row['target_kind']}"
    )
    outcome_label = row['outcome']          # success / partial / failure
    duration = row.get('duration_ms', '?')
    assistant = (
        f"Action: {row['action']}\n"
        f"Outcome: {outcome_label}\n"
        f"Duration: ~{duration}ms"
    )
    return {"messages": [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": assistant},
    ]}

def finding_to_example(row: dict) -> dict:
    user = (
        f"Agent: hive-analyst\n"
        f"Task kind: pattern_recognition\n"
        f"Target: behavioral_traces"
    )
    conf = row.get('confidence', '?')
    state = row.get('state', 'observed')
    assistant = (
        f"Action: report_finding\n"
        f"Outcome: {state} (confidence {conf})\n"
        f"Finding: {row.get('title', '')}"
    )
    return {"messages": [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": assistant},
    ]}

out_path = SRC / "finetune_dataset.jsonl"
n_traces = n_findings = 0

with out_path.open("w") as f:
    for line in (SRC / "traces.jsonl").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        f.write(json.dumps(trace_to_example(row)) + "\n")
        n_traces += 1

    for line in (SRC / "findings.jsonl").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        f.write(json.dumps(finding_to_example(row)) + "\n")
        n_findings += 1

total = n_traces + n_findings
print(f"Written {total} examples ({n_traces} traces + {n_findings} findings)")
print(f"Output: {out_path}")
