# Signal Engine — Backtest Results

**STATUS: PENDING** — run the query below against wallet_intel.db on VPS to populate.

## How to run

```bash
ssh hostinger
cd /opt/ares
python3 - <<'EOF'
import sqlite3, json
conn = sqlite3.connect("wallet_intel/wallet_intel.db")

# Find winning tokens (price_change_pct > 100 within 30min of first buy)
# For each winner, find when each signal fired vs crowd volume arrival
# crowd_volume_arrival = timestamp when volume first exceeded 10x the 5min avg

# Output: for each signal, avg lead time before crowd, hit rate
EOF
```

## Template (fill in after VPS run)

| Signal | Lead time (median min) | Hit rate on winners | False positive rate |
|--------|----------------------|---------------------|---------------------|
| new_pool | TBD | TBD | TBD |
| low_bundler | TBD | TBD | TBD |
| dev_hold | TBD | TBD | TBD |
| holder_curve | TBD | TBD | TBD |
| vol_before_price | TBD | TBD | TBD |
| candle_shape | TBD | TBD | TBD |
| gmgn_smart_buy | TBD | TBD | TBD |
| wallet_cluster | TBD | TBD | TBD |
| **composite ≥55** | TBD | TBD | TBD |

## Calibration notes

- Update `params.json` weights based on actual hit rates
- A signal with hit_rate < 40% or lead_time < 1min should be downweighted
- wallet_cluster is the highest conviction signal historically; weight may increase
