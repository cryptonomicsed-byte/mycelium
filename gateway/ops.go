// Mycelium gateway — ops/observability endpoints (work package Phase 1+2).
//
// Everything here reads the same substrate the rest of the gateway serves;
// no new storage, no new daemons. Endpoints:
//
//	GET  /api/agents           who writes to the substrate: GROUP BY agent
//	GET  /api/skills           generated-skills/ listing (the apply-loop's output)
//	GET  /api/alerts           evaluate generated alert configs (via cli.py alerts)
//	GET  /api/logs             tail of this gateway's own log ring
//	GET  /api/stats/timeseries bucketed trace/finding counts for the charts view
//	POST /api/prune            delete traces before a timestamp + re-anchor
//	GET  /api/picks            proxy to the VPS signal-fusion picks API
//
// Plus the request ring (last 200 requests, surfaced in /api/status's
// last_requests) and the log ring both live here.
package main

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	skillsDir = envOr("MYCELIUM_SKILLS_DIR", "/data/data/com.termux/files/home/mycelium/generated-skills")
	startTime = time.Now()
)

// ------------------------------------------------------------- log ring

type logLine struct {
	TS    string `json:"ts"`
	Level string `json:"level"`
	Msg   string `json:"msg"`
}

var (
	logMu   sync.Mutex
	logRing []logLine // bounded to logRingMax, oldest first
)

const logRingMax = 500

// gwLogf prints like fmt.Println always did AND records into the ring the
// /api/logs endpoint serves -- the Termux deployment has no journald to
// tail, so the dashboard's #/logs view is the only phone-side way to see
// what the gateway said after the launching shell is gone.
func gwLogf(level, format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	fmt.Println(msg)
	logMu.Lock()
	logRing = append(logRing, logLine{TS: time.Now().UTC().Format(time.RFC3339), Level: level, Msg: msg})
	if len(logRing) > logRingMax {
		logRing = logRing[len(logRing)-logRingMax:]
	}
	logMu.Unlock()
}

func handleLogs(w http.ResponseWriter, r *http.Request) {
	n := parseLimit(r.URL.Query().Get("lines"), 200, logRingMax)
	level := r.URL.Query().Get("level")
	logMu.Lock()
	lines := make([]logLine, 0, n)
	for i := len(logRing) - 1; i >= 0 && len(lines) < n; i-- {
		if level != "" && logRing[i].Level != level {
			continue
		}
		lines = append(lines, logRing[i])
	}
	logMu.Unlock()
	// reverse back to oldest-first for natural reading order
	for i, j := 0, len(lines)-1; i < j; i, j = i+1, j-1 {
		lines[i], lines[j] = lines[j], lines[i]
	}
	writeJSON(w, 200, map[string]any{"count": len(lines), "lines": lines})
}

// --------------------------------------------------------- request ring

type reqRecord struct {
	TS     string `json:"ts"`
	Method string `json:"method"`
	Path   string `json:"path"`
	Status int    `json:"status"`
	Ms     int64  `json:"ms"`
}

var (
	reqMu   sync.Mutex
	reqRing []reqRecord
)

const reqRingMax = 200

// statusRecorder captures the status code AND passes http.Flusher through --
// the SSE stream (stream.go) casts its ResponseWriter to Flusher, so a
// wrapper that hides Flush() would silently break live updates.
type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusRecorder) Flush() {
	if f, ok := s.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

// withRequestLog records every request into the ring the request inspector
// reads (/api/status last_requests). Static /web/ files are skipped -- one
// dashboard load is dozens of asset fetches that would evict every API
// request the inspector actually exists to show.
func withRequestLog(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/web/") {
			h.ServeHTTP(w, r)
			return
		}
		rec := &statusRecorder{ResponseWriter: w, status: 200}
		start := time.Now()
		h.ServeHTTP(rec, r)
		reqMu.Lock()
		reqRing = append(reqRing, reqRecord{
			TS: start.UTC().Format(time.RFC3339), Method: r.Method, Path: r.URL.Path,
			Status: rec.status, Ms: time.Since(start).Milliseconds(),
		})
		if len(reqRing) > reqRingMax {
			reqRing = reqRing[len(reqRing)-reqRingMax:]
		}
		reqMu.Unlock()
	})
}

