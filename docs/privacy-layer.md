# Mycelium Privacy Layer — Design Spec

Date: 2026-09-02
Status: DRAFT (ready for implementation)
Scope: de-identification at the Vantage→Mycelium bridge boundary + substrate-side
guarantees + consent model, so the substrate can "learn from everything" while
users stay anonymous and their data stays private.

---

## 1. Why this exists

The product pitch: users come to Vantage for the social experience, paper/live
trading, Pine scripts and token launches; Mycelium underneath ingests ALL of
that activity, mines emergent patterns (wallet correlation, anomalies,
opportunities), and feeds better signals back to agents. The selling point that
makes this defensible: **learn from everyone, expose no one.**

Today the bridge (Vantage backend/mycelium_bridge.py) POSTs real traces to the
Mycelium gateway (:8811/api/trace, no auth, same-host, fail-soft, value-deduped).
Most emitters carry aggregate or public-onchain data. Two carry identity-linked
data. This spec fixes that at the boundary.

## 2. PII audit of the 7 live emitters

| Emitter | agent | target | Payload identity risk |
|---|---|---|---|
| source_performance | trade_outcome_learner | source NAME | LOW — source may be a KOL handle; aggregate stats only |
| aggregate_winner | aggregate_score | token address | NONE — public onchain |
| platform_leader | platform_leaders | token address | NONE — public onchain |
| narrative_theme_heat | narrative_detection | theme_key | NONE — public mints |
| **wallet_reputation** | wallet_learner | **raw wallet address** | **HIGH — address + display_name (handle) + reasoning text** |
| **verified_social_call** | social_tracker | **raw wallet address** | **HIGH — username + wallet + entry_tx_signature (explorer-reversible)** |

Rule of thumb: token/mint addresses are public onchain data — keep them raw
(they ARE the signal). Wallet addresses, usernames, display names, and tx
signatures are identity-linkable — pseudonymize.

## 3. Pseudonym scheme (HMAC-SHA256, keyed, non-reversible)

```
PSEUDONYM_KEY = os.environ["MYCELIUM_PSEUDONYM_KEY"]  # 32+ random bytes, required

wallet  -> "w_" + HMAC_SHA256(key, "wallet:"  + addr)[:16]
user    -> "u_" + HMAC_SHA256(key, "user:"    + username)[:16]
display -> omitted unless user opted in (see §4), never emitted raw
tx_sig  -> "t_" + HMAC_SHA256(key, "tx:"      + sig)[:16]
```

Properties:
- Deterministic per key: the same wallet always maps to the same pseudonym, so
  wallet-correlation mining works across traces without any reversible mapping.
- Non-reversible without the key: a leaked substrate DB is useless to an
  outsider; even with the key, wallets are not enumerable (input is the addr).
- Prefixed by type so w_/u_/t_ can never collide.
- Key rotation (MYCELIUM_PSEUDONYM_KEY changed) rotates ALL pseudonyms —
  correlation across the rotation boundary breaks. Acceptable tradeoff,
  documented; rotate on suspicion or scheduled (e.g. quarterly).

## 4. Consent model

- New column on the agent/user record: `mycelium_opt_in INTEGER NOT NULL DEFAULT 0`.
- DEFAULT (0) = aggregate-only:
  - wallet_reputation: pseudonymized wallet, NO display_name, NO reasoning,
    keep scores/counts (copy_trade_score, first_buyer_count, ...).
  - verified_social_call: pseudonymized wallet + user, NO username, NO
    entry_tx_signature (hash it), keep mint/symbol/prices/pct_change.
- OPT-IN (1) = full pseudonymized traces (display_name + reasoning + hashed tx
  sigs included, still pseudonymized — never raw).
- UI: one toggle in Vantage settings ("Contribute anonymously to the
  intelligence substrate"), default OFF, plain-language explanation.
- Enforcement at the boundary: the bridge reads the flag from the caller's
  session/agent record. The substrate itself never sees the raw value.

## 5. Enforcement points

1. **Bridge boundary (primary)** — a single `_pseudonymize(payload, agent)`
   transform applied in `_post_trace()`/`post_observation()` before the HTTP
   POST, with per-emitter field rules (audit table §2). One function, every
   emitter inherits it. Fields are transformed by KEY, so unknown future
   payload keys default to pass-through for public data + explicit blocklist
   for identity keys.
2. **Substrate schema** — no PII columns by construction. Trace payloads stay
   opaque JSON; miners operate on pseudonyms. Add a lint/test that asserts no
   raw Solana-address regex (44-char base58) or @handle appears in stored
   traces (fail the test on violation).
3. **Findings publication (A2A → Vantage feed)** — findings (e.g. "3-wallet
   cluster accumulating X") publish pseudonyms only when the source was
   pseudonymized. The publisher (mycelium task 4) must not re-introduce raw
   addresses; deterministic pseudonyms are fine in the feed.
4. **Reasoning text** — strip patterns: base58 addresses (44-char), @handles,
   tx sigs, before emit for aggregate-only users; keep for opt-in.

## 6. Key management

- MYCELIUM_PSEUDONYM_KEY: 32+ bytes from /dev/urandom, stored in the Vantage
  .env (never committed), mirrored in the mycelium gateway env if the gateway
  ever needs to validate pseudonyms (it shouldn't — it's opaque to it).
- Rotation procedure: generate new key → deploy → old traces keep old
  pseudonyms (historical correlation breaks at the boundary — acceptable and
  privacy-positive) → note in changelog.

## 7. Implementation checklist (ordered)

1. [ ] mycelium_bridge: add `_pseudonymize()` + per-emitter field rules + env
      key requirement (fail-soft: if key unset, emit pseudonymized-with-
      ephemeral-salt? NO — if key unset, DROP identity fields entirely, never
      emit raw)
2. [ ] Vantage: agents table migration (`mycelium_opt_in`), settings toggle,
      pass flag into bridge callers (pine.py, degen.py, wallet_learner,
      social_tracker)
3. [ ] mycelium repo: PII lint test (no base58-44 / @handle in stored traces)
4. [ ] A2A publisher (task 4): redaction rule for findings, wire test
5. [ ] Docs: this spec → README; changelog entry

## 8. What this buys the demo

The privacy layer IS a hackathon-worthy deliverable on its own ("new tooling
nobody has tried": a substrate that learns from every trade and wallet on the
platform while being provably unable to expose who did what). It's the
difference between "another analytics dashboard" and "the anonymous brain of
the platform."
