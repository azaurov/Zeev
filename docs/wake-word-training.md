# Training custom wake words for Zeev

Everything on the device side is already built and proven with stock models. Two
trained `.onnx` files and two `.env` lines are all that is left.

## 1. Pick the phrase — length is the single biggest lever

openWakeWord's false-accept rate is dominated by phrase length. Short targets
fire on ordinary speech and television constantly.

- **"Sarina"** — three syllables. Looked fine on paper; measured ~1.4 fp/hr
  against real household conversation (see §3), which is 7× upstream's target.
  Superseded by "Hey Sarina". Three syllables was not enough.
- **"Ze'ev"** (zeh-EV) — two syllables, and genuinely better than the
  one-syllable "Zeev". Two is workable but it is the shortest thing worth
  training; expect to spend some time on the threshold.
- **"Zeev"** as one syllable — don't. It would make the false-wake problem
  worse, not better, after ninety minutes of GPU time.
- **"Hey Ze'ev" / "Hey Sarina"** — 4 syllables each, and **this is what is now
  being trained**, the short forms having measured too hot. The shared "Hey"
  onset is the lesser risk; if the two models cross-trigger, the fix is
  `OWW_THRESHOLDS` (per-model, §3), which now exists.

The empirical route was taken and it decided the question: `Ze'ev` and `Sarina`
were trained and lived with, and the log showed real false wakes on ordinary
conversation rather than a threshold wanting a nudge. Hence the `Hey` prefix.
Worth noting the short forms were **not** cheap to disprove — two runs on
`zeev`, one on `sarina` — but validation metrics could not have told us this,
only the mic could.

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

### The current run: "Hey Sarina" and "Hey Ze'ev"

Decided 2026-07-29 on the strength of the live false-wake data below — the
three-syllable `sarina` measured ~1.4 fp/hr against real household
conversation, so both phrases move to the four-syllable `Hey` form.

```python
# run 1
TARGET_PHRASE = ['hey sarina']
MODEL_NAME    = 'hey_sarina'

# run 2 — note 'hey zeev' is deliberately absent, see below
TARGET_PHRASE = ['hey ze ev', 'hey zeh ev']
MODEL_NAME    = 'hey_zeev'

# both runs, per the table above
n_samples, n_samples_val, steps = 10000, 2000, 50000
```

`'hey zeev'` is omitted on purpose: bare `zeev` phonemizes to `[Z][IY][V]`, one
syllable, so including it would again train two different words as one positive
class — the exact mistake that cost run 1. Check the phonemizer line in cell 11
for **both** remaining variants and confirm they agree before letting the run
finish.

**`MODEL_NAME` is the prediction key, so the `.env` must move with it.** The
filename stem is what openWakeWord keys on, so renaming the phrase renames the
key and the old `OWW_VOICE_MAP` silently stops matching — right wake word, wrong
voice, no error. Both lines change together:

```
OWW_MODEL_PATH=/home/ragnar/oww/hey_zeev.onnx,/home/ragnar/oww/hey_sarina.onnx
OWW_VOICE_MAP=hey_zeev:daniel,hey_sarina:sarina
```

Keep the old `zeev.onnx`/`sarina.onnx` on the Pi until the new pair is measured
against a real mic — reverting is then two `.env` edits, not another 90 minutes.

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

### Keep every variant on the same pronunciation

Cell 11 logs how the phrase was phonemized. For `zeev` it printed:

```
WARNING:root:Phones for 'zeev': [Z][IY][V]
```

One syllable — "Zeve", rhyming with *leave*, not "zeh-EV". So
`['zeev', 'ze ev', 'zeh ev']` trained on **two different words at once**,
splitting the positive class and blurring it. Dropping `'zeev'` and keeping
`['ze ev', 'zeh ev']` puts every sample on one target.

Measured across the two runs:

