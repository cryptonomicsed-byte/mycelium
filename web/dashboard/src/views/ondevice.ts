// On-device Mining (#/ondevice) -- hosts the WebNN scoring extracted to
// web/shared/webnn_score.js, imported here as well as by the standalone
// harness at web/webnn_miner.html, so the MLP isn't forked into two copies.
// This panel is the "real" home for it; the standalone page stays a
// zero-build-step debug harness for anyone who wants to run it outside the
// bundled dashboard.
import { MyceliumElement, esc } from "../components/base.js";
import { api } from "../api.js";
import { detectWebNNBackend, computeAnomalyRows, ANOMALY_SCORE_THRESHOLD } from "../../../shared/webnn_score.js";

interface AnomalyRow {
  action: string;
  total: number;
  fail: number;
  rate: number;
  score: number;
  anomaly: boolean;
}

export class OndeviceView extends MyceliumElement {
  private backend = "cpu";
  private rows: AnomalyRow[] = [];
  private pushedCount: number | null = null;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>On-device Mining</h2>
        <div class="view-filters">
          <button data-act="run">Run mine cycle</button>
        </div>
      </div>
      <p>WebNN (<code>navigator.ml</code>) when available, CPU fallback otherwise -- scoring runs
      entirely in this tab; findings are pushed back to the substrate as regular traces. Same math
      as the standalone <a href="/web/webnn_miner.html" target="_blank">debug harness</a>.</p>
      <div class="panel" data-el="backend">detecting backend…</div>
      <div data-el="output"></div>
    `;
    this.querySelector('[data-act="run"]')!.addEventListener("click", () => this.runCycle());
    this.detectBackend();
  }

  private async detectBackend() {
    const { backend, detail } = await detectWebNNBackend();
    this.backend = backend;
    const el = this.querySelector('[data-el="backend"]');
    if (el) {
      el.innerHTML = `backend: <strong>${backend === "webnn" ? "WebNN (navigator.ml)" : "CPU fallback"}</strong> — ${esc(detail)}`;
    }
  }

  private async runCycle() {
    const btn = this.querySelector<HTMLButtonElement>('[data-act="run"]');
    const out = this.querySelector<HTMLElement>('[data-el="output"]')!;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Running…";
    }
    out.innerHTML = `<div class="empty-state">Fetching traces…</div>`;
    try {
      const { traces } = await api.traces({ limit: 500 });
      if (!traces.length) {
        out.innerHTML = `<div class="empty-state">No traces from the gateway.</div>`;
        return;
      }
      this.rows = (await computeAnomalyRows(traces, this.backend)) as AnomalyRow[];
      const anomalies = this.rows.filter((r) => r.anomaly);
      for (const a of anomalies) {
        await api
          .emitTrace({
            agent: "dashboard-ondevice",
            session: "webnn",
            kind: "tool_call",
            action: "webnn_finding",
            target: "gateway",
            outcome: "success",
            payload: {
              miner: "webnn_anomaly",
              confidence: Math.min(0.97, a.score),
              title: `WebNN miner: failure burst on '${a.action}' (${Math.round(a.rate * 100)}%)`,
              evidence: `${a.fail}/${a.total} calls to '${a.action}' failed; anomaly score ${a.score.toFixed(2)}`,
              suggestion: "alert",
            },
          })
          .catch(() => {});
      }
      this.pushedCount = anomalies.length;
      this.renderRows(traces.length);
    } catch (err) {
      out.innerHTML = `<div class="empty-state">Mine cycle failed: ${esc(err instanceof Error ? err.message : String(err))}</div>`;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Run mine cycle";
      }
    }
  }

  private renderRows(traceCount: number) {
    const out = this.querySelector<HTMLElement>('[data-el="output"]');
    if (!out) return;
    const anomalyCount = this.rows.filter((r) => r.anomaly).length;
    out.innerHTML = `
      <p>traces: ${traceCount} | actions: ${this.rows.length} | anomalies: ${anomalyCount}
      ${this.pushedCount != null ? `| pushed ${this.pushedCount} finding(s) to substrate` : ""}</p>
      <table class="data-table">
        <thead><tr><th>Action</th><th>N</th><th>Fail</th><th>Rate</th><th>Score</th></tr></thead>
        <tbody>
          ${this.rows
            .slice(0, 20)
            .map(
              (r) => `
            <tr>
              <td>${esc(r.action)}</td>
              <td>${r.total}</td>
              <td>${r.fail}</td>
              <td>${r.rate.toFixed(2)}</td>
              <td>${r.score.toFixed(3)} ${r.score > ANOMALY_SCORE_THRESHOLD ? "⚠" : ""}</td>
            </tr>
          `,
            )
            .join("")}
        </tbody>
      </table>
    `;
  }
}
