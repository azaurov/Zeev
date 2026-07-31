"""The weekly Torah portion must not open with the previous portion.

torah.db stores one row per *chapter* with no verse markers, but a parsha
routinely starts mid-chapter -- Eikev is Deuteronomy 7:12-11:25. _parse_torah_ref
discarded the verse numbers entirely and get_parsha_text handed over whole
chapters under a header naming the exact reference, while the prompt said "read
the text above from the beginning". Asked for Eikev, Zeev opened at Deut 7:1 --
eleven verses inside Va'etchanan -- and called it Eikev.

That is the wrong-city failure class: stated as fact, with nothing in the reply
for the listener to catch it by. The text cannot be trimmed without verse
markers, so the boundary is stated instead.
"""
import pytest


def test_verse_bounds_are_returned_not_discarded(zeev):
    book, chapters, first_v, last_v = zeev._parse_torah_ref("Deuteronomy 7:12-11:25")
    assert book == "Deuteronomy"
    assert chapters == [7, 8, 9, 10, 11]
    assert (first_v, last_v) == (12, 25), "verse bounds dropped -- the original bug"


def test_cross_book_and_plain_refs_still_parse(zeev):
    assert zeev._parse_torah_ref("Numbers 16:1-18:32") == ("Numbers", [16, 17, 18], 1, 32)
    # Some Hebcal refs name the book again after the dash.
    b, ch, fv, lv = zeev._parse_torah_ref("Genesis 47:28-Genesis 50:26")
    assert b == "Genesis" and ch[0] == 47 and ch[-1] == 50 and (fv, lv) == (28, 26)


def test_unparseable_ref_is_four_values_not_two(zeev):
    """The arity changed; a caller unpacking two would raise at runtime."""
    assert zeev._parse_torah_ref("nonsense") == (None, [], 0, 0)
    assert zeev.get_parsha_text({"torah": "nonsense"}) == ""
    assert zeev.get_parsha_text({}) == ""


@pytest.mark.skipif(not __import__("pathlib").Path(
    "/home/azaurov/Zeev/zeev/data/torah.db").exists(), reason="torah.db not present")
def test_mid_chapter_start_is_flagged(zeev):
    t = zeev.get_parsha_text({"torah": "Deuteronomy 7:12-8:20"})
    assert "begins at verse 12" in t
    assert "belongs to the previous portion" in t
    assert "ends at verse 20" in t
    assert t.startswith("[Deuteronomy 7"), "each chapter must be labelled"


@pytest.mark.skipif(not __import__("pathlib").Path(
    "/home/azaurov/Zeev/zeev/data/torah.db").exists(), reason="torah.db not present")
def test_portion_starting_at_verse_one_claims_no_overlap(zeev):
    """Most portions do start at verse 1; they must not gain a spurious warning."""
    t = zeev.get_parsha_text({"torah": "Numbers 16:1-17:5"})
    assert "belongs to the previous portion" not in t
    assert t.startswith("[Numbers 16")


def test_prompt_no_longer_says_read_from_the_beginning(zeev):
    """The instruction and the data disagreed: the text starts at the chapter,
    the reference does not."""
    src = open("/home/azaurov/Zeev/zeev/zeev.py").read()
    assert "Read the text above from the beginning" not in src
