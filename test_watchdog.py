"""The watchdog only earns its place if it is silent when it should be.

Run: python test_watchdog.py

A monitor that cries wolf gets muted, and a muted monitor is worse than none:
it converts a real outage into one nobody looks at. So most of this file is
about the cases that must NOT alarm.

The case it exists for is the silent one. A sync that runs and fails already
goes red and emails Chris -- that channel is proven, he received the
2026-09-23 one. A sync that never runs at all produces nothing at all.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from watchdog import assess

CHI = ZoneInfo("America/Chicago")


def at(h, m=0, day=28):
    return dt.datetime(2026, 9, day, h, m, tzinfo=CHI)


def rec(date="2026-09-28", completed=True, finished="2026-09-28T03:16:26-05:00",
        started="2026-09-28T03:14:02-05:00", attempts=1):
    return {"date": date, "completed": completed, "finished": finished,
            "started": started, "attempts": attempts}


# --- silent when all is well -------------------------------------------------

def test_a_completed_sync_is_silent():
    """The 2026-09-28 record exactly as it stands: done at 03:16."""
    ok, head, detail = assess(rec(), at(6, 45))
    assert ok, (head, detail)
    assert "03:16:26" in detail
    print("OK: a completed sync passes and says when it finished")


def test_still_silent_late_in_the_day():
    ok, _h, _d = assess(rec(), at(23, 30))
    assert ok
    print("OK: a completed sync stays passing all day, not just at the deadline")


def test_before_the_deadline_a_missing_run_is_not_an_alarm():
    """Fired at 04:00 with yesterday's record: the deadline has not passed, so
    there is nothing wrong yet. Alarming here would be the cry-wolf case."""
    ok, head, _d = assess(rec(date="2026-09-27"), at(4, 0))
    assert ok and head == "NOT YET", (ok, head)
    print("OK: before the deadline, a not-yet-run day is not an alarm")


def test_a_run_in_flight_before_the_deadline_is_not_an_alarm():
    ok, head, _d = assess(
        rec(completed=False, started="2026-09-28T06:30:00-05:00"), at(6, 40))
    assert ok and head == "IN FLIGHT", (ok, head)
    print("OK: a sync still running before the deadline is not an alarm")


# --- loud when it matters ----------------------------------------------------

def test_the_silent_failure_is_caught():
    """The case this exists for. Yesterday's record, deadline passed, and
    nothing anywhere has failed -- because nothing ran."""
    ok, head, detail = assess(rec(date="2026-09-27"), at(6, 46))
    assert not ok, head
    assert head == "TODAY'S SYNC HAS NOT RUN"
    assert "yesterday's" in detail
    print("OK: a day that never synced is caught once the deadline passes")


def test_a_run_that_died_partway_is_caught():
    """Claimed the day, wrote nothing, never came back. The day is 'taken' so
    every later trigger stands down -- the worst shape of failure."""
    ok, head, _d = assess(
        rec(completed=False, started="2026-09-28T02:00:00-05:00", attempts=2),
        at(6, 46))
    assert not ok and head == "STARTED BUT NEVER FINISHED", (ok, head)
    print("OK: a sync that claimed the day and died is caught")


def test_a_run_still_going_at_the_deadline_is_caught():
    ok, head, _d = assess(
        rec(completed=False, started="2026-09-28T06:40:00-05:00"), at(6, 50))
    assert not ok and head == "STILL RUNNING AT THE DEADLINE", (ok, head)
    print("OK: a sync that has not finished by the deadline is caught")


def test_no_record_at_all_is_caught():
    ok, head, _d = assess(None, at(6, 46))
    assert not ok and head == "NO RECORD AT ALL", (ok, head)
    print("OK: an empty or unwritten record is caught, not read as success")


def test_a_completed_record_from_a_future_date_does_not_pass_today():
    """A clock or timezone mistake must not be able to silence the watchdog."""
    ok, _h, _d = assess(rec(date="2026-09-29"), at(6, 46))
    assert not ok
    print("OK: a record dated another day never counts as today's run")


# --- the deadline is a parameter, not a constant ------------------------------

def test_the_deadline_is_honoured():
    yesterday = rec(date="2026-09-27")
    assert assess(yesterday, at(5, 59), deadline="06:00")[0] is True
    assert assess(yesterday, at(6, 1), deadline="06:00")[0] is False
    print("OK: the alarm fires on the configured deadline, not a fixed hour")


if __name__ == "__main__":
    test_a_completed_sync_is_silent()
    test_still_silent_late_in_the_day()
    test_before_the_deadline_a_missing_run_is_not_an_alarm()
    test_a_run_in_flight_before_the_deadline_is_not_an_alarm()
    test_the_silent_failure_is_caught()
    test_a_run_that_died_partway_is_caught()
    test_a_run_still_going_at_the_deadline_is_caught()
    test_no_record_at_all_is_caught()
    test_a_completed_record_from_a_future_date_does_not_pass_today()
    test_the_deadline_is_honoured()
    print("\nALL WATCHDOG TESTS PASSED")
