# Flashing Wyze RTSP firmware

RTSP is not a stock Wyze feature. The toggle appears in the app **only** after
flashing a separate firmware image, which Wyze built for three models and has
since discontinued — the support article calls the versions aged and the feature
beta, and the download links were pulled from the article. The files themselves
are still served by Wyze's CDN (verified 2026-07-30, HTTP 200), which matters
because the community mirrors are being taken down: `kohrar/Wyze-Firmwares`,
the main firmware archive, has been **DMCA-blocked since 2025-11-25** at Wyze's
request. Pull from `download.wyzecam.com` while it still answers.

## Which cameras can take it

Model codes come from `docker-wyze-bridge`'s own log lines (`[+] Adding <name>
[<model>]`) — faster and less error-prone than reading eight app screens.

| Camera | Model code | Product | RTSP |
|---|---|---|---|
| Upstairs | `WYZE_CAKP2JFUS` | Cam v3 | flashed 2026-07-30 |
| Basement cam | `WYZE_CAKP2JFUS` | Cam v3 | flashed 2026-07-30 |
| Leo's room | `WYZEC1-JZ` | Cam v2 | **possible** |
| Living Room Cam | `WYZECP1_JEF` | Cam Pan v1 | **possible** |
| Backyard | `WVOD1` | Cam Outdoor | never built |
| Front Yard | `WVOD1` | Cam Outdoor | never built |
| secret | `ME_WCO3` | outdoor/battery family | never built |
| Doorbell Cam | `HL_DB2` | Video Doorbell v2 | never built |

For the bottom four there is no image to flash. Their only route is the bridge,
and that is currently a dead end for **every** unflashed camera here: the bridge
reaches `Connecting to …` and then fails `IOTC_ER_TIMEOUT` on the TUTK
handshake (cameras report `dtls: 1`, a known open upstream issue). Leo's room
was confirmed to fail this way too, so flashing is not merely the better path,
it is the only one that works.

## Outcome: both remaining candidates failed (2026-07-30)

**Leo's room (v2) and Living Room (Pan v1) cannot run this firmware.** Both were
flashed with the correct image and both **bricked — written, but unbootable** —
and both were recovered with the stock image below. Do not retry; each attempt
risks a working camera for a payoff now known to be zero.

The cause is documented by Wyze themselves on their forum: *"Some iterations of
the V2 lost RTSP compatibility."* Certain production batches physically cannot
boot it. `4.28.4.49` / `4.29.4.49` are the *improved-compatibility* rebuilds
released to address exactly this, and they are the last builds that exist — the
feature was discontinued, so there is nothing newer to try.

What that rules out, so it isn't re-litigated:

- **Not the images.** Contents verified, not just the download path: the v2
  image carries the string `4.28.4.49` and RTSP code, the Pan image `4.29.4.49`,
  and the stock images contain no RTSP code at all. CRCs clean on all four.
- **Not the procedure or the card.** The stock recovery flashed successfully
  from the same card, same `demo.bin` name, same button sequence.
- **Not a downgrade block.** A blocked flash boots the old firmware normally; a
  brick means the image was accepted and written. `wz_mini_hacks` also states
  these two models downgrade freely, with no version threshold.
- **A closed port 554 proves nothing.** On RTSP firmware the port stays closed
  until RTSP is enabled in the app, so a port scan cannot tell "stock" from
  "RTSP firmware, not yet enabled". The firmware version in the app is the only
  reliable read.

So the ceiling is **two working cameras** (the v3s), not four. The only
remaining route for the v2/Pan is replacing the firmware entirely with
[thingino](https://thingino.com/) — open source, actively maintained, native
RTSP, supports the Ingenic T20 these use. It means losing the Wyze app, and
initial flashing often needs UART serial access rather than an SD card. Not
attempted.

## Images

All four fetched from Wyze's official CDN and validated as u-boot legacy
uImages — magic `0x27051956`, header CRC and data CRC both recomputed and
matching. Staged SD-card-ready (already renamed to `demo.bin`) at
`~/wyze-firmware/` on bosgame, outside the repo; `SHA256SUMS.txt` sits alongside.

| Purpose | URL | sha256 (of the `.bin`) |
|---|---|---|
| v2 RTSP | `https://download.wyzecam.com/firmware/rtsp/demo_v2_rtsp_4.28.4.49.bin.zip` | `ec1355…0fc1c8` |
| v2 stock (recovery) | `https://download.wyzecam.com/firmware/v2/demo_4.9.9.3006.bin.zip` | `2ed6dc…d72583` |
| Pan RTSP | `https://download.wyzecam.com/firmware/rtsp/demo_Pan_rtsp_4.29.4.49.bin.zip` | `ea920b…2a60d4` |
| Pan stock (recovery) | `https://download.wyzecam.com/firmware/pan/demo_4.10.9.3006.bin.zip` | `df59c6…f03974` |