func lastRequests(n int) []reqRecord {
	reqMu.Lock()
	defer reqMu.Unlock()
	if len(reqRing) < n {
		n = len(reqRing)
	}
	out := make([]reqRecord, n)
	copy(out, reqRing[len(reqRing)-n:])
	return out
}

// ------------------------------------------------------------ /api/agents

func handleAgents(w http.ResponseWriter, r *http.Request) {
	db := openDB()
	defer db.Close()
	rows, err := db.Query(
		`SELECT agent, COUNT(*), MAX(ts),
		        SUM(CASE WHEN outcome='failure' THEN 1 ELSE 0 END)
		   FROM traces GROUP BY agent ORDER BY COUNT(*) DESC`)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	type agentRow struct {
		Name       string           `json:"name"`
		TraceCount int64            `json:"trace_count"`
		LastSeen   string           `json:"last_seen"`
		ErrorRate  float64          `json:"error_rate"`
		Kinds      map[string]int64 `json:"kinds"`
	}
	var agents []agentRow
	index := map[string]int{}
	for rows.Next() {
		var a agentRow
		var failures int64
		if rows.Scan(&a.Name, &a.TraceCount, &a.LastSeen, &failures) != nil {
			continue
		}
		if a.TraceCount > 0 {
			a.ErrorRate = float64(failures) / float64(a.TraceCount)
		}
		a.Kinds = map[string]int64{}
		index[a.Name] = len(agents)
		agents = append(agents, a)
	}
	rows.Close()

	kindRows, err := db.Query(`SELECT agent, kind, COUNT(*) FROM traces GROUP BY agent, kind`)
	if err == nil {
		for kindRows.Next() {
			var agent, kind string
			var n int64
			if kindRows.Scan(&agent, &kind, &n) != nil {
				continue
			}
			if i, ok := index[agent]; ok {
				agents[i].Kinds[kind] = n
			}
		}
		kindRows.Close()
	}
	if agents == nil {
		agents = []agentRow{}
	}
	writeJSON(w, 200, map[string]any{"count": len(agents), "agents": agents})
}

// ------------------------------------------------------------ /api/skills

// handleSkills lists what the apply-loop has actually produced: every
// generated-skills/<slug>/SKILL.md, with the slug doubling as the join key
// back to the applied finding whose payload carried it (apply.py names the
// directory from the finding's slug).
func handleSkills(w http.ResponseWriter, r *http.Request) {
	entries, err := os.ReadDir(skillsDir)
	if err != nil {
		// A missing directory just means nothing has been applied yet --
		// that's an empty list, not a server error.
		writeJSON(w, 200, map[string]any{"count": 0, "skills": []any{}})
		return
	}
	type skill struct {
		Name  string `json:"name"`
		Path  string `json:"path"`
		Mtime string `json:"mtime"`
		Size  int64  `json:"size"`
	}
	var skills []skill
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		p := filepath.Join(skillsDir, e.Name(), "SKILL.md")
		info, err := os.Stat(p)
		if err != nil {
			continue
		}
		skills = append(skills, skill{
			Name: e.Name(), Path: p,
			Mtime: info.ModTime().UTC().Format(time.RFC3339), Size: info.Size(),
		})
	}
	sort.Slice(skills, func(i, j int) bool { return skills[i].Mtime > skills[j].Mtime })
	if skills == nil {
		skills = []skill{}
	}
	writeJSON(w, 200, map[string]any{"count": len(skills), "skills": skills})
}

// ------------------------------------------------------------ /api/alerts

// handleAlerts evaluates the generated alert configs against the current
// substrate by shelling out to `cli.py alerts` -- the same
// subprocess-to-Python pattern handleMine uses, because the alert
// evaluation logic lives in Python (cli.cmd_alerts) and duplicating it in
// Go would immediately drift.
func handleAlerts(w http.ResponseWriter, r *http.Request) {
	out, err := exec.Command("python3", pythonCli, "alerts").CombinedOutput()
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": string(out[:min(len(out), 400)])})
		return
	}
	var result map[string]any
	if json.Unmarshal(out, &result) != nil {
		writeJSON(w, 502, map[string]any{"error": "unparseable alerts output"})
		return
	}
	writeJSON(w, 200, result)
}

