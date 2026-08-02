#!/usr/bin/env python3
"""Render a two-voice Happy Birthday with REAL harmony: both voices sounding
at once, each syllable retuned to the actual melody.

Zeev (am_adam) carries the melody in G major starting on D3 -- 146.83 Hz, which
is within a hair of his measured natural F0, so most notes need almost no shift.
Sarina (af_heart) sings a diatonic third above, which keeps her inside +/-9
semitones of her own 235 Hz and keeps BOTH parts above 145 Hz, because the
Whisplay speaker cannot reproduce a low harmony line at all.

Per syllable: synthesize -> measure F0 -> rubberband to the target note and to
the slot length -> concatenate. The two finished tracks are mixed with amix, so
the harmony is genuinely simultaneous rather than alternating.
"""
import json, os, subprocess, sys, urllib.request, wave
import numpy as np

TTS_URL = "https://ollama.sogdiana-gematria.net/piper/tts"
OUT = "/tmp/duet"
BPM = float(os.environ.get("DUET_BPM", "131"))     # reference track's pulse
BEAT = 60.0 / BPM
SR = 24000

# G major, octave 3-4. sol=D3 is the melody's starting note.
N = {"D3": 146.83, "E3": 164.81, "F#3": 185.00, "G3": 196.00, "A3": 220.00,
     "B3": 246.94, "C4": 261.63, "D4": 293.66, "E4": 329.63, "F#4": 369.99}
# Diatonic third above, in G major.
THIRD = {"D3": "F#3", "E3": "G3", "F#3": "A3", "G3": "B3", "A3": "C4",
         "B3": "D4", "C4": "E4", "D4": "F#4"}


def melody(name):
    """(syllable, melody note, beats). Standard 3/4, 2 bars per phrase."""
    p = lambda a, b: [("Hap", "D3", .5), ("py", "D3", .5), ("birth", "E3", 1),
                      ("day", "D3", 1), (a, "G3", 1), (b, "F#3", 2)]
    out = p("to", "you")
    out += [("Hap", "D3", .5), ("py", "D3", .5), ("birth", "E3", 1),
            ("day", "D3", 1), ("to", "A3", 1), ("you", "G3", 2)]
    # "dear <name>" -- the high D4 is the peak of the song.
    syl = name.split()
    out += [("Hap", "D3", .5), ("py", "D3", .5), ("birth", "D4", 1),
            ("day", "B3", 1), ("dear", "G3", 1)]
    out += [(syl[0], "F#3", 2 if len(syl) == 1 else 1)]
    if len(syl) > 1:
        out += [(syl[1], "E3", 1)]
    out += [("Hap", "C4", .5), ("py", "C4", .5), ("birth", "B3", 1),
            ("day", "G3", 1), ("to", "A3", 1), ("you", "G3", 2)]
    return out


_cache = {}


def synth(text, voice):
    """Kokoro WAV bytes for one syllable, cached -- syllables repeat heavily."""
    key = (text.lower(), voice)
    if key in _cache:
        return _cache[key]
    path = f"{OUT}/syl_{voice}_{abs(hash(text.lower()))}.wav"
    if not os.path.exists(path):
        body = json.dumps({"text": text, "voice": voice}).encode()
        req = urllib.request.Request(
            TTS_URL, data=body,
            headers={"Content-Type": "application/json", "X-Zeev-Key": KEY})
        with open(path, "wb") as f:
            f.write(urllib.request.urlopen(req, timeout=90).read())
    _cache[key] = path
    return path


def read(path):
    w = wave.open(path)
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return x.astype(np.float64) / 32768.0, w.getframerate()


