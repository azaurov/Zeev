#!/usr/bin/env bash
# Zeev Bluetooth setup — pair a speaker/headset and configure audio
set -e

echo ""
echo "=== Zeev Bluetooth Setup ==="
echo ""

# ── 1. Packages ───────────────────────────────────────────────────────────────
echo "[1/3] Installing packages..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends bluez bluez-tools espeak-ng

# Bluetooth audio backend — detect what's running
if systemctl is-active --quiet pipewire 2>/dev/null || \
   dpkg -l pipewire 2>/dev/null | grep -q "^ii"; then
  echo "      Detected PipeWire — installing Bluetooth audio support..."
  sudo apt-get install -y --no-install-recommends \
    pipewire-audio libspa-0.2-bluetooth wireplumber
elif command -v pulseaudio &>/dev/null; then
  echo "      Detected PulseAudio — installing Bluetooth module..."
  sudo apt-get install -y --no-install-recommends pulseaudio-module-bluetooth
  pulseaudio -k 2>/dev/null || true
else
  echo "      Installing BlueALSA..."
  sudo apt-get install -y --no-install-recommends bluealsa
  sudo systemctl enable bluealsa
  sudo systemctl start bluealsa
fi

# ── 2. Enable Bluetooth service ───────────────────────────────────────────────
echo "[2/3] Enabling Bluetooth service..."
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
sleep 1

# ── 3. Pair device ────────────────────────────────────────────────────────────
echo ""
echo "[3/3] Pair your Bluetooth speaker or headset"
echo ""
echo "  In the bluetoothctl prompt:"
echo ""
echo "    power on"
echo "    agent on"
echo "    scan on"
echo ""
echo "  Wait for your device to appear, then:"
echo ""
echo "    scan off"
echo "    pair XX:XX:XX:XX:XX:XX      ← use your device's MAC"
echo "    trust XX:XX:XX:XX:XX:XX"
echo "    connect XX:XX:XX:XX:XX:XX"
echo "    exit"
echo ""
read -rp "  Press Enter to open bluetoothctl..."
bluetoothctl

# ── Set as default audio output ───────────────────────────────────────────────
echo ""
echo "Setting default audio output..."
echo ""

if command -v wpctl &>/dev/null; then
  echo "  PipeWire detected. To set your BT device as default speaker:"
  echo "    wpctl status                        ← find your device ID"
  echo "    wpctl set-default <id>"
elif command -v pactl &>/dev/null; then
  echo "  PulseAudio detected. To set your BT device as default speaker:"
  echo "    pactl list sinks short              ← find your device name"
  echo "    pactl set-default-sink <name>"
fi

# ── Test ──────────────────────────────────────────────────────────────────────
echo ""
echo "=== Setup complete ==="
echo ""
echo "Test your speaker:"
echo "  espeak-ng 'Hello, I am Zeev'"
echo ""
echo "In the Zeev terminal, use /tts to toggle speech output."
echo "Use /bt to connect or switch devices."
echo ""
