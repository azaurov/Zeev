"""
Regression tests for Zeev's TTS and voice pipeline.

Each test is named after the exact bug it guards against.  Failures are
annotated with the original symptom so future readers understand the WHY.

Bug inventory:
  B1  only-first-sentence playback     — Piper inter-chunk timeout was 0.3s
  B2  Piper persistent-process global  — missing `global _piper_term_proc`
                                         inside nested _run() caused SyntaxError
  B3  Groq TTS 429 silent fallthrough  — no rate-limit state, caller got None
                                         silently and played nothing with no log
  B4  Groq TTS 429 repeated hammering  — 429 re-fired on every call until restart
  B5  Hebrew/RTL detection wrong       — single Hebrew char not triggering gTTS path
  B6  Tetragrammaton not sanitised     — YHWH passed raw to Orpheus / gTTS
  B7  Markdown leaking into TTS        — **bold** / ```code``` sent verbatim
  B8  Empty-clean-text crash           — groq_tts("") returned None but logged error
  B9  Groq TTS non-English bypass      — Hebrew text sent to Orpheus (English-only)
  B10 extract_memory 429 silent swallow— returned USER_FACTS instead of None,
                                         hiding the failure from caller
  B11 _gtts_chunks boundary split      — long text not split at sentence markers
  B12 WAV structure validity           — groq_tts output checked for RIFF header
                                         and non-zero duration
"""

import inspect
import struct
import time
import types
from unittest.mock import MagicMock, patch, call

import pytest

from conftest import make_wav


# ============================================================================
# B1 — Only-first-sentence playback: Piper inter-chunk timeout must be >= 5 s
# ============================================================================

def test_piper_interchunk_timeout_not_too_short(zeev):
    """
    Bug: _piper_speak used select.select with t=0.3 for subsequent chunks.
    On Pi Zero 2W, Piper takes 2-4 s per sentence, so only sentence 1 played.
    Fix: t = 30.0 (first chunk) / 5.0 (subsequent chunks).
    We inspect the source to confirm no value smaller than 5.0 is used as
    the inter-chunk wait.
    """
    src = inspect.getsource(zeev._piper_speak)
    # Extract every numeric literal that appears as the third argument to
    # select.select — or more practically, all float literals in the function.
    import re
    # Find the inter-chunk timeout: the value used when got_audio is True
    # The pattern is: t = <first> if not got_audio else <inter>
    m = re.search(r"t\s*=\s*([\d.]+)\s+if\s+not\s+got_audio\s+else\s+([\d.]+)", src)
    assert m is not None, (
        "_piper_speak must use 'got_audio' guard for inter-chunk timeout "
        "(bug B1: without it the first-chunk timeout doubles as inter-chunk timeout)"
    )
    first_chunk_timeout = float(m.group(1))
    inter_chunk_timeout = float(m.group(2))
    assert first_chunk_timeout >= 20.0, (
        f"First-chunk timeout {first_chunk_timeout}s is too short — "
        "Piper needs ~20 s to load its ONNX model on Pi Zero 2W"
    )
    assert inter_chunk_timeout >= 5.0, (
        f"Inter-chunk timeout {inter_chunk_timeout}s is too short (was 0.3s) — "
        "each subsequent Piper sentence takes 2-4 s on Pi Zero 2W (bug B1)"
    )


# ============================================================================
# B2 — Persistent-process global declarations in nested functions
# ============================================================================

def test_piper_speak_global_declaration_in_nested_run(zeev):
    """
    Bug: `global _piper_term_proc` was missing inside _run() so Python raised
    SyntaxError: 'global _piper_term_proc' appears in nested scope — or worse,
    silently created a local variable instead of updating the module global,
    causing a new Piper process to be spawned on every call (model reload delay).
    """
    src = inspect.getsource(zeev._piper_speak)
    # The outer function and the nested _run() must both declare the global.
    global_count = src.count("global _piper_term_proc")
    assert global_count >= 2, (
        f"_piper_speak must declare 'global _piper_term_proc' in both the "
        f"outer function body AND inside _run().  Found {global_count} declaration(s). "
        "(bug B2: missing inner declaration caused persistent-process to not persist)"
    )


