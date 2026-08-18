// Mycelium gateway — optional WebAuthn auth gate (Phase 2).
//
// Opt-in via MYCELIUM_GATEWAY_AUTH=1, default off -- zero behavior change
// unless explicitly enabled, same pattern as MYCELIUM_GATEWAY_DEV_CORS.
// Single-user "pair this device" model: no username/password, no
// multi-account. Registration proves possession of an authenticator;
// login re-proves it. Any number of devices can be paired (each
// registration appends a credential; WebAuthnCredentials() returns all of
// them), there's just one operator identity.
//
// RP ID must be "localhost", not an IP literal -- Chrome's WebAuthn
// implementation rejects navigator.credentials.create()/.get() outright
// against an IP-literal RP ID (Firefox/Edge tolerate it, Chrome doesn't).
// This is why main.go's `addr` default is "localhost:8811", not
// "127.0.0.1:8811" -- see that var's comment. requireLocalhostHost below
// rejects ceremony requests made against the IP-literal bind address with
// a clear error instead of letting the browser fail cryptically.
//
// Session model: in-memory map[token]expiry behind an HttpOnly cookie, not
// persisted to disk -- the gateway is a long-running process, manually
// restarted (confirmed by reading scripts/cron_cycle.sh, which only ever
// invokes the Python CLI, never touches this process), so losing sessions
// on a restart is an acceptable, rare inconvenience for a personal tool,
// not a real gap.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/go-webauthn/webauthn/protocol"
	"github.com/go-webauthn/webauthn/webauthn"
)

var (
	webauthnCredsPath = envOr("MYCELIUM_WEBAUTHN_CREDS", "/data/data/com.termux/files/home/mycelium/gateway/webauthn_credentials.json")
	authEnabled       = os.Getenv("MYCELIUM_GATEWAY_AUTH") != ""
)

const (
	ceremonyCookieName = "myc_ceremony"
	sessionCookieName  = "myc_session"
	ceremonyTTL        = 5 * time.Minute
	sessionTTL         = 30 * 24 * time.Hour
)

// ------------------------------------------------------------------- user

// mycUser is the single operator identity this gateway trusts. Not a real
// multi-user model -- WebAuthnID is a fixed constant; WebAuthnCredentials
// is backed by the on-disk store below.
type mycUser struct{}

func (mycUser) WebAuthnID() []byte          { return []byte("mycelium-operator") }
func (mycUser) WebAuthnName() string        { return "operator" }
func (mycUser) WebAuthnDisplayName() string { return "Mycelium Operator" }
func (mycUser) WebAuthnCredentials() []webauthn.Credential { return loadCredentials() }

// ------------------------------------------------------------ credential store

var credsMu sync.Mutex

func loadCredentials() []webauthn.Credential {
	credsMu.Lock()
	defer credsMu.Unlock()
	data, err := os.ReadFile(webauthnCredsPath)
	if err != nil {
		return nil
	}
	var creds []webauthn.Credential
	if err := json.Unmarshal(data, &creds); err != nil {
		return nil
	}
	return creds
}

func saveCredential(cred webauthn.Credential) error {
	credsMu.Lock()
	defer credsMu.Unlock()
	var creds []webauthn.Credential
	if data, err := os.ReadFile(webauthnCredsPath); err == nil {
		_ = json.Unmarshal(data, &creds)
	}
	creds = append(creds, cred)
	out, err := json.Marshal(creds)
	if err != nil {
		return err
	}
	return os.WriteFile(webauthnCredsPath, out, 0o600)
}

// ------------------------------------------------------------- webauthn init

var webAuthnInstance *webauthn.WebAuthn

func initWebAuthn() error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		host = addr
	}
	w, err := webauthn.New(&webauthn.Config{
		RPID:                  host,
		RPDisplayName:         "Mycelium",
		RPOrigins:             []string{"http://" + addr},
		AttestationPreference: protocol.PreferNoAttestation,
	})
	if err != nil {
		return err
	}
	webAuthnInstance = w
	return nil
}

