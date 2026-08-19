// Bootstrap: register components, seed the store from the REST snapshot,
// open the live SSE stream, mount the status bar + router.
import { StatusBar } from "./components/status-bar.js";
import { FindingCard } from "./components/finding-card.js";
import { LockScreen } from "./components/lock-screen.js";
import { LiveView } from "./views/live.js";
import { TracesView } from "./views/traces.js";
import { CouncilView } from "./views/council.js";
import { PicksView } from "./views/picks.js";
import { FindingsView } from "./views/findings.js";
import { LoopView } from "./views/loop.js";
import { ProvenanceView } from "./views/provenance.js";
import { WalletsView } from "./views/wallets.js";
import { MinersView } from "./views/miners.js";
import { AlertsView } from "./views/alerts.js";
import { AgentsView } from "./views/agents.js";
import { StatsView } from "./views/stats.js";
import { SystemView } from "./views/system.js";
import { OndeviceView } from "./views/ondevice.js";
import { startRouter } from "./router.js";
import { store } from "./store.js";
import type { Transport } from "./store.js";
import { api, openStream, type StreamHandlers } from "./api.js";
import { openWebTransportStream } from "./wt.js";

// Fixed WT listener address (gateway/wt.go's wtAddr const) -- separate
// listener from the REST/SSE gateway, not derived from MYCELIUM_ADDR.
const WT_ORIGIN = "https://127.0.0.1:8812";

customElements.define("myc-status-bar", StatusBar);
customElements.define("myc-finding-card", FindingCard);
customElements.define("myc-lock-screen", LockScreen);
customElements.define("myc-live-view", LiveView);
customElements.define("myc-traces-view", TracesView);
customElements.define("myc-council-view", CouncilView);
customElements.define("myc-picks-view", PicksView);
customElements.define("myc-findings-view", FindingsView);
customElements.define("myc-loop-view", LoopView);
customElements.define("myc-provenance-view", ProvenanceView);
customElements.define("myc-wallets-view", WalletsView);
customElements.define("myc-miners-view", MinersView);
customElements.define("myc-alerts-view", AlertsView);
customElements.define("myc-agents-view", AgentsView);
customElements.define("myc-stats-view", StatsView);
customElements.define("myc-system-view", SystemView);
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

  // A 401 anywhere in that snapshot fetch flips store.locked (api.ts's
  // getJSON/postJSON) -- MYCELIUM_GATEWAY_AUTH=1 is set and there's no
  // valid session. Show the lock screen instead of the normal shell, and
  // skip opening the SSE stream (it'd 401 too, and just retry forever in
  // the background for no reason while the lock screen is up).
  if (store.get().locked) {
    document.body.appendChild(document.createElement("myc-lock-screen"));
    return;
  }

  // Exclusive live-update transport: SSE by default, WebTransport
  // (experimental, Chromium-only) if the status bar's toggle flips
  // store.transport. Never both at once -- closeCurrentStream() tears down
  // whichever is active before the other opens, since concurrent SSE+WT
  // push would double-insert into store.pushTrace (no id-based dedupe
  // there).
  let closeCurrentStream: (() => void) | null = null;

  function handlers(): StreamHandlers {
    return {
      onOpen: () => store.setConnState("open"),
      onClose: () => store.setConnState("connecting"),
      onTrace: (t) => store.pushTrace(t),
      onFinding: (f) => store.upsertFinding(f),
      onProvenance: (p) => store.setProvenance(p),
    };
  }

  function startTransport(transport: Transport, sinceTrace = "", sinceFinding = "") {
    closeCurrentStream?.();
    closeCurrentStream = null;
    store.setConnState("connecting");
    if (transport === "webtransport") {
      openWebTransportStream(WT_ORIGIN, handlers())
        .then((close) => {
          closeCurrentStream = close;
        })
        .catch((err) => {
          console.warn("mycelium: WebTransport connect failed, falling back to SSE", err);
          store.setTransport("sse");
        });
    } else {
      closeCurrentStream = openStream(sinceTrace, sinceFinding, handlers());
    }
  }

  let lastTransport = store.get().transport;
  store.subscribe((s) => {
    if (s.transport !== lastTransport) {
      lastTransport = s.transport;
      startTransport(s.transport);
    }
  });
  startTransport(lastTransport, sinceTraceTs, sinceFindingTs);

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