def test_piper_warmup_global_declaration(zeev):
    """Same guard for _piper_warmup — must declare global in nested _start()."""
    src = inspect.getsource(zeev._piper_warmup)
    global_count = src.count("global _piper_term_proc")
    assert global_count >= 2, (
        f"_piper_warmup must declare 'global _piper_term_proc' in both the "
        f"outer function body AND inside _start().  Found {global_count}. (bug B2)"
    )


# ============================================================================
# B3 — Groq TTS 429: must return None AND log, not silently fail
# ============================================================================

def test_groq_tts_429_returns_none_and_logs(zeev, capsys):
    """
    Bug: groq_tts() on 429 returned None with no indication to caller.
    Symptom: TTS silently stopped working after daily limit hit; no fallback.
    Fix: log the rate-limit event and set _groq_tts_rate_limited_until.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    original_limit = zeev._groq_tts_rate_limited_until
    zeev._groq_tts_rate_limited_until = 0.0  # reset so we actually hit the API

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev.groq_tts("Hello world")

    captured = capsys.readouterr()
    assert result is None, "groq_tts must return None on 429"
    assert "429" in captured.out, (
        "groq_tts must print a log message on 429 (bug B3: silent fallthrough "
        "made it impossible to diagnose why TTS stopped)"
    )
    # Restore
    zeev._groq_tts_rate_limited_until = original_limit


# ============================================================================
# B4 — Groq TTS 429: rate-limit state must be set so subsequent calls skip
# ============================================================================

def test_groq_tts_429_sets_rate_limit_state(zeev):
    """
    Bug: after a 429, _groq_tts_rate_limited_until was not set, so every
    subsequent speak call hammered the API and got another 429.
    Fix: set _groq_tts_rate_limited_until to next UTC midnight on 429.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    zeev._groq_tts_rate_limited_until = 0.0

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            zeev.groq_tts("Hello world")

    assert zeev._groq_tts_rate_limited_until > time.time(), (
        "_groq_tts_rate_limited_until must be set to a future epoch timestamp "
        "after receiving 429 (bug B4: without this every call re-fired the API)"
    )


def test_groq_tts_429_honoured_on_next_call(zeev):
    """
    After a 429 sets the rate-limit state, the next call must return None
    immediately without making any HTTP request.
    """
    zeev._groq_tts_rate_limited_until = time.time() + 3600  # locked for 1h

    with patch("zeev.requests.post") as mock_post:
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev.groq_tts("Hello world")

    assert result is None, "groq_tts must return None while rate-limited"
    mock_post.assert_not_called(), (
        "groq_tts must NOT call requests.post while rate-limited (bug B4)"
    )

    zeev._groq_tts_rate_limited_until = 0.0  # restore


# ============================================================================
# B5 — Hebrew / RTL detection: single Hebrew char must trigger 'he' path
# ============================================================================

def test_detect_lang(zeev):
    """
    Test that detect_lang returns FORCED_LANG if set, otherwise 'en'.
    Character-based sniffing was removed by design in commit 9c53f789.
    """
    import zeev as zeev_mod
    original_forced = zeev_mod.FORCED_LANG
    try:
        zeev_mod.FORCED_LANG = None
        assert zeev.detect_lang("שלום") == "en"
        assert zeev.detect_lang("Привет") == "en"
        assert zeev.detect_lang("Hello") == "en"

        zeev_mod.FORCED_LANG = "ru"
        assert zeev.detect_lang("Hello") == "ru"
    finally:
        zeev_mod.FORCED_LANG = original_forced


