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
        # Present and empty, which is what the API documents for an open shift.
        # Empty says we meant it; absent only says we never considered it.
        assert s["assignedUserIds"] == [], s
        assert s["openSpots"] == 1, s
        # Nothing anywhere in the payload may name a person.
        assert not any(v for k, v in s.items() if "user" in k.lower()), s
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




def test_every_covered_market_has_a_board():
    """All five markets route somewhere, and only to a real cleaning board."""
    from connecteam_map import CITY_SCHEDULERS, CITY_TIMEZONES, scheduler_for

    for city in CITY_TIMEZONES:
        assert scheduler_for(city), city + " has no Connecteam board"
    # Three cleaning boards only. The account also has checklist and supply
    # schedulers; those must never receive a job.
    assert len(set(CITY_SCHEDULERS.values())) == 3, CITY_SCHEDULERS
    for forbidden in ("12556536", "12899125", "14223255", "19241547"):
        assert forbidden not in set(CITY_SCHEDULERS.values()), forbidden
    # Real spellings as they appear in the sheet, not the normalised keys.
    assert scheduler_for("Bay St. Louis") == scheduler_for("New Orleans") == "2520975"
    assert scheduler_for("Thunderbolt") == scheduler_for("Savannah") == "10540737"
    print("OK routing: five markets, three boards, NOLA+BSL and Savannah+Thunderbolt paired")


def test_a_city_we_do_not_serve_gets_no_board():
    """None is a refusal. A fallback would post a Boston clean to a NOLA crew."""
    from connecteam_map import scheduler_for

    for city in ("Boston", "Nashville", "Scottsdale", "Stowe", "Nantucket", "", "   "):
        assert scheduler_for(city) is None, city
    print("OK routing: an uncovered city gets no board rather than a default one")


def test_no_board_mixes_two_timezones():
    """A board carrying both Central and Eastern cities would put half its jobs an
    hour out. This keeps that from creeping in when a market is added."""
    from connecteam_map import CITY_SCHEDULERS, timezone_for

    by_board = {}
    for city, board in CITY_SCHEDULERS.items():
        by_board.setdefault(board, set()).add(timezone_for(city))
    for board, zones in by_board.items():
        assert len(zones) == 1, "board %s mixes timezones: %s" % (board, zones)
    print("OK routing: no board mixes Central and Eastern cities")




def _mixed_rows():
    import pandas as pd
    return pd.DataFrame([
        {"City": "New Orleans", "Date": "2026-09-04", "Property": "1201 N Roman",
         "Check-out Time": "11:00 AM", "Check-in Time": "", "T/O": "", "Adjustments": ""},
        {"City": "Bay St. Louis", "Date": "2026-09-04", "Property": "11538 Bayou View",
         "Check-out Time": "11:00 AM", "Check-in Time": "", "T/O": "", "Adjustments": ""},
        {"City": "Savannah", "Date": "2026-09-04", "Property": "6 Lake",
         "Check-out Time": "11:00 AM", "Check-in Time": "4:00 PM", "T/O": "yes",
         "Adjustments": "ECO"},
        {"City": "Thunderbolt", "Date": "2026-09-04", "Property": "3 Beaver",
         "Check-out Time": "10:00 AM", "Check-in Time": "", "T/O": "", "Adjustments": ""},
        {"City": "Austin", "Date": "2026-09-04", "Property": "2903 E 3rd A",
         "Check-out Time": "11:00 AM", "Check-in Time": "", "T/O": "", "Adjustments": ""},
        {"City": "Boston", "Date": "2026-09-04", "Property": "12 Hinckley 1",
         "Check-out Time": "11:00 AM", "Check-in Time": "", "T/O": "", "Adjustments": ""},
    ])


def test_jobs_are_grouped_by_board_for_a_city_at_a_time_rollout():
    from connecteam_map import shifts_by_scheduler

    groups = shifts_by_scheduler(_mixed_rows())
    assert set(groups) == {"2520975", "10540737", "10540759"}, groups
    # NOLA and Bay St. Louis land on one board; Savannah and Thunderbolt on another.
    assert len(groups["2520975"]) == 2, groups["2520975"]
    assert len(groups["10540737"]) == 2, groups["10540737"]
    assert len(groups["10540759"]) == 1
    print("OK grouping: jobs arrive per board, so one city can be proved before the next")


def test_a_city_with_no_board_is_dropped_not_defaulted():
    from connecteam_map import shifts_by_scheduler

    every = [r["Property"] for items in shifts_by_scheduler(_mixed_rows()).values()
             for r, _ in items]
    assert "12 Hinckley 1" not in every, every
    assert len(every) == 5, every
    print("OK grouping: a Boston clean is dropped, never defaulted onto a NOLA board")


def test_one_city_can_be_switched_on_alone():
    from connecteam_map import shifts_by_scheduler

    only = shifts_by_scheduler(_mixed_rows(), only_city="Savannah")
    assert set(only) == {"10540737"}, only
    assert [r["Property"] for r, _ in only["10540737"]] == ["6 Lake"], only
    # Thunderbolt shares Savannah's board but is a different market, so asking for
    # Savannah must not sweep it along.
    thun = shifts_by_scheduler(_mixed_rows(), only_city="Thunderbolt")
    assert [r["Property"] for r, _ in thun["10540737"]] == ["3 Beaver"], thun
    print("OK grouping: a single market can be switched on without its board-mate")


def test_the_unassigned_gate_refuses_anything_with_a_person_on_it():
    from connecteam_map import assert_unassigned, shifts_by_scheduler

    payloads = [p for items in shifts_by_scheduler(_mixed_rows()).values()
                for _, p in items]
    assert_unassigned(payloads)          # everything the mapper makes must pass

    for bad in ({"isOpenShift": True, "assignedUserIds": ["u1"]},
                {"isOpenShift": True, "userIds": ["u1"]},
                {"isOpenShift": True, "assignedUsers": [{"id": 1}]},
                {"isOpenShift": False}):
        try:
            assert_unassigned([bad])
        except ValueError:
            continue
        raise AssertionError("gate let an assigned shift through: %r" % bad)
    print("OK gate: any shift naming a person is refused before it can reach the API")


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
    test_every_covered_market_has_a_board()
    test_a_city_we_do_not_serve_gets_no_board()
    test_no_board_mixes_two_timezones()
    test_jobs_are_grouped_by_board_for_a_city_at_a_time_rollout()
    test_a_city_with_no_board_is_dropped_not_defaulted()
    test_one_city_can_be_switched_on_alone()
    test_the_unassigned_gate_refuses_anything_with_a_person_on_it()
    print("\nALL CONNECTEAM-MAP TESTS PASSED")
