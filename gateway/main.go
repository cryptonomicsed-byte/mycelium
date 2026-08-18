// Mycelium gateway — v0.2 polyglot layer (Go).
//
// Serves the substrate over HTTP for any agent (curl / REST) and maintains
// an Ed25519-signed hash chain over every trace (tamper-evident provenance).
// Reads/writes the same SQLite file as the Python core (modernc.org/sqlite,
// pure Go — no cgo). Mining still runs in the Python sandbox (subprocess),
// because that's where the sandboxing lives; the gateway is the transport.
//
// Endpoints:
//   GET  /api/status               health + counts
//   POST /api/trace                emit a trace (JSON body)
//   GET  /api/traces?limit=N       query traces
//   GET  /api/findings?state=open  list findings
//   POST /api/mine                 run the sandboxed mining cycle
//   GET  /api/provenance           full signed chain
//   GET  /api/provenance/verify    re-verify chain integrity
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

const (
	dbPath    = "/data/data/com.termux/files/home/mycelium/mycelium.db"
	keyPath   = "/data/data/com.termux/files/home/mycelium/gateway/provenance_key.json"
	statePath = "/data/data/com.termux/files/home/mycelium/gateway/chain_state.jsonl"
	addr      = "127.0.0.1:8811"
	pythonCli = "/data/data/com.termux/files/home/mycelium/mycelium/cli.py"
	webDir    = "/data/data/com.termux/files/home/mycelium/web"
)

var (
	pubKey  ed25519.PublicKey
	privKey ed25519.PrivateKey
	// dbMu serializes trace inserts: each insertTrace opens its own
	// connection (modernc.org/sqlite), and concurrent writers (HTTP
	// handler + WebTransport stream/datagram goroutines) would otherwise
	// trip SQLITE_BUSY.
	dbMu sync.Mutex
)

// ------------------------------------------------------------------ key mgmt

func loadOrCreateKey() error {
	if data, err := os.ReadFile(keyPath); err == nil {
		var kp struct {
			Pub  string `json:"pub"`
			Priv string `json:"priv"`
		}
		if json.Unmarshal(data, &kp) == nil {
			pubKey, _ = hex.DecodeString(kp.Pub)
			privKey, _ = hex.DecodeString(kp.Priv)
			return nil
		}
	}
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	kp := map[string]string{
		"pub":  hex.EncodeToString(pub),
		"priv": hex.EncodeToString(priv),
	}
	raw, _ := json.MarshalIndent(kp, "", "  ")
	if err := os.MkdirAll(filepath.Dir(keyPath), 0o700); err != nil {
		return err
	}
	if err := os.WriteFile(keyPath, raw, 0o600); err != nil {
		return err
	}
	pubKey, privKey = pub, priv
	return nil
}

// ------------------------------------------------------------------ chain

type Envelope struct {
	Index     int64  `json:"index"`
	TraceID   string `json:"trace_id"`
	TS        string `json:"ts"`
	Action    string `json:"action"`
	Target    string `json:"target"`
	Outcome   string `json:"outcome"`
	Payload   string `json:"payload_sha"`
	PrevHash  string `json:"prev_hash"`
	Hash      string `json:"hash"`
	Sig       string `json:"sig"`
}

func envelopeBody(e Envelope) []byte {
	return []byte(fmt.Sprintf("%d|%s|%s|%s|%s|%s|%s|%s",
		e.Index, e.TraceID, e.TS, e.Action, e.Target, e.Outcome, e.Payload, e.PrevHash))
}

func buildChain(db *sql.DB) ([]Envelope, error) {
	rows, err := db.Query(
		"SELECT id, ts, COALESCE(action,''), COALESCE(target,''), COALESCE(outcome,''), COALESCE(payload,'{}') FROM traces ORDER BY ts, rowid")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var chain []Envelope
	var prevHash string
	var idx int64
	for rows.Next() {
		var id, ts, action, target, outcome, payload string
		if err := rows.Scan(&id, &ts, &action, &target, &outcome, &payload); err != nil {
			return nil, err
		}
		ph := sha256.Sum256([]byte(payload))
		e := Envelope{
			Index: idx, TraceID: id, TS: ts,
			Action: action, Target: target, Outcome: outcome,
			Payload: hex.EncodeToString(ph[:]), PrevHash: prevHash,
		}
		h := sha256.Sum256(envelopeBody(e))
		e.Hash = hex.EncodeToString(h[:])
		e.Sig = hex.EncodeToString(ed25519.Sign(privKey, envelopeBody(e)))
		chain = append(chain, e)
		prevHash = e.Hash
		idx++
	}
	return chain, rows.Err()
}

