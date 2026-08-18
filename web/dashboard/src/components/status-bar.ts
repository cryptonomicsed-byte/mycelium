// Persistent top bar (all routes): live status counts, SSE connection
// state, and the provenance verify badge. The badge is deliberately here,
// not only inside #/provenance -- "impossible to miss" (per the plan) means
// visible from every view, not one route away from a tamper event.
import { MyceliumElement, esc } from "./base.js";
import { store } from "../store.js";
import { ROUTES } from "../router.js";
import { traceViewedTamperedProvenance } from "../trace.js";

export class StatusBar extends MyceliumElement {
  private lastKnownGoodValid: boolean | null = null;
  private toastedThisDivergence = false;
  private toastTimer: ReturnType<typeof setTimeout> | null = null;

  protected render() {
    this.innerHTML = `
      <div class="status-bar">
        <div class="status-bar__brand">MYCELIUM</div>
        <nav class="status-bar__nav">
          ${ROUTES.map((r) => `<a href="#${r.path}" data-path="${r.path}">${esc(r.label)}</a>`).join("")}
        </nav>
        <div class="status-bar__right">
          <span class="status-bar__counts" data-el="counts">…</span>
          <span class="status-bar__conn" data-el="conn">connecting…</span>
          <span class="status-bar__badge" data-el="badge">checking…</span>
        </div>
      </div>
      <div class="toast" data-el="toast" hidden></div>
    `;
    this.highlightActive();
    window.addEventListener("hashchange", this.highlightActive);
  }

  protected unmount() {
    window.removeEventListener("hashchange", this.highlightActive);
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }

  private highlightActive = () => {
    const path = location.hash.replace(/^#/, "") || ROUTES[0]!.path;
    for (const a of this.querySelectorAll<HTMLAnchorElement>(".status-bar__nav a")) {
      a.classList.toggle("active", a.dataset.path === path);
    }
  };

  protected mount() {
    const countsEl = this.querySelector<HTMLElement>('[data-el="counts"]')!;
    const connEl = this.querySelector<HTMLElement>('[data-el="conn"]')!;
    const badgeEl = this.querySelector<HTMLElement>('[data-el="badge"]')!;
    const toastEl = this.querySelector<HTMLElement>('[data-el="toast"]')!;

    this.onDisconnect(
      store.subscribe((s) => {
        countsEl.textContent = s.status
          ? `${s.status.traces} traces · ${s.status.findings} findings`
          : "…";

        connEl.textContent =
          s.connState === "open" ? "● live" : s.connState === "connecting" ? "○ connecting" : "✕ offline";
        connEl.className = `status-bar__conn status-bar__conn--${s.connState}`;

        if (s.provenance) {
          const { valid, anchored, reason } = s.provenance;
          badgeEl.className = `status-bar__badge status-bar__badge--${valid ? "ok" : "tamper"}`;
          badgeEl.textContent = valid
            ? `PROVENANCE OK (${anchored} anchored)`
            : `TAMPER DETECTED — ${reason}`;

          if (!valid && this.lastKnownGoodValid !== false && !this.toastedThisDivergence) {
            this.toastedThisDivergence = true;
            toastEl.textContent = `⚠ Provenance chain diverged: ${reason}`;
            toastEl.hidden = false;
            // Fixed-position, top-right -- exactly where several views put
            // their own top-right buttons (e.g. #/provenance's "Verify
            // now"). Auto-dismiss so a toast nobody happens to click can't
            // sit there indefinitely intercepting clicks underneath it; the
            // badge stays red as the persistent signal regardless.
            if (this.toastTimer) clearTimeout(this.toastTimer);
            this.toastTimer = setTimeout(() => {
              toastEl.hidden = true;
            }, 8000);
            traceViewedTamperedProvenance();
          }
          if (valid) this.toastedThisDivergence = false;
          this.lastKnownGoodValid = valid;
        }
      }),
    );

    toastEl.addEventListener("click", () => {
      toastEl.hidden = true;
      if (this.toastTimer) clearTimeout(this.toastTimer);
    });
  }
}
