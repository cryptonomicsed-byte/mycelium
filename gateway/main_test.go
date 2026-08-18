// Tests for the dashboard-facing additions to the gateway: filtered
// traces/findings, apply/dismiss, /api/miners, and the SSE stream.
//
// Enabled by the dbPath/keyPath/statePath/pythonCli/webDir const->var change:
// each test points every path at its own temp dir rather than the real
// Termux install, via direct package-var reassignment (same-package tests
// can do this; it sidesteps the ordering problem of env vars, which are
// only read once, at package-var-initialization time, before any test runs).
package main

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// substrateDDL mirrors mycelium/core.py's init_db() schema exactly. The Go
// gateway has never owned schema creation (that's the Python side's job in
// real deployment), so tests recreate it directly against a temp SQLite file.
const substrateDDL = `
CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY, ts TEXT NOT NULL, agent TEXT NOT NULL, session TEXT NOT NULL,
    kind TEXT NOT NULL, action TEXT, target TEXT, outcome TEXT,
    duration_ms INTEGER, payload TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY, created_ts TEXT NOT NULL, miner TEXT NOT NULL,
    confidence REAL NOT NULL, title TEXT NOT NULL, evidence TEXT NOT NULL,
    suggestion TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', payload TEXT
);`

// newTestGateway points every gateway path at a fresh temp dir and returns a
// ready-to-query *sql.DB. pythonCli is pointed at the REAL mycelium/cli.py
// (repo-relative ../mycelium/cli.py from gateway/) rather than a fake, since
// the exact JSON-on-every-exit-path behaviour handleFindingApply depends on
// is real Python behaviour worth testing against directly, not assuming.
func newTestGateway(t *testing.T) *sql.DB {
	t.Helper()
	dir := t.TempDir()
	dbPath = filepath.Join(dir, "test.db")
	keyPath = filepath.Join(dir, "provenance_key.json")
	statePath = filepath.Join(dir, "chain_state.jsonl")
	webDir = dir

	repoRoot, err := filepath.Abs("../mycelium")
	if err != nil {
		t.Fatalf("resolve repo root: %v", err)
	}
	pythonCli = filepath.Join(repoRoot, "cli.py")

	if err := loadOrCreateKey(); err != nil {
		t.Fatalf("loadOrCreateKey: %v", err)
	}

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		t.Fatalf("open test db: %v", err)
	}
	// The gateway's own handlers each open their own *sql.DB via openDB(),
	// so a test writing through this handle races those on the same file.
	// SQLite's file locking covers that, but a bare Go pool spreads a single
	// *sql.DB across multiple underlying connections and PRAGMAs only bind
	// to the connection that ran them -- capping this handle at one
	// connection makes busy_timeout apply consistently and keeps this
	// handle's own writes serialized, matching production's dbMu intent.
	db.SetMaxOpenConns(1)
	if _, err := db.Exec("PRAGMA busy_timeout=5000"); err != nil {
		t.Fatalf("set busy_timeout: %v", err)
	}
	if _, err := db.Exec(substrateDDL); err != nil {
		t.Fatalf("create schema: %v", err)
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func insertTestTrace(t *testing.T, db *sql.DB, id, ts, agent, kind, action, outcome string) {
	t.Helper()
	_, err := db.Exec(
		`INSERT INTO traces (id,ts,agent,session,kind,action,target,outcome,duration_ms,payload)
		 VALUES (?,?,?,?,?,?,?,?,?,?)`,
		id, ts, agent, "s1", kind, action, "target", outcome, nil, "{}")
	if err != nil {
		t.Fatalf("insert trace %s: %v", id, err)
	}
}

func insertTestFinding(t *testing.T, db *sql.DB, id, ts, miner, state string) {
	t.Helper()
	_, err := db.Exec(
		`INSERT INTO findings (id,created_ts,miner,confidence,title,evidence,suggestion,state,payload)
		 VALUES (?,?,?,?,?,?,?,?,?)`,
		id, ts, miner, 0.8, "title-"+id, "evidence", "alert", state, "{}")
	if err != nil {
		t.Fatalf("insert finding %s: %v", id, err)
	}
}

func decodeBody(t *testing.T, rec *httptest.ResponseRecorder) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode body %q: %v", rec.Body.String(), err)
	}
	return out
}

