# substrate-tunnel

Join any device — VPS, desktop, Termux phone — to a shared Mycelium
substrate host with one command: an autossh-managed reverse SSH tunnel,
auto-reconnecting on connection loss.

**Built from spec, not ported** — see the module docstring in `cli.py`.
The original Fold4 `council-tunnel` script wasn't reachable from the build
environment; diff against it before treating this as a drop-in phone
replacement.

## Config

| Var / flag | Default | Purpose |
|---|---|---|
| `SUBSTRATE_RELAY_HOST` / `--host` | *(required)* | the shared substrate host to tunnel to |
| `SUBSTRATE_RELAY_USER` / `--user` | unset | SSH user on the relay |
| `SUBSTRATE_REMOTE_PORT` / `--remote-port` | `8811` | port to bind on the relay host |
| `SUBSTRATE_LOCAL_PORT` / `--local-port` | `8811` | local port being forwarded (your mycelium-substrate's `MYCELIUM_ADDR` port) |
| `SUBSTRATE_SSH_KEY` / `--ssh-key` | unset | SSH private key path |
| `SUBSTRATE_KEEPALIVE_INTERVAL` | `15` | seconds between SSH keepalive probes |
| `SUBSTRATE_KEEPALIVE_COUNT_MAX` | `3` | missed probes before autossh reconnects |

## Usage

```
substrate-tunnel --host YOUR_RELAY_IP --user youruser --ssh-key ~/.ssh/id_ed25519
substrate-tunnel --dry-run --host YOUR_RELAY_IP   # print the autossh command, don't run it
```

Requires `autossh` on PATH (`apt install autossh` / `brew install
autossh` / `pkg install autossh` on Termux) — checked at real-run time,
not required for `--dry-run`.

Runs in the foreground and `exec`s into `autossh` (no wrapper process left
behind) — pair with the `substrate-tunnel.service` systemd template
(`Restart=always`) so the supervisor handles respawn, not this tool.
