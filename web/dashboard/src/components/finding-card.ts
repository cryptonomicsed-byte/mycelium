// One finding, rendered with Apply/Dismiss actions. A plain custom element
// configured via a JS property (not attributes -- a Finding has nested
// JSON, awkward to serialize into an attribute string) and communicating
// outward via CustomEvents so the mounting view doesn't need to reach back
// into this component's internals to learn what happened.
import { MyceliumElement, esc, relTime } from "./base.js";
import { api } from "../api.js";
import { traceAppliedFinding, traceDismissedFinding, traceViewedFinding } from "../trace.js";
import type { Finding } from "../types.js";

export class FindingCard extends MyceliumElement {
  private _finding: Finding | null = null;

  set finding(f: Finding) {
    this._finding = f;
    if (this.isConnected) this.render();
  }
  get finding(): Finding {
    if (!this._finding) throw new Error("FindingCard.finding read before set");
    return this._finding;
  }

  protected render() {
    const f = this._finding;
    if (!f) {
      this.innerHTML = "";
      return;
    }
    const confPct = Math.round(f.confidence * 100);
    const stateClass = `finding-card--${f.state}`;
    this.className = `finding-card ${stateClass}`;
    this.innerHTML = `
      <div class="finding-card__head">
        <span class="finding-card__miner">${esc(f.miner)}</span>
        <span class="finding-card__suggestion badge badge--${esc(f.suggestion)}">${esc(f.suggestion)}</span>
        <span class="finding-card__conf" title="confidence">${confPct}%</span>
        <span class="finding-card__state">${esc(f.state)}</span>
      </div>
      <div class="finding-card__title">${esc(f.title)}</div>
      <details class="finding-card__evidence">
        <summary>${esc(f.created_ts)} (${esc(relTime(f.created_ts))})</summary>
        <p>${esc(f.evidence)}</p>
        <pre class="finding-card__payload">${esc(formatPayload(f.payload))}</pre>
      </details>
      ${
        f.state === "open"
          ? `<div class="finding-card__actions">
               <button data-act="apply">Apply</button>
               <button data-act="dismiss" class="secondary">Dismiss</button>
             </div>`
          : ""
      }
    `;

    this.querySelector('[data-act="apply"]')?.addEventListener("click", () => this.doApply());
    this.querySelector('[data-act="dismiss"]')?.addEventListener("click", () => this.doDismiss());
    this.querySelector("details")?.addEventListener(
      "toggle",
      (e) => {
        if ((e.target as HTMLDetailsElement).open) traceViewedFinding(f.id, f.miner);
      },
      { once: true },
    );
  }

  private async doApply() {
    const f = this.finding;
    const btn = this.querySelector<HTMLButtonElement>('[data-act="apply"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Applying…";
    }
    try {
      const result = await api.applyFinding(f.id);
      traceAppliedFinding(f.id, true);
      this.dispatchEvent(
        new CustomEvent("myc:finding-applied", { bubbles: true, detail: { id: f.id, result } }),
      );
    } catch (err) {
      traceAppliedFinding(f.id, false);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Apply";
      }
      this.dispatchEvent(
        new CustomEvent("myc:finding-error", {
          bubbles: true,
          detail: { id: f.id, action: "apply", error: String(err) },
        }),
      );
    }
  }

  private async doDismiss() {
    const f = this.finding;
    const btn = this.querySelector<HTMLButtonElement>('[data-act="dismiss"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Dismissing…";
    }
    try {
      await api.dismissFinding(f.id);
      traceDismissedFinding(f.id, true);
      this.dispatchEvent(new CustomEvent("myc:finding-dismissed", { bubbles: true, detail: { id: f.id } }));
    } catch (err) {
      traceDismissedFinding(f.id, false);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Dismiss";
      }
      this.dispatchEvent(
        new CustomEvent("myc:finding-error", {
          bubbles: true,
          detail: { id: f.id, action: "dismiss", error: String(err) },
        }),
      );
    }
  }
}

function formatPayload(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
