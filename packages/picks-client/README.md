# picks-client

Fetches and prints a picks digest from any service exposing `GET
/api/picks` — config-driven, no hardcoded host.

**Built from spec, not ported** (see `cli.py` docstring) — the original
phone-side script wasn't reachable from the build environment. The plan
already described this piece as "already mostly universal," so this is a
straightforward, honest reimplementation of the same shape.

## Config

| Var / flag | Default | Purpose |
|---|---|---|
| `PICKS_URL` / `--url` | `http://127.0.0.1:8003/api/picks` | picks service endpoint |
| `PICKS_TIMEOUT` / `--timeout` | `15` | request timeout, seconds |

## Usage

```
picks-digest                              # formatted digest
picks-digest --url http://relay:8003/api/picks
picks-digest --json                       # raw JSON passthrough
```

Exit code is non-zero on fetch failure — safe to alert on in a cron/timer.
