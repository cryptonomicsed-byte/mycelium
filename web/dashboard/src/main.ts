// Bootstrap: register components, seed the store from the REST snapshot,
// open the live SSE stream, mount the status bar + router.
import { StatusBar } from "./components/status-bar.js";
import { FindingCard } from "./components/finding-card.js";
import { LiveView } from "./views/live.js";
import { FindingsView } from "./views/findings.js";
import { ProvenanceView } from "./views/provenance.js";
import { WalletsView } from "./views/wallets.js";
import { MinersView } from "./views/miners.js";
import { OndeviceView } from "./views/ondevice.js";
import { startRouter } from "./router.js";
import { store } from "./store.js";
import { api, openStream } from "./api.js";

customElements.define("myc-status-bar", StatusBar);
customElements.define("myc-finding-card", FindingCard);
customElements.define("myc-live-view", LiveView);
customElements.define("myc-findings-view", FindingsView);
customElements.define("myc-provenance-view", ProvenanceView);
customElements.define("myc-wallets-view", WalletsView);
customElements.define("myc-miners-view", MinersView);
customElements.define("myc-ondevice-view", OndeviceView);

async function bootstrap() {
  // Best-effort initial snapshot -- if the gateway is briefly unreachable at
  // load time, the service worker's stale-while-revalidate cache (sw.js)
  // may still have last-known state to show; either way the SSE connect
  // below will retry on its own and the UI shouldn't hard-fail here.
  let sinceTraceTs = "";
  let sinceFindingTs = "";
  try {
    const [status, traces, findings] = await Promise.all([
      api.status(),
      api.traces({ limit: 100 }),
      api.findings({ limit: 200 }),
    ]);
    store.setStatus(status);
    store.seedTraces(traces.traces);
    store.seedFindings(findings.findings);
    sinceTraceTs = traces.traces[0]?.ts ?? "";
    sinceFindingTs = findings.findings.reduce((max, f) => (f.created_ts > max ? f.created_ts : max), "");
  } catch (err) {
    console.warn("mycelium: initial snapshot fetch failed, will rely on the live stream", err);
  }

  openStream(sinceTraceTs, sinceFindingTs, {
    onOpen: () => store.setConnState("open"),
    onClose: () => store.setConnState("connecting"),
    onTrace: (t) => store.pushTrace(t),
    onFinding: (f) => store.upsertFinding(f),
    onProvenance: (p) => store.setProvenance(p),
  });

  // /api/status counts don't stream -- refresh on a slow poll independent of
  // the SSE tick rate, just to keep the top bar's totals honest over a long
  // session.
  setInterval(() => {
    api.status().then((s) => store.setStatus(s)).catch(() => {});
  }, 30_000);

  const body = document.body;
  body.prepend(document.createElement("myc-status-bar"));
  const outlet = document.createElement("main");
  outlet.className = "view-outlet";
  body.appendChild(outlet);
  startRouter(outlet);
}

bootstrap();
