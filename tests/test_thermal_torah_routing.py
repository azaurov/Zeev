"""Regression test for a gap found in the test-and-fix pass (2026-08-04):
the two thermal-question call sites (web /thermal SSE, terminal /thermal)
built a Torah-checkable `ctx` string but never checked it before picking the
model or token budget -- unlike the other three LLM call sites (terminal
main loop, web /chat, device mode), which all force MODELS["2"] (70B) and a
higher token cap for Torah/parsha content. Without the model override, a
thermal question that also touches Torah content stays on 8B and hits its
6k TPM limit; without the token override, the reply truncates -- the exact
failure class documented at length in CLAUDE.md's Torah RAG section
(the four-fixes-for-one-prayer saga), just reachable from an un-gated
fourth code path.

Both call sites build `ctx` the same way:
    f"[thermal camera] Min {min}°C, Max {max}°C, Center {center}°C, "
    f"hotspot at row {row}, col {col} (32×24 grid). {question}"

The HTTP handler and REPL loop themselves aren't unit-tested anywhere (no
mock HTTP server / terminal harness exists for either), so this pins the
part that actually matters and is testable in isolation: the gate that both
fixes rely on must fire on the exact ctx shape they construct, and must NOT
fire on an ordinary thermal question -- that's the behavior the fix depends
on and the thing a future regex change could quietly break.
"""
import pytest


def _thermal_ctx(question, min_t=68, max_t=74, center=71, row=12, col=16):
    return (f"[thermal camera] Min {min_t}°C, Max {max_t}°C, "
            f"Center {center}°C, hotspot at row {row}, col {col} "
            f"(32×24 grid). {question}")


@pytest.mark.parametrize("question", [
    "is it warm enough in here for shabbat services",
    "read me today's Torah portion while you're at it",
    "recite the shema",
])
def test_thermal_ctx_with_torah_content_triggers_the_gate(zeev, question):
    ctx = _thermal_ctx(question)
    assert zeev.needs_torah(ctx) or zeev.needs_parsha_reading(ctx), ctx


@pytest.mark.parametrize("question", [
    "is it too warm in here",
    "what's the hottest spot in the room",
    "should I turn the heat down",
])
def test_ordinary_thermal_ctx_does_not_trigger_the_gate(zeev, question):
    ctx = _thermal_ctx(question)
    assert not zeev.needs_torah(ctx)
    assert not zeev.needs_parsha_reading(ctx)
