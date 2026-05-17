#!/usr/bin/env bash
# Zeev setup — run once as the ragnar user (needs sudo)
set -e

ZEEV_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$ZEEV_DIR/bin"
MODEL_DIR="$ZEEV_DIR/models"
MODEL_FILE="$MODEL_DIR/qwen2.5-1.5b-instruct-q4_k_m.gguf"
LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/b9186/llama-b9186-bin-ubuntu-arm64.tar.gz"
MODEL_URL="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"

echo ""
echo "=== Zeev Setup ==="
echo ""

# ── 1. Swap ───────────────────────────────────────────────────────────────────
if [ ! -f /swapfile ]; then
  echo "[1/5] Creating 3 GB swap file on SD card..."
  sudo fallocate -l 3G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  echo "      Swap enabled."
else
  echo "[1/5] Swap file already exists — skipping."
fi

# ── 2. System packages ────────────────────────────────────────────────────────
echo "[2/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  libgomp1 libopenblas0-pthread python3-requests wget tar 2>/dev/null || \
sudo apt-get install -y --no-install-recommends \
  libgomp1 libopenblas0 python3-requests wget tar

# ── 3. llama.cpp binary ───────────────────────────────────────────────────────
if [ ! -f "$BIN_DIR/llama-server" ]; then
  echo "[3/5] Downloading llama.cpp ARM64 binary..."
  TMP=$(mktemp -d)
  wget -q --show-progress -O "$TMP/llama.tar.gz" "$LLAMA_URL"
  tar -xzf "$TMP/llama.tar.gz" -C "$TMP"
  find "$TMP" -name "llama-server" -exec cp {} "$BIN_DIR/llama-server" \;
  find "$TMP" -name "*.so*" -exec cp {} "$BIN_DIR/" \;
  chmod +x "$BIN_DIR/llama-server"
  rm -rf "$TMP"
  echo "      llama-server installed."
else
  echo "[3/5] llama-server already present — skipping."
fi

# ── 4. Model ──────────────────────────────────────────────────────────────────
if [ ! -f "$MODEL_FILE" ]; then
  echo "[4/5] Downloading Qwen2.5-1.5B model (~930 MB)..."
  echo "      This may take several minutes on a slow connection."
  wget -q --show-progress -c -O "$MODEL_FILE" "$MODEL_URL"
  echo "      Model downloaded."
else
  echo "[4/5] Model already present — skipping."
fi

# ── 5. Permissions ────────────────────────────────────────────────────────────
echo "[5/5] Finalising..."
chmod +x "$ZEEV_DIR/zeev.py"
mkdir -p "$ZEEV_DIR/data"

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start Zeev with:"
echo "  python3 $ZEEV_DIR/zeev.py"
echo ""
echo "Or add this alias to ~/.bashrc:"
echo "  alias zeev='python3 $ZEEV_DIR/zeev.py'"
echo ""
