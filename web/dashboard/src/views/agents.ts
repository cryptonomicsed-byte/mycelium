// Agents (#/agents) -- who actually writes to the substrate: GROUP BY
// agent over the trace table (GET /api/agents), with health badges. An
// agent is "active" if its last trace is <10min old, "stale" under an
// hour, "dead" past that -- heuristic thresholds, but they make the
// multi-agent reality (Hermes, council, wallet_intel, cron, this very
// dashboard) legible at a glance. Click-through jumps to the Trace
// Explorer pre-filtered to that agent via its deep-link support.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { downloadCSV, downloadJSON } from "../export.js";
import type { AgentStat } from "../types.js";

const STALE_MS = 10 * 60 * 1000;
const DEAD_MS = 60 * 60 * 1000;

export class AgentsView extends MyceliumElement {
  private agents: AgentStat[] = [];
  private loading = true;
  private error = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Agents</h2>
        <span class="sub">who writes to the substrate</span>
        <div class="view-filters">
          <button class="secondary" data-act="export-csv">⤓ CSV</button>
          <button class="secondary" data-act="export-json">⤓ JSON</button>
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="export-csv"]')!.addEventListener("click", () =>
      downloadCSV(this.agents as unknown as Record<string, unknown>[], "agents"),
    );
    this.querySelector('[data-act="export-json"]')!.addEventListener("click", () =>
      downloadJSON(this.agents, "agents"),
    );
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 30_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    try {
      const res = await api.agents();
      this.agents = res.agents;
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
      body.innerHTML = `<div class="empty-state">Failed to load agents: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.agents.length) {
      body.innerHTML = `<div class="empty-state">Nothing has written to the substrate yet.</div>`;
      return;
    }
    const now = Date.now();
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Agent</th><th>Health</th><th>Traces</th><th>Last active</th><th>Error rate</th><th>Kinds</th></tr></thead>
        <tbody>
          ${this.agents
            .map((a) => {
              const age = now - Date.parse(a.last_seen);
              const health =
                age < STALE_MS
                  ? `<span class="badge badge--skill">active</span>`
                  : age < DEAD_MS
                    ? `<span class="badge badge--alert">stale</span>`
                    : `<span class="badge badge--config_fix">dead</span>`;
              const kinds = Object.entries(a.kinds)
                .sort((x, y) => y[1] - x[1])
                .map(([k, n]) => `<span class="vote">${esc(k)}:${n}</span>`)
                .join(" ");
              return `<tr class="row-clickable" data-agent="${esc(a.name)}" title="open in Trace Explorer">
                <td><b>${esc(a.name)}</b></td>
                <td>${health}</td>
                <td>${a.trace_count}</td>
                <td class="muted" title="${esc(a.last_seen)}">${esc(relTime(a.last_seen))}</td>
                <td class="${a.error_rate > 0.3 ? "sell" : "muted"}">${(a.error_rate * 100).toFixed(0)}%</td>
                <td>${kinds}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
    body.querySelectorAll<HTMLTableRowElement>("tr.row-clickable").forEach((tr) =>
      tr.addEventListener("click", () => {
        location.hash = `#/traces?agent=${encodeURIComponent(tr.dataset.agent!)}`;
      }),
    );
  }
}
