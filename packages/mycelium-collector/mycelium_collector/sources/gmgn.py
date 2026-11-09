"""GMGN smart-money/KOL trade source -- plain `gmgn-cli` subprocess call,
found on PATH by default (override via GMGN_CLI). Ban-cooldown state lives
in this collector's own local registry, not an external module."""
import json
import shutil
import subprocess
import time

from .. import config
from ..log import log


def gmgn_allowed(conn) -> bool:
    row = conn.execute("SELECT v FROM state WHERE k='gmgn_ban_until'").fetchone()
    if not row:
        return True
    try:
        return time.time() > float(row[0])
    except Exception:
        return True


def _mark_ban(conn, err: str) -> None:
    until = time.time() + 900  # 15 min default cooldown; gmgn-cli's own
    # error text sometimes carries a reset hint but its format isn't
    # stable enough to parse reliably here -- a fixed cooldown is the
    # safe, portable choice for a tool that has to work on any device.
    conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_until', ?)", (str(until),))
    conn.commit()
    log(f"  [gmgn] banned — resuming {time.strftime('%H:%M:%S', time.localtime(until))}: {err[:150]}")


def gmgn_trades(kind: str, conn) -> list:
    """smartmoney|kol trades via `gmgn-cli track <kind> --chain sol --limit 50 --raw`."""
    if not config.GMGN_ENABLED:
        return []
    cli_path = shutil.which(config.GMGN_CLI) or config.GMGN_CLI
    try:
        out = subprocess.run(
            [cli_path, "track", kind, "--chain", "sol", "--limit", "50", "--raw"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        log(f"  [gmgn] {config.GMGN_CLI!r} not found on PATH -- set GMGN_CLI or GMGN_ENABLED=0")
        return []
    except Exception as e:
        log(f"  [gmgn] {kind} exec error: {e}")
        return []

    if out.returncode != 0:
        err = out.stderr or out.stdout or ""
        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            _mark_ban(conn, err)
        else:
            log(f"  [gmgn] {kind} exit {out.returncode}: {err[:150]}")
        return []
    try:
        data = json.loads(out.stdout)
    except Exception:
        log(f"  [gmgn] {kind} bad JSON")
        return []

    items = data.get("list", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    trades = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tok = it.get("base_token") or {}
        mi = it.get("maker_info") or {}
        ts = it.get("timestamp") or 0
        trades.append({
            "tx": it.get("transaction_hash") or "",
            "wallet": it.get("maker") or "",
            "side": (it.get("side") or "").lower(),
            "token": it.get("base_address") or "",
            "symbol": tok.get("symbol") or "?",
            "amount_usd": float(it.get("amount_usd") or 0),
            "price_usd": it.get("price_usd"),
            "ts": float(ts) if ts else 0,
            "tags": mi.get("tags") or [kind],
            "source": f"gmgn:{kind}",
        })
    return trades
