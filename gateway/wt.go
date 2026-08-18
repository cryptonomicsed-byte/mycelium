// Mycelium gateway — WebTransport telemetry pipe (v0.3, live-push v0.5).
//
// Parallel transport to the HTTP API: QUIC/UDP on 127.0.0.1:8812.
// Agents that emit high-frequency traces (tool calls, decisions, errors)
// push them as WebTransport uni-streams or datagrams instead of HTTP
// round-trips. Same insertTrace path as /api/trace, so telemetry lands in
// the same SQLite substrate and the Ed25519 provenance chain picks it up
// automatically.
//
// v0.5 adds the reverse direction: one outbound uni-stream per accepted
// session, pushing the same trace/finding/provenance events the SSE
// endpoint (stream.go) does, on the same tick cadence, via streamSink --
// this is a second delivery path for existing capability, not a second
// implementation of it. This stays strictly loopback-trust-only,
// independent of MYCELIUM_GATEWAY_AUTH (auth.go's session cookie has
// nothing to attach to on a separate QUIC listener); building a
// cookie-forwarding bridge over WebTransport is out of scope.
//
// WebTransport requires TLS even on loopback -- a self-signed ECDSA cert is
// generated on first run (paths below) and rotated automatically before it
// expires: a self-signed cert can only be trusted by a browser via
// serverCertificateHashes pinning, which caps validity at 14 days, so this
// cert is issued for 13 and re-generated in place (via TLSConfig.
// GetCertificate, no listener restart) once it's within a day of expiring.
// GET /api/webtransport/cert-hash (main.go) exposes the current hash for
// the dashboard to pin before each connection attempt.
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
	"github.com/quic-go/webtransport-go"
)

var (
	wtCertPath = envOr("MYCELIUM_WT_CERT", "/data/data/com.termux/files/home/mycelium/gateway/wt_cert.pem")
	wtKeyPath  = envOr("MYCELIUM_WT_KEY", "/data/data/com.termux/files/home/mycelium/gateway/wt_key.pem")
)

const (
	wtAddr            = "127.0.0.1:8812"
	wtMaxTraceLen     = 64 << 10 // 64 KiB -- generous for a payload, bounded against abuse
	wtCertValidity    = 13 * 24 * time.Hour
	wtRotateThreshold = 24 * time.Hour // regenerate once this close to expiry, not only after
	wtRotateCheck     = time.Hour
)

// -------------------------------------------------------------- cert store

// currentWTCert is read on every TLS handshake (TLSConfig.GetCertificate)
// and swapped by wtCertRotationLoop -- letting the cert rotate without
// restarting the QUIC listener, which would drop every open session.
var (
	wtCertMu     sync.RWMutex
	currentCert  *tls.Certificate
	currentUntil time.Time
)

func loadCurrentWTCert() (*tls.Certificate, time.Time) {
	wtCertMu.RLock()
	defer wtCertMu.RUnlock()
	return currentCert, currentUntil
}

func setCurrentWTCert(cert tls.Certificate, until time.Time) {
	wtCertMu.Lock()
	currentCert = &cert
	currentUntil = until
	wtCertMu.Unlock()
}

// currentWTCertHash returns the SHA-256 of the current cert's DER encoding
// (what WebTransport's serverCertificateHashes pinning expects) and its
// expiry, for GET /api/webtransport/cert-hash (main.go).
func currentWTCertHash() ([]byte, time.Time, bool) {
	cert, until := loadCurrentWTCert()
	if cert == nil || len(cert.Certificate) == 0 {
		return nil, time.Time{}, false
	}
	sum := sha256.Sum256(cert.Certificate[0])
	return sum[:], until, true
}

// ensureWTCert loads the on-disk cert if it's still valid for a while
// longer, otherwise generates a fresh one (13d, ECDSA P-256, loopback SANs
// only) and persists it.
func ensureWTCert() (tls.Certificate, time.Time, error) {
	if cert, err := tls.LoadX509KeyPair(wtCertPath, wtKeyPath); err == nil {
		if leaf, err := x509.ParseCertificate(cert.Certificate[0]); err == nil {
			if time.Until(leaf.NotAfter) > wtRotateThreshold {
				return cert, leaf.NotAfter, nil
			}
		}
	}
	return generateWTCert()
}

