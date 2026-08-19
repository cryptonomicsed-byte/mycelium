import {
  webGPUSupported
} from "./chunk-EF7SS7UC.js";

// src/components/base.ts
var MyceliumElement = class extends HTMLElement {
  unsubscribers = [];
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
  onDisconnect(fn) {
    this.unsubscribers.push(fn);
  }
  mount() {
  }
  unmount() {
  }
};
function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function relTime(iso) {
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return iso;
  const s = Math.floor(ms / 1e3);
  if (s < 60) return `${s}s ago`;
  const m2 = Math.floor(s / 60);
  if (m2 < 60) return `${m2}m ago`;
  const h = Math.floor(m2 / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// src/store.ts
var MAX_RECENT_TRACES = 500;
var Store = class {
  state = {
    status: null,
    provenance: null,
    connState: "connecting",
    recentTraces: [],
    findingsById: /* @__PURE__ */ new Map(),
    locked: false,
    transport: "sse"
  };
  listeners = /* @__PURE__ */ new Set();
  get() {
    return this.state;
  }
  subscribe(fn) {
    this.listeners.add(fn);
    fn(this.state);
    return () => this.listeners.delete(fn);
  }
  notify() {
    for (const fn of this.listeners) fn(this.state);
  }
  setStatus(status) {
    this.state = { ...this.state, status };
    this.notify();
  }
  setProvenance(provenance) {
    this.state = { ...this.state, provenance };
    this.notify();
  }
  setConnState(connState) {
    this.state = { ...this.state, connState };
    this.notify();
  }
  setLocked(locked) {
    if (locked === this.state.locked) return;
    this.state = { ...this.state, locked };
    this.notify();
  }
  setTransport(transport) {
    if (transport === this.state.transport) return;
    this.state = { ...this.state, transport };
    this.notify();
  }
  pushTrace(trace) {
    const recentTraces = [trace, ...this.state.recentTraces].slice(0, MAX_RECENT_TRACES);
    this.state = { ...this.state, recentTraces };
    this.notify();
  }
  seedTraces(traces) {
    this.state = { ...this.state, recentTraces: traces.slice(0, MAX_RECENT_TRACES) };
    this.notify();
  }
  upsertFinding(finding) {
    const findingsById = new Map(this.state.findingsById);
    findingsById.set(finding.id, finding);
    this.state = { ...this.state, findingsById };
    this.notify();
  }
  seedFindings(findings) {
    const findingsById = new Map(findings.map((f) => [f.id, f]));
    this.state = { ...this.state, findingsById };
    this.notify();
  }
};
var store = new Store();

// src/router.ts
var ROUTES = [
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
  { path: "/system", label: "System", tag: "myc-system-view" }
];
var DEFAULT_ROUTE = ROUTES[0];
function currentPath() {
  const h = location.hash.replace(/^#/, "");
  const q = h.indexOf("?");
  return (q >= 0 ? h.slice(0, q) : h) || DEFAULT_ROUTE.path;
}
function routeFor(path) {
  return ROUTES.find((r) => r.path === path) ?? DEFAULT_ROUTE;
}
function startRouter(outlet, onNavigate) {
  function mount() {
    const route = routeFor(currentPath());
    outlet.replaceChildren(document.createElement(route.tag));
    onNavigate?.(route);
  }
  window.addEventListener("hashchange", mount);
  if (!location.hash) location.hash = DEFAULT_ROUTE.path;
  mount();
}

// src/api.ts
var GATEWAY_BASE = window.__MYCELIUM_GATEWAY_BASE__ ?? "";
function noteAuthStatus(status) {
  if (status === 401) store.setLocked(true);
}
async function getJSON(path) {
  const res = await fetch(GATEWAY_BASE + path);
  if (!res.ok) {
    noteAuthStatus(res.status);
    throw new Error(`GET ${path} -> ${res.status} ${await res.text().catch(() => "")}`);
  }
  return res.json();
}
async function postJSON(path, body) {
  const res = await fetch(GATEWAY_BASE + path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : void 0,
    body: body ? JSON.stringify(body) : void 0
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    noteAuthStatus(res.status);
    const msg = data?.error ?? `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}
function qs(params) {
  const parts = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== void 0 && v !== "") parts.push(`${k}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}
var api = {
  status: () => getJSON("/api/status"),
  traces: (filters = {}) => getJSON(`/api/traces${qs(filters)}`),
  findings: (filters = {}) => getJSON(`/api/findings${qs(filters)}`),
  applyFinding: (id) => postJSON(
    `/api/findings/${encodeURIComponent(id)}/apply`
  ),
  dismissFinding: (id) => postJSON(`/api/findings/${encodeURIComponent(id)}/dismiss`),
  miners: () => getJSON("/api/miners"),
  mine: () => postJSON("/api/mine"),
  mineWasm: (limit = 500) => postJSON(`/api/mine/wasm?limit=${limit}`),
  provenance: () => getJSON("/api/provenance"),
  provenanceVerify: () => getJSON("/api/provenance/verify"),
  emitTrace: (t) => postJSON("/api/trace", t),
  // Ops endpoints (gateway/ops.go)
  agents: () => getJSON("/api/agents"),
  skills: () => getJSON("/api/skills"),
  alerts: () => getJSON("/api/alerts"),
  logs: (lines = 200, level = "") => getJSON(
    `/api/logs${qs({ lines, level })}`
  ),
  statsTimeseries: (range = "24h") => getJSON(
    `/api/stats/timeseries?range=${range}`
  ),
  prune: (beforeTs) => postJSON("/api/prune", { before_ts: beforeTs }),
  picks: () => getJSON("/api/picks"),
  poolHealth: () => getJSON("/api/poolhealth"),
  certHash: () => getJSON("/api/webtransport/cert-hash")
};
function openStream(sinceTraceTs, sinceFindingTs, handlers) {
  let closed = false;
  let es = null;
  let backoffMs = 1e3;
  const maxBackoffMs = 3e4;
  function connect() {
    if (closed) return;
    const url = `${GATEWAY_BASE}/api/stream${qs({
      since_trace_ts: sinceTraceTs,
      since_finding_ts: sinceFindingTs
    })}`;
    es = new EventSource(url);
    es.addEventListener("open", () => {
      backoffMs = 1e3;
      handlers.onOpen?.();
    });
    es.addEventListener("trace", (ev) => {
      const t = JSON.parse(ev.data);
      sinceTraceTs = t.ts;
      handlers.onTrace?.(t);
    });
    es.addEventListener("finding", (ev) => {
      const f = JSON.parse(ev.data);
      sinceFindingTs = f.created_ts;
      handlers.onFinding?.(f);
    });
    es.addEventListener("provenance", (ev) => {
      handlers.onProvenance?.(JSON.parse(ev.data));
    });
    es.addEventListener("error", () => {
      handlers.onClose?.();
      es?.close();
      es = null;
      if (closed) return;
      setTimeout(connect, backoffMs);
      backoffMs = Math.min(backoffMs * 2, maxBackoffMs);
    });
  }
  connect();
  return () => {
    closed = true;
    es?.close();
  };
}

// src/trace.ts
var SESSION_KEY = "mycelium.dashboard.session_id";
function sessionId() {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}
function emit(kind, action, target, outcome, payload) {
  api.emitTrace({
    agent: "dashboard-ui",
    session: sessionId(),
    kind,
    action,
    target,
    outcome,
    payload
  }).catch(() => {
  });
}
function traceViewedFinding(findingId, miner) {
  emit("observation", "view_finding", findingId, "info", { miner });
}
function traceAppliedFinding(findingId, ok) {
  emit("decision", "apply_finding", findingId, ok ? "success" : "failure");
}
function traceDismissedFinding(findingId, ok) {
  emit("decision", "dismiss_finding", findingId, ok ? "success" : "failure");
}
function traceForcedMine(kind, ok) {
  emit("tool_call", kind === "wasm" ? "force_mine_wasm" : "force_mine", "substrate", ok ? "success" : "failure");
}
function traceChangedFilter(view, filter) {
  emit("observation", "change_filter", `${view}:${filter}`, "info");
}
function traceViewedTamperedProvenance() {
  emit("observation", "view_provenance_tampered", "chain", "info");
}

// src/wt.ts
function webTransportSupported() {
  return typeof window !== "undefined" && "WebTransport" in window;
}
function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}
function dispatch(envelope, handlers) {
  switch (envelope.type) {
    case "trace":
      handlers.onTrace?.(envelope.data);
      break;
    case "finding":
      handlers.onFinding?.(envelope.data);
      break;
    case "provenance":
      handlers.onProvenance?.(envelope.data);
      break;
  }
}
async function readBroadcastStream(stream, handlers) {
  const reader = stream.getReader();
  let buf = new Uint8Array(0);
  function append(chunk) {
    const next = new Uint8Array(buf.length + chunk.length);
    next.set(buf);
    next.set(chunk, buf.length);
    buf = next;
  }
  for (; ; ) {
    const { value, done } = await reader.read();
    if (done) return;
    if (value) append(value);
    for (; ; ) {
      if (buf.length < 4) break;
      const len = (buf[0] << 24 | buf[1] << 16 | buf[2] << 8 | buf[3]) >>> 0;
      if (buf.length < 4 + len) break;
      const bodyBytes = buf.slice(4, 4 + len);
      buf = buf.slice(4 + len);
      try {
        dispatch(JSON.parse(new TextDecoder().decode(bodyBytes)), handlers);
      } catch {
      }
    }
  }
}
async function openWebTransportStream(wtOrigin, handlers) {
  const res = await fetch("/api/webtransport/cert-hash");
  if (!res.ok) throw new Error(`cert-hash fetch failed: ${res.status}`);
  const { hash } = await res.json();
  const transport = new WebTransport(`${wtOrigin}/api/wt`, {
    serverCertificateHashes: [{ algorithm: "sha-256", value: b64ToBytes(hash) }]
  });
  let closed = false;
  transport.closed.then(() => {
    if (!closed) handlers.onClose?.();
  }).catch(() => {
    if (!closed) handlers.onClose?.();
  });
  await transport.ready;
  handlers.onOpen?.();
  (async () => {
    try {
      const reader = transport.incomingUnidirectionalStreams.getReader();
      const { value: stream, done } = await reader.read();
      if (done || !stream) return;
      await readBroadcastStream(stream, handlers);
    } catch {
      if (!closed) handlers.onClose?.();
    }
  })();
  return () => {
    closed = true;
    try {
      transport.close();
    } catch {
    }
  };
}

// src/audio.ts
var ctx = null;
function audioAlertsEnabled() {
  return ctx !== null;
}
function enableAudioAlerts() {
  if (ctx) return;
  const Ctor = window.AudioContext || window.webkitAudioContext;
  if (!Ctor) return;
  ctx = new Ctor();
}
function playTamperAlert() {
  if (!ctx) return;
  const now2 = ctx.currentTime;
  const panner = new StereoPannerNode(ctx, { pan: -1 });
  panner.connect(ctx.destination);
  panner.pan.setValueAtTime(-1, now2);
  panner.pan.linearRampToValueAtTime(1, now2 + 0.6);
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0, now2);
  gain.gain.linearRampToValueAtTime(0.15, now2 + 0.02);
  gain.gain.exponentialRampToValueAtTime(1e-3, now2 + 0.6);
  gain.connect(panner);
  const osc = ctx.createOscillator();
  osc.type = "sawtooth";
  osc.frequency.setValueAtTime(440, now2);
  osc.frequency.exponentialRampToValueAtTime(220, now2 + 0.6);
  osc.connect(gain);
  osc.start(now2);
  osc.stop(now2 + 0.6);
}

// src/components/status-bar.ts
var StatusBar = class extends MyceliumElement {
  lastKnownGoodValid = null;
  toastedThisDivergence = false;
  toastTimer = null;
  render() {
    this.innerHTML = `
      <div class="status-bar">
        <div class="status-bar__brand">MYCELIUM</div>
        <nav class="status-bar__nav">
          ${ROUTES.map((r) => `<a href="#${r.path}" data-path="${r.path}">${esc(r.label)}</a>`).join("")}
        </nav>
        <div class="status-bar__right">
          <span class="status-bar__counts" data-el="counts">\u2026</span>
          <span class="status-bar__council" data-el="council" title="Ares Council daemon (via VPS tunnel)" hidden></span>
          <span class="status-bar__conn" data-el="conn">connecting\u2026</span>
          ${webTransportSupported() ? `<button class="secondary status-bar__transport" data-el="transport-toggle"
                   title="Experimental: live updates over WebTransport instead of SSE">SSE</button>` : ""}
          <button class="secondary status-bar__audio" data-el="audio-toggle"
            title="Play a sound when the provenance chain flips to tampered">\u{1F507} Sound alerts</button>
          <span class="status-bar__badge" data-el="badge">checking\u2026</span>
        </div>
      </div>
      <div class="toast" data-el="toast" hidden></div>
    `;
    this.highlightActive();
    window.addEventListener("hashchange", this.highlightActive);
  }
  unmount() {
    window.removeEventListener("hashchange", this.highlightActive);
    if (this.toastTimer) clearTimeout(this.toastTimer);
  }
  highlightActive = () => {
    const path = location.hash.replace(/^#/, "") || ROUTES[0].path;
    for (const a2 of this.querySelectorAll(".status-bar__nav a")) {
      a2.classList.toggle("active", a2.dataset.path === path);
    }
  };
  mount() {
    const countsEl = this.querySelector('[data-el="counts"]');
    const connEl = this.querySelector('[data-el="conn"]');
    const badgeEl = this.querySelector('[data-el="badge"]');
    const toastEl = this.querySelector('[data-el="toast"]');
    const transportEl = this.querySelector('[data-el="transport-toggle"]');
    const audioEl = this.querySelector('[data-el="audio-toggle"]');
    transportEl?.addEventListener("click", () => {
      store.setTransport(store.get().transport === "sse" ? "webtransport" : "sse");
    });
    audioEl.addEventListener("click", () => {
      enableAudioAlerts();
      audioEl.textContent = audioAlertsEnabled() ? "\u{1F50A} Sound alerts on" : "\u{1F507} Sound alerts";
      audioEl.disabled = audioAlertsEnabled();
    });
    this.onDisconnect(
      store.subscribe((s) => {
        countsEl.textContent = s.status ? `${s.status.traces} traces \xB7 ${s.status.findings} findings` : "\u2026";
        connEl.textContent = s.connState === "open" ? "\u25CF live" : s.connState === "connecting" ? "\u25CB connecting" : "\u2715 offline";
        connEl.className = `status-bar__conn status-bar__conn--${s.connState}`;
        if (transportEl) {
          transportEl.textContent = s.transport === "webtransport" ? "WebTransport" : "SSE";
          transportEl.title = s.transport === "webtransport" ? "Live updates over WebTransport (experimental) -- click to switch back to SSE" : "Live updates over SSE -- click to try WebTransport (experimental)";
        }
        if (s.provenance) {
          const { valid, anchored, reason } = s.provenance;
          badgeEl.className = `status-bar__badge status-bar__badge--${valid ? "ok" : "tamper"}`;
          badgeEl.textContent = valid ? `PROVENANCE OK (${anchored} anchored)` : `TAMPER DETECTED \u2014 ${reason}`;
          if (!valid && this.lastKnownGoodValid !== false && !this.toastedThisDivergence) {
            this.toastedThisDivergence = true;
            toastEl.textContent = `\u26A0 Provenance chain diverged: ${reason}`;
            toastEl.hidden = false;
            if (audioAlertsEnabled()) playTamperAlert();
            if (this.toastTimer) clearTimeout(this.toastTimer);
            this.toastTimer = setTimeout(() => {
              toastEl.hidden = true;
            }, 8e3);
            traceViewedTamperedProvenance();
          }
          if (valid) this.toastedThisDivergence = false;
          this.lastKnownGoodValid = valid;
        }
      })
    );
    toastEl.addEventListener("click", () => {
      toastEl.hidden = true;
      if (this.toastTimer) clearTimeout(this.toastTimer);
    });
    const councilEl = this.querySelector('[data-el="council"]');
    const pollCouncil = async () => {
      try {
        const res = await fetch("/api/council/overview");
        if (!res.ok) throw new Error(String(res.status));
        const o = await res.json();
        if (typeof o.daemon_running !== "boolean") throw new Error("no daemon field");
        councilEl.hidden = false;
        councilEl.textContent = o.daemon_running ? `council \u25CF ${o.verdict_count ?? 0}v` : "council \u25CB down";
        councilEl.className = `status-bar__council status-bar__council--${o.daemon_running ? "up" : "down"}`;
      } catch {
        councilEl.hidden = true;
      }
    };
    pollCouncil();
    const councilTimer = setInterval(pollCouncil, 3e4);
    this.onDisconnect(() => clearInterval(councilTimer));
  }
};

// src/components/finding-card.ts
var FindingCard = class extends MyceliumElement {
  _finding = null;
  set finding(f) {
    this._finding = f;
    if (this.isConnected) this.render();
  }
  get finding() {
    if (!this._finding) throw new Error("FindingCard.finding read before set");
    return this._finding;
  }
  render() {
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
      <div class="conf-bar" title="confidence ${confPct}%"><div class="conf-bar__fill" style="width:${confPct}%"></div></div>
      <details class="finding-card__evidence">
        <summary>${esc(f.created_ts)} (${esc(relTime(f.created_ts))})</summary>
        <p>${esc(f.evidence)}</p>
        <pre class="finding-card__payload">${esc(formatPayload(f.payload))}</pre>
      </details>
      ${f.state === "open" ? `<div class="finding-card__actions">
               <button data-act="apply">Apply</button>
               <button data-act="dismiss" class="secondary">Dismiss</button>
             </div>` : ""}
    `;
    this.querySelector('[data-act="apply"]')?.addEventListener("click", () => this.doApply());
    this.querySelector('[data-act="dismiss"]')?.addEventListener("click", () => this.doDismiss());
    this.querySelector("details")?.addEventListener(
      "toggle",
      (e) => {
        if (e.target.open) traceViewedFinding(f.id, f.miner);
      },
      { once: true }
    );
  }
  async doApply() {
    const f = this.finding;
    const btn = this.querySelector('[data-act="apply"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Applying\u2026";
    }
    try {
      const result = await api.applyFinding(f.id);
      traceAppliedFinding(f.id, true);
      this.dispatchEvent(
        new CustomEvent("myc:finding-applied", { bubbles: true, detail: { id: f.id, result } })
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
          detail: { id: f.id, action: "apply", error: String(err) }
        })
      );
    }
  }
  async doDismiss() {
    const f = this.finding;
    const btn = this.querySelector('[data-act="dismiss"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Dismissing\u2026";
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
          detail: { id: f.id, action: "dismiss", error: String(err) }
        })
      );
    }
  }
};
function formatPayload(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

// src/auth.ts
function b64uToBuf(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}
function bufToB64u(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function webAuthnSupported() {
  return typeof window !== "undefined" && !!window.PublicKeyCredential;
}
async function authPostJSON(path, body) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : void 0,
    body: body ? JSON.stringify(body) : void 0
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error ?? `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}
async function register() {
  const begin = await authPostJSON("/api/auth/register/begin");
  const pk = begin.publicKey;
  pk.challenge = b64uToBuf(pk.challenge);
  pk.user.id = b64uToBuf(pk.user.id);
  if (pk.excludeCredentials) {
    for (const c2 of pk.excludeCredentials) c2.id = b64uToBuf(c2.id);
  }
  const cred = await navigator.credentials.create({ publicKey: pk });
  const attestation = cred.response;
  await authPostJSON("/api/auth/register/finish", {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64u(attestation.clientDataJSON),
      attestationObject: bufToB64u(attestation.attestationObject)
    }
  });
}
async function login() {
  const begin = await authPostJSON("/api/auth/login/begin");
  const pk = begin.publicKey;
  pk.challenge = b64uToBuf(pk.challenge);
  if (pk.allowCredentials) {
    for (const c2 of pk.allowCredentials) c2.id = b64uToBuf(c2.id);
  }
  const assertion = await navigator.credentials.get({ publicKey: pk });
  const ar = assertion.response;
  await authPostJSON("/api/auth/login/finish", {
    id: assertion.id,
    rawId: bufToB64u(assertion.rawId),
    type: assertion.type,
    response: {
      clientDataJSON: bufToB64u(ar.clientDataJSON),
      authenticatorData: bufToB64u(ar.authenticatorData),
      signature: bufToB64u(ar.signature),
      userHandle: ar.userHandle ? bufToB64u(ar.userHandle) : null
    }
  });
}

// src/components/lock-screen.ts
var LockScreen = class extends MyceliumElement {
  render() {
    if (!webAuthnSupported()) {
      this.innerHTML = `
        <div class="lock-screen">
          <h2>Mycelium is locked</h2>
          <p class="empty-state">This browser doesn't support WebAuthn (navigator.credentials).
          Open the dashboard in a modern browser to pair a device or sign in.</p>
        </div>
      `;
      return;
    }
    this.innerHTML = `
      <div class="lock-screen">
        <h2>Mycelium is locked</h2>
        <p>Sign in with a previously-paired device, or pair this one for the first time.</p>
        <div class="lock-screen__actions">
          <button data-act="login">Sign in</button>
          <button data-act="register" class="secondary">Pair this device</button>
        </div>
        <p class="lock-screen__status" data-el="status"></p>
      </div>
    `;
    this.querySelector('[data-act="login"]').addEventListener("click", () => this.doLogin());
    this.querySelector('[data-act="register"]').addEventListener("click", () => this.doRegister());
  }
  setStatus(msg) {
    const el = this.querySelector('[data-el="status"]');
    if (el) el.textContent = msg;
  }
  async doLogin() {
    this.setStatus("Waiting for your device\u2026");
    try {
      await login();
      location.reload();
    } catch (err) {
      this.setStatus(`Sign-in failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  async doRegister() {
    this.setStatus("Pairing this device\u2026");
    try {
      await register();
      this.setStatus("Device paired. Signing you in\u2026");
      await login();
      location.reload();
    } catch (err) {
      this.setStatus(`Pairing failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
};

// src/views/live.ts
var KINDS = [
  "tool_call",
  "decision",
  "memory_write",
  "error",
  "workflow_start",
  "workflow_end",
  "observation"
];
var OUTCOMES = ["success", "failure", "partial", "info"];
var FADE_MS = 5 * 60 * 1e3;
var LiveView = class extends MyceliumElement {
  agent = "";
  kind = "";
  outcome = "";
  action = "";
  wakeLock = null;
  fadeTimer = null;
  shaderBg = null;
  orientationHandler = null;
  render() {
    this.innerHTML = `
      ${webGPUSupported() ? `<canvas class="live-shader-bg" data-el="shader-bg"></canvas>` : ""}
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
          <input type="text" data-f="action" placeholder="action contains\u2026" />
        </div>
      </div>
      <div class="trace-feed" data-el="feed"></div>
    `;
    this.addEventListener("change", (e) => {
      const el = e.target;
      const f = el.dataset.f;
      if (f === "agent" || f === "kind" || f === "outcome") {
        this[f] = el.value;
        traceChangedFilter("live", `${f}=${el.value}`);
        this.renderFeed();
      }
    });
    this.addEventListener("input", (e) => {
      const el = e.target;
      if (el.dataset.f !== "action") return;
      this.action = el.value;
      this.renderFeed();
    });
    this.renderFeed();
  }
  mount() {
    this.onDisconnect(store.subscribe(() => this.renderFeed()));
    this.fadeTimer = setInterval(() => this.renderFeed(), 5e3);
    this.onDisconnect(() => {
      if (this.fadeTimer) clearInterval(this.fadeTimer);
    });
    this.requestWakeLock();
    this.onDisconnect(() => this.releaseWakeLock());
    this.mountShaderBackground();
    this.onDisconnect(() => this.shaderBg?.stop());
  }
  async mountShaderBackground() {
    const canvas = this.querySelector('[data-el="shader-bg"]');
    if (!canvas) return;
    const { mountLiveBackground } = await import("./live-background-N7HOOIAV.js");
    if (!this.isConnected) return;
    const bg = await mountLiveBackground(canvas);
    if (!bg || !this.isConnected) {
      bg?.stop();
      return;
    }
    this.shaderBg = bg;
    this.wireTiltParallax(bg);
  }
  /** Feeds device tilt into the shader's parallax uniform -- mobile-only in
   * practice (deviceorientation just never fires on desktop, so this is
   * inert there, not conditionally skipped). iOS 13+ gates the event
   * behind an explicit tap (DeviceOrientationEvent.requestPermission()),
   * which can't be requested without a user gesture, so on iOS this shows
   * a small opt-in button instead of silently doing nothing. */
  wireTiltParallax(bg) {
    const iosGate = window.DeviceOrientationEvent;
    const attach = () => {
      this.orientationHandler = (e) => {
        const x3 = (e.gamma ?? 0) / 45;
        const y3 = (e.beta ?? 0) / 45;
        bg.setTilt(Math.max(-1, Math.min(1, x3)), Math.max(-1, Math.min(1, y3)));
      };
      window.addEventListener("deviceorientation", this.orientationHandler);
      this.onDisconnect(() => {
        if (this.orientationHandler) window.removeEventListener("deviceorientation", this.orientationHandler);
      });
    };
    if (typeof iosGate?.requestPermission !== "function") {
      attach();
      return;
    }
    const btn = document.createElement("button");
    btn.className = "secondary live-tilt-enable";
    btn.textContent = "Enable tilt parallax";
    btn.addEventListener("click", () => {
      iosGate.requestPermission().then((result) => {
        if (result === "granted") attach();
        btn.remove();
      }).catch(() => btn.remove());
    });
    this.querySelector(".view-header")?.appendChild(btn);
  }
  async requestWakeLock() {
    try {
      const nav = navigator;
      if (nav.wakeLock) this.wakeLock = await nav.wakeLock.request("screen");
    } catch {
    }
  }
  releaseWakeLock() {
    this.wakeLock?.release().catch(() => {
    });
    this.wakeLock = null;
  }
  passesFilters(t) {
    if (this.agent && t.agent !== this.agent) return false;
    if (this.kind && t.kind !== this.kind) return false;
    if (this.outcome && t.outcome !== this.outcome) return false;
    if (this.action && !t.action.toLowerCase().includes(this.action.toLowerCase())) return false;
    return true;
  }
  renderFeed() {
    const feed = this.querySelector('[data-el="feed"]');
    if (!feed) return;
    const { recentTraces } = store.get();
    const agentSel = this.querySelector('[data-f="agent"]');
    const agents = Array.from(new Set(recentTraces.map((t) => t.agent))).sort();
    const agentsKey = agents.join(",");
    if (agentSel.dataset.agentsKey !== agentsKey) {
      agentSel.dataset.agentsKey = agentsKey;
      const current = agentSel.value;
      agentSel.innerHTML = `<option value="">All agents</option>` + agents.map((a2) => `<option value="${esc(a2)}">${esc(a2)}</option>`).join("");
      agentSel.value = current;
    }
    const rows = recentTraces.filter((t) => this.passesFilters(t));
    if (!rows.length) {
      feed.innerHTML = `<div class="empty-state">No traces match the current filters.</div>`;
      return;
    }
    const now2 = Date.now();
    feed.innerHTML = rows.map((t) => {
      const age = now2 - Date.parse(t.ts);
      const opacity = Math.max(0.3, 1 - age / FADE_MS).toFixed(2);
      return `
          <div class="trace-row trace-row--${esc(t.outcome)}" style="opacity:${opacity}">
            <span class="trace-row__ts" title="${esc(t.ts)}">${esc(relTime(t.ts))}</span>
            <span class="trace-row__agent">${esc(t.agent)}</span>
            <span class="trace-row__kind">${esc(t.kind)}</span>
            <span class="trace-row__action" title="${esc(t.target)}">${esc(t.action)} \u2192 ${esc(t.target)}</span>
            <span class="trace-row__outcome">${esc(t.outcome)}</span>
          </div>
        `;
    }).join("");
  }
};

// src/export.ts
function download(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a2 = document.createElement("a");
  a2.href = url;
  a2.download = filename;
  a2.click();
  setTimeout(() => URL.revokeObjectURL(url), 5e3);
}
function stamp() {
  return (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
}
function downloadCSV(rows, name) {
  if (!rows.length) return;
  const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  const cell = (v) => {
    if (v == null) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return `"${s.replace(/"/g, '""')}"`;
  };
  const lines = [cols.map((c2) => cell(c2)).join(",")];
  for (const r of rows) {
    lines.push(cols.map((c2) => cell(r[c2])).join(","));
  }
  download(new Blob([lines.join("\n")], { type: "text/csv" }), `${name}-${stamp()}.csv`);
}
function downloadJSON(data, name) {
  download(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    `${name}-${stamp()}.json`
  );
}
async function copyAsCurl(apiPath) {
  const cmd = `curl -s ${location.origin}${apiPath}`;
  try {
    await navigator.clipboard.writeText(cmd);
    return true;
  } catch {
    return false;
  }
}

// src/views/traces.ts
var KINDS2 = [
  "tool_call",
  "decision",
  "memory_write",
  "error",
  "workflow_start",
  "workflow_end",
  "observation"
];
var OUTCOMES2 = ["success", "failure", "partial", "info"];
var PAGE_SIZE = 100;
function hashQuery() {
  const i = location.hash.indexOf("?");
  return new URLSearchParams(i >= 0 ? location.hash.slice(i + 1) : "");
}
var TracesView = class extends MyceliumElement {
  agent = "";
  kind = "";
  outcome = "";
  search = "";
  rows = [];
  openId = null;
  loading = true;
  error = "";
  exhausted = false;
  render() {
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
            ${KINDS2.map((k) => `<option value="${k}" ${k === this.kind ? "selected" : ""}>${k}</option>`).join("")}
          </select>
          <select data-f="outcome">
            <option value="">All outcomes</option>
            ${OUTCOMES2.map((o) => `<option value="${o}" ${o === this.outcome ? "selected" : ""}>${o}</option>`).join("")}
          </select>
          <input type="text" data-f="search" placeholder="search action/target\u2026" size="18" />
          <button class="secondary" data-act="export-csv">\u2913 CSV</button>
          <button class="secondary" data-act="export-json">\u2913 JSON</button>
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
    this.querySelector('[data-f="agent"]').addEventListener("change", (e) => {
      this.agent = e.target.value.trim();
      refetch();
    });
    this.querySelector('[data-f="kind"]').addEventListener("change", (e) => {
      this.kind = e.target.value;
      refetch();
    });
    this.querySelector('[data-f="outcome"]').addEventListener("change", (e) => {
      this.outcome = e.target.value;
      refetch();
    });
    this.querySelector('[data-f="search"]').addEventListener("input", (e) => {
      this.search = e.target.value.toLowerCase();
      this.renderBody();
    });
    this.querySelector('[data-act="more"]').addEventListener("click", () => this.fetchPage());
    this.querySelector('[data-act="export-csv"]').addEventListener(
      "click",
      () => downloadCSV(this.visibleRows(), "traces")
    );
    this.querySelector('[data-act="export-json"]').addEventListener(
      "click",
      () => downloadJSON(this.visibleRows(), "traces")
    );
    this.querySelector('[data-act="curl"]').addEventListener("click", async (e) => {
      const btn = e.target;
      const ok = await copyAsCurl(this.apiPath());
      btn.textContent = ok ? "copied" : "clipboard unavailable";
      setTimeout(() => btn.textContent = "curl", 1500);
    });
  }
  mount() {
    this.fetchPage();
  }
  apiPath() {
    const params = new URLSearchParams();
    if (this.agent) params.set("agent", this.agent);
    if (this.kind) params.set("kind", this.kind);
    if (this.outcome) params.set("outcome", this.outcome);
    params.set("limit", String(PAGE_SIZE));
    return `/api/traces?${params.toString()}`;
  }
  syncHash() {
    const params = new URLSearchParams();
    if (this.agent) params.set("agent", this.agent);
    if (this.kind) params.set("kind", this.kind);
    if (this.outcome) params.set("outcome", this.outcome);
    const qs2 = params.toString();
    history.replaceState(null, "", `#/traces${qs2 ? "?" + qs2 : ""}`);
  }
  async fetchPage() {
    this.loading = true;
    this.renderBody();
    try {
      const filters = { limit: PAGE_SIZE };
      if (this.agent) filters.agent = this.agent;
      if (this.kind) filters.kind = this.kind;
      if (this.outcome) filters.outcome = this.outcome;
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
  visibleRows() {
    if (!this.search) return this.rows;
    return this.rows.filter(
      (t) => t.action.toLowerCase().includes(this.search) || t.target.toLowerCase().includes(this.search) || t.agent.toLowerCase().includes(this.search)
    );
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Failed to load traces: ${esc(this.error)}</div>`;
      return;
    }
    if (this.loading && !this.rows.length) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    const rows = this.visibleRows();
    if (!rows.length) {
      body.innerHTML = `<div class="empty-state">No traces match \u2014 either the filters are too
        narrow or the agents haven't emitted anything matching yet.</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Time</th><th>Agent</th><th>Kind</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead>
        <tbody>
          ${rows.map(
      (t) => `
            <tr class="row-clickable" data-id="${esc(t.id)}">
              <td class="muted" title="${esc(t.ts)}">${esc(relTime(t.ts))}</td>
              <td>${esc(t.agent)}</td>
              <td>${esc(t.kind)}</td>
              <td><code>${esc(t.action)}</code></td>
              <td class="muted">${esc(t.target.slice(0, 48))}</td>
              <td class="${t.outcome === "failure" ? "sell" : t.outcome === "success" ? "buy" : "muted"}">${esc(t.outcome)}</td>
            </tr>
            ${this.openId === t.id ? `<tr><td colspan="6"><div class="json-drawer">${esc(prettyTrace(t))}</div></td></tr>` : ""}
          `
    ).join("")}
        </tbody>
      </table>
      ${this.exhausted ? `<p class="muted">\u2014 end of substrate \u2014</p>` : ""}
    `;
    body.querySelectorAll("tr.row-clickable").forEach(
      (tr) => tr.addEventListener("click", () => {
        const id = tr.dataset.id;
        this.openId = this.openId === id ? null : id;
        this.renderBody();
      })
    );
  }
};
function prettyTrace(t) {
  let payload = t.payload;
  try {
    payload = JSON.parse(t.payload);
  } catch {
  }
  return JSON.stringify({ ...t, payload }, null, 2);
}

// src/views/council.ts
var GATES = [
  ["Risk veto", "the Risk persona can unilaterally block any trade"],
  ["Contrarian double-dissent", "two contrarian dissents in one debate kill the verdict"],
  ["Liquidity floor", "entry liquidity must be \u2265 $5,000"],
  ["Conviction threshold", "weighted conviction \u2265 0.60 for PAPER, \u2265 0.70 for LIVE"],
  ["PAPER default", "all verdicts execute as paper trades until LIVE is explicitly enabled"],
  ["Two debate rounds", "every verdict passes through two full persona debate rounds"]
];
var CouncilView = class extends MyceliumElement {
  tab = "verdicts";
  verdicts = [];
  calibration = [];
  overview = null;
  substrate = {};
  error = "";
  loaded = false;
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Council</h2>
        <span class="sub">Ares debate verdicts, calibration, substrate</span>
        <div class="view-filters">
          <button class="secondary" data-act="export-csv" title="Download the current tab as CSV">\u2913 CSV</button>
          <button class="secondary" data-act="export-json" title="Download the current tab as JSON">\u2913 JSON</button>
        </div>
      </div>
      <div class="tabs">
        ${["verdicts", "calibration", "council", "overview", "substrate"].map((t) => `<button class="tab-btn ${this.tab === t ? "active" : ""}" data-tab="${t}">${t}</button>`).join("")}
      </div>
      <div data-el="body"></div>
    `;
    this.querySelectorAll(".tab-btn").forEach(
      (btn) => btn.addEventListener("click", () => {
        this.tab = btn.dataset.tab;
        this.render();
        this.renderBody();
      })
    );
    this.querySelector('[data-act="export-csv"]')?.addEventListener("click", () => this.exportTab("csv"));
    this.querySelector('[data-act="export-json"]')?.addEventListener("click", () => this.exportTab("json"));
    if (this.loaded) this.renderBody();
  }
  mount() {
    this.fetchAll();
    const timer2 = setInterval(() => this.fetchAll(), 3e4);
    this.onDisconnect(() => clearInterval(timer2));
  }
  exportTab(format) {
    const name = `council-${this.tab}`;
    const data = this.tab === "calibration" ? this.calibration : this.tab === "overview" ? this.overview : this.tab === "substrate" ? this.substrate : this.verdicts;
    if (format === "json") {
      downloadJSON(data, name);
    } else {
      const rows = Array.isArray(data) ? data : [data];
      downloadCSV(rows, name);
    }
  }
  async fetchAll() {
    try {
      const [v, c2, o, s] = await Promise.all([
        fetch("/api/council/verdicts?limit=20").then((r) => r.json()),
        fetch("/api/council/calibration").then((r) => r.json()),
        fetch("/api/council/overview").then((r) => r.json()),
        fetch("/api/council/substrate").then((r) => r.json())
      ]);
      this.verdicts = Array.isArray(v) ? v : [];
      this.calibration = Array.isArray(c2) ? c2 : [];
      this.overview = o && typeof o === "object" && !o.error ? o : null;
      this.substrate = s && typeof s === "object" ? s : {};
      this.error = "";
    } catch (e) {
      this.error = String(e);
    }
    this.loaded = true;
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector("[data-el='body']");
    if (!body) return;
    if (!this.loaded) {
      body.innerHTML = `<p class="muted">Loading\u2026</p>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Council proxy unreachable: ${esc(this.error)}<br>
        The council lives on the VPS \u2014 this panel needs the tunnel up and the gateway's
        MYCELIUM_COUNCIL_BASE pointing at it.</div>`;
      return;
    }
    switch (this.tab) {
      case "calibration":
        body.innerHTML = this.renderCalibration();
        break;
      case "council":
        body.innerHTML = this.renderCouncilInfo();
        break;
      case "overview":
        body.innerHTML = this.renderOverview();
        break;
      case "substrate":
        body.innerHTML = this.renderSubstrate();
        break;
      default:
        body.innerHTML = this.renderVerdicts();
    }
  }
  renderVerdicts() {
    if (!this.verdicts.length) {
      return `<div class="empty-state">No verdicts yet \u2014 the council needs signals in the pool
        and two debate rounds before a verdict lands.</div>`;
    }
    const rows = this.verdicts.map((v) => {
      const dir = esc(v.direction || "?");
      const conv = v.conviction != null ? v.conviction.toFixed(2) : "\u2014";
      const liq = v.entry_liq ?? v.entry_price;
      const liqStr = liq != null ? `$${Number(liq).toLocaleString()}` : "\u2014";
      const out = esc(v.outcome || "pending");
      const mode = v.paper ? `<span class="badge badge--skill">PAPER</span>` : `<span class="badge badge--config_fix">LIVE</span>`;
      const voteChips = (v.votes || []).map(
        (vv) => `<span class="vote ${esc((vv.direction || "").toLowerCase())}">${esc(vv.persona)}:${esc(vv.direction || "?")} ${(vv.confidence ?? 0).toFixed(2)}\xD7${(vv.weight ?? 0).toFixed(2)}</span>`
      ).join(" ");
      const rationales = (v.votes || []).filter((vv) => vv.rationale).map((vv) => `<p><b>${esc(vv.persona)}</b> \u2014 ${esc(vv.rationale)}</p>`).join("");
      return `<tr>
          <td>#${Number(v.id)}</td>
          <td><b>${esc(v.symbol)}</b></td>
          <td class="${dir === "SELL" ? "sell" : dir === "BUY" ? "buy" : ""}">${dir}</td>
          <td>${conv}</td>
          <td>${liqStr}</td>
          <td>${out}</td>
          <td>${mode}</td>
          <td class="muted" title="${esc(v.posted_at || "")}">${esc(v.posted_at ? relTime(v.posted_at) : "\u2014")}</td>
          <td class="votes">
            ${voteChips}
            ${rationales ? `<details class="finding-card__evidence"><summary>rationales</summary>${rationales}</details>` : ""}
          </td>
        </tr>`;
    }).join("");
    return `<table class="data-table">
      <thead><tr><th>#</th><th>Symbol</th><th>Dir</th><th>Conv</th><th>Entry liq</th><th>Outcome</th><th>Mode</th><th>Time</th><th>Votes</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }
  renderCalibration() {
    if (!this.calibration.length) return `<div class="empty-state">No calibration data yet \u2014 personas calibrate as verdict outcomes resolve.</div>`;
    const rows = this.calibration.map((c2) => {
      const rate = c2.rate != null ? `${(c2.rate * 100).toFixed(0)}%` : "\u2014";
      return `<tr>
          <td><b>${esc(c2.persona)}</b>${c2.veto ? ' <span class="badge badge--alert">veto</span>' : ""}</td>
          <td class="muted">${esc(c2.role || "")}</td>
          <td>${c2.base_weight}</td>
          <td>${rate}</td>
          <td>${c2.correct}/${c2.total}</td>
          <td>${c2.multiplier != null ? c2.multiplier.toFixed(2) : "\u2014"}</td>
          <td><b>${c2.eff_weight != null ? c2.eff_weight.toFixed(2) : "\u2014"}</b></td>
        </tr>`;
    }).join("");
    return `
      <table class="data-table">
        <thead><tr><th>Persona</th><th>Role</th><th>Base w</th><th>Win rate</th><th>Correct</th><th>Multiplier</th><th>Eff w</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p class="muted">effective weight = base \xD7 multiplier, clamped to [0.2, 2.0] \u2014 the multiplier
      moves with each persona's tracked win rate, so consistently-right personas speak louder.</p>`;
  }
  renderCouncilInfo() {
    const personaRows = this.calibration.length ? this.calibration.map(
      (c2) => `<tr>
              <td><b>${esc(c2.persona)}</b></td>
              <td class="muted">${esc(c2.role || "")}</td>
              <td>${c2.base_weight}</td>
              <td>${c2.veto ? '<span class="badge badge--alert">veto</span>' : "\u2014"}</td>
            </tr>`
    ).join("") : `<tr><td colspan="4" class="muted">personas load from calibration data</td></tr>`;
    const gateRows = GATES.map(
      ([name, desc]) => `<tr><td><b>${esc(name)}</b></td><td class="muted">${esc(desc)}</td></tr>`
    ).join("");
    return `
      <h3>Personas &amp; objectives</h3>
      <table class="data-table">
        <thead><tr><th>Persona</th><th>Objective</th><th>Base weight</th><th>Veto</th></tr></thead>
        <tbody>${personaRows}</tbody>
      </table>
      <h3>Gates</h3>
      <table class="data-table"><tbody>${gateRows}</tbody></table>`;
  }
  renderOverview() {
    if (!this.overview) return `<div class="empty-state">No overview data.</div>`;
    const o = this.overview;
    const my = o.mycelium;
    return `<div class="cards">
      <div class="card"><div class="big">${o.daemon_running ? "\u25CF" : "\u25CB"}</div><div class="lbl">daemon ${o.daemon_running ? "running" : "down"} (pid ${esc(o.daemon_pid || "\u2014")})</div></div>
      <div class="card"><div class="big">${Number(o.verdict_count) || 0}</div><div class="lbl">verdicts</div></div>
      <div class="card"><div class="big">${Number(o.trace_buffer_pending) || 0}</div><div class="lbl">traces pending</div></div>
      ${o.signal_pool_count != null ? `<div class="card"><div class="big">${Number(o.signal_pool_count)}</div><div class="lbl">signal pool${o.signal_sources?.length ? " \xB7 " + esc(o.signal_sources.join(", ")) : ""}</div></div>` : ""}
      ${my ? `<div class="card"><div class="big">${Number(my.traces) || 0}</div><div class="lbl">substrate traces</div></div>
      <div class="card"><div class="big">${Number(my.findings) || 0}</div><div class="lbl">findings</div></div>` : ""}
    </div>`;
  }
  renderSubstrate() {
    const my = this.substrate.mycelium;
    const traces = this.substrate.council_traces || [];
    const findings = this.substrate.council_findings || [];
    const badge = my ? `<span class="badge ${my.status === "ok" ? "badge--skill" : "badge--config_fix"}">mycelium ${esc(my.status || "?")}</span>
         <span class="muted">${Number(my.traces) || 0} traces \xB7 ${Number(my.findings) || 0} findings \xB7 gateway reached via tunnel</span>` : `<span class="muted">no mycelium block in substrate payload</span>`;
    const traceRows = traces.slice(0, 30).map(
      (t) => `<tr>
          <td class="muted" title="${esc(t.ts || "")}">${esc(t.ts ? relTime(t.ts) : "\u2014")}</td>
          <td>${esc(t.kind || "")}</td>
          <td><code>${esc(t.action || "")}</code></td>
          <td class="muted">${esc(String(t.target || "").slice(0, 60))}</td>
        </tr>`
    ).join("");
    const findingRows = findings.map(
      (f) => `<div class="finding-card finding-card--${esc(f.state || "open")}">
          <div class="finding-card__head">
            <span class="finding-card__miner">${esc(f.miner || "council")}</span>
            <span class="finding-card__state">${esc(f.state || "open")}</span>
          </div>
          <div class="finding-card__title">${esc(f.title || f.id || "")}</div>
        </div>`
    ).join("");
    return `
      <p>${badge}</p>
      <h3>Council traces (latest 30)</h3>
      ${traces.length ? `<table class="data-table"><thead><tr><th>Time</th><th>Kind</th><th>Action</th><th>Target</th></tr></thead><tbody>${traceRows}</tbody></table>` : `<div class="empty-state">No council traces in the substrate yet.</div>`}
      <h3>Council findings</h3>
      ${findings.length ? findingRows : `<div class="empty-state">No council findings yet.</div>`}`;
  }
};

// src/views/picks.ts
var PicksView = class extends MyceliumElement {
  picks = [];
  openRank = null;
  loading = true;
  error = "";
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Picks</h2>
        <span class="sub">signal fusion \u2014 best tokens, with receipts</span>
        <button class="secondary" data-act="export">\u2913 JSON</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="export"]').addEventListener(
      "click",
      () => downloadJSON(this.picks, "picks")
    );
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 6e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    try {
      const res = await api.picks();
      this.picks = Array.isArray(res) ? res : res.picks ?? [];
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Fusion engine unreachable: ${esc(this.error)}<br>
        Picks come from ares-signal-fusion on the VPS via the gateway proxy \u2014 this panel lights
        up once the engine is deployed and the tunnel is up (signal_fusion/README.md).</div>`;
      return;
    }
    if (!this.picks.length) {
      body.innerHTML = `<div class="empty-state">No picks this window \u2014 every candidate either
        scored too low or hit a hard gate (liquidity, holder concentration, bundler share\u2026).
        A quiet picks list is the gates working, not the engine idle.</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>#</th><th>Symbol</th><th>Score</th><th>Status</th><th>When</th><th></th></tr></thead>
        <tbody>
          ${this.picks.map(
      (p) => `
            <tr class="row-clickable" data-rank="${Number(p.rank)}">
              <td><b>#${Number(p.rank)}</b></td>
              <td><b>${esc(p.symbol)}</b> <span class="muted">${esc((p.token_addr || "").slice(0, 10))}\u2026</span></td>
              <td>
                <div class="conf-bar" style="width:120px"><div class="conf-bar__fill" style="width:${Math.min(100, Number(p.score) || 0)}%"></div></div>
                ${Number(p.score).toFixed(1)}
              </td>
              <td class="muted">${esc(p.status || "candidate")}</td>
              <td class="muted" title="${esc(p.ts || "")}">${esc(p.ts ? relTime(p.ts) : "\u2014")}</td>
              <td class="muted">${this.openRank === p.rank ? "\u25B2" : "\u25BC"}</td>
            </tr>
            ${this.openRank === p.rank ? `<tr><td colspan="6"><div class="json-drawer">${esc(JSON.stringify({ components: p.components ?? {}, gates: p.gates ?? {} }, null, 2))}</div></td></tr>` : ""}`
    ).join("")}
        </tbody>
      </table>`;
    body.querySelectorAll("tr.row-clickable").forEach(
      (tr) => tr.addEventListener("click", () => {
        const rank = Number(tr.dataset.rank);
        this.openRank = this.openRank === rank ? null : rank;
        this.renderBody();
      })
    );
  }
};

// src/views/pools.ts
var PoolsView = class extends MyceliumElement {
  health = null;
  loading = true;
  error = "";
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Key Pools</h2>
        <span class="sub">gmgn / solscan / teamorouter keys + proxy pool health</span>
      </div>
      <div data-el="body"></div>
    `;
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 6e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    try {
      this.health = await api.poolHealth();
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  pct(n, total) {
    return total > 0 ? Math.min(100, Math.round(n / total * 100)) : 0;
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    if (this.error || !this.health) {
      body.innerHTML = `<div class="empty-state">Pool health unreachable: ${esc(this.error || "no data")}<br>
        Comes from ares-poolhealth on the VPS via the gateway proxy \u2014 the tile lights up once
        the service is up and the tunnel is live.</div>`;
      return;
    }
    const h = this.health;
    const g = h.gmgn ?? { keys: 0, cooling: 0, ip_banned: false, ip_ban_until: 0 };
    const p = h.proxies ?? { total: 0, cooldown: 0, live_estimate: 0 };
    const banLeft = g.ip_ban_until > (h.ts ?? 0) ? Math.round(g.ip_ban_until - (h.ts ?? 0)) : 0;
    body.innerHTML = `
      <div class="pool-grid">
        <div class="pool-card ${g.ip_banned ? "warn" : ""}">
          <h3>GMGN</h3>
          <div class="pool-stat"><b>${g.keys}</b> keys</div>
          <div class="pool-stat muted">${g.cooling} cooling (quota cooldown)</div>
          <div class="pool-stat ${g.ip_banned ? "bad" : "good"}">
            ${g.ip_banned ? `IP banned \u2014 ${Math.floor(banLeft / 60)}m left` : "IP clean"}
          </div>
          <div class="conf-bar"><div class="conf-bar__fill ${g.ip_banned ? "warn" : ""}"
            style="width:${100 - this.pct(Math.max(g.cooling, 1), Math.max(g.keys, 1))}%"></div></div>
        </div>
        <div class="pool-card">
          <h3>Proxies</h3>
          <div class="pool-stat"><b>${p.total}</b> in pool</div>
          <div class="pool-stat muted">${p.cooldown} in cooldown</div>
          <div class="pool-stat ${(p.live_estimate ?? 0) > 0 ? "good" : "muted"}">
            ${p.live_estimate ?? 0} live estimate
          </div>
          <div class="conf-bar"><div class="conf-bar__fill"
            style="width:${this.pct(p.live_estimate ?? 0, p.total)}%"></div></div>
        </div>
        <div class="pool-card">
          <h3>Solscan</h3>
          <div class="pool-stat"><b>${h.solscan?.keys ?? 0}</b> keys</div>
          <div class="pool-stat muted">dormant \u2014 free tier reads nothing (paywalled)</div>
        </div>
        <div class="pool-card">
          <h3>TeamoRouter</h3>
          <div class="pool-stat"><b>${h.teamorouter?.keys ?? 0}</b> keys</div>
          <div class="pool-stat muted">farm cron: every 20 min</div>
        </div>
      </div>
      <div class="pool-footer muted">refreshes every 60s \xB7 last update ${h.ts ? esc(relTime(h.ts)) : "\u2014"}</div>
    `;
  }
};

// src/views/findings.ts
var STATES = [
  { key: "open", label: "Open" },
  { key: "applied", label: "Applied" },
  { key: "dismissed", label: "Dismissed" }
];
var VIBRATE_THRESHOLD = 0.8;
var FindingsView = class extends MyceliumElement {
  miner = "";
  minConfidence = 0;
  seenIds = /* @__PURE__ */ new Set();
  lastMapRef = null;
  render() {
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
      `
    ).join("")}
    `;
    this.querySelector('[data-f="miner"]').addEventListener("change", (e) => {
      this.miner = e.target.value;
      traceChangedFilter("findings", `miner=${this.miner}`);
      this.renderGroups();
    });
    const confInput = this.querySelector('[data-f="conf"]');
    confInput.addEventListener("input", () => {
      this.minConfidence = Number(confInput.value);
      this.querySelector('[data-el="conf-val"]').textContent = `${Math.round(this.minConfidence * 100)}%`;
      this.renderGroups();
    });
    confInput.addEventListener("change", () => {
      traceChangedFilter("findings", `min_confidence=${this.minConfidence}`);
    });
    this.addEventListener("myc:finding-error", (e) => {
      const { action, error } = e.detail;
      console.warn(`mycelium: ${action} failed`, error);
    });
    this.renderGroups();
  }
  mount() {
    this.lastMapRef = store.get().findingsById;
    this.seenIds = new Set(this.lastMapRef.keys());
    this.onDisconnect(
      store.subscribe((s) => {
        if (s.findingsById !== this.lastMapRef) {
          this.checkForAlerts(s.findingsById);
          this.lastMapRef = s.findingsById;
          this.renderGroups();
        }
      })
    );
  }
  checkForAlerts(findingsById) {
    for (const [id, f] of findingsById) {
      if (this.seenIds.has(id)) continue;
      this.seenIds.add(id);
      if (f.confidence >= VIBRATE_THRESHOLD && document.hasFocus() && "vibrate" in navigator) {
        navigator.vibrate(200);
      }
    }
  }
  renderGroups() {
    const { findingsById } = store.get();
    const all = Array.from(findingsById.values());
    const minerSel = this.querySelector('[data-f="miner"]');
    const miners = Array.from(new Set(all.map((f) => f.miner))).sort();
    const minersKey = miners.join(",");
    if (minerSel.dataset.minersKey !== minersKey) {
      minerSel.dataset.minersKey = minersKey;
      const current = minerSel.value;
      minerSel.innerHTML = `<option value="">All miners</option>` + miners.map((m2) => `<option value="${esc(m2)}">${esc(m2)}</option>`).join("");
      minerSel.value = current;
    }
    const filtered = all.filter(
      (f) => (!this.miner || f.miner === this.miner) && f.confidence >= this.minConfidence
    );
    filtered.sort((a2, b) => a2.created_ts < b.created_ts ? 1 : -1);
    for (const s of STATES) {
      const rows = filtered.filter((f) => f.state === s.key);
      this.querySelector(`[data-el="count-${s.key}"]`).textContent = `(${rows.length})`;
      const group = this.querySelector(`[data-el="group-${s.key}"]`);
      if (!rows.length) {
        group.innerHTML = `<div class="empty-state">None.</div>`;
        continue;
      }
      group.replaceChildren(
        ...rows.map((f) => {
          const card = document.createElement("myc-finding-card");
          card.finding = f;
          return card;
        })
      );
    }
  }
};

// src/views/loop.ts
var LoopView = class extends MyceliumElement {
  skills = [];
  skillsError = "";
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Self-improvement Loop</h2>
        <span class="sub">traces \u2192 mined \u2192 findings \u2192 applied \u2192 skills</span>
      </div>
      <div data-el="pipeline"></div>
      <h3>Applied findings \u2192 generated skills</h3>
      <div data-el="applied"></div>
      <h3>Generated skills on disk</h3>
      <div data-el="skills"></div>
      <p class="muted">Skills are hot-swappable: agents that load SKILL.md files pick up new ones
      without a restart \u2014 a finding applied here changes future agent behavior with no human in
      the loop.</p>
    `;
  }
  mount() {
    this.onDisconnect(store.subscribe(() => this.renderPipeline()));
    this.fetchSkills();
    const t = setInterval(() => this.fetchSkills(), 3e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetchSkills() {
    try {
      const res = await api.skills();
      this.skills = res.skills;
      this.skillsError = "";
    } catch (err) {
      this.skillsError = err instanceof Error ? err.message : String(err);
    }
    this.renderSkills();
    this.renderApplied();
  }
  counts() {
    const s = store.get();
    const findings = Array.from(s.findingsById.values());
    return {
      traces: s.status?.traces ?? s.recentTraces.length,
      findings: findings.length,
      open: findings.filter((f) => f.state === "open").length,
      applied: findings.filter((f) => f.state === "applied").length,
      skills: this.skills.length
    };
  }
  renderPipeline() {
    const el = this.querySelector('[data-el="pipeline"]');
    if (!el) return;
    const c2 = this.counts();
    const stage = (n, lbl) => `<div class="loop-stage"><div class="big">${n}</div><div class="lbl">${lbl}</div></div>`;
    el.innerHTML = `
      <div class="loop-pipeline">
        ${stage(c2.traces, "traces")}
        <span class="loop-arrow">\u2192</span>
        ${stage("7", "miners")}
        <span class="loop-arrow">\u2192</span>
        ${stage(c2.findings, "findings")}
        <span class="loop-arrow">\u2192</span>
        ${stage(c2.applied, "applied")}
        <span class="loop-arrow">\u2192</span>
        ${stage(c2.skills, "skills")}
      </div>`;
  }
  appliedFindings() {
    return Array.from(store.get().findingsById.values()).filter((f) => f.state === "applied").sort((a2, b) => a2.created_ts < b.created_ts ? 1 : -1);
  }
  renderApplied() {
    const el = this.querySelector('[data-el="applied"]');
    if (!el) return;
    const applied = this.appliedFindings();
    if (!applied.length) {
      el.innerHTML = `<div class="empty-state">No applied findings yet \u2014 apply one from the
        Findings board (or let the cron auto-apply at \u22650.9 confidence) and its artifact
        appears here.</div>`;
      return;
    }
    const skillNames = new Set(this.skills.map((s) => s.name));
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Applied</th><th>Miner</th><th>Finding</th><th>Suggestion</th><th>Artifact</th></tr></thead>
        <tbody>
          ${applied.map((f) => {
      const slug = slugOf(f);
      const hasSkill = slug && skillNames.has(slug);
      return `<tr>
                <td class="muted" title="${esc(f.created_ts)}">${esc(relTime(f.created_ts))}</td>
                <td>${esc(f.miner)}</td>
                <td>${esc(f.title)}</td>
                <td><span class="badge badge--${esc(f.suggestion)}">${esc(f.suggestion)}</span></td>
                <td>${hasSkill ? `<code>generated-skills/${esc(slug)}/SKILL.md</code>` : `<span class="muted">${f.suggestion === "skill" ? "skill not on disk (yet)" : f.suggestion === "alert" ? "generated-alerts/*.json" : "generated-fixes/*.patch"}</span>`}</td>
              </tr>`;
    }).join("")}
        </tbody>
      </table>`;
  }
  renderSkills() {
    const el = this.querySelector('[data-el="skills"]');
    if (!el) return;
    if (this.skillsError) {
      el.innerHTML = `<div class="empty-state">Skills listing unavailable: ${esc(this.skillsError)}</div>`;
      return;
    }
    if (!this.skills.length) {
      el.innerHTML = `<div class="empty-state">generated-skills/ is empty \u2014 no skill-type finding
        has been applied yet.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Skill</th><th>Written</th><th>Size</th><th>Path</th></tr></thead>
        <tbody>
          ${this.skills.map(
      (s) => `<tr>
              <td><b>${esc(s.name)}</b></td>
              <td class="muted" title="${esc(s.mtime)}">${esc(relTime(s.mtime))}</td>
              <td>${s.size} B</td>
              <td class="muted"><code>${esc(s.path)}</code></td>
            </tr>`
    ).join("")}
        </tbody>
      </table>`;
  }
};
function slugOf(f) {
  try {
    const p = JSON.parse(f.payload);
    return p.slug ?? null;
  } catch {
    return null;
  }
}

// src/views/provenance.ts
var ProvenanceView = class extends MyceliumElement {
  lastGoodChain = null;
  currentChain = null;
  currentError = null;
  status = null;
  loading = true;
  cert = null;
  // Session-local audit of explicit verify results -- "when did I last
  // check, and what did it say" without leaving the view.
  verifyHistory = [];
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Provenance Chain</h2>
        <button data-act="verify">Verify now</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="verify"]').addEventListener("click", () => this.refresh());
    this.refresh();
  }
  mount() {
    this.onDisconnect(
      store.subscribe((s) => {
        this.status = s.provenance;
        this.renderBody();
      })
    );
  }
  async refresh() {
    this.loading = true;
    this.renderBody();
    try {
      this.status = await api.provenanceVerify();
      store.setProvenance(this.status);
      this.verifyHistory.unshift({
        ts: (/* @__PURE__ */ new Date()).toISOString(),
        valid: this.status.valid,
        reason: this.status.reason
      });
      this.verifyHistory = this.verifyHistory.slice(0, 10);
    } catch (err) {
      console.warn("mycelium: /api/provenance/verify failed", err);
    }
    try {
      this.cert = await api.certHash();
    } catch {
      this.cert = null;
    }
    try {
      this.currentChain = await api.provenance();
      this.currentError = null;
      this.lastGoodChain = this.currentChain;
    } catch (err) {
      this.currentChain = null;
      this.currentError = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    if (this.status && !this.status.valid) traceViewedTamperedProvenance();
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading && !this.currentChain && !this.lastGoodChain) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    const parts = [];
    if (this.status) {
      const pubkey = this.currentChain?.pubkey ?? this.lastGoodChain?.pubkey ?? "";
      parts.push(`
        <div class="panel">
          <div class="stat-row">
            <div class="stat"><span class="stat__label">Status</span><span class="stat__value">${this.status.valid ? "OK" : "TAMPERED"}</span></div>
            <div class="stat"><span class="stat__label">Anchored</span><span class="stat__value">${this.status.anchored}</span></div>
            <div class="stat"><span class="stat__label">Reason</span><span class="stat__value">${esc(this.status.reason)}</span></div>
            ${pubkey ? `<div class="stat"><span class="stat__label">Pubkey</span><span class="stat__value" style="font-size:0.85em" title="${esc(pubkey)}">${esc(pubkey.slice(0, 16))}\u2026</span></div>` : ""}
            ${this.cert ? `<div class="stat"><span class="stat__label">WT cert (sha-256)</span><span class="stat__value" style="font-size:0.85em" title="expires ${esc(this.cert.expires)}">${esc(this.cert.hash.slice(0, 16))}\u2026</span></div>` : ""}
          </div>
        </div>
      `);
    }
    if (this.verifyHistory.length > 1) {
      parts.push(`
        <details class="finding-card__evidence"><summary>verify history (this session)</summary>
          ${this.verifyHistory.map(
        (h) => `<p class="muted">${esc(relTime(h.ts))} \u2014 ${h.valid ? "OK" : "TAMPERED"} (${esc(h.reason)})</p>`
      ).join("")}
        </details>
      `);
    }
    if (this.currentChain) {
      parts.push(
        `<h3>Chain (${this.currentChain.count} envelopes, pubkey ${esc(this.currentChain.pubkey.slice(0, 16))}\u2026)</h3>`
      );
      parts.push(renderChain(this.currentChain.chain));
    } else if (this.currentError) {
      parts.push(`<div class="empty-state">Current chain unavailable: ${esc(this.currentError)}</div>`);
      if (this.lastGoodChain) {
        parts.push(
          `<h3>Last known-good chain (${this.lastGoodChain.count} envelopes, captured before this divergence)</h3>`
        );
        parts.push(renderChain(this.lastGoodChain.chain));
      }
    }
    body.innerHTML = parts.join("");
  }
};
function renderChain(chain) {
  if (!chain.length) return `<div class="empty-state">Chain is empty.</div>`;
  const rows = [];
  let prevHash = "";
  for (const e of chain) {
    const broken = e.prev_hash !== prevHash;
    rows.push(`
      <div class="chain-link ${broken ? "chain-link--broken" : ""}">${broken ? "\u2715 BROKEN LINK" : "\u2193"}</div>
      <div class="chain-envelope ${broken ? "chain-envelope--broken" : ""}">
        <span>#${e.index}</span>
        <span class="chain-hash" title="${esc(e.trace_id)}">${esc(e.action)} \u2192 ${esc(e.target)} (${esc(e.outcome)})</span>
        <span class="chain-hash" title="prev_hash: ${esc(e.prev_hash)}&#10;hash: ${esc(e.hash)}&#10;sig: ${esc(e.sig)}">${esc(e.hash.slice(0, 12))}\u2026</span>
        <span title="${esc(e.ts)}">${esc(relTime(e.ts))}</span>
      </div>
    `);
    prevHash = e.hash;
  }
  return `<div class="chain-list">${rows.join("")}</div>`;
}

// node_modules/d3-force/src/center.js
function center_default(x3, y3) {
  var nodes, strength = 1;
  if (x3 == null) x3 = 0;
  if (y3 == null) y3 = 0;
  function force() {
    var i, n = nodes.length, node, sx = 0, sy = 0;
    for (i = 0; i < n; ++i) {
      node = nodes[i], sx += node.x, sy += node.y;
    }
    for (sx = (sx / n - x3) * strength, sy = (sy / n - y3) * strength, i = 0; i < n; ++i) {
      node = nodes[i], node.x -= sx, node.y -= sy;
    }
  }
  force.initialize = function(_) {
    nodes = _;
  };
  force.x = function(_) {
    return arguments.length ? (x3 = +_, force) : x3;
  };
  force.y = function(_) {
    return arguments.length ? (y3 = +_, force) : y3;
  };
  force.strength = function(_) {
    return arguments.length ? (strength = +_, force) : strength;
  };
  return force;
}

// node_modules/d3-quadtree/src/add.js
function add_default(d) {
  const x3 = +this._x.call(null, d), y3 = +this._y.call(null, d);
  return add(this.cover(x3, y3), x3, y3, d);
}
function add(tree, x3, y3, d) {
  if (isNaN(x3) || isNaN(y3)) return tree;
  var parent, node = tree._root, leaf = { data: d }, x0 = tree._x0, y0 = tree._y0, x1 = tree._x1, y1 = tree._y1, xm, ym, xp, yp, right, bottom, i, j;
  if (!node) return tree._root = leaf, tree;
  while (node.length) {
    if (right = x3 >= (xm = (x0 + x1) / 2)) x0 = xm;
    else x1 = xm;
    if (bottom = y3 >= (ym = (y0 + y1) / 2)) y0 = ym;
    else y1 = ym;
    if (parent = node, !(node = node[i = bottom << 1 | right])) return parent[i] = leaf, tree;
  }
  xp = +tree._x.call(null, node.data);
  yp = +tree._y.call(null, node.data);
  if (x3 === xp && y3 === yp) return leaf.next = node, parent ? parent[i] = leaf : tree._root = leaf, tree;
  do {
    parent = parent ? parent[i] = new Array(4) : tree._root = new Array(4);
    if (right = x3 >= (xm = (x0 + x1) / 2)) x0 = xm;
    else x1 = xm;
    if (bottom = y3 >= (ym = (y0 + y1) / 2)) y0 = ym;
    else y1 = ym;
  } while ((i = bottom << 1 | right) === (j = (yp >= ym) << 1 | xp >= xm));
  return parent[j] = node, parent[i] = leaf, tree;
}
function addAll(data) {
  var d, i, n = data.length, x3, y3, xz = new Array(n), yz = new Array(n), x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (i = 0; i < n; ++i) {
    if (isNaN(x3 = +this._x.call(null, d = data[i])) || isNaN(y3 = +this._y.call(null, d))) continue;
    xz[i] = x3;
    yz[i] = y3;
    if (x3 < x0) x0 = x3;
    if (x3 > x1) x1 = x3;
    if (y3 < y0) y0 = y3;
    if (y3 > y1) y1 = y3;
  }
  if (x0 > x1 || y0 > y1) return this;
  this.cover(x0, y0).cover(x1, y1);
  for (i = 0; i < n; ++i) {
    add(this, xz[i], yz[i], data[i]);
  }
  return this;
}

// node_modules/d3-quadtree/src/cover.js
function cover_default(x3, y3) {
  if (isNaN(x3 = +x3) || isNaN(y3 = +y3)) return this;
  var x0 = this._x0, y0 = this._y0, x1 = this._x1, y1 = this._y1;
  if (isNaN(x0)) {
    x1 = (x0 = Math.floor(x3)) + 1;
    y1 = (y0 = Math.floor(y3)) + 1;
  } else {
    var z = x1 - x0 || 1, node = this._root, parent, i;
    while (x0 > x3 || x3 >= x1 || y0 > y3 || y3 >= y1) {
      i = (y3 < y0) << 1 | x3 < x0;
      parent = new Array(4), parent[i] = node, node = parent, z *= 2;
      switch (i) {
        case 0:
          x1 = x0 + z, y1 = y0 + z;
          break;
        case 1:
          x0 = x1 - z, y1 = y0 + z;
          break;
        case 2:
          x1 = x0 + z, y0 = y1 - z;
          break;
        case 3:
          x0 = x1 - z, y0 = y1 - z;
          break;
      }
    }
    if (this._root && this._root.length) this._root = node;
  }
  this._x0 = x0;
  this._y0 = y0;
  this._x1 = x1;
  this._y1 = y1;
  return this;
}

// node_modules/d3-quadtree/src/data.js
function data_default() {
  var data = [];
  this.visit(function(node) {
    if (!node.length) do
      data.push(node.data);
    while (node = node.next);
  });
  return data;
}

// node_modules/d3-quadtree/src/extent.js
function extent_default(_) {
  return arguments.length ? this.cover(+_[0][0], +_[0][1]).cover(+_[1][0], +_[1][1]) : isNaN(this._x0) ? void 0 : [[this._x0, this._y0], [this._x1, this._y1]];
}

// node_modules/d3-quadtree/src/quad.js
function quad_default(node, x0, y0, x1, y1) {
  this.node = node;
  this.x0 = x0;
  this.y0 = y0;
  this.x1 = x1;
  this.y1 = y1;
}

// node_modules/d3-quadtree/src/find.js
function find_default(x3, y3, radius) {
  var data, x0 = this._x0, y0 = this._y0, x1, y1, x22, y22, x32 = this._x1, y32 = this._y1, quads = [], node = this._root, q, i;
  if (node) quads.push(new quad_default(node, x0, y0, x32, y32));
  if (radius == null) radius = Infinity;
  else {
    x0 = x3 - radius, y0 = y3 - radius;
    x32 = x3 + radius, y32 = y3 + radius;
    radius *= radius;
  }
  while (q = quads.pop()) {
    if (!(node = q.node) || (x1 = q.x0) > x32 || (y1 = q.y0) > y32 || (x22 = q.x1) < x0 || (y22 = q.y1) < y0) continue;
    if (node.length) {
      var xm = (x1 + x22) / 2, ym = (y1 + y22) / 2;
      quads.push(
        new quad_default(node[3], xm, ym, x22, y22),
        new quad_default(node[2], x1, ym, xm, y22),
        new quad_default(node[1], xm, y1, x22, ym),
        new quad_default(node[0], x1, y1, xm, ym)
      );
      if (i = (y3 >= ym) << 1 | x3 >= xm) {
        q = quads[quads.length - 1];
        quads[quads.length - 1] = quads[quads.length - 1 - i];
        quads[quads.length - 1 - i] = q;
      }
    } else {
      var dx = x3 - +this._x.call(null, node.data), dy = y3 - +this._y.call(null, node.data), d2 = dx * dx + dy * dy;
      if (d2 < radius) {
        var d = Math.sqrt(radius = d2);
        x0 = x3 - d, y0 = y3 - d;
        x32 = x3 + d, y32 = y3 + d;
        data = node.data;
      }
    }
  }
  return data;
}

// node_modules/d3-quadtree/src/remove.js
function remove_default(d) {
  if (isNaN(x3 = +this._x.call(null, d)) || isNaN(y3 = +this._y.call(null, d))) return this;
  var parent, node = this._root, retainer, previous, next, x0 = this._x0, y0 = this._y0, x1 = this._x1, y1 = this._y1, x3, y3, xm, ym, right, bottom, i, j;
  if (!node) return this;
  if (node.length) while (true) {
    if (right = x3 >= (xm = (x0 + x1) / 2)) x0 = xm;
    else x1 = xm;
    if (bottom = y3 >= (ym = (y0 + y1) / 2)) y0 = ym;
    else y1 = ym;
    if (!(parent = node, node = node[i = bottom << 1 | right])) return this;
    if (!node.length) break;
    if (parent[i + 1 & 3] || parent[i + 2 & 3] || parent[i + 3 & 3]) retainer = parent, j = i;
  }
  while (node.data !== d) if (!(previous = node, node = node.next)) return this;
  if (next = node.next) delete node.next;
  if (previous) return next ? previous.next = next : delete previous.next, this;
  if (!parent) return this._root = next, this;
  next ? parent[i] = next : delete parent[i];
  if ((node = parent[0] || parent[1] || parent[2] || parent[3]) && node === (parent[3] || parent[2] || parent[1] || parent[0]) && !node.length) {
    if (retainer) retainer[j] = node;
    else this._root = node;
  }
  return this;
}
function removeAll(data) {
  for (var i = 0, n = data.length; i < n; ++i) this.remove(data[i]);
  return this;
}

// node_modules/d3-quadtree/src/root.js
function root_default() {
  return this._root;
}

// node_modules/d3-quadtree/src/size.js
function size_default() {
  var size = 0;
  this.visit(function(node) {
    if (!node.length) do
      ++size;
    while (node = node.next);
  });
  return size;
}

// node_modules/d3-quadtree/src/visit.js
function visit_default(callback) {
  var quads = [], q, node = this._root, child, x0, y0, x1, y1;
  if (node) quads.push(new quad_default(node, this._x0, this._y0, this._x1, this._y1));
  while (q = quads.pop()) {
    if (!callback(node = q.node, x0 = q.x0, y0 = q.y0, x1 = q.x1, y1 = q.y1) && node.length) {
      var xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
      if (child = node[3]) quads.push(new quad_default(child, xm, ym, x1, y1));
      if (child = node[2]) quads.push(new quad_default(child, x0, ym, xm, y1));
      if (child = node[1]) quads.push(new quad_default(child, xm, y0, x1, ym));
      if (child = node[0]) quads.push(new quad_default(child, x0, y0, xm, ym));
    }
  }
  return this;
}

// node_modules/d3-quadtree/src/visitAfter.js
function visitAfter_default(callback) {
  var quads = [], next = [], q;
  if (this._root) quads.push(new quad_default(this._root, this._x0, this._y0, this._x1, this._y1));
  while (q = quads.pop()) {
    var node = q.node;
    if (node.length) {
      var child, x0 = q.x0, y0 = q.y0, x1 = q.x1, y1 = q.y1, xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
      if (child = node[0]) quads.push(new quad_default(child, x0, y0, xm, ym));
      if (child = node[1]) quads.push(new quad_default(child, xm, y0, x1, ym));
      if (child = node[2]) quads.push(new quad_default(child, x0, ym, xm, y1));
      if (child = node[3]) quads.push(new quad_default(child, xm, ym, x1, y1));
    }
    next.push(q);
  }
  while (q = next.pop()) {
    callback(q.node, q.x0, q.y0, q.x1, q.y1);
  }
  return this;
}

// node_modules/d3-quadtree/src/x.js
function defaultX(d) {
  return d[0];
}
function x_default(_) {
  return arguments.length ? (this._x = _, this) : this._x;
}

// node_modules/d3-quadtree/src/y.js
function defaultY(d) {
  return d[1];
}
function y_default(_) {
  return arguments.length ? (this._y = _, this) : this._y;
}

// node_modules/d3-quadtree/src/quadtree.js
function quadtree(nodes, x3, y3) {
  var tree = new Quadtree(x3 == null ? defaultX : x3, y3 == null ? defaultY : y3, NaN, NaN, NaN, NaN);
  return nodes == null ? tree : tree.addAll(nodes);
}
function Quadtree(x3, y3, x0, y0, x1, y1) {
  this._x = x3;
  this._y = y3;
  this._x0 = x0;
  this._y0 = y0;
  this._x1 = x1;
  this._y1 = y1;
  this._root = void 0;
}
function leaf_copy(leaf) {
  var copy = { data: leaf.data }, next = copy;
  while (leaf = leaf.next) next = next.next = { data: leaf.data };
  return copy;
}
var treeProto = quadtree.prototype = Quadtree.prototype;
treeProto.copy = function() {
  var copy = new Quadtree(this._x, this._y, this._x0, this._y0, this._x1, this._y1), node = this._root, nodes, child;
  if (!node) return copy;
  if (!node.length) return copy._root = leaf_copy(node), copy;
  nodes = [{ source: node, target: copy._root = new Array(4) }];
  while (node = nodes.pop()) {
    for (var i = 0; i < 4; ++i) {
      if (child = node.source[i]) {
        if (child.length) nodes.push({ source: child, target: node.target[i] = new Array(4) });
        else node.target[i] = leaf_copy(child);
      }
    }
  }
  return copy;
};
treeProto.add = add_default;
treeProto.addAll = addAll;
treeProto.cover = cover_default;
treeProto.data = data_default;
treeProto.extent = extent_default;
treeProto.find = find_default;
treeProto.remove = remove_default;
treeProto.removeAll = removeAll;
treeProto.root = root_default;
treeProto.size = size_default;
treeProto.visit = visit_default;
treeProto.visitAfter = visitAfter_default;
treeProto.x = x_default;
treeProto.y = y_default;

// node_modules/d3-force/src/constant.js
function constant_default(x3) {
  return function() {
    return x3;
  };
}

// node_modules/d3-force/src/jiggle.js
function jiggle_default(random) {
  return (random() - 0.5) * 1e-6;
}

// node_modules/d3-force/src/collide.js
function x(d) {
  return d.x + d.vx;
}
function y(d) {
  return d.y + d.vy;
}
function collide_default(radius) {
  var nodes, radii, random, strength = 1, iterations = 1;
  if (typeof radius !== "function") radius = constant_default(radius == null ? 1 : +radius);
  function force() {
    var i, n = nodes.length, tree, node, xi, yi, ri, ri2;
    for (var k = 0; k < iterations; ++k) {
      tree = quadtree(nodes, x, y).visitAfter(prepare);
      for (i = 0; i < n; ++i) {
        node = nodes[i];
        ri = radii[node.index], ri2 = ri * ri;
        xi = node.x + node.vx;
        yi = node.y + node.vy;
        tree.visit(apply);
      }
    }
    function apply(quad, x0, y0, x1, y1) {
      var data = quad.data, rj = quad.r, r = ri + rj;
      if (data) {
        if (data.index > node.index) {
          var x3 = xi - data.x - data.vx, y3 = yi - data.y - data.vy, l = x3 * x3 + y3 * y3;
          if (l < r * r) {
            if (x3 === 0) x3 = jiggle_default(random), l += x3 * x3;
            if (y3 === 0) y3 = jiggle_default(random), l += y3 * y3;
            l = (r - (l = Math.sqrt(l))) / l * strength;
            node.vx += (x3 *= l) * (r = (rj *= rj) / (ri2 + rj));
            node.vy += (y3 *= l) * r;
            data.vx -= x3 * (r = 1 - r);
            data.vy -= y3 * r;
          }
        }
        return;
      }
      return x0 > xi + r || x1 < xi - r || y0 > yi + r || y1 < yi - r;
    }
  }
  function prepare(quad) {
    if (quad.data) return quad.r = radii[quad.data.index];
    for (var i = quad.r = 0; i < 4; ++i) {
      if (quad[i] && quad[i].r > quad.r) {
        quad.r = quad[i].r;
      }
    }
  }
  function initialize() {
    if (!nodes) return;
    var i, n = nodes.length, node;
    radii = new Array(n);
    for (i = 0; i < n; ++i) node = nodes[i], radii[node.index] = +radius(node, i, nodes);
  }
  force.initialize = function(_nodes, _random) {
    nodes = _nodes;
    random = _random;
    initialize();
  };
  force.iterations = function(_) {
    return arguments.length ? (iterations = +_, force) : iterations;
  };
  force.strength = function(_) {
    return arguments.length ? (strength = +_, force) : strength;
  };
  force.radius = function(_) {
    return arguments.length ? (radius = typeof _ === "function" ? _ : constant_default(+_), initialize(), force) : radius;
  };
  return force;
}

// node_modules/d3-force/src/link.js
function index(d) {
  return d.index;
}
function find(nodeById, nodeId) {
  var node = nodeById.get(nodeId);
  if (!node) throw new Error("node not found: " + nodeId);
  return node;
}
function link_default(links) {
  var id = index, strength = defaultStrength, strengths, distance = constant_default(30), distances, nodes, count, bias, random, iterations = 1;
  if (links == null) links = [];
  function defaultStrength(link) {
    return 1 / Math.min(count[link.source.index], count[link.target.index]);
  }
  function force(alpha) {
    for (var k = 0, n = links.length; k < iterations; ++k) {
      for (var i = 0, link, source, target, x3, y3, l, b; i < n; ++i) {
        link = links[i], source = link.source, target = link.target;
        x3 = target.x + target.vx - source.x - source.vx || jiggle_default(random);
        y3 = target.y + target.vy - source.y - source.vy || jiggle_default(random);
        l = Math.sqrt(x3 * x3 + y3 * y3);
        l = (l - distances[i]) / l * alpha * strengths[i];
        x3 *= l, y3 *= l;
        target.vx -= x3 * (b = bias[i]);
        target.vy -= y3 * b;
        source.vx += x3 * (b = 1 - b);
        source.vy += y3 * b;
      }
    }
  }
  function initialize() {
    if (!nodes) return;
    var i, n = nodes.length, m2 = links.length, nodeById = new Map(nodes.map((d, i2) => [id(d, i2, nodes), d])), link;
    for (i = 0, count = new Array(n); i < m2; ++i) {
      link = links[i], link.index = i;
      if (typeof link.source !== "object") link.source = find(nodeById, link.source);
      if (typeof link.target !== "object") link.target = find(nodeById, link.target);
      count[link.source.index] = (count[link.source.index] || 0) + 1;
      count[link.target.index] = (count[link.target.index] || 0) + 1;
    }
    for (i = 0, bias = new Array(m2); i < m2; ++i) {
      link = links[i], bias[i] = count[link.source.index] / (count[link.source.index] + count[link.target.index]);
    }
    strengths = new Array(m2), initializeStrength();
    distances = new Array(m2), initializeDistance();
  }
  function initializeStrength() {
    if (!nodes) return;
    for (var i = 0, n = links.length; i < n; ++i) {
      strengths[i] = +strength(links[i], i, links);
    }
  }
  function initializeDistance() {
    if (!nodes) return;
    for (var i = 0, n = links.length; i < n; ++i) {
      distances[i] = +distance(links[i], i, links);
    }
  }
  force.initialize = function(_nodes, _random) {
    nodes = _nodes;
    random = _random;
    initialize();
  };
  force.links = function(_) {
    return arguments.length ? (links = _, initialize(), force) : links;
  };
  force.id = function(_) {
    return arguments.length ? (id = _, force) : id;
  };
  force.iterations = function(_) {
    return arguments.length ? (iterations = +_, force) : iterations;
  };
  force.strength = function(_) {
    return arguments.length ? (strength = typeof _ === "function" ? _ : constant_default(+_), initializeStrength(), force) : strength;
  };
  force.distance = function(_) {
    return arguments.length ? (distance = typeof _ === "function" ? _ : constant_default(+_), initializeDistance(), force) : distance;
  };
  return force;
}

// node_modules/d3-dispatch/src/dispatch.js
var noop = { value: () => {
} };
function dispatch2() {
  for (var i = 0, n = arguments.length, _ = {}, t; i < n; ++i) {
    if (!(t = arguments[i] + "") || t in _ || /[\s.]/.test(t)) throw new Error("illegal type: " + t);
    _[t] = [];
  }
  return new Dispatch(_);
}
function Dispatch(_) {
  this._ = _;
}
function parseTypenames(typenames, types) {
  return typenames.trim().split(/^|\s+/).map(function(t) {
    var name = "", i = t.indexOf(".");
    if (i >= 0) name = t.slice(i + 1), t = t.slice(0, i);
    if (t && !types.hasOwnProperty(t)) throw new Error("unknown type: " + t);
    return { type: t, name };
  });
}
Dispatch.prototype = dispatch2.prototype = {
  constructor: Dispatch,
  on: function(typename, callback) {
    var _ = this._, T = parseTypenames(typename + "", _), t, i = -1, n = T.length;
    if (arguments.length < 2) {
      while (++i < n) if ((t = (typename = T[i]).type) && (t = get(_[t], typename.name))) return t;
      return;
    }
    if (callback != null && typeof callback !== "function") throw new Error("invalid callback: " + callback);
    while (++i < n) {
      if (t = (typename = T[i]).type) _[t] = set(_[t], typename.name, callback);
      else if (callback == null) for (t in _) _[t] = set(_[t], typename.name, null);
    }
    return this;
  },
  copy: function() {
    var copy = {}, _ = this._;
    for (var t in _) copy[t] = _[t].slice();
    return new Dispatch(copy);
  },
  call: function(type, that) {
    if ((n = arguments.length - 2) > 0) for (var args = new Array(n), i = 0, n, t; i < n; ++i) args[i] = arguments[i + 2];
    if (!this._.hasOwnProperty(type)) throw new Error("unknown type: " + type);
    for (t = this._[type], i = 0, n = t.length; i < n; ++i) t[i].value.apply(that, args);
  },
  apply: function(type, that, args) {
    if (!this._.hasOwnProperty(type)) throw new Error("unknown type: " + type);
    for (var t = this._[type], i = 0, n = t.length; i < n; ++i) t[i].value.apply(that, args);
  }
};
function get(type, name) {
  for (var i = 0, n = type.length, c2; i < n; ++i) {
    if ((c2 = type[i]).name === name) {
      return c2.value;
    }
  }
}
function set(type, name, callback) {
  for (var i = 0, n = type.length; i < n; ++i) {
    if (type[i].name === name) {
      type[i] = noop, type = type.slice(0, i).concat(type.slice(i + 1));
      break;
    }
  }
  if (callback != null) type.push({ name, value: callback });
  return type;
}
var dispatch_default = dispatch2;

// node_modules/d3-timer/src/timer.js
var frame = 0;
var timeout = 0;
var interval = 0;
var pokeDelay = 1e3;
var taskHead;
var taskTail;
var clockLast = 0;
var clockNow = 0;
var clockSkew = 0;
var clock = typeof performance === "object" && performance.now ? performance : Date;
var setFrame = typeof window === "object" && window.requestAnimationFrame ? window.requestAnimationFrame.bind(window) : function(f) {
  setTimeout(f, 17);
};
function now() {
  return clockNow || (setFrame(clearNow), clockNow = clock.now() + clockSkew);
}
function clearNow() {
  clockNow = 0;
}
function Timer() {
  this._call = this._time = this._next = null;
}
Timer.prototype = timer.prototype = {
  constructor: Timer,
  restart: function(callback, delay, time) {
    if (typeof callback !== "function") throw new TypeError("callback is not a function");
    time = (time == null ? now() : +time) + (delay == null ? 0 : +delay);
    if (!this._next && taskTail !== this) {
      if (taskTail) taskTail._next = this;
      else taskHead = this;
      taskTail = this;
    }
    this._call = callback;
    this._time = time;
    sleep();
  },
  stop: function() {
    if (this._call) {
      this._call = null;
      this._time = Infinity;
      sleep();
    }
  }
};
function timer(callback, delay, time) {
  var t = new Timer();
  t.restart(callback, delay, time);
  return t;
}
function timerFlush() {
  now();
  ++frame;
  var t = taskHead, e;
  while (t) {
    if ((e = clockNow - t._time) >= 0) t._call.call(void 0, e);
    t = t._next;
  }
  --frame;
}
function wake() {
  clockNow = (clockLast = clock.now()) + clockSkew;
  frame = timeout = 0;
  try {
    timerFlush();
  } finally {
    frame = 0;
    nap();
    clockNow = 0;
  }
}
function poke() {
  var now2 = clock.now(), delay = now2 - clockLast;
  if (delay > pokeDelay) clockSkew -= delay, clockLast = now2;
}
function nap() {
  var t0, t1 = taskHead, t2, time = Infinity;
  while (t1) {
    if (t1._call) {
      if (time > t1._time) time = t1._time;
      t0 = t1, t1 = t1._next;
    } else {
      t2 = t1._next, t1._next = null;
      t1 = t0 ? t0._next = t2 : taskHead = t2;
    }
  }
  taskTail = t0;
  sleep(time);
}
function sleep(time) {
  if (frame) return;
  if (timeout) timeout = clearTimeout(timeout);
  var delay = time - clockNow;
  if (delay > 24) {
    if (time < Infinity) timeout = setTimeout(wake, time - clock.now() - clockSkew);
    if (interval) interval = clearInterval(interval);
  } else {
    if (!interval) clockLast = clock.now(), interval = setInterval(poke, pokeDelay);
    frame = 1, setFrame(wake);
  }
}

// node_modules/d3-force/src/lcg.js
var a = 1664525;
var c = 1013904223;
var m = 4294967296;
function lcg_default() {
  let s = 1;
  return () => (s = (a * s + c) % m) / m;
}

// node_modules/d3-force/src/simulation.js
function x2(d) {
  return d.x;
}
function y2(d) {
  return d.y;
}
var initialRadius = 10;
var initialAngle = Math.PI * (3 - Math.sqrt(5));
function simulation_default(nodes) {
  var simulation, alpha = 1, alphaMin = 1e-3, alphaDecay = 1 - Math.pow(alphaMin, 1 / 300), alphaTarget = 0, velocityDecay = 0.6, forces = /* @__PURE__ */ new Map(), stepper = timer(step), event = dispatch_default("tick", "end"), random = lcg_default();
  if (nodes == null) nodes = [];
  function step() {
    tick();
    event.call("tick", simulation);
    if (alpha < alphaMin) {
      stepper.stop();
      event.call("end", simulation);
    }
  }
  function tick(iterations) {
    var i, n = nodes.length, node;
    if (iterations === void 0) iterations = 1;
    for (var k = 0; k < iterations; ++k) {
      alpha += (alphaTarget - alpha) * alphaDecay;
      forces.forEach(function(force) {
        force(alpha);
      });
      for (i = 0; i < n; ++i) {
        node = nodes[i];
        if (node.fx == null) node.x += node.vx *= velocityDecay;
        else node.x = node.fx, node.vx = 0;
        if (node.fy == null) node.y += node.vy *= velocityDecay;
        else node.y = node.fy, node.vy = 0;
      }
    }
    return simulation;
  }
  function initializeNodes() {
    for (var i = 0, n = nodes.length, node; i < n; ++i) {
      node = nodes[i], node.index = i;
      if (node.fx != null) node.x = node.fx;
      if (node.fy != null) node.y = node.fy;
      if (isNaN(node.x) || isNaN(node.y)) {
        var radius = initialRadius * Math.sqrt(0.5 + i), angle = i * initialAngle;
        node.x = radius * Math.cos(angle);
        node.y = radius * Math.sin(angle);
      }
      if (isNaN(node.vx) || isNaN(node.vy)) {
        node.vx = node.vy = 0;
      }
    }
  }
  function initializeForce(force) {
    if (force.initialize) force.initialize(nodes, random);
    return force;
  }
  initializeNodes();
  return simulation = {
    tick,
    restart: function() {
      return stepper.restart(step), simulation;
    },
    stop: function() {
      return stepper.stop(), simulation;
    },
    nodes: function(_) {
      return arguments.length ? (nodes = _, initializeNodes(), forces.forEach(initializeForce), simulation) : nodes;
    },
    alpha: function(_) {
      return arguments.length ? (alpha = +_, simulation) : alpha;
    },
    alphaMin: function(_) {
      return arguments.length ? (alphaMin = +_, simulation) : alphaMin;
    },
    alphaDecay: function(_) {
      return arguments.length ? (alphaDecay = +_, simulation) : +alphaDecay;
    },
    alphaTarget: function(_) {
      return arguments.length ? (alphaTarget = +_, simulation) : alphaTarget;
    },
    velocityDecay: function(_) {
      return arguments.length ? (velocityDecay = 1 - _, simulation) : 1 - velocityDecay;
    },
    randomSource: function(_) {
      return arguments.length ? (random = _, forces.forEach(initializeForce), simulation) : random;
    },
    force: function(name, _) {
      return arguments.length > 1 ? (_ == null ? forces.delete(name) : forces.set(name, initializeForce(_)), simulation) : forces.get(name);
    },
    find: function(x3, y3, radius) {
      var i = 0, n = nodes.length, dx, dy, d2, node, closest;
      if (radius == null) radius = Infinity;
      else radius *= radius;
      for (i = 0; i < n; ++i) {
        node = nodes[i];
        dx = x3 - node.x;
        dy = y3 - node.y;
        d2 = dx * dx + dy * dy;
        if (d2 < radius) closest = node, radius = d2;
      }
      return closest;
    },
    on: function(name, _) {
      return arguments.length > 1 ? (event.on(name, _), simulation) : event.on(name);
    }
  };
}

// node_modules/d3-force/src/manyBody.js
function manyBody_default() {
  var nodes, node, random, alpha, strength = constant_default(-30), strengths, distanceMin2 = 1, distanceMax2 = Infinity, theta2 = 0.81;
  function force(_) {
    var i, n = nodes.length, tree = quadtree(nodes, x2, y2).visitAfter(accumulate);
    for (alpha = _, i = 0; i < n; ++i) node = nodes[i], tree.visit(apply);
  }
  function initialize() {
    if (!nodes) return;
    var i, n = nodes.length, node2;
    strengths = new Array(n);
    for (i = 0; i < n; ++i) node2 = nodes[i], strengths[node2.index] = +strength(node2, i, nodes);
  }
  function accumulate(quad) {
    var strength2 = 0, q, c2, weight = 0, x3, y3, i;
    if (quad.length) {
      for (x3 = y3 = i = 0; i < 4; ++i) {
        if ((q = quad[i]) && (c2 = Math.abs(q.value))) {
          strength2 += q.value, weight += c2, x3 += c2 * q.x, y3 += c2 * q.y;
        }
      }
      quad.x = x3 / weight;
      quad.y = y3 / weight;
    } else {
      q = quad;
      q.x = q.data.x;
      q.y = q.data.y;
      do
        strength2 += strengths[q.data.index];
      while (q = q.next);
    }
    quad.value = strength2;
  }
  function apply(quad, x1, _, x22) {
    if (!quad.value) return true;
    var x3 = quad.x - node.x, y3 = quad.y - node.y, w = x22 - x1, l = x3 * x3 + y3 * y3;
    if (w * w / theta2 < l) {
      if (l < distanceMax2) {
        if (x3 === 0) x3 = jiggle_default(random), l += x3 * x3;
        if (y3 === 0) y3 = jiggle_default(random), l += y3 * y3;
        if (l < distanceMin2) l = Math.sqrt(distanceMin2 * l);
        node.vx += x3 * quad.value * alpha / l;
        node.vy += y3 * quad.value * alpha / l;
      }
      return true;
    } else if (quad.length || l >= distanceMax2) return;
    if (quad.data !== node || quad.next) {
      if (x3 === 0) x3 = jiggle_default(random), l += x3 * x3;
      if (y3 === 0) y3 = jiggle_default(random), l += y3 * y3;
      if (l < distanceMin2) l = Math.sqrt(distanceMin2 * l);
    }
    do
      if (quad.data !== node) {
        w = strengths[quad.data.index] * alpha / l;
        node.vx += x3 * w;
        node.vy += y3 * w;
      }
    while (quad = quad.next);
  }
  force.initialize = function(_nodes, _random) {
    nodes = _nodes;
    random = _random;
    initialize();
  };
  force.strength = function(_) {
    return arguments.length ? (strength = typeof _ === "function" ? _ : constant_default(+_), initialize(), force) : strength;
  };
  force.distanceMin = function(_) {
    return arguments.length ? (distanceMin2 = _ * _, force) : Math.sqrt(distanceMin2);
  };
  force.distanceMax = function(_) {
    return arguments.length ? (distanceMax2 = _ * _, force) : Math.sqrt(distanceMax2);
  };
  force.theta = function(_) {
    return arguments.length ? (theta2 = _ * _, force) : Math.sqrt(theta2);
  };
  return force;
}

// src/ar/xr-detect.ts
async function arSupported() {
  if (typeof navigator === "undefined" || !navigator.xr) return false;
  try {
    return await navigator.xr.isSessionSupported("immersive-ar");
  } catch {
    return false;
  }
}

// src/views/wallets.ts
var WalletsView = class extends MyceliumElement {
  sim = null;
  // null = not checked yet -- the button only ever appears once this
  // resolves true, never speculatively (arSupported() is async, so it
  // can't be part of the synchronous initial template the way the
  // WebGPU/WebTransport feature-detects elsewhere in this app are).
  arAvailable = null;
  lastCorrelations = [];
  render() {
    this.innerHTML = `
      <div class="view-header"><h2>Wallet / Money-Flow</h2></div>
      <div data-el="body"></div>
    `;
    this.renderBody();
  }
  mount() {
    this.onDisconnect(store.subscribe(() => this.renderBody()));
    this.onDisconnect(() => this.sim?.stop());
    arSupported().then((ok) => {
      this.arAvailable = ok;
      if (this.isConnected) this.renderBody();
    });
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    const { findingsById } = store.get();
    const all = Array.from(findingsById.values());
    const activity = all.filter((f) => f.miner === "wallet_activity").sort((a2, b) => a2.created_ts < b.created_ts ? 1 : -1)[0];
    const correlations = all.filter((f) => f.miner === "wallet_correlation");
    const anomalies = all.filter((f) => f.miner === "wallet_anomaly");
    if (!activity && !correlations.length && !anomalies.length) {
      body.innerHTML = `<div class="empty-state">No wallet findings yet -- run the wallet_activity /
        wallet_correlation / wallet_anomaly miners over trace data with wallet_buy actions.</div>`;
      return;
    }
    body.innerHTML = `
      ${activity ? renderActivity(activity) : ""}
      ${correlations.length ? `
        <div class="view-header">
          <h3>Wallet Clusters (${correlations.length})</h3>
          ${this.arAvailable ? `<button class="secondary" data-act="enter-ar">Enter AR</button>` : ""}
        </div>
        <svg data-el="graph" width="100%" height="360" viewBox="0 0 800 360"
             style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;"></svg>
        ${renderCorrelationTable(correlations)}
      ` : ""}
      ${anomalies.length ? `<h3>Wallet Anomalies (${anomalies.length})</h3>${renderAnomalies(anomalies)}` : ""}
    `;
    this.wireShareButtons(body);
    if (correlations.length) {
      this.lastCorrelations = correlations;
      this.renderGraph(correlations);
      this.wireAR(body);
    }
  }
  wireAR(root) {
    const btn = root.querySelector('[data-act="enter-ar"]');
    btn?.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Starting AR\u2026";
      try {
        const { enterWalletAR } = await import("./wallet-ar-3UXTXVVZ.js");
        const { nodes, links } = this.buildARGraph(this.lastCorrelations);
        await enterWalletAR(nodes, links, () => {
          btn.disabled = false;
          btn.textContent = "Enter AR";
        });
      } catch (err) {
        console.warn("mycelium: entering AR failed", err);
        btn.disabled = false;
        btn.textContent = "Enter AR";
      }
    });
  }
  /** Reuses the same 2D force-layout positions the SVG graph already
   * computed (renderGraph populates GraphNode.x/y via d3-force) so the AR
   * cluster's layout matches what's on screen, rather than re-running the
   * simulation a second time for a second renderer. */
  buildARGraph(correlations) {
    const nodes = [];
    const links = [];
    const seen = /* @__PURE__ */ new Set();
    for (const f of correlations) {
      const p = safeParse(f.payload);
      if (!p) continue;
      links.push({ sourceId: p.wallet_a, targetId: p.wallet_b, weight: p.shared.length });
      for (const id of [p.wallet_a, p.wallet_b]) {
        if (seen.has(id)) continue;
        seen.add(id);
        nodes.push({ id, x: 400, y: 180 });
      }
    }
    return { nodes: this.withSimPositions(nodes), links };
  }
  withSimPositions(nodes) {
    const simNodes = this.sim?.nodes() ?? [];
    const byId = new Map(simNodes.map((n) => [n.id, n]));
    return nodes.map((n) => {
      const simNode = byId.get(n.id);
      return { id: n.id, x: simNode?.x ?? n.x, y: simNode?.y ?? n.y };
    });
  }
  wireShareButtons(root) {
    root.querySelectorAll("[data-share]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const text = btn.dataset.share;
        try {
          if (navigator.share) {
            await navigator.share({ title: "Mycelium wallet finding", text });
          } else {
            await navigator.clipboard.writeText(text);
            const orig = btn.textContent;
            btn.textContent = "Copied";
            setTimeout(() => {
              btn.textContent = orig;
            }, 1500);
          }
        } catch {
        }
      });
    });
  }
  renderGraph(correlations) {
    const svg = this.querySelector('[data-el="graph"]');
    if (!svg) return;
    this.sim?.stop();
    const nodeIds = /* @__PURE__ */ new Set();
    const links = [];
    for (const f of correlations) {
      const p = safeParse(f.payload);
      if (!p) continue;
      nodeIds.add(p.wallet_a);
      nodeIds.add(p.wallet_b);
      links.push({ source: p.wallet_a, target: p.wallet_b, shared: p.shared.length });
    }
    const nodes = Array.from(nodeIds).map((id) => ({ id }));
    const W = 800;
    const H = 360;
    svg.innerHTML = `<g data-el="links"></g><g data-el="nodes"></g>`;
    const linkG = svg.querySelector('[data-el="links"]');
    const nodeG = svg.querySelector('[data-el="nodes"]');
    const svgNS = "http://www.w3.org/2000/svg";
    const linkEls = links.map((l) => {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("stroke", "var(--border)");
      line.setAttribute("stroke-width", String(Math.min(6, 1 + l.shared)));
      linkG.appendChild(line);
      return line;
    });
    const nodeEls = nodes.map((n) => {
      const g = document.createElementNS(svgNS, "g");
      const circle = document.createElementNS(svgNS, "circle");
      circle.setAttribute("r", "10");
      circle.setAttribute("fill", "var(--accent)");
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("font-size", "9");
      label.setAttribute("fill", "var(--fg-dim)");
      label.setAttribute("dy", "-14");
      label.setAttribute("text-anchor", "middle");
      label.textContent = `${n.id.slice(0, 6)}\u2026`;
      g.append(circle, label);
      nodeG.appendChild(g);
      return g;
    });
    this.sim = simulation_default(nodes).force(
      "link",
      link_default(links).id((d) => d.id).distance(80)
    ).force("charge", manyBody_default().strength(-120)).force("center", center_default(W / 2, H / 2)).force("collide", collide_default(20)).on("tick", () => {
      links.forEach((l, i) => {
        const s = l.source;
        const t = l.target;
        const el = linkEls[i];
        el.setAttribute("x1", String(s.x ?? 0));
        el.setAttribute("y1", String(s.y ?? 0));
        el.setAttribute("x2", String(t.x ?? 0));
        el.setAttribute("y2", String(t.y ?? 0));
      });
      nodes.forEach((n, i) => {
        nodeEls[i].setAttribute("transform", `translate(${n.x ?? 0},${n.y ?? 0})`);
      });
    });
  }
};
function safeParse(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}
function renderActivity(f) {
  const p = safeParse(f.payload);
  if (!p) return "";
  return `
    <h3>Money Flow</h3>
    <p class="finding-card__title">${esc(f.title)}</p>
    <table class="data-table">
      <thead><tr><th>Token</th><th>Wallets</th><th>Buys</th><th>Volume USD</th><th>Smart</th></tr></thead>
      <tbody>
        ${p.tokens.map(
    (t) => `
          <tr>
            <td>${esc(t.symbol)}</td>
            <td>${t.distinct_wallets}</td>
            <td>${t.buys}</td>
            <td>$${t.volume_usd.toLocaleString()}</td>
            <td>${t.smart_wallets}</td>
          </tr>
        `
  ).join("")}
      </tbody>
    </table>
    <table class="data-table">
      <thead><tr><th>Wallet</th><th>Tokens</th><th>Buys</th><th>Volume USD</th><th>Tags</th></tr></thead>
      <tbody>
        ${p.wallets.map(
    (w) => `
          <tr>
            <td title="${esc(w.wallet)}">${esc(w.wallet.slice(0, 10))}\u2026</td>
            <td>${w.distinct_tokens}</td>
            <td>${w.buys}</td>
            <td>$${w.volume_usd.toLocaleString()}</td>
            <td>${w.tags.map((t) => `<span class="badge badge--skill">${esc(t)}</span>`).join(" ")}</td>
          </tr>
        `
  ).join("")}
      </tbody>
    </table>
  `;
}
function renderCorrelationTable(correlations) {
  return `
    <table class="data-table">
      <thead><tr><th>Wallet A</th><th>Wallet B</th><th>Shared tokens</th><th></th></tr></thead>
      <tbody>
        ${correlations.map((f) => {
    const p = safeParse(f.payload);
    if (!p) return "";
    const shareText = `Wallet cluster: ${p.wallet_a} + ${p.wallet_b} co-bought ${p.shared.join(", ")}`;
    return `
            <tr>
              <td title="${esc(p.wallet_a)}">${esc(p.wallet_a.slice(0, 10))}\u2026</td>
              <td title="${esc(p.wallet_b)}">${esc(p.wallet_b.slice(0, 10))}\u2026</td>
              <td>${p.shared.length}</td>
              <td><button class="secondary" data-share="${esc(shareText)}">Share</button></td>
            </tr>
          `;
  }).join("")}
      </tbody>
    </table>
  `;
}
function renderAnomalies(anomalies) {
  return anomalies.map((f) => {
    const raw = safeParse(f.payload);
    const isBurst = !!raw && "tokens" in raw && "window_s" in raw;
    const shareText = `${f.title} -- ${f.evidence}`;
    return `
      <div class="finding-card finding-card--${esc(f.state)}">
        <div class="finding-card__head">
          <span class="badge badge--alert">${isBurst ? "burst" : "everything-buyer"}</span>
          <span class="finding-card__conf">${Math.round(f.confidence * 100)}%</span>
        </div>
        <div class="finding-card__title">${esc(f.title)}</div>
        <p>${esc(f.evidence)}</p>
        <div class="finding-card__actions">
          <button class="secondary" data-share="${esc(shareText)}">Share</button>
        </div>
      </div>
    `;
  }).join("");
}

// src/views/miners.ts
var MinersView = class extends MyceliumElement {
  miners = [];
  loading = true;
  error = null;
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Miner Registry</h2>
        <div class="view-filters">
          <button data-act="mine">Force mine cycle</button>
          <button data-act="mine-wasm" class="secondary">Force WASM mine</button>
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="mine"]').addEventListener("click", () => this.forceMine("cycle"));
    this.querySelector('[data-act="mine-wasm"]').addEventListener("click", () => this.forceMine("wasm"));
    this.load();
  }
  mount() {
    const t = setInterval(() => this.load(), 3e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async load() {
    this.loading = this.miners.length === 0;
    if (this.loading) this.renderBody();
    try {
      const res = await api.miners();
      this.miners = res.miners;
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  async forceMine(kind) {
    const actAttr = kind === "wasm" ? "mine-wasm" : "mine";
    const label = kind === "wasm" ? "Force WASM mine" : "Force mine cycle";
    const btn = this.querySelector(`[data-act="${actAttr}"]`);
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Mining\u2026";
    }
    try {
      if (kind === "wasm") await api.mineWasm();
      else await api.mine();
      traceForcedMine(kind, true);
      await this.load();
    } catch (err) {
      traceForcedMine(kind, false);
      console.warn(`mycelium: force mine (${kind}) failed`, err);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = label;
      }
    }
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading && !this.miners.length) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Failed to load miners: ${esc(this.error)}</div>`;
      return;
    }
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Miner</th><th>What it detects</th><th>Findings</th><th>Open / Applied / Dismissed</th><th>Avg conf</th><th>Last finding</th></tr></thead>
        <tbody>
          ${this.miners.map((m2) => {
      const s = m2.by_state ?? {};
      return `
            <tr>
              <td><b>${esc(m2.miner)}</b></td>
              <td class="muted">${esc(MINER_DESCRIPTIONS[m2.miner] ?? "")}</td>
              <td>${m2.findings}</td>
              <td><span class="badge badge--alert">${s.open ?? 0}</span> <span class="badge badge--skill">${s.applied ?? 0}</span> <span class="muted">${s.dismissed ?? 0}</span></td>
              <td>${m2.avg_confidence != null ? Math.round(m2.avg_confidence * 100) + "%" : "\u2014"}</td>
              <td>${m2.last_finding_ts ? esc(relTime(m2.last_finding_ts)) : "never"}</td>
            </tr>
          `;
    }).join("")}
        </tbody>
      </table>
    `;
  }
};
var MINER_DESCRIPTIONS = {
  recurring_workflow: "tool sequences repeated across sessions \u2192 compound-skill candidates",
  anomaly: "failure bursts on a single action vs. its baseline rate",
  cross_agent: "the same failure hitting multiple distinct agents \u2192 shared root cause",
  opportunity: "high-frequency call pairs where one compound tool would save calls",
  wallet_activity: "money-flow digest: what everyone is buying, who the movers are",
  wallet_correlation: "wallets co-buying \u22652 of the same tokens \u2192 co-movement clusters",
  wallet_anomaly: "burst buyers (\u22653 tokens <10min) and everything-buyers (\u22654 distinct)"
};

// src/views/alerts.ts
var AlertsView = class extends MyceliumElement {
  alerts = [];
  tripped = 0;
  loading = true;
  error = "";
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Alerts</h2>
        <span class="sub">generated watchdog configs, evaluated live</span>
        <button class="secondary" data-act="refresh">Re-evaluate</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="refresh"]').addEventListener("click", () => this.fetch());
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 3e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    try {
      const res = await api.alerts();
      this.alerts = res.alerts ?? [];
      this.tripped = res.tripped ?? 0;
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Evaluating alert configs\u2026</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Alert evaluation failed: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.alerts.length) {
      body.innerHTML = `<div class="empty-state">No alert configs yet \u2014 applying an alert-type
        finding writes one to generated-alerts/, and it shows up here armed.</div>`;
      return;
    }
    body.innerHTML = `
      <p>${this.tripped ? `<span class="badge badge--config_fix">${this.tripped} TRIPPED</span>` : `<span class="badge badge--skill">all quiet</span>`}</p>
      <table class="data-table">
        <thead><tr><th>Alert</th><th>Watched action</th><th>Failures</th><th>Rate</th><th>State</th><th>Status</th></tr></thead>
        <tbody>
          ${this.alerts.map(
      (a2) => `<tr>
              <td><code>${esc(a2.alert)}</code></td>
              <td>${esc(a2.action || "\u2014")}</td>
              <td>${a2.failures}/${a2.total}</td>
              <td>${(a2.rate * 100).toFixed(0)}%</td>
              <td class="muted">${esc(a2.state ?? "\u2014")}</td>
              <td>${a2.tripped ? `<span class="badge badge--config_fix">TRIPPED</span>` : `<span class="muted">quiet</span>`}</td>
            </tr>`
    ).join("")}
        </tbody>
      </table>`;
  }
};

