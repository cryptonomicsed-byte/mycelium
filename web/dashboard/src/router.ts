// Hash-based routing -- bookmarkable URLs (#/live, #/findings, ...) without
// a router library or server-side route configuration (the Go gateway just
// serves index.html for /web/ and everything else is client-side).

export interface Route {
  path: string; // e.g. "/live"
  label: string;
  tag: string; // custom element tag name to mount
}

export const ROUTES: Route[] = [
  { path: "/live", label: "Live", tag: "myc-live-view" },
  { path: "/traces", label: "Traces", tag: "myc-traces-view" },
  { path: "/council", label: "Council", tag: "myc-council-view" },
  { path: "/picks", label: "Picks", tag: "myc-picks-view" },
  { path: "/pools", label: "Pools", tag: "myc-pools-view" },
  { path: "/findings", label: "Findings", tag: "myc-findings-view" },
  { path: "/loop", label: "Loop", tag: "myc-loop-view" },
  { path: "/provenance", label: "Provenance", tag: "myc-provenance-view" },
  { path: "/wallets", label: "Wallets", tag: "myc-wallets-view" },
  { path: "/miners", label: "Miners", tag: "myc-miners-view" },
  { path: "/alerts", label: "Alerts", tag: "myc-alerts-view" },
  { path: "/agents", label: "Agents", tag: "myc-agents-view" },
  { path: "/stats", label: "Stats", tag: "myc-stats-view" },
  { path: "/ondevice", label: "On-device", tag: "myc-ondevice-view" },
  { path: "/system", label: "System", tag: "myc-system-view" },
];

const DEFAULT_ROUTE = ROUTES[0]!;

function currentPath(): string {
  // Strip any deep-link query (#/traces?agent=council) -- routes match on
  // the path segment only; views read their own query via location.hash.
  const h = location.hash.replace(/^#/, "");
  const q = h.indexOf("?");
  return (q >= 0 ? h.slice(0, q) : h) || DEFAULT_ROUTE.path;
}

function routeFor(path: string): Route {
  return ROUTES.find((r) => r.path === path) ?? DEFAULT_ROUTE;
}

/** Mounts the route matching location.hash into `outlet`, and re-mounts on
 * every hashchange. Each view is a custom element -- connectedCallback /
 * disconnectedCallback (see MyceliumElement) handle its own subscriptions,
 * so swapping views is just replaceChildren, no manual teardown needed
 * here. */
export function startRouter(outlet: HTMLElement, onNavigate?: (route: Route) => void) {
  function mount() {
    const route = routeFor(currentPath());
    outlet.replaceChildren(document.createElement(route.tag));
    onNavigate?.(route);
  }
  window.addEventListener("hashchange", mount);
  if (!location.hash) location.hash = DEFAULT_ROUTE.path;
  mount();
}
