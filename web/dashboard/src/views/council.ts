// Council view — the Ares Council debate surface: Verdicts, Calibration,
// Overview, and Substrate. Data comes same-origin through the gateway's
// /api/council/* proxy (gateway/main.go handleCouncilProxy), which forwards
// to the VPS Vantage API with the local agent key.
import { MyceliumElement, esc } from "../components/base.js";

interface Verdict {
  id: number;
  symbol: string;
  direction: string;
  conviction: number | null;
  entry_price: number | null;
  outcome: string;
  paper: number;
  posted_at: string;
  votes?: Array<{
    persona: string;
    direction: string;
    confidence: number;
    weight: number;
    rationale?: string;
  }>;
}

interface CalibrationRow {
  persona: string;
  role: string;
  base_weight: number;
  veto: boolean;
  correct: number;
  total: number;
  rate: number | null;
  multiplier: number;
  eff_weight: number;
}

interface Overview {
  daemon_running: boolean;
  daemon_pid: string;
  verdict_count: number;
  trace_buffer_pending: number;
  mycelium?: { findings: number; status: string; traces: number };
}

export class CouncilView extends MyceliumElement {
  private tab = "verdicts";
  private verdicts: Verdict[] = [];
  private calibration: CalibrationRow[] = [];
  private overview: Overview | null = null;
  private substrate: unknown[] = [];
  private error = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Council</h2>
        <span class="sub">Ares debate verdicts, calibration, substrate</span>
      </div>
      <div class="tabs">
        ${["verdicts", "calibration", "overview", "substrate"]
          .map((t) => `<button class="tab-btn ${this.tab === t ? "active" : ""}" data-tab="${t}">${t}</button>`)
          .join("")}
      </div>
      <div data-el="body"></div>
    `;
    this.querySelectorAll(".tab-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        this.tab = (btn as HTMLElement).dataset.tab!;
        this.render();
        this.fetchAll();
      })
    );
    this.mount();
  }

  protected mount() {
    this.fetchAll();
  }

  private async fetchAll() {
    const body = this.querySelector<HTMLElement>("[data-el='body']");
    if (!body) return;
    body.innerHTML = `<p class="muted">Loading…</p>`;
    try {
      const [v, c, o, s] = await Promise.all([
        fetch("/api/council/verdicts?limit=20").then((r) => r.json()),
        fetch("/api/council/calibration").then((r) => r.json()),
        fetch("/api/council/overview").then((r) => r.json()),
        fetch("/api/council/substrate").then((r) => r.json()),
      ]);
      this.verdicts = Array.isArray(v) ? v : [];
      this.calibration = Array.isArray(c) ? c : [];
      this.overview = (o && typeof o === "object" ? o : null) as Overview | null;
      this.substrate = (s && Array.isArray(s.council_traces) ? s.council_traces : []);
      this.error = "";
    } catch (e) {
      this.error = String(e);
    }
    this.renderBody();
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>("[data-el='body']");
    if (!body) return;
    if (this.error) {
      body.innerHTML = `<p class="muted">council proxy error: ${esc(this.error)}</p>`;
      return;
    }
    switch (this.tab) {
      case "calibration":
        body.innerHTML = this.renderCalibration();
        break;
      case "overview":
        body.innerHTML = this.renderOverview();
        break;
      case "substrate":
        body.innerHTML = this.renderSubstrate();
        break;
      default:
        body.innerHTML = this.renderVerdicts();
    }
  }

  private renderVerdicts(): string {
    if (!this.verdicts.length) return `<p class="muted">No verdicts yet.</p>`;
    const rows = this.verdicts
      .map((v) => {
        const dir = esc(v.direction || "?");
        const conv = v.conviction != null ? v.conviction.toFixed(2) : "—";
        const out = esc(v.outcome || "pending");
        const votes = (v.votes || [])
          .map(
            (vv) =>
              `<span class="vote ${esc((vv.direction || "").toLowerCase())}" title="${esc(vv.rationale || "")}">${esc(vv.persona)}:${esc(vv.direction || "?")} ${(vv.confidence ?? 0).toFixed(2)}</span>`
          )
          .join(" ");
        return `<tr>
          <td>#${v.id}</td>
          <td><b>${esc(v.symbol)}</b></td>
          <td class="${dir === "SELL" ? "sell" : dir === "BUY" ? "buy" : ""}">${dir}</td>
          <td>${conv}</td>
          <td>${out}</td>
          <td class="muted">${esc(v.posted_at || "")}</td>
          <td class="votes">${votes}</td>
        </tr>`;
      })
      .join("");
    return `<table><tr><th>#</th><th>Symbol</th><th>Dir</th><th>Conv</th><th>Outcome</th><th>Posted</th><th>Votes</th></tr>${rows}</table>`;
  }

  private renderCalibration(): string {
    if (!this.calibration.length) return `<p class="muted">No calibration data.</p>`;
    const rows = this.calibration
      .map((c) => {
        const rate = c.rate != null ? `${(c.rate * 100).toFixed(0)}%` : "—";
        return `<tr>
          <td><b>${esc(c.persona)}</b>${c.veto ? ' <span class="badge warn">veto</span>' : ""}</td>
          <td class="muted">${esc(c.role || "")}</td>
          <td>${c.base_weight}</td>
          <td>${rate}</td>
          <td>${c.correct}/${c.total}</td>
          <td>${c.multiplier != null ? c.multiplier.toFixed(2) : "—"}</td>
          <td><b>${c.eff_weight != null ? c.eff_weight.toFixed(2) : "—"}</b></td>
        </tr>`;
      })
      .join("");
    return `<table><tr><th>Persona</th><th>Role</th><th>Base w</th><th>Win rate</th><th>Correct</th><th>Multiplier</th><th>Eff w</th></tr>${rows}</table>`;
  }

  private renderOverview(): string {
    if (!this.overview) return `<p class="muted">No overview data.</p>`;
    const o = this.overview;
    const my = o.mycelium;
    return `<div class="cards">
      <div class="card"><div class="big">${o.daemon_running ? "●" : "○"}</div><div class="lbl">daemon ${o.daemon_running ? "running" : "down"} (pid ${esc(o.daemon_pid || "—")})</div></div>
      <div class="card"><div class="big">${o.verdict_count}</div><div class="lbl">verdicts</div></div>
      <div class="card"><div class="big">${o.trace_buffer_pending}</div><div class="lbl">traces pending</div></div>
      ${my ? `<div class="card"><div class="big">${my.traces}</div><div class="lbl">substrate traces</div></div>
      <div class="card"><div class="big">${my.findings}</div><div class="lbl">findings</div></div>` : ""}
    </div>`;
  }

  private renderSubstrate(): string {
    if (!this.substrate.length) return `<p class="muted">No council traces in substrate.</p>`;
    const items = this.substrate
      .slice(0, 25)
      .map((t) => {
        const tr = t as { action?: string; ts?: string; payload?: string };
        return `<div class="trace-row"><code>${esc(tr.action || "")}</code> <span class="muted">${esc(tr.ts || "")}</span> <span class="muted">${esc(String(tr.payload || "").slice(0, 120))}</span></div>`;
      })
      .join("");
    return `<div class="trace-list">${items}</div>`;
  }
}
