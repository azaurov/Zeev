"""MLX90640 32×24 thermal camera helper — I2C bus 3 (GPIO5/SDA, GPIO6/SCL)."""

THERMAL_BUS  = 3
THERMAL_ADDR = 0x33

_mlx = None


class _SMBus3I2C:
    """busio.I2C-compatible shim over smbus2 for adafruit_mlx90640."""

    def __init__(self, bus_num):
        import smbus2 as _smbus2
        self._smbus2 = _smbus2
        self._bus    = _smbus2.SMBus(bus_num)
        self._locked = False

    def try_lock(self):
        if self._locked:
            return False
        self._locked = True
        return True

    def unlock(self):
        self._locked = False

    def writeto(self, addr, buf, *, start=0, end=None, stop=True):
        self._bus.i2c_rdwr(self._smbus2.i2c_msg.write(addr, list(buf[start:end])))

    def readfrom_into(self, addr, buf, *, start=0, end=None, stop=True):
        rlen = len(buf[start:end])
        msg  = self._smbus2.i2c_msg.read(addr, rlen)
        self._bus.i2c_rdwr(msg)
        for i, b in enumerate(msg):
            buf[start + i] = b

    def writeto_then_readfrom(self, addr, wbuf, rbuf, *, out_start=0, out_end=None,
                              in_start=0, in_end=None, stop=False, in_stop=True):
        w    = self._smbus2.i2c_msg.write(addr, list(wbuf[out_start:out_end]))
        rlen = len(rbuf[in_start:in_end])
        r    = self._smbus2.i2c_msg.read(addr, rlen)
        self._bus.i2c_rdwr(w, r)
        for i, b in enumerate(r):
            rbuf[in_start + i] = b


def init_thermal():
    """Try to connect to the MLX90640 on bus 3. Returns True on success."""
    global _mlx
    try:
        import adafruit_mlx90640
        i2c    = _SMBus3I2C(THERMAL_BUS)
        sensor = adafruit_mlx90640.MLX90640(i2c)
        sensor.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
        _mlx = sensor
        return True
    except Exception:
        return False


def capture_frame():
    """Return 768 calibrated temperature floats (°C), rows top-to-bottom, left-to-right."""
    if _mlx is None:
        raise RuntimeError("Thermal sensor not initialised — call init_thermal() first")
    frame = [0.0] * 768
    _mlx.getFrame(frame)
    return frame


def frame_summary(frame):
    """Return {min, max, center, hotspot_row, hotspot_col} for a captured frame."""
    hi = max(range(768), key=lambda i: frame[i])
    return {
        "min":         round(min(frame), 1),
        "max":         round(max(frame), 1),
        "center":      round(frame[383], 1),
        "hotspot_row": hi // 32,
        "hotspot_col": hi % 32,
    }


# Gradient stops: blue → cyan → green → yellow → orange → red
_PALETTE = [
    (0,   0,   200),
    (0,   180, 220),
    (0,   200, 80),
    (220, 200, 0),
    (230, 80,  0),
    (230, 0,   0),
]


def _lerp_color(t):
    t   = max(0.0, min(1.0, t))
    seg = t * (len(_PALETTE) - 1)
    lo  = int(seg)
    hi  = min(lo + 1, len(_PALETTE) - 1)
    f   = seg - lo
    r0, g0, b0 = _PALETTE[lo]
    r1, g1, b1 = _PALETTE[hi]
    return int(r0 + f*(r1-r0)), int(g0 + f*(g1-g0)), int(b0 + f*(b1-b0))


def ascii_map(frame):
    """Return a colored ANSI string rendering of the 32×24 frame."""
    lo, hi = min(frame), max(frame)
    span   = hi - lo or 1.0
    chars  = " .+oO#@"
    lines  = []
    for row in range(24):
        parts = []
        for col in range(32):
            norm = (frame[row * 32 + col] - lo) / span
            r, g, b = _lerp_color(norm)
            ch = chars[int(norm * (len(chars) - 1))]
            parts.append(f"\033[38;2;{r};{g};{b}m{ch}\033[0m")
        lines.append("".join(parts))
    return "\n".join(lines)
