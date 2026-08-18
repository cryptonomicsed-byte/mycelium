#!/usr/bin/env python3
"""Patch ares_council_dashboard.py: classification-aware Wallets tab."""
src = open('/opt/ares/ares_council_dashboard.py').read()

old = """        out[\"wallets\"] = [dict(r) for r in conn.execute(
            \"SELECT substr(address,1,10)||'..' addr, buys, volume_usd, edge, distinct_tokens, tags \"
            \"FROM wallets ORDER BY volume_usd DESC LIMIT 12\")]"""
new = """        out[\"wallets\"] = [dict(r) for r in conn.execute(
            \"SELECT substr(address,1,10)||'..' addr, buys, volume_usd, edge, distinct_tokens, tags \"
            \"FROM wallets WHERE tags != '[]' AND tags IS NOT NULL \"
            \"ORDER BY volume_usd DESC LIMIT 15\")]
        out[\"role_counts\"] = {}
        for (t,) in conn.execute(\"SELECT tags FROM wallets\"):
            try:
                import json as _j
                for tag in _j.loads(t):
                    base = tag.split(':')[0]
                    out[\"role_counts\"][base] = out[\"role_counts\"].get(base, 0) + 1
            except Exception:
                pass"""
assert src.count(old) == 1, f'anchor count {src.count(old)}'
src = src.replace(old, new)

old2 = """ h+='<h2>Top wallets (by volume)</h2><table><tr><th>Wallet</th><th>Buys</th><th>Volume</th><th>Distinct tokens</th><th>Edge</th><th>Tags</th></tr>';"""
new2 = """ if(d.role_counts){h+='<h2>Wallet roles</h2><p>';
  for(const [k,v] of Object.entries(d.role_counts)){h+=`<span class="badge ok">${esc(k)}: ${v.toLocaleString()}</span> `;}
  h+='</p>';}
 h+='<h2>Classified wallets</h2><table><tr><th>Wallet</th><th>Buys</th><th>Volume</th><th>Distinct tokens</th><th>Edge</th><th>Tags</th></tr>';"""
assert src.count(old2) == 1, f'anchor2 count {src.count(old2)}'
src = src.replace(old2, new2)

open('/opt/ares/ares_council_dashboard.py', 'w').write(src)
print('dashboard patched OK')
