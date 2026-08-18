#!/usr/bin/env python3
"""Patch collector.py: escalating ban backoff in the CLI-fallback path."""
path = "/opt/ares/wallet_intel/collector.py"
src = open(path).read()

old = '''        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            try:
                import scanner as _sc
                until = _sc._parse_ban_reset(err)
            except Exception:
                until = None
            until = (until + 60) if until else (time.time() + 900)
            conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_until', ?)",
                         (str(until),))
            conn.commit()
            log(f"  [gmgn] banned — resuming {time.strftime('%H:%M:%S', time.localtime(until))}")'''
new = '''        if "RATE_LIMIT_BANNED" in err or "banned" in err.lower():
            try:
                import scanner as _sc
                _sc._mark_ban(conn, err)
            except Exception:
                try:
                    until = _sc._parse_ban_reset(err) if 'scanner' in dir() else None
                except Exception:
                    until = None
                until = (until + 300) if until else (time.time() + 900)
                conn.execute("INSERT OR REPLACE INTO state(k, v) VALUES ('gmgn_ban_until', ?)",
                             (str(until),))
                conn.commit()
                log(f"  [gmgn] banned — resuming {time.strftime('%H:%M:%S', time.localtime(until))}")'''
assert src.count(old) == 1, f'anchor count {src.count(old)}'
src = src.replace(old, new)
open(path, "w").write(src)
print("collector.py patched: escalating ban backoff")
