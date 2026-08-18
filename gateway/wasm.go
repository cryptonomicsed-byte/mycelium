// Mycelium gateway — Wasm-sandboxed miner runner (v0.3).
//
// Loads a WebAssembly miner (compiled with GOOS=wasip1 GOARCH=wasm) into a
// wazero runtime and runs it against the current trace window. The miner
// reads traces JSON on stdin, writes findings JSON on stdout — the same
// interface as the Python miners in miners.py, but behind a true compile
// boundary: no filesystem, no network, no syscalls beyond WASI stdio. The
// substrate is only reachable through the host, which validates and persists
// whatever the miner returns.
//
// Endpoint: POST /api/mine/wasm?limit=500
package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/tetratelabs/wazero"
	"github.com/tetratelabs/wazero/imports/wasi_snapshot_preview1"
)

const wasmMinerPath = "/data/data/com.termux/files/home/mycelium/gateway/miner_recurring.wasm"

// Long-lived wazero runtime + compiled module. The runtime must outlive
// the module it compiled — a CompiledModule is tied to the runtime that
// produced it, so closing the runtime per-request would invalidate the cache.
var (
	wasmRuntime   wazero.Runtime
	wasmCompiled  wazero.CompiledModule
	wasmInitErr   error
	wasmInitOnce  sync.Once
)

func initWasmMiner() error {
	wasmInitOnce.Do(func() {
		raw, err := os.ReadFile(wasmMinerPath)
		if err != nil {
			wasmInitErr = fmt.Errorf("read wasm miner: %w", err)
			return
		}
		ctx := context.Background()
		rt := wazero.NewRuntime(ctx)
		wasi_snapshot_preview1.MustInstantiate(ctx, rt)
		compiled, err := rt.CompileModule(ctx, raw)
		if err != nil {
			wasmInitErr = fmt.Errorf("compile wasm: %w", err)
			return
		}
		wasmRuntime = rt
		wasmCompiled = compiled
	})
	return wasmInitErr
}

// runWasmMiner feeds traces JSON to the wasm miner's stdin and returns the
// findings JSON from its stdout. Uses the shared runtime; each call gets a
// fresh module instance (wazero instances are isolated).
func runWasmMiner(tracesJSON []byte, timeout time.Duration) ([]byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	var stdout, stderr bytes.Buffer
	config := wazero.NewModuleConfig().
		WithStdin(bytes.NewReader(tracesJSON)).
		WithStdout(&stdout).
		WithStderr(&stderr)

	// Instantiate once — wazero runs the wasi _start entrypoint, reading
	// traces from stdin and writing findings to stdout.
	if _, err := wasmRuntime.InstantiateModule(ctx, wasmCompiled, config); err != nil {
		return nil, fmt.Errorf("run wasm: %w (stderr: %s)", err, stderr.String())
	}
	return stdout.Bytes(), nil
}

func handleMineWasm(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	if err := initWasmMiner(); err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	db := openDB()
	defer db.Close()
	limit := "500"
	if l := r.URL.Query().Get("limit"); l != "" {
		limit = l
	}
	rows, err := db.Query("SELECT id,ts,agent,session,kind,action,target,outcome,duration_ms,payload FROM traces ORDER BY ts DESC LIMIT " + limit)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	defer rows.Close()
	var traces []map[string]any
	for rows.Next() {
		var id, ts, agent, session, kind, action, target, outcome, payload string
		var dur *int64
		rows.Scan(&id, &ts, &agent, &session, &kind, &action, &target, &outcome, &dur, &payload)
		traces = append(traces, map[string]any{
			"id": id, "ts": ts, "agent": agent, "session": session, "kind": kind,
			"action": action, "target": target, "outcome": outcome,
			"duration_ms": dur, "payload": payload,
		})
	}
	tracesJSON, _ := json.Marshal(traces)
	findingsJSON, err := runWasmMiner(tracesJSON, 30*time.Second)
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": err.Error()})
		return
	}
	var findings []map[string]any
	if err := json.Unmarshal(findingsJSON, &findings); err != nil {
		writeJSON(w, 502, map[string]any{"error": "wasm miner returned bad json: " + err.Error()})
		return
	}
	// Persist validated findings through the same dedupe path as Python miners.
	saved := 0
	for _, f := range findings {
		miner, _ := f["miner"].(string)
		title, _ := f["title"].(string)
		evidence, _ := f["evidence"].(string)
		suggestion, _ := f["suggestion"].(string)
		conf, _ := f["confidence"].(float64)
		payload, _ := f["payload"].(map[string]any)
		if miner == "" || title == "" {
			continue
		}
		_, err := db.Exec(
			"INSERT OR IGNORE INTO findings (id,created_ts,miner,confidence,title,evidence,suggestion,state,payload) VALUES (?,?,?,?,?,?,?,?,?)",
			newID(), time.Now().UTC().Format("2006-01-02T15:04:05Z"), miner, conf, title,
			evidence, suggestion, "open", mustJSON(payload),
		)
		if err == nil {
			saved++
		}
	}
	writeJSON(w, 200, map[string]any{
		"status": "ok", "wasm": true, "miner": "wasm_recurring",
		"traces_seen": len(traces), "findings": len(findings), "saved": saved,
	})
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
}

var _ = sql.ErrNoRows
