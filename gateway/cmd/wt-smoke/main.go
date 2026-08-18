// wt-smoke — Go WebTransport client smoke test for the Mycelium gateway.
//
// Connects to 127.0.0.1:8812, opens a WebTransport session at /api/wt,
// pushes one trace as a uni-stream and one as a datagram, reads the ack
// datagrams. Verifies both landed in the substrate via the HTTP API.
package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/quic-go/webtransport-go"
)

const (
	wtURL  = "https://127.0.0.1:8812/api/wt"
	httpURL = "http://127.0.0.1:8811"
)

func trace(agent, action, kind string, n int) []byte {
	b, _ := json.Marshal(map[string]any{
		"agent": agent, "session": "wt-test", "kind": kind,
		"action": action, "target": "gateway:8812", "outcome": "success",
		"payload": map[string]any{"transport": "webtransport", "n": n},
	})
	return b
}

func main() {
	d := &webtransport.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, // self-signed loopback cert
	}
	ctx := context.Background()
	rsp, sess, err := d.Dial(ctx, wtURL, nil)
	if err != nil {
		fmt.Println("wt-smoke: dial failed:", err)
		os.Exit(1)
	}
	defer rsp.Body.Close()
	fmt.Println("wt-smoke: session open, status", rsp.Status)

	// --- uni-stream trace ---
	str, err := sess.OpenUniStreamSync(sess.Context())
	if err != nil {
		fmt.Println("wt-smoke: open uni-stream failed:", err)
		os.Exit(1)
	}
	body := trace("wt-smoke", "wt_stream_push", "tool_call", 1)
	if _, err := str.Write(body); err != nil {
		fmt.Println("wt-smoke: stream write failed:", err)
		os.Exit(1)
	}
	if err := str.Close(); err != nil {
		fmt.Println("wt-smoke: stream close failed:", err)
		os.Exit(1)
	}
	fmt.Println("wt-smoke: uni-stream trace sent")

	// --- datagram trace ---
	dg := trace("wt-smoke", "wt_datagram_push", "observation", 2)
	if err := sess.SendDatagram(dg); err != nil {
		fmt.Println("wt-smoke: datagram send failed:", err)
		os.Exit(1)
	}
	fmt.Println("wt-smoke: datagram trace sent")

	// --- read acks (stream echo + datagram acks) ---
	go func() {
		for {
			s, err := sess.AcceptStream(sess.Context())
			if err != nil {
				return
			}
			data, _ := io.ReadAll(s)
			fmt.Println("wt-smoke: stream ack:", string(data))
		}
	}()
	ctx = sess.Context()
	timeout := time.After(5 * time.Second)
	acks := 0
	for acks < 2 {
		select {
		case <-ctx.Done():
			fmt.Println("wt-smoke: session closed early")
			os.Exit(1)
		case <-timeout:
			fmt.Println("wt-smoke: ack timeout (got", acks, "acks)")
			os.Exit(1)
		default:
			ack, err := sess.ReceiveDatagram(ctx)
			if err != nil {
				fmt.Println("wt-smoke: recv datagram err:", err)
				break
			}
			acks++
			fmt.Println("wt-smoke: datagram ack:", string(ack))
		}
	}
	time.Sleep(500 * time.Millisecond)

	// --- verify via HTTP API ---
	resp, err := http.Get(httpURL + "/api/traces?limit=8")
	if err != nil {
		fmt.Println("wt-smoke: http verify failed:", err)
		os.Exit(1)
	}
	defer resp.Body.Close()
	var data struct {
		Traces []map[string]any `json:"traces"`
	}
	raw, _ := io.ReadAll(resp.Body)
	json.Unmarshal(raw, &data)
	hits := 0
	for _, t := range data.Traces {
		if t["agent"] == "wt-smoke" {
			hits++
			fmt.Printf("wt-smoke: in substrate -> %v %v %v\n", t["action"], t["outcome"], t["ts"])
		}
	}
	fmt.Printf("wt-smoke: %d traces in substrate\n", hits)
	if hits >= 2 {
		fmt.Println("WT_SMOKE_PASS")
	} else {
		fmt.Println("WT_SMOKE_FAIL")
		os.Exit(1)
	}
}
