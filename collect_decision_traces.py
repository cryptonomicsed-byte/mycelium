#!/usr/bin/env python3
"""
Phase 28.2 — Decision-trace collector for OSO Brain corpus expansion.

Pulls DECISION traces (state → action → outcome) from two high-ROI sources:
  1. VPS ares_logs/  — 700 MB of live 81-agent operational logs
  2. Vantage db      — 724 MB, 241 tables incl. task_completions, negotiations

Unlike the 84k wallet_intel OBSERVATION traces (which log other wallets' actions
and are 97% "success"), these sources contain real agent decisions with failure
outcomes.

Usage:
    python collect_decision_traces.py --source vantage_db --db /path/to/vantage.db
    python collect_decision_traces.py --source ares_logs --logs /path/to/ares_logs/
    python collect_decision_traces.py --source both --vps hostinger
    python collect_decision_traces.py --dry-run   # count + sample only

Output:
    decision_traces.jsonl     — raw decision traces (agent/kind/action/outcome)
    decision_finetune.jsonl   — chat format, ready to merge into finetune_dataset.jsonl

Run on VPS directly for best performance (avoids 700 MB transfer):
    ssh hostinger 'python3 /opt/ares/mycelium/collect_decision_traces.py --source both'
"""
import argparse
import json
import os
import pathlib
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Iterator

OUT_DIR = pathlib.Path(__file__).parent
TRACES_OUT  = OUT_DIR / "decision_traces.jsonl"
FINETUNE_OUT = OUT_DIR / "decision_finetune.jsonl"

SYSTEM_PROMPT = (
    "You are a sovereign agent operating inside the Ọmọ Kọ́dà hive. "
    "Given an agent role and a task context, select the correct action "
    "and predict the outcome."
)

# Outcomes we consider genuine decisions (not pure observations)
DECISION_OUTCOMES = {"success", "failure", "partial", "error", "timeout", "denied"}

# Wallet_intel actions to skip (pure observation, no decision content)
WALLET_OBS_ACTIONS = {"wallet_buy", "wallet_sell", "wallet_found", "wallet_observe",
                       "price_update", "balance_update"}


# ── Vantage DB collector ───────────────────────────────────────────────────────

VANTAGE_QUERIES = {
    # task completions: agent decided to complete a task
    "task_completions": """
        SELECT
            t.assigned_to   AS agent,
            'task_complete' AS kind,
            t.task_type     AS action,
            CASE WHEN t.status = 'completed' THEN 'success'
                 WHEN t.status = 'failed'    THEN 'failure'
                 ELSE t.status END           AS outcome,
            COALESCE(t.completed_at, t.updated_at) AS ts,
            t.result        AS payload_json
        FROM tasks t
        WHERE t.status IN ('completed', 'failed', 'cancelled')
          AND t.assigned_to IS NOT NULL
        LIMIT 20000
    """,
    # agent activity: any logged action
    "agent_activity": """
        SELECT
            a.agent_id      AS agent,
            a.action_type   AS kind,
            a.action_name   AS action,
            a.outcome       AS outcome,
            a.created_at    AS ts,
            a.details       AS payload_json
        FROM agent_activity_log a
        WHERE a.outcome IS NOT NULL
          AND a.action_name NOT IN ('heartbeat', 'ping', 'health_check')
        LIMIT 30000
    """,
    # negotiations: multi-step decisions with real win/lose outcomes
    "negotiations": """
        SELECT
            n.initiator_id  AS agent,
            'negotiation'   AS kind,
            n.negotiation_type AS action,
            n.outcome       AS outcome,
            n.updated_at    AS ts,
            n.final_terms   AS payload_json
        FROM negotiations n
        WHERE n.outcome IS NOT NULL
        LIMIT 5000
    """,
}


