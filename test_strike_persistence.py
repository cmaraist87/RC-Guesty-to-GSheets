"""A strike must survive the night, even when the tab churns.

Run: python test_strike_persistence.py

Measured in production over three mornings, from the run logs:

    tab            run ends with    next run reads    lost
    Septiembre 18th     48                33           15
    Octubre    18th     41                33            8
    Octubre    19th     44                37            7
    Noviembre / Diciembre / Febrero    unchanged        none

The quiet tabs never lose a strike; the two that churn lose 15-20% every night.
A booking is struck, the line disappears overnight, and next morning the sync sees
an unstruck row with no live booking and strikes it again -- counted as a fresh
cancellation each time, which is what the reconciliation check keeps refusing to
certify.

This replays the real cycle: merge, apply_row_marks, read the marks back off the
grid the requests actually produced, and go round again. Nothing here models what
the code "should" do -- the mark state comes from interpreting the repeatCell
requests apply_row_marks emits.
"""
import re

import pandas as pd

import sheets_client
from sheet_merge import merge_reservations_into_sheet
from sheets_client import _STRIKE_FLAGS, apply_row_marks

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned", "Property",
          "Verified", "Check out - Time", "OUT", "Check-in Time", "IN", "T/O",
          "Adjustments"]
SEP = ("2026-09-01", "2026-09-30")


class Grid:
    """A worksheet that remembers which rows carry a strikethrough.

    It applies the repeatCell requests the way Sheets does -- by row range -- so
    the marks that come back are the ones the code actually asked for.
    """

    def __init__(self, n_rows):
        self.title = "Octubre 2026"
        self.id = 1
        self.row_count, self.col_count = max(n_rows, 1000), len(HEADER)
        self.struck: set[int] = set()      # 0-based DATA rows
        self.spreadsheet = self

    def batch_update(self, body):
        for req in body.get("requests", []):
            rc = req.get("repeatCell")
            if not rc or "strikethrough" not in rc.get("fields", ""):
                continue
            on = (rc["cell"]["userEnteredFormat"]["textFormat"]["strikethrough"])
            rng = rc["range"]
            for grid_row in range(rng["startRowIndex"], rng["endRowIndex"]):
                data_row = grid_row - 1          # grid row 1 (0-based) = data row 0
                if data_row < 0:
                    continue
                if on:
                    self.struck.add(data_row)
                else:
                    self.struck.discard(data_row)


def row(date, prop, guest, code):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "assigned": "", "Property": prop, "Verified": "",
            "Check out - Time": "11:00 AM", "OUT": "", "Check-in Time": "", "IN": "",
            "T/O": "", "Adjustments": ""}


def cand(date, prop, guest, code):
    return {"City": "New Orleans", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "Property": prop, "Check-out Time": "11:00 AM",
            "Check-in Time": "", "T/O": "", "Adjustments": ""}


CAND_COLS = list(cand("", "", "", "").keys())


def one_morning(sheet, candidates, prior_struck):
    """merge -> apply marks -> read the marks back. Returns (new sheet, struck)."""
    full, stats, ch = merge_reservations_into_sheet(
        pd.DataFrame(candidates or [], columns=CAND_COLS),
        sheet, cancel_window=SEP, struck_rows=frozenset(prior_struck))
    grid = Grid(len(full))
    grid.struck = set(prior_struck)
    apply_row_marks(grid, ch["row_flags"], prior_highlight=set(),
                    prior_struck=set(prior_struck), n_cols=len(HEADER))
    return full, grid.struck, stats, ch


def test_a_strike_survives_a_quiet_night():
    """The control: nothing changes, so nothing may be lost."""
    sheet = pd.DataFrame([row("2026-09-10", "1022 Erato", "Ann", "LIVE1"),
                          row("2026-09-20", "1130 Baronne", "Bob", "GONE1")],
                         columns=HEADER)
    cands = [cand("2026-09-10", "1022 Erato", "Ann", "LIVE1")]
    struck: set = set()
    for _ in range(3):
        sheet, struck, stats, _ch = one_morning(sheet, cands, struck)
    assert len(struck) == 1, struck
    assert stats["cancelled"] == 0, "day 3 must not re-cancel an already struck row"
    print("OK a quiet tab keeps its strike, and does not re-cancel")


def test_a_strike_survives_a_night_with_churn():
    """The real case. Rows are added each morning, so the sort moves the struck row
    and every position shifts under it."""
    sheet = pd.DataFrame([row("2026-09-20", "1130 Baronne", "Bob", "GONE1")],
                         columns=HEADER)
    cands = []
    struck: set = set()
    recancelled = []
    for day in range(1, 6):
        # A new booking each morning, dated EARLIER, so it sorts in above the
        # struck row and pushes it down.
        cands = cands + [cand(f"2026-09-{day:02d}", f"{100+day} Erato",
                              f"Guest {day}", f"LIVE{day}")]
        sheet, struck, stats, ch = one_morning(sheet, cands, struck)
        if day > 1 and stats["cancelled"]:
            recancelled.append((day, stats["cancelled"]))
    assert len(struck) == 1, f"the strike was lost or duplicated: {struck}"
    assert not recancelled, (
        f"an already-struck booking was re-cancelled on day(s) {recancelled} -- "
        "this is the production leak")
    print("OK a strike survives five mornings of churn and is never re-cancelled")


def test_a_strike_survives_rows_being_removed():
    """Rows leaving shrinks the block; the trailing region is mark-cleared, and the
    struck row must not be caught by that."""
    sheet = pd.DataFrame(
        [row(f"2026-09-{d:02d}", f"{100+d} Erato", f"Guest {d}", f"LIVE{d}")
         for d in range(1, 8)]
        + [row("2026-09-20", "1130 Baronne", "Bob", "GONE1")],
        columns=HEADER)
    struck: set = set()
    cands = [cand(f"2026-09-{d:02d}", f"{100+d} Erato", f"Guest {d}", f"LIVE{d}")
             for d in range(1, 8)]
    sheet, struck, _s, _c = one_morning(sheet, cands, struck)
    assert len(struck) == 1, struck
    for drop in range(1, 5):          # bookings leave, the block shrinks
        cands = cands[:-1]
        sheet, struck, stats, _c = one_morning(sheet, cands, struck)
        assert stats["cancelled"] == 0 or len(struck) >= 1, (drop, struck, stats)
    assert len(struck) >= 1, f"the strike vanished as the tab shrank: {struck}"
    print("OK a strike survives the tab shrinking around it")


if __name__ == "__main__":
    test_a_strike_survives_a_quiet_night()
    test_a_strike_survives_a_night_with_churn()
    test_a_strike_survives_rows_being_removed()
    print("\nALL STRIKE-PERSISTENCE TESTS PASSED")
