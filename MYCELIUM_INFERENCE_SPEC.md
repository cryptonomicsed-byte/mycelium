# Mycelium Inference Architecture
## Design Specification — No Code Until Reviewed
### Date: 2026-09-14

---

## What This Spec Defines

Three additions to the existing Mycelium architecture. Nothing is replaced.

```
1. larql as the local inference backend for Mycelium miners
2. `direction: i8` field on the finding schema  {-1, 0, +1}
3. The semantic feedback loop:
   If-Script hypothesis → OSOVM execution → Zàngbétò proof
   → Mycelium observation → finding → updated rule → If-Script
```

What is NOT changed: the trace substrate, the mining loop, the SQLite schema
(except one column), the Go gateway, the WASM miner sandboxing.

---

## Audit: What Already Exists vs. What's New

| Concept from thread | Reality | Action |
|---|---|---|
| "Clone microsoft/BitNet" | larql already has production BitNet b1.58 support — GGUF load, I2_S ternary quantization (GGML type 36), native-ternary `/v1/infer` path, ARM64-optimized | **DON'T clone BitNet. Use larql.** |
| Ternary {-1,0,+1} in Mycelium | `direction` exists on Signal objects only. Finding schema has NO direction field. | **Add `direction: i8` to finding schema.** |
| 65,536 compound state space | If-Script `tier_max_odu()` already compiles this. `u16 odu_id` throughout CosmogramState, Field, Archetype. | Already exists. No new code. |
| If-Script as semantic grammar | If-Script IS this. Pest parser → AST. Compiler partial (parsing works, expressions not done). | Not new. Use existing. |
| OSOVM execution engine | 160+ opcodes. Production. | Already exists. |
| Semantic feedback loop | Every piece exists. Zero connections between them. | **Wire the loop — see Section 4.** |

---

## Section 1: larql as Local Inference Backend

### Why larql, not Microsoft BitNet repo

larql (`~/larql/`) already:
- Detects `bitnet-b1.58` architecture in GGUF files
- Validates I2_S ternary quantization (GGML type 36, `--keep-quant` flag)
- Routes to dedicated native-ternary inference path in `larql-inference`
- Exposes `/v1/infer`, `/v1/embed`, `/v1/health` HTTP API
- Runs on ARM64 (Termux/Fold 8 native)

**Adding Microsoft's repo would create two competing local inference systems.**
larql is already the answer.

### How Mycelium miners call larql

Current miner flow (`mycelium/mycelium/core.py`, `apply.py`):
```
trace (text)
    ↓
miner function (Python)
    ↓
pattern matching / heuristics
    ↓
add_finding(title, evidence, suggestion, confidence)
```

After this spec is implemented:
```
trace (text)
    ↓
miner function (Python)
    ↓
POST http://localhost:[larql_port]/v1/infer  ← NEW
    body: { "prompt": classify_prompt(trace), "model": "bitnet-b1.58" }
    ↓
{ "direction": 1, "confidence": 0.82, "extraction": {...} }
    ↓
add_finding(..., direction=1, confidence=0.82)
```

### larql inference request format

```python
# New: larql_client.py in mycelium/mycelium/
def infer_trace(trace_text: str, hypothesis: str) -> dict:
    """
    Returns: { "direction": int, "confidence": float, "extraction": dict }
    direction: -1 (contradicts) | 0 (neutral) | 1 (supports)
    """
    payload = {
        "prompt": CLASSIFY_PROMPT.format(
            hypothesis=hypothesis,
            trace=trace_text
        ),
        "max_tokens": 64,
        "temperature": 0.0   # deterministic classification
    }
    resp = httpx.post(f"{LARQL_BASE_URL}/v1/infer", json=payload, timeout=10.0)
    return parse_ternary_response(resp.json())
```

### Configuration

```python
# config added to mycelium settings:
LARQL_BASE_URL = os.getenv("LARQL_URL", "http://localhost:7780")
LARQL_MODEL    = os.getenv("LARQL_MODEL", "bitnet-b1.58")
LARQL_ENABLED  = os.getenv("LARQL_ENABLED", "false").lower() == "true"
```

**larql is opt-in.** If `LARQL_ENABLED=false` (default), miners run existing
heuristic-only path unchanged. No breaking change to current behavior.

### Fallback chain

```
LARQL_ENABLED=true AND larql reachable  → neural classification
LARQL_ENABLED=true AND larql unreachable → fall back to heuristics (warn, don't crash)
LARQL_ENABLED=false                     → heuristics only (current behavior)
```

---

## Section 2: Finding Schema Extension — `direction` Field

### Current finding schema (core.py lines 78-88, SQLite)

```python
(id, created_ts, miner, confidence, title, evidence, suggestion, state, payload)
```

### Addition: `direction` column

```python
# One new column in findings table:
direction INTEGER DEFAULT 0   -- {-1, 0, +1}
```

```python
# Updated add_finding() signature:
def add_finding(title, evidence, suggestion, confidence,
                direction=0,    # NEW — ternary: -1 | 0 | +1
                payload=None):
```

