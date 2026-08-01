"""Autonomous calendar reminders: what earns a spoken reminder, and when.

Fixtures mirror the SHAPES seen on the real calendar (checked 2026-08-01), which
is the only reason the classifier is exclusion-based rather than keyword-based:
178 events in 45 days, 155 of them four daily habit blocks, and the genuine
appointments titled "psychiatry" / "Haircut w/Julie" / "physical therapy" --
not one containing the word "appointment". An allowlist misses all of them.
"""
import time
import datetime as dt
import pytest

HOUR = 3600
DAY = 86400


def _timed(summary, start_dt, **kw):
    e = {"id": kw.pop("id", "evt1"), "summary": summary, "status": "confirmed",
         "start": {"dateTime": start_dt.isoformat()}}
    e.update(kw)
    return e


def _allday(summary, date_str, **kw):
    e = {"id": kw.pop("id", "evt2"), "summary": summary, "status": "confirmed",
         "start": {"date": date_str}}
    e.update(kw)
    return e


def _soon(hours=48):
    return dt.datetime.now().astimezone() + dt.timedelta(hours=hours)


# --- classification -------------------------------------------------------

def test_daily_habit_block_is_not_an_event(zeev):
    """"Dinner" 45x in 45 days is a habit, not something to announce."""
    e = _timed("\U0001F37D️ Dinner", _soon(), recurringEventId="r1")
    assert zeev.gcal_event_category(e, {"dinner"}) is None


def test_fortnightly_recurrence_survives(zeev):
    """Recycling collection recurs but 3x in 45 days -- and putting the bins
    out is exactly the kind of thing worth being told about."""
    e = _timed("Recycling collection at Canton", _soon(), recurringEventId="r2")
    assert zeev.gcal_event_category(e, set()) is not None


def test_appointment_without_the_word_appointment(zeev):
    """The real failure an allowlist would have caused."""
    assert zeev.gcal_event_category(_timed("psychiatry", _soon()), set()) == "appointment"
    assert zeev.gcal_event_category(_timed("Haircut w/Julie", _soon()), set()) == "appointment"


def test_unrecognised_timed_event_still_counts(zeev):
    assert zeev.gcal_event_category(_timed("Coffee with Boris", _soon()), set()) == "event"


def test_free_marked_event_is_skipped(zeev):
    e = _timed("submit job searches", _soon(), transparency="transparent")
    assert zeev.gcal_event_category(e, set()) is None


def test_declined_event_is_skipped(zeev):
    e = _timed("Team sync", _soon(),
               attendees=[{"self": True, "responseStatus": "declined"}])
    assert zeev.gcal_event_category(e, set()) is None


def test_cancelled_event_is_skipped(zeev):
    e = _timed("Standup", _soon(), status="cancelled")
    assert zeev.gcal_event_category(e, set()) is None


def test_plain_allday_event_is_skipped(zeev):
    assert zeev.gcal_event_category(_allday("Real Estate Tax due", "2026-09-01"), set()) is None


# --- birthdays: the ordering that makes or breaks the feature -------------

def test_birthday_survives_every_later_exclusion(zeev):
    """Google files contact birthdays as all-day AND transparent AND recurring.
    Each of those is independently a skip, so checking birthdays first is
    load-bearing, not stylistic -- get the order wrong and birthdays, half of
    what was asked for, silently never fire."""
    e = _allday("Gabriel Zaurov's birthday", "2026-05-09",
                eventType="birthday", transparency="transparent",
                recurringEventId="b1")
    assert zeev.gcal_event_category(e, {"gabriel zaurov's birthday"}) == "birthday"


def test_anniversary_is_a_birthday_to_google(zeev):
    e = _allday("María Zaurova's anniversary", "2026-03-31", eventType="birthday",
                transparency="transparent")
    assert zeev.gcal_event_category(e, set()) == "birthday"


def test_hand_typed_birthday_has_no_eventType(zeev):
    """"Hannah's birthday" and "Mail out bday" are ordinary events -- hence the
    regex alongside the eventType check."""
    assert zeev.gcal_event_category(_allday("Hannah's birthday ", "2027-02-02"), set()) == "birthday"
    assert zeev.gcal_event_category(_allday("Mail out bday", "2027-05-02"), set()) == "birthday"


# --- timing ----------------------------------------------------------------

def test_allday_anchor_is_local_9am_not_utc(zeev):
    """A bare date has no zone. Anchored in UTC this lands at 5am local (or on
    the wrong day) -- the failure class _now_str() already documents."""
    ts, all_day = zeev.gcal_event_start(_allday("x", "2026-05-09"))
    assert all_day
    local = dt.datetime.fromtimestamp(ts)
    assert (local.hour, local.minute) == (zeev._GCAL_ALLDAY_HOUR, 0)
    assert local.date() == dt.date(2026, 5, 9)