Note the **capital `P`** in the Pan RTSP filename — lowercase 403s, which reads
as "the file is gone" when it is only misspelled.

Cross-check on provenance: the archive.org mirror `v-RTSP-webcam` lists these
two RTSP zips at 9,100,363 and 9,108,911 bytes, byte-for-byte the sizes the
official CDN returned. Two independent sources agreeing on size is worth more
than either one alone.

**A valid CRC is not a promise of a clean boot.** The v3 beta validated fully on
both CRCs and still bricked the camera on first flash. A bad CRC rules a file
out; a good one only means the cheap failure isn't the one you'll hit.

## Recovery — read this before flashing, not after

Recovery is the same operation as flashing, with the stock image. One of the two
v3 flashes here bricked (red LED → IR-cut click → power off, repeating, through
both an SD-card removal and a PSU swap) and stock reflash is what brought it
back. Budget for it.

1. FAT32 card, **`demo.bin` at the root**, nothing else needed.
2. Card in, hold the setup button, apply power with the button held.
3. Release after 3–6 s.
4. Wait. Solid purple for roughly 5 minutes. Do not cut power.

Keep a card with the recovery `demo.bin` written *before* you start, so a brick
is a two-minute swap rather than a download hunt on a dead camera.

## Flashing

Same procedure, RTSP image instead:

- **Leo's room (v2)** → `~/wyze-firmware/leos-room-v2/rtsp/demo.bin`
- **Living Room (Pan v1)** → `~/wyze-firmware/living-room-pan/rtsp/demo.bin`

The file must be named exactly `demo.bin` for v2 and Pan. (The v3 image uses
`demo_wcv3.bin` — different model, different name; do not carry the habit over.)

After it boots, in the app: **Settings → Advanced Settings → RTSP**, enable it,
set a username and password, and read off the URL.

**Turn off automatic firmware updates for these two cameras afterwards.** An app
update reflashes stock and silently removes RTSP — the symptom is Zeev losing a
camera it had yesterday.

## Wiring into Zeev

**The path differs by model, and getting it wrong costs hours.** v2 and Pan use
`/live`. The v3s use `/stream0` — `/live` on v3 firmware answers a raw DESCRIBE
with `401 Digest` but gives ffmpeg only `Invalid data found when processing
input`, with no RTSP dialogue at all, so it presents as an auth problem and
every credential theory is a dead end.

Append to `WYZE_CAMERAS` in the Pi's `~/Zeev/.env`, comma-separated, URL only:

```
WYZE_CAMERAS=upstairs=rtsp://10.0.0.217:554/stream0,basement-cam=rtsp://10.0.0.84:554/stream0,leos-room=rtsp://<IP>:554/live,living-room-cam=rtsp://<IP>:554/live
```

Credentials stay **out of the URL** — `wyze_stream_url()` percent-encodes them.
If the new cameras share the existing login, `WYZE_RTSP_USER`/`WYZE_RTSP_PASS`
already covers them; otherwise add per-camera overrides:

```
WYZE_RTSP_USER_LEOS_ROOM=<user>
WYZE_RTSP_PASS_LEOS_ROOM=<pass>
```

Edit `.env` with an editor, **never `printf`** — a camera password containing `%`
reached the file once as 32 spaces plus `0.000000rak4^^+nop3=`.

Do **not** enable secure RTSP (RTSPS). ffmpeg cannot complete the handshake with
this firmware's TLS 1.3 self-signed stack — it fails `Error in the pull
function` with and without verification — and port 322 keeps listening
afterwards while no longer answering, which looks like a network fault.

Verify with a real frame before declaring it done; `resolve_wyze_cam()` matching
the name is not evidence the stream works.
