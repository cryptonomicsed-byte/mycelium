// Tests for the auth.go middleware/session/ceremony logic that's testable
// without a real WebAuthn ceremony (no browser, no authenticator). The
// actual register/login cryptographic round-trip (attestation + assertion
// verification against go-webauthn) was verified separately via a headless
// Chromium session driving a CDP virtual authenticator against a live
// gateway -- see the PR description for that transcript. This file covers
// what Go can exercise natively: session/ceremony store lifecycle, the
// IP-literal-host rejection, and withAuth's gating (or lack of it).
package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestSessionLifecycle(t *testing.T) {
	token := createSession()
	if !validSession(token) {
		t.Fatal("freshly created session should be valid")
	}
	revokeSession(token)
	if validSession(token) {
		t.Fatal("revoked session should no longer be valid")
	}
	if validSession("") {
		t.Fatal("empty token must never validate")
	}
	if validSession("not-a-real-token") {
		t.Fatal("unknown token must not validate")
	}
}

func TestSessionExpiry(t *testing.T) {
	token := randomToken()
	sessionsMu.Lock()
	sessions[token] = time.Now().Add(-time.Second) // already expired
	sessionsMu.Unlock()
	defer revokeSession(token)

	if validSession(token) {
		t.Fatal("expired session should not validate")
	}
	// validSession prunes expired entries on read -- confirm it's actually gone.
	sessionsMu.Lock()
	_, still := sessions[token]
	sessionsMu.Unlock()
	if still {
		t.Fatal("validSession should prune the expired entry")
	}
}

func TestCeremonyOneShot(t *testing.T) {
	id := storeCeremony(nil)
	if _, ok := takeCeremony(id); !ok {
		t.Fatal("first takeCeremony should succeed")
	}
	if _, ok := takeCeremony(id); ok {
		t.Fatal("second takeCeremony on the same id must fail -- ceremonies are one-shot")
	}
}

func TestCeremonyExpiry(t *testing.T) {
	id := randomToken()
	ceremoniesMu.Lock()
	ceremonies[id] = ceremonyEntry{session: nil, expires: time.Now().Add(-time.Second)}
	ceremoniesMu.Unlock()

	if _, ok := takeCeremony(id); ok {
		t.Fatal("expired ceremony should not be returned")
	}
}

func TestRequireLocalhostHost(t *testing.T) {
	cases := []struct {
		host string
		want bool
	}{
		{"localhost:8811", true},
		{"localhost", true},
		{"127.0.0.1:8811", false},
		{"127.0.0.1", false},
		{"::1", false},
	}
	for _, c := range cases {
		w := httptest.NewRecorder()
		r := httptest.NewRequest("POST", "/api/auth/register/begin", nil)
		r.Host = c.host
		got := requireLocalhostHost(w, r)
		if got != c.want {
			t.Errorf("requireLocalhostHost(Host=%q) = %v, want %v", c.host, got, c.want)
		}
		if !c.want && w.Code != 400 {
			t.Errorf("Host=%q: expected 400 on rejection, got %d", c.host, w.Code)
		}
	}
}

func TestWithAuthDisabledPassesThrough(t *testing.T) {
	orig := authEnabled
	authEnabled = false
	defer func() { authEnabled = orig }()

	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	h := withAuth(inner)

	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "/api/status", nil) // no session cookie at all
	h.ServeHTTP(w, r)

	if !called {
		t.Fatal("withAuth(disabled) must always pass through, regardless of session state")
	}
	if w.Code != 200 {
		t.Fatalf("expected 200 (inner handler wrote nothing, default), got %d", w.Code)
	}
}

func TestWithAuthEnabledGatesAPI(t *testing.T) {
	orig := authEnabled
	authEnabled = true
	defer func() { authEnabled = orig }()

	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	h := withAuth(inner)

	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "/api/status", nil) // no session cookie
	h.ServeHTTP(w, r)

	if called {
		t.Fatal("withAuth(enabled) must not call through without a valid session")
	}
	if w.Code != 401 {
		t.Fatalf("expected 401, got %d", w.Code)
	}
}

func TestWithAuthEnabledLetsAuthRoutesThrough(t *testing.T) {
	orig := authEnabled
	authEnabled = true
	defer func() { authEnabled = orig }()

	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	h := withAuth(inner)

	w := httptest.NewRecorder()
	r := httptest.NewRequest("POST", "/api/auth/login/begin", nil) // chicken-and-egg: never gated
	h.ServeHTTP(w, r)

	if !called {
		t.Fatal("/api/auth/* must never be gated by withAuth, even when enabled")
	}
}

func TestWithAuthEnabledLetsWebThrough(t *testing.T) {
	orig := authEnabled
	authEnabled = true
	defer func() { authEnabled = orig }()

	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	h := withAuth(inner)

	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "/web/", nil) // the lock screen itself has to load
	h.ServeHTTP(w, r)

	if !called {
		t.Fatal("/web/* must never be gated by withAuth -- the lock screen UI has to load before login")
	}
}

func TestWithAuthEnabledAcceptsValidSession(t *testing.T) {
	orig := authEnabled
	authEnabled = true
	defer func() { authEnabled = orig }()

	token := createSession()
	defer revokeSession(token)

	called := false
	inner := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })
	h := withAuth(inner)

	w := httptest.NewRecorder()
	r := httptest.NewRequest("GET", "/api/status", nil)
	r.AddCookie(&http.Cookie{Name: sessionCookieName, Value: token})
	h.ServeHTTP(w, r)

	if !called {
		t.Fatal("withAuth(enabled) should pass through with a valid session cookie")
	}
}