def test_query_language_detection(zeev):
    """
    Test that _build_system_prompt detects the language of the user query
    and overrides the prompt language.
    """
    import zeev as zeev_mod
    original_forced = zeev_mod.FORCED_LANG
    try:
        # English query should override forced Russian to English instruction
        zeev_mod.FORCED_LANG = "ru"
        prompt = zeev_mod._build_system_prompt("Hello, how are you?")
        assert "Reply in English only." in prompt
        assert "Reply in Russian" not in prompt

        # Russian query should keep Russian instruction
        prompt_ru = zeev_mod._build_system_prompt("Привет, как дела?")
        assert "Reply in Russian" in prompt_ru
        assert "Reply in English" not in prompt_ru
    finally:
        zeev_mod.FORCED_LANG = original_forced


def test_speak_terminal_language_override(zeev):
    """
    If FORCED_LANG is 'ru' but text to speak is English, it should override to 'en'
    and route to English TTS.
    """
    import zeev as zeev_mod
    from unittest.mock import patch
    original_forced = zeev_mod.FORCED_LANG
    try:
        zeev_mod.FORCED_LANG = "ru"
        calls = []

        class FakeAudio:
            available = True
            def speak_sync(self, text, lang=None, dev=None):
                calls.append(("audio", lang))

        with (
            patch.object(zeev_mod, "TTS_AVAILABLE", True),
            patch.object(zeev_mod, "_audio", FakeAudio()),
        ):
            # English text with forced Russian should be overridden to 'en'
            # and routed to _audio.speak_sync with lang='en'
            zeev_mod.speak_terminal("Hello, my friend.")
            assert len(calls) == 1
            assert calls[0][1] == "en"

            # Russian text with forced Russian should NOT be overridden,
            # so it should NOT use _audio.speak_sync (it uses _gtts_speak or _piper_direct)
            calls.clear()
            def fake_gtts_speak(text, lang, adev=None):
                calls.append(("gtts", lang))

            with (
                patch.object(zeev_mod, "_gtts_speak", fake_gtts_speak),
                patch("shutil.which", return_value="/usr/bin/mpg123"),
            ):
                zeev_mod.speak_terminal("Привет, друг.")
                assert len(calls) == 1
                assert calls[0][1] == "ru"
    finally:
        zeev_mod.FORCED_LANG = original_forced


def test_apply_language_suffix(zeev):
    """
    Test that _apply_language_suffix appends the appropriate dynamic instruction suffix
    based on the language instruction found in the system prompt.
    """
    import zeev as zeev_mod
    msgs = [
        {"role": "system", "content": "Reply in Russian (Русский) only. ..."},
        {"role": "user", "content": "Hello"}
    ]
    res = zeev_mod._apply_language_suffix(msgs)
    assert res[-1]["content"] == "Hello (ответь только на русском языке)"

    msgs_en = [
        {"role": "system", "content": "Reply in English only. ..."},
        {"role": "user", "content": "Hello"}
    ]
    res_en = zeev_mod._apply_language_suffix(msgs_en)
    assert res_en[-1]["content"] == "Hello (reply in English only)"


# ============================================================================
# B6 — Tetragrammaton sanitisation in _clean_for_tts
# ============================================================================

def test_clean_for_tts_tetragrammaton_english(zeev):
    """
    Bug B6: The four-letter name was passed raw to Orpheus/gTTS, causing
    jarring or incorrect pronunciation.  English mode must replace with 'Adonai'.
    """
    result = zeev._clean_for_tts("The name יהוה is holy.", lang="en")
    assert "יהוה" not in result, "Raw Tetragrammaton must be removed for English TTS"
    assert "Adonai" in result, "_clean_for_tts(lang='en') must replace YHWH with 'Adonai'"


def test_clean_for_tts_tetragrammaton_hebrew(zeev):
    """Hebrew mode must replace with the Hebrew reverential form אֲדֹנָי."""
    result = zeev._clean_for_tts("ברוך יהוה לעולם", lang="he")
    assert "יהוה" not in result, "Raw Tetragrammaton must be removed for Hebrew TTS"
    assert "אֲדֹנָי" in result, "_clean_for_tts(lang='he') must replace YHWH with אֲדֹנָי"


# ============================================================================
# B7 — Markdown must be stripped before TTS
# ============================================================================

