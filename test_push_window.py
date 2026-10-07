"""The rolling window, and greying a card the sheet no longer wants.

Run: python test_push_window.py

Chris' calls on 2026-10-06: a rolling 45 days from today, and a booking that
VANISHES from the sheet gets greyed rather than deleted.

The window number is load-bearing in three places at once -- which month tabs are
read, which cards are created, and which slice of the board is reconciled -- and
they must be the same bounds. Reconcile wider than you push and every card an
earlier run left outside the window gets touched. Push wider than you reconcile
and you create cards nothing ever corrects again.

This file covered the stale-card rules too, until Chris corrected the colour rule
on 2026-10-06 and identity moved from the slot to the Confirmation Code. Those
rules now live in connecteam_cards.plan and are tested in
test_booking_identity.py; what is left here is the window arithmetic, which that
change did not touch.
"""
from datetime import date, timedelta

from connecteam_map import WINDOW_DAYS, months_in_window, window_bounds


def test_the_window_is_45_rolling_days():
    assert WINDOW_DAYS == 45, WINDOW_DAYS
    first, last = window_bounds(date(2026, 10, 6))
    assert (first, last) == (date(2026, 10, 6), date(2026, 11, 20)), (first, last)
    print(f"OK: 45 rolling days -> {first} to {last}")


def test_the_window_never_shrinks_at_month_end():
    """The reason it is rolling and not 'this month and next'. On the 30th a
    calendar window is one day deep for the month the crews are about to work."""
    for day in (date(2026, 10, 1), date(2026, 10, 30), date(2026, 10, 31),
                date(2026, 11, 1)):
        first, last = window_bounds(day)
        assert (last - first).days == WINDOW_DAYS, (day, first, last)
    print("OK: the horizon is the same depth on the 1st and on the 31st")


def test_the_window_spans_two_tabs_and_sometimes_three():
    """A 45-day window never fits in one month tab, so the push cannot assume
    one. Three happens whenever it starts late in a short month."""
    two = months_in_window(*window_bounds(date(2026, 10, 6)))
    assert two == [(2026, 10), (2026, 11)], two

    three = months_in_window(*window_bounds(date(2026, 11, 20)))
    assert three == [(2026, 11), (2026, 12), (2027, 1)], three
    print(f"OK: two tabs normally, three near a boundary -> {three}")


def test_the_window_crosses_a_year():
    """Month arithmetic that forgets December is the classic version of this bug."""
    got = months_in_window(*window_bounds(date(2026, 12, 20)))
    assert got == [(2026, 12), (2027, 1), (2027, 2)], got
    assert all(m in range(1, 13) for _y, m in got), got
    print(f"OK: December rolls into the new year -> {got}")


def test_months_in_window_never_builds_an_invalid_date():
    """Walked month by month rather than by day arithmetic, because a 31st does
    not survive being moved into a 30-day month. Checked across a whole year,
    including a leap February."""
    for n in range(0, 800):
        day = date(2026, 1, 1) + timedelta(days=n)
        first, last = window_bounds(day)
        got = months_in_window(first, last)
        assert got == sorted(set(got)), (day, got)
        assert (first.year, first.month) == got[0], (day, got)
        assert (last.year, last.month) == got[-1], (day, got)
        assert 2 <= len(got) <= 3, (day, got)
    print("OK: 800 consecutive start dates all produce a sane tab list")




if __name__ == "__main__":
    test_the_window_is_45_rolling_days()
    test_the_window_never_shrinks_at_month_end()
    test_the_window_spans_two_tabs_and_sometimes_three()
    test_the_window_crosses_a_year()
    test_months_in_window_never_builds_an_invalid_date()
    print("")
    print("ALL PUSH-WINDOW TESTS PASSED")
