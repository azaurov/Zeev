"""Regression tests for the pre-LLM intent gates.

Every capability in device mode is selected by one of these hand-written
regexes before the model ever sees the transcript, so a gate that is too greedy
silently hijacks normal conversation and one that is too strict silently drops
a feature. They are pure functions and were completely untested; the git log is
largely a list of the bugs that cost:

  d56f0a7  BT call intent gate rejected natural vocative lead-ins
  0f0fed3  use 'dial' not 'call' as the BT phone-call trigger word
  8245ff7  don't trigger language switch for content requests
  6e75f4b  instruct LLM to output real Hebrew script

Each of those has a named test below. When a gate is retuned, expect to change
these -- that is the point: the change becomes visible instead of silent.
"""
import pytest


# ---------------------------------------------------------------------------
# Phone-call intent  (_bt_call_match / _BT_CALL_RE)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "dial 555-123-4567",
    "dial +1 (617) 555-0198",
    "phone my mom",
    "ring dad",
    "Hi Serena, can you dial my wife at 555-123-4567",
])
def test_call_intent_matches(zeev, text):
    assert zeev._bt_call_match(text) is not None, text


@pytest.mark.parametrize("text", [
    "I'll call you back later",
    "let's call it a day",
    "call me old fashioned but I like vinyl",
    "remind me to call the dentist",
])
def test_call_intent_ignores_casual_call(zeev, text):
    """0f0fed3: 'call' is too common in speech, so 'dial' is the trigger.

    These must fall through to normal chat rather than dialing anyone.
    """
    assert zeev._bt_call_match(text) is None, text


def test_call_intent_allows_vocative_lead_in(zeev):
    """d56f0a7: a greeting before the command used to push the match past a
    char-position gate, and the whole utterance fell through to plain chat."""
    text = "Hi Serena, can you dial my wife Maria at 555-867-5309 please"
    assert zeev._bt_call_match(text) is not None


def test_call_intent_ignores_late_mention(zeev):
    """A long ramble that merely mentions dialling is not a call request."""
    text = ("So I was thinking about the trip and the hotel and the rental car "
            "and all of the things we still have to sort out before friday, and "
            "then I remembered I should dial 555-123-4567 at some point")
    assert zeev._bt_call_match(text) is None


# ---------------------------------------------------------------------------
# Language switch  (lang_switch_intent)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,code", [
    ("speak Russian", "ru"),
    ("talk to me in Spanish", "es"),
    ("switch to Hebrew", "he"),
    ("say it in English", "en"),
])
def test_lang_switch_matches(zeev, text, code):
    assert zeev.lang_switch_intent(text) == code, text


@pytest.mark.parametrize("text", [
    "say the Hebrew alphabet",
    "spell a Russian word",
    "what's the Spanish word for cat",
    "teach me a Hebrew phrase",
])
def test_lang_switch_ignores_content_requests(zeev, text):
    """8245ff7: naming a language as a *topic* is not a request to switch.

    Guarded by a preceding article and a following content noun.
    """
    assert zeev.lang_switch_intent(text) is None, text


def test_lang_switch_ignores_incidental_mention(zeev):
    """The classic false positive the proximity rule exists for."""
    assert zeev.lang_switch_intent("I have a Russian friend from work") is None


@pytest.mark.parametrize("text", [
    "Yes, that was the tefillin angelic prayer, the one you just recited. "
    "I need help with that. Can you also say it in Hebrew?",
    "Can you also say it in Hebrew?",
    "say it in Hebrew",
    "can you say the prayer in Hebrew",
    "say that again in Russian",
    "read this in Spanish",
])
def test_lang_switch_ignores_translation_requests(zeev, text):
    """Live 2026-07-31: 'Can you also say it in Hebrew?' answered with
    'switching to Hebrew' and never recited the prayer.

    An object between the verb and the language name ("it", "the prayer") means
    render THAT in the language -- a content request, not a mode change. The
    trigger sat at the end of the utterance, so this was never a truncation
    bug even though the 8s record cap was broken at the same time.
    """
    assert zeev.lang_switch_intent(text) is None, text


@pytest.mark.parametrize("text", [
    "say it in English",
    "say that in English",
    "answer in English",
])
def test_lang_switch_english_is_the_escape_hatch(zeev, text):
    """English is exempt from the object guard on purpose.

    FORCED_LANG is sticky. Reading "say it in English" as a content request
    would leave a user stuck in Hebrew with no spoken way back, so English
    over-triggers toward the default state by design.
    """
    assert zeev.lang_switch_intent(text) == "en", text


@pytest.mark.parametrize("text", [
    "talk to me in Spanish",
    "speak to me in Hebrew",
    "answer in Russian from now on",
    "switch to Hebrew",
    "reply in Spanish please",
])
def test_lang_switch_still_matches_real_switches(zeev, text):
    """The object guard must not eat indirect objects ("to me") or adverbials."""
    assert zeev.lang_switch_intent(text) is not None, text


