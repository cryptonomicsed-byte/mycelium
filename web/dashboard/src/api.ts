// Typed fetchers for the gateway REST surface + the SSE client.
// Same-origin by default (dashboard served from the gateway's own /web/) --
// GATEWAY_BASE is empty so fetch() resolves relative to the current origin.
// Overridable for local dev against a separate dev server (paired with
// MYCELIUM_GATEWAY_DEV_CORS=1 on the Go side).
import type {
  Trace, Finding, MinerStat, ProvenanceStatus, ProvenanceChain, GatewayStatus,
  Kind, Outcome, AgentStat, SkillEntry, AlertResult, StatsBucket, Pick,
} from "./types.js";
import { store } from "./store.js";

const GATEWAY_BASE = (window as any).__MYCELIUM_GATEWAY_BASE__ ?? "";

// A 401 here means MYCELIUM_GATEWAY_AUTH=1 is set gateway-side and there's
// no valid session -- flip the store-wide lock rather than let every
// individual view handle it separately (main.ts mounts <myc-lock-screen>
// in place of the shell when store.locked is true). Any other status still
// throws normally so callers can react to their own errors.
function noteAuthStatus(status: number) {
  if (status === 401) store.setLocked(true);
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(GATEWAY_BASE + path);
  if (!res.ok) {
    noteAuthStatus(res.status);
    throw new Error(`GET ${path} -> ${res.status} ${await res.text().catch(() => "")}`);
  }
  return res.json() as Promise<T>;
}

async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(GATEWAY_BASE + path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    noteAuthStatus(res.status);
    const msg = (data as any)?.error ?? `HTTP ${res.status}`;
    const err = new Error(msg) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return data as T;
}

export interface TraceFilters {
  agent?: string;
  kind?: Kind;
  action?: string;
  outcome?: Outcome;
  since?: string; // forward cursor: ts > since
  before?: string; // backward cursor: ts < before (Trace Explorer paging)
  limit?: number;
}

function qs(params: object): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params as Record<string, string | number | undefined>)) {
    if (v !== undefined && v !== "") parts.push(`${k}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  status: () => getJSON<GatewayStatus>("/api/status"),

  traces: (filters: TraceFilters = {}) =>
    getJSON<{ count: number; traces: Trace[] }>(`/api/traces${qs(filters)}`),

  findings: (filters: { state?: string; miner?: string; since?: string; limit?: number } = {}) =>
    getJSON<{ count: number; findings: Finding[] }>(`/api/findings${qs(filters)}`),

  applyFinding: (id: string) =>
    postJSON<{ status: string; path: string; slug: string; finding_id: string }>(
      `/api/findings/${encodeURIComponent(id)}/apply`,
    ),

  dismissFinding: (id: string) =>
    postJSON<{ status: string; id: string }>(`/api/findings/${encodeURIComponent(id)}/dismiss`),

  miners: () => getJSON<{ count: number; miners: MinerStat[] }>("/api/miners"),

  mine: () => postJSON<Record<string, unknown>>("/api/mine"),
  mineWasm: (limit = 500) => postJSON<Record<string, unknown>>(`/api/mine/wasm?limit=${limit}`),

  provenance: () => getJSON<ProvenanceChain>("/api/provenance"),
  provenanceVerify: () => getJSON<ProvenanceStatus>("/api/provenance/verify"),

  emitTrace: (t: {
    agent: string; session: string; kind: Kind; action?: string;
    target?: string; outcome?: Outcome; payload?: unknown;
  }) => postJSON<{ status: string; id: string }>("/api/trace", t),

  // Ops endpoints (gateway/ops.go)
  agents: () => getJSON<{ count: number; agents: AgentStat[] }>("/api/agents"),
  skills: () => getJSON<{ count: number; skills: SkillEntry[] }>("/api/skills"),
  alerts: () => getJSON<{ alerts: AlertResult[]; tripped: number }>("/api/alerts"),
  logs: (lines = 200, level = "") =>
    getJSON<{ count: number; lines: { ts: string; level: string; msg: string }[] }>(
      `/api/logs${qs({ lines, level })}`,
    ),
  statsTimeseries: (range: "24h" | "7d" | "30d" = "24h") =>
    getJSON<{ range: string; count: number; buckets: StatsBucket[] }>(
      `/api/stats/timeseries?range=${range}`,
    ),
  prune: (beforeTs: string) =>
    postJSON<{ deleted: number; reanchored: number }>("/api/prune", { before_ts: beforeTs }),
  picks: () => getJSON<{ picks?: Pick[]; count?: number } | Pick[]>("/api/picks"),
  certHash: () => getJSON<{ hash: string; expires: string }>("/api/webtransport/cert-hash"),
};

// --------------------------------------------------------------------- SSE

export interface StreamHandlers {
  onTrace?: (t: Trace) => void;
  onFinding?: (f: Finding) => void;
  onProvenance?: (p: ProvenanceStatus) => void;
  onOpen?: () => void;
  onClose?: () => void;
}

/** Opens GET /api/stream and dispatches named events. Auto-reconnects with
 * backoff on failure -- a dropped connection (gateway restart, network
 * blip) should recover on its own rather than leave the dashboard silently
 * stale. Returns a function that permanently closes the stream. */
export function openStream(
  sinceTraceTs: string,
  sinceFindingTs: string,
  handlers: StreamHandlers,
): () => void {
  let closed = false;
  let es: EventSource | null = null;
  let backoffMs = 1000;
  const maxBackoffMs = 30_000;

  function connect() {
    if (closed) return;
    const url = `${GATEWAY_BASE}/api/stream${qs({
      since_trace_ts: sinceTraceTs,
      since_finding_ts: sinceFindingTs,
    })}`;
    es = new EventSource(url);

    es.addEventListener("open", () => {
      backoffMs = 1000; // reset backoff once a connection actually succeeds
      handlers.onOpen?.();
    });
    es.addEventListener("trace", (ev) => {
      const t = JSON.parse((ev as MessageEvent).data) as Trace;
      sinceTraceTs = t.ts;
      handlers.onTrace?.(t);
    });
    es.addEventListener("finding", (ev) => {
      const f = JSON.parse((ev as MessageEvent).data) as Finding;
      sinceFindingTs = f.created_ts;
      handlers.onFinding?.(f);
    });
    es.addEventListener("provenance", (ev) => {
      handlers.onProvenance?.(JSON.parse((ev as MessageEvent).data) as ProvenanceStatus);
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