def f0_of(path):
    """Median F0 over the voiced part, by autocorrelation."""
    x, sr = read(path)
    n = int(0.04 * sr)
    if len(x) < n * 2:
        return None
    est = []
    for j in range(0, len(x) - n, n // 2):
        s = x[j:j + n]
        if np.sqrt(np.mean(s ** 2)) < 0.02:
            continue
        s = (s - s.mean()) * np.hanning(n)
        ac = np.correlate(s, s, "full")[n - 1:]
        lo, hi = int(sr / 400), int(sr / 70)
        if hi >= len(ac):
            continue
        peak = lo + int(np.argmax(ac[lo:hi]))
        if ac[peak] > 0.3 * ac[0]:
            est.append(sr / peak)
    return float(np.median(est)) if est else None


def f0_near(path, target):
    """F0 of the sustained middle of a segment, searched only AROUND the target.

    Two separate failures made the open-loop estimate unusable: a window that
    catches a consonant onset returns nonsense, and an unconstrained lag search
    picks the octave below about a third of the time (measured 122 Hz where the
    signal was plainly 245). Both vanish once the search is bounded to a
    tritone either side of the note we are actually aiming at.
    """
    x, sr = read(path)
    if len(x) < 1024:
        return None
    a, b = int(len(x) * 0.25), int(len(x) * 0.85)
    s = x[a:b]
    if len(s) < 512 or np.sqrt(np.mean(s ** 2)) < 0.01:
        return None
    s = (s - s.mean()) * np.hanning(len(s))
    ac = np.correlate(s, s, "full")[len(s) - 1:]
    lo, hi = int(sr / (target * 1.45)), int(sr / (target * 0.69))
    if hi >= len(ac) or lo < 1:
        return None
    return sr / (lo + int(np.argmax(ac[lo:hi])))


def retune(path, target, out):
    """One corrective pass: measure what we actually produced, fix the residual."""
    got = f0_near(path, target)
    if not got or abs(got / target - 1.0) < 0.02:
        return path, got
    corr = max(0.5, min(2.0, target / got))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path,
                    "-af", f"rubberband=pitch={corr:.5f}:transients=smooth:"
                           f"detector=soft:phase=independent",
                    "-ar", str(SR), "-ac", "1", out], check=True)
    return out, f0_near(out, target)


def render_part(items, voice, note_of, out_path):
    """One sung line: each syllable retuned and fitted to its beat slot."""
    track = []
    for i, (text, mnote, beats) in enumerate(items):
        src = synth(text, voice)
        x, sr = read(src)
        slot = int(round(beats * BEAT * sr))
        target = N[note_of(mnote)]
        base = f0_of(src) or target
        pitch = target / base
        # Sung notes hold; speech does not. Fit the syllable into ~70% of the
        # slot and let the rest ring out as silence, rather than stretching a
        # 0.5s syllable across a 2-beat note, which smears into a drone.
        want = min(len(x) / sr, max(0.18, beats * BEAT * 0.72))
        tempo = max(0.35, min(2.6, (len(x) / sr) / want))
        seg = f"{OUT}/seg_{voice}_{i}.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-af", f"rubberband=pitch={pitch:.5f}:tempo={tempo:.5f}:transients=smooth:"
                    f"detector=soft:phase=independent,"
                    f"afade=t=out:st={max(0.0, want-0.06):.3f}:d=0.06",
             "-ar", str(SR), "-ac", "1", seg], check=True)
        seg, got = retune(seg, target, f"{OUT}/fix_{voice}_{i}.wav")
        y, _ = read(seg)
        buf = np.zeros(slot)
        m = min(len(y), slot)
        buf[:m] = y[:m]
        track.append(buf)
        cents = 1200 * np.log2(got / target) if got else float("nan")
        print(f"   {voice:8} {text:6} {note_of(mnote):3} want {target:6.1f} "
              f"got {got or 0:6.1f}Hz ({cents:+5.0f} cents)", flush=True)
    sig = np.concatenate(track)
    sig = sig / (np.max(np.abs(sig)) + 1e-9) * 0.85
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((sig * 32767).astype(np.int16).tobytes())
    return out_path


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "Alex"
    os.makedirs(OUT, exist_ok=True)
    items = melody(name)
    print(f"Rendering duet for {name}: {len(items)} syllables at {BPM:.0f} BPM")
    lead = render_part(items, "am_adam", lambda n: n, f"{OUT}/zeev.wav")
    harm = render_part(items, "af_heart", lambda n: THIRD[n], f"{OUT}/sarina.wav")
    mixed = f"{OUT}/duet_{name.lower()}.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", lead, "-i", harm,
         "-filter_complex",
         "[0:a]volume=1.0[a];[1:a]volume=0.9,adelay=12|12[b];"
         "[a][b]amix=inputs=2:duration=longest:normalize=0,"
         "aresample=24000,alimiter=limit=0.95",
         mixed], check=True)
    x, sr = read(mixed)
    print(f"\nMixed -> {mixed}  ({len(x)/sr:.1f}s, peak {np.max(np.abs(x)):.2f})")
    return mixed


def _load_key():
    """Key from the environment, else the .env beside this file's repo root."""
    key = os.environ.get("REMOTE_PIPER_KEY") or os.environ.get("BOSGAME_KEY")
    if key:
        return key
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    for line in open(env):
        line = line.strip()
        if line.startswith(("REMOTE_PIPER_KEY=", "BOSGAME_KEY=")):
            key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("no REMOTE_PIPER_KEY / BOSGAME_KEY available")
    return key


if __name__ == "__main__":
    KEY = _load_key()
    TTS_URL = os.environ.get("REMOTE_PIPER_URL", TTS_URL)
    main()