// ---------------------------------------------------------------- /api/traces

func TestParseLimit(t *testing.T) {
	cases := []struct {
		raw      string
		def, max int
		want     int
	}{
		{"", 100, 5000, 100},
		{"50", 100, 5000, 50},
		{"999999", 100, 5000, 5000}, // clamped to max
		{"0", 100, 5000, 100},       // non-positive -> default
		{"-5", 100, 5000, 100},
		{"not-a-number", 100, 5000, 100},
	}
	for _, c := range cases {
		if got := parseLimit(c.raw, c.def, c.max); got != c.want {
			t.Errorf("parseLimit(%q, %d, %d) = %d, want %d", c.raw, c.def, c.max, got, c.want)
		}
	}
}

func TestHandleTraces_Filters(t *testing.T) {
	db := newTestGateway(t)
	insertTestTrace(t, db, "t1", "2026-01-01T00:00:00Z", "agent-a", "tool_call", "patch", "success")
	insertTestTrace(t, db, "t2", "2026-01-01T00:00:01Z", "agent-a", "tool_call", "grep", "failure")
	insertTestTrace(t, db, "t3", "2026-01-01T00:00:02Z", "agent-b", "tool_call", "patch", "success")

	req := httptest.NewRequest("GET", "/api/traces?agent=agent-a&outcome=success", nil)
	rec := httptest.NewRecorder()
	handleTraces(rec, req)
	body := decodeBody(t, rec)

	traces, _ := body["traces"].([]any)
	if len(traces) != 1 {
		t.Fatalf("expected exactly 1 trace matching agent=agent-a&outcome=success, got %d: %v", len(traces), traces)
	}
	row := traces[0].(map[string]any)
	if row["id"] != "t1" {
		t.Errorf("expected trace t1, got %v", row["id"])
	}
}

func TestHandleTraces_SinceFilter(t *testing.T) {
	db := newTestGateway(t)
	insertTestTrace(t, db, "t1", "2026-01-01T00:00:00Z", "a", "tool_call", "x", "success")
	insertTestTrace(t, db, "t2", "2026-01-01T00:00:05Z", "a", "tool_call", "x", "success")

	req := httptest.NewRequest("GET", "/api/traces?since=2026-01-01T00:00:02Z", nil)
	rec := httptest.NewRecorder()
	handleTraces(rec, req)
	body := decodeBody(t, rec)

	if body["count"].(float64) != 1 {
		t.Fatalf("expected 1 trace after the since watermark, got %v", body["count"])
	}
}

// --------------------------------------------------------------- /api/findings

func TestHandleFindings_MinerFilter(t *testing.T) {
	db := newTestGateway(t)
	insertTestFinding(t, db, "f1", "2026-01-01T00:00:00Z", "anomaly", "open")
	insertTestFinding(t, db, "f2", "2026-01-01T00:00:01Z", "wallet_activity", "open")

	req := httptest.NewRequest("GET", "/api/findings?miner=wallet_activity", nil)
	rec := httptest.NewRecorder()
	handleFindings(rec, req)
	body := decodeBody(t, rec)

	findings, _ := body["findings"].([]any)
	if len(findings) != 1 || findings[0].(map[string]any)["id"] != "f2" {
		t.Fatalf("expected exactly finding f2, got %v", findings)
	}
}

// ------------------------------------------------------------ apply / dismiss

