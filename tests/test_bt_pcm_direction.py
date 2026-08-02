"""Choosing a Bluetooth OUTPUT device.

A phone connects to the Pi as an audio SOURCE, so its a2dp PCM is capture-only
-- something to listen to, not something to play through. Matching
`PROFILE=a2dp` without checking direction picked Alex's S22 Ultra as the
"headphones" the moment he paired it to place a call. Every aplay to it then
failed (`keepalive: aplay: exit status 1`, once every 20 seconds), the device
stayed up and went completely silent, and it read as a crash.
"""
import subprocess
import pytest

# Exactly what `bluealsa-aplay --list-pcms` printed on the Pi, 2026-08-02.
PHONE_ONLY = """bluealsa:DEV=4C:2E:5E:8D:9A:86,PROFILE=sco,SRV=org.bluealsa
    Alexander's S22 Ultra, trusted phone, playback
    SCO (): S16_LE 1 channel 0 Hz
bluealsa:DEV=4C:2E:5E:8D:9A:86,PROFILE=sco,SRV=org.bluealsa
    Alexander's S22 Ultra, trusted phone, capture
    SCO (): S16_LE 1 channel 0 Hz
bluealsa:DEV=4C:2E:5E:8D:9A:86,PROFILE=a2dp,SRV=org.bluealsa
    Alexander's S22 Ultra, trusted phone, capture
    A2DP (SBC): S16_LE 1 channel 44100 Hz
"""

REAL_HEADPHONES = """bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp,SRV=org.bluealsa
    Jaybird Vista, trusted audio-headphones, playback
    A2DP (SBC): S16_LE 2 channels 44100 Hz
"""

BOTH = PHONE_ONLY + REAL_HEADPHONES


@pytest.fixture
def bt(zeev, monkeypatch):
    monkeypatch.setattr(zeev, "_BT_AUDIO_DEV", "")
    monkeypatch.setattr(zeev, "_BT_RATE", 44100)
    monkeypatch.setattr(zeev, "_BT_CHANNELS", 1)
    monkeypatch.setattr(zeev, "_audio", None)

    def listing(text):
        monkeypatch.setattr(
            zeev.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=text, stderr=""))
    return zeev, listing


def test_a_phone_is_not_a_speaker(bt):
    """The live failure: the S22's only a2dp PCM is capture."""
    z, listing = bt
    listing(PHONE_ONLY)
    z.bt_detect_connected()
    assert z._BT_AUDIO_DEV == "", "a capture-only PCM must never become the output"


def test_real_headphones_are_still_detected(bt):
    z, listing = bt
    listing(REAL_HEADPHONES)
    z.bt_detect_connected()
    assert z._BT_AUDIO_DEV == "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp"
    assert z._BT_CHANNELS == 2 and z._BT_RATE == 44100


def test_headphones_win_when_a_phone_is_also_connected(bt):
    """Both at once is the normal state during a call: the phone carries the
    call, the headphones carry Zeev."""
    z, listing = bt
    listing(BOTH)
    z.bt_detect_connected()
    assert z._BT_AUDIO_DEV == "bluealsa:DEV=AA:BB:CC:DD:EE:FF,PROFILE=a2dp"


def test_nothing_connected_leaves_the_speaker_alone(bt):
    z, listing = bt
    listing("")
    z.bt_detect_connected()
    assert z._BT_AUDIO_DEV == ""
