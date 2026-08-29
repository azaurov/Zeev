#!/usr/bin/env python3
"""Small always-on HTTP endpoint for the Zepp OS watch app.

Deliberately a separate, lightweight process from `zeev.py --device` (which
owns the Whisplay HAT / mic / camera): it imports zeev.py as a module (same
safe pattern `.claude/skills/run-zeev/driver.py` already uses -- everything
in zeev.py is gated behind `if __name__ == "__main__":`) and only ever talks
to Bluetooth hardware via the already-shared zeev-audio daemon socket, and to
Wyze cameras over the network -- no local camera/mic device is touched, so it
can run alongside device mode without contention.

Usage:
    python3 zeev/watch_server.py [--port 5050]

Auth: every request must carry `X-Zeev-Watch-Key` matching ZEEV_WATCH_KEY
from .env. No key configured means the server refuses everything (fail
closed, not open) -- this is meant to sit behind nginx on a public hostname.
"""
import argparse
import hmac
import json
import re
import requests
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zeev  # noqa: E402  (import after sys.path patch)

# Fixed pairing target: the TOZO NC9 earbuds paired on ragnarok. Not
# scan-and-guess -- a watch tap should always reconnect the same known
# headphones, never whichever device happens to answer a fresh scan first.
_BLE_TARGET_MAC = "94:4B:F8:6B:08:08"
_BLE_TARGET_NAME = "TOZO NC9"

# "Find Leo and/or Smokey" (widened 2026-08-22, was Smokey-only): Leo the dog
# and Smokey the cat, swept in sequence. Direct zeev.WYZE_SUBJECTS lookups by
# key, not zeev.resolve_subject(text) -- this is a fixed watch-button tap,
# not free user text the conversational trigger/tail-matching gate needs to
# handle, so going straight at the dict is simpler and matches the exact-ref
# lookups the blessings already use for the same reason.
_FIND_SUBJECT_KEYS = ("leo", "smokey")

# import_sefaria.py strips the <i class="footnote">...</i> body but leaves the
# bare superscript reference digit stuck to the preceding word ("Blessed1
# are You... Adonoy2 our God") -- invisible when the LLM paraphrases this text
# on its way to speech, but this endpoint speaks torah_search() output
# directly with no LLM in between, so "one"/"two" would otherwise get spoken
# aloud. Matches only a letter immediately followed by 1-2 digits then a word
# boundary (not space-separated numbers like verse/chapter counts).
_FOOTNOTE_MARKER_RE = re.compile(r"(?<=[a-zA-Z])\d{1,2}\b")


def _strip_footnote_markers(text):
    return _FOOTNOTE_MARKER_RE.sub("", text)


# The Tetragrammaton (YHVH) is never read aloud as written -- tradition
# substitutes "Adonai" in prayer. Google's TTS has no notion of this (it's
# not a word in its Hebrew training data) and either sounds out the raw
# letters or garbles them, which is exactly the "didn't pronounce the
# Adonai" Alex heard live (2026-08-22). Matched with optional niqud
# (vowel-point combining marks, U+0591-U+05C7) between each consonant since
# the DB text is fully vowelized and a plain "יהוה" literal would never
# match it.
_NIQUD_CHARS = r"[֑-ׇ]"
_TETRAGRAMMATON_RE = re.compile(
    r"י" + _NIQUD_CHARS + r"*ה" + _NIQUD_CHARS + r"*ו" + _NIQUD_CHARS + r"*ה" + _NIQUD_CHARS + r"*"
)
_ADONAI = "אֲדֹנָי"


def _substitute_tetragrammaton(text):
    return _TETRAGRAMMATON_RE.sub(_ADONAI, text)


# Google's Hebrew TTS is trained almost entirely on plain, unvocalized modern
# Hebrew -- the heavily-pointed liturgical text in torah.db is a different
# register it handles poorly, garbling ordinary words like "Eloheinu" (found
# live 2026-08-22, right after fixing the Tetragrammaton). Stripping niqud
# reduces the text to the standard printed form of the same words (verified:
# this exact blessing's niqud-stripped text matches how a siddur prints it
# unvocalized) -- a root-cause fix rather than patching one mispronounced
# word at a time.
_NIQUD_RE = re.compile(_NIQUD_CHARS + "+")


