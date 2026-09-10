"""A row older than the fetch window is never rewritten.

Run: python test_history_frozen.py

The fetch asks Guesty for checkOut >= some date. A stay that ended before that is
no longer carried -- but a LATER booking checking in on the same day still is, and
it produces a candidate for the same (Property, Date). Rewriting the row from that
candidate replaces a completed turnover with a check-in only, erasing the departure
clean from the record.

That is exactly what happened on 2026-09-09: the window moved to 8 September, and
17 rows dated the 7th lost their checkout time. The client reported them as jobs
that had never reached the sheet.
"""
import pandas as pd

from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]


def sheet_row(date, prop, guest, code, out="", inn="", to=""):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "assigned": "", "Property": prop, "Verified": "",
            "Check out - Time": out, "OUT": "", "Check-in Time": inn,
            "IN": "", "T/O": to, "Adjustments": ""}


def cand(date, prop, guest, code, out="", inn=""):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "Property": prop, "Check-out Time": out,
            "Check-in Time": inn, "T/O": "", "Adjustments": ""}


def merge(candidates, rows, **kw):
    return merge_reservations_into_sheet(
        pd.DataFrame(candidates or [], columns=list(cand("", "", "", "").keys())),
        pd.DataFrame(rows, columns=HEADER), **kw)


# The completed turnover, as the sheet recorded it on the day.
DONE = sheet_row("2026-09-07", "1022 Erato", "Anthony Piserchio", "HMRXFRZS2R",
                 out="11:00 AM", inn="3:00 PM", to="yes")
# All the fetch still carries: the guest who checked IN that afternoon.
PARTIAL = cand("2026-09-07", "1022 Erato", "Jesse Bielecke", "HMRXFRZS2R",
               inn="3:00 PM")


def test_a_past_row_keeps_its_checkout():
    full, stats, _ch = merge([PARTIAL], [DONE], history_before="2026-09-08")
    assert stats["updated"] == 0, stats
    row = full.iloc[0]
    assert row["Check out - Time"] == "11:00 AM", row.to_dict()
    assert row["T/O"] == "yes", row.to_dict()
    print("OK a 09-07 row keeps its 11:00 AM checkout once the window passes it")


def test_without_the_guard_the_checkout_is_lost():
    """The behaviour being fixed -- kept as proof the guard is what saves it."""
    full, stats, _ch = merge([PARTIAL], [DONE])
    assert stats["updated"] == 1, stats
    assert full.iloc[0]["Check out - Time"] == "", (
        "expected the old behaviour to erase the checkout")
    print("OK without the guard the same row loses its checkout (the reported bug)")


def test_a_row_inside_the_window_still_updates():
    """The guard must not freeze the live part of the sheet."""
    live = sheet_row("2026-09-20", "1022 Erato", "Old Name", "HMRXFRZS2R",
                     out="11:00 AM")
    changed = cand("2026-09-20", "1022 Erato", "New Name", "HMRXFRZS2R",
                   out="11:00 AM")
    full, stats, _ch = merge([changed], [live], history_before="2026-09-08")
    assert stats["updated"] == 1, stats
    assert full.iloc[0]["Guest"] == "New Name", full.iloc[0].to_dict()
    print("OK a row inside the window still updates normally")


def test_a_past_row_may_still_be_added():
    """Adding what the fetch does carry takes nothing away; only overwriting is
    refused."""
    newcomer = cand("2026-09-07", "1130 Baronne 3", "Rosaline Mitchell", "HM5MN3KD2X",
                    inn="4:00 PM")
    full, stats, _ch = merge([newcomer], [], history_before="2026-09-08")
    assert stats["new"] == 1, stats
    assert len(full) == 1 and full.iloc[0]["Property"] == "1130 Baronne 3"
    print("OK a past date with no row at all can still gain one")


def test_the_guard_is_off_when_no_date_is_given():
    full, stats, _ch = merge([PARTIAL], [DONE], history_before=None)
    assert stats["updated"] == 1, stats
    print("OK history_before=None leaves behaviour unchanged")


if __name__ == "__main__":
    test_a_past_row_keeps_its_checkout()
    test_without_the_guard_the_checkout_is_lost()
    test_a_row_inside_the_window_still_updates()
    test_a_past_row_may_still_be_added()
    test_the_guard_is_off_when_no_date_is_given()
    print("\nALL HISTORY-FROZEN TESTS PASSED")
