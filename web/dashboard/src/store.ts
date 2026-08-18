// Minimal global store: one source of truth for the state every view and
// the persistent top bar need (status counts, SSE connection state, the
// provenance badge, and a bounded live buffer of recent traces/findings).
// Deliberately not Redux-shaped -- six views and a top bar don't need
// reducers/actions/middleware, just "hold some state, notify subscribers".
import type { Trace, Finding, ProvenanceStatus, GatewayStatus } from "./types.js";

export type ConnState = "connecting" | "open" | "closed";

export interface AppState {
  status: GatewayStatus | null;
  provenance: ProvenanceStatus | null;
  connState: ConnState;
  recentTraces: Trace[]; // newest first, bounded
  findingsById: Map<string, Finding>;
}

const MAX_RECENT_TRACES = 500;

type Listener = (state: Readonly<AppState>) => void;

class Store {
  private state: AppState = {
    status: null,
    provenance: null,
    connState: "connecting",
    recentTraces: [],
    findingsById: new Map(),
  };
  private listeners = new Set<Listener>();

  get(): Readonly<AppState> {
    return this.state;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    fn(this.state); // immediate replay so a newly-mounted view doesn't wait for the next event
    return () => this.listeners.delete(fn);
  }

  private notify() {
    for (const fn of this.listeners) fn(this.state);
  }

  setStatus(status: GatewayStatus) {
    this.state = { ...this.state, status };
    this.notify();
  }

  setProvenance(provenance: ProvenanceStatus) {
    this.state = { ...this.state, provenance };
    this.notify();
  }

  setConnState(connState: ConnState) {
    this.state = { ...this.state, connState };
    this.notify();
  }

  pushTrace(trace: Trace) {
    const recentTraces = [trace, ...this.state.recentTraces].slice(0, MAX_RECENT_TRACES);
    this.state = { ...this.state, recentTraces };
    this.notify();
  }

  seedTraces(traces: Trace[]) {
    // Bootstrap replaces rather than merges -- called once at startup with
    // the initial GET /api/traces page, newest-first already from the API.
    this.state = { ...this.state, recentTraces: traces.slice(0, MAX_RECENT_TRACES) };
    this.notify();
  }

  upsertFinding(finding: Finding) {
    const findingsById = new Map(this.state.findingsById);
    findingsById.set(finding.id, finding);
    this.state = { ...this.state, findingsById };
    this.notify();
  }

  seedFindings(findings: Finding[]) {
    const findingsById = new Map(findings.map((f) => [f.id, f]));
    this.state = { ...this.state, findingsById };
    this.notify();
  }
}

export const store = new Store();
