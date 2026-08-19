// Miner Registry (#/miners) -- GET /api/miners merges a findings-table
// GROUP BY with the 7 hardcoded miner names (gateway/main.go knownMiners,
// mirroring mycelium/miners.py's MINERS dict) so a miner with zero findings
// still shows up here, not just active ones. "Force mine" buttons trigger a
// real cycle and each also emits a dashboard self-trace.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { traceForcedMine } from "../trace.js";
import type { MinerStat } from "../types.js";

export class MinersView extends MyceliumElement {
  private miners: MinerStat[] = [];
  private loading = true;
  private error: string | null = null;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Miner Registry</h2>
        <div class="view-filters">
          <button data-act="mine">Force mine cycle</button>
          <button data-act="mine-wasm" class="secondary">Force WASM mine</button>
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="mine"]')!.addEventListener("click", () => this.forceMine("cycle"));
    this.querySelector('[data-act="mine-wasm"]')!.addEventListener("click", () => this.forceMine("wasm"));
    this.load();
  }

  protected mount() {
    const t = setInterval(() => this.load(), 30_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async load() {
    this.loading = this.miners.length === 0;
    if (this.loading) this.renderBody();
    try {
      const res = await api.miners();
      this.miners = res.miners;
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }

  private async forceMine(kind: "cycle" | "wasm") {
    const actAttr = kind === "wasm" ? "mine-wasm" : "mine";
    const label = kind === "wasm" ? "Force WASM mine" : "Force mine cycle";
    const btn = this.querySelector<HTMLButtonElement>(`[data-act="${actAttr}"]`);
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Mining…";
    }
    try {
      if (kind === "wasm") await api.mineWasm();
      else await api.mine();
      traceForcedMine(kind, true);
      await this.load();
    } catch (err) {
      traceForcedMine(kind, false);
      console.warn(`mycelium: force mine (${kind}) failed`, err);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = label;
      }
    }
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    if (this.loading && !this.miners.length) {
      body.innerHTML = `<div class="empty-state">Loading…</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Failed to load miners: ${esc(this.error)}</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Miner</th><th>What it detects</th><th>Findings</th><th>Open / Applied / Dismissed</th><th>Avg conf</th><th>Last finding</th></tr></thead>
        <tbody>
          ${this.miners
            .map((m) => {
              const s = m.by_state ?? {};
              return `
            <tr>
              <td><b>${esc(m.miner)}</b></td>
              <td class="muted">${esc(MINER_DESCRIPTIONS[m.miner] ?? "")}</td>
              <td>${m.findings}</td>
              <td><span class="badge badge--alert">${s.open ?? 0}</span> <span class="badge badge--skill">${s.applied ?? 0}</span> <span class="muted">${s.dismissed ?? 0}</span></td>
              <td>${m.avg_confidence != null ? Math.round(m.avg_confidence * 100) + "%" : "—"}</td>
              <td>${m.last_finding_ts ? esc(relTime(m.last_finding_ts)) : "never"}</td>
            </tr>
          `;
            })
            .join("")}
        </tbody>
      </table>
    `;
  }
}

// What each registered miner actually looks for (mirrors the docstrings in
// mycelium/miners.py -- hand-kept in sync, same as the gateway's
// knownMiners list).
const MINER_DESCRIPTIONS: Record<string, string> = {
  recurring_workflow: "tool sequences repeated across sessions → compound-skill candidates",
  anomaly: "failure bursts on a single action vs. its baseline rate",
  cross_agent: "the same failure hitting multiple distinct agents → shared root cause",
  opportunity: "high-frequency call pairs where one compound tool would save calls",
  wallet_activity: "money-flow digest: what everyone is buying, who the movers are",
  wallet_correlation: "wallets co-buying ≥2 of the same tokens → co-movement clusters",
  wallet_anomaly: "burst buyers (≥3 tokens <10min) and everything-buyers (≥4 distinct)",
};
