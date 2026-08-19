// Trace Explorer (#/traces) -- the full substrate trace table, server-side
// filtered (agent/kind/outcome via /api/traces' existing filters), client-
// side searched, cursor-paginated on ts. Distinct from #/live: Live is the
// streaming pheromone-trail feed; this is the queryable archive.
//
// Deep links (work package Part 0 #4): filters round-trip through the hash
// query -- #/traces?agent=council&kind=error&outcome=failure -- so a
// filtered view is a shareable URL. The router only matches on the path
// segment, so the query part is ours to manage here.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { traceChangedFilter } from "../trace.js";
import { downloadCSV, downloadJSON, copyAsCurl } from "../export.js";
import type { Trace, Kind, Outcome } from "../types.js";

const KINDS: Kind[] = [
  "tool_call", "decision", "memory_write", "error",
  "workflow_start", "workflow_end", "observation",
];
const OUTCOMES: Outcome[] = ["success", "failure", "partial", "info"];
const PAGE_SIZE = 100;

function hashQuery(): URLSearchParams {
  const i = location.hash.indexOf("?");
  return new URLSearchParams(i >= 0 ? location.hash.slice(i + 1) : "");
}

export class TracesView extends MyceliumElement {
  private agent = "";
  private kind = "";
  private outcome = "";
  private search = "";
  private rows: Trace[] = [];
  private openId: string | null = null;
  private loading = true;
  private error = "";
  private exhausted = false;

  protected render() {
    // Seed filters from the deep link before first fetch.
    const q = hashQuery();
    this.agent = q.get("agent") ?? "";
    this.kind = q.get("kind") ?? "";
    this.outcome = q.get("outcome") ?? "";

    this.innerHTML = `
      <div class="view-header">
        <h2>Trace Explorer</h2>
        <div class="view-filters">
          <input type="text" data-f="agent" placeholder="agent" value="${esc(this.agent)}" size="12" />
          <select data-f="kind">
            <option value="">All kinds</option>
            ${KINDS.map((k) => `<option value="${k}" ${k === this.kind ? "selected" : ""}>${k}</option>`).join("")}
          </select>
          <select data-f="outcome">
            <option value="">All outcomes</option>
            ${OUTCOMES.map((o) => `<option value="${o}" ${o === this.outcome ? "selected" : ""}>${o}</option>`).join("")}
          </select>
          <input type="text" data-f="search" placeholder="search action/target…" size="18" />
          <button class="secondary" data-act="export-csv">⤓ CSV</button>
          <button class="secondary" data-act="export-json">⤓ JSON</button>
          <button class="secondary" data-act="curl" title="Copy this query as a curl command">curl</button>
        </div>
      </div>
      <div data-el="body"></div>
      <div style="margin-top:0.8em">
        <button data-act="more" class="secondary">Load older</button>
      </div>
    `;

    const refetch = () => {
      this.syncHash();
      traceChangedFilter("traces", `agent=${this.agent};kind=${this.kind};outcome=${this.outcome}`);
      this.rows = [];
      this.exhausted = false;
      this.fetchPage();
    };
    this.querySelector<HTMLInputElement>('[data-f="agent"]')!.addEventListener("change", (e) => {
      this.agent = (e.target as HTMLInputElement).value.trim();
      refetch();
    });
    this.querySelector<HTMLSelectElement>('[data-f="kind"]')!.addEventListener("change", (e) => {
      this.kind = (e.target as HTMLSelectElement).value;
      refetch();
    });
    this.querySelector<HTMLSelectElement>('[data-f="outcome"]')!.addEventListener("change", (e) => {
      this.outcome = (e.target as HTMLSelectElement).value;
      refetch();
    });
    this.querySelector<HTMLInputElement>('[data-f="search"]')!.addEventListener("input", (e) => {
      this.search = (e.target as HTMLInputElement).value.toLowerCase();
      this.renderBody();
    });
    this.querySelector('[data-act="more"]')!.addEventListener("click", () => this.fetchPage());
    this.querySelector('[data-act="export-csv"]')!.addEventListener("click", () =>
      downloadCSV(this.visibleRows() as unknown as Record<string, unknown>[], "traces"),
    );
    this.querySelector('[data-act="export-json"]')!.addEventListener("click", () =>
      downloadJSON(this.visibleRows(), "traces"),
    );
    this.querySelector('[data-act="curl"]')!.addEventListener("click", async (e) => {
      const btn = e.target as HTMLButtonElement;
      const ok = await copyAsCurl(this.apiPath());
      btn.textContent = ok ? "copied" : "clipboard unavailable";
      setTimeout(() => (btn.textContent = "curl"), 1500);
    });
  }

