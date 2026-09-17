#!/data/data/com.termux/files/usr/bin/bash
# Mycelium QLoRA fine-tune — fully headless Kaggle run (no browser after step 0)
set -euo pipefail
M="$(cd "$(dirname "$0")" && pwd)"

# 0. ONE-TIME browser step: kaggle.com -> Account -> Create New API Token
#    -> saves kaggle.json. Then: mkdir -p ~/.kaggle && mv ~/Downloads/kaggle.json ~/.kaggle/
#    Set the account username in BOTH metadata files (replace <KAGGLE_USERNAME>).

# 1. Upload the dataset
kaggle datasets create -p "$M/kaggle-upload" --dir-mode zip || \
  kaggle datasets version -p "$M/kaggle-upload" -m "update traces"

# 2. Push + run the notebook (T4x2, internet on, per kernel-metadata.json)
kaggle kernels push -p "$M/kaggle-kernel"

# 3. Poll until done
kaggle kernels status "$(python3 -c "import json;print(json.load(open('$M/kaggle-kernel/kernel-metadata.json'))['id'])")"

# 4. Pull the GGUF + adapter back down
kaggle kernels output "$(python3 -c "import json;print(json.load(open('$M/kaggle-kernel/kernel-metadata.json'))['id'])")" -p "$M/kaggle-output"

echo "GGUF -> $M/kaggle-output/ ; deploy to ~/larql/models/ and run:"
echo "  larql serve --model mycelium-q4_k_m.gguf --port 7780"
echo "  export LARQL_ENABLED=1 LARQL_URL=http://localhost:7780"