// -------------------------------------------------- /api/stats/timeseries

// handleStatsTimeseries buckets traces (by kind) and findings (by state)
// for the charts view: hourly buckets over 24h, daily over 7d/30d. Buckets
// come from substr() over the ISO timestamps the substrate already stores
// ("2026-08-18T18:41:30Z" -> "2026-08-18T18" hourly, "2026-08-18" daily),
// so no timestamp parsing happens in SQL at all.
func handleStatsTimeseries(w http.ResponseWriter, r *http.Request) {
	rng := r.URL.Query().Get("range")
	var since time.Time
	var bucketLen int // prefix length of the ISO timestamp that names a bucket
	switch rng {
	case "7d":
		since, bucketLen = time.Now().UTC().AddDate(0, 0, -7), 10
	case "30d":
		since, bucketLen = time.Now().UTC().AddDate(0, 0, -30), 10
	default:
		rng = "24h"
		since, bucketLen = time.Now().UTC().Add(-24*time.Hour), 13
	}
	sinceStr := since.Format(time.RFC3339)

	db := openDB()
	defer db.Close()

	type bucket struct {
		TS       string           `json:"ts"`
		Traces   map[string]int64 `json:"traces_by_kind"`
		Total    int64            `json:"traces_total"`
		Findings map[string]int64 `json:"findings_by_state"`
		MineRuns int64            `json:"mine_runs"`
	}
	buckets := map[string]*bucket{}
	get := func(key string) *bucket {
		b, ok := buckets[key]
		if !ok {
			b = &bucket{TS: key, Traces: map[string]int64{}, Findings: map[string]int64{}}
			buckets[key] = b
		}
		return b
	}

	rows, err := db.Query(
		fmt.Sprintf(`SELECT substr(ts,1,%d), kind, COUNT(*) FROM traces WHERE ts >= ? GROUP BY 1, 2`, bucketLen),
		sinceStr)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	for rows.Next() {
		var key, kind string
		var n int64
		if rows.Scan(&key, &kind, &n) != nil {
			continue
		}
		b := get(key)
		b.Traces[kind] += n
		b.Total += n
	}
	rows.Close()

	fRows, err := db.Query(
		fmt.Sprintf(`SELECT substr(created_ts,1,%d), state, COUNT(*) FROM findings WHERE created_ts >= ? GROUP BY 1, 2`, bucketLen),
		sinceStr)
	if err == nil {
		for fRows.Next() {
			var key, state string
			var n int64
			if fRows.Scan(&key, &state, &n) != nil {
				continue
			}
			get(key).Findings[state] += n
		}
		fRows.Close()
	}

	// "a mine run happened in this bucket" = a tool_call trace whose action
	// mentions mining (force_mine, force_mine_wasm, mine, cycle emit these).
	mRows, err := db.Query(
		fmt.Sprintf(`SELECT substr(ts,1,%d), COUNT(*) FROM traces
		              WHERE ts >= ? AND kind='tool_call' AND action LIKE '%%mine%%' GROUP BY 1`, bucketLen),
		sinceStr)
	if err == nil {
		for mRows.Next() {
			var key string
			var n int64
			if mRows.Scan(&key, &n) != nil {
				continue
			}
			get(key).MineRuns = n
		}
		mRows.Close()
	}

	keys := make([]string, 0, len(buckets))
	for k := range buckets {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]*bucket, 0, len(keys))
	for _, k := range keys {
		out = append(out, buckets[k])
	}
	writeJSON(w, 200, map[string]any{"range": rng, "count": len(out), "buckets": out})
}

// ------------------------------------------------------------- /api/prune