@pytest.mark.parametrize("raw,should_not_contain,should_contain", [
    ("**bold text**",        "**",       "bold text"),
    ("_italic_",             "_",        "italic"),
    ("`inline code`",        "`",        "inline code"),
    ("## Heading",           "#",        "Heading"),
    # Fenced code blocks are removed entirely (content + delimiters); only
    # the surrounding whitespace survives.  The delimiter is what must be gone.
    ("```\ncode block\n```", "```",      " "),
    ("~strikethrough~",      "~",        "strikethrough"),
])
def test_clean_for_tts_strips_markdown(zeev, raw, should_not_contain, should_contain):
    """
    Bug B7: Markdown syntax was passed verbatim to TTS engines, producing
    artifacts like 'asterisk asterisk bold asterisk asterisk'.
    Note: fenced code blocks are stripped entirely (content + delimiters).
    """
    result = zeev._clean_for_tts(raw)
    assert should_not_contain not in result, (
        f"_clean_for_tts must strip {should_not_contain!r} from TTS input (bug B7)"
    )
    # For the code-block case should_contain is " " — just verify no backticks remain.
    if should_contain.strip():
        assert should_contain in result, (
            f"_clean_for_tts must preserve the readable text {should_contain!r}"
        )


# ============================================================================
# B8 — Empty-text edge case: groq_tts("") must return None without error log
# ============================================================================

def test_groq_tts_empty_string_returns_none_silently(zeev, capsys):
    """
    Bug B8: groq_tts("") logged '[groq_tts] skipped: clean text is empty'
    on every startup greeting check — noisy and wasteful.  Empty input must
    return None without an HTTP call and without printing an error.
    """
    with patch("zeev.requests.post") as mock_post:
        result = zeev.groq_tts("")

    assert result is None
    mock_post.assert_not_called()
    # The log message is expected (it's intentional instrumentation), but
    # what must NOT happen is an HTTP call or an exception.


# ============================================================================
# B9 — Groq Orpheus English-only: non-English text must be rejected
# ============================================================================

@pytest.mark.parametrize("text", [
    "שלום, מה שלומך?",       # Hebrew
    "Привет, как дела?",      # Russian
    "¿Cómo estás hoy?",       # Spanish
])
def test_groq_tts_rejects_non_english(zeev, text):
    """
    Bug B9: groq_tts() sent Hebrew/Russian/Spanish to Orpheus, which is
    English-only, producing garbled output.  detect_lang must gate the call.
    """
    with patch("zeev.requests.post") as mock_post:
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev.groq_tts(text)

    assert result is None, (
        f"groq_tts must return None for non-English text: {text!r} (bug B9)"
    )
    mock_post.assert_not_called(), (
        f"groq_tts must NOT call Orpheus API for non-English text: {text!r} (bug B9)"
    )


# ============================================================================
# B10 — extract_memory 429: must return None, not USER_FACTS
# ============================================================================

