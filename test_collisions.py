"""Two bookings on one property and date: one row out, and it says so.

Run: python test_collisions.py

process_reservations keys its lookups on (property, date), so a second booking on
the same property and day REPLACES the first. Only one row can exist -- the sheet's
model really is one row per property per day -- but until 2026-09-19 the loser
disappeared with no message anywhere.

That is not hypothetical. normalize_property strips the version marker, so
"717 Teche V1" and "717 Teche V2" are one property here, and two distinct Guesty
listings collapse into one slot.

HM9HSTFK88 (Angie Flanigan, 717 Teche, 17 Sept) was reported missing from the sheet
and entered by hand. It passed every filter and was in the fetch, so a silent
overwrite is the mechanism that fits; by the time the search narrowed this far the
booking had aged out of the window and it could no longer be confirmed against live
data. The collapse is pinned here either way, because a job vanishing without a
word is worth catching whatever caused that particular case.
"""
import io
from contextlib import redirect_stdout

from guesty_adapter import reservations_to_frames
from processing import process_reservations


def res(code, guest, ci, co, listing, city="New Orleans"):
    return {"_id": "r" + code, "confirmationCode": code, "status": "confirmed",
            "guest": {"fullName": guest},
            "listing": {"_id": "l" + code, "nickname": listing,
                        "address": {"city": city}},
            "checkInDateLocalized": ci, "checkOutDateLocalized": co,
            "plannedArrival": "04:00 PM", "plannedDeparture": "11:00 AM"}


def run(reservations):
    co, ci = reservations_to_frames(reservations)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cand = process_reservations(co, ci, city_seed={})
    return cand, buf.getvalue()


def test_a_collision_is_reported_not_swallowed():
    """The whole point: the loser must be named, in the log, on the day it happens."""
    cand, out = run([
        res("HM9HSTFK88", "Angie Flanigan", "2026-09-15", "2026-09-17", "717 Teche V1"),
        res("HMOTHER111", "Someone Else", "2026-09-12", "2026-09-17", "717 Teche V2"),
    ])
    assert "were overwritten" in out, out
    assert "HM9HSTFK88" in out, "the booking that was dropped must be named"
    assert "717 Teche" in out and "2026-09-17" in out, out
    print("OK a collision names the booking it dropped")


def test_only_one_row_survives_the_collision():
    """Documenting the behaviour, not endorsing it: one row per property per day."""
    cand, _ = run([
        res("HM9HSTFK88", "Angie Flanigan", "2026-09-15", "2026-09-17", "717 Teche V1"),
        res("HMOTHER111", "Someone Else", "2026-09-12", "2026-09-17", "717 Teche V2"),
    ])
    day = cand[cand["Date"] == "2026-09-17"]
    assert len(day) == 1, day.to_string()
    print("OK one row survives, as the sheet's model requires")


def test_the_version_marker_is_what_makes_them_collide():
    """V1 and V2 are different listings in Guesty and the same property here."""
    from processing import normalize_property

    assert normalize_property("717 Teche V1") == normalize_property("717 Teche V2")
    assert normalize_property("717 Teche V1") == ["717 Teche"]
    print("OK V1 and V2 normalise to one property -- the cause of the collision")


def test_no_collision_stays_quiet():
    """Two bookings at the same property on DIFFERENT days are not a collision, and
    a warning that cried wolf would be ignored within a week."""
    cand, out = run([
        res("HMAAA", "First Guest", "2026-09-12", "2026-09-15", "717 Teche V1"),
        res("HMBBB", "Second Guest", "2026-09-15", "2026-09-19", "717 Teche V1"),
    ])
    assert "were overwritten" not in out, out
    print("OK different days are not a collision")


def test_different_properties_same_day_stay_quiet():
    cand, out = run([
        res("HMAAA", "First Guest", "2026-09-15", "2026-09-17", "717 Teche V1"),
        res("HMBBB", "Second Guest", "2026-09-15", "2026-09-17", "1022 Erato V1"),
    ])
    assert "were overwritten" not in out, out
    assert len(cand[cand["Date"] == "2026-09-17"]) == 2
    print("OK different properties on one day both survive")


if __name__ == "__main__":
    test_a_collision_is_reported_not_swallowed()
    test_only_one_row_survives_the_collision()
    test_the_version_marker_is_what_makes_them_collide()
    test_no_collision_stays_quiet()
    test_different_properties_same_day_stay_quiet()
    print("\nALL COLLISION TESTS PASSED")