def test_timed_event_keeps_its_offset(zeev):
    e = _timed("psychiatry", dt.datetime(2026, 8, 3, 14, 30))
    ts, all_day = zeev.gcal_event_start(e)
    assert not all_day
    assert dt.datetime.fromtimestamp(ts).hour == 14


def test_appointment_gets_a_day_and_an_hour_of_warning(zeev):
    now = time.time()
    e = _timed("physical therapy", _soon(72), id="pt")
    plan = zeev.gcal_reminder_plan(e, now, set())
    leads = sorted(round((s - d) / 60) for _, d, _, s in plan)
    assert leads == [60, 1440]
    assert {k for k, _, _, _ in plan} == {"pt:60", "pt:1440"}


def test_key_separates_leads_of_one_event(zeev):
    """One event carries several reminders; if they shared a key each scan
    would overwrite the other and only one would ever survive."""
    plan = zeev.gcal_reminder_plan(_timed("Interview", _soon(72), id="i1"), time.time(), set())
    assert len({k for k, _, _, _ in plan}) == len(plan) > 1


def test_past_events_produce_nothing(zeev):
    past = dt.datetime.now().astimezone() - dt.timedelta(hours=3)
    assert zeev.gcal_reminder_plan(_timed("gone", past), time.time(), set()) == []


def test_imminent_event_with_a_passed_lead_fires_now(zeev):
    """Discovered 20 minutes before an appointment: the hour-ahead lead is gone,
    but staying silent because the warning is late is the wrong answer."""
    now = time.time()
    plan = zeev.gcal_reminder_plan(_timed("psychiatry", _soon(0.33), id="p"), now, set())
    assert plan and min(d for _, d, _, _ in plan) >= now


def test_distant_event_does_not_replay_passed_leads(zeev):
    """A birthday found today does not announce "tomorrow" a week early --
    otherwise a cold start recites the entire fortnight at once."""
    now = time.time()
    e = _allday("Eli's birthday", (dt.date.today() + dt.timedelta(days=9)).isoformat(),
                eventType="birthday")
    dues = [d for _, d, _, _ in zeev.gcal_reminder_plan(e, now, set())]
    assert all(d > now + 7 * DAY for d in dues)


# --- spoken text -----------------------------------------------------------

def test_emoji_are_stripped_for_tts(zeev):
    txt = zeev.gcal_reminder_text(_timed("\U0001F37D️ Dinner", _soon()),
                                  "event", 30, time.time() + DAY, False)
    assert "\U0001F37D" not in txt and "Dinner" in txt


def test_no_double_punctuation(zeev):
    """"Happy birthday!" is a real entry on this calendar."""
    txt = zeev.gcal_reminder_text(_allday("Happy birthday!", "2026-04-27"),
                                  "birthday", 0, time.time(), True)
    assert "!." not in txt


def test_birthday_text_says_today_or_tomorrow(zeev):
    start = time.time() + DAY
    assert zeev.gcal_reminder_text(_allday("Eli's birthday", "2026-06-03"),
                                   "birthday", 0, start, True).startswith("Today")
    assert zeev.gcal_reminder_text(_allday("Eli's birthday", "2026-06-03"),
                                   "birthday", 1440, start, True).startswith("Tomorrow")


def test_timed_text_carries_the_clock(zeev):
    start = dt.datetime.now().astimezone().replace(hour=14, minute=30) + dt.timedelta(days=1)
    txt = zeev.gcal_reminder_text(_timed("psychiatry", start), "appointment", 60,
                                  start.timestamp(), False)
    assert "2:30 PM" in txt and "In an hour" in txt


def test_whole_hour_drops_the_minutes(zeev):
    start = dt.datetime.now().astimezone().replace(hour=8, minute=0) + dt.timedelta(days=1)
    txt = zeev.gcal_reminder_text(_timed("Recycling", start), "event", 30,
                                  start.timestamp(), False)
    assert "8 AM" in txt and "8:00" not in txt


# --- the scan: create / reschedule / prune --------------------------------

@pytest.fixture
def scan_db(zeev, tmp_path, monkeypatch):
    """Point zeev at a scratch DB so the scan can be driven end to end."""
    monkeypatch.setattr(zeev, "ZEEV_DB", tmp_path / "t.db")
    monkeypatch.setattr(zeev, "_db_con", None)
    yield zeev
    try:
        zeev._db_con.close()
    except Exception:
        pass
    zeev._db_con = None


def _drive(zeev, monkeypatch, events, truncated=False):
    monkeypatch.setattr(zeev, "gcal_events", lambda *a, **k: (events, truncated))
    return zeev.gcal_scan_once()


