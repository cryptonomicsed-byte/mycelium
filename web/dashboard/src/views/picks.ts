// Picks (#/picks) -- the signal-fusion engine's ranked token list, served
// through the gateway's /api/picks proxy to the VPS (signal_fusion/ writes
// ares_picks.db there). Every pick is explainable: the components column
// expands into the per-component scores and cited drivers, because a
// number without its "why" is exactly what the fusion spec forbids.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { downloadJSON } from "../export.js";
import type { Pick } from "../types.js";

export class PicksView extends MyceliumElement {
  private picks: Pick[] = [];
  private openRank: number | null = null;
  private loading = true;
  private error = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Picks</h2>
        <span class="sub">signal fusion — best tokens, with receipts</span>
        <button class="secondary" data-act="export">⤓ JSON</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="export"]')!.addEventListener("click", () =>
      downloadJSON(this.picks, "picks"),
    );
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 60_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    try {
      const res = await api.picks();
      this.picks = Array.isArray(res) ? res : (res.picks ?? []);
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading…</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Fusion engine unreachable: ${esc(this.error)}<br>
        Picks come from ares-signal-fusion on the VPS via the gateway proxy — this panel lights
        up once the engine is deployed and the tunnel is up (signal_fusion/README.md).</div>`;
      return;
    }
    if (!this.picks.length) {
      body.innerHTML = `<div class="empty-state">No picks this window — every candidate either
        scored too low or hit a hard gate (liquidity, holder concentration, bundler share…).
        A quiet picks list is the gates working, not the engine idle.</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>#</th><th>Symbol</th><th>Score</th><th>Status</th><th>When</th><th></th></tr></thead>
        <tbody>
          ${this.picks
            .map(
              (p) => `
            <tr class="row-clickable" data-rank="${Number(p.rank)}">
              <td><b>#${Number(p.rank)}</b></td>
              <td><b>${esc(p.symbol)}</b> <span class="muted">${esc((p.token_addr || "").slice(0, 10))}…</span></td>
              <td>
                <div class="conf-bar" style="width:120px"><div class="conf-bar__fill" style="width:${Math.min(100, Number(p.score) || 0)}%"></div></div>
                ${Number(p.score).toFixed(1)}
              </td>
              <td class="muted">${esc(p.status || "candidate")}</td>
              <td class="muted" title="${esc(p.ts || "")}">${esc(p.ts ? relTime(p.ts) : "—")}</td>
              <td class="muted">${this.openRank === p.rank ? "▲" : "▼"}</td>
            </tr>
            ${
              this.openRank === p.rank
                ? `<tr><td colspan="6"><div class="json-drawer">${esc(JSON.stringify({ components: p.components ?? {}, gates: p.gates ?? {} }, null, 2))}</div></td></tr>`
                : ""
            }`,
            )
            .join("")}
        </tbody>
      </table>`;
    body.querySelectorAll<HTMLTableRowElement>("tr.row-clickable").forEach((tr) =>
      tr.addEventListener("click", () => {
        const rank = Number(tr.dataset.rank);
        this.openRank = this.openRank === rank ? null : rank;
        this.renderBody();
      }),
    );
  }
}
