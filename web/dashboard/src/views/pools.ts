// Pools (#/pools) -- key-pool + proxy-pool health for the data farms.
// Served through the gateway's /api/poolhealth proxy to the VPS poolhealth
// service (:8004). Counts and states only — never raw keys.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import type { PoolHealth } from "../types.js";

export class PoolsView extends MyceliumElement {
  private health: PoolHealth | null = null;
  private loading = true;
  private error = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Key Pools</h2>
        <span class="sub">gmgn / solscan / teamorouter keys + proxy pool health</span>
      </div>
      <div data-el="body"></div>
    `;
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 60_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    try {
      this.health = await api.poolHealth();
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }

  private pct(n: number, total: number): number {
    return total > 0 ? Math.min(100, Math.round((n / total) * 100)) : 0;
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading…</div>`;
      return;
    }
    if (this.error || !this.health) {
      body.innerHTML = `<div class="empty-state">Pool health unreachable: ${esc(this.error || "no data")}<br>
        Comes from ares-poolhealth on the VPS via the gateway proxy — the tile lights up once
        the service is up and the tunnel is live.</div>`;
      return;
    }
    const h = this.health;
    const g = h.gmgn ?? { keys: 0, cooling: 0, ip_banned: false, ip_ban_until: 0 };
    const p = h.proxies ?? { total: 0, cooldown: 0, live_estimate: 0 };
    const banLeft = g.ip_ban_until > (h.ts ?? 0) ? Math.round(g.ip_ban_until - (h.ts ?? 0)) : 0;

    body.innerHTML = `
      <div class="pool-grid">
        <div class="pool-card ${g.ip_banned ? "warn" : ""}">
          <h3>GMGN</h3>
          <div class="pool-stat"><b>${g.keys}</b> keys</div>
          <div class="pool-stat muted">${g.cooling} cooling (quota cooldown)</div>
          <div class="pool-stat ${g.ip_banned ? "bad" : "good"}">
            ${g.ip_banned ? `IP banned — ${Math.floor(banLeft / 60)}m left` : "IP clean"}
          </div>
          <div class="conf-bar"><div class="conf-bar__fill ${g.ip_banned ? "warn" : ""}"
            style="width:${100 - this.pct(Math.max(g.cooling, 1), Math.max(g.keys, 1))}%"></div></div>
        </div>
        <div class="pool-card">
          <h3>Proxies</h3>
          <div class="pool-stat"><b>${p.total}</b> in pool</div>
          <div class="pool-stat muted">${p.cooldown} in cooldown</div>
          <div class="pool-stat ${(p.live_estimate ?? 0) > 0 ? "good" : "muted"}">
            ${p.live_estimate ?? 0} live estimate
          </div>
          <div class="conf-bar"><div class="conf-bar__fill"
            style="width:${this.pct(p.live_estimate ?? 0, p.total)}%"></div></div>
        </div>
        <div class="pool-card">
          <h3>Solscan</h3>
          <div class="pool-stat"><b>${h.solscan?.keys ?? 0}</b> keys</div>
          <div class="pool-stat muted">dormant — free tier reads nothing (paywalled)</div>
        </div>
        <div class="pool-card">
          <h3>TeamoRouter</h3>
          <div class="pool-stat"><b>${h.teamorouter?.keys ?? 0}</b> keys</div>
          <div class="pool-stat muted">farm cron: every 20 min</div>
        </div>
      </div>
      <div class="pool-footer muted">refreshes every 60s · last update ${h.ts ? esc(relTime(h.ts)) : "—"}</div>
    `;
  }
}
