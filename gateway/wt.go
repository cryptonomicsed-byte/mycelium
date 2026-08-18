// Mycelium gateway — WebTransport telemetry pipe (v0.3).
//
// Parallel transport to the HTTP API: QUIC/UDP on 127.0.0.1:8812.
// Agents that emit high-frequency traces (tool calls, decisions, errors)
// push them as WebTransport uni-streams or datagrams instead of HTTP
// round-trips. Same insertTrace path as /api/trace, so telemetry lands in
// the same SQLite substrate and the Ed25519 provenance chain picks it up
// automatically.
//
// WebTransport requires TLS even on loopback — a self-signed ECDSA cert is
// generated on first run (gateway/wt_cert.pem, wt_key.pem). Loopback-only,
// so trust is pinned by address, not by CA.
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/quic-go/quic-go"
	"github.com/quic-go/quic-go/http3"
	"github.com/quic-go/webtransport-go"
)

const (
	wtAddr        = "127.0.0.1:8812"
	wtCertPath    = "/data/data/com.termux/files/home/mycelium/gateway/wt_cert.pem"
	wtKeyPath     = "/data/data/com.termux/files/home/mycelium/gateway/wt_key.pem"
	wtMaxTraceLen = 64 << 10 // 64 KiB — generous for a payload, bounded against abuse
)

// ensureWTCert loads the self-signed cert or generates a fresh one (10y,
// ECDSA P-256, loopback SANs only).
func ensureWTCert() (tls.Certificate, error) {
	if cert, err := tls.LoadX509KeyPair(wtCertPath, wtKeyPath); err == nil {
		return cert, nil
	}
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tpl := x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "mycelium-gateway-loopback"},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().AddDate(10, 0, 0),
		KeyUsage:     x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
		DNSNames:     []string{"localhost"},
		IsCA:         true,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, &tpl, &tpl, &priv.PublicKey, priv)
	if err != nil {
		return tls.Certificate{}, err
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyDER, _ := x509.MarshalECPrivateKey(priv)
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})
	if err := os.WriteFile(wtCertPath, certPEM, 0o600); err != nil {
		return tls.Certificate{}, err
	}
	if err := os.WriteFile(wtKeyPath, keyPEM, 0o600); err != nil {
		return tls.Certificate{}, err
	}
	return tls.X509KeyPair(certPEM, keyPEM)
}

// handleWTSession pumps telemetry off one WebTransport session until it
// closes. Accepts bidirectional AND uni streams (each stream = 1 trace)
// plus datagrams (each datagram = 1 trace), acks each with a datagram.
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

// wtServe runs the WebTransport listener. Blocks; call in a goroutine.
func wtServe() error {
	cert, err := ensureWTCert()
	if err != nil {
		return fmt.Errorf("wt cert: %w", err)
	}
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
			Certificates: []tls.Certificate{cert},
			NextProtos:   []string{"h3"},
		},
		Handler: mux,
	}
	wtServer.CheckOrigin = func(r *http.Request) bool { return true } // loopback telemetry
	fmt.Println("mycelium webtransport on", wtAddr)
	return wtServer.ListenAndServeTLS(wtCertPath, wtKeyPath)
}

var _ = filepath.Dir
