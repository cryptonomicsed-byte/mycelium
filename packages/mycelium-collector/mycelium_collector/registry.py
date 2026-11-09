"""Local dedupe/stats registry -- this collector's own small SQLite state.
Separate from (and much smaller than) the substrate itself; the substrate
doesn't need to know about wallets/tokens, only the traces this emits."""
import json
import sqlite3

from . import config


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS seen(tx TEXT PRIMARY KEY, source TEXT, emitted_at TEXT);
    CREATE TABLE IF NOT EXISTS wallets(
        address TEXT PRIMARY KEY, tags TEXT, buys INTEGER DEFAULT 0, sells INTEGER DEFAULT 0,
        volume_usd REAL DEFAULT 0, distinct_tokens INTEGER DEFAULT 0,
        edge REAL, first_seen TEXT, last_seen TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS token_stats(
        mint TEXT PRIMARY KEY, symbol TEXT, distinct_wallets INTEGER DEFAULT 0,
        buy_volume REAL DEFAULT 0, first_buy_ts TEXT, first_buyers TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS wallet_tokens(
        wallet TEXT, mint TEXT, first_buy_ts TEXT, PRIMARY KEY(wallet, mint));
    CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
    """)
    conn.commit()
    return conn


def update_registry(conn, trades, now_iso: str) -> None:
    for t in trades:
        if t["tx"]:
            conn.execute("INSERT OR IGNORE INTO seen(tx, source, emitted_at) VALUES (?,?,?)",
                         (t["tx"], t["source"], now_iso))
        w = t["wallet"]
        if not w:
            continue
        conn.execute("""INSERT INTO wallets(address, tags, buys, sells, volume_usd,
                        distinct_tokens, first_seen, last_seen, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(address) DO UPDATE SET
                          tags=excluded.tags, buys=wallets.buys+excluded.buys,
                          sells=wallets.sells+excluded.sells,
                          volume_usd=wallets.volume_usd+excluded.volume_usd,
                          last_seen=excluded.last_seen, updated_at=excluded.updated_at""",
                     (w, json.dumps(t["tags"]),
                      1 if t["side"] == "buy" else 0, 1 if t["side"] == "sell" else 0,
                      t["amount_usd"], 0, now_iso, now_iso, now_iso))
        if t["token"] and t["side"] == "buy":
            conn.execute("INSERT OR IGNORE INTO wallet_tokens(wallet, mint, first_buy_ts) VALUES (?,?,?)",
                         (w, t["token"], now_iso))
            conn.execute("""INSERT INTO token_stats(mint, symbol, distinct_wallets, buy_volume,
                            first_buy_ts, first_buyers, updated_at)
                            VALUES (?,?,1,?,?,?,?)
                            ON CONFLICT(mint) DO UPDATE SET
                              symbol=excluded.symbol, buy_volume=token_stats.buy_volume+excluded.buy_volume,
                              updated_at=excluded.updated_at""",
                         (t["token"], t["symbol"], t["amount_usd"], now_iso,
                          json.dumps([w]), now_iso))
    conn.execute("""UPDATE wallets SET distinct_tokens =
                    (SELECT COUNT(*) FROM wallet_tokens wt WHERE wt.wallet = wallets.address)""")
    conn.commit()
