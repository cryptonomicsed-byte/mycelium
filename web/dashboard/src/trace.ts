// Self-improving-UI wiring: the dashboard emits real mycelium traces for its
// own usage, agent="dashboard-ui". Because recurring_workflow/anomaly/
// cross_agent/opportunity (mycelium/miners.py) already operate over ANY
// traces regardless of source, this closes the self-improvement loop with
// zero new miner code -- those miners start finding patterns in dashboard
// usage for free once it participates as a traced agent.
//
// Two constraints straight from mycelium/core.py, not stylistic choices:
//   - VALID_KINDS is a hard ValueError boundary (core.py:18) -- there is no
//     "ui_event" kind, so every call site here maps onto the existing fixed
//     vocabulary rather than inventing one.
//   - outcome stays "info" for actions that aren't really a success/failure
//     (e.g. changing a filter) -- forcing "success" on everything would
//     dilute the anomaly miner's failure-rate baseline for every OTHER
//     agent's traces sharing the substrate.
import { api } from "./api.js";
import type { Kind, Outcome } from "./types.js";

const SESSION_KEY = "mycelium.dashboard.session_id";

function sessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

function emit(kind: Kind, action: string, target: string, outcome: Outcome, payload?: unknown) {
  // Fire-and-forget: a trace-emission failure must never break the UI
  // action that triggered it.
  api.emitTrace({
    agent: "dashboard-ui", session: sessionId(), kind, action, target, outcome, payload,
  }).catch(() => {});
}

/** A finding was opened/expanded for detail. */
export function traceViewedFinding(findingId: string, miner: string) {
  emit("observation", "view_finding", findingId, "info", { miner });
}

/** A finding was applied via the UI. outcome reflects the real result -- an
 * apply failure IS a genuine failure, unlike a filter change. */
export function traceAppliedFinding(findingId: string, ok: boolean) {
  emit("decision", "apply_finding", findingId, ok ? "success" : "failure");
}

/** A finding was dismissed via the UI. */
export function traceDismissedFinding(findingId: string, ok: boolean) {
  emit("decision", "dismiss_finding", findingId, ok ? "success" : "failure");
}

/** A mine cycle (regular or Wasm) was forced from the Miners panel. */
export function traceForcedMine(kind: "cycle" | "wasm", ok: boolean) {
  emit("tool_call", kind === "wasm" ? "force_mine_wasm" : "force_mine", "substrate", ok ? "success" : "failure");
}

/** A filter (agent/kind/action/outcome/miner/confidence threshold) changed
 * on a list view. Not a success/failure -- just an observation. */
export function traceChangedFilter(view: string, filter: string) {
  emit("observation", "change_filter", `${view}:${filter}`, "info");
}

/** The provenance view was opened while the chain was in a tampered state --
 * worth its own signal, since "someone looked at this during a divergence"
 * is exactly the kind of cross-agent correlation the miners are built to
 * surface (see mycelium/miners.py's cross_agent miner). */
export function traceViewedTamperedProvenance() {
  emit("observation", "view_provenance_tampered", "chain", "info");
}