func verifyChain(chain []Envelope) (bool, int64, string) {
	var prevHash string
	for i := range chain {
		e := chain[i]
		if e.PrevHash != prevHash {
			return false, e.Index, "prev_hash mismatch"
		}
		h := sha256.Sum256(envelopeBody(e))
		if hex.EncodeToString(h[:]) != e.Hash {
			return false, e.Index, "hash mismatch"
		}
		sig, _ := hex.DecodeString(e.Sig)
		if !ed25519.Verify(pubKey, envelopeBody(e), sig) {
			return false, e.Index, "signature invalid"
		}
		prevHash = e.Hash
	}
	return true, int64(len(chain)), "ok"
}

// ------------------------------------------------------------------ anchor

type Recorded struct {
	Index   int64  `json:"index"`
	TraceID string `json:"trace_id"`
	Hash    string `json:"hash"`
	Sig     string `json:"sig"`
}

// loadRecorded reads the append-only anchor log (oldest -> newest).
func loadRecorded() []Recorded {
	var recs []Recorded
	data, err := os.ReadFile(statePath)
	if err != nil {
		return recs
	}
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		var r Recorded
		if json.Unmarshal([]byte(line), &r) == nil {
			recs = append(recs, r)
		}
	}
	return recs
}

// reconcile extends the anchor log with any new chain envelopes and reports
// divergence (tamper/corruption) when the DB-derived chain contradicts it.
// Returns (ok, checkedCount, reason).
func reconcile(chain []Envelope) (bool, int64, string) {
	recs := loadRecorded()
	if len(recs) > len(chain) {
		return false, int64(len(chain)), "anchor log longer than derived chain"
	}
	for i, r := range recs {
		e := chain[i]
		if r.TraceID != e.TraceID || r.Hash != e.Hash || r.Sig != e.Sig {
			return false, r.Index, "chain diverged from anchor log (tamper/corruption)"
		}
	}
	if len(chain) > len(recs) {
		f, err := os.OpenFile(statePath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
		if err != nil {
			return false, 0, "cannot open anchor log"
		}
		defer f.Close()
		for _, e := range chain[len(recs):] {
			rec := Recorded{Index: e.Index, TraceID: e.TraceID, Hash: e.Hash, Sig: e.Sig}
			raw, _ := json.Marshal(rec)
			if _, err := f.Write(append(raw, '\n')); err != nil {
				return false, e.Index, "anchor log append failed"
			}
		}
	}
	return true, int64(len(chain)), "ok"
}

// ------------------------------------------------------------------ handlers

func openDB() *sql.DB {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil
	}
	return db
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	if db == nil {
		writeJSON(w, 500, map[string]any{"status": "error", "msg": "cannot open db"})
		return
	}
	defer db.Close()
	var traces, findings int64
	db.QueryRow("SELECT COUNT(*) FROM traces").Scan(&traces)
	db.QueryRow("SELECT COUNT(*) FROM findings").Scan(&findings)
	writeJSON(w, 200, map[string]any{
		"status": "ok", "traces": traces, "findings": findings,
		"pubkey": hex.EncodeToString(pubKey),
	})
}

// insertTrace writes one trace envelope (JSON body) to SQLite and returns
// the response payload. Shared by the HTTP handler and the WebTransport
// telemetry pipe so both transports feed the SAME substrate + chain.
func insertTrace(body []byte) (int, map[string]any) {
	var t map[string]any
	if err := json.Unmarshal(body, &t); err != nil {
		return 400, map[string]any{"error": "bad json"}
	}
	agent, _ := t["agent"].(string)
	session, _ := t["session"].(string)
	kind, _ := t["kind"].(string)
	if agent == "" || session == "" || kind == "" {
		return 400, map[string]any{"error": "agent/session/kind required"}
	}
	action, _ := t["action"].(string)
	target, _ := t["target"].(string)
	outcome, _ := t["outcome"].(string)
	if outcome == "" {
		outcome = "info"
	}
	payload := "{}"
	if p, ok := t["payload"]; ok {
		if raw, err := json.Marshal(p); err == nil {
			payload = string(raw)
		}
	}
	db := openDB()
	defer db.Close()
	db.Exec("PRAGMA busy_timeout=5000")
	dbMu.Lock()
	defer dbMu.Unlock()
	ts := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	_, err := db.Exec(
		"INSERT INTO traces (id,ts,agent,session,kind,action,target,outcome,duration_ms,payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
		newID(), ts, agent, session, kind, action, target, outcome, nil, payload)
	if err != nil {
		return 500, map[string]any{"error": err.Error()}
	}
	return 201, map[string]any{"status": "ok", "ts": ts}
}

func handleTrace(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	body, _ := io.ReadAll(r.Body)
	code, resp := insertTrace(body)
	writeJSON(w, code, resp)
}