def test_scan_creates_and_is_idempotent(scan_db, monkeypatch):
    z = scan_db
    ev = [_timed("psychiatry", _soon(72), id="p1")]
    created, moved, pruned = _drive(z, monkeypatch, ev)
    assert (created, moved, pruned) == (2, 0, 0)     # day-before + hour-before
    # A second pass must not duplicate: the loop runs every 30 minutes, so a
    # non-idempotent scan would stack reminders all day.
    assert _drive(z, monkeypatch, ev) == (0, 0, 0)
    assert len(z.list_reminders()) == 2


def test_rescheduled_event_replaces_the_stale_reminder(scan_db, monkeypatch):
    z = scan_db
    _drive(z, monkeypatch, [_timed("physical therapy", _soon(72), id="p2")])
    before = {r["due_ts"] for r in z.list_reminders()}
    created, moved, pruned = _drive(
        z, monkeypatch, [_timed("physical therapy", _soon(96), id="p2")])
    assert moved == 2 and created == 0
    after = z.list_reminders()
    assert len(after) == 2, "the old announcement must be cancelled, not left behind"
    assert {r["due_ts"] for r in after} != before


def test_deleted_event_cancels_its_reminder(scan_db, monkeypatch):
    z = scan_db
    _drive(z, monkeypatch, [_timed("Coffee with Boris", _soon(48), id="c1")])
    assert z.list_reminders()
    assert _drive(z, monkeypatch, [])[2] > 0
    assert z.list_reminders() == []


def test_truncated_response_never_prunes(scan_db, monkeypatch):
    """maxResults silently caps the API response. Reading that as "the event was
    deleted" would cancel reminders for events that still exist -- two sane
    behaviours combining into silent data loss."""
    z = scan_db
    _drive(z, monkeypatch, [_timed("Interview", _soon(48), id="i9")])
    n = len(z.list_reminders())
    assert n
    assert _drive(z, monkeypatch, [], truncated=True)[2] == 0
    assert len(z.list_reminders()) == n


def test_already_fired_reminder_is_not_retracted(scan_db, monkeypatch):
    """Rescheduling must not delete an announcement that already happened --
    it cannot be unsaid, and the row is the only record that it was."""
    z = scan_db
    _drive(z, monkeypatch, [_timed("Standup", _soon(72), id="s1")])
    z.due_reminders(now=time.time() + 10 * DAY)          # mark everything fired
    fired_before = [r for r in z.list_reminders(include_fired=True) if r["fired"]]
    assert fired_before
    _drive(z, monkeypatch, [_timed("Standup", _soon(96), id="s1")])
    rows = z.list_reminders(include_fired=True)
    kept = {r["id"] for r in rows if r["fired"]}
    assert kept == {r["id"] for r in fired_before}
    assert any(not r["fired"] for r in rows), "the moved event still needs a reminder"


# --- routine detection: the trap that got through the first build ---------

def _series(summary, sid, n, step_days=7):
    base = dt.datetime.now().astimezone() + dt.timedelta(hours=6)
    return [_timed(summary, base + dt.timedelta(days=i * step_days),
                   id=f"{sid}-{i}", recurringEventId=sid) for i in range(n)]


def test_one_habit_split_across_several_series_is_still_a_habit(zeev):
    """Caught live: "Digital Downtime" is FIVE weekly recurrences, one per
    weekday. Keyed on recurringEventId each looked like a harmless 2-in-a-
    fortnight event and all five passed -- eight spoken reminders for a block
    whose whole purpose is to be left alone. Keyed on the title it is 10."""
    events = []
    for k in range(5):
        events += _series("Digital Downtime", f"dd{k}", 2)
    routine = zeev.gcal_routine_titles(events, days=14)
    assert "digital downtime" in routine
    assert all(zeev.gcal_event_category(e, routine) is None for e in events)


def test_weekly_appointment_is_not_routine(zeev):
    """The other side of that line: a weekly physiotherapy slot must survive."""
    events = _series("physical therapy", "pt", 2)
    routine = zeev.gcal_routine_titles(events, days=14)
    assert "physical therapy" not in routine
    assert zeev.gcal_event_category(events[0], routine) == "appointment"


def test_routine_is_a_rate_so_the_window_can_change(zeev):
    """A raw count is silently coupled to ZEEV_GCAL_SCAN_DAYS: halve the window
    and a habit block drops under a count threshold tuned on the longer one."""
    daily = _series("\U0001F37D️ Dinner", "d1", 14, step_days=1)
    assert "dinner" in zeev.gcal_routine_titles(daily, days=14)
    assert "dinner" in zeev.gcal_routine_titles(daily[:7], days=7)
    assert "dinner" in zeev.gcal_routine_titles(daily[:3], days=3)


def test_one_off_events_are_never_routine(zeev):
    """No recurringEventId means it was typed by hand for a reason, however
    generic the title."""
    events = [_timed("Meeting", _soon(24 * i), id=f"m{i}") for i in range(1, 9)]
    assert zeev.gcal_routine_titles(events, days=14) == set()
