"""The sentence-batching rule behind streaming TTS.

_stream_speak is a closure inside run_device_mode (needs the HAT to import), so
the rule is reimplemented here. It is small and the failure modes are the ones
that make speech sound wrong rather than crash:

  - flushing too eagerly => staccato one-word utterances
  - flushing too late    => no latency win over the buffered path
  - dropping the tail    => the reply ends mid-thought
  - duplicating a chunk  => Zeev says the same sentence twice

If the regex or min_chars in zeev.py changes, change it here too.
"""
import re

import pytest

SENT_RE = re.compile(r"^(.*[.!?])(?:\s|$)", re.DOTALL)
MIN_CHARS = 45


def chunks_for(tokens, min_chars=MIN_CHARS, finalize=True):
    """Mirror of _stream_speak's flush loop. Returns the spoken chunks."""
    out, buf = [], ""
    for tok in tokens:
        buf += tok
        m = SENT_RE.match(buf)
        if m and len(m.group(1).strip()) >= min_chars:
            out.append(m.group(1).strip())
            buf = buf[m.end(1):].lstrip()
    if not finalize:
        return out
    tail = buf.strip()
    if tail:
        m = re.search(r"^(.*[.!?])\s*$", tail, re.DOTALL)
        if m:
            out.append(m.group(1).strip())
        elif not out:
            # Nothing spoken yet -- a reply with no terminator must still be
            # said. Otherwise the dangling fragment is dropped.
            out.append(tail)
    return out


def as_tokens(text, n=3):
    return [text[i:i + n] for i in range(0, len(text), n)]


def test_nothing_is_lost_or_duplicated():
    text = ("I'm doing well, thanks for asking. The weather today is clear and "
            "cold. Let me know if you want the forecast for tomorrow.")
    out = chunks_for(as_tokens(text))
    assert " ".join(out) == text, out


def test_short_sentences_are_batched_not_staccato():
    """"Sure." alone would be a 4-char utterance; it must ride along."""
    text = "Sure. Okay. Yes. That works well enough for what you described."
    out = chunks_for(as_tokens(text))
    assert all(len(c) >= 10 for c in out), out
    assert " ".join(out) == text


def test_first_chunk_arrives_before_the_whole_reply():
    """The entire point: speech starts on sentence one, not completion."""
    text = ("The first sentence is comfortably long enough to be spoken on its own. "
            "The second sentence arrives much later in the stream. "
            "And a third follows it.")
    toks = as_tokens(text)
    # finalize=False: mid-stream, nothing has ended yet -- so anything returned
    # is a chunk that was flushed before the reply finished generating.
    out_partial = chunks_for(toks[: len(toks) // 2], finalize=False)
    assert len(out_partial) >= 1, "no audio would start until generation ended"
    assert out_partial[0].endswith(".")


def test_dangling_fragment_is_dropped():
    """Matches the buffered path, which truncated to the last terminator."""
    text = "This is a complete sentence that is long enough. And this one is cut off"
    out = chunks_for(as_tokens(text))
    assert out[-1].endswith("."), out
    assert "cut off" not in " ".join(out)


def test_tail_without_any_terminator_is_still_spoken():
    """A reply with no punctuation at all must not be silently swallowed."""
    text = "no punctuation anywhere in this reply at all"
    assert chunks_for(as_tokens(text)) == [text]


def test_empty_stream_produces_nothing():
    assert chunks_for([]) == []
    assert chunks_for(["", "", ""]) == []


def test_question_and_exclamation_terminate():
    text = "Did you want the forecast for tomorrow morning? I can pull it up!"
    out = chunks_for(as_tokens(text))
    assert " ".join(out) == text
    assert out[0].endswith("?")


@pytest.mark.parametrize("size", [1, 2, 5, 17, 200])
def test_result_is_independent_of_token_size(size):
    """Providers chunk SSE arbitrarily; output must not depend on it."""
    text = ("Here is a first sentence of reasonable length. Here is a second one "
            "that is also long. And a third to finish things off.")
    # Batching may differ (a single huge delta flushes as one chunk), but the
    # spoken text must not.
    assert " ".join(chunks_for(as_tokens(text, size))) == \
           " ".join(chunks_for(as_tokens(text, 3)))


def test_decimal_numbers_do_not_split_early():
    """A period is not always a sentence end; min_chars limits the damage."""
    text = "The temperature is 41.5 degrees Fahrenheit right now outside."
    out = chunks_for(as_tokens(text))
    assert " ".join(out) == text