def _strip_niqud(text):
    return _NIQUD_RE.sub("", text)


# Blessing audio is Hebrew at half speed (Alex explicitly wants to hear the
# pronunciation, not a fast English paraphrase) -- the Go daemon has no
# Hebrew path at all (Piper/Kokoro are en/ru/es only, see CLAUDE.md
# Multilingual TTS), so this goes through gTTS + ffmpeg + mpg123 directly in
# this process, the same tools zeev.py's own speak_terminal() uses for
# Hebrew. atempo (not mpg123's --pitch) because atempo is a pure time-stretch
# -- it keeps the voice's pitch natural instead of dropping it into a slowed-
# record register, at the cost of a bit of extra ffmpeg CPU on the Pi Zero.
_BLESSING_TEMPO = 0.6

# +15% louder than whatever the current playback device is already set to
# (requested 2026-08-22; started at +5%, raised after "too quiet"). Applied
# via ffmpeg's volume filter, same chain as the atempo slowdown, rather than
# touching system/device volume -- this must only affect blessing playback,
# not every other TTS path.
_BLESSING_VOLUME = 1.15


def _current_audio_dev():
    """Whatever ALSA PCM the Go daemon currently has active (BT headphones if
    connected, wired speaker otherwise) -- watch_server.py has no local BT
    state of its own (zeev.bt_audio_dev()'s _BT_AUDIO_DEV global is only ever
    populated by device mode's own BT init, which never runs in this
    process), so the daemon's own live query is the only source of truth
    here."""
    if zeev._audio and zeev._audio.available:
        try:
            return zeev._audio.audio_dev() or "default"
        except Exception:
            pass
    return "default"


