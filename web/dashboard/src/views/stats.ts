// Stats (#/stats) -- hand-rolled canvas charts over /api/stats/timeseries
// (gateway/ops.go). No chart library: two chart kinds (stacked bars for
// traces-by-kind, grouped bars for findings-by-state + a mine-runs line)
// don't justify one, and the repo's ethos is minimal dependencies. Colors
// come from the app palette so the charts read as part of the dashboard,
// not an embed.
import { MyceliumElement, esc } from "../components/base.js";
import { api } from "../api.js";
import type { StatsBucket } from "../types.js";

const KIND_COLORS: Record<string, string> = {
  tool_call: "#7ad",
  decision: "#7c5",
  observation: "#a8d",
  error: "#f55",
  memory_write: "#fa3",
  workflow_start: "#5aa",
  workflow_end: "#588",
};
const STATE_COLORS: Record<string, string> = {
  open: "#fa3",
  applied: "#7c5",
  dismissed: "#888",
};

export class StatsView extends MyceliumElement {
  private range: "24h" | "7d" | "30d" = "24h";
  private buckets: StatsBucket[] = [];
  private error = "";
  private loading = true;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Stats</h2>
        <div class="view-filters">
          ${(["24h", "7d", "30d"] as const)
            .map(
              (r) =>
                `<button class="tab-btn ${this.range === r ? "active" : ""}" data-range="${r}">${r}</button>`,
            )
            .join("")}
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelectorAll<HTMLButtonElement>("[data-range]").forEach((btn) =>
      btn.addEventListener("click", () => {
        this.range = btn.dataset.range as typeof this.range;
        this.render();
        this.fetch();
      }),
    );
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 30_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    this.loading = this.buckets.length === 0;
    try {
      const res = await api.statsTimeseries(this.range);
      this.buckets = res.buckets;
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
      body.innerHTML = `<div class="empty-state">Stats unavailable: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.buckets.length) {
      body.innerHTML = `<div class="empty-state">No activity in this window — quiet substrate.</div>`;
      return;
    }
    const kinds = Array.from(new Set(this.buckets.flatMap((b) => Object.keys(b.traces_by_kind))));
    const states = Array.from(new Set(this.buckets.flatMap((b) => Object.keys(b.findings_by_state))));
    body.innerHTML = `
      <div class="chart-panel">
        <h3>Traces per ${this.range === "24h" ? "hour" : "day"}, stacked by kind</h3>
        <div class="chart-legend">${kinds
          .map((k) => `<span><span class="swatch" style="background:${KIND_COLORS[k] ?? "#666"}"></span>${esc(k)}</span>`)
          .join("")}</div>
        <canvas data-el="traces-chart"></canvas>
      </div>
      <div class="chart-panel">
        <h3>Findings by state + mine runs</h3>
        <div class="chart-legend">${states
          .map((s) => `<span><span class="swatch" style="background:${STATE_COLORS[s] ?? "#666"}"></span>${esc(s)}</span>`)
          .join("")}<span><span class="swatch" style="background:#ddd"></span>mine runs (line)</span></div>
        <canvas data-el="findings-chart"></canvas>
      </div>`;
    this.drawTraces();
    this.drawFindings();
  }

  private setupCanvas(sel: string): CanvasRenderingContext2D | null {
    const canvas = this.querySelector<HTMLCanvasElement>(sel);
    if (!canvas) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 180;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);
    return ctx;
  }

  private drawTraces() {
    const ctx = this.setupCanvas('[data-el="traces-chart"]');
    if (!ctx) return;
    const w = ctx.canvas.clientWidth || 800;
    const h = 180;
    const pad = 4;
    const max = Math.max(...this.buckets.map((b) => b.traces_total), 1);
    const bw = Math.max(2, (w - pad * 2) / this.buckets.length - 2);
    this.buckets.forEach((b, i) => {
      const x = pad + i * ((w - pad * 2) / this.buckets.length);
      let y = h - pad;
      for (const [kind, n] of Object.entries(b.traces_by_kind)) {
        const bh = ((h - pad * 2) * n) / max;
        ctx.fillStyle = KIND_COLORS[kind] ?? "#666";
        ctx.fillRect(x, y - bh, bw, bh);
        y -= bh;
      }
    });
  }

  private drawFindings() {
    const ctx = this.setupCanvas('[data-el="findings-chart"]');
    if (!ctx) return;
    const w = ctx.canvas.clientWidth || 800;
    const h = 180;
    const pad = 4;
    const maxF = Math.max(...this.buckets.map((b) => Object.values(b.findings_by_state).reduce((a, n) => a + n, 0)), 1);
    const maxM = Math.max(...this.buckets.map((b) => b.mine_runs), 1);
    const bw = Math.max(2, (w - pad * 2) / this.buckets.length - 2);
    this.buckets.forEach((b, i) => {
      const x = pad + i * ((w - pad * 2) / this.buckets.length);
      let y = h - pad;
      for (const [state, n] of Object.entries(b.findings_by_state)) {
        const bh = ((h - pad * 2) * n) / maxF;
        ctx.fillStyle = STATE_COLORS[state] ?? "#666";
        ctx.fillRect(x, y - bh, bw, bh);
        y -= bh;
      }
    });
    // mine-runs line over the bars
    ctx.strokeStyle = "#ddd";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    this.buckets.forEach((b, i) => {
      const x = pad + (i + 0.5) * ((w - pad * 2) / this.buckets.length);
      const y = h - pad - ((h - pad * 2) * b.mine_runs) / maxM;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
}
