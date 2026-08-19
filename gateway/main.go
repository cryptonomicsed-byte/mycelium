// Mycelium gateway — v0.2 polyglot layer (Go).
//
// Serves the substrate over HTTP for any agent (curl / REST) and maintains
// an Ed25519-signed hash chain over every trace (tamper-evident provenance).
// Reads/writes the same SQLite file as the Python core (modernc.org/sqlite,
// pure Go — no cgo). Mining still runs in the Python sandbox (subprocess),
// because that's where the sandboxing lives; the gateway is the transport.
//
// Endpoints:
//
//	GET  /api/status                    health + counts
//	POST /api/trace                     emit a trace (JSON body)
//	GET  /api/traces?limit=N&agent=&kind=&action=&outcome=&session=&since=
//	                                     query traces
//	GET  /api/findings?state=&miner=&since=&limit=N
//	                                     list findings
//	POST /api/findings/{id}/apply       apply a finding (skill/alert/config_fix)
//	POST /api/findings/{id}/dismiss     dismiss an open finding
//	GET  /api/miners                    per-miner stats, zero-finding miners included
//	POST /api/mine                      run the sandboxed mining cycle
//	POST /api/mine/wasm                 run the Wasm-sandboxed miner
//	GET  /api/provenance                full signed chain
//	GET  /api/provenance/verify         re-verify chain integrity
//	GET  /api/stream                    SSE: trace/finding/provenance/heartbeat events
//	GET  /api/webtransport/cert-hash    current WT cert's SHA-256 + expiry, for pinning
//	POST /api/auth/register/{begin,finish}  pair a device (WebAuthn) -- only when
//	                                         MYCELIUM_GATEWAY_AUTH=1, see auth.go
//	POST /api/auth/login/{begin,finish}     sign in with a paired device
//	POST /api/auth/logout                   clear the current session
package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// Env-overridable rather than const so gateway/main_test.go can point every
// path at a temp directory instead of the real Termux install -- mirrors the
// MYCELIUM_DB env-var pattern mycelium/core.py already uses. Real deployment
// is unaffected: each default is exactly the value this used to be a const.
//
// addr's default host is "localhost", not "127.0.0.1" -- an IP-literal
// hostname can't be a WebAuthn RP ID (Chrome rejects it outright), and
// mycelium.dashboard_url() (mcp_server.py) derives its URL from this same
// var via MYCELIUM_ADDR, so the two need to agree on one shared default
// rather than drift into two independently-chosen ones. addr is what gets
// advertised (logged, used in URLs) -- bindAddr is what ListenAndServe
// actually binds, kept as the literal loopback IP so listening behavior is
// unaffected by however "localhost" happens to resolve on a given system
// (dual-stack resolvers can prefer ::1, which is still loopback-only but a
// needless behavior change from what this bound before).
var (
	addr      = envOr("MYCELIUM_ADDR", "localhost:8811")
	bindAddr  = envOr("MYCELIUM_BIND_ADDR", "127.0.0.1:8811")
	dbPath    = envOr("MYCELIUM_DB", "/data/data/com.termux/files/home/mycelium/mycelium.db")
	keyPath   = envOr("MYCELIUM_PROVENANCE_KEY", "/data/data/com.termux/files/home/mycelium/gateway/provenance_key.json")
	statePath = envOr("MYCELIUM_CHAIN_STATE", "/data/data/com.termux/files/home/mycelium/gateway/chain_state.jsonl")
	pythonCli = envOr("MYCELIUM_CLI", "/data/data/com.termux/files/home/mycelium/mycelium/cli.py")
	webDir    = envOr("MYCELIUM_WEB_DIR", "/data/data/com.termux/files/home/mycelium/web")
	// Council proxy: the Ares Council verdicts/calibration live on the VPS
	// Vantage API. The dashboard (served from this gateway) needs them
	// same-origin, so /api/council/* proxies to the VPS with the agent key
	// from the local .vantage_key file (or MYCELIUM_COUNCIL_BASE for dev).
	councilBase = envOr("MYCELIUM_COUNCIL_BASE", "http://2.25.70.156:8001")
	councilKey  = envOr("MYCELIUM_COUNCIL_KEY", "/data/data/com.termux/files/home/.vantage_key")
	// Picks proxy: the ares-signal-fusion sidecar (picks_server.py) serves
	// /api/picks from ares_picks.db on the VPS :8003. Default points there.
	picksBase   = envOr("MYCELIUM_PICKS_BASE", "http://2.25.70.156:8003")
)

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

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
	Index    int64  `json:"index"`
	TraceID  string `json:"trace_id"`
	TS       string `json:"ts"`
	Action   string `json:"action"`
	Target   string `json:"target"`
	Outcome  string `json:"outcome"`
	Payload  string `json:"payload_sha"`
	PrevHash string `json:"prev_hash"`
	Hash     string `json:"hash"`
	Sig      string `json:"sig"`
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
	m := map[string]any{
		"status": "ok", "traces": traces, "findings": findings,
		"pubkey": hex.EncodeToString(pubKey),
	}
	opsStatusExtras(db, m) // uptime, storage, auth mode, request ring (ops.go)
	writeJSON(w, 200, m)
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

