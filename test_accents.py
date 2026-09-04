"""Per-cell colours: turnover on the Property cell, early/late on the time cells.

Run: python test_accents.py

    Column M (T/O) = yes            -> Column G (Property)        light cornflower blue 2
    Column N has ECO or LCO         -> Column I (Check-out Time)  light red 3
    Column N has ECI or LCI         -> Column K (Check-in Time)   light red 3
"""
import sheets_client
from sheets_client import (ADJUSTMENT_RGB, TURNOVER_RGB, accent_columns,
                           apply_row_marks, desired_accents)

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", ".", "Property",
          "FALSE", "Check out - Time", "FALSE.1", "Check-in Time", "FALSE.2",
          "T/O", "Adjustments"]
G, I, K = 6, 8, 10


def _row(prop="1201 N Roman", to="", adj=""):
    r = [""] * len(HEADER)
    r[G], r[8], r[10], r[12], r[13] = prop, "11:00 AM", "4:00 PM", to, adj
    return r


class RecordingSS:
    def __init__(self):
        self.requests = []

    def batch_update(self, body):
        self.requests.extend(body["requests"])


class FakeWS:
    def __init__(self):
        self.title, self.id = "Septiembre 2026", 1
        self.spreadsheet = RecordingSS()


def test_the_columns_land_on_G_I_K_M_N():
    cols = accent_columns(HEADER)
    assert cols["property"] == G, cols          # G
    assert cols["checkout"] == I, cols          # I
    assert cols["checkin"] == K, cols           # K
    assert cols["turnover"] == 12, cols         # M
    assert cols["adjustments"] == 13, cols      # N
    print("OK: the roles resolve to G, I, K, M and N on the live layout")


def test_turnover_colours_the_property_cell_only():
    got = desired_accents([_row(to="yes")], HEADER)
    assert got == {(0, G): TURNOVER_RGB}, got
    assert desired_accents([_row(to="")], HEADER) == {}
    print("OK: T/O yes fills Property, and nothing else")


def test_out_codes_colour_the_checkout_time_and_in_codes_the_checkin():
    for code in ("ECO", "LCO"):
        got = desired_accents([_row(adj=code)], HEADER)
        assert got == {(0, I): ADJUSTMENT_RGB}, (code, got)
    for code in ("ECI", "LCI"):
        got = desired_accents([_row(adj=code)], HEADER)
        assert got == {(0, K): ADJUSTMENT_RGB}, (code, got)
    # Both ends adjusted, and a turnover on top: three separate cells.
    got = desired_accents([_row(to="yes", adj="ECO, LCI")], HEADER)
    assert got == {(0, G): TURNOVER_RGB, (0, I): ADJUSTMENT_RGB,
                   (0, K): ADJUSTMENT_RGB}, got
    print("OK: out-codes colour the check-out time, in-codes the check-in time")


def test_an_accent_survives_the_amber_that_is_painted_over_it():
    """The row highlight covers the whole row, so on a row it just painted the
    accent underneath is gone. Those rows must be repainted even though nothing
    about the accent itself changed."""
    ws = FakeWS()
    rows = [_row(to="yes")]
    marks = apply_row_marks(ws, ["new"], prior_highlight=set(), prior_struck=set(),
                            n_cols=len(HEADER),
                            accents_wanted=desired_accents(rows, HEADER),
                            accents_have={(0, G): TURNOVER_RGB})  # already correct
    assert marks["accents"] == 1, marks
    order = [(r["repeatCell"]["range"].get("startColumnIndex"),
              r["repeatCell"]["range"].get("endColumnIndex"))
             for r in ws.spreadsheet.requests]
    assert order[-1] == (G, G + 1), order   # the accent is painted LAST, so it wins
    print("OK: an accent flattened by the amber row fill is repainted on top")


def test_a_correct_accent_on_a_quiet_row_is_not_repainted():
    ws = FakeWS()
    rows = [_row(to="yes")]
    marks = apply_row_marks(ws, [""], prior_highlight=set(), prior_struck=set(),
                            n_cols=len(HEADER),
                            accents_wanted=desired_accents(rows, HEADER),
                            accents_have={(0, G): TURNOVER_RGB})
    assert marks["accents"] == 0, marks
    assert ws.spreadsheet.requests == [], ws.spreadsheet.requests
    print("OK: a colour already correct costs no request -- a quiet week sends none")


def test_an_accent_that_stops_applying_goes_back_to_the_row_colour():
    """A booking that stops being a turnover must not be left blue -- and on an
    amber row it goes back to amber, not to white."""
    ws = FakeWS()
    rows = [_row(to="")]                       # no longer a turnover
    apply_row_marks(ws, ["updated"], prior_highlight={0}, prior_struck=set(),
                    n_cols=len(HEADER),
                    accents_wanted=desired_accents(rows, HEADER),
                    accents_have={(0, G): TURNOVER_RGB})
    fills = [r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
             for r in ws.spreadsheet.requests
             if r["repeatCell"]["range"].get("startColumnIndex") == G]
    assert fills and sheets_client._same_rgb(fills[-1], sheets_client.HIGHLIGHT_RGB), fills

    ws2 = FakeWS()
    apply_row_marks(ws2, [""], prior_highlight=set(), prior_struck=set(),
                    n_cols=len(HEADER),
                    accents_wanted=desired_accents(rows, HEADER),
                    accents_have={(0, G): TURNOVER_RGB})
    fills2 = [r["repeatCell"]["cell"]["userEnteredFormat"]["backgroundColor"]
              for r in ws2.spreadsheet.requests
              if r["repeatCell"]["range"].get("startColumnIndex") == G]
    assert fills2 and sheets_client._same_rgb(fills2[-1], sheets_client.NO_FILL_RGB), fills2
    print("OK: a lapsed accent reverts to the row's own colour, amber or white")


def test_a_colour_the_team_applied_by_hand_is_left_alone():
    """Only our two colours are ever cleared. Anything else on those cells is
    somebody's own marking and is none of the sync's business."""
    ws = FakeWS()
    theirs = {"red": 0.7, "green": 0.9, "blue": 0.7}     # some green they chose
    apply_row_marks(ws, [""], prior_highlight=set(), prior_struck=set(),
                    n_cols=len(HEADER),
                    accents_wanted=desired_accents([_row()], HEADER),
                    accents_have={(0, G): theirs})
    assert ws.spreadsheet.requests == [], ws.spreadsheet.requests
    print("OK: a hand-applied colour is never cleared by the sync")


def test_an_unrecognisable_layout_paints_nothing():
    assert desired_accents([_row(to="yes")], ["a", "b", "c"]) == {}
    print("OK: a layout without those columns produces no accents at all")


if __name__ == "__main__":
    test_the_columns_land_on_G_I_K_M_N()
    test_turnover_colours_the_property_cell_only()
    test_out_codes_colour_the_checkout_time_and_in_codes_the_checkin()
    test_an_accent_survives_the_amber_that_is_painted_over_it()
    test_a_correct_accent_on_a_quiet_row_is_not_repainted()
    test_an_accent_that_stops_applying_goes_back_to_the_row_colour()
    test_a_colour_the_team_applied_by_hand_is_left_alone()
    test_an_unrecognisable_layout_paints_nothing()
    print("\nALL ACCENT TESTS PASSED")
