// Council view — the Ares Council debate surface: Verdicts, Calibration,
// Council (personas + gates), Overview, and Substrate. Data comes
// same-origin through the gateway's /api/council/* proxy (gateway/main.go
// handleCouncilProxy), which forwards to the VPS Vantage API with the
// local agent key. Every field is rendered defensively -- the VPS side
// evolves independently of this dashboard, so a missing field degrades to
// "—", never to a broken view.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { downloadCSV, downloadJSON } from "../export.js";

interface Vote {
  persona: string;
  direction: string;
  confidence: number;
  weight: number;
  rationale?: string;
}

interface Verdict {
  id: number;
  symbol: string;
  direction: string;
  conviction: number | null;
  entry_liq?: number | null;
  entry_price?: number | null;
  outcome: string;
  paper: number;
  posted_at: string;
  votes?: Vote[];
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
  signal_pool_count?: number;
  signal_sources?: string[];
  mycelium?: { findings: number; status: string; traces: number };
}

interface SubstrateData {
  council_traces?: Array<{ ts?: string; kind?: string; action?: string; target?: string; payload?: string }>;
  council_findings?: Array<{ id?: string; title?: string; state?: string; miner?: string; created_ts?: string }>;
  mycelium?: { status?: string; traces?: number; findings?: number };
}

// The gates the council actually enforces (mirrors the daemon config on the
// VPS -- this text is the explainer the old dashboard carried, kept in sync
// by hand since the gates are code on the VPS side, not data).
const GATES = [
  ["Risk veto", "the Risk persona can unilaterally block any trade"],
  ["Contrarian double-dissent", "two contrarian dissents in one debate kill the verdict"],
  ["Liquidity floor", "entry liquidity must be ≥ $5,000"],
  ["Conviction threshold", "weighted conviction ≥ 0.60 for PAPER, ≥ 0.70 for LIVE"],
  ["PAPER default", "all verdicts execute as paper trades until LIVE is explicitly enabled"],
  ["Two debate rounds", "every verdict passes through two full persona debate rounds"],
] as const;

