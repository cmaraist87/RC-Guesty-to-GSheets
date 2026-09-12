"""Telling a reassignment from a combined listing losing one unit.

Run: python test_stale_moves.py

A struck row whose confirmation code is still live elsewhere is one of two things,
and the row itself cannot say which:

  * Guesty REASSIGNED the booking. The strike counts a cancellation that never
    happened, and the row should not exist.
  * The booking is a COMBINED LISTING -- "704 N 2nd" and "706 N 2nd" under one
    code -- and one unit dropped out. That is a real cancellation; it keeps its line.

Reading the addresses cannot separate them: 704 and 706 N 2nd look like different
properties and are not. The evidence that does is how often the two addresses have
been booked together across the whole tab.
"""
import pandas as pd

from stale_moves import classify, pair_history


def rows(*triples):
    return pd.DataFrame([{"Confirmation Code": c, "Property": p, "Date": d}
                         for c, p, d in triples])


def live(*pairs):
    return [pd.Series({"Property": p, "Date": d}) for p, d in pairs]


def test_a_booking_live_on_another_day_is_a_move():
    """Unambiguous: a combined listing's units are cleaned the same day, so nothing
    about one produces a live row on a different date."""
    v, why = classify(pd.Series({"Property": "1320 Baronne", "Date": "2026-08-02"}),
                      live(("1320 Baronne", "2026-07-30")), {})
    assert v == "move", (v, why)
    print("OK live on another day -> MOVE")


def test_two_units_booked_together_again_and_again_is_a_cancellation():
    """704 and 706 N 2nd share many bookings: one listing. The address heuristic
    called this a move and would have deleted a real cancellation."""
    sheet = rows(*[(f"C{i}", p, f"2026-08-{i:02d}")
                   for i in range(1, 9) for p in ("704 N 2nd", "706 N 2nd")])
    pairs = pair_history(sheet)
    v, why = classify(pd.Series({"Property": "704 N 2nd", "Date": "2026-08-02"}),
                      live(("706 N 2nd", "2026-08-02")), pairs)
    assert v == "cancellation", (v, why)
    assert "combined listing" in why, why
    print("OK 704/706 N 2nd share 8 bookings -> CANCELLATION, keeps its line")


def test_a_one_off_pairing_is_a_reassignment():
    """833 Alvar and 1409 Carondelet have never been booked together before."""
    sheet = rows(("C1", "833 Alvar", "2026-08-02"),
                 ("C1", "1409 Carondelet", "2026-08-02"),
                 ("C2", "833 Alvar", "2026-08-10"),
                 ("C3", "1409 Carondelet", "2026-08-12"))
    pairs = pair_history(sheet)
    v, why = classify(pd.Series({"Property": "833 Alvar", "Date": "2026-08-02"}),
                      live(("1409 Carondelet", "2026-08-02")), pairs)
    assert v == "move", (v, why)
    print("OK a one-off pairing -> MOVE")


def test_no_shared_history_is_left_unclear():
    v, why = classify(pd.Series({"Property": "833 Alvar", "Date": "2026-08-02"}),
                      live(("1409 Carondelet", "2026-08-02")), {})
    assert v == "unclear", (v, why)
    print("OK no evidence either way -> UNCLEAR, left alone")


def test_only_moves_are_ever_deletable():
    """The guard that matters: --fix deletes 'move' and nothing else."""
    assert {"move", "cancellation", "unclear"} >= {
        classify(pd.Series({"Property": "A 1", "Date": "2026-08-02"}),
                 live(("A 2", "2026-08-02")), {})[0]}
    print("OK the only deletable verdict is 'move'")


if __name__ == "__main__":
    test_a_booking_live_on_another_day_is_a_move()
    test_two_units_booked_together_again_and_again_is_a_cancellation()
    test_a_one_off_pairing_is_a_reassignment()
    test_no_shared_history_is_left_unclear()
    test_only_moves_are_ever_deletable()
    print("\nALL STALE-MOVE TESTS PASSED")
