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

**Two fixes belong in cell 1 before you run anything**, both on the negative
clip path — which means cell 11 generates all 3000 positive clips and runs a
full fifteen minutes before either one surfaces.

1. **`deep-phonemizer` is missing from the pip block.** Add it next to
   `pronouncing` so a Run-all carries it. Without it: `ModuleNotFoundError: No
   module named 'dp'`.
2. **PyTorch 2.6 broke DeepPhonemizer's checkpoint load.** `torch.load`
   flipped its `weights_only` default to `True`, and the checkpoint pickles a
   `dp.preprocessing.text.Preprocessor`, so it raises
   `_pickle.UnpicklingError: Weights only load failed`. Patch the single
   `torch.load` call in `dp/model/model.py` (line 306 in 0.0.19):

   ```python
   import dp.model.model as _m, ast
   _p = _m.__file__
   _src = open(_p).read()
   _old = 'checkpoint = torch.load(checkpoint_path, map_location=device)'
   if 'weights_only' not in _src:
       assert _src.count(_old) == 1
       _src = _src.replace(_old, _old[:-1] + ', weights_only=False)')
       ast.parse(_src)                 # never write a file you haven't parsed
       open(_p, 'w').write(_src)
   ```

   `weights_only=False` permits arbitrary code execution from the checkpoint.
   It's the standard fix here and the file comes from DeepPhonemizer's own S3
   bucket — the same source openWakeWord has always pulled it from — but that
   is the trade being made.

Note the shape of that patch: exact-string match, asserted unique, parsed
before writing. That is the difference between this and the Colab assistant's
line-index rewrite that corrupted `data.py`.

- **Free Colab (T4)**: ~2.5 h, and free sessions get reclaimed when idle — keep
  the tab open and the machine awake. **Do not leave it running overnight on
  the free tier.** A reclaimed runtime wipes `/content` entirely: the 17 GB
  ACAV download, the generated clips, the config, all of it. Verified the hard
  way. Unattended runs are what Colab Pro's background execution is for.
- **Colab Pro (L4 + High RAM)**: ~75–90 min, $10/month, about 1/100th of the
  monthly credits per run. An A100 buys nothing; the job is network- and
  CPU-bound.

### Never accept Colab's "fix this error" suggestion — this cost a whole run

Colab offers an AI assistant that patches errors for you. On this notebook it
rewrites **library source in place, by line index**, and when the patch misses
it leaves the file syntactically broken. Observed live: an inserted "Patch G"
cell that ran `git checkout -- openwakeword/data.py`, re-applied itself, printed
`(Success: False)`, and left `data.py` with a `return` outside a function. Every
subsequent run restored the file and re-broke it, so the error kept coming back
at a *different line number* — which is exactly what makes it look like a
mysterious moving target rather than one bad cell.

The tell: a `SyntaxError` inside an installed package that a fresh clone parses
cleanly. If you see that, the runtime has been mutated. Don't patch further —
`Runtime → Disconnect and delete runtime` and start clean from the GitHub copy,
not from your saved one (your saved copy contains the injected cells).

If a cell genuinely fails, the notebook's own cells are all idempotent and
cache-aware — re-running is safe and cheap. Fix the cause, don't rewrite the
library.

Run it twice, once per phrase.

### Raise the sample counts — the notebook's defaults undertrain badly

The notebook ships a reduced config tuned for a fast demo run, not a usable
model. Measured on the first `zeev` run: **recall 0.44, 2.0 false positives per
hour** — it would miss more than half of what you say. Upstream's own
`examples/custom_model.yml` targets **0.2 fp/hr**, ten times better.

| Key | Notebook | Upstream default | Use |
|---|---|---|---|
| `n_samples` | 2000 | 10000 | **10000** |
| `n_samples_val` | 1000 | 2000 | **2000** |
| `steps` | 20000 | 50000 | **50000** |

Editing these in cell 10 costs roughly half an hour more: TTS generation in
cell 11 scales linearly with `n_samples` (~15 min at 10000), while training
itself is cheap — 20000 steps measured at 1.9 min on a T4, so 50000 is about
five.

**A short phrase needs the higher counts most.** `zeev` at 2000 samples was the
worst case: fewest samples, fewest syllables. If recall still comes out below
~0.85 at 10000 samples, lengthen the phrase to `['hey zeev', 'hey ze ev']`
rather than spending another run on tuning.

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
