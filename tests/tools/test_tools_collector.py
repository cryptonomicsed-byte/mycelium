"""Tests for mycelium-collector (U2): registry.py deduplication + update_registry logic."""
import sqlite3
import tempfile

import pytest


def _mem_conn():
    """In-memory SQLite with the registry schema."""
    conn = sqlite3.connect(":memory:")
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


# ── Smoke ──────────────────────────────────────────────────────────────────

def test_import():
    from mycelium_collector import registry, collector, config  # noqa: F401


# ── Registry logic ─────────────────────────────────────────────────────────

def test_update_registry_inserts_wallet():
    from mycelium_collector.registry import update_registry

    conn = _mem_conn()
    trades = [
        {
            "tx": "tx-001",
            "source": "gmgn",
            "wallet": "wallet-AAA",
            "tags": ["smart"],
            "side": "buy",
            "amount_usd": 500.0,
            "token": "mint-XYZ",
            "symbol": "XYZ",
        }
    ]
    update_registry(conn, trades, "2026-01-01T00:00:00Z")

    row = conn.execute("SELECT buys, volume_usd FROM wallets WHERE address='wallet-AAA'").fetchone()
    assert row is not None
    assert row[0] == 1   # buys
    assert row[1] == 500.0


def test_update_registry_deduplicates_tx():
    from mycelium_collector.registry import update_registry

    conn = _mem_conn()
    trade = {
        "tx": "tx-dup",
        "source": "gmgn",
        "wallet": "wallet-BBB",
        "tags": [],
        "side": "buy",
        "amount_usd": 100.0,
        "token": "mint-ABC",
        "symbol": "ABC",
    }
    update_registry(conn, [trade], "2026-01-01T00:00:00Z")
    update_registry(conn, [trade], "2026-01-01T00:01:00Z")  # duplicate

    count = conn.execute("SELECT COUNT(*) FROM seen WHERE tx='tx-dup'").fetchone()[0]
    assert count == 1  # deduped


def test_update_registry_sell_side():
    from mycelium_collector.registry import update_registry

    conn = _mem_conn()
    trades = [
        {
            "tx": "tx-sell",
            "source": "gmgn",
            "wallet": "wallet-CCC",
            "tags": [],
            "side": "sell",
            "amount_usd": 200.0,
            "token": None,
            "symbol": None,
        }
    ]
    update_registry(conn, trades, "2026-01-01T00:00:00Z")

    row = conn.execute("SELECT sells FROM wallets WHERE address='wallet-CCC'").fetchone()
    assert row[0] == 1