// handlePrune deletes traces older than before_ts and RE-ANCHORS the
// provenance chain: the anchor log is derived index-by-index from the trace
// table, so deleting early rows shifts every index and the old anchors
// would (correctly!) read as tamper. An operator-initiated prune is a
// legitimate administrative action, so the anchor log is atomically
// rewritten from the post-prune chain -- and the prune itself is recorded
// as a trace first, so the action is part of the new chain rather than
// invisible.
func handlePrune(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"error": "POST only"})
		return
	}
	var req struct {
		BeforeTS string `json:"before_ts"`
	}
	body, _ := io.ReadAll(io.LimitReader(r.Body, 4096))
	if json.Unmarshal(body, &req) != nil || req.BeforeTS == "" {
		writeJSON(w, 400, map[string]any{"error": "body must be {\"before_ts\": \"<ISO8601>\"}"})
		return
	}

	dbMu.Lock()
	defer dbMu.Unlock()
	db := openDB()
	defer db.Close()

	res, err := db.Exec("DELETE FROM traces WHERE ts < ?", req.BeforeTS)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	deleted, _ := res.RowsAffected()

	// Record the prune in the substrate itself (this row joins the new
	// chain below). insertTrace would re-take dbMu -- insert directly.
	payload, _ := json.Marshal(map[string]any{"before_ts": req.BeforeTS, "deleted": deleted})
	db.Exec(`INSERT INTO traces (id, ts, agent, session, kind, action, target, outcome, payload)
	         VALUES (?, ?, 'gateway', 'ops', 'tool_call', 'prune_traces', 'substrate', 'success', ?)`,
		fmt.Sprintf("prune-%d", time.Now().UnixNano()), time.Now().UTC().Format(time.RFC3339), string(payload))

	chain, err := buildChain(db)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "chain rebuild: " + err.Error()})
		return
	}
	tmp := statePath + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "anchor rewrite: " + err.Error()})
		return
	}
	for _, e := range chain {
		raw, _ := json.Marshal(Recorded{Index: e.Index, TraceID: e.TraceID, Hash: e.Hash, Sig: e.Sig})
		f.Write(append(raw, '\n'))
	}
	f.Close()
	if err := os.Rename(tmp, statePath); err != nil {
		writeJSON(w, 500, map[string]any{"error": "anchor swap: " + err.Error()})
		return
	}
	gwLogf("info", "pruned %d traces before %s, re-anchored %d envelopes", deleted, req.BeforeTS, len(chain))
	writeJSON(w, 200, map[string]any{"deleted": deleted, "reanchored": len(chain)})
}

// ------------------------------------------------------------- /api/picks

// handlePicksProxy forwards /api/picks to the VPS signal-fusion API using
// the exact same key-forwarding pattern as handleCouncilProxy -- the picks
// table lives beside the council on the VPS (signal_fusion/ writes it), and
// the browser needs it same-origin.
func handlePicksProxy(w http.ResponseWriter, r *http.Request) {
	key := ""
	if b, err := os.ReadFile(councilKey); err == nil {
		key = strings.TrimSpace(string(b))
	}
	target := picksBase + "/api/picks"
	if r.URL.RawQuery != "" {
		target += "?" + r.URL.RawQuery
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, target, nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": "picks proxy build: " + err.Error()})
		return
	}
	if key != "" {
		req.Header.Set("X-Agent-Key", key)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, 502, map[string]any{"error": "picks API unreachable: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(body)
}

// ------------------------------------------------------- status extension

// opsStatusExtras adds the work-package /api/status fields: uptime, storage
// stats, auth mode, and the request-inspector ring. Called from
// handleStatus (main.go) so /api/status stays the one status endpoint.
func opsStatusExtras(db *sql.DB, m map[string]any) {
	m["uptime_secs"] = int64(time.Since(startTime).Seconds())
	m["auth_enabled"] = authEnabled

	storage := map[string]any{}
	if info, err := os.Stat(dbPath); err == nil {
		storage["db_bytes"] = info.Size()
	}
	if info, err := os.Stat(dbPath + "-wal"); err == nil {
		storage["wal_bytes"] = info.Size()
	}
	var oldest, newest string
	db.QueryRow("SELECT COALESCE(MIN(ts),''), COALESCE(MAX(ts),'') FROM traces").Scan(&oldest, &newest)
	storage["oldest_ts"] = oldest
	storage["newest_ts"] = newest
	m["storage"] = storage
	m["last_requests"] = lastRequests(50)
}
