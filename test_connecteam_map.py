"""
Offline tests for the sheet -> Connecteam shift mapping (no network, no API key).

Run: python test_connecteam_map.py
"""
import datetime as dt

import pandas as pd

from connecteam_map import (DEFAULT_CLEAN_HOURS, STANDARD_COLOR, TURNOVER_COLOR,
                            shift_for_row, shift_title, shifts_for_rows,
                            timezone_for)


def _row(**over):
    base = {"City": "New Orleans", "Date": "2026-09-03", "Property": "1022 Mandeville",
            "Check-out Time": "11:00 AM", "Check-in Time": "", "T/O": "",
            "Adjustments": ""}
    base.update(over)
    return base


def _utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def test_only_rows_with_a_checkout_become_jobs():
    """A clean is needed when a guest LEAVES. A check-in-only row is somebody
    arriving at a unit cleaned when the previous guest left -- not a second job."""
    assert shift_for_row(_row()) is not None
    assert shift_for_row(_row(**{"Check-out Time": "", "Check-in Time": "04:00 PM"})) is None
    assert shift_for_row(_row(**{"Check-out Time": ""})) is None
    print("OK: a departure makes a job; an arrival on its own does not")


def test_every_shift_is_unassigned():
    """The whole point: jobs land in Unassigned and never name a person."""
    for row in (_row(), _row(**{"T/O": "yes", "Check-in Time": "04:00 PM"})):
        s = shift_for_row(row)
        assert s["isOpenShift"] is True, s
        assert "assignedUserIds" not in s, "an open shift must carry no user ids"
        assert not any("user" in k.lower() for k in s), s
    print("OK: every shift is an open, unassigned shift with no user ids")


def test_turnover_window_is_the_real_gap_between_guests():
    """On a turnover the cleaner has exactly the window between the old guest
    leaving and the new one arriving -- that is the shift, not a nominal duration."""
    s = shift_for_row(_row(**{"Check-in Time": "04:00 PM", "T/O": "yes"}))
    assert (_utc(s["endTime"]) - _utc(s["startTime"])) == dt.timedelta(hours=5)
    assert s["color"] == TURNOVER_COLOR, "a turnover must stand out on the board"

    # A departure with no arrival today has no hard deadline -> nominal duration.
    s2 = shift_for_row(_row())
    assert (_utc(s2["endTime"]) - _utc(s2["startTime"])) == dt.timedelta(hours=DEFAULT_CLEAN_HOURS)
    assert s2["color"] == STANDARD_COLOR
    print("OK: a turnover spans the real gap; a plain departure gets a nominal window")


def test_cities_resolve_to_their_own_timezone():
    """The five markets straddle two zones. An hour out is invisible in the data and
    very visible to a cleaner standing outside a locked door."""
    assert timezone_for("New Orleans") == "America/Chicago"
    assert timezone_for("Austin") == "America/Chicago"
    assert timezone_for("bay saint louis") == "America/Chicago", "spelling drift"
    assert timezone_for("Savannah") == "America/New_York"
    assert timezone_for("Thunderbolt") == "America/New_York"

    # Same wall-clock time in two zones must NOT be the same instant.
    nola = shift_for_row(_row(City="New Orleans"))
    sav = shift_for_row(_row(City="Savannah", Property="6 Lake"))
    assert nola["startTime"] - sav["startTime"] == 3600, (nola, sav)
    assert _utc(nola["startTime"]).hour == 16   # 11:00 CDT
    assert _utc(sav["startTime"]).hour == 15    # 11:00 EDT
    print("OK: each city's shift is stamped in its own timezone")


def test_title_carries_the_codes_a_cleaner_needs():
    assert shift_title(_row()) == "1022 Mandeville"
    assert shift_title(_row(**{"T/O": "yes"})) == "1022 Mandeville — T/O"
    assert shift_title(_row(**{"Adjustments": "ECO"})) == "1022 Mandeville — ECO"
    assert shift_title(_row(**{"T/O": "yes", "Adjustments": "ECO, LCI"})) == \
        "1022 Mandeville — T/O · ECO · LCI"
    print("OK: the title reads property first, then what changes the timing")


def test_unparseable_rows_are_skipped_not_guessed():
    """Better no shift than one at the wrong time -- a cleaner sent to the wrong hour
    is worse than a job someone notices is missing."""
    assert shift_for_row(_row(Date="not-a-date")) is None
    assert shift_for_row(_row(**{"Check-out Time": "half eleven"})) is None
    print("OK: a row that cannot be placed in time produces no shift")


def test_backwards_times_do_not_make_a_negative_shift():
    """Check-in before check-out is nonsense, but it must not emit a shift that ends
    before it starts."""
    s = shift_for_row(_row(**{"Check-out Time": "04:00 PM", "Check-in Time": "11:00 AM"}))
    assert s["endTime"] > s["startTime"], s
    assert (_utc(s["endTime"]) - _utc(s["startTime"])) == dt.timedelta(hours=DEFAULT_CLEAN_HOURS)
    print("OK: out-of-order times fall back to a nominal window, never a negative one")


def test_timestamps_are_seconds_not_milliseconds():
    """Connecteam rejects anything over 1e12 -- the classic ms/seconds mistake."""
    s = shift_for_row(_row())
    for key in ("startTime", "endTime"):
        assert isinstance(s[key], int), s
        assert s[key] < 1e12, f"{key} looks like milliseconds: {s[key]}"
        assert s[key] > 1e9, f"{key} is not a plausible epoch: {s[key]}"
    print("OK: timestamps are epoch seconds, inside Connecteam's accepted range")


def test_shifts_for_rows_pairs_each_payload_with_its_row():
    """The caller has to write the returned shift id back to the row it came from."""
    df = pd.DataFrame([
        _row(Property="1022 Mandeville"),
        _row(Property="3223 Canal", **{"Check-out Time": "", "Check-in Time": "04:00 PM"}),
        _row(Property="6 Lake", City="Savannah", **{"T/O": "yes", "Check-in Time": "04:00 PM"}),
    ])
    pairs = shifts_for_rows(df)
    assert len(pairs) == 2, [p[1]["title"] for p in pairs]
    assert [r["Property"] for r, _ in pairs] == ["1022 Mandeville", "6 Lake"]
    assert all(p["isOpenShift"] for _, p in pairs)
    print("OK: only job rows come back, each paired with the row that produced it")


if __name__ == "__main__":
    test_only_rows_with_a_checkout_become_jobs()
    test_every_shift_is_unassigned()
    test_turnover_window_is_the_real_gap_between_guests()
    test_cities_resolve_to_their_own_timezone()
    test_title_carries_the_codes_a_cleaner_needs()
    test_unparseable_rows_are_skipped_not_guessed()
    test_backwards_times_do_not_make_a_negative_shift()
    test_timestamps_are_seconds_not_milliseconds()
    test_shifts_for_rows_pairs_each_payload_with_its_row()
    print("\nALL CONNECTEAM-MAP TESTS PASSED")
