// Base class for every component/view in this app. Light DOM (no Shadow
// DOM) so the single global stylesheet (styles/app.css) applies everywhere
// without per-component style duplication -- appropriate at this app's
// scale (a handful of views), where Shadow DOM's encapsulation would cost
// more in repeated CSS than it'd save in isolation.
//
// Subclasses override render() to produce their innerHTML and mount() /
// unmount() for store subscriptions or timers -- connectedCallback /
// disconnectedCallback call through to these so cleanup is never optional.
export abstract class MyceliumElement extends HTMLElement {
  private unsubscribers: Array<() => void> = [];

  connectedCallback() {
    this.render();
    this.mount();
  }

  disconnectedCallback() {
    this.unmount();
    for (const fn of this.unsubscribers) fn();
    this.unsubscribers = [];
  }

  /** Register a cleanup function (store unsubscribe, clearInterval, etc.)
   * to run automatically when this element leaves the DOM. */
  protected onDisconnect(fn: () => void) {
    this.unsubscribers.push(fn);
  }

  protected abstract render(): void;
  protected mount(): void {
    /* optional override */
  }
  protected unmount(): void {
    /* optional override */
  }
}

/** Escapes text for safe interpolation into innerHTML -- every view renders
 * agent-controlled strings (trace targets, finding evidence, wallet
 * addresses), so this is load-bearing, not decorative. */
export function esc(s: unknown): string {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Relative time for trace/finding timestamps ("3s ago", "5m ago"). */
export function relTime(iso: string): string {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return iso;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}
