# Whale Radar — Buy-Cluster Event Schema

A "buy cluster" fires when ≥N watchlist wallets buy the same token within a time window.
This is the Lane 2 conviction signal fed into the Hub score.

## ClusterEvent object

```json
{
  "event": "buy_cluster",
  "token_address": "string",
  "symbol": "string",
  "cluster_size": 4,
  "wallets": [
    {
      "address": "string (pseudonymized: w_xxxxxxxx)",
      "tag": "alpha_wallet",
      "conviction": 0.85,
      "buy_ts": "2026-09-17T11:58:00Z",
      "buy_volume_usd": 1200.0
    }
  ],
  "first_buy_ts": "2026-09-17T11:55:00Z",
  "last_buy_ts": "2026-09-17T11:58:00Z",
  "window_minutes": 10,
  "total_volume_usd": 4800.0,
  "radar_score": 88,
  "detected_at": "2026-09-17T11:58:30Z"
}
```

## Radar score formula

```
radar_score = clamp(
  (cluster_size / 5) * 40 +          # up to 40 pts for cluster size (5+ wallets = max)
  avg(wallet.conviction) * 40 +       # up to 40 pts for wallet quality
  (total_volume_usd / 10000) * 20,    # up to 20 pts for volume (10k = max)
  0, 100
)
```

## Detection thresholds

| Parameter | Value | Notes |
|-----------|-------|-------|
| min_cluster_size | 3 | minimum watchlist wallets buying same token |
| window_minutes | 10 | buy timestamps must all be within this window |
| min_wallet_conviction | 0.50 | filter out low-quality watchlist entries |
| min_volume_per_wallet | $200 | ignore dust buys |

## Watchlist derivation

See `radar/watchlist.json` — STATUS: PENDING VPS run.

Derive from:
1. wallet_intel.db WHERE role IN ('alpha_wallet', 'smart_degen', 'first_buyer')
2. GMGN smart money / KOL track
3. Vantage alpha_wallets pool (signal_fusion sources.py)

Ranked by: win_rate_est × conviction × recency_decay
