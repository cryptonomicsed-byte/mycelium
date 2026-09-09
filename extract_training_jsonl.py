#!/usr/bin/env python3
"""
mycelium.db → privacy-safe training JSONL extractor

Output schema per record:
  {agent_role, kind, action, target_kind, outcome, duration_ms, ts_bucket}

Privacy rules:
  - NO payload contents (stripped entirely)
  - NO raw wallet/token addresses (base58 replaced with <token_addr>)
  - NO session IDs (dropped)
  - NO row IDs (dropped)
  - ts bucketed to hour (no sub-hour precision)

Usage:
  python3 extract_training_jsonl.py [--output traces.jsonl] [--findings findings.jsonl] [--stats]
"""

import sqlite3
import json
import re
import sys
import argparse
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

DB_PATH = Path(__file__).parent / "mycelium.db"

# Base58 Solana address pattern (32-44 chars, base58 alphabet)
_BASE58_RE = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')
# Ethereum/EVM address pattern
_EVM_RE = re.compile(r'\b0x[0-9a-fA-F]{40}\b')
# Generic private key / nsec / secret patterns (extra safety)
_SECRET_RE = re.compile(r'\b(nsec1[a-z0-9]{58}|[0-9a-f]{64})\b', re.I)


def scrub_target(target: str | None) -> str:
    """Replace raw addresses with typed placeholders, keep structural meaning."""
    if not target:
        return ""
    t = str(target)
    t = _SECRET_RE.sub("<secret>", t)
    t = _EVM_RE.sub("<evm_addr>", t)
    t = _BASE58_RE.sub("<token_addr>", t)
    return t


def bucket_ts(ts_str: str | None) -> str:
    """Truncate ISO timestamp to hour precision."""
    if not ts_str:
        return ""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%dT%H:00Z")
    except Exception:
        return ts_str[:13] if ts_str else ""


def extract_traces(db: sqlite3.Connection) -> list[dict]:
    c = db.cursor()
    c.execute("""
        SELECT agent, kind, action, target, outcome, duration_ms, ts
        FROM traces
        ORDER BY ts ASC
    """)
    rows = c.fetchall()

    records = []
    for agent, kind, action, target, outcome, duration_ms, ts in rows:
        records.append({
            "agent_role":   str(agent or ""),
            "kind":         str(kind or ""),
            "action":       str(action or ""),
            "target_kind":  scrub_target(target),
            "outcome":      str(outcome or ""),
            "duration_ms":  int(duration_ms) if duration_ms is not None else 0,
            "ts_bucket":    bucket_ts(ts),
        })
    return records


def extract_findings(db: sqlite3.Connection) -> list[dict]:
    """
    Findings encode distilled behavioral patterns from the miner.
    Safe to include: miner type, confidence, title (no addresses), state.
    Strip: evidence (may contain raw action details), suggestion (structural only).
    """
    c = db.cursor()
    c.execute("""
        SELECT miner, confidence, title, state
        FROM findings
        ORDER BY confidence DESC
    """)
    rows = c.fetchall()

    records = []
    for miner, confidence, title, state in rows:
        title_clean = scrub_target(str(title or ""))
        records.append({
            "type":       "finding",
            "miner":      str(miner or ""),
            "confidence": float(confidence) if confidence is not None else 0.0,
            "title":      title_clean,
            "state":      str(state or ""),
        })
    return records


def print_stats(traces: list[dict], findings: list[dict]):
    print(f"\n{'='*60}")
    print(f"MYCELIUM TRAINING DATASET STATS")
    print(f"{'='*60}")
    print(f"  Traces:   {len(traces):,}")
    print(f"  Findings: {len(findings):,}")
    print(f"  Total:    {len(traces) + len(findings):,}")

    print(f"\n--- Agent Distribution (traces) ---")
    agent_counts = Counter(r["agent_role"] for r in traces)
    for agent, n in agent_counts.most_common():
        bar = "█" * (n * 40 // len(traces))
        print(f"  {agent:40s} {n:5d}  {bar}")

    print(f"\n--- Outcome Distribution ---")
    outcome_counts = Counter(r["outcome"] for r in traces)
    for outcome, n in outcome_counts.most_common():
        pct = n * 100 // len(traces)
        print(f"  {outcome:20s} {n:5d}  ({pct}%)")

    print(f"\n--- Kind Distribution ---")
    kind_counts = Counter(r["kind"] for r in traces)
    for kind, n in kind_counts.most_common():
        print(f"  {kind:30s} {n:5d}")

    print(f"\n--- Action Distribution (top 20) ---")
    action_counts = Counter(r["action"] for r in traces)
    for action, n in action_counts.most_common(20):
        print(f"  {action:40s} {n:5d}")

    durations = [r["duration_ms"] for r in traces if r["duration_ms"] > 0]
    if durations:
        avg = sum(durations) // len(durations)
        print(f"\n--- Duration ---")
        print(f"  avg: {avg:,} ms  min: {min(durations):,} ms  max: {max(durations):,} ms")

    # Estimate file size
    sample = json.dumps(traces[0]) if traces else "{}"
    est_bytes = len(sample) * len(traces)
    print(f"\n--- Size estimate ---")
    print(f"  ~{est_bytes // 1024} KB for traces JSONL")
    print(f"  ~{est_bytes // 1024 // 1024} MB" if est_bytes > 1_000_000 else "")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Extract mycelium training JSONL")
    parser.add_argument("--output", default="traces.jsonl", help="Output path for traces JSONL")
    parser.add_argument("--findings", default="findings.jsonl", help="Output path for findings JSONL")
    parser.add_argument("--stats", action="store_true", help="Print dataset statistics")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to mycelium.db")
    args = parser.parse_args()

    db = sqlite3.connect(args.db)

    traces = extract_traces(db)
    findings = extract_findings(db)
    db.close()

    # Write traces
    out = Path(args.output)
    with out.open("w") as f:
        for rec in traces:
            f.write(json.dumps(rec) + "\n")
    print(f"Wrote {len(traces):,} traces → {out}")

    # Write findings
    fout = Path(args.findings)
    with fout.open("w") as f:
        for rec in findings:
            f.write(json.dumps(rec) + "\n")
    print(f"Wrote {len(findings):,} findings → {fout}")

    if args.stats or True:  # always show stats
        print_stats(traces, findings)


if __name__ == "__main__":
    main()
