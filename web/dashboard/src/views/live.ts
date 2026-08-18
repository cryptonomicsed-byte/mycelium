// Live Trace Stream (#/live) -- SSE-fed scrolling feed of the substrate's
// raw traces, filterable by agent/kind/action/outcome. Colored by outcome
// and fading with age -- the "pheromone trail" visual from the plan: a
// trace is a signal that decays, exactly like the stigmergic metaphor this
// whole substrate is built on.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { store } from "../store.js";
import { traceChangedFilter } from "../trace.js";
import type { Trace, Kind, Outcome } from "../types.js";

const KINDS: Kind[] = [
  "tool_call", "decision", "memory_write", "error",
  "workflow_start", "workflow_end", "observation",
];
const OUTCOMES: Outcome[] = ["success", "failure", "partial", "info"];
const FADE_MS = 5 * 60 * 1000; // floor opacity reached 5 minutes after a trace lands

export class LiveView extends MyceliumElement {
  private agent = "";
  private kind = "";
  private outcome = "";
  private action = "";
  private wakeLock: { release(): Promise<void> } | null = null;
  private fadeTimer: ReturnType<typeof setInterval> | null = null;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Live Trace Stream</h2>
        <div class="view-filters">
          <select data-f="agent"><option value="">All agents</option></select>
          <select data-f="kind">
            <option value="">All kinds</option>
            ${KINDS.map((k) => `<option value="${k}">${k}</option>`).join("")}
          </select>
          <select data-f="outcome">
            <option value="">All outcomes</option>
            ${OUTCOMES.map((o) => `<option value="${o}">${o}</option>`).join("")}
          </select>
          <input type="text" data-f="action" placeholder="action contains…" />
        </div>
      </div>
      <div class="trace-feed" data-el="feed"></div>
    `;

    this.addEventListener("change", (e) => {
      const el = e.target as HTMLSelectElement;
      const f = el.dataset.f;
      if (f === "agent" || f === "kind" || f === "outcome") {
        this[f] = el.value;
        traceChangedFilter("live", `${f}=${el.value}`);
        this.renderFeed();
      }
    });
    this.addEventListener("input", (e) => {
      const el = e.target as HTMLInputElement;
      if (el.dataset.f !== "action") return;
      this.action = el.value;
      this.renderFeed();
    });

    this.renderFeed();
  }

  protected mount() {
    this.onDisconnect(store.subscribe(() => this.renderFeed()));
    this.fadeTimer = setInterval(() => this.renderFeed(), 5000);
    this.onDisconnect(() => {
      if (this.fadeTimer) clearInterval(this.fadeTimer);
    });
    this.requestWakeLock();
    this.onDisconnect(() => this.releaseWakeLock());
  }

  private async requestWakeLock() {
    try {
      const nav = navigator as Navigator & { wakeLock?: { request(type: "screen"): Promise<{ release(): Promise<void> }> } };
      if (nav.wakeLock) this.wakeLock = await nav.wakeLock.request("screen");
    } catch {
      // unsupported browser, denied permission, or backgrounded tab -- the
      // feed still works fine, it just won't keep the screen awake
    }
  }

  private releaseWakeLock() {
    this.wakeLock?.release().catch(() => {});
    this.wakeLock = null;
  }

  private passesFilters(t: Trace): boolean {
    if (this.agent && t.agent !== this.agent) return false;
    if (this.kind && t.kind !== this.kind) return false;
    if (this.outcome && t.outcome !== this.outcome) return false;
    if (this.action && !t.action.toLowerCase().includes(this.action.toLowerCase())) return false;
    return true;
  }

  private renderFeed() {
    const feed = this.querySelector<HTMLElement>('[data-el="feed"]');
    if (!feed) return;
    const { recentTraces } = store.get();

    // Repopulate the agent filter from whatever's actually live, rather
    // than hardcoding agent names anywhere in the dashboard.
    const agentSel = this.querySelector<HTMLSelectElement>('[data-f="agent"]')!;
    const agents = Array.from(new Set(recentTraces.map((t) => t.agent))).sort();
    const agentsKey = agents.join(",");
    if (agentSel.dataset.agentsKey !== agentsKey) {
      agentSel.dataset.agentsKey = agentsKey;
      const current = agentSel.value;
      agentSel.innerHTML =
        `<option value="">All agents</option>` +
        agents.map((a) => `<option value="${esc(a)}">${esc(a)}</option>`).join("");
      agentSel.value = current;
    }

    const rows = recentTraces.filter((t) => this.passesFilters(t));
    if (!rows.length) {
      feed.innerHTML = `<div class="empty-state">No traces match the current filters.</div>`;
      return;
    }
    const now = Date.now();
    feed.innerHTML = rows
      .map((t) => {
        const age = now - Date.parse(t.ts);
        const opacity = Math.max(0.3, 1 - age / FADE_MS).toFixed(2);
        return `
          <div class="trace-row trace-row--${esc(t.outcome)}" style="opacity:${opacity}">
            <span class="trace-row__ts" title="${esc(t.ts)}">${esc(relTime(t.ts))}</span>
            <span class="trace-row__agent">${esc(t.agent)}</span>
            <span class="trace-row__kind">${esc(t.kind)}</span>
            <span class="trace-row__action" title="${esc(t.target)}">${esc(t.action)} → ${esc(t.target)}</span>
            <span class="trace-row__outcome">${esc(t.outcome)}</span>
          </div>
        `;
      })
      .join("");
  }
}
