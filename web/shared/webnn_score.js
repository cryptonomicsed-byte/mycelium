// Mycelium — shared on-device anomaly scoring (WebNN, CPU fallback).
//
// Extracted from webnn_miner.html so its exact MLP (weights, forward pass,
// aggregation) is not forked into two implementations: the standalone
// harness at /web/webnn_miner.html and the dashboard's #/ondevice panel both
// import this. A plain ES module, loadable via <script type="module"> with
// no bundler -- the standalone page stays a zero-build-step debug harness,
// which is what it always was.
//
// The model: a fixed 3->8->1 MLP over per-action features
// [failure_rate, count_normalized, recency_score], scoring an anomaly
// probability in [0,1]. Weights are small hand-picked constants -- this is
// on-device INFERENCE, not training; WebNN and the CPU fallback compute the
// identical math and must agree on every input (see webnn_score.test.mjs).

export const ANOMALY_SCORE_THRESHOLD = 0.75;
export const ANOMALY_MIN_SAMPLES = 3;

const W1 = [
  [1.4, -0.5, 0.3], [-0.4, 1.2, 0.6], [0.5, 0.4, -0.7], [1.0, -0.3, 0.2],
  [-0.6, 0.9, 0.4], [0.3, -1.0, 0.5], [1.1, 0.2, -0.4], [-0.3, 0.7, 1.0],
];
const B1 = [0.2, -0.2, 0.1, -0.3, 0.25, 0.0, -0.1, 0.2];
const W2 = [[1.2], [-0.9], [1.0], [-0.8], [0.9], [-0.6], [0.8], [-0.5]];
const B2 = [0.0];

function relu(x) {
  return x > 0 ? x : 0;
}

/** CPU fallback: plain JS forward pass through the same 3->8->1 MLP. */
export function scoreCPU(features) {
  const h = new Array(8);
  for (let j = 0; j < 8; j++) {
    let s = B1[j];
    for (let i = 0; i < 3; i++) s += W1[j][i] * features[i];
    h[j] = relu(s);
  }
  let s = B2[0];
  for (let j = 0; j < 8; j++) s += W2[j][0] * h[j];
  return 1 / (1 + Math.exp(-s)); // sigmoid -> anomaly probability
}

/** WebNN version of the identical MLP, via MLGraphBuilder. Throws if
 * navigator.ml is unavailable -- callers should feature-detect first via
 * detectWebNNBackend() rather than try/catch this on every call. */
export async function scoreWebNN(features) {
  const ctx = navigator.ml.createContext();
  const b = new MLGraphBuilder(ctx);
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
  const eb = await ctx.createEphemeralExecutionContext(g);
  eb.execute({ f: fb }, { o: out });
  return 1 / (1 + Math.exp(-out[0]));
}

/** Probes navigator.ml with a throwaway 1x1 matmul to confirm the graph
 * actually compiles (having the API present is not the same as it working),
 * and returns which backend is safe to use for real scoring. Never throws --
 * any failure here just means "use CPU". */
export async function detectWebNNBackend() {
  try {
    if (!navigator.ml || !navigator.ml.createContext) {
      return { backend: "cpu", detail: "navigator.ml unavailable (enable the WebNN origin trial in chrome://flags)" };
    }
    const ctx = navigator.ml.createContext();
    const builder = new MLGraphBuilder(ctx);
    const a = builder.input("a", { type: "float32", dimensions: [1, 1] });
    const b = builder.input("b", { type: "float32", dimensions: [1, 1] });
    const c = builder.matmul(a, b);
    await builder.build({ c });
    return { backend: "webnn", detail: "graph probe compiled OK" };
  } catch (e) {
    return { backend: "cpu", detail: `WebNN probe failed: ${e.message}` };
  }
}

/** Scores one feature vector on the given backend, with CPU fallback if a
 * WebNN call throws mid-session (e.g. context lost) rather than surfacing a
 * scoring failure to the caller. */
export async function scoreAnomaly(features, backend) {
  if (backend === "webnn") {
    try {
      return await scoreWebNN(features);
    } catch {
      return scoreCPU(features);
    }
  }
  return scoreCPU(features);
}

/** Aggregates raw trace rows (as returned by GET /api/traces) into one row
 * per action: {action, total, fail, latest}. Pure function, no I/O -- both
 * the standalone page and the dashboard panel fetch traces themselves and
 * hand the array here. */
export function aggregateByAction(traces) {
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

/** End-to-end: aggregate -> feature vector -> score -> sorted rows with an
 * `anomaly` flag. The single function both the standalone page and the
 * dashboard's #/ondevice panel call so the whole pipeline -- not just the
 * MLP -- can't drift between the two surfaces. */
export async function computeAnomalyRows(traces, backend) {
  const rows = aggregateByAction(traces);
  if (rows.length === 0) return [];
  const now = Date.now();
  const maxCount = Math.max(...rows.map((r) => r.total));
  for (const r of rows) {
    const rate = r.fail / r.total;
    const countN = r.total / maxCount;
    const recency = r.latest ? Math.min(1, (now - r.latest) / 86400000) : 0;
    r.rate = rate;
    r.score = await scoreAnomaly([rate, countN, recency], backend);
  }
  rows.sort((a, b) => b.score - a.score);
  for (const r of rows) {
    r.anomaly = r.score > ANOMALY_SCORE_THRESHOLD && r.total >= ANOMALY_MIN_SAMPLES;
  }
  return rows;
}
