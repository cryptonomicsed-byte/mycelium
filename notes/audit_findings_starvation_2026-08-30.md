# AUDIT — Mycelium findings starvation (2026-08-30)

Scope: `ares-mycelium-cycle.timer` / `mycelium.cli cycle`, all 6 miner domains
(`mycelium/miners/`). Triggered by production logs showing `new_findings: 0`
on every single mining cycle observed.

## Summary

Two separate, unrelated root causes, both real, both now fixed or honestly
explained:

1. **wallet-intel domain** — a real bug, fixed outside this repo (systemd
   config only). Real wallet_buy/wallet_sell data was being silently lost
   into an orphaned server.
2. **agent-ops domain** — not a bug. A structural trace-kind mismatch: the
   four generic miners only read `kind="tool_call"` traces, and the only
   real `tool_call` traces in the substrate can never satisfy any of their
   thresholds by construction. Fixed by adding two new domain-specific
   miners rather than forcing the generic ones to reinterpret data they
   weren't designed for.

`signal_quality` remains genuinely empty — its own docstring already
documents that its `action="signal_post"` trace contract has no real emitter
anywhere in this codebase yet.

## F1 — wallet-intel: real traces sent to a dead server (FIXED)

`ares-wallet-intel.service`'s systemd unit hardcoded
`MYCELIUM_URL=http://10.88.0.2:8811`. `10.88.0.2` is not a local address —
`ip route get 10.88.0.2` resolves it via `wg0` (WireGuard), i.e. a different
machine entirely. Curling it directly confirmed a real server answering
(`Server: mycelium-substrate-http/0.1 Python/3.12.3`, HTTP 201) — an orphaned
prototype implementation, not the real Rust gateway
(`/opt/ares/ares-signal-fusion/gateway-src/mycelium-gateway`, which binds
`127.0.0.1:8811`/`0.0.0.0:8811` per its own systemd unit).

`collector.py` (the daemon that emits `wallet_buy`/`wallet_sell`) reads
`MYCELIUM_URL`. A sibling script, `scanner.py` (emits `wallet_found` role
discoveries, the same daemon process), reads a **different** env var name —
`MYCELIUM_GATEWAY` — which was never overridden, so it correctly defaulted to
`http://127.0.0.1:8811` and worked the whole time. This exactly explains the
real observed data: 40 real `wallet_found` traces existed, zero
`wallet_buy`/`wallet_sell` ever did, despite `collector.py`'s own logs
claiming "emitted 100 traces to mycelium" every ~5 minutes.

**Fix**: corrected the systemd unit's `MYCELIUM_URL` to
`http://127.0.0.1:8811`, backed up the original unit
(`/etc/systemd/system/ares-wallet-intel.service.bak-*`), restarted the
service.

**Verified real before/after**:
| | before fix | after 1 real cycle |
|---|---|---|
| `wallet_buy` traces | 1 (manual test) | 73 |
| `wallet_sell` traces | 0 | 92 |
| mining cycle `new_findings` | 0 (every prior run) | 6 |

Findings produced were real and legible: `"Money flow: 5 tokens tracked, top
= WSOL (5 wallets)"`, multiple `"Buy burst: hit 3 tokens in <10min"`.
`ares-wallet-intel.service`: 0 restarts, active/running post-fix.

## F2 — agent-ops: structural trace-kind mismatch (FIXED differently)

`recurring_workflow`/`anomaly`/`cross_agent`/`opportunity`
(`mycelium/miners/__init__.py`) all filter on `t["kind"] == "tool_call"`.
Real data in the substrate:

```
signal_fusion|observation|81       (fusion_pick)
council|decision|52                (debate_verdict)
mycelium-cron|tool_call|43         (cycle -- the ONLY tool_call traces)
wallet_intel|observation|40        (wallet_found)
signal_fusion|workflow_end|24      (fusion_run)
signal_fusion|workflow_start|23    (fusion_run)
```

The 43 `tool_call` traces are `mycelium-cron|cycle|success` — one agent, one
action, 100% success, **one unique session per trace** (verified: every
`cycle-<epoch>` session has exactly 1 row). This makes all four miners
mathematically incapable of ever firing on this input:

- `recurring_workflow`/`opportunity` need a repeated 2/3-action *sequence
  within one session* — impossible with 1 action per session.
- `anomaly` needs ≥50% failure rate over ≥4 samples — 0% failures, always.
- `cross_agent` needs ≥2 distinct agents failing the same action — only 1
  agent ever produces `tool_call` traces at all.

The real, information-rich traces that DO exist (`fusion_pick`,
`debate_verdict`, `wallet_found`) are never `kind="tool_call"`, so these four
miners never even look at them.

