#!/usr/bin/env python3
"""
Turns raw battery_log.db samples (see battery_log.py) into a short, human
-readable power-usage insight: current discharge/charge rate and an ETA.

Read-only, no writes -- safe to import from ragnarok's zeev_status_check.py,
which runs inside a live PHP admin request and must finish in a few seconds.
A small SQLite SELECT over a table capped at 30 days of 5-min samples
(~8,600 rows) comfortably meets that.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "battery_log.db"

# A rate estimate needs enough spread to not be dominated by PiSugar's own
# read noise (level moves in ~1% steps) -- 20 min / 3 samples is the
# smallest window where a real few-%/hr drain reads as more than one step.
_MIN_WINDOW_MIN = 20
_MIN_SAMPLES = 3
_RATE_WINDOW_HOURS = 3  # how far back to look for a same-charging-state run

# "What's eating the battery" -- idle vs. active discharge cost, from real
# face-state ground truth (idle_sec/active_sec, written by battery_log.py
# from zeev.py's face_state_totals.json) rather than a CPU-time guess, which
# can't tell always-on background housekeeping (wake-word scoring, WM8960
# keepalive) apart from Zeev actually doing something.
_IDLE_ACTIVE_LOOKBACK_DAYS = 7
# A consecutive pair spanning more than this got interrupted by something
# (a reboot, the timer missing a tick) -- its level delta no longer reflects
# steady drain over that gap, so it would blend unrelated time into a rate.
_MAX_PAIR_GAP_MIN = 10
# Don't report a bucket average built from too few samples to mean anything.
_MIN_BUCKET_PAIRS = 5


def _rows(conn, since_ts):
    return conn.execute(
        "SELECT ts, level, charging FROM battery_samples WHERE ts >= ? ORDER BY ts",
        (since_ts,),
    ).fetchall()


def _idle_active_rates(conn, now):
    """Average discharge rate (%/hr, signed) over unplugged 5-min ticks,
    split by whether that tick was mostly idle or mostly active. Returns
    (idle_rate, active_rate, idle_n, active_n) -- either rate is None until
    its bucket has _MIN_BUCKET_PAIRS samples."""
    rows = conn.execute(
        "SELECT ts, level, charging, idle_sec, active_sec FROM battery_samples "
        "WHERE ts >= ? ORDER BY ts",
        (now - _IDLE_ACTIVE_LOOKBACK_DAYS * 86400,),
    ).fetchall()

    idle_rates, active_rates = [], []
    for prev, cur in zip(rows, rows[1:]):
        ts0, lvl0, chg0, _i0, _a0 = prev
        ts1, lvl1, chg1, idle1, active1 = cur
        if lvl0 is None or lvl1 is None:
            continue
        if chg0 or chg1:  # only unplugged stretches -- charging swamps usage
            continue
        if idle1 is None or active1 is None:
            continue
        gap_min = (ts1 - ts0) / 60
        if gap_min <= 0 or gap_min > _MAX_PAIR_GAP_MIN:
            continue
        rate = (lvl1 - lvl0) / (gap_min / 60)  # %/hr, negative while draining
        (active_rates if active1 >= idle1 else idle_rates).append(rate)

    def _avg(xs):
        return sum(xs) / len(xs) if len(xs) >= _MIN_BUCKET_PAIRS else None

    return _avg(idle_rates), _avg(active_rates), len(idle_rates), len(active_rates)


def compute_insights(db_path=None):
    """Returns a dict, or None if the log doesn't exist / has no recent rows."""
    db_path = db_path or DB_PATH
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        now = time.time()
        recent = _rows(conn, now - _RATE_WINDOW_HOURS * 3600)
        if not recent:
            return None
        _last_ts, last_level, last_charging = recent[-1]
        charging = bool(last_charging) if last_charging is not None else None

        # Walk backward from the tail collecting a contiguous run that
        # shares the current charging state -- a charge/unplug transition
        # inside the window would otherwise blend two different rates.
        run = []
        for ts, level, chg in reversed(recent):
            if level is None:
                continue
            chg_bool = bool(chg) if chg is not None else None
            if chg_bool != charging:
                break
            run.append((ts, level))
        run.reverse()

        rate = None       # %/hr, signed (positive while charging)
        eta_hours = None
        if len(run) >= _MIN_SAMPLES:
            span_min = (run[-1][0] - run[0][0]) / 60
            if span_min >= _MIN_WINDOW_MIN:
                delta_level = run[-1][1] - run[0][1]
                rate = delta_level / (span_min / 60)
                if charging:
                    if rate > 0.05:
                        eta_hours = max(0.0, (100 - last_level) / rate)
                else:
                    if rate < -0.05:
                        eta_hours = max(0.0, last_level / -rate)

        day = _rows(conn, now - 24 * 3600)
        levels_24h = [r[1] for r in day if r[1] is not None]

        idle_rate, active_rate, idle_n, active_n = _idle_active_rates(conn, now)

        return {
            "level": last_level,
            "charging": charging,
            "rate_pct_per_hr": rate,
            "eta_hours": eta_hours,
            "min_24h": min(levels_24h) if levels_24h else None,
            "max_24h": max(levels_24h) if levels_24h else None,
            "samples_24h": len(levels_24h),
            "idle_rate_pct_per_hr": idle_rate,
            "active_rate_pct_per_hr": active_rate,
            "idle_rate_n": idle_n,
            "active_rate_n": active_n,
        }
    finally:
        conn.close()


def format_metric(insights):
    """Short one-line metric string for the control-deck's pisugar-battery card."""
    if not insights or insights.get("level") is None:
        return None
    lvl = insights["level"]
    charging = insights["charging"]
    rate = insights["rate_pct_per_hr"]
    eta = insights["eta_hours"]

    base = f"{lvl:.0f}%"
    if charging:
        base += " · charging"
        if eta is not None:
            base += f" · full in ~{eta:.1f}h"
    elif rate is not None:
        base += f" · {rate:+.1f}%/hr"
        if eta is not None:
            base += f" · ~{eta:.0f}h left"
    return base


def format_note(insights):
    """Longer 'what's eating the battery' string for the card's note field --
    idle vs. active discharge cost, only once both buckets have enough
    unplugged 5-min ticks (7d lookback) to trust the averages."""
    if not insights:
        return None
    idle_rate = insights.get("idle_rate_pct_per_hr")
    active_rate = insights.get("active_rate_pct_per_hr")
    if idle_rate is None or active_rate is None:
        return None
    return (
        f"idle baseline ~{-idle_rate:.2f}%/hr, "
        f"active (listening/thinking/speaking) ~{-active_rate:.2f}%/hr "
        f"-- {insights['active_rate_n']} active vs {insights['idle_rate_n']} idle "
        f"5-min samples, last {_IDLE_ACTIVE_LOOKBACK_DAYS}d"
    )


if __name__ == "__main__":
    import json
    print(json.dumps(compute_insights(), indent=2))
