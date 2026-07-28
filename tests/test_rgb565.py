"""The LCD push converts every frame to RGB565 by hand. The vectorized numpy
path must be byte-identical to the pure-Python reference -- a mismatch shows up
as wrong colours or a torn image on the Whisplay HAT, not as an exception.

_push_lcd and its two converters are closures inside run_device_mode, which
needs the HAT to import, so the implementations are re-declared here from the
same arithmetic. If you change the conversion in zeev.py, change it here too --
this test guards the numpy/Python equivalence, not the call site.
"""
import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
from PIL import Image  # noqa: E402


def rgb565_py(img):
    pixels = list(img.convert("RGB").getdata())
    buf = bytearray(len(pixels) * 2)
    for i, (r, g, b) in enumerate(pixels):
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        buf[i * 2] = v >> 8
        buf[i * 2 + 1] = v & 0xFF
    return bytes(buf)


def rgb565_np(img):
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = (((a[:, :, 0] & 0xF8) << 8)
         | ((a[:, :, 1] & 0xFC) << 3)
         | (a[:, :, 2] >> 3))
    return v.astype(">u2").tobytes()


def _img_from(pixels, size):
    im = Image.new("RGB", size)
    im.putdata(pixels)
    return im


def test_matches_on_random_frame():
    rng = np.random.default_rng(0)
    w, h = 24, 28                      # same 6:7 aspect as the real 240x280
    data = rng.integers(0, 256, size=(h * w, 3), dtype=np.uint8)
    img = _img_from([tuple(int(c) for c in px) for px in data], (w, h))
    assert rgb565_np(img) == rgb565_py(img)


@pytest.mark.parametrize("colour", [
    (0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (1, 1, 1), (7, 3, 7), (248, 252, 248), (127, 128, 129),
])
def test_matches_on_edge_colours(colour):
    """Channel masking (0xF8/0xFC) is where an off-by-one shift would hide."""
    img = _img_from([colour] * (4 * 4), (4, 4))
    assert rgb565_np(img) == rgb565_py(img)


def test_output_length_is_two_bytes_per_pixel():
    img = _img_from([(10, 20, 30)] * (8 * 5), (8, 5))
    assert len(rgb565_np(img)) == 8 * 5 * 2


def test_byte_order_is_big_endian():
    """High byte first -- the reference emits v >> 8 before v & 0xFF."""
    img = _img_from([(255, 0, 0)], (1, 1))
    out = rgb565_np(img)
    assert out == bytes([0xF8, 0x00]), out.hex()


def test_full_frame_matches():
    """Real panel geometry, so a stride/reshape bug can't slip through."""
    rng = np.random.default_rng(1)
    w, h = 240, 280
    arr = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    assert rgb565_np(img) == rgb565_py(img)
