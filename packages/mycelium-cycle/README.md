# mycelium-cycle

Chains one full Mycelium self-maintenance cycle for cron/systemd timers:
mine (sandboxed) + auto-apply → publish (checkpoint/Gitea anchor) → A2A
publish findings to the Vantage feed. Each step is failure-isolated and
reported in the summary rather than aborting the whole cycle.

Was a Fold4-only cron script (`scripts/cron_cycle.sh`) hardcoded to
`/data/data/com.termux/files/home/mycelium/mycelium/cli.py` — now a real
console script, installable anywhere `mycelium-substrate` is installed.

See the [tools README](../README.md) for install and
multi-device setup, and the `mycelium-cycle.timer` systemd template.

## Usage

```
mycelium-cycle                  # silent unless something happened (cron-safe)
mycelium-cycle --verbose        # always print the full summary
mycelium-cycle --a2a-limit 5    # publish up to 5 findings per cycle (default 3)
```

Uses `mycelium-substrate`'s own config (`MYCELIUM_DB`, `GITEA_*`,
`NOSTR_*`, `VANTAGE_URL`/`VANTAGE_KEY`) — no separate config of its own.
