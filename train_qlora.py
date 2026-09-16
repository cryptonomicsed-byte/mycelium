#!/usr/bin/env python3
"""
Phase 28.1 — Mycelium QLoRA fine-tune runner.

Runs on GPU.ai (A40 $0.49/hr) or any provider supporting the OpenAI
fine-tuning API (/v1/files + /v1/fine_tuning/jobs).

Usage:
    python train_qlora.py                        # uses GPU.ai + finetune_dataset.jsonl
    python train_qlora.py --dataset my.jsonl     # custom dataset
    python train_qlora.py --base Qwen/Qwen2.5-3B-Instruct
    python train_qlora.py --dry-run              # validate + cost estimate only

Output:
    fine_tune_job.json   — job metadata (id, status, model)
    After completion:    GGUF export + llama.cpp deploy instructions printed

Environment:
    GPUAI_API_KEY   — GPU.ai key (from ~/.claude/projects/.../memory/reference_gpuai.md)
    BASE_URL        — override API base (default: https://api.gpu.ai/v1)

Phase 28.1 spec: sovereign-eco-blueprint/specs/OSO_BRAIN_SPEC.md
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request
import urllib.error

DEFAULT_DATASET  = pathlib.Path(__file__).parent / "finetune_dataset.jsonl"
DEFAULT_BASE     = "Qwen/Qwen2.5-3B-Instruct"
DEFAULT_BASE_URL = "https://api.gpu.ai/v1"
JOB_OUT          = pathlib.Path(__file__).parent / "fine_tune_job.json"

# QLoRA hyperparams — tuned for 3B on A40 24 GB
HYPERPARAMS = {
    "n_epochs":          3,
    "batch_size":        4,
    "learning_rate_multiplier": 0.1,
    # GPU.ai extension fields (ignored by other providers)
    "lora_r":           16,
    "lora_alpha":       32,
    "lora_dropout":     0.05,
    "warmup_ratio":     0.03,
    "max_seq_length":  512,
}


def die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def api_key() -> str:
    key = os.environ.get("GPUAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        die(
            "Set GPUAI_API_KEY (GPU.ai key is in your account vault).\n"
            "  export GPUAI_API_KEY=gpuai_live_yxOFo45nCboRVOU6ak8lduNJ"
        )
    return key


def base_url() -> str:
    return os.environ.get("BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def http_post(path: str, body: dict, key: str, content_type="application/json") -> dict:
    url = base_url() + path
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        die(f"HTTP {e.code} from {path}:\n{body_text}")


def http_post_multipart(path: str, filename: str, data: bytes, key: str) -> dict:
    """Minimal multipart/form-data upload — stdlib only."""
    boundary = "----WaggleBoundary7777"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="purpose"\r\n\r\n'
        f"fine-tune\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/jsonl\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()

    url = base_url() + path
    req = urllib.request.Request(
        url, data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code} uploading file:\n{e.read().decode()}")


def http_get(path: str, key: str) -> dict:
    url = base_url() + path
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code} from {path}:\n{e.read().decode()}")


def validate_dataset(path: pathlib.Path) -> int:
    """Returns example count; dies on format errors."""
    count = 0
    with open(path) as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError as e:
                die(f"Line {i}: invalid JSON — {e}")
            if "messages" not in ex:
                die(f"Line {i}: missing 'messages' key")
            msgs = ex["messages"]
            roles = [m.get("role") for m in msgs]
            if "user" not in roles or "assistant" not in roles:
                die(f"Line {i}: messages must include 'user' and 'assistant' roles")
            count += 1
    return count


def estimate_cost(n_examples: int, n_epochs: int) -> None:
    # A40 at $0.49/hr. Rough: 3B QLoRA ≈ 2,000 examples/min on A40.
    tokens_per_example = 200  # conservative average for our traces
    total_tokens = n_examples * tokens_per_example * n_epochs
    minutes = (n_examples * n_epochs) / 2000
    cost = (minutes / 60) * 0.49
    print(f"  Examples : {n_examples:,}")
    print(f"  Epochs   : {n_epochs}")
    print(f"  ~Tokens  : {total_tokens:,}")
    print(f"  ~Time    : {minutes:.0f} min on A40")
    print(f"  ~Cost    : ${cost:.2f} at $0.49/hr (A40)")


def poll_job(job_id: str, key: str, interval: int = 30) -> dict:
    print(f"\nPolling job {job_id} every {interval}s (ctrl+c to detach — job keeps running)...")
    while True:
        job = http_get(f"/fine_tuning/jobs/{job_id}", key)
        status = job.get("status", "unknown")
        finished_at = job.get("finished_at")
        print(f"  [{time.strftime('%H:%M:%S')}] status={status}", end="")
        if finished_at:
            print(f"  finished_at={finished_at}")
        else:
            print()
        if status in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 28.1 — Mycelium QLoRA fine-tune")
    ap.add_argument("--dataset",  default=str(DEFAULT_DATASET), help="Path to .jsonl dataset")
    ap.add_argument("--base",     default=DEFAULT_BASE,         help="Base model ID")
    ap.add_argument("--epochs",   type=int, default=HYPERPARAMS["n_epochs"])
    ap.add_argument("--dry-run",  action="store_true",          help="Validate + estimate only")
    ap.add_argument("--no-poll",  action="store_true",          help="Submit and exit without polling")
    args = ap.parse_args()

    dataset = pathlib.Path(args.dataset)
    if not dataset.exists():
        die(f"Dataset not found: {dataset}\nRun prepare_finetune.py first.")

    print("=== Phase 28.1 — Mycelium QLoRA Fine-Tune ===")
    print(f"  Dataset : {dataset}")
    print(f"  Base    : {args.base}")
    print(f"  API     : {base_url()}")
    print()

    print("Validating dataset...")
    n = validate_dataset(dataset)
    print(f"  ✅ {n:,} valid examples")
    print()

    HYPERPARAMS["n_epochs"] = args.epochs
    print("Cost estimate:")
    estimate_cost(n, args.epochs)
    print()

    if args.dry_run:
        print("--dry-run: stopping here. Remove flag to submit.")
        return

    key = api_key()

    # 1. Upload file
    print("Uploading dataset...")
    raw = dataset.read_bytes()
    file_resp = http_post_multipart("/files", dataset.name, raw, key)
    file_id = file_resp.get("id")
    if not file_id:
        die(f"No file id in response: {file_resp}")
    print(f"  ✅ file_id = {file_id}")

    # 2. Create fine-tune job
    print("Creating fine-tune job...")
    job_body = {
        "training_file": file_id,
        "model":         args.base,
        "hyperparameters": HYPERPARAMS,
        "suffix":        "oso-brain-v1",
    }
    job = http_post("/fine_tuning/jobs", job_body, key)
    job_id = job.get("id")
    if not job_id:
        die(f"No job id in response: {job}")

    JOB_OUT.write_text(json.dumps(job, indent=2))
    print(f"  ✅ job_id = {job_id}")
    print(f"  Saved to {JOB_OUT}")
    print()

    if args.no_poll:
        print(f"--no-poll: job submitted. Check status:")
        print(f"  python train_qlora.py --status {job_id}")
        return

    # 3. Poll to completion
    result = poll_job(job_id, key)
    status = result.get("status")
    fine_tuned_model = result.get("fine_tuned_model", "")

    JOB_OUT.write_text(json.dumps(result, indent=2))

    if status == "succeeded":
        print(f"\n✅ Fine-tune complete: {fine_tuned_model}")
        print()
        print("=== Next: export GGUF and deploy ===")
        print("1. Download the model adapter from GPU.ai dashboard")
        print("2. Merge with base:")
        print(f"   python -c \"")
        print(f"     from peft import PeftModel")
        print(f"     from transformers import AutoModelForCausalLM, AutoTokenizer")
        print(f"     m = AutoModelForCausalLM.from_pretrained('{args.base}')")
        print(f"     m = PeftModel.from_pretrained(m, './adapter')")
        print(f"     m.merge_and_unload().save_pretrained('./merged')\"")
        print("3. Convert to GGUF:")
        print("   python llama.cpp/convert_hf_to_gguf.py ./merged --outfile oso_brain_v1.gguf")
        print("4. Quantize:")
        print("   ./llama.cpp/quantize oso_brain_v1.gguf oso_brain_v1_q4.gguf Q4_K_M")
        print("5. Upload to Walrus:")
        print("   walrus store oso_brain_v1_q4.gguf  # content-addressed, agents pull at birth")
        print("6. Deploy on Omarchy:")
        print("   ollama create oso-brain -f Modelfile  # GGUF → ollama → localhost:11434")
        print()
        print("Phase 28.1 complete. Wire omokoda-core inference provider 'oso_brain' to :11434.")
    else:
        print(f"\n❌ Job ended with status={status}")
        error = result.get("error", {})
        if error:
            print(f"   {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
