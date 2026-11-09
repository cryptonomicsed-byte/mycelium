# mycelium-collector

Watches wallet activity (GMGN smart-money/KOL trades, optionally your own
Helius-tracked wallet list, GeckoTerminal price enrichment) and emits
`observation` traces into a Mycelium substrate — local or remote, no local
Vantage/database coupling required.

See the [tools README](../README.md) for install and
multi-device setup.

## Config

| Var | Default | Purpose |
|---|---|---|
| `MYCELIUM_URL` | `http://127.0.0.1:8811` | substrate HTTP gateway to emit traces into — point this at a remote Contabo/hostinger substrate to collect from anywhere |
| `MYCELIUM_COLLECTOR_DIR` | `~/.mycelium-collector` | this collector's own local state dir |
| `WALLET_INTEL_DB` | `<dir>/wallet_intel.db` | local dedupe/stats registry (separate from the substrate) |
| `GMGN_CLI` | `gmgn-cli` (found on PATH) | GMGN CLI binary; set `GMGN_ENABLED=0` to skip this source entirely |
| `HELIUS_API_KEY` | unset | set to enable the Helius Enhanced Transactions source |
| `TRACKED_WALLETS` | unset | comma-separated addresses to watch via Helius |
| `TRACKED_WALLETS_FILE` | unset | newline-delimited wallet file (merged with `TRACKED_WALLETS`) |
| `GECKOTERMINAL_URL` | GeckoTerminal's public token endpoint | override if you run a proxy/cache |

Every source is independently disable-able — with nothing configured beyond
`MYCELIUM_URL`, the collector still runs (GeckoTerminal is public/no-key;
GMGN needs `gmgn-cli` on PATH; Helius needs a key and a wallet list — either
missing just means that source contributes nothing that cycle, not an error).

## CLI

```
mycelium-collector --once        # single collection cycle, prints a summary
mycelium-collector --daemon 300  # loop every 300s (default)
mycelium-collector --status      # registry snapshot: top wallets/tokens
```

## What changed from the original phone-native version

The original `wallet_intel/collector.py` had two things that don't survive
being installed on a different device, both fixed here:

1. Hardcoded absolute paths (`/opt/ares/wallet_intel`, `/usr/local/bin/gmgn-cli`)
   → env-configurable with working local defaults.
2. Its "Helius" source didn't call Helius at all — it opened
   `/opt/ares/Vantage/data/vantage.db` directly as a read-only SQLite file,
   which only worked because it happened to run on the same box as that
   exact Vantage instance. Replaced with a real Helius Enhanced
   Transactions API call against a configured wallet list.

Not ported: the original's GMGN key-rotation/proxy layer (`gmgn_pool`,
`gmgn_cli_proxy`, `scanner` — undeclared sibling modules outside this
collector's own source, not part of the public tool spec). `GMGN_CLI` can
point at any wrapper script speaking the same CLI interface if you want to
add that back yourself.

**Not yet live-verified**: the Helius source is written against Helius's
documented API shape but wasn't exercised against a real API key in this
environment (no credentials available at build time). GMGN and
GeckoTerminal sources hit real public/CLI interfaces and are the tested
path.