### Semantics

```
direction = +1   supports / activate / increase / correct
direction =  0   neutral / insufficient evidence / hold
direction = -1   contradicts / inhibit / decrease / incorrect
```

This is NOT a replacement for `confidence`. They are orthogonal:
- `confidence: float` = how strongly does the miner believe this?
- `direction: i8`     = which way does the evidence point?

Example: a finding can be high-confidence AND directionally negative:
```
direction=-1, confidence=0.94
"This pattern STRONGLY INDICATES the hypothesis is WRONG"
```

### Migration

One `ALTER TABLE` if the DB already exists:
```sql
ALTER TABLE findings ADD COLUMN direction INTEGER DEFAULT 0;
```

Existing miners that don't pass `direction` default to 0 (neutral). No breaking change.

### Finding wire format (extended)

```json
{
  "id": "f-1842",
  "miner": "wallet_intel",
  "title": "wallet_cluster_accumulation",
  "direction": 1,
  "confidence": 0.82,
  "evidence": ["..."],
  "suggestion": "...",
  "state": "open",
  "payload": {}
}
```

---

## Section 3: T0–T4 Precision Model (Design Principle)

This is a cross-ecosystem principle, not a code change. Every subsystem should
choose the cheapest precision that preserves correctness for its operation.

```
T0  {-1, 0, +1}     Ternary directional state
                    Used by: Mycelium findings, ternary evidence aggregation
                    Maps to: BitNet b1.58 weight representation in larql

T1  INT8            Compact counters and signals
                    Used by: larql activations (8-bit), VCP telemetry signals

T2  FP16 / BF16     Neural inference intermediate computation
                    Used by: larql embeddings, OSOVM soft scoring

T3  FP32            Simulation, statistics, probability accumulation
                    Used by: OSOVM physics, Blocksim MuJoCo, Mycelium confidence

T4  FP64            High-precision accounting, cryptography
                    Used by: ASE token accounting, Zàngbétò receipt hashes,
                              OSOVM financial settlement
```

**Aggregation rule:** when multiple findings are combined, promote to the minimum
precision that preserves the result. Ternary voting can stay T0 unless probabilities
are needed, then promote to T3.

### Ternary evidence aggregation (multi-agent)

Multiple agents contributing to the same hypothesis:

```
Vantage agent         direction=+1, confidence=0.91
Wallet miner          direction=+1, confidence=0.74
Market miner          direction= 0, confidence=0.50
OSOVM simulation      direction=-1, confidence=0.63
Witness node          direction=+1, confidence=0.88
─────────────────────────────────────────────────
weighted_direction = Σ(direction_i × confidence_i) / Σ(confidence_i)
                   = (+0.91 + 0.74 + 0 - 0.63 + 0.88) / (0.91+0.74+0.50+0.63+0.88)
                   = 1.90 / 3.66
                   = +0.519  →  direction=+1 (threshold: |x| > 0.3)
```

This aggregation lives in Mycelium, not in any single agent.

---

## Section 4: The Semantic Feedback Loop

This is the most architecturally significant part. Every component exists;
the connections do not.

### The loop

```
If-Script
    │  hypothesis / rule authored in If-Script grammar
    │  e.g.: IF wallet_cluster AND confidence > 0.7 THEN emit_signal
    ▼
OSOVM
    │  executes the rule against current state (160+ opcodes)
    │  produces an execution result (success/fail/value)
    ▼
Zàngbétò receipt (ARP ActionReceipt, ReceiptKind::Compute)
    │  proves the execution happened
    │  receipt_hash anchored on-chain
    ▼
Mycelium observation
    │  Mycelium miner watches for execution receipts matching this rule
    │  larql classifies: did the predicted outcome occur?
    ▼
Finding
    │  direction=+1  (prediction was correct)
    │  direction=-1  (prediction was wrong)
    │  confidence=0.xx
    ▼
Pattern accumulation
    │  Mycelium accumulates direction+confidence across many executions
    │  sufficient evidence → new skill / rule update
    ▼
Updated rule → back to If-Script
```

### What each component does in the loop

```
If-Script   → authored hypothesis / rule (existing — compiler partial)
OSOVM       → executes rule (existing — 160+ opcodes)
Zàngbétò    → receipt of execution (existing type, not wired to this loop)
ARP         → ActionReceipt wrapping (gaps #23-24 — think/act not wrapped yet)
Mycelium    → observes outcome + produces finding (existing — apply loop one-way)
larql       → classifies whether outcome matched prediction (gap #42 — not wired)
```

### The three wiring gaps this loop requires

