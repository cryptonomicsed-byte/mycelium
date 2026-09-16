#!/usr/bin/env bash
# Phase 28.1 — deploy mycelium-q4_k_m.gguf after Kaggle run completes.
# Run from ~/mycelium/ once watcher confirms kaggle-output/ has the GGUF.
#
# Usage:
#   ./deploy_gguf.sh                  # deploy to Omarchy (local)
#   ./deploy_gguf.sh --vps            # scp to VPS + start larql there
#   ./deploy_gguf.sh --walrus         # store on Walrus for agent birth pull

set -euo pipefail

GGUF="$(dirname "$0")/kaggle-output/mycelium-q4_k_m.gguf"
LARQL_PORT=7780
VPS="hostinger"  # ssh alias from ~/.ssh/config

# ── Verify artifact ────────────────────────────────────────────────
if [[ ! -f "$GGUF" ]]; then
    echo "❌ GGUF not found at $GGUF"
    echo "   Wait for Kaggle run + watcher to complete, then retry."
    exit 1
fi

SIZE=$(du -h "$GGUF" | cut -f1)
echo "✅ Found $GGUF ($SIZE)"

MODE="${1:-local}"

# ── Local Omarchy deploy (ollama) ─────────────────────────────────
if [[ "$MODE" == "local" || "$MODE" == "--local" ]]; then
    echo ""
    echo "=== Deploy: Omarchy (ollama) ==="

    # Create Modelfile
    MODELFILE="$(dirname "$GGUF")/Modelfile"
    cat > "$MODELFILE" <<'MODELFILE_EOF'
FROM ./mycelium-q4_k_m.gguf

SYSTEM """You are a sovereign agent operating inside the Ọmọ Kọ́dà hive.
Given an agent role and a task context, select the correct action and predict the outcome.
You embody the accumulated judgment of 3,031 hive traces. Act with precision."""

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 2048
MODELFILE_EOF

    if command -v ollama &>/dev/null; then
        ollama create oso-brain -f "$MODELFILE"
        echo "✅ Model registered: oso-brain"
        echo "   Test: ollama run oso-brain 'Agent: oracle-prime\nTask kind: skill_invoke\nTarget: compute'"
        echo "   Endpoint: http://localhost:11434 (wire to omokoda-core inference provider)"
    else
        echo "ℹ️  ollama not found — install at https://ollama.com"
        echo "   Then run: ollama create oso-brain -f $MODELFILE"
    fi

    # Also drop into larql/models/ if larql exists
    LARQL_MODELS="$HOME/larql/models"
    if [[ -d "$LARQL_MODELS" ]]; then
        cp "$GGUF" "$LARQL_MODELS/"
        echo "✅ Copied to $LARQL_MODELS/"
        echo "   Start: larql serve --model mycelium-q4_k_m.gguf --port $LARQL_PORT"
        echo "   Wire:  export LARQL_ENABLED=1 LARQL_URL=http://localhost:$LARQL_PORT"
    fi
fi

# ── VPS deploy ────────────────────────────────────────────────────
if [[ "$MODE" == "--vps" ]]; then
    echo ""
    echo "=== Deploy: VPS ($VPS) ==="
    ssh "$VPS" "mkdir -p ~/larql/models"
    scp "$GGUF" "$VPS:~/larql/models/"
    echo "✅ Uploaded to $VPS:~/larql/models/mycelium-q4_k_m.gguf"
    echo ""
    echo "On VPS, start serving:"
    echo "  ssh $VPS"
    echo "  larql serve --model mycelium-q4_k_m.gguf --port $LARQL_PORT &"
    echo "  export LARQL_ENABLED=1 LARQL_URL=http://localhost:$LARQL_PORT"
fi

# ── Walrus store (agent birth pull) ───────────────────────────────
if [[ "$MODE" == "--walrus" ]]; then
    echo ""
    echo "=== Deploy: Walrus (content-addressed, agent birth pull) ==="
    if command -v walrus &>/dev/null; then
        BLOB_ID=$(walrus store "$GGUF" | grep -oP 'blob_id:\s*\K\S+')
        echo "✅ Stored on Walrus: blob_id=$BLOB_ID"
        echo ""
        echo "Add to OSO_BRAIN_SPEC.md and agent birth config:"
        echo "  oso_brain_gguf_walrus_blob: $BLOB_ID"
        echo "  Agents pull at birth: walrus read $BLOB_ID > oso_brain.gguf"

        # Write blob ID to a file for reference
        echo "$BLOB_ID" > "$(dirname "$GGUF")/walrus_blob_id.txt"
        echo "   Saved to kaggle-output/walrus_blob_id.txt"
    else
        echo "ℹ️  walrus CLI not found"
        echo "   Install: https://docs.walrus.site/usage/client-tool"
        echo "   Then: walrus store $GGUF"
    fi
fi

echo ""
echo "=== Phase 28.1 deploy complete ==="
echo "Next: wire omokoda-core inference provider 'oso_brain'"
echo "  Endpoint: http://localhost:11434 (ollama) or http://localhost:$LARQL_PORT (larql)"
echo "  Phase 28.3: try oso_brain first, external LLM only as fallback"