def collect_vantage(db_path: str, dry_run: bool = False) -> Iterator[dict]:
    """Yield decision traces from Vantage sqlite db."""
    if not os.path.exists(db_path):
        print(f"[vantage] db not found at {db_path}", file=sys.stderr)
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Discover which tables actually exist
    existing = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    count = 0

    for table, sql in VANTAGE_QUERIES.items():
        # Extract the FROM table name to check existence
        m = re.search(r'FROM\s+(\w+)', sql)
        tbl = m.group(1) if m else None
        if tbl and tbl not in existing:
            print(f"[vantage] table '{tbl}' not found, skipping", file=sys.stderr)
            continue

        try:
            rows = cur.execute(sql).fetchall()
        except sqlite3.Error as e:
            print(f"[vantage] {table}: {e}", file=sys.stderr)
            continue

        for row in rows:
            outcome = (row["outcome"] or "").lower()
            action = (row["action"] or "").lower()
            if action in WALLET_OBS_ACTIONS:
                continue
            if outcome not in DECISION_OUTCOMES:
                outcome = "success" if not outcome else outcome

            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}

            trace = {
                "agent":       row["agent"] or "vantage-agent",
                "kind":        row["kind"] or "task",
                "action":      row["action"] or "unknown",
                "outcome":     outcome,
                "source":      f"vantage/{table}",
                "ts":          row["ts"] or datetime.now(timezone.utc).isoformat(),
                "payload":     payload,
            }
            count += 1
            if not dry_run:
                yield trace

    conn.close()
    print(f"[vantage] {count} decision traces collected")


# ── ares_logs collector ────────────────────────────────────────────────────────

# Log line patterns: try structured JSON first, then key=value, then heuristic
_JSON_RE  = re.compile(r'\{.*\}')
_KV_RE    = re.compile(r'(\w+)=([^\s,]+)')
_ACT_RE   = re.compile(r'\b(action|cmd|op|call|invoke)\s*[:=]\s*([^\s,;]+)', re.I)
_OUT_RE   = re.compile(r'\b(success|fail(?:ed|ure)?|error|ok|done|timeout|denied)\b', re.I)
_AGENT_RE = re.compile(r'\bares[-_]([a-z_]+)', re.I)


