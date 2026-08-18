// Findings Board (#/findings) -- grouped by state, filterable by miner and a
// confidence threshold. Apply/Dismiss are delegated entirely to
// <myc-finding-card> (components/finding-card.ts); this view's job is just
// grouping, filtering, and reacting to a high-confidence finding landing
// live (one buzz, only while the tab is focused -- not spam).
import { MyceliumElement, esc } from "../components/base.js";
import { store } from "../store.js";
import { traceChangedFilter } from "../trace.js";
import type { Finding, FindingState } from "../types.js";

const STATES: { key: FindingState; label: string }[] = [
  { key: "open", label: "Open" },
  { key: "applied", label: "Applied" },
  { key: "dismissed", label: "Dismissed" },
];
const VIBRATE_THRESHOLD = 0.8;

export class FindingsView extends MyceliumElement {
  private miner = "";
  private minConfidence = 0;
  private seenIds = new Set<string>();
  private lastMapRef: Map<string, Finding> | null = null;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Findings Board</h2>
        <div class="view-filters">
          <select data-f="miner"><option value="">All miners</option></select>
          <label>
            min confidence
            <input type="range" data-f="conf" min="0" max="0.95" step="0.05" value="0" />
            <span data-el="conf-val">0%</span>
          </label>
        </div>
      </div>
      ${STATES.map(
        (s) => `
        <h3>${s.label} <span data-el="count-${s.key}"></span></h3>
        <div data-el="group-${s.key}"></div>
      `,
      ).join("")}
    `;

    this.querySelector('[data-f="miner"]')!.addEventListener("change", (e) => {
      this.miner = (e.target as HTMLSelectElement).value;
      traceChangedFilter("findings", `miner=${this.miner}`);
      this.renderGroups();
    });
    const confInput = this.querySelector<HTMLInputElement>('[data-f="conf"]')!;
    confInput.addEventListener("input", () => {
      this.minConfidence = Number(confInput.value);
      this.querySelector('[data-el="conf-val"]')!.textContent = `${Math.round(this.minConfidence * 100)}%`;
      this.renderGroups();
    });
    confInput.addEventListener("change", () => {
      traceChangedFilter("findings", `min_confidence=${this.minConfidence}`);
    });

    this.addEventListener("myc:finding-error", (e) => {
      const { action, error } = (e as CustomEvent).detail;
      console.warn(`mycelium: ${action} failed`, error);
    });

    this.renderGroups();
  }

  protected mount() {
    this.lastMapRef = store.get().findingsById;
    this.seenIds = new Set(this.lastMapRef.keys());
    this.onDisconnect(
      store.subscribe((s) => {
        if (s.findingsById !== this.lastMapRef) {
          this.checkForAlerts(s.findingsById);
          this.lastMapRef = s.findingsById;
          this.renderGroups();
        }
      }),
    );
  }

  private checkForAlerts(findingsById: Map<string, Finding>) {
    for (const [id, f] of findingsById) {
      if (this.seenIds.has(id)) continue;
      this.seenIds.add(id);
      if (f.confidence >= VIBRATE_THRESHOLD && document.hasFocus() && "vibrate" in navigator) {
        navigator.vibrate(200);
      }
    }
  }

  private renderGroups() {
    const { findingsById } = store.get();
    const all = Array.from(findingsById.values());

    const minerSel = this.querySelector<HTMLSelectElement>('[data-f="miner"]')!;
    const miners = Array.from(new Set(all.map((f) => f.miner))).sort();
    const minersKey = miners.join(",");
    if (minerSel.dataset.minersKey !== minersKey) {
      minerSel.dataset.minersKey = minersKey;
      const current = minerSel.value;
      minerSel.innerHTML =
        `<option value="">All miners</option>` +
        miners.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
      minerSel.value = current;
    }

    const filtered = all.filter(
      (f) => (!this.miner || f.miner === this.miner) && f.confidence >= this.minConfidence,
    );
    filtered.sort((a, b) => (a.created_ts < b.created_ts ? 1 : -1));

    for (const s of STATES) {
      const rows = filtered.filter((f) => f.state === s.key);
      this.querySelector(`[data-el="count-${s.key}"]`)!.textContent = `(${rows.length})`;
      const group = this.querySelector(`[data-el="group-${s.key}"]`)!;
      if (!rows.length) {
        group.innerHTML = `<div class="empty-state">None.</div>`;
        continue;
      }
      group.replaceChildren(
        ...rows.map((f) => {
          const card = document.createElement("myc-finding-card") as HTMLElement & { finding: Finding };
          card.finding = f;
          return card;
        }),
      );
    }
  }
}