// src/views/agents.ts
var STALE_MS = 10 * 60 * 1e3;
var DEAD_MS = 60 * 60 * 1e3;
var AgentsView = class extends MyceliumElement {
  agents = [];
  loading = true;
  error = "";
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Agents</h2>
        <span class="sub">who writes to the substrate</span>
        <div class="view-filters">
          <button class="secondary" data-act="export-csv">\u2913 CSV</button>
          <button class="secondary" data-act="export-json">\u2913 JSON</button>
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="export-csv"]').addEventListener(
      "click",
      () => downloadCSV(this.agents, "agents")
    );
    this.querySelector('[data-act="export-json"]').addEventListener(
      "click",
      () => downloadJSON(this.agents, "agents")
    );
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 3e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    try {
      const res = await api.agents();
      this.agents = res.agents;
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Failed to load agents: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.agents.length) {
      body.innerHTML = `<div class="empty-state">Nothing has written to the substrate yet.</div>`;
      return;
    }
    const now2 = Date.now();
    body.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Agent</th><th>Health</th><th>Traces</th><th>Last active</th><th>Error rate</th><th>Kinds</th></tr></thead>
        <tbody>
          ${this.agents.map((a2) => {
      const age = now2 - Date.parse(a2.last_seen);
      const health = age < STALE_MS ? `<span class="badge badge--skill">active</span>` : age < DEAD_MS ? `<span class="badge badge--alert">stale</span>` : `<span class="badge badge--config_fix">dead</span>`;
      const kinds = Object.entries(a2.kinds).sort((x3, y3) => y3[1] - x3[1]).map(([k, n]) => `<span class="vote">${esc(k)}:${n}</span>`).join(" ");
      return `<tr class="row-clickable" data-agent="${esc(a2.name)}" title="open in Trace Explorer">
                <td><b>${esc(a2.name)}</b></td>
                <td>${health}</td>
                <td>${a2.trace_count}</td>
                <td class="muted" title="${esc(a2.last_seen)}">${esc(relTime(a2.last_seen))}</td>
                <td class="${a2.error_rate > 0.3 ? "sell" : "muted"}">${(a2.error_rate * 100).toFixed(0)}%</td>
                <td>${kinds}</td>
              </tr>`;
    }).join("")}
        </tbody>
      </table>`;
    body.querySelectorAll("tr.row-clickable").forEach(
      (tr) => tr.addEventListener("click", () => {
        location.hash = `#/traces?agent=${encodeURIComponent(tr.dataset.agent)}`;
      })
    );
  }
};

