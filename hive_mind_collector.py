#!/usr/bin/env python3
"""
hive_mind_collector.py — Phase 28.2 hive-mind corpus collection.

Collects 5 additional data streams beyond expand_corpus.py's 4 sources:
  1. WorkReceipts       — ARP receipt files (work completion proofs)
  2. SplatCorpus        — Gaussian splat quality metrics from ScarabSwarm
  3. SwarmCoord         — multi-agent coordination events from swarm logs
  4. OduDecision        — Odù oracle decision traces from OSOVM
  5. Reputation         — agent reputation change events from Vantage

Output: hive_mind_traces.jsonl (appended, deduped by trace_id)
Schema per record:
  {
    "trace_id": str,         # sha256[:16] of (kind+action+target_kind+ts_bucket)
    "agent_role": str,
    "kind": str,             # stream name: work_receipt|splat|swarm|odu|reputation
    "action": str,
    "target_kind": str,
    "outcome": str,
    "duration_ms": int,
    "ts_bucket": int,        # unix timestamp rounded to 300s
    "source": str,           # filename / stream origin
    "meta": dict             # stream-specific extra fields
  }

Usage:
  python3 hive_mind_collector.py [--dry-run] [--limit 20000]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Iterator

HOME = Path.home()
MYCELIUM = HOME / "mycelium"
OUTPUT = MYCELIUM / "hive_mind_traces.jsonl"

# ── helpers ───────────────────────────────────────────────────────────────────

def _trace_id(kind: str, action: str, target: str, ts: int) -> str:
    raw = f"{kind}:{action}:{target}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_existing_ids() -> set[str]:
    if not OUTPUT.exists():
        return set()
    ids: set[str] = set()
    with open(OUTPUT) as f:
        for line in f:
            try:
                r = json.loads(line)
                ids.add(r["trace_id"])
            except Exception:
                pass
    return ids


def _ts_bucket(ts: float) -> int:
    return int(ts) // 300 * 300


def _emit(kind: str, action: str, target: str, outcome: str,
          role: str, duration_ms: int, ts: float,
          source: str, meta: dict) -> dict:
    bucket = _ts_bucket(ts)
    return {
        "trace_id": _trace_id(kind, action, target, bucket),
        "agent_role": role,
        "kind": kind,
        "action": action,
        "target_kind": target,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "ts_bucket": bucket,
        "source": source,
        "meta": meta,
    }


# ── stream 1: WorkReceipts ────────────────────────────────────────────────────

def collect_work_receipts() -> Iterator[dict]:
    receipts_dir = HOME / "ARP" / "receipts"
    if not receipts_dir.exists():
        return
    for fp in sorted(receipts_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
            action = data.get("action", "settle")
            outcome = "success" if data.get("verified", True) else "rejected"
            duration_ms = int(data.get("duration_ms", 0))
            ts = float(data.get("timestamp", time.time()))
            role = data.get("principal_role", "agent")
            capability = data.get("capability", "work")
            yield _emit(
                kind="work_receipt",
                action=action,
                target=capability,
                outcome=outcome,
                role=role,
                duration_ms=duration_ms,
                ts=ts,
                source=fp.name,
                meta={
                    "receipt_id": data.get("receipt_id", ""),
                    "f1_score": data.get("f1_score"),
                },
            )
        except Exception:
            pass


# ── stream 2: SplatCorpus ─────────────────────────────────────────────────────

def collect_splat_corpus() -> Iterator[dict]:
    splat_dir = HOME / "ScarabSwarm" / "receipts"
    if not splat_dir.exists():
        return
    for fp in sorted(splat_dir.glob("*.json")):
        try:
            data = json.loads(fp.read_text())
            f1 = float(data.get("f1_score", 0.0))
            outcome = "mint_eligible" if f1 >= 0.777 else "below_threshold"
            ts = float(data.get("timestamp", time.time()))
            yield _emit(
                kind="splat",
                action="gaussian_splat",
                target="sim_receipt",
                outcome=outcome,
                role=data.get("agent_role", "simulator"),
                duration_ms=int(data.get("duration_ms", 0)),
                ts=ts,
                source=fp.name,
                meta={
                    "f1_score": f1,
                    "veil_id": data.get("veil_id", ""),
                    "trajectory_count": data.get("trajectory_count"),
                },
            )
        except Exception:
            pass


# ── stream 3: SwarmCoord ──────────────────────────────────────────────────────

_SWARM_COORD_PATTERN = re.compile(
    r'"event"\s*:\s*"(?P<event>[^"]+)".*?"agent_id"\s*:\s*"(?P<agent>[^"]+)"'
)

def collect_swarm_coord() -> Iterator[dict]:
    swarm_log = HOME / "ScarabSwarm" / "logs" / "swarm.jsonl"
    if not swarm_log.exists():
        return
    with open(swarm_log) as f:
        for line in f:
            try:
                data = json.loads(line)
                event = data.get("event", "")
                if not event:
                    continue
                ts = float(data.get("ts", time.time()))
                action = event.split(".")[-1] if "." in event else event
                role = data.get("role", "swarm_agent")
                outcome = data.get("outcome", "ok")
                yield _emit(
                    kind="swarm",
                    action=action,
                    target="swarm_agent",
                    outcome=outcome,
                    role=role,
                    duration_ms=int(data.get("duration_ms", 0)),
                    ts=ts,
                    source="swarm.jsonl",
                    meta={
                        "agent_id": data.get("agent_id", ""),
                        "swarm_size": data.get("swarm_size"),
                        "consensus": data.get("consensus"),
                    },
                )
            except Exception:
                pass


# ── stream 4: OduDecision ────────────────────────────────────────────────────

def collect_odu_decisions() -> Iterator[dict]:
    traces_dir = HOME / "OSOVM" / "traces"
    if not traces_dir.exists():
        return
    for fp in sorted(traces_dir.glob("*.jsonl")):
        with open(fp) as f:
            for line in f:
                try:
                    data = json.loads(line)
                    opcode = data.get("opcode", "")
                    if not opcode:
                        continue
                    ts = float(data.get("ts", time.time()))
                    outcome = "pass" if data.get("success", True) else "fail"
                    odu = data.get("odu_id", 0)
                    yield _emit(
                        kind="odu",
                        action=opcode,
                        target="osovm_state",
                        outcome=outcome,
                        role=data.get("agent_role", "agent"),
                        duration_ms=int(data.get("duration_us", 0) // 1000),
                        ts=ts,
                        source=fp.name,
                        meta={
                            "odu_id": odu,
                            "gate_score": data.get("gate_score"),
                            "ase_delta": data.get("ase_delta"),
                        },
                    )
                except Exception:
                    pass


# ── stream 5: Reputation ─────────────────────────────────────────────────────

def collect_reputation() -> Iterator[dict]:
    rep_log = HOME / "Vantage" / "logs" / "reputation.jsonl"
    if not rep_log.exists():
        # Try alternate path
        rep_log = HOME / "Vantage" / "backend" / "logs" / "reputation.jsonl"
    if not rep_log.exists():
        return
    with open(rep_log) as f:
        for line in f:
            try:
                data = json.loads(line)
                delta = float(data.get("delta", 0.0))
                action = "rep_increase" if delta >= 0 else "rep_decrease"
                outcome = "above_threshold" if float(data.get("new_score", 0)) >= 0.5 else "below_threshold"
                ts = float(data.get("ts", time.time()))
                yield _emit(
                    kind="reputation",
                    action=action,
                    target="agent_reputation",
                    outcome=outcome,
                    role=data.get("role", "agent"),
                    duration_ms=0,
                    ts=ts,
                    source="reputation.jsonl",
                    meta={
                        "agent_id": data.get("agent_id", ""),
                        "delta": delta,
                        "new_score": data.get("new_score"),
                        "reason": data.get("reason", ""),
                    },
                )
            except Exception:
                pass


# ── stream 6: SpatialCaptures ────────────────────────────────────────────────

def collect_spatial_captures() -> Iterator[dict]:
    splats_dir = HOME / "ScarabSwarm" / "splats"
    if not splats_dir.exists():
        return

    # ── completed manifests ──────────────────────────────────────────────────
    for fp in sorted(splats_dir.glob("*.manifest.json")):
        try:
            data = json.loads(fp.read_text())
            name = data.get("name", fp.stem)
            vertex_count = int(data.get("vertex_count", 0))
            training_iterations = int(data.get("training_iterations", 0))
            outcome = "mint_eligible" if vertex_count > 100_000 else "low_fidelity"
            ts = float(data.get("capture_ts", time.time()))
            duration_ms = training_iterations * 50
            yield _emit(
                kind="spatial_capture",
                action="gaussian_splat_train",
                target="walrus_blob",
                outcome=outcome,
                role="spatial_agent",
                duration_ms=duration_ms,
                ts=ts,
                source=fp.name,
                meta={
                    "name": name,
                    "vertex_count": vertex_count,
                    "gpu_instance_id": data.get("gpu_instance_id", ""),
                    "training_iterations": training_iterations,
                },
            )
        except Exception:
            pass

    # ── pending SOG compression markers ──────────────────────────────────────
    for fp in sorted(splats_dir.glob("*.sog.todo")):
        try:
            # Marker files may be empty or contain minimal JSON; use mtime as ts.
            ts = fp.stat().st_mtime
            name = fp.name.removesuffix(".sog.todo")
            yield _emit(
                kind="spatial_capture",
                action="gaussian_splat_train",
                target="walrus_blob",
                outcome="pending_compression",
                role="spatial_agent",
                duration_ms=0,
                ts=ts,
                source=fp.name,
                meta={
                    "name": name,
                    "vertex_count": 0,
                    "gpu_instance_id": "",
                    "training_iterations": 0,
                },
            )
        except Exception:
            pass


# ── synthetic fallback ────────────────────────────────────────────────────────

_SYNTHETIC_TEMPLATES = [
    ("work_receipt",  "assign",         "job",          "success",      "employer"),
    ("work_receipt",  "verify",         "proof",        "mint_eligible","verifier"),
    ("work_receipt",  "settle",         "escrow",       "success",      "worker"),
    ("splat",         "gaussian_splat", "sim_receipt",  "mint_eligible","simulator"),
    ("splat",         "gaussian_splat", "sim_receipt",  "below_threshold","simulator"),
    ("swarm",         "consensus",      "swarm_agent",  "ok",           "coordinator"),
    ("swarm",         "elect_leader",   "swarm_agent",  "ok",           "swarm_agent"),
    ("odu",           "0x54",           "osovm_state",  "pass",         "agent"),
    ("odu",           "0x55",           "osovm_state",  "pass",         "agent"),
    ("odu",           "0x27",           "osovm_state",  "pass",         "employer"),
    ("reputation",    "rep_increase",   "agent_reputation","above_threshold","witness"),
    ("reputation",    "rep_decrease",   "agent_reputation","below_threshold","adjudicator"),
]


def generate_synthetic(n: int = 5000) -> Iterator[dict]:
    import random
    rng = random.Random(42)
    base_ts = 1_700_000_000.0
    for i in range(n):
        tpl = _SYNTHETIC_TEMPLATES[i % len(_SYNTHETIC_TEMPLATES)]
        kind, action, target, outcome, role = tpl
        ts = base_ts + i * 17.3
        yield _emit(
            kind=kind,
            action=action,
            target=target,
            outcome=outcome,
            role=role,
            duration_ms=rng.randint(10, 5000),
            ts=ts,
            source="synthetic",
            meta={"synthetic": True, "seed_idx": i},
        )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=20_000)
    parser.add_argument("--synthetic-fill", type=int, default=5000,
                        help="synthetic traces to add if real sources are sparse")
    args = parser.parse_args()

    existing = _load_existing_ids()
    print(f"Existing hive_mind_traces: {len(existing)}")

    streams = [
        ("work_receipts",     collect_work_receipts()),
        ("splat_corpus",      collect_splat_corpus()),
        ("swarm_coord",       collect_swarm_coord()),
        ("odu_decisions",     collect_odu_decisions()),
        ("reputation",        collect_reputation()),
        ("spatial_captures",  collect_spatial_captures()),
    ]

    new_records: list[dict] = []
    for name, stream in streams:
        count = 0
        for rec in stream:
            if rec["trace_id"] not in existing:
                existing.add(rec["trace_id"])
                new_records.append(rec)
                count += 1
                if len(new_records) >= args.limit:
                    break
        print(f"  {name}: +{count}")

    # Synthetic fill if real sources are sparse
    real_count = len(new_records)
    if real_count < args.synthetic_fill:
        for rec in generate_synthetic(args.synthetic_fill - real_count):
            if rec["trace_id"] not in existing:
                existing.add(rec["trace_id"])
                new_records.append(rec)

    print(f"New records: {len(new_records)}")

    if args.dry_run:
        print("--dry-run: not writing")
        return

    with open(OUTPUT, "a", encoding="utf-8") as f:
        for rec in new_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Written to {OUTPUT}")


if __name__ == "__main__":
    main()