| Run | Samples | Variants | recall | fp/hr |
|---|---|---|---|---|
| 1 | 2000 | mixed 1- and 2-syllable | 0.44 | 2.00 |
| 2 | 10000 | 2-syllable only | 0.72 | 0.95 |

**Read the phonemizer line before letting a run finish.** It is the only place
the notebook tells you what it actually thinks the word sounds like.

### Validation metrics are measured on TTS voices, not yours

`recall` and `fp/hr` come from synthetic speech. They rank models usefully but
they are not your voice on the Whisplay mic. Before spending another 45-minute
run on a marginal model, put the `.onnx` on the Pi and count real triggers out
of twenty — that measurement is ten minutes and it is the one that decides.

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

Observed with both custom models loaded (warm restart, not a cold boot):

```
[wake] openwakeword ready — zeev, sarina in 4.8s, threshold 0.5, avail 140M, swap 22M
```

A `model not found` or `init failed` line means the path is wrong or the file
didn't survive the copy.

**That line is also how you verify the prediction keys**, and it is worth
reading rather than skimming. A trained model does not carry its name: the ONNX
graph output is just `output` (input `onnx::Flatten_0`), so openWakeWord derives
the key from the **filename stem**. Rename `sarina.onnx` and the key changes
with it — the wake still fires, but `OWW_VOICE_MAP` misses and the voice falls
back to whatever the transcript regex guesses, which surfaces much later as
"right wake word, wrong voice" rather than as an error. The ready line
enumerating exactly `zeev, sarina` is the confirmation.

Note also that both files are byte-for-byte the same **size** (790,682) — same
classifier-head architecture, different weights. Size is not a way to tell them
apart; use `md5sum` after the copy.

**`OWW_THRESHOLD` is global; `OWW_THRESHOLDS` overrides it per model.**

```
OWW_THRESHOLDS=sarina:0.80,zeev:0.55
```

Stems not listed keep the global value, and the ready line annotates the ones
that are (`zeev, sarina@0.80`) — a typo'd stem shows up there as a *missing*
annotation rather than as a phrase that quietly kept the global. Thresholding
happens **per model before the max is taken**, so raising one model's bar can't
mask a quieter model that legitimately fired. Pinned by `tests/test_wake_gate.py`.

This exists because a single global number made two models of unequal quality
mutually exclusive to tune. Don't reach for it first, though — see below for why
it usually isn't the fix.

### A threshold cannot separate overlapping distributions

Measured live over the `sarina` model's whole first session — 2026-07-29
19:25–23:00, **3 h 35 min** of ordinary household conversation, 13 triggers:

| | scores |
|---|---|
| `sarina` real wakes | 0.58, 0.60, 0.74, 0.80, 0.90, 0.93, 0.96, 0.98 |
| `sarina` false wakes | 0.52, 0.59, 0.64, 0.73 |
| `zeev` false wakes | 0.51 |
| `zeev` real wakes | none in the window |

**The ranges overlap** (real 0.58–0.98 against false 0.51–0.73), so no cut
exists. It is tempting to read the two lowest reals as false wakes that happened
to capture intentional speech — that yields a tidy break at 0.73/0.74 — but both
fired 4–6 s after the previous reply finished, well past `OWW_SETTLE`, with
distinct scores (the stale-buffer signature is *identical* scores), and one of
them was "That's not right" seconds after Zeev gave the wrong time. That is a
person addressing the device. Take the plain reading.

Note also that **5 false wakes in 3 h 35 min — ~1.4 fp/hr — is a phrase-length
problem, not a tuning problem**: upstream targets 0.2 fp/hr, so this is seven
times over. (The listener only runs in `idle`/`ready`, so true armed time is a
little under the wall clock and the real rate a little above 1.4.) Three syllables
turned out not to be enough against real conversation, so the fix is the `Hey`
prefix from §1: retrain as `['hey sarina']`. No noise guard can help here
either; all four false transcripts were fluent multi-word speech and sail
through the `\w{2,}` check by construction.

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
