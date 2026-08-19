// Tests for the ops/observability endpoints (ops.go). Same conventions as
// main_test.go: temp-dir substrate via direct package-var reassignment, the
// real Python CLI for anything that shells out.
package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestHandleAgents_GroupsAndErrorRate(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	insertTestTrace(t, db, "tr1", "2026-08-18T10:00:00Z", "alpha", "tool_call", "patch", "success")
	insertTestTrace(t, db, "tr2", "2026-08-18T10:01:00Z", "alpha", "tool_call", "patch", "failure")
	insertTestTrace(t, db, "tr3", "2026-08-18T10:02:00Z", "beta", "observation", "look", "info")

	w := httptest.NewRecorder()
	handleAgents(w, httptest.NewRequest("GET", "/api/agents", nil))
	if w.Code != 200 {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Count  int `json:"count"`
		Agents []struct {
			Name       string           `json:"name"`
			TraceCount int64            `json:"trace_count"`
			LastSeen   string           `json:"last_seen"`
			ErrorRate  float64          `json:"error_rate"`
			Kinds      map[string]int64 `json:"kinds"`
		} `json:"agents"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Count != 2 {
		t.Fatalf("expected 2 agents, got %d", resp.Count)
	}
	// ordered by trace count desc -> alpha first
	a := resp.Agents[0]
	if a.Name != "alpha" || a.TraceCount != 2 {
		t.Fatalf("unexpected first agent: %+v", a)
	}
	if a.ErrorRate != 0.5 {
		t.Fatalf("alpha error rate: got %v, want 0.5", a.ErrorRate)
	}
	if a.Kinds["tool_call"] != 2 {
		t.Fatalf("alpha kinds: %+v", a.Kinds)
	}
	if a.LastSeen != "2026-08-18T10:01:00Z" {
		t.Fatalf("alpha last_seen: %s", a.LastSeen)
	}
}

func TestHandleSkills_ScansGeneratedSkills(t *testing.T) {
	dir := t.TempDir()
	oldSkills := skillsDir
	skillsDir = dir
	defer func() { skillsDir = oldSkills }()

	if err := os.MkdirAll(filepath.Join(dir, "patch_grep"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "patch_grep", "SKILL.md"), []byte("# skill"), 0o644); err != nil {
		t.Fatal(err)
	}
	// a dir without SKILL.md must not appear
	os.MkdirAll(filepath.Join(dir, "empty_dir"), 0o755)

	w := httptest.NewRecorder()
	handleSkills(w, httptest.NewRequest("GET", "/api/skills", nil))
	var resp struct {
		Count  int `json:"count"`
		Skills []struct {
			Name string `json:"name"`
			Size int64  `json:"size"`
		} `json:"skills"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Count != 1 || resp.Skills[0].Name != "patch_grep" {
		t.Fatalf("unexpected skills: %+v", resp)
	}
}

func TestHandleSkills_MissingDirIsEmptyNotError(t *testing.T) {
	oldSkills := skillsDir
	skillsDir = filepath.Join(t.TempDir(), "does-not-exist")
	defer func() { skillsDir = oldSkills }()

	w := httptest.NewRecorder()
	handleSkills(w, httptest.NewRequest("GET", "/api/skills", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"count":0`) {
		t.Fatalf("expected empty 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestHandleAlerts_RealPythonBridge(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	// The subprocess reads env, not our package vars.
	t.Setenv("MYCELIUM_DB", dbPath)
	alertsDir := t.TempDir()
	t.Setenv("MYCELIUM_ALERTS_DIR", alertsDir)
	cfg := `{"condition": {"action": "terminal", "min_failures": 1, "min_rate": 0.5}, "state": "open"}`
	if err := os.WriteFile(filepath.Join(alertsDir, "alert_terminal.json"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	insertTestTrace(t, db, "at1", "2026-08-18T10:00:00Z", "a", "tool_call", "terminal", "failure")
	insertTestTrace(t, db, "at2", "2026-08-18T10:01:00Z", "a", "tool_call", "terminal", "failure")

	w := httptest.NewRecorder()
	handleAlerts(w, httptest.NewRequest("GET", "/api/alerts", nil))
	if w.Code != 200 {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Alerts []struct {
			Alert   string `json:"alert"`
			Tripped bool   `json:"tripped"`
		} `json:"alerts"`
		Tripped int `json:"tripped"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if len(resp.Alerts) != 1 || !resp.Alerts[0].Tripped || resp.Tripped != 1 {
		t.Fatalf("expected one tripped alert, got: %s", w.Body.String())
	}
}

func TestHandleStatsTimeseries_BucketsHourly(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	now := time.Now().UTC()
	h1 := now.Add(-2*time.Hour).Format("2006-01-02T15") + ":00:00Z"
	h2 := now.Add(-1*time.Hour).Format("2006-01-02T15") + ":00:00Z"
	insertTestTrace(t, db, "s1", h1, "a", "tool_call", "patch", "success")
	insertTestTrace(t, db, "s2", h1, "a", "observation", "look", "info")
	insertTestTrace(t, db, "s3", h2, "a", "tool_call", "force_mine", "success")
	insertTestFinding(t, db, "f1", h2, "anomaly", "open")

	w := httptest.NewRecorder()
	handleStatsTimeseries(w, httptest.NewRequest("GET", "/api/stats/timeseries?range=24h", nil))
	if w.Code != 200 {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Range   string `json:"range"`
		Buckets []struct {
			TS       string           `json:"ts"`
			Traces   map[string]int64 `json:"traces_by_kind"`
			Total    int64            `json:"traces_total"`
			Findings map[string]int64 `json:"findings_by_state"`
			MineRuns int64            `json:"mine_runs"`
		} `json:"buckets"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Range != "24h" || len(resp.Buckets) != 2 {
		t.Fatalf("expected 2 hourly buckets, got %d (%s)", len(resp.Buckets), w.Body.String())
	}
	b1, b2 := resp.Buckets[0], resp.Buckets[1]
	if b1.Total != 2 || b1.Traces["tool_call"] != 1 || b1.Traces["observation"] != 1 {
		t.Fatalf("bucket1 wrong: %+v", b1)
	}
	if b2.Total != 1 || b2.MineRuns != 1 || b2.Findings["open"] != 1 {
		t.Fatalf("bucket2 wrong: %+v", b2)
	}
}

func TestHandlePrune_DeletesAndReanchors(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	insertTestTrace(t, db, "old1", "2026-01-01T00:00:00Z", "a", "tool_call", "patch", "success")
	insertTestTrace(t, db, "old2", "2026-01-02T00:00:00Z", "a", "tool_call", "patch", "success")
	insertTestTrace(t, db, "new1", "2026-08-18T00:00:00Z", "a", "tool_call", "patch", "success")

	// Anchor the pre-prune chain so the test proves re-anchoring, not just
	// anchoring-from-scratch.
	chain, err := buildChain(db)
	if err != nil {
		t.Fatal(err)
	}
	if ok, _, why := reconcile(chain); !ok {
		t.Fatalf("pre-prune reconcile failed: %s", why)
	}

	body := strings.NewReader(`{"before_ts": "2026-06-01T00:00:00Z"}`)
	w := httptest.NewRecorder()
	handlePrune(w, httptest.NewRequest("POST", "/api/prune", body))
	if w.Code != 200 {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Deleted    int64 `json:"deleted"`
		Reanchored int64 `json:"reanchored"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Deleted != 2 {
		t.Fatalf("expected 2 deleted, got %d", resp.Deleted)
	}
	// new1 + the prune-audit trace = 2 envelopes in the re-anchored chain
	if resp.Reanchored != 2 {
		t.Fatalf("expected 2 reanchored, got %d", resp.Reanchored)
	}

	// The post-prune chain must verify AND reconcile cleanly against the
	// rewritten anchor log -- a prune must never read as tamper afterward.
	chain, err = buildChain(db)
	if err != nil {
		t.Fatal(err)
	}
	if ok, _, why := verifyChain(chain); !ok {
		t.Fatalf("post-prune chain invalid: %s", why)
	}
	if ok, n, why := reconcile(chain); !ok || n != 2 {
		t.Fatalf("post-prune reconcile: ok=%v n=%d why=%s", ok, n, why)
	}
}

func TestHandlePrune_RejectsBadRequests(t *testing.T) {
	newTestGateway(t).Close()
	w := httptest.NewRecorder()
	handlePrune(w, httptest.NewRequest("GET", "/api/prune", nil))
	if w.Code != 405 {
		t.Fatalf("GET should 405, got %d", w.Code)
	}
	w = httptest.NewRecorder()
	handlePrune(w, httptest.NewRequest("POST", "/api/prune", strings.NewReader(`{}`)))
	if w.Code != 400 {
		t.Fatalf("empty before_ts should 400, got %d", w.Code)
	}
}

func TestLogRingAndHandler(t *testing.T) {
	logMu.Lock()
	logRing = nil
	logMu.Unlock()
	gwLogf("info", "hello %d", 1)
	gwLogf("error", "boom")

	w := httptest.NewRecorder()
	handleLogs(w, httptest.NewRequest("GET", "/api/logs?lines=10", nil))
	var resp struct {
		Count int       `json:"count"`
		Lines []logLine `json:"lines"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Count != 2 || resp.Lines[0].Msg != "hello 1" || resp.Lines[1].Level != "error" {
		t.Fatalf("unexpected logs: %+v", resp)
	}

	// level filter
	w = httptest.NewRecorder()
	handleLogs(w, httptest.NewRequest("GET", "/api/logs?level=error", nil))
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp.Count != 1 || resp.Lines[0].Msg != "boom" {
		t.Fatalf("level filter failed: %+v", resp)
	}
}

func TestRequestLogMiddleware(t *testing.T) {
	reqMu.Lock()
	reqRing = nil
	reqMu.Unlock()

	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(418)
	})
	h := withRequestLog(inner)

	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/api/status", nil))
	h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("GET", "/web/dashboard/index.html", nil))

	recs := lastRequests(10)
	if len(recs) != 1 {
		t.Fatalf("expected 1 record (static /web/ skipped), got %d", len(recs))
	}
	if recs[0].Path != "/api/status" || recs[0].Status != 418 {
		t.Fatalf("unexpected record: %+v", recs[0])
	}
}

func TestStatusIncludesOpsExtras(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	insertTestTrace(t, db, "x1", "2026-08-18T10:00:00Z", "a", "tool_call", "patch", "success")

	w := httptest.NewRecorder()
	handleStatus(w, httptest.NewRequest("GET", "/api/status", nil))
	var resp map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"uptime_secs", "auth_enabled", "storage", "last_requests"} {
		if _, ok := resp[key]; !ok {
			t.Fatalf("status missing %q: %v", key, resp)
		}
	}
	storage := resp["storage"].(map[string]any)
	if storage["oldest_ts"] != "2026-08-18T10:00:00Z" {
		t.Fatalf("storage oldest_ts wrong: %v", storage)
	}
}

func TestHandleMiners_ByStateSplit(t *testing.T) {
	db := newTestGateway(t)
	defer db.Close()
	insertTestFinding(t, db, "m1", "2026-08-18T10:00:00Z", "anomaly", "open")
	insertTestFinding(t, db, "m2", "2026-08-18T10:01:00Z", "anomaly", "applied")

	w := httptest.NewRecorder()
	handleMiners(w, httptest.NewRequest("GET", "/api/miners", nil))
	var resp struct {
		Miners []struct {
			Miner   string           `json:"miner"`
			ByState map[string]int64 `json:"by_state"`
		} `json:"miners"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	for _, m := range resp.Miners {
		if m.Miner == "anomaly" {
			if m.ByState["open"] != 1 || m.ByState["applied"] != 1 {
				t.Fatalf("anomaly by_state wrong: %+v", m.ByState)
			}
			return
		}
	}
	t.Fatal("anomaly miner missing from response")
}
