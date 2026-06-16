#!/usr/bin/env python3
"""Smoke test: verify phone is connected via HFP/SCO and show negotiated codec rate."""

import re
import subprocess
import sys


def list_pcms() -> str:
    result = subprocess.run(
        ["bluealsa-aplay", "--list-pcms"],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout


def parse_sco_devices(output: str) -> list[dict]:
    devices = []
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if "PROFILE=sco" not in line:
            continue
        mac_m = re.search(r'DEV=([0-9A-Fa-f:]{17})', line)
        if not mac_m:
            continue
        mac = mac_m.group(1)
        name = ""
        direction = "unknown"
        rate = 0
        codec = "unknown"
        # Next lines: device name (has direction), then format line (has Hz)
        for j in range(i + 1, min(i + 4, len(lines))):
            stripped = lines[j].strip()
            if not stripped:
                continue
            if not name and not stripped.startswith("SCO"):
                # "Alexander's S22 Ultra, trusted phone, playback"
                parts = [p.strip() for p in stripped.split(",")]
                name = parts[0]
                for p in parts:
                    if "playback" in p:
                        direction = "playback"
                    elif "capture" in p:
                        direction = "capture"
            rate_m = re.search(r'(\d+)\s+Hz', stripped)
            if rate_m:
                rate = int(rate_m.group(1))
            if "mSBC" in stripped or "LC3" in stripped:
                codec = "mSBC"
            elif "CVSD" in stripped or rate == 8000:
                codec = "CVSD"
            elif rate == 16000:
                codec = "mSBC"
        devices.append({"mac": mac, "name": name, "direction": direction,
                        "rate": rate, "codec": codec})
    return devices


def check_dbus(mac: str) -> str:
    try:
        import dbus
        bus = dbus.SystemBus()
        path = "/org/bluealsa/hci0/dev_" + mac.replace(":", "_") + "/rfcomm"
        obj = bus.get_object("org.bluealsa", path)
        dbus.Interface(obj, "org.bluealsa.RFCOMM1")
        return "OK"
    except ImportError:
        return "MISSING (run: sudo apt install python3-dbus)"
    except Exception as e:
        return f"ERROR: {e}"


def check_webrtcvad() -> str:
    try:
        import webrtcvad  # noqa: F401
        webrtcvad.Vad(2)  # actually exercise the C extension
        return "OK"
    except ImportError as e:
        return f"MISSING: {e}"
    except Exception as e:
        return f"INSTALLED but broken: {e}"


def main():
    print("=== Zeev BT/HFP smoke test ===\n")

    output = list_pcms()
    if not output.strip():
        print("ERROR: bluealsa-aplay returned no output. Is BlueALSA running?")
        print("  sudo systemctl status bluealsa")
        sys.exit(1)

    devices = parse_sco_devices(output)
    if not devices:
        print("NO SCO devices found.")
        print("Check that:")
        print("  1. BlueALSA runs with -p hfp-hf  (ps aux | grep bluealsa)")
        print("  2. Phone is connected and accepted the HFP pairing")
        print("\nRaw bluealsa-aplay output:")
        print(output)
        sys.exit(1)

    seen_macs = set()
    for dev in devices:
        if dev["mac"] in seen_macs:
            continue
        seen_macs.add(dev["mac"])
        rate_str = f"{dev['rate']} Hz" if dev["rate"] > 0 else "0 Hz (negotiated at call time)"
        codec_str = dev["codec"] if dev["rate"] > 0 else "pending"
        print(f"Phone  : {dev['name']}")
        print(f"MAC    : {dev['mac']}")
        print(f"Rate   : {rate_str}")
        print(f"Codec  : {codec_str}")

    print()
    mac = devices[0]["mac"]

    playback = any(d["direction"] == "playback" for d in devices)
    capture  = any(d["direction"] == "capture"  for d in devices)
    print(f"SCO playback : {'YES' if playback else 'NO'}")
    print(f"SCO capture  : {'YES' if capture  else 'NO'}")

    print(f"\nDBus RFCOMM  : {check_dbus(mac)}")
    print(f"webrtcvad    : {check_webrtcvad()}")

    print()
    if playback and capture:
        print("All clear. You can now say 'call <number>' in Zeev.")
    else:
        print("WARNING: both SCO playback and capture are needed.")


if __name__ == "__main__":
    main()
