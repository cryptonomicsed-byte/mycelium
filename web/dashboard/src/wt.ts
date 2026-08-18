// WebTransport live-update client, mirroring api.ts's openStream() shape.
// Chromium-only (feature-detect window.WebTransport) and strictly
// experimental -- this is an alternative to SSE the operator opts into via
// the status bar's transport toggle, never a silent auto-upgrade. The
// toggle is exclusive: switching to WebTransport tears down the SSE
// EventSource first, since concurrent SSE+WT push would double-insert into
// store.pushTrace (no id-based dedupe exists there).
//
// The cert hash (GET /api/webtransport/cert-hash) is fetched fresh
// immediately before every connection attempt, never cached -- gateway/
// wt.go rotates its cert in place while the process runs, and
// serverCertificateHashes is only checked at connection establishment; an
// already-open session is unaffected by rotation, but a new connection
// pinned to a stale hash would fail outright.
import type { StreamHandlers } from "./api.js";
import type { Trace, Finding, ProvenanceStatus } from "./types.js";

export function webTransportSupported(): boolean {
  return typeof window !== "undefined" && "WebTransport" in window;
}

function b64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

interface Envelope {
  type: string;
  data: unknown;
}

function dispatch(envelope: Envelope, handlers: StreamHandlers) {
  switch (envelope.type) {
    case "trace":
      handlers.onTrace?.(envelope.data as Trace);
      break;
    case "finding":
      handlers.onFinding?.(envelope.data as Finding);
      break;
    case "provenance":
      handlers.onProvenance?.(envelope.data as ProvenanceStatus);
      break;
  }
}

// wt.go's broadcastToSession frames each event as a 4-byte big-endian
// length prefix + JSON body (WebTransport streams have no built-in message
// framing the way SSE's blank-line-terminated text does) -- this reads
// that framing back off the one server-opened uni-stream.
async function readBroadcastStream(stream: ReadableStream<Uint8Array>, handlers: StreamHandlers) {
  const reader = stream.getReader();
  let buf = new Uint8Array(0);

  function append(chunk: Uint8Array) {
    const next = new Uint8Array(buf.length + chunk.length);
    next.set(buf);
    next.set(chunk, buf.length);
    buf = next;
  }

  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    if (value) append(value);

    for (;;) {
      if (buf.length < 4) break;
      // >>> 0 forces unsigned interpretation -- plain << would treat a
      // length with the high bit set as negative (JS bitwise ops are
      // 32-bit signed).
      const len = ((buf[0]! << 24) | (buf[1]! << 16) | (buf[2]! << 8) | buf[3]!) >>> 0;
      if (buf.length < 4 + len) break;
      const bodyBytes = buf.slice(4, 4 + len);
      buf = buf.slice(4 + len);
      try {
        dispatch(JSON.parse(new TextDecoder().decode(bodyBytes)) as Envelope, handlers);
      } catch {
        // malformed envelope -- skip it, don't kill the whole stream over one bad frame
      }
    }
  }
}

/** Opens a WebTransport session pinned to the gateway's current cert hash
 * and dispatches events into the same handlers api.ts's openStream() (SSE)
 * uses, so callers don't need a second event model. Returns a function
 * that closes the session. */
export async function openWebTransportStream(
  wtOrigin: string,
  handlers: StreamHandlers,
): Promise<() => void> {
  const res = await fetch("/api/webtransport/cert-hash");
  if (!res.ok) throw new Error(`cert-hash fetch failed: ${res.status}`);
  const { hash } = (await res.json()) as { hash: string };

  const transport = new WebTransport(`${wtOrigin}/api/wt`, {
    serverCertificateHashes: [{ algorithm: "sha-256", value: b64ToBytes(hash) as BufferSource }],
  });

  let closed = false;
  transport.closed
    .then(() => {
      if (!closed) handlers.onClose?.();
    })
    .catch(() => {
      if (!closed) handlers.onClose?.();
    });

  await transport.ready;
  handlers.onOpen?.();

  (async () => {
    try {
      const reader = transport.incomingUnidirectionalStreams.getReader();
      const { value: stream, done } = await reader.read();
      if (done || !stream) return;
      await readBroadcastStream(stream as ReadableStream<Uint8Array>, handlers);
    } catch {
      if (!closed) handlers.onClose?.();
    }
  })();

  return () => {
    closed = true;
    try {
      transport.close();
    } catch {
      // already closed -- fine
    }
  };
}
