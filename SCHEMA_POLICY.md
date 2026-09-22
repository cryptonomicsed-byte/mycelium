# Mycelium schema policy

The rule, and the reasoning that produced it. Read this before adding a
column anywhere in this repo.

## The rule

> **Data you can re-derive gets rebuilt. Data you cannot gets migrated. Not
> knowing which one you hold is what broke this substrate.**

Three stores in this ecosystem answered that question three different ways,
and the answers are all correct — they were just never written down here.

| store | what it holds | policy | why |
|---|---|---|---|
| fomopulse | a trade tape | **delete + re-sync from the chain** | the chain is the source of truth; the tape is derived |
| Vantage | signals, agents, guild state | **ALTER migrations** (see `signal_pool`'s `_SIGNAL_POOL_MINT_MIGRATION` in `routers/intel.py`) | records, not derivable |
| Mycelium | traces, findings | **had neither** | nobody asked the question |

fomopulse can throw its database away because a re-sync reconstitutes it
(`AGENTS.md`: *"a database that does not match it is deleted and re-synced
from the chain"*). Vantage cannot, and knows it, so it migrates. **Mycelium
cannot either — and did nothing.** That omission is the entire bug below.

## The failure this policy exists to prevent

`findings.direction` was added to the DDL in `core.py` with no ALTER:

```sql
CREATE TABLE IF NOT EXISTS findings (... direction INTEGER NOT NULL DEFAULT 0, ...)
```

`CREATE TABLE IF NOT EXISTS` is a **no-op** against a database that already
has the table. So `direction` existed on databases created after that edit
and nowhere else. `add_finding`'s INSERT was positional:

```python
"INSERT INTO findings VALUES (:id,:created_ts,:miner,:confidence,:direction,...)"
```

Ten values into a nine-column table. **Every write against the live database
failed.** The table still held 82 rows from before the column was added, so
it looked populated and looked healthy. Nothing compared the code's schema
against the database's, so nothing reported it — and Mycelium's memory had
stopped growing silently.

### The second bug, underneath the first

Adding the ALTER was not enough, and the way it failed is worth keeping.

`ALTER TABLE ... ADD COLUMN` puts the column **physically last**. The DDL
declares `direction` **fourth**. So after the migration the live table and a
brand-new table had the same columns in different physical orders:

```
live  (migrated): id, created_ts, miner, confidence, title, evidence,
                  suggestion, state, payload, direction
fresh (from DDL): id, created_ts, miner, confidence, direction, title,
                  evidence, suggestion, state, payload
```

`INSERT INTO findings VALUES (...)` assigns **by physical position**. On a
fresh database that is correct. On the migrated one, every value from
`direction` onward landed one column early — `direction` into `title`, `title`
into `evidence`, `payload` into `direction`. Read back, a finding returned its
payload where its direction should be and appeared to be in the `alert` state.

**The corruption was invisible to the test suite, because tests build fresh
databases.** It appeared only on the one database that had been migrated —
which is to say, only in production. A probe caught it: a finding written and
read back came out with its fields shifted, which is the only reason it was
found at all.

The fix is to name the columns:

```python
"INSERT INTO findings (id, created_ts, miner, confidence, direction, title,"
" evidence, suggestion, state, payload) VALUES (:id, :created_ts, ...)"
```

Named inserts are independent of physical order, and physical order is the
only thing that varies between a migrated database and a fresh one. Both
`add_finding` and `emit` now name their columns; `emit`'s `traces` insert was
the same landmine, unfired only because no column had ever been added to
`traces`.

### What this pair teaches

- **A populated table is not a working writer.** The 82 rows were real. They
  were also the last ones written. Anything that checks "does the substrate
  have findings" answered yes throughout the outage.
- **`CREATE TABLE IF NOT EXISTS` is not a migration.** It is a no-op on the
  one database that needs the change.
- **Adding a column is not finished when the ALTER runs.** A positional INSERT
  turns a schema change into silent data corruption on every migrated
  database, and no fresh-database test will ever see it.
- **Migrated and fresh databases must be treated as different environments.**
  Every migration test here builds a database from the *old* DDL and migrates
  it, because that is the only shape that reproduces the failure.

## The mechanism

`core._COLUMN_MIGRATIONS` is the registry — one entry per column added to the
DDL after a database could have been written:

```python
_COLUMN_MIGRATIONS = (
    ("findings", "direction", "INTEGER NOT NULL DEFAULT 0"),
)
```

- `core.init_db()` applies every entry at open, idempotently, and prints to
  stderr which columns it added. Silence means nothing needed adding.
- `core.verify_schema()` compares the live database against the registry and
  returns `{ok, drift, schema_version_stored, schema_version_expected}`.
  Drift here means **a writer is failing**, not that a feature is missing.
- `python -m mycelium.cli schema` runs that check.
- `storage.PostgresBackend.init_db()` runs the same registry with
  `ADD COLUMN IF NOT EXISTS`, through `sql.Identifier` so the names are
  quoted as identifiers rather than interpolated as text.
- `core.SCHEMA_VERSION` is bumped when the registry changes, and written to
  `meta`. It was previously written and never read — recorded and unchecked,
  which is worse than absent because it reads as tracked.

## Adding a column, in order

1. Add the column to the DDL in `core.py` **and** to `_COLUMN_MIGRATIONS`.
2. Add it to `PostgresBackend.DDL` — the registry covers the ALTER on both
   backends, but the CREATE statement still has to be right for new databases.
3. Bump `SCHEMA_VERSION`.
4. Run `python -m mycelium.cli schema` and confirm `ok: true` with empty drift.
5. Confirm a write actually lands — `add_finding` then `get_finding` it back.
   Not `init_db` returning without an exception, which it did throughout the
   outage.

## Recoverability audit — every table, classified

Because the policy is "migrate, never rebuild," it is worth knowing exactly
which tables that commits us to. `RECORD` means the only copy; `DERIVED`
means it can be reconstituted from a source of truth, so it may be rebuilt.

### RECORD — migrate, back up, never drop

| table | where | why it is the only copy |
|---|---|---|
| `traces` | `core.py` | an agent's own history. There is no upstream that recorded it. |
| `findings` | `core.py` | produced from traces by miners over a *moving window* — re-mining next week reads different traces and yields different findings. Plus `state` (applied/dismissed) is a decision made by an actor, which no recomputation recovers. |
| `meta` | `core.py` | `schema_version` and cursors. Losing it makes an old database look new. |
| `picks` | `signal_fusion/store.py` | a pick is a decision taken at a time against a market that has since moved. Recomputing it produces a different pick, not the same one. |
| `outcomes` | `signal_fusion/store.py` | the *resolution* of a pick, recorded at a moment the market will not return to. The price path after a signal is not re-observable. |
| `seen`, `state` | `wallet/collector.py`, `wallet/scanner.py` | cursors over what was already emitted. Rebuildable in principle, but the emission timestamps are history, and a wrong cursor re-emits old events as new. |

### DERIVED — rebuildable from a source of truth

| table | where | source of truth |
|---|---|---|
| `wallets` | `wallet/scanner.py` | the chain + GMGN/Birdeye |
| `token_stats` | `wallet/scanner.py` | the chain + the feed |
| `wallet_tokens` | `wallet/scanner.py` | the chain — who holds what is readable at any time |
| `wallet_reputation`, `edges` | `wallet_intel.db` (`/opt/ares/wallet_intel/`) | recomputable from the scans above |

The DERIVED tables are the exception that proves the rule: they are scans of
public chain state, so a rebuild costs time rather than information. **Nothing
in `core.py` is in this category.** The substrate itself is entirely RECORD,
which is why `_COLUMN_MIGRATIONS` is the only path and why there is no
rebuild-to-migrate fallback available here.

## What this policy does not solve

- **Backfill.** A column added by migration gets its `DEFAULT` on existing
  rows; it does not get a correct value. `findings.direction` defaulted to `0`
  (neutral) on the 82 pre-existing rows, which is a guess, not a measurement.
  When the value matters, the migration has to say so explicitly rather than
  lean on the default.
- **Order.** `_COLUMN_MIGRATIONS` is applied in tuple order. A migration that
  depends on another must come after it.
- **Detecting drift in columns that were never registered.** `verify_schema`
  checks the registry, not the full DDL. A column added to the DDL and
  forgotten in the registry is invisible to it — which is why step 1 above is
  two edits, not one.
