"""The amber lifecycle: painted the day a row changes, gone the next day.

Run: python test_amber_lifecycle.py

The rule, as the sheet's owner states it:

    A row is amber on the cycle where it arrives or changes. At the next cycle it
    loses the amber, because it is no longer new -- UNLESS something in it changed
    again (date, times, guest, anything), in which case it stays amber or turns
    amber again.

This drives the real merge and the real mark painter across consecutive days and
asserts the amber actually lifts. Nothing here is mocked except the worksheet.
"""
import pandas as pd

import sheets_client
from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]

BOOKING = {
    "City": "New Orleans", "Day": "Sunday", "Date": "2026-11-01",
    "Confirmation Code": "HM2HTCNFT5", "Guest": "James Cull",
    "Property": "1022 Mandeville", "Check-out Time": "11:00 AM",
    "Check-in Time": "", "T/O": "", "Adjustments": "",
}
HL_COL = sheets_client.highlight_columns(HEADER)[0]


def a_day(candidates, sheet, prior_highlight):
    """One nightly cycle. Returns (new sheet, which rows are amber afterwards)."""
    full, _stats, changes = merge_reservations_into_sheet(
        pd.DataFrame(candidates), sheet, city_ref_csv="property_to_city.csv")
    flags = changes["row_flags"]

    amber_now = {i for i, f in enumerate(flags)
                 if f in sheets_client._HIGHLIGHT_FLAGS}
    lifted = prior_highlight - amber_now
    painted = amber_now - prior_highlight
    return full, amber_now, painted, lifted, flags


def test_amber_is_painted_then_lifts_then_returns():
    sheet = pd.DataFrame(columns=HEADER)
    amber = set()

    # DAY 1 -- the booking appears for the first time.
    sheet, amber, painted, lifted, flags = a_day([BOOKING], sheet, amber)
    assert flags == ["new"], flags
    assert amber == {0} and painted == {0} and not lifted
    print("day 1  booking arrives          -> flag 'new',       AMBER ON")

    # DAY 2 -- Guesty returns the identical booking. Nothing changed.
    sheet, amber, painted, lifted, flags = a_day([BOOKING], sheet, amber)
    assert flags == [""], flags
    assert amber == set() and lifted == {0} and not painted
    print("day 2  nothing changed          -> flag '',          AMBER LIFTS")

    # DAY 3 -- the guest name changes. Same confirmation code, same slot.
    renamed = dict(BOOKING, Guest="James Cullen")
    sheet, amber, painted, lifted, flags = a_day([renamed], sheet, amber)
    assert flags[-1] == "updated", flags
    assert painted, "a changed row must go amber again"
    print("day 3  guest name changed       -> flag 'updated',   AMBER RETURNS")

    # DAY 4 -- quiet again. The amber must come straight back off.
    sheet, amber, painted, lifted, flags = a_day([renamed], sheet, amber)
    assert flags == [""], flags
    assert amber == set() and lifted, (amber, lifted)
    print("day 4  quiet again              -> flag '',          AMBER LIFTS")


def test_each_kind_of_change_re_ambers_the_row():
    """'Anything changes' -- checked one field at a time, each from a quiet sheet."""
    settled = pd.DataFrame(columns=HEADER)
    settled, _amber, _p, _l, _f = a_day([BOOKING], settled, set())
    settled, _amber, _p, _l, flags = a_day([BOOKING], settled, {0})
    assert flags == [""], "precondition: the sheet is quiet before each case"

    cases = {
        "check-out time": dict(BOOKING, **{"Check-out Time": "10:00 AM"}),
        "check-in time":  dict(BOOKING, **{"Check-in Time": "4:00 PM"}),
        "guest":          dict(BOOKING, Guest="Someone Else"),
        "date":           dict(BOOKING, Date="2026-11-02"),
        "property":       dict(BOOKING, Property="1024 Mandeville"),
    }
    for what, changed in cases.items():
        _full, amber, painted, _lifted, flags = a_day([changed], settled.copy(), set())
        assert painted, f"a changed {what} left the row un-ambered: {flags}"
        print(f"       {what:<15} changed      -> {sorted(set(flags))}, AMBER ON")


def test_the_painter_agrees_with_the_flags():
    """The flags above are only half the chain -- apply_row_marks turns them into
    the actual cell requests. Same three days, asserted on what it would send."""
    on = sheets_client.apply_row_marks.__wrapped__ \
        if hasattr(sheets_client.apply_row_marks, "__wrapped__") else None

    class WS:
        title, id = "Noviembre 2026", 0
        spreadsheet = None

    # Day 2's flags ('' for a settled row) against yesterday's amber must CLEAR it.
    marks = sheets_client.apply_row_marks(
        WS(), [""], prior_highlight={0}, prior_struck=set(), n_cols=len(HEADER),
        highlight_span=sheets_client.highlight_columns(HEADER))
    assert marks["unhighlighted"] == 1 and marks["highlighted"] == 0, marks
    print("painter: settled row + yesterday's amber -> 1 cleared, 0 painted")

    marks = sheets_client.apply_row_marks(
        WS(), ["updated"], prior_highlight=set(), prior_struck=set(),
        n_cols=len(HEADER), highlight_span=sheets_client.highlight_columns(HEADER))
    assert marks["highlighted"] == 1 and marks["unhighlighted"] == 0, marks
    print("painter: changed row + no amber          -> 1 painted, 0 cleared")


if __name__ == "__main__":
    test_amber_is_painted_then_lifts_then_returns()
    print()
    test_each_kind_of_change_re_ambers_the_row()
    print()
    test_the_painter_agrees_with_the_flags()
    print("\nALL AMBER LIFECYCLE TESTS PASSED")
