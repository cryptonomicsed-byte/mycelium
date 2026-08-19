// Alerts (#/alerts) -- the generated-alerts/ watchdog configs evaluated
// against the current substrate (GET /api/alerts shells out to `cli.py
// alerts`, the same evaluation the cron watchdog runs). Tripped alerts are
// the signal; configs that exist but aren't tripping are shown too, since
// "the watchdog is armed and quiet" is information.
import { MyceliumElement, esc } from "../components/base.js";
import { api } from "../api.js";
import type { AlertResult } from "../types.js";

export class AlertsView extends MyceliumElement {
  private alerts: AlertResult[] = [];
  private tripped = 0;
  private loading = true;
  private error = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Alerts</h2>
        <span class="sub">generated watchdog configs, evaluated live</span>
        <button class="secondary" data-act="refresh">Re-evaluate</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="refresh"]')!.addEventListener("click", () => this.fetch());
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 30_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    try {
      const res = await api.alerts();
      this.alerts = res.alerts ?? [];
      this.tripped = res.tripped ?? 0;
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
      body.innerHTML = `<div class="empty-state">Evaluating alert configs…</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Alert evaluation failed: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.alerts.length) {
      body.innerHTML = `<div class="empty-state">No alert configs yet — applying an alert-type
        finding writes one to generated-alerts/, and it shows up here armed.</div>`;
      return;
    }
    body.innerHTML = `
      <p>${this.tripped ? `<span class="badge badge--config_fix">${this.tripped} TRIPPED</span>` : `<span class="badge badge--skill">all quiet</span>`}</p>
      <table class="data-table">
        <thead><tr><th>Alert</th><th>Watched action</th><th>Failures</th><th>Rate</th><th>State</th><th>Status</th></tr></thead>
        <tbody>
          ${this.alerts
            .map(
              (a) => `<tr>
              <td><code>${esc(a.alert)}</code></td>
              <td>${esc(a.action || "—")}</td>
              <td>${a.failures}/${a.total}</td>
              <td>${(a.rate * 100).toFixed(0)}%</td>
              <td class="muted">${esc(a.state ?? "—")}</td>
              <td>${a.tripped ? `<span class="badge badge--config_fix">TRIPPED</span>` : `<span class="muted">quiet</span>`}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>`;
  }
}