@pytest.mark.parametrize("text", [
    "Can you also say it in Hebrew?",
    "recite the prayer in Hebrew",
    "read that in Hebrew",
])
def test_hebrew_content_requests_get_the_script_instruction(zeev, text):
    """Falling through the switch gate is only half the fix.

    The turn now reaches the LLM with lang='en', so _HE_CONTENT_RE is what
    tells it to answer in Hebrew script; _speak_device then picks Hebrew TTS
    off the script itself. Without this the reply comes back transliterated
    and is spoken as English.
    """
    assert zeev._HE_CONTENT_RE.search(text), text


# ---------------------------------------------------------------------------
# Bluetooth intents  (extract_bt_intent)
# ---------------------------------------------------------------------------

def test_bt_intent_returns_none_for_plain_chat(zeev):
    assert zeev.extract_bt_intent("how are you doing today") is None


def test_bt_intent_disconnect_not_read_as_connect(zeev):
    """'disconnect' contains 'connect'; ordering in extract_bt_intent matters."""
    got = zeev.extract_bt_intent("disconnect my headphones")
    assert got in ("disconnect", None), got
    if got is not None:
        assert got != "connect"


def test_bt_intent_values_are_from_the_documented_set(zeev):
    valid = {"scan", "pair", "connect", "disconnect", "tether_on", "tether_off", None}
    for text in ["scan for bluetooth devices", "pair with my headphones",
                 "connect my headphones", "disconnect bluetooth",
                 "turn on tethering", "turn off tethering", "hello there"]:
        assert zeev.extract_bt_intent(text) in valid, text


# ---------------------------------------------------------------------------
# Music  (extract_music_query)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,query", [
    ("play Dark Side of the Moon", "Dark Side of the Moon"),
    ("put on some Miles Davis", "some Miles Davis"),
    ("can you play Hallelujah", "Hallelujah"),
    ("i want to hear Zeppelin", "Zeppelin"),
])
def test_music_query_extracted(zeev, text, query):
    assert zeev.extract_music_query(text) == query


@pytest.mark.parametrize("text", [
    "what are you playing at",
    "the play was excellent",
    "how do I play chess",
])
def test_music_query_anchored_to_start(zeev, text):
    """_MUSIC_RE uses .match(), so 'play' mid-sentence must not trigger."""
    assert zeev.extract_music_query(text) is None, text


# ---------------------------------------------------------------------------
# Search / weather  (needs_search, needs_weather)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "what's the weather like",
    "any news today",
    "what's the latest on the election",
    "who won the game",
])
def test_needs_search_true(zeev, text):
    assert zeev.needs_search(text) is True, text


@pytest.mark.parametrize("text", [
    "tell me a story",
    "how do I write a for loop",
    "what is the meaning of life",
])
def test_needs_search_false(zeev, text):
    assert zeev.needs_search(text) is False, text


def test_weather_is_a_subset_of_search(zeev):
    """_build_system_prompt appends the spoken-units instruction only for
    weather, and relies on the search branch having fired first."""
    for text in ["what's the weather", "give me the forecast",
                 "how hot is it", "how cold is it"]:
        assert zeev.needs_weather(text) is True, text
        assert zeev.needs_search(text) is True, text


def test_weather_narrower_than_search(zeev):
    assert zeev.needs_search("any news today") is True
    assert zeev.needs_weather("any news today") is False


# ---------------------------------------------------------------------------
# Calendar  (_CALENDAR_RE)
# ---------------------------------------------------------------------------

def test_remind_me_now_reaches_the_reminder_tool(zeev):
    """"remind me to call Dave at 4" used to match only the calendar regex,
    fire a calendar *read*, and silently discard the intent.

    _CALENDAR_RE still matches it -- that only injects calendar context and is
    harmless -- but the tool gate must also match, which is what actually
    schedules the reminder.
    """
    text = "remind me to call Dave at 4"
    assert zeev._TOOL_INTENT_RE.search(text) is not None
    assert zeev._CALENDAR_RE.search(text) is not None


@pytest.mark.parametrize("text", [
    "what's on my calendar",
    "do i have anything tomorrow",
    "am i free this afternoon",
])
def test_calendar_regex_matches_real_queries(zeev, text):
    assert zeev._CALENDAR_RE.search(text) is not None, text


# ---------------------------------------------------------------------------
# Torah / parsha
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "what's this week's parsha",
    "read me the portion",
    "what is parashat this week",
])
def test_parsha_reading_matches(zeev, text):
    assert zeev.needs_parsha_reading(text) is True, text


def test_parsha_reading_ignores_unrelated(zeev):
    assert zeev.needs_parsha_reading("what's the weather") is False


def test_needs_torah_requires_the_db(zeev):
    """needs_torah is gated on TORAH_DB existing, so it must be False (not
    raise) on a machine without the corpus."""
    assert zeev.needs_torah("tell me about the Talmud") in (True, False)