// ------------------------------------------------------- ceremony + sessions

type ceremonyEntry struct {
	session *webauthn.SessionData
	expires time.Time
}

var (
	ceremoniesMu sync.Mutex
	ceremonies   = map[string]ceremonyEntry{}

	sessionsMu sync.Mutex
	sessions   = map[string]time.Time{}
)

func randomToken() string {
	b := make([]byte, 32)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func storeCeremony(s *webauthn.SessionData) string {
	id := randomToken()
	ceremoniesMu.Lock()
	ceremonies[id] = ceremonyEntry{session: s, expires: time.Now().Add(ceremonyTTL)}
	ceremoniesMu.Unlock()
	return id
}

// takeCeremony is one-shot: a ceremony (and its challenge) is consumed on
// first read whether or not it turns out still valid, so a begin/finish
// pair can never be replayed.
func takeCeremony(id string) (*webauthn.SessionData, bool) {
	ceremoniesMu.Lock()
	defer ceremoniesMu.Unlock()
	e, ok := ceremonies[id]
	delete(ceremonies, id)
	if !ok || time.Now().After(e.expires) {
		return nil, false
	}
	return e.session, true
}

func createSession() string {
	token := randomToken()
	sessionsMu.Lock()
	sessions[token] = time.Now().Add(sessionTTL)
	sessionsMu.Unlock()
	return token
}

func validSession(token string) bool {
	if token == "" {
		return false
	}
	sessionsMu.Lock()
	defer sessionsMu.Unlock()
	exp, ok := sessions[token]
	if !ok {
		return false
	}
	if time.Now().After(exp) {
		delete(sessions, token)
		return false
	}
	return true
}

func revokeSession(token string) {
	sessionsMu.Lock()
	delete(sessions, token)
	sessionsMu.Unlock()
}

// ------------------------------------------------------------------ handlers

func writeAuthJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func setCookie(w http.ResponseWriter, name, value, path string, maxAge time.Duration) {
	http.SetCookie(w, &http.Cookie{
		Name: name, Value: value, Path: path,
		HttpOnly: true, SameSite: http.SameSiteLaxMode, MaxAge: int(maxAge.Seconds()),
		// No Secure flag: this is plain HTTP on localhost -- a Secure
		// cookie would be silently dropped by the browser on a non-HTTPS
		// origin.
	})
}

func clearCookie(w http.ResponseWriter, name, path string) {
	http.SetCookie(w, &http.Cookie{Name: name, Value: "", Path: path, MaxAge: -1, HttpOnly: true})
}

func ceremonyFromRequest(r *http.Request) (*webauthn.SessionData, bool) {
	c, err := r.Cookie(ceremonyCookieName)
	if err != nil {
		return nil, false
	}
	return takeCeremony(c.Value)
}

// requireLocalhostHost rejects auth-ceremony requests made against an
// IP-literal Host header (e.g. 127.0.0.1:8811) with a clear error, rather
// than letting Chrome's WebAuthn implementation fail cryptically inside
// browser JS for an RP ID it silently refuses.
func requireLocalhostHost(w http.ResponseWriter, r *http.Request) bool {
	host := r.Host
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	if net.ParseIP(host) != nil {
		writeAuthJSON(w, 400, map[string]string{
			"error": fmt.Sprintf("WebAuthn requires a non-IP hostname -- use http://%s/web/ instead of an IP address", addr),
		})
		return false
	}
	return true
}

func handleAuthRegisterBegin(w http.ResponseWriter, r *http.Request) {
	if !requireLocalhostHost(w, r) {
		return
	}
	creation, session, err := webAuthnInstance.BeginRegistration(mycUser{})
	if err != nil {
		writeAuthJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	setCookie(w, ceremonyCookieName, storeCeremony(session), "/api/auth", ceremonyTTL)
	writeAuthJSON(w, 200, creation)
}

func handleAuthRegisterFinish(w http.ResponseWriter, r *http.Request) {
	if !requireLocalhostHost(w, r) {
		return
	}
	session, ok := ceremonyFromRequest(r)
	if !ok {
		writeAuthJSON(w, 400, map[string]string{"error": "no or expired registration ceremony"})
		return
	}
	cred, err := webAuthnInstance.FinishRegistration(mycUser{}, *session, r)
	if err != nil {
		writeAuthJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	if err := saveCredential(*cred); err != nil {
		writeAuthJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	clearCookie(w, ceremonyCookieName, "/api/auth")
	writeAuthJSON(w, 200, map[string]string{"status": "registered"})
}

func handleAuthLoginBegin(w http.ResponseWriter, r *http.Request) {
	if !requireLocalhostHost(w, r) {
		return
	}
	if len(loadCredentials()) == 0 {
		writeAuthJSON(w, 409, map[string]string{"error": "no device paired yet -- register first"})
		return
	}
	assertion, session, err := webAuthnInstance.BeginLogin(mycUser{})
	if err != nil {
		writeAuthJSON(w, 500, map[string]string{"error": err.Error()})
		return
	}
	setCookie(w, ceremonyCookieName, storeCeremony(session), "/api/auth", ceremonyTTL)
	writeAuthJSON(w, 200, assertion)
}

func handleAuthLoginFinish(w http.ResponseWriter, r *http.Request) {
	if !requireLocalhostHost(w, r) {
		return
	}
	session, ok := ceremonyFromRequest(r)
	if !ok {
		writeAuthJSON(w, 400, map[string]string{"error": "no or expired login ceremony"})
		return
	}
	if _, err := webAuthnInstance.FinishLogin(mycUser{}, *session, r); err != nil {
		writeAuthJSON(w, 401, map[string]string{"error": err.Error()})
		return
	}
	clearCookie(w, ceremonyCookieName, "/api/auth")
	setCookie(w, sessionCookieName, createSession(), "/", sessionTTL)
	writeAuthJSON(w, 200, map[string]string{"status": "ok"})
}

func handleAuthLogout(w http.ResponseWriter, r *http.Request) {
	if c, err := r.Cookie(sessionCookieName); err == nil {
		revokeSession(c.Value)
	}
	clearCookie(w, sessionCookieName, "/")
	writeAuthJSON(w, 200, map[string]string{"status": "logged_out"})
}

// ---------------------------------------------------------------- middleware

// withAuth gates every /api/* route except /api/auth/* behind a valid
// session cookie, but only when MYCELIUM_GATEWAY_AUTH is set (authEnabled
// is read once at startup, matching the rest of this file's env-flag
// style) -- default off, zero behavior change otherwise. /web/* and
// everything outside /api/ is never gated: the lock-screen UI itself has
// to load before a session exists.
func withAuth(h http.Handler) http.Handler {
	if !authEnabled {
		return h
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/") || strings.HasPrefix(r.URL.Path, "/api/auth/") {
			h.ServeHTTP(w, r)
			return
		}
		c, err := r.Cookie(sessionCookieName)
		if err != nil || !validSession(c.Value) {
			writeAuthJSON(w, 401, map[string]string{"error": "unauthorized"})
			return
		}
		h.ServeHTTP(w, r)
	})
}

// registerAuthRoutes mounts /api/auth/* and initializes the WebAuthn
// relying party. Only called when authEnabled -- when auth is off, these
// endpoints don't exist at all rather than existing-but-unused.
func registerAuthRoutes() error {
	if err := initWebAuthn(); err != nil {
		return err
	}
	http.HandleFunc("/api/auth/register/begin", handleAuthRegisterBegin)
	http.HandleFunc("/api/auth/register/finish", handleAuthRegisterFinish)
	http.HandleFunc("/api/auth/login/begin", handleAuthLoginBegin)
	http.HandleFunc("/api/auth/login/finish", handleAuthLoginFinish)
	http.HandleFunc("/api/auth/logout", handleAuthLogout)
	return nil
}