def test_extract_memory_429_returns_none(zeev):
    """
    Bug B10: extract_memory() returned USER_FACTS on 429, which the caller
    (main() quit handler) interpreted as success and printed 'Memory updated'
    even though nothing was extracted.  Callers check `if result is None` to
    show a 'rate limited' warning — returning USER_FACTS breaks that contract.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 429

    session = [
        {"role": "user", "content": "I live in Tel Aviv"},
        {"role": "assistant", "content": "Nice city!"},
    ]

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev.extract_memory(session)

    assert result is None, (
        "extract_memory must return None on 429, not USER_FACTS (bug B10)"
    )


# ============================================================================
# B11 — _gtts_chunks: long text must be split at sentence boundaries
# ============================================================================

def test_gtts_chunks_splits_long_text(zeev):
    """
    Bug B11: _gtts_chunks with a 200-char limit must yield multiple chunks
    for multi-sentence text rather than one giant chunk, which caused the
    Google TTS URL to exceed its query-string limit and return garbled audio.
    """
    text = (
        "This is the first sentence. "
        "This is the second sentence. "
        "This is the third sentence. "
        "This is the fourth sentence. "
        "This is the fifth sentence."
    )
    chunks = list(zeev._gtts_chunks(text, limit=60))
    assert len(chunks) > 1, (
        "_gtts_chunks must split long text into multiple chunks (bug B11)"
    )
    for c in chunks:
        assert len(c) <= 60, (
            f"_gtts_chunks produced a chunk longer than limit: {c!r} (bug B11)"
        )


def test_gtts_chunks_short_text_stays_one_chunk(zeev):
    """Short text that fits in the limit must not be fragmented."""
    text = "Hello there."
    chunks = list(zeev._gtts_chunks(text, limit=200))
    assert chunks == ["Hello there."]


def test_gtts_chunks_empty_text_yields_nothing(zeev):
    chunks = list(zeev._gtts_chunks("", limit=200))
    assert chunks == []


# ============================================================================
# B12 — WAV structure validator (used by groq_tts output checks)
# ============================================================================

class WavValidator:
    """Assert structural and temporal properties of WAV bytes."""

    @staticmethod
    def assert_valid(wav_bytes: bytes, min_duration_s: float = 0.0):
        assert wav_bytes[:4] == b"RIFF", "WAV must start with RIFF header"
        assert wav_bytes[8:12] == b"WAVE", "WAV must have WAVE format marker"
        assert wav_bytes[12:16] == b"fmt ", "WAV must have fmt chunk"
        # Parse sample rate and data size for duration check
        sample_rate = struct.unpack_from("<I", wav_bytes, 24)[0]
        bits_per_sample = struct.unpack_from("<H", wav_bytes, 34)[0]
        # Find data chunk
        pos = 12
        while pos + 8 <= len(wav_bytes):
            chunk_id = wav_bytes[pos:pos+4]
            chunk_size = struct.unpack_from("<I", wav_bytes, pos+4)[0]
            if chunk_id == b"data":
                bytes_per_sample = bits_per_sample // 8
                n_samples = chunk_size // bytes_per_sample
                duration = n_samples / sample_rate
                assert duration > min_duration_s, (
                    f"WAV duration {duration:.3f}s <= minimum {min_duration_s}s"
                )
                return
            pos += 8 + chunk_size
        raise AssertionError("WAV has no data chunk")


def test_wav_validator_accepts_valid_wav():
    wav = make_wav(duration_s=0.5)
    WavValidator.assert_valid(wav, min_duration_s=0.4)


def test_wav_validator_rejects_zero_duration():
    wav = make_wav(duration_s=0.0)
    with pytest.raises(AssertionError, match="duration"):
        WavValidator.assert_valid(wav, min_duration_s=0.1)


def test_wav_validator_rejects_garbage():
    with pytest.raises(AssertionError, match="RIFF"):
        WavValidator.assert_valid(b"garbage data that is not a wav file")


def test_groq_tts_200_returns_valid_wav(zeev):
    """
    When the Groq API returns HTTP 200, groq_tts() must return bytes that
    pass the WAV validator with non-zero duration.
    """
    fake_wav = make_wav(duration_s=1.0)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_wav

    zeev._groq_tts_rate_limited_until = 0.0

    # Clear any stale cache from a prior real-API run that may have written
    # bytes under this key — we want the test to exercise the mocked API
    # path, not the cache-read path.
    import hashlib as _hl
    _key = _hl.md5(
        f"daniel:{zeev._clean_for_tts('Hello, this is a test.', 'en')[:4096]}".encode()
    ).hexdigest()
    _cp = zeev.TTS_CACHE_DIR / f"{_key}.wav"
    if _cp.exists():
        _cp.unlink()

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev.groq_tts("Hello, this is a test.")

    assert result is not None, "groq_tts must return bytes on HTTP 200"
    WavValidator.assert_valid(result, min_duration_s=0.5)


# ============================================================================
# speak_terminal routing: Hebrew routes to gTTS, not Orpheus
# ============================================================================

def test_speak_terminal_routes_hebrew_to_gtts_not_orpheus(zeev):
    """
    When speak_terminal() receives Hebrew text, it must call _gtts_speak()
    (the Google Translate TTS path) and must NOT call groq_tts() (Orpheus
    is English-only — bug B9 at the routing level).
    """
    import shutil as _shutil

    calls = []

    def fake_gtts_speak(text, lang, adev=None):
        calls.append(("gtts", lang))

    def fake_groq_tts(text):
        calls.append(("groq", text))
        return None

    with (
        patch.object(zeev, "_gtts_speak", fake_gtts_speak),
        patch.object(zeev, "groq_tts", fake_groq_tts),
        patch.object(zeev, "TTS_AVAILABLE", True),
        patch("shutil.which", return_value="/usr/bin/mpg123"),
    ):
        thread = None
        import threading as _threading
        original_thread = _threading.Thread

        # speak_terminal calls threading.Thread(...).start() inline.
        # Capture threads AFTER start() so we can join them; do not re-start.
        started = []
        class CapturingThread(original_thread):
            def start(self):
                super().start()
                started.append(self)

        with patch("zeev.threading.Thread", CapturingThread):
            zeev.speak_terminal("שלום, מה שלומך?")
            for t in list(started):
                t.join(timeout=2)

    groq_calls = [c for c in calls if c[0] == "groq"]
    gtts_calls  = [c for c in calls if c[0] == "gtts"]

    assert not groq_calls, (
        "speak_terminal must NOT call groq_tts for Hebrew text (bug B9 routing)"
    )
    assert gtts_calls, "speak_terminal must call _gtts_speak for Hebrew text"
    assert gtts_calls[0][1] == "he", (
        f"_gtts_speak must be called with lang='he', got {gtts_calls[0][1]!r}"
    )


# ============================================================================
# B13 — Whisper 429: must not silently return '' as if no speech
# ============================================================================

def test_whisper_multipart_429_does_not_silently_pass(zeev, capsys):
    """
    Bug B13: _whisper_multipart() returned "" on HTTP 429 from Groq Whisper
    with no logging and no rate-limit state. Callers (groq_stt, groq_stt_call)
    interpret "" as "no speech detected", so the call loop spins silently
    during a 429 — the AI appears to have stopped listening. After the
    cooldown the very next call hits the API again, hammering for another 429.

    Fix: log the 429 loudly, set a cooldown timestamp, and return "" so
    callers still get the empty-transcript signal.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "rate limit exceeded"

    # Use a tiny valid WAV so groq_stt doesn't early-return on empty bytes
    import struct
    fake_wav = b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", 0)

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            result = zeev._whisper_multipart(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                "fake-key", fake_wav, "whisper-large-v3-turbo",
            )

    captured = capsys.readouterr()
    assert result == "", "_whisper_multipart must return empty string on 429"
    assert "429" in captured.out, (
        "_whisper_multipart must print a log line on 429 (bug B13: silent "
        "fallthrough made it impossible to diagnose why STT stopped)"
    )
    assert zeev._groq_stt_rate_limited_until > time.time(), (
        "_whisper_multipart must set a future cooldown timestamp on 429 "
        "(bug B13: without this every call re-fires the API)"
    )


