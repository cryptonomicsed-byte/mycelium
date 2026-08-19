# ares-signal-fusion

Fuses ALL available intelligence — Vantage signal pool, wallet intel
(classified wallets + GMGN tags), market data, council verdicts, Mycelium
miner findings — into ONE ranked, transparent list of the best tokens to
trade. Every pick stores its full component breakdown (which signals, which
wallets, which findings drove the score) so the math is reproducible by
hand. Implements `SIGNAL_FUSION_PROMPT.md` (repo root).

**PAPER ONLY, structurally**: nothing in this module executes trades. Picks
are candidates for the council's normal debate path; LIVE execution stays
behind the council's Risk-veto gates plus a manual user flip.

## Layout

| file | role |
|---|---|
| `signal_fusion.py` | main loop: `--once` / `--daemon` / `--report`, SIGHUP config hot-reload |
| `sources.py` | normalizers (pure, unit-tested) + defensive live fetchers → common schema |
| `scoring.py` | composite score Σ w·S (S_signal/S_wallet/S_council/S_finding/S_market), half-life decay |
| `gates.py` | hard vetoes: liquidity ≥$5k, top-10 <60%, bundler+rat <30%, vol ≥$10k, honeypot/tax, age, 24h dedupe, Sabbath |
| `store.py` | `ares_picks.db`: picks + vetoes + outcome marks (+4h/+24h/+7d) + calibration report |
| `backtest.py` | replay over history when it exists; otherwise forward PAPER tracking IS the backtest |
| `config.json` | ALL weights/thresholds/half-lives — hot-reloaded on SIGHUP, never auto-edited |
| `ares-signal-fusion.service` | systemd unit (`Restart=always`, `ExecReload` sends SIGHUP) |

## Deploy (VPS)

```bash
# from the repo checkout on the VPS
mkdir -p /opt/ares/ares-signal-fusion
cp -r signal_fusion /opt/ares/ares-signal-fusion/signal_fusion
cp signal_fusion/config.json /opt/ares/ares-signal-fusion/
# edit config.json endpoints for the VPS: vantage_base is local :8001 there,
# wallet_registry_db path, mycelium_gateway through the tunnel
cp signal_fusion/ares-signal-fusion.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now ares-signal-fusion
journalctl -u ares-signal-fusion -f
```

Manual run / verification:

```bash
python3 -m signal_fusion.signal_fusion --once --debug   # single pass, prints top picks
python3 -m signal_fusion.signal_fusion --report          # calibration report (≥20 resolved picks)
python3 -m signal_fusion.backtest --windows 12           # replay if history exists
```

The dashboard's `#/picks` view reads these picks through the mycelium
gateway's `/api/picks` proxy — the VPS side must serve `GET /api/picks`
from `ares_picks.db` (`store.PickStore(...).top_picks()` is the row
source; expose it from the :8001 API or a small sidecar the proxy points
at via `MYCELIUM_COUNCIL_BASE`).

## Outcome tracking & self-calibration

Entry price recorded per pick; later runs record price marks at +4h/+24h/+7d
(`outcomes` table, real data — no simulation). After ≥20 resolved picks,
`--report` compares average return per dominant component and suggests
weight adjustments. Suggestions only — `config.json` is never modified by
the engine.

## Tests

`tests/test_signal_fusion.py` (repo root, stdlib unittest) covers the
decay math, every scoring component with hand-recomputable numbers, all
hard gates including the forced-veto case, the store round-trip with
outcome marks, and a full `run_once` over synthetic signals with the clock
pinned. Run: `python3 -m unittest tests.test_signal_fusion -v`.
