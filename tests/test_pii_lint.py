"""PII lint test — asserts no raw Solana addresses or @handles appear in
stored traces (privacy-layer.md §5, step 3).

Scans the live mycelium.db for violations in the traces and findings tables.
Fails if any raw wallet address (44-char base58) or @-prefixed handle
appears in stored event payload/action/target fields.

Run: pytest tests/test_pii_lint.py -v
"""
import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "mycelium.db"

# Only lint entries created after the privacy layer was deployed (2026-09-17).
# Historical data has known violations from before pseudonymization was wired.
PRIVACY_EPOCH = "2026-09-17"

# Solana addresses: base58 chars, exactly 32-44 chars (most are 44)
_WALLET_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
# @handle pattern
_HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,50}")

# Known-safe prefixes — these ARE pseudonyms or public data, not PII
_PSEUDONYM_PREFIXES = ("w_", "u_", "t_", "x_")
# Public onchain token mints (44-char) are allowed if they appear in
# the token_address / mint key specifically — we only check identity-
# linked fields. But for simplicity we flag ALL raw 44-char b58 strings
# that are NOT already pseudonymized (i.e. don't start with a prefix).


def _is_pseudonym(value: str) -> bool:
    return any(value.startswith(p) for p in _PSEUDONYM_PREFIXES)


def _scan_string(s: str) -> list[str]:
    violations = []
    for match in _WALLET_RE.finditer(s):
        candidate = match.group(0)
        if not _is_pseudonym(candidate) and len(candidate) == 44:
            violations.append(f"raw-wallet:{candidate[:8]}…")
    for match in _HANDLE_RE.finditer(s):
        violations.append(f"raw-handle:{match.group(0)}")
    return violations


def _scan_payload(payload) -> list[str]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return _scan_string(payload)
    if isinstance(payload, dict):
        violations = []
        for k, v in payload.items():
            if k in ("token_address", "mint", "contract_address"):
                # public onchain data — allowed raw
                continue
            violations.extend(_scan_string(str(v)))
        return violations
    return []


def test_no_raw_pii_in_traces():
    if not DB_PATH.exists():
        return  # no db yet — pass
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    violations = []
    try:
        rows = conn.execute(
            "SELECT id, agent, action, target, payload FROM traces "
            "WHERE created_ts >= ? LIMIT 10000",
            (PRIVACY_EPOCH,),
        ).fetchall()
        for row in rows:
            for field in ("agent", "action", "target"):
                v = row[field] or ""
                hits = _scan_string(v)
                if hits:
                    violations.append(f"traces.{field} row={row['id']}: {hits}")
            hits = _scan_payload(row["payload"])
            if hits:
                violations.append(f"traces.payload row={row['id']}: {hits}")
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet
    finally:
        conn.close()

    assert not violations, (
        f"PII found in mycelium.db traces ({len(violations)} violation(s)):\n"
        + "\n".join(violations[:20])
    )


def test_no_raw_pii_in_findings():
    if not DB_PATH.exists():
        return
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    violations = []
    try:
        rows = conn.execute(
            "SELECT id, title, evidence, suggestion FROM findings "
            "WHERE created_ts >= ? LIMIT 5000",
            (PRIVACY_EPOCH,),
        ).fetchall()
        for row in rows:
            for field in ("title", "evidence", "suggestion"):
                v = row[field] or ""
                hits = _scan_string(v)
                if hits:
                    violations.append(f"findings.{field} row={row['id']}: {hits}")
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()

    assert not violations, (
        f"PII found in mycelium.db findings ({len(violations)} violation(s)):\n"
        + "\n".join(violations[:20])
    )