1. **ARP think/act wrapping** (gaps #23-24): OSOVM execution results must produce
   ARP `ActionReceipt` with `ReceiptKind::Compute`. These receipts become the
   "execution happened" signals Mycelium observes.

2. **larql ↔ Mycelium** (gap #42): Mycelium miner calls larql `/v1/infer` to
   classify whether the actual outcome matched the predicted direction.
   This closes the one-way apply loop into a feedback loop.

3. **Rule update path** (gap #55 — skill auto-application): When Mycelium
   accumulates sufficient directional evidence, it emits an updated skill/rule
   that If-Script can load. The auto-application mechanism exists partially
   (`generated-skills/`) but isn't wired back to If-Script.

### Minimum viable feedback loop (Phase 1)

Don't wire the full loop at once. Implement in this order:

```
Phase 1 — Instrument (no If-Script/OSOVM changes needed yet)
    Gap 57: Add direction field to finding schema
    Gap 42: Wire larql as opt-in miner backend (LARQL_ENABLED=false default)
    Test: run a miner manually with larql enabled, verify direction field populated

Phase 2 — Close the OSOVM→Mycelium path
    Gap 23-24: ARP wrapping for think/act
    Result: OSOVM execution receipts flow into Mycelium as observations

Phase 3 — Close the Mycelium→If-Script path
    Gap 55: Skill auto-application spec + implementation
    Result: accumulated findings → updated If-Script rules (full loop)
```

---

## Section 5: Edge Deployment — Fold 8 / ARM64

### larql on ARM64 (Termux)

larql already runs on ARM64 with CPU/OpenBLAS backend (Metal is Apple-only).
BitNet b1.58 at 2.4B parameters: estimated ~1.5-3 GB RAM, acceptable on Fold 8.

Deployment:
```
1. larql server running locally: larql-server --port 7780 --model bitnet-b1.58.gguf
2. Mycelium with LARQL_ENABLED=true, LARQL_URL=http://localhost:7780
3. Mycelium miners run inference locally — no cloud LLM needed for classification
```

### The edge intelligence node

```
Fold 8
  ├── Omo-Koda2 agent kernel (:7777)
  ├── Vantage client (X-Agent-Key)
  ├── larql server (:7780) — BitNet b1.58 local inference
  ├── Mycelium client — traces + local finding extraction
  ├── minipae — local NIP-AE memory
  └── Meshtastic (omokoda-mesh-firmware) — offline mesh
           │
           ▼ (when internet available)
       Mycelium collective
           ↓
       ecosystem-wide learning
```

Every device contributes structured findings (direction + confidence), NOT raw
model weights or tensors. The swarm communication layer stays small.

---

## Section 6: Fine-Tuning Target Update

Gap #25 (Mycelium fine-tuning) should target **BitNet b1.58 GGUF** specifically
because larql serves it natively on ARM64 via the native-ternary inference path.

Updated fine-tuning pipeline:
```
2,949 traces / 82 findings (existing, privacy-verified)
    ↓
QLoRA fine-tuning on BitNet b1.58 base
    (instead of generic Qwen/Llama 3B)
    ↓
Export to GGUF with I2_S ternary quantization (--keep-quant)
    ↓
Deploy to larql-server on ARM64
    ↓
Mycelium miners call it locally
```

Reasoning: a BitNet-family fine-tuned model will have dramatically lower RAM
and inference latency on ARM64 than a full FP16 Llama/Qwen 3B, and larql's
native-ternary path is already optimized for it.

---

## What NOT to Build

| Proposed | Reason |
|---|---|
| Clone microsoft/BitNet | larql already does this production-quality |
| New 65,536 state system | If-Script `tier_max_odu()` already compiles it |
| New If-Script role | It IS the semantic grammar — just needs expression parsing completed |
| Tensor-level swarm communication | Findings with direction+confidence are sufficient — keep messages small |
| Merging larql into Mycelium | Keep them separate; Mycelium calls larql over HTTP (opt-in) |
| Hardcoding 65,536 semantic meanings | Let Mycelium discover relationships; only 256 primitives need authoring |

---

## Implementation Order (No Code Until Reviewed)

```
Phase 1 (instrumentation — no breaking changes):
    Gap 57: direction field in finding schema (core.py, 1 SQL column + signature update)
    Gap 42: larql_client.py in mycelium/ (opt-in via LARQL_ENABLED env var)

Phase 2 (loop closure — requires gaps 23-24 first):
    ARP wrapping for think/act in Omo-Koda2
    Mycelium receipt watcher (new miner domain: "execution-outcomes")

Phase 3 (full loop — requires gap 55 spec first):
    Skill auto-application wired to If-Script rule loader
    Feedback closes: finding → updated rule → If-Script execution

Phase 4 (fine-tuning):
    Gap 25: Run QLoRA on BitNet b1.58 base with 2,949 traces
    Deploy GGUF to larql-server on ARM64
    Validate: Mycelium miner inference latency < 500ms on Fold 8
```

---

## Open Questions

1. **larql port:** Is 7780 available on Fold 8, or should it be configured dynamically?
2. **Miner granularity:** Should every miner call larql, or only specific high-value domains (wallet-intel, signal-quality)?
3. **Ternary aggregation threshold:** |weighted_direction| > 0.3 to snap to +1/-1, else 0 — is this the right threshold?
4. **Receipt miner domain:** The new "execution-outcomes" miner domain that watches ARP receipts — should it live in Mycelium core or as a new WASM miner module?