def _parse_log_line(line: str, filename: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    agent = "ares-agent"
    m = _AGENT_RE.search(filename)
    if m:
        agent = f"ares-{m.group(1)}"

    # Try JSON blob embedded in line
    jm = _JSON_RE.search(line)
    if jm:
        try:
            d = json.loads(jm.group())
            action  = d.get("action") or d.get("cmd") or d.get("op") or d.get("event")
            outcome = d.get("outcome") or d.get("status") or d.get("result")
            kind    = d.get("kind") or d.get("type") or "log_event"
            if action and outcome:
                outcome = outcome.lower()
                if "success" in outcome or "ok" in outcome or "done" in outcome:
                    outcome = "success"
                elif "fail" in outcome or "error" in outcome:
                    outcome = "failure"
                elif "timeout" in outcome:
                    outcome = "timeout"
                if outcome in DECISION_OUTCOMES:
                    return {"agent": d.get("agent", agent), "kind": kind,
                            "action": action, "outcome": outcome,
                            "source": f"ares_logs/{filename}", "payload": d}
        except Exception:
            pass

    # Try key=value
    kv = dict(_KV_RE.findall(line))
    action  = kv.get("action") or kv.get("cmd") or kv.get("op")
    outcome_raw = kv.get("outcome") or kv.get("status") or kv.get("result") or ""
    if not outcome_raw:
        om = _OUT_RE.search(line)
        outcome_raw = om.group(1) if om else ""

    if action and outcome_raw:
        outcome = outcome_raw.lower()
        if "success" in outcome or outcome in ("ok", "done", "true", "1"):
            outcome = "success"
        elif "fail" in outcome or "error" in outcome:
            outcome = "failure"
        else:
            outcome = outcome_raw.lower()
        if outcome in DECISION_OUTCOMES:
            am = _ACT_RE.search(line)
            action = am.group(2) if am else action
            return {"agent": kv.get("agent", agent), "kind": kv.get("kind", "log_event"),
                    "action": action, "outcome": outcome,
                    "source": f"ares_logs/{filename}", "payload": kv}
    return None


def collect_ares_logs(logs_dir: str, dry_run: bool = False) -> Iterator[dict]:
    """Yield decision traces from ares_logs/ directory."""
    logs_path = pathlib.Path(logs_dir)
    if not logs_path.exists():
        print(f"[ares_logs] dir not found: {logs_dir}", file=sys.stderr)
        return

    log_files = sorted(logs_path.rglob("*.log")) + sorted(logs_path.rglob("*.jsonl"))
    count = skip = 0
    for f in log_files:
        try:
            for line in f.read_text(errors="replace").splitlines():
                trace = _parse_log_line(line, f.name)
                if trace:
                    if trace["action"] in WALLET_OBS_ACTIONS:
                        skip += 1
                        continue
                    count += 1
                    if not dry_run:
                        yield trace
        except Exception as e:
            print(f"[ares_logs] {f.name}: {e}", file=sys.stderr)

    print(f"[ares_logs] {count} decision traces collected, {skip} wallet_obs skipped")


# ── Chat format converter ──────────────────────────────────────────────────────

def trace_to_chat(trace: dict) -> dict:
    duration = trace.get("duration_ms", "?")
    payload  = trace.get("payload", {})
    target   = (payload.get("target") or payload.get("resource") or
                payload.get("task_type") or "?")
    user = (
        f"Agent: {trace['agent']}\n"
        f"Task kind: {trace['kind']}\n"
        f"Target: {target}"
    )
    assistant = (
        f"Action: {trace['action']}\n"
        f"Outcome: {trace['outcome']}\n"
        f"Duration: ~{duration}ms"
    )
    return {"messages": [
        {"role": "system",    "content": SYSTEM_PROMPT},
        {"role": "user",      "content": user},
        {"role": "assistant", "content": assistant},
    ]}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Phase 28.2 — decision trace collector")
    ap.add_argument("--source",     choices=["vantage_db", "ares_logs", "both"], default="both")
    ap.add_argument("--db",         default="/opt/ares/Vantage/data/vantage.db",
                    help="Path to vantage.db")
    ap.add_argument("--logs",       default="/opt/ares/ares_logs",
                    help="Path to ares_logs/ directory")
    ap.add_argument("--vps",        default="", help="SSH alias — if set, run via ssh")
    ap.add_argument("--dry-run",    action="store_true")
    ap.add_argument("--append",     action="store_true",
                    help="Append to finetune_dataset.jsonl instead of separate file")
    args = ap.parse_args()

    if args.vps:
        # Delegate to VPS where the data lives
        import subprocess
        script = pathlib.Path(__file__).resolve()
        remote_script = f"/tmp/{script.name}"
        print(f"Copying {script.name} to {args.vps}:{remote_script} ...")
        subprocess.run(["scp", str(script), f"{args.vps}:{remote_script}"], check=True)
        cmd = ["ssh", args.vps,
               f"python3 {remote_script} --source {args.source} "
               f"--db {args.db} --logs {args.logs}"]
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"Running on {args.vps} ...")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode == 0:
            print(f"\nTo pull output: scp {args.vps}:{OUT_DIR}/decision_traces.jsonl .")
        return

    all_traces = []

    if args.source in ("vantage_db", "both"):
        for t in collect_vantage(args.db, args.dry_run):
            all_traces.append(t)

    if args.source in ("ares_logs", "both"):
        for t in collect_ares_logs(args.logs, args.dry_run):
            all_traces.append(t)

    if args.dry_run:
        outcomes = {}
        for t in all_traces:
            outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
        print(f"\nDry run — {len(all_traces)} total decision traces")
        print("Outcome distribution:", outcomes)
        return

    # Write raw traces
    TRACES_OUT.write_text("\n".join(json.dumps(t) for t in all_traces) + "\n")
    print(f"\n✅ {len(all_traces)} traces → {TRACES_OUT}")

    # Convert to chat format
    chat_rows = [trace_to_chat(t) for t in all_traces]
    finetune_path = OUT_DIR / "finetune_dataset.jsonl" if args.append else FINETUNE_OUT
    mode = "a" if args.append else "w"
    with open(finetune_path, mode) as f:
        for row in chat_rows:
            f.write(json.dumps(row) + "\n")
    print(f"✅ {len(chat_rows)} chat examples → {finetune_path}")

    # Summary
    outcomes = {}
    for t in all_traces:
        outcomes[t["outcome"]] = outcomes.get(t["outcome"], 0) + 1
    print("\nOutcome distribution:", outcomes)
    total_existing = sum(1 for _ in open(OUT_DIR / "finetune_dataset.jsonl"))
    print(f"Total finetune_dataset.jsonl: {total_existing} examples")


if __name__ == "__main__":
    main()
