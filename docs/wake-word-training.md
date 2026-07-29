# Training custom wake words for Zeev

Everything on the device side is already built and proven with stock models. Two
trained `.onnx` files and two `.env` lines are all that is left.

## 1. Pick the phrase — use "Hey Zeev" and "Hey Sarina", not the bare names

openWakeWord's false-accept rate is dominated by phrase length. **"Zeev" is one
syllable**, and a one-syllable target fires on ordinary speech and television
constantly — training it would make the false-wake problem worse, not better,
after ninety minutes of GPU time. "Hey Zeev" and "Hey Sarina" are 3–4 syllables
and sit in the range these models are good at.

Both phrases share the "Hey" onset, which is the lesser risk. If they end up
cross-triggering, the fix is per-model thresholds, not retraining — ask and it
gets added.

## 2. Train

<https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing>

openWakeWord's own automatic training notebook. Free, runs on a Colab T4,
roughly 75–90 minutes per phrase, unattended — the only input is the phrase
text; it synthesizes its own training audio. Run it twice, once per phrase, and
download the resulting `.onnx` from each run.

Colab disconnects idle sessions, so keep the tab open or check back.

## 3. Install

```bash
ssh ragnar@ragnarok "mkdir -p ~/oww"
scp hey_zeev.onnx hey_sarina.onnx ragnar@ragnarok:~/oww/
```

Then in `/home/ragnar/Zeev/.env`:

```
OWW_MODEL_PATH=/home/ragnar/oww/hey_zeev.onnx,/home/ragnar/oww/hey_sarina.onnx
OWW_VOICE_MAP=hey_zeev:daniel,hey_sarina:sarina
```

`sudo systemctl restart zeev-device`, then watch it arm:

```bash
journalctl -u zeev-device -f | grep '\[wake\]'
```

You want `[wake] openwakeword ready — hey_zeev, hey_sarina in ~5s`. A
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
[wake] hey_zeev trigger (score 0.92) -> voice daniel
```

so after a false wake, `journalctl -u zeev-device | grep trigger` tells you which
model fired and how confidently — which is what the threshold should be set
against. A false wake scoring 0.95 is not a threshold problem and raising it will
only make Zeev deaf to you.

Two models cost essentially the same CPU as one (~70% of one core): the
melspectrogram and embedding stages are shared, only the small classifier head
runs per model.
