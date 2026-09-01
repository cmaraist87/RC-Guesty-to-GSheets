"""The nightly run must happen once a day even when GitHub delivers the trigger late.

Run: python test_daily_gate.py
"""
from datetime import datetime

from daily_gate import TZ, mark_complete, should_run
from lease_lock import InMemoryObjectStore


def at(h, m=0, day=1):
    return datetime(2026, 9, day, h, m, tzinfo=TZ)


def test_the_bug_that_started_this():
    """2026-09-01: both triggers arrived over four hours late, at Chicago 8 and 9.

    Under the old exact-hour rule both were skipped and the sync never ran. It must
    now run -- once -- no matter which delayed trigger arrives first.
    """
    store = InMemoryObjectStore()
    ok, why = should_run(store, now=at(8, 49))
    assert ok, why
    assert "first trigger" in why, why
    mark_complete(store, now=at(8, 52))            # it runs, takes ~3 minutes
    ok2, why2 = should_run(store, now=at(9, 22))
    assert not ok2, why2          # the second delayed trigger must not run again
    assert "already completed" in why2, why2
    print("OK late triggers: the 8:49 run proceeds, the 9:22 one stands down")


def test_only_one_run_per_day():
    store = InMemoryObjectStore()
    assert should_run(store, now=at(4, 0))[0]
    mark_complete(store, now=at(4, 3))
    for h, m in ((4, 30), (5, 0), (9, 22), (11, 59)):
        ok, why = should_run(store, now=at(h, m))
        assert not ok, (h, m, why)
        assert "already completed" in why, why
    print("OK once a day: after a completed run every later trigger stands down")


def test_a_new_day_runs_again():
    store = InMemoryObjectStore()
    assert should_run(store, now=at(4))[0]
    mark_complete(store, now=at(4, 3))
    ok, why = should_run(store, now=at(4, day=2))
    assert ok and "first trigger of 2026-09-02" in why, why
    print("OK a new day claims itself and runs")


def test_a_crashed_run_can_be_retried_the_same_day():
    """A claim is not a completion. A 4 AM crash must not cost the whole day."""
    store = InMemoryObjectStore()
    assert should_run(store, now=at(4))[0]      # claimed, then the run dies
    # Too soon to tell a dead run from a live one -- do not double-spend a token.
    ok_soon, why_soon = should_run(store, now=at(4, 10))
    assert not ok_soon and "still in flight" in why_soon, why_soon
    ok, why = should_run(store, now=at(9, 22))  # the second cron arrives, long after
    assert ok, why
    assert "never finished" in why and "attempt 2" in why, why
    mark_complete(store, now=at(9, 25))
    assert not should_run(store, now=at(10))[0]
    print("OK a claimed-but-unfinished day is retried, then closed on success")


def test_outside_the_window_never_runs():
    store = InMemoryObjectStore()
    for h in (0, 2, 12, 13, 23):
        ok, why = should_run(store, now=at(h))
        assert not ok, (h, why)
        assert "outside the" in why, why
    # And a trigger outside the window must not have claimed the day.
    assert should_run(store, now=at(4))[0], "the real morning run must still be free"
    print("OK the window holds: a 1 AM or 1 PM trigger is not this morning's run")


def test_no_shared_store_falls_back_to_the_exact_hour():
    """Without state there is nothing to claim, so two triggers could both spend a
    Guesty token. Missing a run is recoverable; double-spending the daily quota on
    a five-per-day limit is not."""
    ok, why = should_run(None, now=at(4))
    assert ok and "exact 4 AM rule" in why, why
    ok, why = should_run(None, now=at(9, 22))
    assert not ok and "cannot both run" in why, why
    print("OK no store: degrades to the old exact-hour rule rather than double-running")


def test_two_triggers_racing_produce_one_run():
    """Both crons land at once. The compare-and-swap decides; only one proceeds."""
    store = InMemoryObjectStore()
    results = [should_run(store, now=at(8, 49)), should_run(store, now=at(8, 49))]
    assert sum(1 for ok, _ in results if ok) == 1, results
    assert "still in flight" in results[1][1], results
    print("OK a race between two triggers still yields exactly one run")


def test_an_unreadable_record_skips_rather_than_double_runs():
    class Broken:
        def read(self, name):
            raise RuntimeError("bucket unreachable")

    ok, why = should_run(Broken(), now=at(4))
    assert not ok and "unreadable" in why, why
    print("OK an unreachable bucket skips rather than risking a double run")


if __name__ == "__main__":
    test_the_bug_that_started_this()
    test_only_one_run_per_day()
    test_a_new_day_runs_again()
    test_a_crashed_run_can_be_retried_the_same_day()
    test_outside_the_window_never_runs()
    test_no_shared_store_falls_back_to_the_exact_hour()
    test_two_triggers_racing_produce_one_run()
    test_an_unreadable_record_skips_rather_than_double_runs()
    print("\nALL DAILY-GATE TESTS PASSED")
