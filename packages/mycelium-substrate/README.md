# mycelium-substrate (compatibility shim)

`mycelium-tools` used to ship its own fork of the substrate core as the
`mycelium_substrate` package. When the tools were merged into this repo
(ticket P2-01) that fork was **dropped** — the real substrate is the
`mycelium` package at the root of this repository.

This package keeps old code working unchanged:

```python
from mycelium_substrate import core, miners, a2a, publish   # == mycelium.core, ...
from mycelium_substrate.cli import cmd_cycle                 # == mycelium.cli.cmd_cycle
```

`mycelium_substrate.<name>` is the *same module object* as `mycelium.<name>`
(not a copy), so module state such as `core.DB_PATH` and `mock.patch`
targets are shared.

Console scripts kept from mycelium-tools:

| Script | Points at |
|---|---|
| `mycelium` | `mycelium.cli:main` |
| `mycelium-mcp` | `mycelium.mcp_server:serve` |
| `mycelium-http` | `mycelium_substrate.http_server:serve` — minimal stdlib trace server (`POST /api/trace`, `GET /api/traces`, `GET /api/status`) on top of `mycelium.core`. The full dashboard is still the Go `gateway/`. |

## Finding the `mycelium` package

The shim needs `mycelium` importable. In order it tries: already importable
→ `$MYCELIUM_REPO` → the repo it lives in (editable install from
`packages/mycelium-substrate`). `install.sh` at the repo root does the
editable install, so this normally just works.