func newID() string {
	b := make([]byte, 16)
	rand.Read(b)
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

func handleTraces(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	limit := "100"
	if l := r.URL.Query().Get("limit"); l != "" {
		limit = l
	}
	rows, err := db.Query("SELECT id,ts,agent,session,kind,action,target,outcome,duration_ms,payload FROM traces ORDER BY ts DESC LIMIT " + limit)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	defer rows.Close()
	var out []map[string]any
	for rows.Next() {
		var id, ts, agent, session, kind, action, target, outcome, payload string
		var dur *int64
		rows.Scan(&id, &ts, &agent, &session, &kind, &action, &target, &outcome, &dur, &payload)
		out = append(out, map[string]any{
			"id": id, "ts": ts, "agent": agent, "session": session, "kind": kind,
			"action": action, "target": target, "outcome": outcome,
			"duration_ms": dur, "payload": payload,
		})
	}
	writeJSON(w, 200, map[string]any{"count": len(out), "traces": out})
}

func handleFindings(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	q := "SELECT id,created_ts,miner,confidence,title,evidence,suggestion,state,payload FROM findings"
	args := []any{}
	if s := r.URL.Query().Get("state"); s != "" {
		q += " WHERE state=?"
		args = append(args, s)
	}
	q += " ORDER BY confidence DESC LIMIT 100"
	rows, err := db.Query(q, args...)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	defer rows.Close()
	var out []map[string]any
	for rows.Next() {
		var id, created, miner, title, evidence, suggestion, state, payload string
		var conf float64
		rows.Scan(&id, &created, &miner, &conf, &title, &evidence, &suggestion, &state, &payload)
		out = append(out, map[string]any{
			"id": id, "created_ts": created, "miner": miner, "confidence": conf,
			"title": title, "evidence": evidence, "suggestion": suggestion,
			"state": state, "payload": payload,
		})
	}
	writeJSON(w, 200, map[string]any{"count": len(out), "findings": out})
}

func handleMine(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	out, err := exec.Command("python3", pythonCli, "cycle").CombinedOutput()
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": string(out[:min(len(out), 400)])})
		return
	}
	var result map[string]any
	json.Unmarshal(out, &result)
	writeJSON(w, 200, result)
}

func handleProvenance(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	chain, err := buildChain(db)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	ok, checked, why := reconcile(chain)
	if !ok {
		writeJSON(w, 409, map[string]any{
			"status": "diverged", "checked": checked, "reason": why,
		})
		return
	}
	writeJSON(w, 200, map[string]any{
		"count": len(chain), "pubkey": hex.EncodeToString(pubKey),
		"anchored": checked, "chain": chain,
	})
}

func handleProvenanceVerify(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	chain, err := buildChain(db)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	cryptoOK, _, cryptoWhy := verifyChain(chain)
	anchOK, anchored, anchorWhy := reconcile(chain)
	valid := cryptoOK && anchOK
	reason := cryptoWhy
	if !anchOK {
		reason = anchorWhy
	}
	writeJSON(w, 200, map[string]any{
		"valid": valid, "anchored": anchored, "reason": reason,
	})
}

func main() {
	if err := loadOrCreateKey(); err != nil {
		fmt.Fprintln(os.Stderr, "key init:", err)
		os.Exit(1)
	}
	http.HandleFunc("/api/status", handleStatus)
	http.HandleFunc("/api/trace", handleTrace)
	http.HandleFunc("/api/traces", handleTraces)
	http.HandleFunc("/api/findings", handleFindings)
	http.HandleFunc("/api/mine", handleMine)
	http.HandleFunc("/api/mine/wasm", handleMineWasm)
	http.HandleFunc("/api/provenance", handleProvenance)
	http.HandleFunc("/api/provenance/verify", handleProvenanceVerify)
	// Static: WebNN miner harness (served from 127.0.0.1 = secure context,
	// which WebNN requires; also same-origin with the API so no CORS).
	http.HandleFunc("/web/", func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path == "/web/" || path == "/web" {
			path = "/web/webnn_miner.html"
		}
		file := webDir + "/" + path[len("/web/"):]
		if !strings.HasPrefix(file, webDir) {
			http.NotFound(w, r)
			return
		}
		if _, err := os.Stat(file); err != nil {
			http.NotFound(w, r)
			return
		}
		http.ServeFile(w, r, file)
	})
	fmt.Println("mycelium gateway on", addr)
	go func() {
		if err := wtServe(); err != nil {
			fmt.Fprintln(os.Stderr, "webtransport:", err)
		}
	}()
	if err := http.ListenAndServe(addr, nil); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

var _ = strings.TrimSpace
