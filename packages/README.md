# mycelium tools (merged from mycelium-tools)

These packages used to live in
[`cryptonomicsed-byte/mycelium-tools`](https://github.com/cryptonomicsed-byte/mycelium-tools).
They were merged here in ticket **P2-01** so the tools ship next to the
substrate they talk to. Nothing was retired: the old repo still exists.

What changed in the move:

- **Substrate core dropped.** mycelium-tools carried its own fork of the
  substrate (`core`, `storage`, `miners`, `apply`, `a2a`, `publish`,
  `sandbox`, `nostr_wire`, `mcp_server`, `cli`). The real one is this repo's
  `mycelium/` package, so the fork was not copied.
- **`mycelium_substrate` is now a shim** (`packages/mycelium-substrate`)
  that re-exports `mycelium.*` under the old name — `from mycelium_substrate
  import core` still works and is literally `mycelium.core`. The one module it
  still carries is `http_server` (`mycelium-http`), the small stdlib trace
  server, which now runs on `mycelium.core`.
- **Tools unchanged**: collector / cycle / picks-client / substrate-tunnel
  are copied as-is.
- `install.sh` and the `systemd/` `--user` templates are at the repo root.

| Tool | Package | What it does |
|---|---|---|
| `mycelium`, `mycelium-mcp`, `mycelium-http` | `mycelium-substrate` (shim) | Substrate CLI / MCP server (= `mycelium.cli` / `mycelium.mcp_server`) and the minimal HTTP trace server |
| `mycelium-collector` | `mycelium-collector` | Wallet-intel collector — GMGN smart-money/KOL trades, optional Helius API pull, GeckoTerminal price enrichment — emits traces into a substrate, local or remote |
| `mycelium-cycle` | `mycelium-cycle` | One self-maintenance cycle: mine + auto-apply → publish (anchor) → A2A-publish findings. Cron/timer-safe |
| `substrate-tunnel` | `substrate-tunnel` | autossh-managed reverse-SSH tunnel to join a device to a shared substrate host |
| `picks-digest` | `picks-client` | Fetches a picks digest from any `GET /api/picks`-shaped service |

Nothing here runs unless you install it and start it yourself; no unit is
enabled by `install.sh`. Each package README has its full env-var reference.

## Install

```bash
./install.sh                       # venv at ~/.venvs/mycelium-tools
# or: ./install.sh /custom/venv/path
# MYCELIUM_INSTALL_UNITS=1 ./install.sh   # also copy systemd --user templates
```

## Quickstart

```bash
source ~/.venvs/mycelium-tools/bin/activate
mycelium init                      # create the local trace DB
mycelium-http &                    # minimal HTTP trace server on :8811
mycelium-collector --once          # one collection cycle
mycelium findings
```

To join a shared substrate instead, set `SUBSTRATE_RELAY_HOST`,
`SUBSTRATE_RELAY_USER`, `SUBSTRATE_SSH_KEY`, run `substrate-tunnel`, and point
`MYCELIUM_URL` at the tunnelled port.

## Tests

```bash
python -m pytest tests/tools
```
(`tests/tools/conftest.py` puts the packages and the repo root on `sys.path`,
so no install is needed to run them.)