### Option considered and rejected: broaden the kind filter

Checked whether reusing the generic miners on `observation`/`decision` kinds
would be honest, by inspecting the real data shape first:

- `signal_fusion`'s `session` is the **literal constant string `"fusion"`**
  across its entire lifetime (not one session per run, unlike
  `mycelium-cron`'s `cycle-<epoch>`). A sequence miner would treat weeks of
  interleaved, unrelated fusion cycles as one giant "session" — structurally
  wrong, not merely imprecise.
- `council`'s real `outcome` column is `"success"` / `"partial"` — the
  literal string `"failure"` `anomaly()`/`cross_agent()` check for never
  appears. Treating `"partial"` (an ABSTAIN from insufficient conviction — a
  correct outcome) as a failure would be asserting a meaning the data does
  not actually have.

Rejected for both reasons: this would either silently produce vacuous
findings or require inventing a semantic mapping not supported by the data.

### Fix: two new domain-specific miners (the registry's own documented pattern)

`registry.py`'s own docstring states the intended extension path: *"Adding
domain #3 means: write its miners in their own module, call
register_domain()/register_miner(), done."* Already used for `wallet-intel`,
`signal-quality`, `trade-source-performance`. Followed the same pattern for
two more:

- **`mycelium/miners/fusion_ops.py`** — `fusion_veto_saturation`. Reads
  `signal_fusion`'s real `workflow_end`/`fusion_run` payload
  (`tokens_seen`/`vetoes`/`picks`). Threshold (avg ratio ≥ 0.95 over ≥5 real
  runs) set from real observed data: 25 real completions on this host
  measured a vetoes/tokens_seen ratio of **97.9%–100%, avg 99.2%**, across
  every single run — not a guessed number.
- **`mycelium/miners/council_quality.py`** — `council_persistent_abstain`.
  Reads `council`'s real `debate_verdict` payload
  (`symbol`/`passed`/`conviction`). Fires when a symbol has ≥5 real verdicts
  in the window and **zero** passes. Real calibration data: `ANSEM` had been
  debated 29× with 0 passes ever; `RICHAPE` 29× with only 2 passes (correctly
  excluded — the miner's own `passes > 0` check works as intended).
- `mycelium/miners/__init__.py`: two new `from . import ... # noqa` lines at
  the bottom, same shape as the three existing plugin imports. No changes to
  `core.py`/`storage.py`/`apply.py`/`sandbox.py`/`cli.py`/`mcp_server.py`.

**Verified real before/after**:
- Both miner functions unit-tested directly against a read-only copy of the
  real production DB *before* being wired into the registry — 1 real finding
  each, matching the calibration data above exactly.
- After wiring: `list_domains()` → all 6 domains register cleanly.
- Real production cycle (`mycelium.cli cycle`): `miners_run` 9 → 11,
  `sandbox_errors: 0`. Both new findings persisted:
  - `fusion_veto_saturation`: *"Fusion veto rate 99.2% over last 25 runs (86
    total picks)"*
  - `council_persistent_abstain`: *"Council never reaches conviction on
    ANSEM (29x debated, 0 passes)"*
- `ares-mycelium-cycle.timer` confirmed still `active`, will pick up both new
  miners on its normal 5-minute schedule automatically.

## F3 — signal_quality: genuinely unbuilt, not a bug (NOT changed)

`signal_quality.py`'s own module docstring already states this honestly:
requires `action="signal_post"` traces (`kind` in `decision`/`observation`),
and *"nothing in this codebase emits matching traces yet... A future
integration point (a Buzz-posted trading call, a GMGN/social comment feed, a
manual /signal-post tool) emits these."* Confirmed via direct query: zero
`signal_post` traces exist anywhere in the substrate. No fix applied — there
is genuinely nothing to fix; this is an intentionally-unbuilt integration
point, not a starvation bug.

## Git history

This component (`/opt/ares/mycelium-gateway/pysrc`) had never been
version-controlled before this investigation. Initialized a local git repo
scoped to the real Python source only (excludes `mycelium.db`,
`provenance_key.json`, `chain_state.jsonl`, and the `web/` dashboard build —
none of which belong in source control). Two commits:

1. `947fd8e` — baseline import of the pre-existing, already-running code.
2. `9ea0764` — the real fix (F2's two new miner files + the `__init__.py`
   wiring), with the full investigation writeup in the commit message.

F1's fix (the `MYCELIUM_URL` systemd unit correction) lives in
`/etc/systemd/system/ares-wallet-intel.service`, outside this repo's scope;
the original unit is backed up alongside it
(`ares-wallet-intel.service.bak-*`).