// src/views/stats.ts
var KIND_COLORS = {
  tool_call: "#7ad",
  decision: "#7c5",
  observation: "#a8d",
  error: "#f55",
  memory_write: "#fa3",
  workflow_start: "#5aa",
  workflow_end: "#588"
};
var STATE_COLORS = {
  open: "#fa3",
  applied: "#7c5",
  dismissed: "#888"
};
var StatsView = class extends MyceliumElement {
  range = "24h";
  buckets = [];
  error = "";
  loading = true;
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Stats</h2>
        <div class="view-filters">
          ${["24h", "7d", "30d"].map(
      (r) => `<button class="tab-btn ${this.range === r ? "active" : ""}" data-range="${r}">${r}</button>`
    ).join("")}
        </div>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelectorAll("[data-range]").forEach(
      (btn) => btn.addEventListener("click", () => {
        this.range = btn.dataset.range;
        this.render();
        this.fetch();
      })
    );
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 3e4);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    this.loading = this.buckets.length === 0;
    try {
      const res = await api.statsTimeseries(this.range);
      this.buckets = res.buckets;
      this.error = "";
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    this.renderBody();
  }
  renderBody() {
    const body = this.querySelector('[data-el="body"]');
    if (!body) return;
    if (this.loading) {
      body.innerHTML = `<div class="empty-state">Loading\u2026</div>`;
      return;
    }
    if (this.error) {
      body.innerHTML = `<div class="empty-state">Stats unavailable: ${esc(this.error)}</div>`;
      return;
    }
    if (!this.buckets.length) {
      body.innerHTML = `<div class="empty-state">No activity in this window \u2014 quiet substrate.</div>`;
      return;
    }
    const kinds = Array.from(new Set(this.buckets.flatMap((b) => Object.keys(b.traces_by_kind))));
    const states = Array.from(new Set(this.buckets.flatMap((b) => Object.keys(b.findings_by_state))));
    body.innerHTML = `
      <div class="chart-panel">
        <h3>Traces per ${this.range === "24h" ? "hour" : "day"}, stacked by kind</h3>
        <div class="chart-legend">${kinds.map((k) => `<span><span class="swatch" style="background:${KIND_COLORS[k] ?? "#666"}"></span>${esc(k)}</span>`).join("")}</div>
        <canvas data-el="traces-chart"></canvas>
      </div>
      <div class="chart-panel">
        <h3>Findings by state + mine runs</h3>
        <div class="chart-legend">${states.map((s) => `<span><span class="swatch" style="background:${STATE_COLORS[s] ?? "#666"}"></span>${esc(s)}</span>`).join("")}<span><span class="swatch" style="background:#ddd"></span>mine runs (line)</span></div>
        <canvas data-el="findings-chart"></canvas>
      </div>`;
    this.drawTraces();
    this.drawFindings();
  }
  setupCanvas(sel) {
    const canvas = this.querySelector(sel);
    if (!canvas) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 180;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    const ctx2 = canvas.getContext("2d");
    if (!ctx2) return null;
    ctx2.scale(dpr, dpr);
    ctx2.clearRect(0, 0, w, h);
    return ctx2;
  }
  drawTraces() {
    const ctx2 = this.setupCanvas('[data-el="traces-chart"]');
    if (!ctx2) return;
    const w = ctx2.canvas.clientWidth || 800;
    const h = 180;
    const pad = 4;
    const max = Math.max(...this.buckets.map((b) => b.traces_total), 1);
    const bw = Math.max(2, (w - pad * 2) / this.buckets.length - 2);
    this.buckets.forEach((b, i) => {
      const x3 = pad + i * ((w - pad * 2) / this.buckets.length);
      let y3 = h - pad;
      for (const [kind, n] of Object.entries(b.traces_by_kind)) {
        const bh = (h - pad * 2) * n / max;
        ctx2.fillStyle = KIND_COLORS[kind] ?? "#666";
        ctx2.fillRect(x3, y3 - bh, bw, bh);
        y3 -= bh;
      }
    });
  }
  drawFindings() {
    const ctx2 = this.setupCanvas('[data-el="findings-chart"]');
    if (!ctx2) return;
    const w = ctx2.canvas.clientWidth || 800;
    const h = 180;
    const pad = 4;
    const maxF = Math.max(...this.buckets.map((b) => Object.values(b.findings_by_state).reduce((a2, n) => a2 + n, 0)), 1);
    const maxM = Math.max(...this.buckets.map((b) => b.mine_runs), 1);
    const bw = Math.max(2, (w - pad * 2) / this.buckets.length - 2);
    this.buckets.forEach((b, i) => {
      const x3 = pad + i * ((w - pad * 2) / this.buckets.length);
      let y3 = h - pad;
      for (const [state, n] of Object.entries(b.findings_by_state)) {
        const bh = (h - pad * 2) * n / maxF;
        ctx2.fillStyle = STATE_COLORS[state] ?? "#666";
        ctx2.fillRect(x3, y3 - bh, bw, bh);
        y3 -= bh;
      }
    });
    ctx2.strokeStyle = "#ddd";
    ctx2.lineWidth = 1.5;
    ctx2.beginPath();
    this.buckets.forEach((b, i) => {
      const x3 = pad + (i + 0.5) * ((w - pad * 2) / this.buckets.length);
      const y3 = h - pad - (h - pad * 2) * b.mine_runs / maxM;
      if (i === 0) ctx2.moveTo(x3, y3);
      else ctx2.lineTo(x3, y3);
    });
    ctx2.stroke();
  }
};

// src/views/system.ts
var STACK = [
  ["Gateway (Go)", "REST :8811 + SSE /api/stream + WebTransport :8812 (rotating cert), Ed25519 provenance chain over every trace"],
  ["Auth", "optional WebAuthn device pairing (MYCELIUM_GATEWAY_AUTH=1) \u2014 status below shows the live mode"],
  ["Storage", "SQLite substrate (swappable to Postgres via MYCELIUM_BACKEND=postgres on the Python side)"],
  ["Miners (Python)", "7 registered pattern miners, subprocess-sandboxed (RLIMIT_DATA) + a Wasm sandbox (wazero) via POST /api/mine/wasm"],
  ["A2A", "findings publish to the Vantage feed (a2a.py); anchor checkpoints push to Gitea (publish.py)"],
  ["Council tunnel", "/api/council/* + /api/picks proxy to the VPS Vantage API \u2014 the agent key never reaches the browser"],
  ["On-device", "WebNN anomaly scoring in this browser (#/ondevice + /web/webnn_miner.html)"]
];
var SystemView = class extends MyceliumElement {
  status = null;
  cert = null;
  logs = [];
  render() {
    this.innerHTML = `
      <div class="view-header"><h2>System</h2><span class="sub">what's running where</span></div>
      <h3>Stack</h3>
      <table class="data-table"><tbody>
        ${STACK.map(([k, v]) => `<tr><td><b>${esc(k)}</b></td><td class="muted">${esc(v)}</td></tr>`).join("")}
      </tbody></table>
      <h3>Live status</h3>
      <div data-el="live"></div>
      <h3>Storage &amp; retention</h3>
      <div data-el="storage"></div>
      <h3>Request inspector (last 50)</h3>
      <div data-el="requests"></div>
      <h3>Gateway log tail</h3>
      <div data-el="logs"></div>
    `;
  }
  mount() {
    this.fetch();
    const t = setInterval(() => this.fetch(), 15e3);
    this.onDisconnect(() => clearInterval(t));
  }
  async fetch() {
    try {
      this.status = await api.status();
    } catch {
      this.status = null;
    }
    try {
      this.cert = await api.certHash();
    } catch {
      this.cert = null;
    }
    try {
      this.logs = (await api.logs(50)).lines;
    } catch {
      this.logs = [];
    }
    this.renderLive();
    this.renderStorage();
    this.renderRequests();
    this.renderLogs();
  }
  renderLive() {
    const el = this.querySelector('[data-el="live"]');
    if (!el) return;
    if (!this.status) {
      el.innerHTML = `<div class="empty-state">Gateway unreachable.</div>`;
      return;
    }
    const s = this.status;
    const up = s.uptime_secs != null ? formatDuration(s.uptime_secs) : "\u2014";
    el.innerHTML = `<div class="cards">
      <div class="card"><div class="big">${esc(up)}</div><div class="lbl">gateway uptime</div></div>
      <div class="card"><div class="big">${s.auth_enabled ? "\u{1F512}" : "\u{1F513}"}</div><div class="lbl">WebAuthn ${s.auth_enabled ? "ON" : "off (loopback trust)"}</div></div>
      <div class="card"><div class="big">${s.traces}</div><div class="lbl">traces</div></div>
      <div class="card"><div class="big">${s.findings}</div><div class="lbl">findings</div></div>
      <div class="card"><div class="big" style="font-size:0.9em">${esc(s.pubkey.slice(0, 16))}\u2026</div><div class="lbl">provenance pubkey</div></div>
      ${this.cert ? `<div class="card"><div class="big" style="font-size:0.9em">${esc(this.cert.hash.slice(0, 12))}\u2026</div><div class="lbl">WT cert, expires ${esc(relTime(this.cert.expires).replace(" ago", ""))}</div></div>` : ""}
    </div>`;
  }
  renderStorage() {
    const el = this.querySelector('[data-el="storage"]');
    if (!el) return;
    const st = this.status?.storage;
    if (!st) {
      el.innerHTML = `<div class="empty-state">No storage stats.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table"><tbody>
        <tr><td><b>substrate DB</b></td><td>${formatBytes(st.db_bytes ?? 0)}${st.wal_bytes ? ` (+ ${formatBytes(st.wal_bytes)} WAL)` : ""}</td></tr>
        <tr><td><b>oldest trace</b></td><td class="muted">${esc(st.oldest_ts || "\u2014")}</td></tr>
        <tr><td><b>newest trace</b></td><td class="muted">${esc(st.newest_ts || "\u2014")}</td></tr>
      </tbody></table>
      <p>
        <input type="text" data-el="prune-ts" placeholder="prune before (ISO ts, e.g. 2026-01-01T00:00:00Z)" size="42" />
        <button class="secondary" data-act="prune">Prune + re-anchor</button>
        <span class="muted" data-el="prune-result"></span>
      </p>
      <p class="muted">Prune deletes traces older than the timestamp AND rewrites the provenance
      anchor log from the surviving chain \u2014 the prune itself is recorded as a trace, so the
      action stays auditable.</p>`;
    this.querySelector('[data-act="prune"]')?.addEventListener("click", async () => {
      const input = this.querySelector('[data-el="prune-ts"]');
      const out = this.querySelector('[data-el="prune-result"]');
      const ts = input.value.trim();
      if (!ts) {
        out.textContent = "enter a timestamp first";
        return;
      }
      if (!confirm(`Delete all traces before ${ts} and re-anchor the chain? This cannot be undone.`)) return;
      try {
        const res = await api.prune(ts);
        out.textContent = `deleted ${res.deleted}, re-anchored ${res.reanchored}`;
        this.fetch();
      } catch (err) {
        out.textContent = `prune failed: ${err instanceof Error ? err.message : String(err)}`;
      }
    });
  }
  renderRequests() {
    const el = this.querySelector('[data-el="requests"]');
    if (!el) return;
    const reqs = this.status?.last_requests ?? [];
    if (!reqs.length) {
      el.innerHTML = `<div class="empty-state">No API requests recorded since gateway start.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>ms</th></tr></thead>
        <tbody>
          ${reqs.slice().reverse().map(
      (r) => `<tr>
              <td class="muted" title="${esc(r.ts)}">${esc(relTime(r.ts))}</td>
              <td>${esc(r.method)}</td>
              <td><code>${esc(r.path)}</code></td>
              <td class="${r.status >= 400 ? "sell" : "muted"}">${r.status}</td>
              <td class="muted">${r.ms}</td>
            </tr>`
    ).join("")}
        </tbody>
      </table>`;
  }
  renderLogs() {
    const el = this.querySelector('[data-el="logs"]');
    if (!el) return;
    if (!this.logs.length) {
      el.innerHTML = `<div class="empty-state">Log ring is empty.</div>`;
      return;
    }
    el.innerHTML = `<div class="json-drawer">${this.logs.map((l) => `${esc(l.ts)} [${esc(l.level)}] ${esc(l.msg)}`).join("\n")}</div>`;
  }
};
function formatBytes(n) {
  if (n > 1 << 20) return (n / (1 << 20)).toFixed(1) + " MB";
  if (n > 1 << 10) return (n / (1 << 10)).toFixed(1) + " KB";
  return n + " B";
}
function formatDuration(secs) {
  if (secs < 90) return `${secs}s`;
  const m2 = Math.floor(secs / 60);
  if (m2 < 90) return `${m2}m`;
  const h = Math.floor(m2 / 60);
  if (h < 36) return `${h}h ${m2 % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

// ../shared/webnn_score.js
var ANOMALY_SCORE_THRESHOLD = 0.75;
var ANOMALY_MIN_SAMPLES = 3;
var W1 = [
  [1.4, -0.5, 0.3],
  [-0.4, 1.2, 0.6],
  [0.5, 0.4, -0.7],
  [1, -0.3, 0.2],
  [-0.6, 0.9, 0.4],
  [0.3, -1, 0.5],
  [1.1, 0.2, -0.4],
  [-0.3, 0.7, 1]
];
var B1 = [0.2, -0.2, 0.1, -0.3, 0.25, 0, -0.1, 0.2];
var W2 = [[1.2], [-0.9], [1], [-0.8], [0.9], [-0.6], [0.8], [-0.5]];
var B2 = [0];
function relu(x3) {
  return x3 > 0 ? x3 : 0;
}
function scoreCPU(features) {
  const h = new Array(8);
  for (let j = 0; j < 8; j++) {
    let s2 = B1[j];
    for (let i = 0; i < 3; i++) s2 += W1[j][i] * features[i];
    h[j] = relu(s2);
  }
  let s = B2[0];
  for (let j = 0; j < 8; j++) s += W2[j][0] * h[j];
  return 1 / (1 + Math.exp(-s));
}
async function scoreWebNN(features) {
  const ctx2 = navigator.ml.createContext();
  const b = new MLGraphBuilder(ctx2);
  const f = b.input("f", { type: "float32", dimensions: [1, 3] });
  const w1 = b.constant({ type: "float32", dimensions: [8, 3] }, Float32Array.from(W1.flat()));
  const b1 = b.constant({ type: "float32", dimensions: [8] }, Float32Array.from(B1));
  const w2 = b.constant({ type: "float32", dimensions: [1, 8] }, Float32Array.from(W2.flat()));
  const b2 = b.constant({ type: "float32", dimensions: [1] }, Float32Array.from(B2));
  let h = b.matmul(f, b.transpose(w1, { permutation: [1, 0] }));
  h = b.add(h, b1);
  h = b.relu(h);
  let o = b.matmul(h, b.transpose(w2, { permutation: [1, 0] }));
  o = b.add(o, b2);
  const g = await b.build({ o });
  const fb = new Float32Array([features[0], features[1], features[2]]);
  const out = new Float32Array(1);
  const eb = await ctx2.createEphemeralExecutionContext(g);
  eb.execute({ f: fb }, { o: out });
  return 1 / (1 + Math.exp(-out[0]));
}
async function detectWebNNBackend() {
  try {
    if (!navigator.ml || !navigator.ml.createContext) {
      return { backend: "cpu", detail: "navigator.ml unavailable (enable the WebNN origin trial in chrome://flags)" };
    }
    const ctx2 = navigator.ml.createContext();
    const builder = new MLGraphBuilder(ctx2);
    const a2 = builder.input("a", { type: "float32", dimensions: [1, 1] });
    const b = builder.input("b", { type: "float32", dimensions: [1, 1] });
    const c2 = builder.matmul(a2, b);
    await builder.build({ c: c2 });
    return { backend: "webnn", detail: "graph probe compiled OK" };
  } catch (e) {
    return { backend: "cpu", detail: `WebNN probe failed: ${e.message}` };
  }
}
async function scoreAnomaly(features, backend) {
  if (backend === "webnn") {
    try {
      return await scoreWebNN(features);
    } catch {
      return scoreCPU(features);
    }
  }
  return scoreCPU(features);
}
function aggregateByAction(traces) {
  const byAction = {};
  for (const t of traces) {
    const k = t.action || "?";
    byAction[k] = byAction[k] || { action: k, total: 0, fail: 0, latest: 0 };
    byAction[k].total++;
    if (t.outcome === "failure") byAction[k].fail++;
    byAction[k].latest = Math.max(byAction[k].latest, t.ts ? Date.parse(t.ts) : 0);
  }
  return Object.values(byAction);
}
async function computeAnomalyRows(traces, backend) {
  const rows = aggregateByAction(traces);
  if (rows.length === 0) return [];
  const now2 = Date.now();
  const maxCount = Math.max(...rows.map((r) => r.total));
  for (const r of rows) {
    const rate = r.fail / r.total;
    const countN = r.total / maxCount;
    const recency = r.latest ? Math.min(1, (now2 - r.latest) / 864e5) : 0;
    r.rate = rate;
    r.score = await scoreAnomaly([rate, countN, recency], backend);
  }
  rows.sort((a2, b) => b.score - a2.score);
  for (const r of rows) {
    r.anomaly = r.score > ANOMALY_SCORE_THRESHOLD && r.total >= ANOMALY_MIN_SAMPLES;
  }
  return rows;
}

// src/views/ondevice.ts
var OndeviceView = class extends MyceliumElement {
  backend = "cpu";
  rows = [];
  pushedCount = null;
  render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>On-device Mining</h2>
        <div class="view-filters">
          <button data-act="run">Run mine cycle</button>
        </div>
      </div>
      <p>WebNN (<code>navigator.ml</code>) when available, CPU fallback otherwise -- scoring runs
      entirely in this tab; findings are pushed back to the substrate as regular traces. Same math
      as the standalone <a href="/web/webnn_miner.html" target="_blank">debug harness</a>.</p>
      <div class="panel" data-el="backend">detecting backend\u2026</div>
      <div data-el="output"></div>
    `;
    this.querySelector('[data-act="run"]').addEventListener("click", () => this.runCycle());
    this.detectBackend();
  }
  async detectBackend() {
    const { backend, detail } = await detectWebNNBackend();
    this.backend = backend;
    const el = this.querySelector('[data-el="backend"]');
    if (el) {
      el.innerHTML = `backend: <strong>${backend === "webnn" ? "WebNN (navigator.ml)" : "CPU fallback"}</strong> \u2014 ${esc(detail)}`;
    }
  }
  async runCycle() {
    const btn = this.querySelector('[data-act="run"]');
    const out = this.querySelector('[data-el="output"]');
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Running\u2026";
    }
    out.innerHTML = `<div class="empty-state">Fetching traces\u2026</div>`;
    try {
      const { traces } = await api.traces({ limit: 500 });
      if (!traces.length) {
        out.innerHTML = `<div class="empty-state">No traces from the gateway.</div>`;
        return;
      }
      this.rows = await computeAnomalyRows(traces, this.backend);
      const anomalies = this.rows.filter((r) => r.anomaly);
      for (const a2 of anomalies) {
        await api.emitTrace({
          agent: "dashboard-ondevice",
          session: "webnn",
          kind: "tool_call",
          action: "webnn_finding",
          target: "gateway",
          outcome: "success",
          payload: {
            miner: "webnn_anomaly",
            confidence: Math.min(0.97, a2.score),
            title: `WebNN miner: failure burst on '${a2.action}' (${Math.round(a2.rate * 100)}%)`,
            evidence: `${a2.fail}/${a2.total} calls to '${a2.action}' failed; anomaly score ${a2.score.toFixed(2)}`,
            suggestion: "alert"
          }
        }).catch(() => {
        });
      }
      this.pushedCount = anomalies.length;
      this.renderRows(traces.length);
    } catch (err) {
      out.innerHTML = `<div class="empty-state">Mine cycle failed: ${esc(err instanceof Error ? err.message : String(err))}</div>`;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Run mine cycle";
      }
    }
  }
  renderRows(traceCount) {
    const out = this.querySelector('[data-el="output"]');
    if (!out) return;
    const anomalyCount = this.rows.filter((r) => r.anomaly).length;
    out.innerHTML = `
      <p>traces: ${traceCount} | actions: ${this.rows.length} | anomalies: ${anomalyCount}
      ${this.pushedCount != null ? `| pushed ${this.pushedCount} finding(s) to substrate` : ""}</p>
      <table class="data-table">
        <thead><tr><th>Action</th><th>N</th><th>Fail</th><th>Rate</th><th>Score</th></tr></thead>
        <tbody>
          ${this.rows.slice(0, 20).map(
      (r) => `
            <tr>
              <td>${esc(r.action)}</td>
              <td>${r.total}</td>
              <td>${r.fail}</td>
              <td>${r.rate.toFixed(2)}</td>
              <td>${r.score.toFixed(3)} ${r.score > ANOMALY_SCORE_THRESHOLD ? "\u26A0" : ""}</td>
            </tr>
          `
    ).join("")}
        </tbody>
      </table>
    `;
  }
};

// src/main.ts
var WT_ORIGIN = "https://127.0.0.1:8812";
customElements.define("myc-status-bar", StatusBar);
customElements.define("myc-finding-card", FindingCard);
customElements.define("myc-lock-screen", LockScreen);
customElements.define("myc-live-view", LiveView);
customElements.define("myc-traces-view", TracesView);
customElements.define("myc-council-view", CouncilView);
customElements.define("myc-picks-view", PicksView);
customElements.define("myc-pools-view", PoolsView);
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
  let sinceTraceTs = "";
  let sinceFindingTs = "";
  try {
    const [status, traces, findings] = await Promise.all([
      api.status(),
      api.traces({ limit: 100 }),
      api.findings({ limit: 200 })
    ]);
    store.setStatus(status);
    store.seedTraces(traces.traces);
    store.seedFindings(findings.findings);
    sinceTraceTs = traces.traces[0]?.ts ?? "";
    sinceFindingTs = findings.findings.reduce((max, f) => f.created_ts > max ? f.created_ts : max, "");
  } catch (err) {
    console.warn("mycelium: initial snapshot fetch failed, will rely on the live stream", err);
  }
  if (store.get().locked) {
    document.body.appendChild(document.createElement("myc-lock-screen"));
    return;
  }
  let closeCurrentStream = null;
  function handlers() {
    return {
      onOpen: () => store.setConnState("open"),
      onClose: () => store.setConnState("connecting"),
      onTrace: (t) => store.pushTrace(t),
      onFinding: (f) => store.upsertFinding(f),
      onProvenance: (p) => store.setProvenance(p)
    };
  }
  function startTransport(transport, sinceTrace = "", sinceFinding = "") {
    closeCurrentStream?.();
    closeCurrentStream = null;
    store.setConnState("connecting");
    if (transport === "webtransport") {
      openWebTransportStream(WT_ORIGIN, handlers()).then((close) => {
        closeCurrentStream = close;
      }).catch((err) => {
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
  setInterval(() => {
    api.status().then((s) => store.setStatus(s)).catch(() => {
    });
  }, 3e4);
  const body = document.body;
  body.prepend(document.createElement("myc-status-bar"));
  const outlet = document.createElement("main");
  outlet.className = "view-outlet";
  body.appendChild(outlet);
  startRouter(outlet);
}
bootstrap();
//# sourceMappingURL=main.js.map