# ---------------------------------------------------------------------------
# Model routing  (route_model)
# ---------------------------------------------------------------------------

def test_route_model_returns_a_known_model(zeev):
    known = {mid for mid, _ in zeev.MODELS.values()}
    for text in ["hi there", "prove that sqrt 2 is irrational",
                 "write me a python class", "what's up"]:
        assert zeev.route_model(text) in known, text


def test_route_model_defaults_to_fast(zeev):
    """Casual chat must not burn the 70B/reasoning budget."""
    assert zeev.route_model("hey how's it going") == zeev.MODELS["1"][0]


def test_route_model_escalates_for_reasoning(zeev):
    """Reasoning prompts should not be answered by the 8B model."""
    got = zeev.route_model("solve this step by step: if x^2 = 16 what is x")
    assert got != zeev.MODELS["1"][0]


# ---------------------------------------------------------------------------
# Gates must tolerate junk rather than raise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("junk", ["", "   ", "!!!", "?", "\n", "123", "ה", "привет"])
def test_gates_never_raise_on_junk(zeev, junk):
    """STT hands these over regularly. A raise here kills the turn thread."""
    zeev.needs_search(junk)
    zeev.needs_weather(junk)
    zeev.needs_parsha_reading(junk)
    zeev.needs_gps(junk)
    zeev.extract_music_query(junk)
    zeev.extract_bt_intent(junk)
    zeev.lang_switch_intent(junk)
    zeev._bt_call_match(junk)
    zeev.route_model(junk)


# ---------------------------------------------------------------------------
# Named liturgical prayers  (_TORAH_RE / _ANGEL_PRAYER_RE)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    # Grounded: these retrieve from data/torah.db (Siddur Ashkenaz, weekday).
    "what is Avinu Malkeinu",
    "teach me Ma Tovu",
    "asher yatzar",
    "what is tachanun",
    # NOT grounded: the imported Siddur is weekday-only, so Shabbat, festival
    # and life-cycle liturgy is absent. Matching still routes to 70B instead of
    # letting the 8B answer cold, which is the point.
    "the Shehecheyanu blessing",
    "what's Lecha Dodi",
    "recite Ana Bekoach",
    "kol nidre on yom kippur",
    "mi shebeirach for healing",
    "tefillat haderech before a trip",
    "the priestly blessing",
    "how do I daven mincha",
])
def test_named_prayers_reach_the_torah_path(zeev, text):
    """Live 2026-07-31: "help me with the angelic prayer" matched nothing, so
    it reached the 8B with no RAG and no routing and invented both an English
    text and a Hebrew one, naming a prayer ("Tefillat HaShalom") that does not
    exist.
    """
    assert zeev._TORAH_RE.search(text) or zeev._ANGEL_PRAYER_RE.search(text), text


@pytest.mark.parametrize("text", [
    "help me with the angelic prayer",
    "say the angelic prayer in Hebrew",
    "what is the prayer about angels",
])
def test_angelic_prayer_needs_a_prayer_word_nearby(zeev, text):
    """Not a standard liturgical name -- it is what Alex called it."""
    assert zeev._ANGEL_PRAYER_RE.search(text), text


@pytest.mark.parametrize("text", [
    "she has an angelic voice",
    "my daughter looks like an angel",
    "call my angel investor about the deal",
])
def test_bare_angelic_is_ordinary_english(zeev, text):
    """Why "angelic" is NOT in _TORAH_RE's flat alternation: that alternation
    has no way to express proximity, and the bare word is common speech."""
    assert not zeev._ANGEL_PRAYER_RE.search(text), text
    assert not zeev._TORAH_RE.search(text), text


# --- "call" needs a number to count ---------------------------------------

@pytest.mark.parametrize("text", [
    "Can you call my wife Maria? It's 857-701-7252 and sing her the happy birthday song.",
    "call 857-701-7252",
    "calling Maria at (857) 701-7252 please",
    "dial 857-701-7252",
])
def test_call_intent_detected(zeev, text):
    assert zeev._bt_call_match(text)


@pytest.mark.parametrize("text", [
    "Let's call it a night",
    "I'll call you back later",
    "remind me to call Dave at 4",
    "call my wife",
    "give me a call sometime",
    "set a timer for 20 minutes",
])
def test_bare_call_is_not_a_dial(zeev, text):
    """"call" was dropped as a trigger because it is everywhere in speech. A
    spoken PHONE NUMBER is what makes it unambiguous -- nothing else in casual
    conversation looks like one."""
    assert not zeev._bt_call_match(text)


def test_a_short_number_is_not_a_phone_number(zeev):
    """"call me at 4" and "call room 210" must not dial."""
    assert not zeev._bt_call_match("call me at 4")
    assert not zeev._bt_call_match("call room 210")
