// System/About (#/system) -- the honest "what's running where" panel: the
// stack description (static -- it IS the architecture, not data), live
// status fields from /api/status's ops extension (uptime, storage, auth
// mode), the WebTransport cert, the request inspector, the gateway log
// tail, and the storage-retention prune control.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import type { GatewayStatus } from "../types.js";

const STACK = [
  ["Gateway (Go)", "REST :8811 + SSE /api/stream + WebTransport :8812 (rotating cert), Ed25519 provenance chain over every trace"],
  ["Auth", "optional WebAuthn device pairing (MYCELIUM_GATEWAY_AUTH=1) — status below shows the live mode"],
  ["Storage", "SQLite substrate (swappable to Postgres via MYCELIUM_BACKEND=postgres on the Python side)"],
  ["Miners (Python)", "7 registered pattern miners, subprocess-sandboxed (RLIMIT_DATA) + a Wasm sandbox (wazero) via POST /api/mine/wasm"],
  ["A2A", "findings publish to the Vantage feed (a2a.py); anchor checkpoints push to Gitea (publish.py)"],
  ["Council tunnel", "/api/council/* + /api/picks proxy to the VPS Vantage API — the agent key never reaches the browser"],
  ["On-device", "WebNN anomaly scoring in this browser (#/ondevice + /web/webnn_miner.html)"],
] as const;

export class SystemView extends MyceliumElement {
  private status: GatewayStatus | null = null;
  private cert: { hash: string; expires: string } | null = null;
  private logs: { ts: string; level: string; msg: string }[] = [];

  protected render() {
    this.innerHTML = `
      <div class="view-header"><h2>System</h2><span class="sub">what's running where</span></div>
      <h3>Stack</h3>
      <table class="data-table"><tbody>
        ${STACK.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td><td class="muted">${esc(v)}</td></tr>`).join("")}
      </tbody></table>
      <h3>Live status</h3>
      <div data-el="live"></div>
      <h3>Storage &amp; retention</h3>
      <div data-el="storage"></div>
      <h3>Request inspector (last 50)</h3>
      <div data-el="requests"></div>
      <h3>Gateway log tail</h3>
      <div data-el="logs"></div>
    `;
  }

  protected mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 15_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetch() {
    try {
      this.status = await api.status();
    } catch {
      this.status = null;
    }
    try {
      this.cert = await api.certHash();
    } catch {
      this.cert = null;
    }
    try {
      this.logs = (await api.logs(50)).lines;
    } catch {
      this.logs = [];
    }
    this.renderLive();
    this.renderStorage();
    this.renderRequests();
    this.renderLogs();
  }

  private renderLive() {
    const el = this.querySelector<HTMLElement>('[data-el="live"]');
    if (!el) return;
    if (!this.status) {
      el.innerHTML = `<div class="empty-state">Gateway unreachable.</div>`;
      return;
    }
    const s = this.status;
    const up = s.uptime_secs != null ? formatDuration(s.uptime_secs) : "—";
    el.innerHTML = `<div class="cards">
      <div class="card"><div class="big">${esc(up)}</div><div class="lbl">gateway uptime</div></div>
      <div class="card"><div class="big">${s.auth_enabled ? "🔒" : "🔓"}</div><div class="lbl">WebAuthn ${s.auth_enabled ? "ON" : "off (loopback trust)"}</div></div>
      <div class="card"><div class="big">${s.traces}</div><div class="lbl">traces</div></div>
      <div class="card"><div class="big">${s.findings}</div><div class="lbl">findings</div></div>
      <div class="card"><div class="big" style="font-size:0.9em">${esc(s.pubkey.slice(0, 16))}…</div><div class="lbl">provenance pubkey</div></div>
      ${this.cert ? `<div class="card"><div class="big" style="font-size:0.9em">${esc(this.cert.hash.slice(0, 12))}…</div><div class="lbl">WT cert, expires ${esc(relTime(this.cert.expires).replace(" ago", ""))}</div></div>` : ""}
    </div>`;
  }

  private renderStorage() {
    const el = this.querySelector<HTMLElement>('[data-el="storage"]');
    if (!el) return;
    const st = this.status?.storage;
    if (!st) {
      el.innerHTML = `<div class="empty-state">No storage stats.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table"><tbody>
        <tr><td><b>substrate DB</b></td><td>${formatBytes(st.db_bytes ?? 0)}${st.wal_bytes ? ` (+ ${formatBytes(st.wal_bytes)} WAL)` : ""}</td></tr>
        <tr><td><b>oldest trace</b></td><td class="muted">${esc(st.oldest_ts || "—")}</td></tr>
        <tr><td><b>newest trace</b></td><td class="muted">${esc(st.newest_ts || "—")}</td></tr>
      </tbody></table>
      <p>
        <input type="text" data-el="prune-ts" placeholder="prune before (ISO ts, e.g. 2026-01-01T00:00:00Z)" size="42" />
        <button class="secondary" data-act="prune">Prune + re-anchor</button>
        <span class="muted" data-el="prune-result"></span>
      </p>
      <p class="muted">Prune deletes traces older than the timestamp AND rewrites the provenance
      anchor log from the surviving chain — the prune itself is recorded as a trace, so the
      action stays auditable.</p>`;
    this.querySelector('[data-act="prune"]')?.addEventListener("click", async () => {
      const input = this.querySelector<HTMLInputElement>('[data-el="prune-ts"]')!;
      const out = this.querySelector<HTMLElement>('[data-el="prune-result"]')!;
      const ts = input.value.trim();
      if (!ts) {
        out.textContent = "enter a timestamp first";
        return;
      }
      if (!confirm(`Delete all traces before ${ts} and re-anchor the chain? This cannot be undone.`)) return;
      try {
        const res = await api.prune(ts);
        out.textContent = `deleted ${res.deleted}, re-anchored ${res.reanchored}`;
        this.fetch();
      } catch (err) {
        out.textContent = `prune failed: ${err instanceof Error ? err.message : String(err)}`;
      }
    });
  }

  private renderRequests() {
    const el = this.querySelector<HTMLElement>('[data-el="requests"]');
    if (!el) return;
    const reqs = this.status?.last_requests ?? [];
    if (!reqs.length) {
      el.innerHTML = `<div class="empty-state">No API requests recorded since gateway start.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>ms</th></tr></thead>
        <tbody>
          ${reqs
            .slice()
            .reverse()
            .map(
              (r) => `<tr>
              <td class="muted" title="${esc(r.ts)}">${esc(relTime(r.ts))}</td>
              <td>${esc(r.method)}</td>
              <td><code>${esc(r.path)}</code></td>
              <td class="${r.status >= 400 ? "sell" : "muted"}">${r.status}</td>
              <td class="muted">${r.ms}</td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>`;
  }

  private renderLogs() {
    const el = this.querySelector<HTMLElement>('[data-el="logs"]');
    if (!el) return;
    if (!this.logs.length) {
      el.innerHTML = `<div class="empty-state">Log ring is empty.</div>`;
      return;
    }
    el.innerHTML = `<div class="json-drawer">${this.logs
      .map((l) => `${esc(l.ts)} [${esc(l.level)}] ${esc(l.msg)}`)
      .join("\n")}</div>`;
  }
}

function formatBytes(n: number): string {
  if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n > 1 << 10) return (n / (1 << 10)).toFixed(1) + " KB";
  return n + " B";
}

function formatDuration(secs: number): string {
  if (secs < 90) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 90) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 36) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}
