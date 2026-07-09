"""
LipSync engine, ported from aiavatar-pi (https://github.com/uezo/aiavatar-pi),
itself a Python port of lipsync.js from AIAvatarKit.

Determines mouth shape (closed/half/open/e/u) from audio RMS and spectral centroid.
No external dependencies required.
"""

import math


class LipSyncEngine:
    def __init__(
        self,
        audio_hz=30,
        cutoff_hz=8.0,
        min_vowel_interval=0.12,
        peak_margin=0.02,
        history_seconds=10,
        rms_queue_max=3,
        peak_decay=0.995,
    ):
        self.audio_hz = audio_hz
        self.min_vowel_interval = min_vowel_interval
        self.peak_margin = peak_margin

        # Mouth opening levels (env thresholds)
        self.levels = [
            {"thresh": 0.0, "shape": "closed"},
            {"thresh": 0.30, "shape": "half"},
            {"thresh": 0.52, "shape": "open"},
        ]

        # Vowel bands (spectral centroid thresholds)
        self.vowel_bands = [
            {"upper": 0.16, "shape": "u"},
            {"upper": 0.20, "shape": "open"},
            {"upper": 1.0, "shape": "e"},
        ]

        # 1-pole LPF coefficient
        self.beta = 1.0 - math.exp(-2.0 * math.pi * cutoff_hz / audio_hz)

        # Online normalization
        self.noise = 1e-4
        self.peak = 1e-3
        self.peak_decay = peak_decay

        # Short-term smoothing
        self.rms_queue = []
        self.rms_queue_max = rms_queue_max
        self.env_lp = 0.0

        # History buffers
        self.env_history = []
        self.centroid_history = []
        self.history_max = int(audio_hz * history_seconds)

        # Auto-tuned thresholds
        self.thresholds = {
            "talk": 0.06,
            "half": 0.30,
            "open": 0.52,
            "u": 0.16,
            "e": 0.20,
        }

        # Vowel state
        self.current_open_shape = "open"
        self.last_vowel_change_t = -999.0

        # Peak detection
        self.e_prev2 = 0.0
        self.e_prev1 = 0.0

        self.mouth_shape = "closed"
        self.env = 0.0
        self.centroid = 0.0

    def update(self, rms, centroid01, t_sec):
        """Feed audio metrics and return current mouth shape string."""
        rms_raw = rms if math.isfinite(rms) else 0.0
        centroid_val = centroid01 if math.isfinite(centroid01) else 0.0
        t = t_sec if math.isfinite(t_sec) else 0.0

        # --- Online normalization ---
        if rms_raw < self.noise + 0.0005:
            self.noise = 0.99 * self.noise + 0.01 * rms_raw
        else:
            self.noise = 0.999 * self.noise + 0.001 * rms_raw

        self.peak = max(rms_raw, self.peak * self.peak_decay)
        denom = max(self.peak - self.noise, 1e-6)
        rms_norm = math.pow(_clamp((rms_raw - self.noise) / denom, 0, 1), 0.5)

        # --- Short-term smoothing ---
        self.rms_queue.append(rms_norm)
        if len(self.rms_queue) > self.rms_queue_max:
            self.rms_queue.pop(0)
        rms_sm = sum(self.rms_queue) / len(self.rms_queue)

        # --- Envelope LPF ---
        self.env_lp += self.beta * (rms_sm - self.env_lp)
        env = _clamp(0.75 * self.env_lp + 0.25 * rms_sm, 0, 1)

        self.env = env
        self.centroid = _clamp(centroid_val, 0, 1)

        # --- History ---
        self.env_history.append(env)
        self.centroid_history.append(self.centroid)
        if len(self.env_history) > self.history_max:
            self.env_history.pop(0)
        if len(self.centroid_history) > self.history_max:
            self.centroid_history.pop(0)

        # --- Auto-update thresholds roughly every second ---
        if (len(self.env_history) > self.audio_hz * 3
                and len(self.env_history) % self.audio_hz == 0):
            self._auto_update_thresholds()

        # --- Mouth level ---
        level_shape = self._pick_level_shape(env)
        mouth_shape = level_shape

        # --- Vowel update (only when env exceeds the open gate) ---
        open_gate = self.thresholds["open"]
        if env >= open_gate:
            is_peak = (
                self.e_prev2 < self.e_prev1
                and self.e_prev1 >= env
                and self.e_prev1 > open_gate + self.peak_margin
            )
            if is_peak and (t - self.last_vowel_change_t) >= self.min_vowel_interval:
                cm = _mean_last(self.centroid_history, 5, self.centroid)
                self.current_open_shape = self._pick_vowel_shape(cm)
                self.last_vowel_change_t = t
            mouth_shape = self.current_open_shape

        self.mouth_shape = mouth_shape
        self.e_prev2 = self.e_prev1
        self.e_prev1 = env

        return self.mouth_shape

    def reset(self):
        """Reset smoothing state (call when all playback ends)."""
        self.mouth_shape = "closed"
        self.env_lp = 0.0
        self.rms_queue.clear()
        self.e_prev2 = 0.0
        self.e_prev1 = 0.0
        self.current_open_shape = "open"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pick_level_shape(self, env):
        shape = self.levels[0]["shape"] if self.levels else "closed"
        for level in self.levels:
            if env >= level["thresh"]:
                shape = level["shape"]
            else:
                break
        return shape

    def _pick_vowel_shape(self, centroid):
        for band in self.vowel_bands:
            if centroid <= band["upper"]:
                return band["shape"]
        return self.vowel_bands[-1]["shape"] if self.vowel_bands else "open"

    def _auto_update_thresholds(self):
        vals = list(self.env_history)
        sorted_vals = sorted(vals)
        k = max(1, int(0.2 * len(sorted_vals)))
        noise_floor = _median(sorted_vals[:k])
        self.thresholds["talk"] = _clamp(noise_floor + 0.05, 0.03, 0.18)

        talk_vals = [v for v in vals if v > self.thresholds["talk"]]
        if len(talk_vals) > 20:
            half = _percentile(talk_vals, 25)
            open_th = _percentile(talk_vals, 58)
            self.thresholds["half"] = max(half, self.thresholds["talk"] + 0.02)
            self.thresholds["open"] = max(open_th, self.thresholds["half"] + 0.05)

            # Update centroid thresholds
            open_mask = [e >= self.thresholds["open"] for e in self.env_history]
            cent_open = [c for c, m in zip(self.centroid_history, open_mask) if m]
            if len(cent_open) <= 20:
                cent_open = [
                    c for c, e in zip(self.centroid_history, self.env_history)
                    if e > self.thresholds["talk"]
                ]

            if len(cent_open) > 20:
                self.thresholds["u"] = _percentile(cent_open, 20)
                self.thresholds["e"] = _percentile(cent_open, 80)

        self._sync_thresholds()

    def _sync_thresholds(self):
        for level in self.levels:
            if level["shape"] == "half":
                level["thresh"] = self.thresholds["half"]
            elif level["shape"] == "open":
                level["thresh"] = self.thresholds["open"]
        self.levels.sort(key=lambda x: x["thresh"])

        for band in self.vowel_bands:
            if band["shape"] == "u":
                band["upper"] = self.thresholds["u"]
            elif band["shape"] == "e":
                band["upper"] = self.thresholds["e"]
        self.vowel_bands.sort(key=lambda x: x["upper"])


# ------------------------------------------------------------------
# Module-level utility functions
# ------------------------------------------------------------------

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _mean_last(arr, n, fallback):
    m = min(n, len(arr))
    if m <= 0:
        return fallback
    return sum(arr[-m:]) / m


def _percentile(arr, p):
    a = sorted(arr)
    idx = (p / 100) * (len(a) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(a) - 1)
    t = idx - lo
    return a[lo] * (1 - t) + a[hi] * t


def _median(a):
    if not a:
        return 0
    mid = len(a) // 2
    return a[mid] if len(a) % 2 else 0.5 * (a[mid - 1] + a[mid])
