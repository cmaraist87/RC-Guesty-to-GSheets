"""Tests for the nightly reservation fingerprint + diff.

Run: python test_reservation_snapshot.py
"""
import json

from lease_lock import InMemoryObjectStore
from reservation_snapshot import (SNAPSHOT_NAME, diff, fingerprint, format_report,
                                  load, save)


def res(rid, code, checkout, guest="John Doe", listing="1201 N Roman V2",
        checkin="2026-09-02", status="confirmed"):
    return {"_id": rid, "confirmationCode": code, "status": status,
            "guest": {"fullName": guest},
            "listing": {"nickname": listing, "_id": "lst_1",
                        "address": {"city": "New Orleans"}},
            "checkInDateLocalized": checkin, "checkOutDateLocalized": checkout}


def test_fingerprint_keyed_by_reservation_id():
    fp = fingerprint([res("r1", "HA-AAA", "2026-09-02")])
    assert list(fp) == ["r1"], fp
    assert fp["r1"]["code"] == "HA-AAA" and fp["r1"]["checkout"] == "2026-09-02"
    # No _id -> dropped, never keyed on something that could collide.
    assert fingerprint([{"confirmationCode": "HA-BBB"}]) == {}
    print("OK fingerprint: keyed by _id, id-less reservations dropped")


def test_date_change_is_an_edit_not_a_cancellation():
    """The exact case that made the sheet strike a live booking.

    John Doe moves his checkout 09/02 -> 09/04. Keyed by _id that is ONE edited
    reservation, not a disappearance plus an arrival.
    """
    before = fingerprint([res("r1", "HA-AAA", "2026-09-02")])
    after = fingerprint([res("r1", "HA-AAA", "2026-09-04")])
    d = diff(before, after)
    assert d["counts"] == {"added": 0, "gone": 0, "left_window": 0, "edited": 1,
                           "code_changed": 0, "total": 1}, d["counts"]
    assert d["edited"][0]["changed"] == ["checkout"], d["edited"]
    assert d["edited"][0]["from"]["checkout"] == "2026-09-02"
    assert d["edited"][0]["to"]["checkout"] == "2026-09-04"
    print("OK diff: a date change reads as one edit, not a cancel + a new booking")


def test_code_change_is_flagged_loudly():
    """If a channel reissues the code on an edit, this is what proves it."""
    before = fingerprint([res("r1", "HA-AAA", "2026-09-02")])
    after = fingerprint([res("r1", "HA-ZZZ", "2026-09-04")])
    d = diff(before, after)
    assert d["counts"]["code_changed"] == 1, d["counts"]
    assert d["counts"]["gone"] == 0 and d["counts"]["added"] == 0, d["counts"]
    assert set(d["code_changed"][0]["changed"]) == {"code", "checkout"}
    report = format_report(d, taken_at="2026-08-28T09:00:00+00:00")
    assert "CHANGED CONFIRMATION CODE" in report and "HA-AAA -> HA-ZZZ" in report, report
    print("OK diff: a reissued confirmation code is caught and reported loudly")


def test_real_cancellation_and_arrival():
    before = fingerprint([res("r1", "HA-AAA", "2026-09-02"),
                          res("r2", "HA-BBB", "2026-09-05", guest="Jane Roe")])
    after = fingerprint([res("r1", "HA-AAA", "2026-09-02"),
                         res("r3", "HA-CCC", "2026-09-09", guest="Ann Lee")])
    d = diff(before, after)
    assert d["gone"] == ["r2"] and d["added"] == ["r3"], d
    assert d["counts"]["edited"] == 0, d["counts"]
    print("OK diff: a disappearance is a cancellation, an appearance is a new booking")


def test_unchanged_run_is_silent():
    fp = fingerprint([res("r1", "HA-AAA", "2026-09-02")])
    d = diff(fp, fp)
    assert d["counts"] == {"added": 0, "gone": 0, "left_window": 0, "edited": 0,
                           "code_changed": 0, "total": 1}, d["counts"]
    assert "held on every edit" not in format_report(d, taken_at="x")
    print("OK diff: a quiet night reports nothing")