func generateWTCert() (tls.Certificate, time.Time, error) {
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, time.Time{}, err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	notAfter := time.Now().Add(wtCertValidity)
	tpl := x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "mycelium-gateway-loopback"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:           []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
		DNSNames:              []string{"localhost"},
		IsCA:                  true,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, &tpl, &tpl, &priv.PublicKey, priv)
	if err != nil {
		return tls.Certificate{}, time.Time{}, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, _ := x509.MarshalECPrivateKey(priv)
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	if err := os.WriteFile(wtCertPath, certPEM, 0o600); err != nil {
		return tls.Certificate{}, time.Time{}, err
	}
	if err := os.WriteFile(wtKeyPath, keyPEM, 0o600); err != nil {
		return tls.Certificate{}, time.Time{}, err
	}
	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	return cert, notAfter, err
}

// wtCertRotationLoop regenerates the cert in place once it's within
// wtRotateThreshold of expiry. Runs for the lifetime of the process --
// checking only at startup would be insufficient since the gateway is a
// long-running process, not restarted often enough to naturally pick up a
// fresh cert before the old one ages past the 14-day pinning cap.
func wtCertRotationLoop() {
	ticker := time.NewTicker(wtRotateCheck)
	defer ticker.Stop()
	for range ticker.C {
		_, until := loadCurrentWTCert()
		if time.Until(until) > wtRotateThreshold {
			continue
		}
		cert, newUntil, err := generateWTCert()
		if err != nil {
			fmt.Println("mycelium webtransport: cert rotation failed:", err)
			continue
		}
		setCurrentWTCert(cert, newUntil)
		fmt.Println("mycelium webtransport: rotated cert, valid until", newUntil.Format(time.RFC3339))
	}
}

// --------------------------------------------------------- inbound (v0.3)

// handleWTSession pumps telemetry off one WebTransport session until it
// closes. Accepts bidirectional AND uni streams (each stream = 1 trace)
// plus datagrams (each datagram = 1 trace), acks each with a datagram, and
// (v0.5) pushes live substrate events back out over a dedicated outbound
// uni-stream (see broadcastToSession below).
func handleWTSession(sess *webtransport.Session) {
	ctx := sess.Context()
	go func() {
		for {
			str, err := sess.AcceptStream(ctx)
			if err != nil {
				return
			}
			go func() {
				body, _ := io.ReadAll(io.LimitReader(str, wtMaxTraceLen))
				code, resp := insertTrace(body)
				_ = code
				ack, _ := json.Marshal(resp)
				_ = sess.SendDatagram(ack)
				_ = str.Close()
			}()
		}
	}()
	go func() {
		for {
			str, err := sess.AcceptUniStream(ctx)
			if err != nil {
				return
			}
			go func() {
				body, _ := io.ReadAll(io.LimitReader(str, wtMaxTraceLen))
				code, resp := insertTrace(body)
				_ = code
				ack, _ := json.Marshal(resp)
				_ = sess.SendDatagram(ack)
			}()
		}
	}()
	go broadcastToSession(sess)
	for {
		dg, err := sess.ReceiveDatagram(ctx)
		if err != nil {
			return
		}
		if len(dg) > wtMaxTraceLen {
			continue
		}
		code, resp := insertTrace(dg)
		_ = code
		ack, _ := json.Marshal(resp)
		_ = sess.SendDatagram(ack)
	}
}

// -------------------------------------------------------- outbound (v0.5)

// wtSink implements streamSink (stream.go) over a WebTransport outbound
// uni-stream: length-prefixed JSON envelopes ({"type":..., "data":...},
// the same event/data shape SSE sends as named events + body) since a WT
// stream has no built-in message framing the way SSE's blank-line-
// terminated text does. Datagrams were considered and rejected: QUIC
// datagrams are capped by path MTU (roughly 1200-1450 bytes), well under
// wtMaxTraceLen's 64 KiB, so a finding with real evidence text or a
// provenance snapshot would silently truncate over datagrams -- a
// correctness bug, not a style choice.
type wtSink struct {
	str *webtransport.SendStream
}