export class CouncilView extends MyceliumElement {
  private tab = "verdicts";
  private verdicts: Verdict[] = [];
  private calibration: CalibrationRow[] = [];
  private overview: Overview | null = null;
  private substrate: SubstrateData = {};
  private error = "";
  private loaded = false;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Council</h2>
        <span class="sub">Ares debate verdicts, calibration, substrate</span>
        <div class="view-filters">
          <button class="secondary" data-act="export-csv" title="Download the current tab as CSV">⤓ CSV</button>
          <button class="secondary" data-act="export-json" title="Download the current tab as JSON">⤓ JSON</button>
        </div>
      </div>
      <div class="tabs">
        ${["verdicts", "calibration", "council", "overview", "substrate"]
          .map((t) => `<button class="tab-btn ${this.tab === t ? "active" : ""}" data-tab="${t}">${t}</button>`)
          .join("")}
      </div>
      <div data-el="body"></div>
    `;
    this.querySelectorAll(".tab-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        this.tab = (btn as HTMLElement).dataset.tab!;
        this.render();
        this.renderBody();
      }),
    );
    this.querySelector('[data-act="export-csv"]')?.addEventListener("click", () => this.exportTab("csv"));
    this.querySelector('[data-act="export-json"]')?.addEventListener("click", () => this.exportTab("json"));
    if (this.loaded) this.renderBody();
  }

  protected mount() {
    this.fetchAll();
    const timer = setInterval(() => this.fetchAll(), 30_000);
    this.onDisconnect(() => clearInterval(timer));
  }

  private exportTab(format: "csv" | "json") {
    const name = `council-${this.tab}`;
    const data: unknown =
      this.tab === "calibration" ? this.calibration
      : this.tab === "overview" ? this.overview
      : this.tab === "substrate" ? this.substrate
      : this.verdicts;
    if (format === "json") {
      downloadJSON(data, name);
    } else {
      const rows = Array.isArray(data) ? (data as Record<string, unknown>[]) : [data as Record<string, unknown>];
      downloadCSV(rows, name);
    }
  }

  private async fetchAll() {
    try {
      const [v, c, o, s] = await Promise.all([
        fetch("/api/council/verdicts?limit=20").then((r) => r.json()),
        fetch("/api/council/calibration").then((r) => r.json()),
        fetch("/api/council/overview").then((r) => r.json()),
        fetch("/api/council/substrate").then((r) => r.json()),
      ]);
      this.verdicts = Array.isArray(v) ? v : [];
      this.calibration = Array.isArray(c) ? c : [];
      this.overview = (o && typeof o === "object" && !o.error ? o : null) as Overview | null;
      this.substrate = (s && typeof s === "object" ? s : {}) as SubstrateData;
      this.error = "";
    } catch (e) {
      this.error = String(e);
    }
    this.loaded = true;
    this.renderBody();
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>("[data-el='body']");
    if (!body) return;
    if (!this.loaded) {
      body.innerHTML = `<p class="muted">Loading…</p>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Council proxy unreachable: ${esc(this.error)}<br>
        The council lives on the VPS — this panel needs the tunnel up and the gateway's
        MYCELIUM_COUNCIL_BASE pointing at it.</div>`;
      return;
    }
    switch (this.tab) {
      case "calibration":
        body.innerHTML = this.renderCalibration();
        break;
      case "council":
        body.innerHTML = this.renderCouncilInfo();
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
    if (!this.verdicts.length) {
      return `<div class="empty-state">No verdicts yet — the council needs signals in the pool
        and two debate rounds before a verdict lands.</div>`;
    }
    const rows = this.verdicts
      .map((v) => {
        const dir = esc(v.direction || "?");
        const conv = v.conviction != null ? v.conviction.toFixed(2) : "—";
        const liq = v.entry_liq ?? v.entry_price;
        const liqStr = liq != null ? `$${Number(liq).toLocaleString()}` : "—";
        const out = esc(v.outcome || "pending");
        const mode = v.paper ? `<span class="badge badge--skill">PAPER</span>` : `<span class="badge badge--config_fix">LIVE</span>`;
        const voteChips = (v.votes || [])
          .map(
            (vv) =>
              `<span class="vote ${esc((vv.direction || "").toLowerCase())}">${esc(vv.persona)}:${esc(vv.direction || "?")} ${(vv.confidence ?? 0).toFixed(2)}×${(vv.weight ?? 0).toFixed(2)}</span>`,
          )
          .join(" ");
        const rationales = (v.votes || [])
          .filter((vv) => vv.rationale)
          .map((vv) => `<p><b>${esc(vv.persona)}</b> — ${esc(vv.rationale!)}</p>`)
          .join("");
        return `<tr>
          <td>#${Number(v.id)}</td>
          <td><b>${esc(v.symbol)}</b></td>
          <td class="${dir === "SELL" ? "sell" : dir === "BUY" ? "buy" : ""}">${dir}</td>
          <td>${conv}</td>
          <td>${liqStr}</td>
          <td>${out}</td>
          <td>${mode}</td>
          <td class="muted" title="${esc(v.posted_at || "")}">${esc(v.posted_at ? relTime(v.posted_at) : "—")}</td>
          <td class="votes">
            ${voteChips}
            ${rationales ? `<details class="finding-card__evidence"><summary>rationales</summary>${rationales}</details>` : ""}
          </td>
        </tr>`;
      })
      .join("");
    return `<table class="data-table">
      <thead><tr><th>#</th><th>Symbol</th><th>Dir</th><th>Conv</th><th>Entry liq</th><th>Outcome</th><th>Mode</th><th>Time</th><th>Votes</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  private renderCalibration(): string {
    if (!this.calibration.length) return `<div class="empty-state">No calibration data yet — personas calibrate as verdict outcomes resolve.</div>`;
    const rows = this.calibration
      .map((c) => {
        const rate = c.rate != null ? `${(c.rate * 100).toFixed(0)}%` : "—";
        return `<tr>
          <td><b>${esc(c.persona)}</b>${c.veto ? ' <span class="badge badge--alert">veto</span>' : ""}</td>
          <td class="muted">${esc(c.role || "")}</td>
          <td>${c.base_weight}</td>
          <td>${rate}</td>
          <td>${c.correct}/${c.total}</td>
          <td>${c.multiplier != null ? c.multiplier.toFixed(2) : "—"}</td>
          <td><b>${c.eff_weight != null ? c.eff_weight.toFixed(2) : "—"}</b></td>
        </tr>`;
      })
      .join("");
    return `
      <table class="data-table">
        <thead><tr><th>Persona</th><th>Role</th><th>Base w</th><th>Win rate</th><th>Correct</th><th>Multiplier</th><th>Eff w</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">effective weight = base × multiplier, clamped to [0.2, 2.0] — the multiplier
      moves with each persona's tracked win rate, so consistently-right personas speak louder.</p>`;
  }

  private renderCouncilInfo(): string {
    const personaRows = this.calibration.length
      ? this.calibration
          .map(
            (c) => `<tr>
              <td><b>${esc(c.persona)}</b></td>
              <td class="muted">${esc(c.role || "")}</td>
              <td>${c.base_weight}</td>
              <td>${c.veto ? '<span class="badge badge--alert">veto</span>' : "—"}</td>
            </tr>`,
          )
          .join("")
      : `<tr><td colspan="4" class="muted">personas load from calibration data</td></tr>`;
    const gateRows = GATES.map(
      ([name, desc]) => `<tr><td><b>${esc(name)}</b></td><td class="muted">${esc(desc)}</td></tr>`,
    ).join("");
    return `
      <h3>Personas &amp; objectives</h3>
      <table class="data-table">
        <thead><tr><th>Persona</th><th>Objective</th><th>Base weight</th><th>Veto</th></tr></thead>
        <tbody>${personaRows}</tbody>
      </table>
      <h3>Gates</h3>
      <table class="data-table"><tbody>${gateRows}</tbody></table>`;
  }

  private renderOverview(): string {
    if (!this.overview) return `<div class="empty-state">No overview data.</div>`;
    const o = this.overview;
    const my = o.mycelium;
    return `<div class="cards">
      <div class="card"><div class="big">${o.daemon_running ? "●" : "○"}</div><div class="lbl">daemon ${o.daemon_running ? "running" : "down"} (pid ${esc(o.daemon_pid || "—")})</div></div>
      <div class="card"><div class="big">${Number(o.verdict_count) || 0}</div><div class="lbl">verdicts</div></div>
      <div class="card"><div class="big">${Number(o.trace_buffer_pending) || 0}</div><div class="lbl">traces pending</div></div>
      ${o.signal_pool_count != null ? `<div class="card"><div class="big">${Number(o.signal_pool_count)}</div><div class="lbl">signal pool${o.signal_sources?.length ? " · " + esc(o.signal_sources.join(", ")) : ""}</div></div>` : ""}
      ${my ? `<div class="card"><div class="big">${Number(my.traces) || 0}</div><div class="lbl">substrate traces</div></div>
      <div class="card"><div class="big">${Number(my.findings) || 0}</div><div class="lbl">findings</div></div>` : ""}
    </div>`;
  }

  private renderSubstrate(): string {
    const my = this.substrate.mycelium;
    const traces = this.substrate.council_traces || [];
    const findings = this.substrate.council_findings || [];
    const badge = my
      ? `<span class="badge ${my.status === "ok" ? "badge--skill" : "badge--config_fix"}">mycelium ${esc(my.status || "?")}</span>
         <span class="muted">${Number(my.traces) || 0} traces · ${Number(my.findings) || 0} findings · gateway reached via tunnel</span>`
      : `<span class="muted">no mycelium block in substrate payload</span>`;
    const traceRows = traces
      .slice(0, 30)
      .map(
        (t) => `<tr>
          <td class="muted" title="${esc(t.ts || "")}">${esc(t.ts ? relTime(t.ts) : "—")}</td>
          <td>${esc(t.kind || "")}</td>
          <td><code>${esc(t.action || "")}</code></td>
          <td class="muted">${esc(String(t.target || "").slice(0, 60))}</td>
        </tr>`,
      )
      .join("");
    const findingRows = findings
      .map(
        (f) => `<div class="finding-card finding-card--${esc(f.state || "open")}">
          <div class="finding-card__head">
            <span class="finding-card__miner">${esc(f.miner || "council")}</span>
            <span class="finding-card__state">${esc(f.state || "open")}</span>
          </div>
          <div class="finding-card__title">${esc(f.title || f.id || "")}</div>
        </div>`,
      )
      .join("");
    return `
      <p>${badge}</p>
      <h3>Council traces (latest 30)</h3>
      ${traces.length ? `<table class="data-table"><thead><tr><th>Time</th><th>Kind</th><th>Action</th><th>Target</th></tr></thead><tbody>${traceRows}</tbody></table>` : `<div class="empty-state">No council traces in the substrate yet.</div>`}
      <h3>Council findings</h3>
      ${findings.length ? findingRows : `<div class="empty-state">No council findings yet.</div>`}`;
  }
}