def test_round_trip_through_the_object_store():
    store = InMemoryObjectStore()
    fp0, gen0, taken0 = load(store)
    assert fp0 == {} and taken0 == "", (fp0, taken0)   # first run: baseline, no diff
    assert "baseline recorded" in format_report(diff({}, {}), taken_at=taken0)

    day1 = fingerprint([res("r1", "HA-AAA", "2026-09-02")])
    assert save(store, day1, gen0)

    back, gen1, taken1 = load(store)
    assert back == day1, (back, day1)
    assert taken1, "the stored snapshot must carry when it was taken"
    assert gen1 != gen0

    day2 = fingerprint([res("r1", "HA-AAA", "2026-09-04")])
    assert diff(back, day2)["counts"]["edited"] == 1
    assert save(store, day2, gen1)
    print("OK store: baseline -> save -> reload -> diff, with generations advancing")


def test_store_failures_never_break_the_sync():
    class Broken:
        def read(self, name):
            raise RuntimeError("bucket on fire")

        def write(self, name, payload, if_generation_match):
            raise RuntimeError("bucket still on fire")

    assert load(Broken()) == ({}, 0, "")
    assert save(Broken(), {"r1": {}}, 0) is False
    assert load(None) == ({}, 0, "")     # unconfigured bucket is not an error
    assert save(None, {}, 0) is False

    store = InMemoryObjectStore()
    store.write(SNAPSHOT_NAME, b"{not json", if_generation_match=0)
    fp, _, taken = load(store)
    assert fp == {} and taken == "", (fp, taken)
    print("OK resilience: an unreachable or corrupt snapshot degrades to a baseline")


def test_window_exit_is_not_a_cancellation():
    """The fetch window slides every night. Ageing out is not a cancellation.

    Without this, every morning's report would open with a fabricated cancellation
    count -- the fastest way to make a report worth ignoring.
    """
    before = fingerprint([res("r1", "HA-AAA", "2026-07-01", checkin="2026-06-28"),
                          res("r2", "HA-BBB", "2026-09-10", checkin="2026-09-05")])
    d = diff(before, {}, window=("2026-08-01", "2026-11-30"))
    assert d["gone"] == ["r2"], d["gone"]                  # inside the window: real
    assert d["left_window"] == ["r1"], d["left_window"]    # aged out: not a cancellation
    assert d["counts"]["gone"] == 1 and d["counts"]["left_window"] == 1, d["counts"]
    report = format_report(d, taken_at="2026-08-28T09:00:00+00:00")
    assert "1 cancelled" in report and "aged out" in report, report
    # A reservation still to come but beyond the lookahead is equally not cancelled.
    far = fingerprint([res("r3", "HA-CCC", "2027-03-02", checkin="2027-03-01")])
    assert diff(far, {}, window=("2026-08-01", "2026-11-30"))["gone"] == []
    print("OK diff: reservations that age out of the window are not called cancellations")


def test_no_window_means_every_disappearance_counts():
    before = fingerprint([res("r1", "HA-AAA", "2026-07-01", checkin="2026-06-28")])
    assert diff(before, {})["gone"] == ["r1"]
    print("OK diff: with no window given, nothing is excused")


if __name__ == "__main__":
    test_fingerprint_keyed_by_reservation_id()
    test_date_change_is_an_edit_not_a_cancellation()
    test_code_change_is_flagged_loudly()
    test_real_cancellation_and_arrival()
    test_unchanged_run_is_silent()
    test_round_trip_through_the_object_store()
    test_store_failures_never_break_the_sync()
    test_window_exit_is_not_a_cancellation()
    test_no_window_means_every_disappearance_counts()
    print("\nALL RESERVATION-SNAPSHOT TESTS PASSED")