  protected mount() {
    this.fetchPage();
  }

  private apiPath(): string {
    const params = new URLSearchParams();
    if (this.agent) params.set("agent", this.agent);
    if (this.kind) params.set("kind", this.kind);
    if (this.outcome) params.set("outcome", this.outcome);
    params.set("limit", String(PAGE_SIZE));
    return `/api/traces?${params.toString()}`;
  }

  private syncHash() {
    const params = new URLSearchParams();
    if (this.agent) params.set("agent", this.agent);
    if (this.kind) params.set("kind", this.kind);
    if (this.outcome) params.set("outcome", this.outcome);
    const qs = params.toString();
    // Replace, don't push -- filter changes shouldn't pollute back-button
    // history one keystroke at a time.
    history.replaceState(null, "", `#/traces${qs ? "?" + qs : ""}`);
  }

  private async fetchPage() {
    this.loading = true;
    this.renderBody();
    try {
      const filters: Parameters<typeof api.traces>[0] = { limit: PAGE_SIZE };
      if (this.agent) filters.agent = this.agent;
      if (this.kind) filters.kind = this.kind as Kind;
      if (this.outcome) filters.outcome = this.outcome as Outcome;
      // Cursor pagination: /api/traces returns newest-first; "Load older"
      // re-queries with `before` = the oldest ts already held (ts < before,
      // the backward cursor added to handleTraces for exactly this view).
      const oldest = this.rows[this.rows.length - 1]?.ts;
      if (oldest) filters.before = oldest;
      const res = await api.traces(filters);
      const fresh = res.traces.filter((t) => !this.rows.some((r) => r.id === t.id));
      if (!fresh.length) this.exhausted = true;
      this.rows = [...this.rows, ...fresh];
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }

  private visibleRows(): Trace[] {
    if (!this.search) return this.rows;
    return this.rows.filter(
      (t) =>
        t.action.toLowerCase().includes(this.search) ||
        t.target.toLowerCase().includes(this.search) ||
        t.agent.toLowerCase().includes(this.search),
    );
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Failed to load traces: ${esc(this.error)}</div>`;
      return;
    }
    if (this.loading && !this.rows.length) {
      body.innerHTML = `<div class="empty-state">Loading…</div>`;
      return;
    }
    const rows = this.visibleRows();
    if (!rows.length) {
      body.innerHTML = `<div class="empty-state">No traces match — either the filters are too
        narrow or the agents haven't emitted anything matching yet.</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Time</th><th>Agent</th><th>Kind</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (t) => `
            <tr class="row-clickable" data-id="${esc(t.id)}">
              <td class="muted" title="${esc(t.ts)}">${esc(relTime(t.ts))}</td>
              <td>${esc(t.agent)}</td>
              <td>${esc(t.kind)}</td>
              <td><code>${esc(t.action)}</code></td>
              <td class="muted">${esc(t.target.slice(0, 48))}</td>
              <td class="${t.outcome === "failure" ? "sell" : t.outcome === "success" ? "buy" : "muted"}">${esc(t.outcome)}</td>
            </tr>
            ${
              this.openId === t.id
                ? `<tr><td colspan="6"><div class="json-drawer">${esc(prettyTrace(t))}</div></td></tr>`
                : ""
            }
          `,
            )
            .join("")}
        </tbody>
      </table>
      ${this.exhausted ? `<p class="muted">— end of substrate —</p>` : ""}
    `;
    body.querySelectorAll<HTMLTableRowElement>("tr.row-clickable").forEach((tr) =>
      tr.addEventListener("click", () => {
        const id = tr.dataset.id!;
        this.openId = this.openId === id ? null : id;
        this.renderBody();
      }),
    );
  }
}

function prettyTrace(t: Trace): string {
  let payload: unknown = t.payload;
  try {
    payload = JSON.parse(t.payload);
  } catch {
    // leave as raw string
  }
  return JSON.stringify({ ...t, payload }, null, 2);
}
