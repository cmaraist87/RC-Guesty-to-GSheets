"""The rolling window, and greying a card the sheet no longer wants.

Run: python test_push_window.py

Chris' calls on 2026-10-06: a rolling 45 days from today, and a booking that
VANISHES from the sheet gets greyed rather than deleted.

The window number is load-bearing in three places at once -- which month tabs are
read, which cards are created, and which slice of the board is reconciled -- and
they must be the same bounds. Reconcile wider than you push and every card an
earlier run left outside the window gets greyed. Push wider than you reconcile
and you create cards nothing ever corrects again. Most of this file is about
that and about the safety rail on the stale half.
"""
from datetime import date, timedelta

from connecteam_client import ConnecteamClient, _shift_key
from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, STANDARD_TITLE,
                            TURNOVER_TITLE, WINDOW_DAYS, months_in_window,
                            window_bounds)


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


# ---------------------------------------------------------------- stale cards

def _board_card(jid, title, start, colour, cid="a:b", assigned=None):
    return {"id": cid, "jobId": jid, "title": title, "startTime": start,
            "endTime": start + 900, "color": colour, "isOpenShift": True,
            "assignedUserIds": assigned or [], "openSpots": 1,
            "timezone": "America/Chicago"}


def _wanted(jid, title, start, colour):
    return {"jobId": jid, "title": title, "startTime": start,
            "endTime": start + 900, "color": colour, "isOpenShift": True,
            "assignedUserIds": [], "openSpots": 1, "timezone": "America/Chicago"}


class Spy(ConnecteamClient):
    """No key, no session. Records what it was asked to repaint."""

    def __init__(self, on_board):
        self._on_board = on_board
        self.painted = []

    def existing_shifts(self, scheduler_id, start, end):
        return [s for s in self._on_board if start <= int(s["startTime"]) <= end]

    def recolour_shift(self, scheduler_id, shift, color):
        self.painted.append((str(shift.get("id")), color))
        shift["color"] = color          # so the read-back check sees it land
        return {}


T0 = 1790000000


def test_a_vanished_booking_is_greyed_not_deleted():
    board = [_board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR, cid="gone:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [("gone:1", CANCELLED_COLOR)], spy.painted
    print("OK: a card the sheet no longer wants goes grey")


def test_the_team_s_own_cards_are_never_touched():
    """The safety rail. The team create their own cards on these boards and carry
    the property in the TITLE. Repainting one would be us editing their schedule,
    and on a crew board that is somebody's day."""
    board = [_board_card("J9", "6504 Porter A", T0, "#D9B443", cid="theirs:1"),
             _board_card("J9", "", T0, STANDARD_COLOR, cid="untitled:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [], spy.painted
    print("OK: a card whose title is not one of ours is left alone")


def test_a_card_outside_the_window_is_left_alone():
    """The bounds are passed in, not derived from what we want. A card from an
    earlier, wider push must not be greyed just because this window is narrower
    -- that is how a whole month of correct cards would go grey overnight."""
    board = [_board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR, cid="inside:1"),
             _board_card("J2", STANDARD_TITLE, T0 + 10_000_000, STANDARD_COLOR,
                         cid="far-future:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [("inside:1", CANCELLED_COLOR)], spy.painted
    print("OK: only cards inside the reconcile window are considered")


def test_an_empty_sheet_does_not_grey_the_whole_board():
    """Bounds derived from `wanted` would collapse to nothing here, and every
    card in the gap would survive as a ghost. Passed-in bounds mean an empty
    stretch of sheet correctly greys that stretch of board and nothing else."""
    board = [_board_card(f"J{i}", STANDARD_TITLE, T0 + i, STANDARD_COLOR,
                         cid=f"c{i}") for i in range(3)]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert sorted(spy.painted) == [("c0", CANCELLED_COLOR), ("c1", CANCELLED_COLOR),
                                   ("c2", CANCELLED_COLOR)], spy.painted
    print("OK: an empty window greys that window, bounds come from the caller")


def test_a_returning_booking_goes_back_to_green():
    """A booking can come back -- a Guesty hiccup, or a reinstated reservation.
    The grey must not be a one-way door."""
    board = [_board_card("J1", STANDARD_TITLE, T0, CANCELLED_COLOR, cid="back:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [_wanted("J1", STANDARD_TITLE, T0, STANDARD_COLOR)],
                          T0 - 100, T0 + 100, live=True,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [("back:1", STANDARD_COLOR)], spy.painted
    print("OK: a booking that comes back turns its card green again")


def test_reconciling_twice_changes_nothing_the_second_time():
    """Idempotence. A nightly job that repaints the same cards every night is
    burning API calls and makes the log useless for spotting a real change."""
    board = [_board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR, cid="x:1")]
    spy = Spy(board)
    for _ in range(2):
        spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                              stale_color=CANCELLED_COLOR,
                              our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [("x:1", CANCELLED_COLOR)], spy.painted
    print("OK: the second run repaints nothing")


def test_greying_is_off_when_the_board_is_not_gated_for_it():
    """stale_color=None is what a market board gets until that is a decision
    somebody makes. It must mean 'leave every card alone', not 'use a default'."""
    board = [_board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR, cid="y:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=True,
                          stale_color=None,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [], spy.painted
    print("OK: with no stale colour, nothing is greyed")


def test_nothing_is_sent_unless_live():
    board = [_board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR, cid="z:1")]
    spy = Spy(board)
    spy.reconcile_colours("19713722", [], T0 - 100, T0 + 100, live=False,
                          stale_color=CANCELLED_COLOR,
                          our_titles=(STANDARD_TITLE, TURNOVER_TITLE))
    assert spy.painted == [], spy.painted
    print("OK: a preview repaints nothing")


def test_the_match_key_is_the_one_create_skips_on():
    """reconcile and create_shifts must agree about which desired card is which
    existing card, or one will create what the other just greyed."""
    a = _board_card("J1", STANDARD_TITLE, T0, STANDARD_COLOR)
    b = _wanted("J1", STANDARD_TITLE, T0, CANCELLED_COLOR)
    assert _shift_key(a) == _shift_key(b), (_shift_key(a), _shift_key(b))
    # And a different start is a different card, which is why a MOVED booking
    # leaves a grey card behind at the old time rather than moving in place.
    c = _wanted("J1", STANDARD_TITLE, T0 + 3600, STANDARD_COLOR)
    assert _shift_key(a) != _shift_key(c)
    print("OK: colour is not part of the key; start time is")


if __name__ == "__main__":
    test_the_window_is_45_rolling_days()
    test_the_window_never_shrinks_at_month_end()
    test_the_window_spans_two_tabs_and_sometimes_three()
    test_the_window_crosses_a_year()
    test_months_in_window_never_builds_an_invalid_date()
    test_a_vanished_booking_is_greyed_not_deleted()
    test_the_team_s_own_cards_are_never_touched()
    test_a_card_outside_the_window_is_left_alone()
    test_an_empty_sheet_does_not_grey_the_whole_board()
    test_a_returning_booking_goes_back_to_green()
    test_reconciling_twice_changes_nothing_the_second_time()
    test_greying_is_off_when_the_board_is_not_gated_for_it()
    test_nothing_is_sent_unless_live()
    test_the_match_key_is_the_one_create_skips_on()
    print("\nALL PUSH-WINDOW TESTS PASSED")
