"""The cancellation count must reconcile against Guesty's own figure.

Run: python test_reconciliation.py

The month's cancellation total is the number the client audits, and the only way it
goes wrong is a row struck for a booking that did not cancel. Guesty's own figure
for that is the snapshot's `gone`: reservations present last night, absent tonight,
still inside the fetch window.

Rows are not reservations -- one cancelled booking strikes a check-out row and a
check-in row, and a combined listing multiplies both. What can never legitimately
exceed `gone` is the number of DISTINCT confirmation codes struck.
"""
from sync import _reconciles


def snap(gone, had_baseline=True):
    return {"had_baseline": had_baseline, "counts": {"gone": gone}}


def test_the_2026_09_09_failure_would_have_fired():
    """Guesty lost 2 reservations. The sheet struck 4 bookings -- the extra two
    being Taylor Eshmont and Lareina Kostenchuk, who had merely moved. Nothing
    compared the two numbers, so it stayed wrong for two days and the client found
    it first."""
    struck = {"HMQ4JEA5CQ", "HMQFJTFFX8", "HMWB2JXMHF", "HMKMRDCK9Y"}
    assert _reconciles(struck, snap(2)) is False
    print("OK 4 bookings struck vs 2 lost -> FAILS (the bug that reached the client)")


def test_a_clean_run_passes():
    """2026-09-11: Kelly Mangan and Nicole Aguilar, 4 rows, 2 bookings, 2 lost."""
    assert _reconciles({"HMTKFS2EPF", "HMPXPWPZED"}, snap(2)) is True
    print("OK 2 bookings struck vs 2 lost -> passes")


def test_many_rows_from_few_bookings_is_fine():
    """A combined listing strikes several rows under ONE code. Counting rows would
    false-alarm here; counting bookings does not."""
    assert _reconciles({"HMQFJTFFX8"}, snap(2)) is True
    print("OK one booking across many rows, 2 lost -> passes")


def test_striking_nothing_always_passes():
    assert _reconciles(set(), snap(0)) is True
    print("OK striking nothing -> passes")


def test_striking_when_guesty_lost_nothing_fails():
    """The sharpest case: Guesty lost no reservation at all, so no row can be a
    cancellation."""
    assert _reconciles({"HMANY00001"}, snap(0)) is False
    print("OK any strike when Guesty lost nothing -> FAILS")


def test_a_first_run_is_never_judged():
    """No baseline: everything reads as added and nothing as gone, so the
    comparison is meaningless and must stay silent."""
    assert _reconciles({"A", "B", "C"}, snap(0, had_baseline=False)) is True
    assert _reconciles({"A", "B", "C"}, None) is True
    print("OK no baseline -> silent, never a false alarm")


if __name__ == "__main__":
    test_the_2026_09_09_failure_would_have_fired()
    test_a_clean_run_passes()
    test_many_rows_from_few_bookings_is_fine()
    test_striking_nothing_always_passes()
    test_striking_when_guesty_lost_nothing_fails()
    test_a_first_run_is_never_judged()
    print("\nALL RECONCILIATION TESTS PASSED")