// parseLimit clamps to [1, maxLimit], defaulting to def on anything unparsable
// or non-positive -- a query-string field is untrusted input regardless of
// whether it ever reaches raw SQL.
func parseLimit(raw string, def, maxLimit int) int {
	if raw == "" {
		return def
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n <= 0 {
		return def
	}
	if n > maxLimit {
		return maxLimit
	}
	return n
}

func handleTraces(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	q := r.URL.Query()
	limit := parseLimit(q.Get("limit"), 100, 5000)

	sqlq := "SELECT id,ts,agent,session,kind,action,target,outcome,duration_ms,payload FROM traces WHERE 1=1"
	var args []any
	// Mirrors mycelium/core.py's query_traces filter set (agent/kind/action/
	// outcome/session), which the Python CLI/MCP surface already supports --
	// this endpoint only had `limit` until now.
	for _, f := range []struct{ col, val string }{
		{"agent", q.Get("agent")}, {"kind", q.Get("kind")},
		{"action", q.Get("action")}, {"outcome", q.Get("outcome")},
		{"session", q.Get("session")},
	} {
		if f.val != "" {
			sqlq += " AND " + f.col + "=?"
			args = append(args, f.val)
		}
	}
	if since := q.Get("since"); since != "" {
		sqlq += " AND ts > ?"
		args = append(args, since)
	}
	// `before` is the backward cursor `since` isn't: the Trace Explorer
	// pages OLDER by re-querying with everything strictly before the oldest
	// row it already holds.
	if before := q.Get("before"); before != "" {
		sqlq += " AND ts < ?"
		args = append(args, before)
	}
	sqlq += " ORDER BY ts DESC LIMIT ?"
	args = append(args, limit)

	rows, err := db.Query(sqlq, args...)
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
	qs := r.URL.Query()
	limit := parseLimit(qs.Get("limit"), 100, 5000)

	sqlq := "SELECT id,created_ts,miner,confidence,title,evidence,suggestion,state,payload FROM findings WHERE 1=1"
	var args []any
	if s := qs.Get("state"); s != "" {
		sqlq += " AND state=?"
		args = append(args, s)
	}
	if m := qs.Get("miner"); m != "" {
		sqlq += " AND miner=?"
		args = append(args, m)
	}
	if since := qs.Get("since"); since != "" {
		sqlq += " AND created_ts > ?"
		args = append(args, since)
	}
	sqlq += " ORDER BY confidence DESC LIMIT ?"
	args = append(args, limit)
	rows, err := db.Query(sqlq, args...)
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

// findingIDFromPath extracts the {id} from "/api/findings/{id}/{action}",
// mirroring the manual prefix-trimming style already used for /web/ rather
// than switching to net/http's pattern routing for just these two routes.
func findingIDFromPath(path, suffix string) (string, bool) {
	const prefix = "/api/findings/"
	if !strings.HasPrefix(path, prefix) || !strings.HasSuffix(path, suffix) {
		return "", false
	}
	id := strings.TrimSuffix(strings.TrimPrefix(path, prefix), suffix)
	if id == "" || strings.Contains(id, "/") {
		return "", false
	}
	return id, true
}

// handleFindingApply runs `cli.py apply <id>` and maps its result to a real
// HTTP status. cmd_apply (mycelium/cli.py) prints structured JSON on every
// path -- including its error exit(1) branches -- so unlike handleMine this
// must parse stdout regardless of exit code rather than treating any
// non-zero exit as a blanket 502.
func handleFindingApply(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	id, ok := findingIDFromPath(r.URL.Path, "/apply")
	if !ok {
		writeJSON(w, 400, map[string]any{"error": "bad path, expected /api/findings/{id}/apply"})
		return
	}
	out, _ := exec.Command("python3", pythonCli, "apply", id).CombinedOutput()
	var result map[string]any
	if err := json.Unmarshal(bytes.TrimSpace(lastLine(out)), &result); err != nil {
		writeJSON(w, 502, map[string]any{"error": "apply produced no parseable JSON", "raw": string(out[:min(len(out), 400)])})
		return
	}
	if result["status"] == "applied" {
		writeJSON(w, 200, result)
		return
	}
	if msg, ok := result["message"].(string); ok {
		writeJSON(w, 404, map[string]any{"error": msg})
		return
	}
	errMsg, _ := result["error"].(string)
	switch {
	case strings.Contains(errMsg, "already"):
		writeJSON(w, 409, map[string]any{"error": errMsg})
	case strings.Contains(errMsg, "not wired"):
		writeJSON(w, 422, map[string]any{"error": errMsg})
	case errMsg != "":
		writeJSON(w, 500, map[string]any{"error": errMsg})
	default:
		writeJSON(w, 502, map[string]any{"error": "unrecognized apply result", "result": result})
	}
}

// lastLine returns the final non-empty line of b -- cli.py's _p() pretty-
// prints with json.dumps(indent=2), so stdout is multi-line JSON, not NDJSON;
// this take the whole trailing JSON blob starting at its first '{' instead of
// assuming a single line.
func lastLine(b []byte) []byte {
	if i := bytes.IndexByte(b, '{'); i >= 0 {
		return b[i:]
	}
	return b
}

// handleFindingDismiss needs no Python round-trip -- core.set_finding_state
// already supports the "dismissed" state, nothing previously called it with
// that value (no dismiss path exists in mcp_server.py's tools or cli.py's
// subparsers today). Only an open finding can be dismissed, same as apply.
func handleFindingDismiss(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	id, ok := findingIDFromPath(r.URL.Path, "/dismiss")
	if !ok {
		writeJSON(w, 400, map[string]any{"error": "bad path, expected /api/findings/{id}/dismiss"})
		return
	}
	db := openDB()
	defer db.Close()
	res, err := db.Exec("UPDATE findings SET state='dismissed' WHERE id=? AND state='open'", id)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		var exists bool
		db.QueryRow("SELECT EXISTS(SELECT 1 FROM findings WHERE id=?)", id).Scan(&exists)
		if !exists {
			writeJSON(w, 404, map[string]any{"error": "finding not found"})
			return
		}
		writeJSON(w, 409, map[string]any{"error": "finding is not open"})
		return
	}
	writeJSON(w, 200, map[string]any{"status": "dismissed", "id": id})
}

// handleFindingAction routes POST /api/findings/{id}/apply and .../dismiss
// -- registered as a single prefix handler (see main()) since only two
// suffixes exist; adding net/http pattern routing for just these two would
// be a second routing style alongside the rest of this file's plain
// HandleFunc + manual path handling.
func handleFindingAction(w http.ResponseWriter, r *http.Request) {
	switch {
	case strings.HasSuffix(r.URL.Path, "/apply"):
		handleFindingApply(w, r)
	case strings.HasSuffix(r.URL.Path, "/dismiss"):
		handleFindingDismiss(w, r)
	default:
		http.NotFound(w, r)
	}
}

// handleMiners reports per-miner stats merged with the full known registry
// (mycelium/miners.py's MINERS dict, hand-copied below since Go cannot
// introspect the Python registry -- keep this list in sync by hand if a
// miner is added or removed there) so a miner with zero findings yet still
// appears rather than silently vanishing from the panel.
var knownMiners = []string{
	"recurring_workflow", "anomaly", "cross_agent", "opportunity",
	"wallet_activity", "wallet_correlation", "wallet_anomaly",
}

func handleMiners(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	rows, err := db.Query(
		`SELECT miner, COUNT(*), MAX(created_ts), AVG(confidence)
		   FROM findings GROUP BY miner`)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	stats := map[string]map[string]any{}
	for rows.Next() {
		var miner, lastTS string
		var count int64
		var avgConf float64
		if err := rows.Scan(&miner, &count, &lastTS, &avgConf); err != nil {
			continue
		}
		stats[miner] = map[string]any{
			"miner": miner, "findings": count,
			"last_finding_ts": lastTS, "avg_confidence": avgConf,
			"by_state": map[string]int64{},
		}
	}
	rows.Close()

	// Per-state split (open/applied/dismissed) so the Miners view can show
	// what each miner's findings actually became, not just how many exist.
	stateRows, err := db.Query(`SELECT miner, state, COUNT(*) FROM findings GROUP BY miner, state`)
	if err == nil {
		for stateRows.Next() {
			var miner, state string
			var n int64
			if stateRows.Scan(&miner, &state, &n) != nil {
				continue
			}
			if s, ok := stats[miner]; ok {
				s["by_state"].(map[string]int64)[state] = n
			}
		}
		stateRows.Close()
	}

	out := make([]map[string]any, 0, len(knownMiners))
	for _, name := range knownMiners {
		if s, ok := stats[name]; ok {
			out = append(out, s)
			continue
		}
		out = append(out, map[string]any{
			"miner": name, "findings": int64(0),
			"last_finding_ts": nil, "avg_confidence": nil,
			"by_state": map[string]int64{},
		})
	}
	writeJSON(w, 200, map[string]any{"count": len(out), "miners": out})
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

// handleWebTransportCertHash exposes the current WebTransport cert's
// SHA-256 (base64, standard alphabet) + expiry so a browser client can pin
// it via serverCertificateHashes before calling new WebTransport(...) --
// see wt.go's header comment for why this has to be fetched fresh
// immediately before each connection attempt rather than cached: the cert
// rotates in place (wtCertRotationLoop) and pinning a stale hash would fail
// a new connection outright, even though any already-open session is
// unaffected by rotation.
func handleWebTransportCertHash(w http.ResponseWriter, r *http.Request) {
	sum, until, ok := currentWTCertHash()
	if !ok {
		writeJSON(w, 503, map[string]any{"error": "webtransport not ready yet"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"hash":    base64.StdEncoding.EncodeToString(sum),
		"expires": until.Format(time.RFC3339),
	})
}

// handleCouncilProxy proxies /api/council/* to the Ares Council API on the
// VPS (Vantage :8001). The dashboard is served from this gateway, so council
// verdicts/calibration need a same-origin path — the browser can't hit the
// VPS directly (different origin + needs the agent key). We hold the key
// locally (MYCELIUM_COUNCIL_KEY, default ~/.vantage_key) and forward.
func handleCouncilProxy(w http.ResponseWriter, r *http.Request) {
	sub := strings.TrimPrefix(r.URL.Path, "/api/council")
	if sub == "" || sub == "/" {
		writeJSON(w, 400, map[string]any{"error": "council endpoint required: /api/council/{overview,verdicts,calibration,substrate}"})
		return
	}
	key := ""
	if b, err := os.ReadFile(councilKey); err == nil {
		key = strings.TrimSpace(string(b))
	}
	target := councilBase + "/api/council" + sub
	if r.URL.RawQuery != "" {
		target += "?" + r.URL.RawQuery
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, target, nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "council proxy build: " + err.Error()})
		return
	}
	if key != "" {
		req.Header.Set("X-Agent-Key", key)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": "council unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
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
	http.HandleFunc("/api/findings/", handleFindingAction)
	http.HandleFunc("/api/miners", handleMiners)
	http.HandleFunc("/api/mine", handleMine)
	http.HandleFunc("/api/mine/wasm", handleMineWasm)
	http.HandleFunc("/api/provenance", handleProvenance)
	http.HandleFunc("/api/provenance/verify", handleProvenanceVerify)
	http.HandleFunc("/api/stream", handleStream)
	http.HandleFunc("/api/webtransport/cert-hash", handleWebTransportCertHash)
	http.HandleFunc("/api/council/", handleCouncilProxy)
	// Ops/observability endpoints (ops.go) -- work package Phase 1+2.
	http.HandleFunc("/api/agents", handleAgents)
	http.HandleFunc("/api/skills", handleSkills)
	http.HandleFunc("/api/alerts", handleAlerts)
	http.HandleFunc("/api/logs", handleLogs)
	http.HandleFunc("/api/stats/timeseries", handleStatsTimeseries)
	http.HandleFunc("/api/prune", handlePrune)
	http.HandleFunc("/api/picks", handlePicksProxy)
	// Static: WebNN miner harness (served from 127.0.0.1 = secure context,
	// which WebNN requires; also same-origin with the API so no CORS).
	http.HandleFunc("/web/", func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if path == "/web/" || path == "/web" {
			// The dashboard (web/dashboard/) is the landing surface now, not
			// the WebNN utility page -- that page is unaffected and still
			// reachable at its own URL, /web/webnn_miner.html.
			path = "/web/dashboard/index.html"
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
	if authEnabled {
		if err := registerAuthRoutes(); err != nil {
			fmt.Fprintln(os.Stderr, "webauthn init:", err)
			os.Exit(1)
		}
		gwLogf("info", "mycelium gateway: auth enabled (MYCELIUM_GATEWAY_AUTH), pair a device at http://%s/web/", addr)
	}

	gwLogf("info", "mycelium gateway on %s (reachable at http://%s)", bindAddr, addr)
	go func() {
		if err := wtServe(); err != nil {
			gwLogf("error", "webtransport: %v", err)
		}
	}()
	// CORS wraps outermost so its OPTIONS preflight short-circuit (see
	// withDevCORS) always fires before withAuth gets a chance to see the
	// request -- a preflight carries no cookies, so if auth ran first it
	// would 401 every preflight whenever both flags are enabled together.
	// withRequestLog sits between the two so the inspector ring records
	// auth 401s too, but never the preflights CORS already swallowed.
	if err := http.ListenAndServe(bindAddr, withDevCORS(withRequestLog(withAuth(http.DefaultServeMux)))); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

// withDevCORS adds permissive CORS headers, but only when explicitly opted
// into via MYCELIUM_GATEWAY_DEV_CORS -- the default deployment trusts
// same-origin loopback (dashboard served from this gateway's own /web/), and
// this stays off so that trust model doesn't silently widen. It exists for
// iterating on the frontend from a separate dev server without redeploying
// into webDir on every change.
func withDevCORS(h http.Handler) http.Handler {
	if os.Getenv("MYCELIUM_GATEWAY_DEV_CORS") == "" {
		return h
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		h.ServeHTTP(w, r)
	})
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

var _ = strings.TrimSpace
