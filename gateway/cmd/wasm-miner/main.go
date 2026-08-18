// wasm-miner — a Mycelium miner compiled to WebAssembly (wasip1).
//
// Implements the same miner interface as the Python miners in miners.py:
// reads a JSON array of traces on stdin, writes a JSON array of findings on
// stdout. Runs inside the gateway under wazero, giving a true compile
// boundary: the miner has no filesystem/network/syscall access beyond WASI
// stdio — it cannot touch the substrate except through the host.
//
// Miner logic: recurring_workflow — detects action sequences that repeat
// more than MIN_REPEATS times (a cheap, useful signal: agents grinding the
// same tool loop). Kept dependency-free (stdlib only) so the .wasm stays
// small and the compile is trivial:
//
//   GOOS=wasip1 GOARCH=wasm go build -o miner_recurring.wasm ./cmd/wasm-miner
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
)

// Trace mirrors the substrate envelope (fields we care about).
type Trace struct {
	Action string `json:"action"`
	Target string `json:"target"`
	Outcome string `json:"outcome"`
	Agent  string `json:"agent"`
	Kind   string `json:"kind"`
	TS     string `json:"ts"`
}

// Finding is the output envelope, compatible with core.add_finding.
type Finding struct {
	Miner      string         `json:"miner"`
	Confidence float64        `json:"confidence"`
	Title      string         `json:"title"`
	Evidence   string         `json:"evidence"`
	Suggestion string         `json:"suggestion"`
	Payload    map[string]any `json:"payload"`
}

const (
	minRepeats = 5  // a sequence must repeat this many times to matter
	windowSize = 2  // bigram action sequence
)

func main() {
	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stderr, "read stdin:", err)
		os.Exit(1)
	}
	var traces []Trace
	if err := json.Unmarshal(raw, &traces); err != nil {
		fmt.Fprintln(os.Stderr, "bad traces json:", err)
		os.Exit(1)
	}

	// Bigram frequency over (action, target) pairs in trace order.
	counts := map[string]int{}
	order := []string{}
	var prevKey string
	for _, t := range traces {
		if t.Action == "" {
			continue
		}
		key := t.Action + "\x00" + t.Target
		if prevKey != "" {
			pair := prevKey + "\x1f" + key
			if counts[pair] == 0 {
				order = append(order, pair)
			}
			counts[pair]++
		}
		prevKey = key
	}

	findings := []Finding{}
	for _, pair := range order {
		n := counts[pair]
		if n < minRepeats {
			continue
		}
		parts := splitPair(pair)
		conf := 0.5 + 0.08*float64(n)
		if conf > 0.97 {
			conf = 0.97
		}
		title := fmt.Sprintf("Wasm miner: '%s' -> '%s' repeated %dx", parts[0], parts[1], n)
		findings = append(findings, Finding{
			Miner:      "wasm_recurring",
			Confidence: conf,
			Title:      title,
			Evidence:   fmt.Sprintf("sequence [%s -> %s] ran %dx in the substrate window", parts[0], parts[1], n),
			Suggestion: "skill",
			Payload: map[string]any{
				"count": n, "from": parts[0], "to": parts[1], "wasm": true,
			},
		})
	}

	out, _ := json.Marshal(findings)
	os.Stdout.Write(out)
}

func splitPair(pair string) []string {
	for i := 0; i < len(pair); i++ {
		if pair[i] == '\x1f' {
			return []string{
				strings.TrimSpace(strings.ReplaceAll(pair[:i], "\x00", " ")),
				strings.TrimSpace(strings.ReplaceAll(pair[i+1:], "\x00", " ")),
			}
		}
	}
	return []string{pair}
}
