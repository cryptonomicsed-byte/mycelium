// Persistent top bar (all routes): live status counts, SSE connection
// state, and the provenance verify badge. The badge is deliberately here,
// not only inside #/provenance -- "impossible to miss" (per the plan) means
// visible from every view, not one route away from a tamper event.
import { MyceliumElement, esc } from "./base.js";
import { store } from "../store.js";
import { ROUTES } from "../router.js";
import { traceViewedTamperedProvenance } from "../trace.js";
import { webTransportSupported } from "../wt.js";
import { audioAlertsEnabled, enableAudioAlerts, playTamperAlert } from "../audio.js";

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
          <span class="status-bar__council" data-el="council" title="Ares Council daemon (via VPS tunnel)" hidden></span>
          <span class="status-bar__conn" data-el="conn">connecting…</span>
          ${
            webTransportSupported()
              ? `<button class="secondary status-bar__transport" data-el="transport-toggle"
                   title="Experimental: live updates over WebTransport instead of SSE">SSE</button>`
              : ""
          }
          <button class="secondary status-bar__audio" data-el="audio-toggle"
            title="Play a sound when the provenance chain flips to tampered">🔇 Sound alerts</button>
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
    const transportEl = this.querySelector<HTMLButtonElement>('[data-el="transport-toggle"]');
    const audioEl = this.querySelector<HTMLButtonElement>('[data-el="audio-toggle"]')!;

    transportEl?.addEventListener("click", () => {
      store.setTransport(store.get().transport === "sse" ? "webtransport" : "sse");
    });

    audioEl.addEventListener("click", () => {
      // enableAudioAlerts() must run inside this click handler -- that's
      // what actually unlocks AudioContext playback under browser autoplay
      // policy, a synthesized alert fired from a later store subscription
      // callback (no user gesture in that call stack) would be silently
      // blocked.
      enableAudioAlerts();
      audioEl.textContent = audioAlertsEnabled() ? "🔊 Sound alerts on" : "🔇 Sound alerts";
      audioEl.disabled = audioAlertsEnabled();
    });

    this.onDisconnect(
      store.subscribe((s) => {
        countsEl.textContent = s.status
          ? `${s.status.traces} traces · ${s.status.findings} findings`
          : "…";

        connEl.textContent =
          s.connState === "open" ? "● live" : s.connState === "connecting" ? "○ connecting" : "✕ offline";
        connEl.className = `status-bar__conn status-bar__conn--${s.connState}`;

        if (transportEl) {
          transportEl.textContent = s.transport === "webtransport" ? "WebTransport" : "SSE";
          transportEl.title =
            s.transport === "webtransport"
              ? "Live updates over WebTransport (experimental) -- click to switch back to SSE"
              : "Live updates over SSE -- click to try WebTransport (experimental)";
        }

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
            if (audioAlertsEnabled()) playTamperAlert();
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

    // Council daemon chip: polled, not streamed -- the council lives on the
    // VPS behind the proxy, so its state can't ride the local SSE stream.
    // Hidden entirely (not shown red) when the proxy is unreachable: on a
    // box without the tunnel this would otherwise be a permanent false
    // alarm in the corner of every view.
    const councilEl = this.querySelector<HTMLElement>('[data-el="council"]')!;
    const pollCouncil = async () => {
      try {
        const res = await fetch("/api/council/overview");
        if (!res.ok) throw new Error(String(res.status));
        const o = (await res.json()) as { daemon_running?: boolean; verdict_count?: number };
        if (typeof o.daemon_running !== "boolean") throw new Error("no daemon field");
        councilEl.hidden = false;
        councilEl.textContent = o.daemon_running ? `council ● ${o.verdict_count ?? 0}v` : "council ○ down";
        councilEl.className = `status-bar__council status-bar__council--${o.daemon_running ? "up" : "down"}`;
      } catch {
        councilEl.hidden = true;
      }
    };
    pollCouncil();
    const councilTimer = setInterval(pollCouncil, 30_000);
    this.onDisconnect(() => clearInterval(councilTimer));
  }
}
