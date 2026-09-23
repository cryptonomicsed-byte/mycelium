#!/usr/bin/env bash
# submit_finetune.sh — Upload training data and submit QLoRA job to GPU.ai
#
# PREREQUISITES:
#   1. Fund the GPU.ai account at https://api.gpu.ai (needs ~$5 minimum)
#   2. Run: python3 prepare_finetune.py  (creates finetune_dataset.jsonl)
#   3. Then run this script
#
# GPU.ai fine-tuning is OpenAI-compatible: /v1/files + /v1/fine_tuning/jobs

set -euo pipefail

API_KEY="${GPUAI_API_KEY:?GPUAI_API_KEY is not set -- export it from the vault; never commit the key}"
BASE="https://api.gpu.ai/v1"
DATASET="$(dirname "$0")/finetune_dataset.jsonl"
# Qwen3.5-9B is the best cost/capability match for behavior distillation at 3K examples
MODEL="qwen3.5-9b"

echo "=== Mycelium QLoRA Fine-Tune Submission ==="
echo "Dataset: $DATASET ($(wc -l < "$DATASET") examples)"
echo "Base model: $MODEL"
echo ""

if [[ ! -f "$DATASET" ]]; then
    echo "ERROR: $DATASET not found. Run: python3 prepare_finetune.py"
    exit 1
fi

# Step 1: Upload training file
echo "[1/3] Uploading training file..."
UPLOAD_RESP=$(curl -sf "$BASE/files" \
    -H "Authorization: Bearer $API_KEY" \
    -F "purpose=fine-tune" \
    -F "file=@$DATASET")

echo "Upload response: $UPLOAD_RESP"
FILE_ID=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "File ID: $FILE_ID"

# Step 2: Submit fine-tuning job
echo ""
echo "[2/3] Submitting fine-tuning job..."
JOB_RESP=$(curl -sf -X POST "$BASE/fine_tuning/jobs" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"model\": \"$MODEL\",
        \"training_file\": \"$FILE_ID\",
        \"hyperparameters\": {
            \"n_epochs\": 3,
            \"batch_size\": 8,
            \"learning_rate_multiplier\": 2
        },
        \"suffix\": \"mycelium-hive\"
    }")

echo "Job response: $JOB_RESP"
JOB_ID=$(echo "$JOB_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Job ID: $JOB_ID"

# Step 3: Poll until done
echo ""
echo "[3/3] Polling job status (check every 60s)..."
while true; do
    STATUS_RESP=$(curl -sf "$BASE/fine_tuning/jobs/$JOB_ID" \
        -H "Authorization: Bearer $API_KEY")
    STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))")
    FINE_TUNED=$(echo "$STATUS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('fine_tuned_model',''))")
    echo "  $(date -u +%H:%M:%SZ)  status=$STATUS  model=$FINE_TUNED"

    if [[ "$STATUS" == "succeeded" ]]; then
        echo ""
        echo "=== SUCCESS ==="
        echo "Fine-tuned model: $FINE_TUNED"
        echo ""
        echo "Next: export GGUF and deploy to Omarchy:"
        echo "  1. Download weights from GPU.ai dashboard"
        echo "  2. llama.cpp convert: python3 convert_hf_to_gguf.py --outtype q4_k_m"
        echo "  3. ollama create mycelium-brain -f Modelfile"
        break
    elif [[ "$STATUS" == "failed" || "$STATUS" == "cancelled" ]]; then
        echo "Job $STATUS. Full response:"
        echo "$STATUS_RESP" | python3 -m json.tool
        exit 1
    fi
    sleep 60
done