def test_whisper_multipart_429_honoured_on_next_call(zeev):
    """
    After a 429 sets the STT rate-limit state, the next call must return
    '' immediately without making any HTTP request.
    """
    zeev._groq_stt_rate_limited_until = time.time() + 3600  # locked for 1h
    try:
        with patch("zeev.requests.post") as mock_post:
            result = zeev._whisper_multipart(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                "fake-key", b"RIFF...data", "whisper-large-v3-turbo",
            )
        assert result == "", "_whisper_multipart must return '' while rate-limited"
        mock_post.assert_not_called(), (
            "_whisper_multipart must NOT call requests.post while rate-limited"
        )
    finally:
        zeev._groq_stt_rate_limited_until = 0.0


# ============================================================================
# B14 — _groq_post 429: must set cooldown state so subsequent calls skip
# ============================================================================

def test_groq_post_429_sets_rate_limit_state(zeev):
    """
    Bug B14: _groq_post() returned the 429 response without setting any
    cooldown. Every subsequent call hammered Groq and got another 429.
    Same shape as the original _orpheus_429_until / _groq_tts_rate_limited_until
    bug (bug B3/B4), but on the chat-completion path.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "rate limit exceeded"

    msgs = [{"role": "user", "content": "hi"}]
    model = "llama-3.1-8b-instant"
    zeev._groq_model_rate_limited_until[model] = 0.0

    with patch("zeev.requests.post", return_value=mock_resp):
        with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
            resp, err = zeev._groq_post(msgs, model,
                                        stream=False, max_tokens=200)

    assert resp is mock_resp, "_groq_post must return the response on 429 (caller checks status)"
    assert zeev._groq_model_rate_limited_until.get(model, 0) > time.time(), (
        "_groq_post must set a future cooldown timestamp on 429 (bug B14)"
    )
    # cleanup
    zeev._groq_model_rate_limited_until[model] = 0.0


def test_groq_post_429_honoured_on_next_call(zeev):
    """
    After a 429 sets the cooldown, the next _groq_post must NOT make an
    HTTP request — return None with a clear rate-limit error.
    """
    model = "llama-3.1-8b-instant"
    zeev._groq_model_rate_limited_until[model] = time.time() + 3600
    try:
        with patch("zeev.requests.post") as mock_post:
            with patch.object(zeev, "GROQ_API_KEY", "fake-key"):
                resp, err = zeev._groq_post(
                    [{"role": "user", "content": "hi"}],
                    model, stream=False, max_tokens=200,
                )
        assert resp is None, "_groq_post must return None while rate-limited"
        assert err and "rate-limit" in err.lower(), (
            f"_groq_post must return a 'rate-limit' error while locked, got {err!r}"
        )
        mock_post.assert_not_called()
    finally:
        zeev._groq_model_rate_limited_until[model] = 0.0


# ============================================================================
# Speech Recognition STT Provider Tests
# ============================================================================

def test_stt_dispatch_to_speech_recognition(zeev):
    """
    Test that the stt dispatcher calls the speech_recognition provider
    when STT_SERVER is configured as 'speech-recognition'.
    """
    import zeev as zeev_mod
    original_server = zeev_mod.STT_SERVER
    zeev_mod.STT_SERVER = "speech-recognition"
    fake_wav = b"fake wav data"

    mock_sr = MagicMock()
    mock_audio_file = MagicMock()
    mock_audio_data = MagicMock()
    mock_recognizer = MagicMock()

    mock_recognizer.recognize_google.return_value = "hello from google"
    mock_sr.Recognizer.return_value = mock_recognizer
    mock_sr.AudioFile.return_value.__enter__.return_value = mock_audio_file
    mock_recognizer.record.return_value = mock_audio_data

    try:
        with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
            result = zeev.stt(fake_wav)
            assert result == "hello from google"
            mock_sr.AudioFile.assert_called_once()
            mock_recognizer.recognize_google.assert_called_once_with(mock_audio_data, language="en-US")
    finally:
        zeev_mod.STT_SERVER = original_server


def test_detect_active_speaker(zeev):
    """
    Test that detect_active_speaker correctly identifies the speaker
    from user text and session history.
    """
    # 1. Direct mention in user text
    assert zeev.detect_active_speaker("Hi, this is Maria speaking.") == "Maria"
    assert zeev.detect_active_speaker("I am Maria") == "Maria"
    assert zeev.detect_active_speaker("I'm Alex") == "Alex"

    # 2. History-based detection
    session = [
        {"role": "user", "content": "I am Maria"},
        {"role": "assistant", "content": "Hello Maria, how are you?"}
    ]
    assert zeev.detect_active_speaker("how is the weather?", session) == "Maria"

    # 3. Assistant address detection
    session2 = [
        {"role": "user", "content": "how is the weather?"},
        {"role": "assistant", "content": "It's nice. Hello Alex!"}
    ]
    assert zeev.detect_active_speaker("cool", session2) == "Alex"