func TestHandleFindingIDFromPath(t *testing.T) {
	cases := []struct {
		path, suffix string
		wantID       string
		wantOK       bool
	}{
		{"/api/findings/abc-123/apply", "/apply", "abc-123", true},
		{"/api/findings/abc-123/dismiss", "/dismiss", "abc-123", true},
		{"/api/findings/abc-123/apply", "/dismiss", "", false}, // wrong suffix
		{"/api/findings//apply", "/apply", "", false},          // empty id
		{"/api/findings/a/b/apply", "/apply", "", false},       // id must not contain '/'
	}
	for _, c := range cases {
		id, ok := findingIDFromPath(c.path, c.suffix)
		if id != c.wantID || ok != c.wantOK {
			t.Errorf("findingIDFromPath(%q, %q) = (%q, %v), want (%q, %v)", c.path, c.suffix, id, ok, c.wantID, c.wantOK)
		}
	}
}

func TestHandleFindingDismiss(t *testing.T) {
	db := newTestGateway(t)
	insertTestFinding(t, db, "f1", "2026-01-01T00:00:00Z", "anomaly", "open")

	// Dismissing an open finding succeeds.
	req := httptest.NewRequest("POST", "/api/findings/f1/dismiss", nil)
	rec := httptest.NewRecorder()
	handleFindingDismiss(rec, req)
	if rec.Code != 200 {
		t.Fatalf("expected 200 dismissing an open finding, got %d: %s", rec.Code, rec.Body.String())
	}
	var state string
	db.QueryRow("SELECT state FROM findings WHERE id='f1'").Scan(&state)
	if state != "dismissed" {
		t.Fatalf("expected state=dismissed in the DB, got %q", state)
	}

	// Dismissing it again is a conflict, not a silent success.
	rec2 := httptest.NewRecorder()
	handleFindingDismiss(rec2, httptest.NewRequest("POST", "/api/findings/f1/dismiss", nil))
	if rec2.Code != 409 {
		t.Fatalf("expected 409 re-dismissing an already-dismissed finding, got %d", rec2.Code)
	}

	// Dismissing a finding that never existed is 404, not 409.
	rec3 := httptest.NewRecorder()
	handleFindingDismiss(rec3, httptest.NewRequest("POST", "/api/findings/nonexistent/dismiss", nil))
	if rec3.Code != 404 {
		t.Fatalf("expected 404 dismissing a nonexistent finding, got %d", rec3.Code)
	}
}

func TestHandleFindingApply_RealPythonBridge(t *testing.T) {
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skip("python3 not available in this environment")
	}
	db := newTestGateway(t)
	skillsDir := t.TempDir()
	t.Setenv("MYCELIUM_DB", dbPath)
	t.Setenv("MYCELIUM_SKILLS_DIR", skillsDir)

	insertTestFinding(t, db, "fskill", "2026-01-01T00:00:00Z", "opportunity", "open")
	db.Exec(`UPDATE findings SET suggestion='skill', payload=? WHERE id='fskill'`,
		`{"slug":"test_skill","sequence":["patch","grep"]}`)
	insertTestFinding(t, db, "fnotwired", "2026-01-01T00:00:01Z", "anomaly", "open")
	db.Exec(`UPDATE findings SET suggestion='not_a_real_type' WHERE id='fnotwired'`)

	t.Run("not found -> 404", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handleFindingApply(rec, httptest.NewRequest("POST", "/api/findings/does-not-exist/apply", nil))
		if rec.Code != 404 {
			t.Fatalf("expected 404, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("unwired suggestion type -> 422", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handleFindingApply(rec, httptest.NewRequest("POST", "/api/findings/fnotwired/apply", nil))
		if rec.Code != 422 {
			t.Fatalf("expected 422, got %d: %s", rec.Code, rec.Body.String())
		}
	})

	t.Run("skill finding applies and writes a real SKILL.md -> 200", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handleFindingApply(rec, httptest.NewRequest("POST", "/api/findings/fskill/apply", nil))
		if rec.Code != 200 {
			t.Fatalf("expected 200, got %d: %s", rec.Code, rec.Body.String())
		}
		body := decodeBody(t, rec)
		path, _ := body["path"].(string)
		if path == "" {
			t.Fatalf("expected a skill file path in the response, got %v", body)
		}
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("expected the SKILL.md to actually exist on disk at %s: %v", path, err)
		}
	})

	t.Run("re-applying the same finding -> 409", func(t *testing.T) {
		rec := httptest.NewRecorder()
		handleFindingApply(rec, httptest.NewRequest("POST", "/api/findings/fskill/apply", nil))
		if rec.Code != 409 {
			t.Fatalf("expected 409 re-applying an already-applied finding, got %d: %s", rec.Code, rec.Body.String())
		}
	})
}

