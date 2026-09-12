"""Cancellation decided by what Guesty LOST, not by the shape of the sheet.

Run: python test_vanished_codes.py

Every cancellation bug this project has had came from inferring "cancelled" when
the real question -- did this reservation disappear? -- was answerable. The
snapshot knows: it fingerprints the fetch each night and reports exactly which
reservations vanished.

With that in hand only one ambiguity is left. A reservation Guesty still holds may
have changed date, been reassigned, or lost one unit of a combined listing -- and
the last of those IS a cancellation while the other two are not.
"""
import pandas as pd

from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]
SEP = ("2026-09-01", "2026-09-30")


def row(date, prop, guest, code):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "assigned": "", "Property": prop, "Verified": "",
            "Check out - Time": "", "OUT": "", "Check-in Time": "", "IN": "",
            "T/O": "", "Adjustments": ""}


def cand(date, prop, guest, code):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "Property": prop, "Check-out Time": "",
            "Check-in Time": "", "T/O": "", "Adjustments": ""}


def merge(candidates, rows, **kw):
    return merge_reservations_into_sheet(
        pd.DataFrame(candidates or [], columns=list(cand("", "", "", "").keys())),
        pd.DataFrame(rows, columns=HEADER), cancel_window=SEP, **kw)


def test_guesty_lost_it_so_it_is_a_cancellation():
    sheet = [row("2026-09-15", "402 W Hall", "Gone Guest", "GONE1")]
    _f, stats, _c = merge([], sheet, vanished_codes={"GONE1"})
    assert (stats["cancelled"], stats["moved"]) == (1, 0), stats
    print("OK Guesty lost it -> CANCELLED")


def test_a_date_change_is_not_a_cancellation():
    """Paola Cardozo: checkout slid 09-15 -> 09-18, same property. Her old row was
    struck as a cancellation on 2026-09-11 and the reconciliation check caught it."""
    sheet = [row("2026-09-15", "402 W Hall", "Paola Cardozo", "GY-SZEyE3K2"),
             row("2026-09-18", "402 W Hall", "Paola Cardozo", "GY-SZEyE3K2")]
    cands = [cand("2026-09-18", "402 W Hall", "Paola Cardozo", "GY-SZEyE3K2")]
    _f, stats, _c = merge(cands, sheet, vanished_codes=set())
    assert (stats["cancelled"], stats["moved"]) == (0, 1), stats
    print("OK same property, new date -> MOVED (the 2026-09-11 bug)")


def test_a_reassignment_is_not_a_cancellation():
    """Taylor Eshmont: 31 Congress 301 -> 406 W Hall, two addresses with no shared
    history."""
    sheet = [row("2026-09-13", "31 Congress 301", "Taylor Eshmont", "HMWB2JXMHF"),
             row("2026-09-13", "406 W Hall", "Taylor Eshmont", "HMWB2JXMHF")]
    cands = [cand("2026-09-13", "406 W Hall", "Taylor Eshmont", "HMWB2JXMHF")]
    _f, stats, _c = merge(cands, sheet, vanished_codes=set())
    assert (stats["cancelled"], stats["moved"]) == (0, 1), stats
    print("OK reassigned to an unrelated property -> MOVED")


def test_one_unit_of_a_combined_listing_is_still_a_cancellation():
    """402 and 404 W Hall are one listing -- booked together again and again. When
    402 drops out the reservation survives, but that unit really was cancelled."""
    history = [r for i in range(1, 6)
               for r in (row(f"2026-09-0{i}", "402 W Hall", "G", f"H{i}"),
                         row(f"2026-09-0{i}", "404 W Hall", "G", f"H{i}"))]
    sheet = history + [row("2026-09-20", "402 W Hall", "Dana Reyes", "SHARED1"),
                       row("2026-09-20", "404 W Hall", "Dana Reyes", "SHARED1")]
    cands = ([cand("2026-09-20", "404 W Hall", "Dana Reyes", "SHARED1")]
             + [cand(f"2026-09-0{i}", p, "G", f"H{i}") for i in range(1, 6)
                for p in ("402 W Hall", "404 W Hall")])
    _f, stats, changes = merge(cands, sheet, vanished_codes=set())
    assert stats["cancelled"] == 1, (stats, changes["cancelled"])
    assert changes["cancelled"][0]["Property"] == "402 W Hall"
    assert stats["moved"] == 0, changes["moved"]
    print("OK a unit dropping out of a combined listing -> CANCELLED")


def test_without_a_snapshot_the_old_inference_still_runs():
    """First run, or shared state unreachable: nothing to go on, so behave as before
    rather than striking nothing at all."""
    sheet = [row("2026-09-15", "402 W Hall", "Gone Guest", "GONE1")]
    _f, stats, _c = merge([], sheet, vanished_codes=None)
    assert stats["cancelled"] == 1, stats
    print("OK no snapshot -> falls back to the old inference")


if __name__ == "__main__":
    test_guesty_lost_it_so_it_is_a_cancellation()
    test_a_date_change_is_not_a_cancellation()
    test_a_reassignment_is_not_a_cancellation()
    test_one_unit_of_a_combined_listing_is_still_a_cancellation()
    test_without_a_snapshot_the_old_inference_still_runs()
    print("\nALL VANISHED-CODE TESTS PASSED")
