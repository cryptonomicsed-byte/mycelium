// mycelium-provenance — Rust verifier for the Mycelium trace chain.
//
// Reads a JSON export of the chain (as served by the Go gateway at
// GET /api/provenance) and independently re-verifies:
//   1. hash continuity  — each envelope's prev_hash == previous hash
//   2. content hashes   — SHA-256 over "index|trace_id|ts|prev_hash"
//   3. Ed25519 signatures over the same body with the gateway's public key
//
// Usage:
//   mycelium-provenance chain.json            # verify a chain file
//   mycelium-provenance --url http://127.0.0.1:8811/api/provenance  (not yet)
//
// This is the v0.2 provenance audit surface: any third party can verify the
// substrate's integrity with zero trust in the gateway process itself.

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::env;
use std::fs;
use std::process;

fn body(index: i64, trace_id: &str, ts: &str, action: &str, target: &str,
        outcome: &str, payload_sha: &str, prev_hash: &str) -> String {
    format!("{}|{}|{}|{}|{}|{}|{}|{}",
            index, trace_id, ts, action, target, outcome, payload_sha, prev_hash)
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("usage: {} <chain.json>", args[0]);
        process::exit(2);
    }
    let raw = fs::read_to_string(&args[1]).unwrap_or_else(|e| {
        eprintln!("cannot read {}: {}", args[1], e);
        process::exit(2);
    });
    let v: Value = serde_json::from_str(&raw).unwrap_or_else(|e| {
        eprintln!("bad json: {}", e);
        process::exit(2);
    });

    let pub_hex = v["pubkey"].as_str().unwrap_or("");
    if pub_hex.is_empty() {
        eprintln!("export missing pubkey — server returned a divergence/409 response, not a chain");
        process::exit(2);
    }
    let pub_bytes = hex_decode(pub_hex).unwrap_or_else(|| {
        eprintln!("bad pubkey hex");
        process::exit(2);
    });
    let pub_arr: [u8; 32] = pub_bytes.try_into().unwrap_or_else(|_| {
        eprintln!("bad pubkey length (want 32 bytes)");
        process::exit(2);
    });
    let vk = VerifyingKey::from_bytes(&pub_arr).unwrap_or_else(|e| {
        eprintln!("bad public key: {}", e);
        process::exit(2);
    });

    let chain = v["chain"].as_array().expect("chain array missing");

    // Optional anchor log: every recorded envelope must match the derived
    // chain exactly. The anchor is the append-only audit surface — the DB
    // can be rewritten, the anchor cannot (without rewriting it too).
    let anchor_path = args.get(2);
    let mut anchored: usize = 0;
    if let Some(ap) = anchor_path {
        let raw_anchor = fs::read_to_string(ap).unwrap_or_else(|e| {
            eprintln!("cannot read anchor {}: {}", ap, e);
            process::exit(2);
        });
        for (i, line) in raw_anchor.lines().enumerate() {
            if line.trim().is_empty() {
                continue;
            }
            let rec: Value = serde_json::from_str(line).unwrap_or_else(|e| {
                eprintln!("bad anchor line {}: {}", i, e);
                process::exit(2);
            });
            let want_hash = rec["hash"].as_str().unwrap_or("");
            let want_trace = rec["trace_id"].as_str().unwrap_or("");
            let e = &chain[i];
            if e["trace_id"].as_str().unwrap_or("") != want_trace
                || e["hash"].as_str().unwrap_or("") != want_hash
            {
                eprintln!(
                    "ANCHOR MISMATCH at envelope #{}: derived={} anchor={}",
                    i, e["hash"].as_str().unwrap_or(""), want_hash
                );
                process::exit(1);
            }
            anchored += 1;
        }
    }

    let mut prev = String::new();
    let mut checked: usize = 0;
    for (i, e) in chain.iter().enumerate() {
        let index = e["index"].as_i64().unwrap_or(-1);
        let trace_id = e["trace_id"].as_str().unwrap_or("");
        let ts = e["ts"].as_str().unwrap_or("");
        let action = e["action"].as_str().unwrap_or("");
        let target = e["target"].as_str().unwrap_or("");
        let outcome = e["outcome"].as_str().unwrap_or("");
        let payload_sha = e["payload_sha"].as_str().unwrap_or("");
        let prev_hash = e["prev_hash"].as_str().unwrap_or("");
        let hash = e["hash"].as_str().unwrap_or("");
        let sig_hex = e["sig"].as_str().unwrap_or("");

        if prev_hash != prev {
            fail(i, index, "prev_hash continuity broken");
        }
        let body = body(index, trace_id, ts, action, target, outcome, payload_sha, prev_hash);
        let digest = Sha256::digest(body.as_bytes());
        let digest_hex = hex_encode(&digest);
        if digest_hex != hash {
            fail(i, index, "content hash mismatch");
        }
        let sig_bytes = match hex_decode(sig_hex) {
            Some(b) => b,
            None => fail(i, index, "bad signature hex"),
        };
        let sig_arr: [u8; 64] = sig_bytes.try_into().unwrap_or_else(|_| {
            fail(i, index, "bad signature length (want 64 bytes)")
        });
        let sig = Signature::from_bytes(&sig_arr);
        if vk.verify(body.as_bytes(), &sig).is_err() {
            fail(i, index, "Ed25519 signature invalid");
        }
        prev = hash.to_string();
        checked += 1;
    }
    println!(
        "chain valid: {} envelopes verified ({} anchored to {}) (pubkey {})",
        checked,
        anchored,
        anchor_path.map(String::as_str).unwrap_or("<none>"),
        &pub_hex[..pub_hex.len().min(16)]
    );
}

fn fail(i: usize, index: i64, why: &str) -> ! {
    eprintln!("VERIFY FAILED at envelope #{} (index {}): {}", i, index, why);
    process::exit(1);
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect()
}

fn hex_encode(b: &[u8]) -> String {
    b.iter().map(|x| format!("{:02x}", x)).collect()
}