// ------------------------------------------------------------------ /api/miners

func TestHandleMiners_IncludesZeroFindingMiners(t *testing.T) {
	db := newTestGateway(t)
	insertTestFinding(t, db, "f1", "2026-01-01T00:00:00Z", "anomaly", "open")
	insertTestFinding(t, db, "f2", "2026-01-01T00:00:01Z", "anomaly", "open")
	insertTestFinding(t, db, "f3", "2026-01-01T00:00:02Z", "wallet_activity", "open")

	rec := httptest.NewRecorder()
	handleMiners(rec, httptest.NewRequest("GET", "/api/miners", nil))
	body := decodeBody(t, rec)

	miners, _ := body["miners"].([]any)
	if len(miners) != len(knownMiners) {
		t.Fatalf("expected all %d known miners represented, got %d: %v", len(knownMiners), len(miners), miners)
	}
	byName := map[string]map[string]any{}
	for _, m := range miners {
		row := m.(map[string]any)
		byName[row["miner"].(string)] = row
	}
	if byName["anomaly"]["findings"].(float64) != 2 {
		t.Errorf("expected anomaly findings=2, got %v", byName["anomaly"]["findings"])
	}
	if byName["cross_agent"]["findings"].(float64) != 0 {
		t.Errorf("expected cross_agent (no findings seeded) to still appear with findings=0, got %v", byName["cross_agent"])
	}
}

// ------------------------------------------------------------------ /api/stream

func TestHandleStream_SendsProvenanceThenTraceEvent(t *testing.T) {
	db := newTestGateway(t)
	srv := httptest.NewServer(http.HandlerFunc(handleStream))
	defer srv.Close()

	client := &http.Client{Timeout: 10 * time.Second}
	req, _ := http.NewRequest("GET", srv.URL+"/api/stream", nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatalf("connect to stream: %v", err)
	}
	defer resp.Body.Close()

	if ct := resp.Header.Get("Content-Type"); ct != "text/event-stream" {
		t.Fatalf("expected Content-Type text/event-stream, got %q", ct)
	}

	// Insert a trace shortly after connecting so it lands inside the first
	// ~2s trace-poll tick, then read events until we've seen both an initial
	// "provenance" snapshot (sent immediately on connect) and the "trace"
	// event, bounded so a regression hangs the test instead of the suite.
	go func() {
		time.Sleep(300 * time.Millisecond)
		insertTestTrace(t, db, "live1", time.Now().UTC().Format("2006-01-02T15:04:05Z"), "a", "tool_call", "x", "success")
	}()

	seen := map[string]bool{}
	scanner := bufio.NewScanner(resp.Body)
	deadline := time.Now().Add(8 * time.Second)
	var currentEvent string
	for scanner.Scan() && time.Now().Before(deadline) {
		line := scanner.Text()
		switch {
		case strings.HasPrefix(line, "event: "):
			currentEvent = strings.TrimPrefix(line, "event: ")
		case strings.HasPrefix(line, "data: ") && currentEvent != "":
			seen[currentEvent] = true
			currentEvent = ""
		}
		if seen["provenance"] && seen["trace"] {
			return // success
		}
	}
	t.Fatalf("did not see both provenance and trace SSE events within the deadline; saw: %v", seen)
}