def _speak_hebrew_slow(text, tempo=_BLESSING_TEMPO):
    """Fire-and-forget: gTTS chunks -> ffmpeg atempo (pitch-preserving
    slowdown) -> mpg123. Runs in a background thread, same fire-and-forget
    reasoning as _speak() -- the HTTP response must not wait on this.
    Best-effort: any failure here must not turn a working text reply into a
    500, so everything below is wrapped and only logged.
    """
    if not (shutil.which("mpg123") and shutil.which("ffmpeg")):
        print("[watch] mpg123/ffmpeg not available, cannot speak Hebrew", flush=True)
        return

    def _run():
        adev = _current_audio_dev()
        try:
            for chunk in zeev._gtts_chunks(text):
                mp3 = zeev._gtts_fetch_chunk(chunk, "he")
                if not mp3:
                    continue
                ffmpeg = subprocess.Popen(
                    ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                     "-af", f"atempo={tempo},volume={_BLESSING_VOLUME}", "-f", "mp3", "pipe:1"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
                slowed, _ = ffmpeg.communicate(input=mp3, timeout=30)
                if not slowed:
                    continue
                player = subprocess.Popen(
                    ["mpg123", "-q", "-a", adev, "-"],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                player.communicate(input=slowed, timeout=60)
        except Exception as e:
            print(f"[watch] Hebrew speak failed: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()


# Cartesia's sonic-3.5 has real native Hebrew prosody (sonic-2, used
# elsewhere in this project for English phone-call TTS via zeev.cartesia_tts,
# is English-only -- Hebrew only arrived with sonic-3). Requested live
# 2026-08-22 after gTTS still botched vowel stress even post niqud-fix --
# gTTS is a Google Translate hack, not a real phonetic model, and was never
# going to get stress right. Full niqud text is passed through UNSTRIPPED
# here (opposite of the gTTS path): a real phonetic model should use the
# vowel points to get stress right, not choke on them the way gTTS did.
_CARTESIA_HEBREW_MODEL = "sonic-3.5"


def _cartesia_tts_hebrew(text):
    """Returns WAV bytes or None (no key configured, HTTP error, network
    failure) -- every failure mode falls back to the gTTS path, never to
    silence."""
    if not zeev.CARTESIA_API_KEY:
        return None
    try:
        resp = requests.post(
            "https://api.cartesia.ai/tts/bytes",
            headers={"X-API-Key": zeev.CARTESIA_API_KEY,
                     "Cartesia-Version": "2024-06-10",
                     "Content-Type": "application/json"},
            json={"model_id": _CARTESIA_HEBREW_MODEL,
                  "transcript": text[:4000],
                  "voice": {"mode": "id", "id": zeev.CARTESIA_VOICE_ID},
                  "language": "he",
                  "output_format": {"container": "wav", "encoding": "pcm_s16le",
                                     "sample_rate": 22050}},
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"[watch] Cartesia Hebrew TTS HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
            return None
        return resp.content
    except Exception as e:
        print(f"[watch] Cartesia Hebrew TTS error: {e}", flush=True)
        return None


def _speak_hebrew(text_niqud, text_no_niqud, tempo=_BLESSING_TEMPO):
    """Fire-and-forget: try Cartesia (native Hebrew prosody) first, fall back
    to the gTTS pipeline (_speak_hebrew_slow) on any failure -- no API key,
    an HTTP error, a network blip, or ffmpeg/mpg123 producing nothing. Runs
    in its own thread so a slow/failed Cartesia call can't hold up the HTTP
    response; the gTTS fallback spawns its own thread in turn, which is
    harmless (both are still fire-and-forget, just one thread deep).
    """
    def _run():
        wav = _cartesia_tts_hebrew(text_niqud)
        if not wav:
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)
            return
        if not (shutil.which("mpg123") and shutil.which("ffmpeg")):
            print("[watch] mpg123/ffmpeg not available, cannot play Cartesia Hebrew audio", flush=True)
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)
            return
        adev = _current_audio_dev()
        try:
            # Re-encoded to mp3 (not played as the wav Cartesia returns)
            # because mpg123, not aplay, is what's proven safe against
            # BlueALSA's strict format matching in this project (see
            # CLAUDE.md's BT audio resampling note) -- same reasoning the
            # gTTS path already relies on.
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
                 "-af", f"atempo={tempo},volume={_BLESSING_VOLUME}", "-f", "mp3", "pipe:1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            slowed, _ = ffmpeg.communicate(input=wav, timeout=30)
            if not slowed:
                _speak_hebrew_slow(text_no_niqud, tempo=tempo)
                return
            player = subprocess.Popen(
                ["mpg123", "-q", "-a", adev, "-"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            player.communicate(input=slowed, timeout=60)
        except Exception as e:
            print(f"[watch] Cartesia Hebrew playback failed: {e}", flush=True)
            _speak_hebrew_slow(text_no_niqud, tempo=tempo)

    threading.Thread(target=_run, daemon=True).start()


def _already_connected(mac):
    return any(m == mac and connected for m, _name, connected in zeev.bt_list())


def _cmd_pair_ble():
    # BlueZ returns a spurious "br-connection-page-timeout" error when asked
    # to connect a device that's already connected (found live 2026-08-18) --
    # skip the redundant connect attempt rather than report a false failure.
    if _already_connected(_BLE_TARGET_MAC):
        return True, f"Already connected to {_BLE_TARGET_NAME}."
    if not zeev.bt_pair(_BLE_TARGET_MAC):
        return False, f"Couldn't pair {_BLE_TARGET_NAME}."
    if not zeev.bt_connect(_BLE_TARGET_MAC):
        return False, f"Paired {_BLE_TARGET_NAME} but couldn't connect."
    return True, f"Connected to {_BLE_TARGET_NAME}."


def _speak(text):
    """Fire-and-forget TTS through whatever audio device is active (BT
    headphones if connected, wired speaker otherwise) -- same daemon call
    device mode uses. Fire-and-forget, not speak_sync, because speak_sync
    blocks up to 180s: the watch app is waiting on this HTTP response with a
    spinner, and a multi-minute news digest must not hold that open. Best
    effort -- a TTS failure must not turn a working text reply into a 500.

    skip_espeak=True: this endpoint has no better fallback of its own queued
    (unlike device mode's English path, which escalates to Groq Orpheus on a
    Kokoro/Piper failure), so a failure here should mean silence, not the
    daemon quietly re-speaking the whole digest in the robotic espeak-ng
    voice -- the watch already shows the text either way.
    """
    if not (zeev._audio and zeev._audio.available):
        return
    try:
        zeev._audio.speak(text, skip_espeak=True)
    except Exception as e:
        print(f"[watch] speak failed: {e}", flush=True)


def _cmd_world_news():
    text = zeev.get_shpeel()
    _speak(text)
    return True, text


def _cmd_find_subject(name, speak=True, include_image=False, text=""):
    """Sweep for any single configured subject by key/alias -- the general
    form _cmd_find_smokey's fixed leo+smokey pair is built on. `speak=False`
    is for callers with their own display surface (the web chat's /chat
    handler): the whole point there is a silent text answer, not announcing
    the lookup through the Pi's physical speaker on every remote chat turn.
    Returns (ok, message, image_b64_or_None).

    `text` (optional): the caller's original request text, passed straight
    to zeev.resolve_subject_cams() -- explicitly-named cameras (RTSP or
    phone-relay) win outright, "all/every cameras" phrasing means sweep
    every reachable camera, and otherwise the subject's configured defaults
    are used, mirroring device mode's own handle_transcript logic. Found
    live 2026-08-28: without this, "find Leo in the living room and
    backyard and front yard" over the web UI silently swept the subject's
    default RTSP cams instead, since this endpoint had no way to see what
    was actually asked for -- and, in a second round the same night, so did
    "find Leo on all the cameras" even after the first fix landed, since
    naming zero *specific* cameras isn't the same as naming none at all.

    On an actual find, the exact frame that produced the "found" verdict
    (sweep_for_subject()'s third return value, already grabbed during the
    sweep -- no second fetch) is always attached, regardless of
    `include_image`/photo-wording in the request. Found live 2026-08-28: a
    plain-text "found Leo" answer gave Alex no way to independently check
    whether it was a real sighting or a vision-model slip, and there's no
    good reason to gate photographic evidence of a genuine find behind
    whether the user happened to also say "picture" -- `include_image` still
    controls the miss/inconclusive case, where a second fresh grab (prefers
    the first RTSP camera actually swept, falling back to the first
    phone-relay camera swept if that's all that was named -- a fixed
    subj["cams"][0] would grab the wrong camera's image whenever a request
    named phone-relay cameras only) is the best available evidence, a few
    seconds of camera staleness being a fine trade for not re-plumbing
    sweep_for_subject's signature for every other caller (device mode, the
    old fixed find_smokey command) just for this one caller's needs.
    """
    key = (name or "").strip().lower()
    subj = zeev.WYZE_SUBJECTS.get(key)
    if not subj:
        return False, f"{name!r} isn't configured as a subject (check ZEEV_SUBJECTS).", None
    cams, named_phone_cams = zeev.resolve_subject_cams(text, subj)
    reply, _frames, found_img = zeev.sweep_for_subject(subj, cams=cams, phone_cams=named_phone_cams)
    if speak:
        _speak(reply)
    image = found_img
    if not image and include_image:
        if cams:
            image = zeev.wyze_snapshot(cams[0])
        elif named_phone_cams:
            _ok, _msg, image = zeev.phone_camera_snapshot_remote(named_phone_cams[0])
    return True, reply, image


def _cmd_snapshot(camera):
    """Plain photo of a named camera, no subject/vision-verdict involved --
    for "show me the bedroom cam" rather than "check on Smokey". Returns
    (ok, message, image_b64_or_None)."""
    cam = (camera or "").strip().lower()
    if cam not in zeev.WYZE_CAMERAS:
        return False, f"{camera!r} isn't a configured camera.", None
    image = zeev.wyze_snapshot(cam)
    if not image:
        return False, f"Couldn't get a frame from {zeev.wyze_cam_label(cam)} right now.", None
    return True, f"Here's {zeev.wyze_cam_label(cam)}.", image


def _cmd_sweep(text):
    """Multi-camera sweep for "show me what's on the cameras" / "all cameras"
    phrasing -- the web chat's counterpart to device mode's inline sweep
    branch in handle_transcript (zeev.py, the `if sweep:` block under the
    camera gate). Device mode only ever speaks a combined description since
    it has no display; this returns each camera's frame too, since the web
    UI can show them. Sequential (not device mode's overlapped grab-under-
    vision-call optimization) -- this is a one-shot server-side sweep behind
    an HTTP request, not a live spoken turn where the next grab needs to
    start before the current vision call to keep pace with speech.
    Returns (ok, message, images_b64_list).
    """
    sweep = zeev.resolve_wyze_sweep(text)
    if not sweep:
        return False, "That didn't resolve to a camera sweep.", []
    seen_parts, missing, images = [], [], []
    for s in sweep:
        img = zeev.wyze_snapshot(s)
        label = zeev.wyze_cam_label(s)
        if not img:
            print(f"[watch] sweep: no frame from {s}", flush=True)
            missing.append(label)
            continue
        vreply, verr = zeev.vision_complete(img, zeev.sweep_vision_prompt(label))
        if not vreply:
            print(f"[watch] sweep: vision failed on {s}: {verr}", flush=True)
            missing.append(label)
            continue
        clean = zeev._strip_stage_directions(vreply).strip()
        if not clean:
            missing.append(label)
            continue
        seen_parts.append((label, clean))
        images.append(img)
    if seen_parts:
        message = " ".join(f"On the {lb}: {d}" for lb, d in seen_parts)
        if missing:
            message += (" I couldn't get a picture from the "
                         + " or the ".join(missing) + ".")
        return True, message, images
    where = " or the ".join(missing) or "cameras"
    return False, (f"I couldn't get a picture from the {where} just now "
                    "— they may be asleep or offline."), []


def _cmd_find_smokey():
    replies = []
    any_configured = False
    for key in _FIND_SUBJECT_KEYS:
        subj = zeev.WYZE_SUBJECTS.get(key)
        if not subj:
            replies.append(f"{key.capitalize()} isn't configured as a subject "
                            f"(check ZEEV_SUBJECTS).")
            continue
        any_configured = True
        reply, _frames, _found_img = zeev.sweep_for_subject(subj)
        replies.append(reply)
    combined = " ".join(replies)
    if not any_configured:
        return False, combined
    _speak(combined)
    return True, combined


def _speak_blessing_and_return(en, he):
    """Shared by both DB-sourced and hardcoded-literal blessings: speaks
    Hebrew (slowed, Tetragrammaton fixed) if available, else falls back to
    the English via the Go daemon; always returns the English text for the
    watch screen (Zepp OS's bitmap fonts aren't guaranteed to render Hebrew
    glyphs)."""
    en = _strip_footnote_markers(en)
    if he:
        tetra_fixed = _substitute_tetragrammaton(_strip_footnote_markers(he))
        _speak_hebrew(tetra_fixed, _strip_niqud(tetra_fixed))
    else:
        _speak(en)
    return True, en


def _make_blessing_cmd(query):
    """DB-sourced blessing: query is the canonical torah.db `ref` spelling
    (not necessarily how a person would say it -- _torah_ref_lookup's LIKE
    probe needs the literal spelling that's actually in `ref`)."""
    def _cmd():
        rows = zeev.torah_search(query, k=1)
        if not rows:
            return False, f"Couldn't find {query} in the Torah database."
        _ref, en, he = rows[0]
        if not en:
            return False, f"{query} has no English text in the Torah database."
        return _speak_blessing_and_return(en, he)
    return _cmd


def _torah_by_exact_ref(ref):
    """Exact ref lookup, bypassing torah_search()'s fuzzy bigram/FTS
    matching -- needed where that fuzzy matching can't reliably disambiguate.
    Found live 2026-08-22: a bare "Shema" query resolved to Talmud Berakhot
    2a (the Gemara's opening sugya about *when* to recite it) instead of the
    actual prayer text -- "shema" is too short for _torah_ref_lookup's
    bigram/single-word tiers (bigrams need 2 words; singles need len>=7), so
    it fell through to FTS body-text search, which naturally favors whatever
    passage mentions the word "shema" most, not the passage that most is
    named it. Safe here specifically because these are fixed watch-button
    lookups against a ref already confirmed to exist (see git history for
    this file), not free user text a fuzzy matcher needs to handle."""
    if not zeev.TORAH_DB.exists():
        return None
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{zeev.TORAH_DB}?mode=ro", uri=True)
        row = con.execute(
            "SELECT ref, en, he FROM passages WHERE ref = ?", (ref,)
        ).fetchone()
        con.close()
        return row
    except Exception as e:
        print(f"[watch] exact ref lookup failed: {e}", flush=True)
        return None


def _make_ref_blessing_cmd(ref):
    def _cmd():
        row = _torah_by_exact_ref(ref)
        if not row:
            return False, f"Couldn't find {ref!r} in the Torah database."
        _ref, en, he = row
        if not en:
            return False, f"{ref!r} has no English text in the Torah database."
        return _speak_blessing_and_return(en, he)
    return _cmd


def _make_literal_blessing_cmd(en, he):
    """Hardcoded blessing: for the six primary before-eating blessings plus
    Borei Nefashot ("Brich"), which torah.db either doesn't have as clean
    passages at all, or only has buried in long Talmudic sugya discussion,
    or (Borei Nefashot itself) has with no English translation at all in the
    imported source. These are extremely short, universally fixed one-line
    formulas -- identical in every printed Ashkenazi siddur -- so hardcoding
    carries none of the fabrication risk a longer or more variable passage
    would (see docs/torah-rag.md on why this project otherwise insists on
    DB-grounding)."""
    def _cmd():
        return _speak_blessing_and_return(en, he)
    return _cmd


# Order matches the physical blessing card Alex photographed (2026-08-22):
# Al Netilas, Hamotzi, Mezonos, Hagofen, Hoaytz, Hoadomo, Shehakol, Brich,
# Modeh Ani, Shema. "source" picks which _make_*_cmd factory builds the
# command; db entries name the torah.db query, literal entries carry their
# own en/he text directly. "Brich" uses Borei Nefashot (Alex's choice) --
# the short one-liner said after most everyday foods, not the longer
# multi-variant Al Hamichyah (specific to the 5 grains/wine/7 species).
_BLESSINGS = [
    {"key": "netilas_yadayim", "label": "Netilas Yadayim", "source": "db",
     "query": "Netilat Yadayim"},
    {"key": "hamotzi", "label": "Hamotzi", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who brings forth bread from the earth.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם הַמּוֹצִיא לֶחֶם מִן הָאָרֶץ"},
    {"key": "mezonos", "label": "Mezonos", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who creates various kinds of nourishment.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם בּוֹרֵא מִינֵי מְזוֹנוֹת"},
    {"key": "hagofen", "label": "Hagofen", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who creates the fruit of the vine.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם בּוֹרֵא פְּרִי הַגָּפֶן"},
    {"key": "hoaytz", "label": "Hoaytz", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who creates the fruit of the tree.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם בּוֹרֵא פְּרִי הָעֵץ"},
    {"key": "hoadomo", "label": "Hoadomo", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who creates the fruit of the ground.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם בּוֹרֵא פְּרִי הָאֲדָמָה"},
    {"key": "shehakol", "label": "Shehakol", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "by Whose word all things came to be.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם שֶׁהַכֹּל נִהְיֶה בִּדְבָרוֹ"},
    {"key": "brich", "label": "Brich", "source": "literal",
     "en": "Blessed are You, Adonoy our God, King of the Universe, "
           "Who creates many living things and their needs, for all the "
           "things You have created to sustain every living being. "
           "Blessed is the Life of all worlds.",
     "he": "בָּרוּךְ אַתָּה יְהֹוָה אֱלֹהֵינוּ מֶלֶךְ הָעוֹלָם בּוֹרֵא נְפָשׁוֹת רַבּוֹת "
           "וְחֶסְרוֹנָן עַל כָּל מַה שֶׁבָּרָאתָ לְהַחֲיוֹת בָּהֶם נֶפֶשׁ כָּל חָי "
           "בָּרוּךְ חֵי הָעוֹלָמִים"},
    {"key": "modeh_ani", "label": "Modeh Ani", "source": "ref",
     "ref": "Siddur Ashkenaz, Weekday, Shacharit, Preparatory Prayers, Modeh Ani"},
    # Hardcoded, not DB-sourced (Alex's choice, 2026-08-22): torah.db's
    # "Shema" entry has footnote *commentary* interleaved directly into the
    # prayer text itself ("...Adonoy is One.1Customarily one closes his
    # eyes...", "-Maseches Berachos 12b", "cited by Rashi"), not just stray
    # reference digits like the other passages -- _strip_footnote_markers
    # can't clean that, it's editorial prose mixed into the body, not a
    # marker glued onto a word. Just the core first line/verse, not the full
    # three paragraphs.
    {"key": "shema", "label": "Shema", "source": "literal",
     "en": "Hear, O Israel: Adonoy is our God, Adonoy is One.",
     "he": "שְׁמַע יִשְׂרָאֵל יְהֹוָה אֱלֹהֵינוּ יְהֹוָה אֶחָד"},
]


def _blessing_cmd(entry):
    if entry["source"] == "db":
        return _make_blessing_cmd(entry["query"])
    if entry["source"] == "ref":
        return _make_ref_blessing_cmd(entry["ref"])
    return _make_literal_blessing_cmd(entry["en"], entry["he"])


_COMMANDS = {
    "pair_ble": _cmd_pair_ble,
    "world_news": _cmd_world_news,
    "find_smokey": _cmd_find_smokey,
}
_COMMANDS.update(
    (f"blessing_{entry['key']}", _blessing_cmd(entry)) for entry in _BLESSINGS
)


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if not zeev.ZEEV_WATCH_KEY:
                return False
            got = self.headers.get("X-Zeev-Watch-Key", "")
            return hmac.compare_digest(got, zeev.ZEEV_WATCH_KEY)

        def do_POST(self):
            if self.path != "/watch":
                self._json(404, {"ok": False, "error": "not found"})
                return
            if not self._authorized():
                self._json(403, {"ok": False, "error": "unauthorized"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json(400, {"ok": False, "error": "malformed JSON body"})
                return
            cmd = data.get("cmd")
            # find_subject/snapshot take request-body params, unlike every
            # other command here -- handled separately from the plain
            # zero-arg _COMMANDS dispatch below rather than widening every
            # command's signature for the two that need arguments.
            if cmd == "find_subject":
                try:
                    ok, message, image = _cmd_find_subject(
                        data.get("name"), speak=bool(data.get("speak", True)),
                        include_image=bool(data.get("image", False)),
                        text=data.get("text", ""))
                except Exception as e:
                    print(f"[watch] find_subject failed: {e}", flush=True)
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                payload = {"ok": ok, "message": message}
                if image:
                    payload["image"] = image
                self._json(200, payload)
                return
            if cmd == "snapshot":
                try:
                    ok, message, image = _cmd_snapshot(data.get("camera"))
                except Exception as e:
                    print(f"[watch] snapshot failed: {e}", flush=True)
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                payload = {"ok": ok, "message": message}
                if image:
                    payload["image"] = image
                self._json(200, payload)
                return
            if cmd == "sweep":
                try:
                    ok, message, images = _cmd_sweep(data.get("text", ""))
                except Exception as e:
                    print(f"[watch] sweep failed: {e}", flush=True)
                    self._json(500, {"ok": False, "error": str(e)})
                    return
                payload = {"ok": ok, "message": message}
                if images:
                    payload["images"] = images
                self._json(200, payload)
                return
            fn = _COMMANDS.get(cmd)
            if not fn:
                self._json(400, {"ok": False, "error": f"unknown cmd {cmd!r}"})
                return
            try:
                ok, message = fn()
            except Exception as e:
                print(f"[watch] {cmd} failed: {e}", flush=True)
                self._json(500, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": ok, "message": message})

    return Handler


def run_watch_server(host="0.0.0.0", port=5050):
    if not zeev.ZEEV_WATCH_KEY:
        print("[watch] WARNING: ZEEV_WATCH_KEY is not set — every request will "
              "be refused. Add ZEEV_WATCH_KEY=... to .env.", flush=True)
    # Without this, zeev.bt_pair/bt_connect/bt_scan silently fall through to
    # a raw bluetoothctl subprocess (15s hard timeout on connect) instead of
    # the already-running zeev-audio daemon's fast BT path -- found live
    # 2026-08-18, a real "pair_ble" request timed out and reported failure
    # even though the earbuds connected moments later on their own.
    zeev._init_audio()
    server = ThreadingHTTPServer((host, port), _make_handler())
    print(f"[watch] listening on {host}:{port}", flush=True)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050)
    args = ap.parse_args()
    run_watch_server(port=args.port)


if __name__ == "__main__":
    main()
