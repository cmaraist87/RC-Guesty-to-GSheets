"""A repair run must not forget marks it cannot re-derive.

Run: python test_repair_window.py

SYNC_REPAIR_STRIKES re-derives cancellations by handing the merge an empty set
of struck rows, so every row is judged fresh against Guesty. On 2026-09-20 that
stripped 24 Septiembre rows dated 09-01 to 09-18 -- eight of whose bookings
Guesty still reports as canceled, among them Ryan Gentry HMQ4JEA5CQ, cancelled
2026-09-09.

The fetch is checkOut >= yesterday. The merge SKIPS any row outside that window
rather than judging it (see the `lo <= d <= hi` gate in sheet_merge), so a mark
forgotten out there is never re-applied and the line is simply gone.
"""
import pandas as pd

from sync import _repairable_struck

WINDOW = ("2026-09-19", "2027-03-19")


def sheet(dates):
    return pd.DataFrame({"Date": list(dates),
                         "Property": ["P"] * len(dates),
                         "Confirmation Code": [f"C{i}" for i in range(len(dates))]})


def test_a_mark_outside_the_fetch_is_kept():
    """The 2026-09-20 regression: past rows keep their lines through a repair."""
    df = sheet(["2026-09-03", "2026-09-13", "2026-09-25", "2026-10-01"])
    keep = _repairable_struck(df, {0, 1, 2, 3}, WINDOW)
    assert keep == {0, 1}, ("rows before 09-19 must keep their marks", keep)
    print("OK: rows the repair cannot re-derive keep the marks they had")


def test_a_mark_inside_the_fetch_is_forgotten():
    """The point of a repair: in-window rows are re-judged against Guesty."""
    df = sheet(["2026-09-25", "2026-10-01", "2027-01-05"])
    assert _repairable_struck(df, {0, 1, 2}, WINDOW) == frozenset()
    print("OK: rows inside the fetch are handed over for re-derivation")


def test_the_window_edges_belong_to_the_fetch():
    """lo and hi are inclusive in the merge's own gate; match it exactly."""
    df = sheet(["2026-09-18", "2026-09-19", "2027-03-19", "2027-03-20"])
    keep = _repairable_struck(df, {0, 1, 2, 3}, WINDOW)
    assert keep == {0, 3}, keep
    print("OK: the boundary dates are treated as inside the fetch, as the merge does")


def test_a_mark_past_the_last_row_is_dropped():
    """A stale position below the data identifies no booking; it is not kept."""
    df = sheet(["2026-09-03"])
    assert _repairable_struck(df, {0, 7, 99}, WINDOW) == {0}
    print("OK: marks past the last data row are not carried into the repair")


def test_no_window_and_no_sheet_are_safe():
    assert _repairable_struck(sheet([]), {1}, WINDOW) == frozenset()
    assert _repairable_struck(sheet(["2026-09-03"]), {0}, None) == frozenset()
    print("OK: an empty tab or a missing window forgets everything, as before")


def test_an_unparseable_date_is_kept_rather_than_lost():
    """A row whose Date cannot be read is not evidence that it is in the fetch.

    Losing a line is silent and permanent; keeping one the repair might have
    lifted shows up in the next run's diff. Prefer the recoverable mistake.
    """
    df = sheet(["", "not a date", "2026-10-01"])
    keep = _repairable_struck(df, {0, 1, 2}, WINDOW)
    assert 2 not in keep, keep
    assert {0, 1} <= keep, ("an unreadable date must keep its mark", keep)
    print("OK: a row with no readable date keeps its mark")


if __name__ == "__main__":
    test_a_mark_outside_the_fetch_is_kept()
    test_a_mark_inside_the_fetch_is_forgotten()
    test_the_window_edges_belong_to_the_fetch()
    test_a_mark_past_the_last_row_is_dropped()
    test_no_window_and_no_sheet_are_safe()
    test_an_unparseable_date_is_kept_rather_than_lost()
    print("\nALL REPAIR-WINDOW TESTS PASSED")
