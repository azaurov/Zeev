#!/usr/bin/env bash
# Zeev setup — run once on a fresh Raspberry Pi
set -e

ZEEV_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "=== Zeev Setup ==="
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/3] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends python3-requests

# ── 2. API keys ───────────────────────────────────────────────────────────────
echo ""
echo "[2/3] API keys"
echo ""

add_key() {
  local var="$1" label="$2" url="$3"
  if grep -q "export $var=" ~/.bashrc 2>/dev/null; then
    echo "      $var already set in ~/.bashrc — skipping."
  else
    echo "      Get your key at: $url"
    read -rp "      Enter $label API key (or press Enter to skip): " key
    if [ -n "$key" ]; then
      echo "export $var=\"$key\"" >> ~/.bashrc
      echo "      $var added to ~/.bashrc."
    else
      echo "      Skipped. Add manually: export $var=\"your_key\" >> ~/.bashrc"
    fi
  fi
}

add_key "GROQ_API_KEY"   "Groq"   "https://console.groq.com/keys"
echo ""
add_key "TAVILY_API_KEY" "Tavily" "https://app.tavily.com"

# ── 3. Permissions + data dir ─────────────────────────────────────────────────
echo ""
echo "[3/3] Finalising..."
chmod +x "$ZEEV_DIR/zeev.py"
mkdir -p "$ZEEV_DIR/data"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "Reload your shell, then start Zeev:"
echo "  source ~/.bashrc"
echo "  python3 $ZEEV_DIR/zeev.py"
echo ""
echo "Optional alias:"
echo "  echo \"alias zeev='python3 $ZEEV_DIR/zeev.py'\" >> ~/.bashrc"
echo ""
