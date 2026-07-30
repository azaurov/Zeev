"""route_model must actually reach the reasoning model.

MODELS["3"] was migrated off the retired deepseek-r1-distill to GPT-OSS 120B,
but _REASONING_RE was never revisited: it keyed on written-maths vocabulary
("theorem", "calculate", the bare "think through") that spoken questions almost
never contain. Measured 2026-07-30, *zero* of eight ordinary spoken word
problems reached it -- six went to 8B. The model was fine; nothing routed to it.

The negatives matter as much as the positives: over-routing sends casual chat to
a 120B model, and two of these are near misses by construction.
"""
import pytest

REASONING = [
    "If a train leaves at 3:40pm and the trip takes 95 minutes, what time does it arrive?",
    "what's 15 percent of 340",
    "how many days until December 25th",
    "if I leave at 5 and drive for 3 hours and 20 minutes when do I get there",
    "work it out for me: 12 times 47",
    "what is 288 divided by 16",
    "split 175 dollars between 4 people",
    "how much is 2/3 of 90",
    "think it through: which is heavier",
    "prove that the square root of two is irrational",
]

NOT_REASONING = [
    "tell me a joke",
    "play some jazz",
    "how are you doing",
    "who is Rambam",
    "remind me to call Dave at 4",   # has a digit, but is not arithmetic
    "how many nieces do I have",     # "how many" with no number: recall, not maths
    "check the basement cam",
    "what time is it",               # bare clock read, not elapsed time
    "what's on my calendar",
]


@pytest.mark.parametrize("text", REASONING)
def test_routes_to_reasoning_model(zeev, text):
    assert zeev.route_model(text) == zeev.MODELS["3"][0], \
        f"{text!r} should reach the reasoning model"


@pytest.mark.parametrize("text", NOT_REASONING)
def test_casual_text_avoids_reasoning_model(zeev, text):
    assert zeev.route_model(text) != zeev.MODELS["3"][0], \
        f"{text!r} must not pay for the reasoning model"


def test_reasoning_model_is_one_groq_actually_serves(zeev):
    """The bug that started this: a model id can silently stop existing."""
    assert zeev.MODELS["3"][0] == "openai/gpt-oss-120b"
    assert "deepseek" not in zeev.MODELS["3"][0], "deepseek R1 was retired by Groq"
    assert zeev.model_label(zeev.MODELS["3"][0]) == "GPT-OSS"
