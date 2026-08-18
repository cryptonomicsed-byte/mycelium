// Mycelium gateway — SSE live-update stream (v0.4, dashboard).
//
// A dashboard client bootstraps via the existing GET /api/traces and
// GET /api/findings, then opens this stream with the max timestamps it
// already has so no history is re-sent. New traces/findings poll the
// substrate on a short tick; provenance re-verifies on a longer one --
// buildChain() re-signs the ENTIRE chain on every call (gateway/main.go),
// real cost that grows with trace count, so ticking that at the same
// cadence as trace polling would get expensive fast. Incremental tail
// verification (only re-checking envelopes past the last-verified index)
// is the natural v0.5 fix once chain length makes this tick matter; noted
// here rather than built now since nothing here is under real load yet.
//
// GET /api/stream?since_trace_ts=<ISO8601>&since_finding_ts=<ISO8601>
//
// Named SSE events:
//
//	trace       one new trace row (same shape as GET /api/traces)
//	finding     one new/changed finding row (same shape as GET /api/findings)
//	provenance  {valid, anchored, reason} -- same shape as
//	            GET /api/provenance/verify. Fires unconditionally on its own
//	            ~15s tick (not only when something changed), which is what
//	            makes it double as the stream's liveness signal: a client
//	            with no trace/finding activity still gets a fresh, non-stale
//	            tamper-status event at least every ~15s. A separate
//	            "heartbeat" event type would only ever fire in a gap this
//	            tick already closes, so there isn't one.
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

const (
	streamTraceTick      = 2 * time.Second
	streamProvenanceTick = 15 * time.Second
	streamPollLimit      = 200 // per tick, per kind -- bounds one poll's worst case
)

// writeSSE serializes one named SSE event and flushes it immediately (SSE
// has no framing beyond blank-line-terminated text; without an explicit
// Flush, Go's http server would happily buffer this behind Nagle/transport
// buffering and the "live" stream would arrive in laggy bursts instead).
// Returns false if the write failed (client gone), signaling the caller to
// stop the stream rather than spin forever writing into a dead connection.
func writeSSE(w http.ResponseWriter, flusher http.Flusher, event string, data any) bool {
	raw, err := json.Marshal(data)
	if err != nil {
		return false
	}
	if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, raw); err != nil {
		return false
	}
	flusher.Flush()
	return true
}

func streamProvenanceEvent(w http.ResponseWriter, flusher http.Flusher) bool {
	db := openDB()
	defer db.Close()
	chain, err := buildChain(db)
	if err != nil {
		return writeSSE(w, flusher, "provenance", map[string]any{
			"valid": false, "anchored": 0, "reason": "chain build failed: " + err.Error(),
		})
	}
	cryptoOK, _, cryptoWhy := verifyChain(chain)
	anchOK, anchored, anchorWhy := reconcile(chain)
	reason := cryptoWhy
	if !anchOK {
		reason = anchorWhy
	}
	return writeSSE(w, flusher, "provenance", map[string]any{
		"valid": cryptoOK && anchOK, "anchored": anchored, "reason": reason,
	})
}

// streamNewTraces polls traces with ts > since, sends each as its own SSE
// event, and returns the new watermark (or the old one, unchanged, if
// nothing new landed) plus whether the connection is still alive.
func streamNewTraces(w http.ResponseWriter, flusher http.Flusher, since string) (string, bool) {
	db := openDB()
	defer db.Close()
	rows, err := db.Query(
		`SELECT id,ts,agent,session,kind,action,target,outcome,duration_ms,payload
		   FROM traces WHERE ts > ? ORDER BY ts ASC LIMIT ?`,
		since, streamPollLimit)
	if err != nil {
		return since, true // transient query error -- keep the connection, try again next tick
	}
	defer rows.Close()
	for rows.Next() {
		var id, ts, agent, session, kind, action, target, outcome, payload string
		var dur *int64
		if rows.Scan(&id, &ts, &agent, &session, &kind, &action, &target, &outcome, &dur, &payload) != nil {
			continue
		}
		since = ts
		if !writeSSE(w, flusher, "trace", map[string]any{
			"id": id, "ts": ts, "agent": agent, "session": session, "kind": kind,
			"action": action, "target": target, "outcome": outcome,
			"duration_ms": dur, "payload": payload,
		}) {
			return since, false
		}
	}
	return since, true
}

// streamNewFindings mirrors streamNewTraces for findings, watermarked on
// created_ts. A finding whose state changes (applied/dismissed) after its
// created_ts has already passed the watermark will NOT re-emit here -- the
// dashboard's apply/dismiss actions already know the new state locally
// (they're the ones that caused it), so this only needs to cover genuinely
// new findings from a mine cycle running elsewhere (cron, another agent).
func streamNewFindings(w http.ResponseWriter, flusher http.Flusher, since string) (string, bool) {
	db := openDB()
	defer db.Close()
	rows, err := db.Query(
		`SELECT id,created_ts,miner,confidence,title,evidence,suggestion,state,payload
		   FROM findings WHERE created_ts > ? ORDER BY created_ts ASC LIMIT ?`,
		since, streamPollLimit)
	if err != nil {
		return since, true
	}
	defer rows.Close()
	for rows.Next() {
		var id, created, miner, title, evidence, suggestion, state, payload string
		var conf float64
		if rows.Scan(&id, &created, &miner, &conf, &title, &evidence, &suggestion, &state, &payload) != nil {
			continue
		}
		since = created
		if !writeSSE(w, flusher, "finding", map[string]any{
			"id": id, "created_ts": created, "miner": miner, "confidence": conf,
			"title": title, "evidence": evidence, "suggestion": suggestion,
			"state": state, "payload": payload,
		}) {
			return since, false
		}
	}
	return since, true
}

func handleStream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, 500, map[string]any{"error": "streaming unsupported by this response writer"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	q := r.URL.Query()
	sinceTrace := q.Get("since_trace_ts")
	sinceFinding := q.Get("since_finding_ts")

	// Immediate snapshot on connect: the client shouldn't wait a full tick
	// for the first tamper-status read.
	if !streamProvenanceEvent(w, flusher) {
		return
	}

	traceTicker := time.NewTicker(streamTraceTick)
	defer traceTicker.Stop()
	provTicker := time.NewTicker(streamProvenanceTick)
	defer provTicker.Stop()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case <-traceTicker.C:
			var alive bool
			sinceTrace, alive = streamNewTraces(w, flusher, sinceTrace)
			if !alive {
				return
			}
			sinceFinding, alive = streamNewFindings(w, flusher, sinceFinding)
			if !alive {
				return
			}
		case <-provTicker.C:
			if !streamProvenanceEvent(w, flusher) {
				return
			}
		}
	}
}
