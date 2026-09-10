"""The tab stays in date order, and the marks travel with their rows.

Run: python test_row_order.py

New and updated rows are appended to the end of the sheet. Without a re-sort every
row the sync touches sinks to the bottom, and the month drifts further out of order
each morning. On 2026-09-10 Septiembre's 7 September jobs sat in five separate
blocks, the last at row 1177 of 1219 -- and the team read the stranded ones as
missing and reported them to the client as never delivered.

The marks (strikethrough, amber, accent fills) are painted BY POSITION from
row_flags, so if the rows move and the flags do not, every mark lands on the wrong
row. That is the same defect that put strikethroughs on the wrong bookings in
August, so it is pinned here too.
"""
import pandas as pd

from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]


def sheet_row(date, prop, guest, code):
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
        pd.DataFrame(rows, columns=HEADER), **kw)


def test_a_new_row_lands_in_date_order_not_at_the_bottom():
    sheet = [sheet_row("2026-09-05", "1022 Erato", "Ann Lee", "AAA1"),
             sheet_row("2026-09-20", "1130 Baronne", "Bob Fox", "BBB2")]
    candidates = [cand("2026-09-05", "1022 Erato", "Ann Lee", "AAA1"),
                  cand("2026-09-07", "1206 Magazine", "Cy Dunn", "CCC3"),
                  cand("2026-09-20", "1130 Baronne", "Bob Fox", "BBB2")]

    full, _stats, _ch = merge(candidates, sheet)
    dates = [str(d)[:10] for d in full["Date"]]
    assert dates == sorted(dates), dates
    assert dates[1] == "2026-09-07", f"the new row was appended, not sorted in: {dates}"
    print("OK a new 09-07 row sits between 09-05 and 09-20, not at the bottom")


def test_an_updated_row_does_not_sink():
    """The real mechanism: a row is rewritten every time anything about it changes,
    and each rewrite used to move it to the end."""
    sheet = [sheet_row("2026-09-07", "1022 Erato", "Ann Lee", "AAA1"),
             sheet_row("2026-09-08", "1130 Baronne", "Bob Fox", "BBB2"),
             sheet_row("2026-09-09", "1206 Magazine", "Cy Dunn", "CCC3")]
    # The 09-07 booking changes guest -> it is dropped and re-added as "updated".
    candidates = [cand("2026-09-07", "1022 Erato", "Ann Leigh", "AAA1"),
                  cand("2026-09-08", "1130 Baronne", "Bob Fox", "BBB2"),
                  cand("2026-09-09", "1206 Magazine", "Cy Dunn", "CCC3")]

    full, stats, _ch = merge(candidates, sheet)
    assert stats["updated"] == 1, stats
    dates = [str(d)[:10] for d in full["Date"]]
    assert dates == sorted(dates), dates
    assert str(full.iloc[0]["Guest"]) == "Ann Leigh", full[["Date", "Guest"]].to_dict()
    print("OK an updated row keeps its place in the month")


def test_the_marks_travel_with_their_rows():
    """A strike must stay on the booking it belongs to, not the position it sat in."""
    sheet = [sheet_row("2026-09-20", "1130 Baronne", "Bob Fox", "BBB2"),
             sheet_row("2026-09-07", "1022 Erato", "Ann Lee", "AAA1")]
    # Bob is gone from Guesty -> cancelled. Ann is unchanged. The sheet is out of
    # order, so sorting moves Bob from row 0 to row 1.
    candidates = [cand("2026-09-07", "1022 Erato", "Ann Lee", "AAA1")]

    full, stats, changes = merge(candidates, sheet,
                                 cancel_window=("2026-09-01", "2026-09-30"))
    assert stats["cancelled"] == 1, stats
    flags = changes["row_flags"]
    struck_at = [i for i, f in enumerate(flags) if f == "cancelled"]
    assert len(struck_at) == 1, flags
    i = struck_at[0]
    assert str(full.iloc[i]["Guest"]) == "Bob Fox", (
        f"the strike flag points at row {i}, which is "
        f"{full.iloc[i]['Guest']!r}, not the cancelled booking")
    assert [str(d)[:10] for d in full["Date"]] == ["2026-09-07", "2026-09-20"]
    print("OK the cancelled flag follows Bob Fox through the sort")


def test_a_tab_already_in_order_is_left_exactly_alone():
    sheet = [sheet_row("2026-09-07", "1022 Erato", "Ann Lee", "AAA1"),
             sheet_row("2026-09-08", "1130 Baronne", "Bob Fox", "BBB2")]
    candidates = [cand("2026-09-07", "1022 Erato", "Ann Lee", "AAA1"),
                  cand("2026-09-08", "1130 Baronne", "Bob Fox", "BBB2")]
    full, stats, _ch = merge(candidates, sheet)
    assert stats["unchanged"] == 2, stats
    assert list(full["Guest"]) == ["Ann Lee", "Bob Fox"], list(full["Guest"])
    print("OK a tab already in order is not reshuffled")


def test_same_day_rows_are_ordered_by_property():
    sheet = []
    candidates = [cand("2026-09-07", "852 Bartholomew", "C One", "C1"),
                  cand("2026-09-07", "1022 Erato", "A One", "A1"),
                  cand("2026-09-07", "1206 Magazine", "B One", "B1")]
    full, _stats, _ch = merge(candidates, sheet)
    props = list(full["Property"])
    assert props == sorted(props), props
    print("OK rows sharing a date are ordered by property")


if __name__ == "__main__":
    test_a_new_row_lands_in_date_order_not_at_the_bottom()
    test_an_updated_row_does_not_sink()
    test_the_marks_travel_with_their_rows()
    test_a_tab_already_in_order_is_left_exactly_alone()
    test_same_day_rows_are_ordered_by_property()
    print("\nALL ROW-ORDER TESTS PASSED")