func (s wtSink) send(event string, data any) bool {
	envelope, err := json.Marshal(map[string]any{"type": event, "data": data})
	if err != nil {
		return false
	}
	var lenBuf [4]byte
	binary.BigEndian.PutUint32(lenBuf[:], uint32(len(envelope)))
	if _, err := s.str.Write(lenBuf[:]); err != nil {
		return false
	}
	if _, err := s.str.Write(envelope); err != nil {
		return false
	}
	return true
}

// broadcastToSession pushes trace/finding/provenance events to one WT
// session over a dedicated outbound uni-stream, on the identical tick
// cadence stream.go's SSE endpoint uses (streamTraceTick, streamProvenanceTick)
// via the same streamSink-backed polling functions -- one implementation
// of "what's new," two transports delivering it.
func broadcastToSession(sess *webtransport.Session) {
	str, err := sess.OpenUniStreamSync(sess.Context())
	if err != nil {
		return
	}
	defer str.Close()
	sink := wtSink{str: str}

	// Starts from "now" (current watermarks), not history -- a WT client
	// bootstraps its initial state via the existing REST endpoints exactly
	// like the SSE client does, so this only needs to carry what changes
	// after the session opens.
	db := openDB()
	var sinceTrace, sinceFinding string
	_ = db.QueryRow(`SELECT COALESCE(MAX(ts), '') FROM traces`).Scan(&sinceTrace)
	_ = db.QueryRow(`SELECT COALESCE(MAX(created_ts), '') FROM findings`).Scan(&sinceFinding)
	db.Close()

	if !streamProvenanceEvent(sink) {
		return
	}

	traceTicker := time.NewTicker(streamTraceTick)
	defer traceTicker.Stop()
	provTicker := time.NewTicker(streamProvenanceTick)
	defer provTicker.Stop()

	ctx := sess.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case <-traceTicker.C:
			var alive bool
			sinceTrace, alive = streamNewTraces(sink, sinceTrace)
			if !alive {
				return
			}
			sinceFinding, alive = streamNewFindings(sink, sinceFinding)
			if !alive {
				return
			}
		case <-provTicker.C:
			if !streamProvenanceEvent(sink) {
				return
			}
		}
	}
}

// ------------------------------------------------------------------ serve

// wtServe runs the WebTransport listener. Blocks; call in a goroutine.
func wtServe() error {
	cert, until, err := ensureWTCert()
	if err != nil {
		return fmt.Errorf("wt cert: %w", err)
	}
	setCurrentWTCert(cert, until)
	go wtCertRotationLoop()

	var wtServer webtransport.Server
	mux := http.NewServeMux()
	mux.HandleFunc("/api/wt", func(w http.ResponseWriter, r *http.Request) {
		sess, err := wtServer.Upgrade(w, r)
		if err != nil {
			http.Error(w, "wt upgrade failed: "+err.Error(), http.StatusBadRequest)
			return
		}
		go handleWTSession(sess)
	})
	wtServer.H3 = &http3.Server{
		Addr: wtAddr,
		QUICConfig: &quic.Config{
			MaxIdleTimeout: 2 * time.Minute,
		},
		TLSConfig: &tls.Config{
			// GetCertificate (not the static Certificates list) so
			// wtCertRotationLoop's swaps take effect on the next handshake
			// without restarting this listener.
			GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
				cert, _ := loadCurrentWTCert()
				return cert, nil
			},
			NextProtos: []string{"h3"},
		},
		Handler: mux,
	}
	wtServer.CheckOrigin = func(r *http.Request) bool { return true } // loopback telemetry
	fmt.Println("mycelium webtransport on", wtAddr)
	// Not ListenAndServeTLS(certPath, keyPath) -- that method overwrites
	// TLSConfig.Certificates from the given files, bypassing GetCertificate
	// above. ListenAndServe uses H3.TLSConfig exactly as set.
	return wtServer.ListenAndServe()
}
