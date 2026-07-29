# Training custom wake words for Zeev

Everything on the device side is already built and proven with stock models. Two
trained `.onnx` files and two `.env` lines are all that is left.

## 1. Pick the phrase — length is the single biggest lever

openWakeWord's false-accept rate is dominated by phrase length. Short targets
fire on ordinary speech and television constantly.

- **"Sarina"** — three syllables. Fine on its own.
- **"Ze'ev"** (zeh-EV) — two syllables, and genuinely better than the
  one-syllable "Zeev". Two is workable but it is the shortest thing worth
  training; expect to spend some time on the threshold.
- **"Zeev"** as one syllable — don't. It would make the false-wake problem
  worse, not better, after ninety minutes of GPU time.
- **"Hey Ze'ev" / "Hey Sarina"** — the safe option, 3–4 syllables each, if
  "Ze'ev" alone turns out too trigger-happy. The shared "Hey" onset is the
  lesser risk; if the two models cross-trigger, the fix is per-model
  thresholds, not retraining — ask and it gets added.

Training is free and unattended, so the empirical route is reasonable: train
`Ze'ev` and `Sarina`, live with them for a few days, and only fall back to the
"Hey" prefix if the log shows real false wakes rather than a threshold that
wants nudging.

**Spelling matters more than usual for `Ze'ev`.** The notebook generates its own
training audio from the text you type, so it trains on whatever the TTS thinks
that string sounds like. The apostrophe is not reliably pronounced. Listen to
the generated samples the notebook plays back — if they sound wrong, retype it
phonetically (`Zeh ev`, `Zeh-ev`) until the samples sound like the word you
actually say. A model trained on a mispronunciation will never hear you.

## 2. Train

**Do not use openWakeWord's own `automatic_model_training.ipynb`.** It bit-rotted
against current Colab images and fails for about eight independent reasons —
Python 3.12 has no `piper-phonemize` wheels, `torchaudio` 2.x dropped
`set_audio_backend`, several config keys became required-or-`KeyError`, and the
`piper-sample-generator` package layout moved. Upstream issues
[#296](https://github.com/dscripka/openWakeWord/issues/296) and
[#70](https://github.com/dscripka/openWakeWord/issues/70) track it.

Use the patched 2026 notebook instead:

<https://colab.research.google.com/github/alfiedennen/openwakeword-colab-2026/blob/main/train_wakeword.ipynb>

Run all, walk away, download the `.onnx` at the end. Two lines to edit, in
cell 10:

```python
TARGET_PHRASE = ['zeev', 'ze ev', 'zeh ev']   # pronunciation variants
MODEL_NAME    = 'zeev'
```

`TARGET_PHRASE` is a **list of variants**, which is exactly where the `Ze'ev`
pronunciation problem gets solved: give it the spellings that make the TTS say
the word correctly and it trains on all of them.

- **Free Colab (T4)**: ~2.5 h, and free sessions get disconnected — keep the tab
  open.
- **Colab Pro (L4 + High RAM)**: ~75–90 min, $10/month, about 1/100th of the
  monthly credits per run. An A100 buys nothing; the job is network- and
  CPU-bound.

Run it twice, once per phrase.

It's a single-author community repo, so read it before running — I checked, and
every download goes to a legitimate upstream (`rhasspy/piper-sample-generator`,
`dscripka/openWakeWord` releases, the ACAV100M feature set on HuggingFace, the
FMA music set). No credentials, no Drive mount.

**Local training is not an option here.** The maintained local pipeline
([CoreWorxLab/openwakeword-training](https://github.com/CoreWorxLab/openwakeword-training))
needs an NVIDIA GPU — RTX 3060 12 GB or better, 4–8 h per run. bosgame and
feiergente01 are both CPU-only, so Colab it is.

## 3. Install

```bash
ssh ragnar@ragnarok "mkdir -p ~/oww"
scp zeev.onnx sarina.onnx ragnar@ragnarok:~/oww/
```

Then in `/home/ragnar/Zeev/.env`:

```
OWW_MODEL_PATH=/home/ragnar/oww/zeev.onnx,/home/ragnar/oww/sarina.onnx
OWW_VOICE_MAP=zeev:daniel,sarina:sarina
```

`sudo systemctl restart zeev-device`, then watch it arm:

```bash
journalctl -u zeev-device -f | grep '\[wake\]'
```

You want `[wake] openwakeword ready — zeev, sarina in ~5s`. A
`model not found` or `init failed` line means the path is wrong or the file
didn't survive the copy.

The stem of the filename is the key in `OWW_VOICE_MAP` — rename the files and
the map must follow, or the wake fires but the voice falls back to whatever the
transcript regex guesses.

## 4. Tune

Both knobs live in the same `.env`.

| Symptom | Knob | Direction |
|---|---|---|
| Fires at the TV / other people talking | `OWW_THRESHOLD` (0.5) | up, in steps of 0.1 |
| Misses you when you speak normally | `OWW_THRESHOLD` | down |
| Misses you when you speak *quietly* | `OWW_ENERGY_MULT` (1.8) | down |
| Runs hot / load above ~1.0 | `OWW_ENERGY_MULT` | up |
| Answers its own reply's tail | `OWW_SETTLE` (1.5s) | up |

Change **one at a time**. Every trigger is logged with its score:

```
[wake] zeev trigger (score 0.92) -> voice daniel
```

so after a false wake, `journalctl -u zeev-device | grep trigger` tells you which
model fired and how confidently — which is what the threshold should be set
against. A false wake scoring 0.95 is not a threshold problem and raising it will
only make Zeev deaf to you.

Two models cost essentially the same CPU as one (~70% of one core): the
melspectrogram and embedding stages are shared, only the small classifier head
runs per model.
