"""
Offline test of per-month tab routing (no network) using a mocked spreadsheet.

Run: python test_monthly_routing.py
"""
import os
import re
import tempfile

import sheets_client
import sync

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned", "Property",
          "Verified", "Check out - Time", "OUT", "Check-in Time", "IN",
          "T/O", "Adjustments", "", ""]


def _row(city, day, date, conf, guest, cb1, prop, co, ci, to, adj):
    return [city, day, date, conf, cb1, ".", prop, "FALSE", co, "FALSE", ci, "FALSE", to, adj, "", ""]


class FakeWS:
    def __init__(self, title, values):
        self.title = title
        self._values = values
        self.updated = None
        self.cleared = []
        self.id = id(self)
        self.row_count = max(len(values), 1000)
        self.col_count = len(values[0]) if values else 0

    def get_all_values(self):
        return self._values

    def update(self, range_name=None, values=None, value_input_option=None, **kw):
        self.updated = values

    def batch_clear(self, ranges):
        self.cleared = ranges
        # Behave like Sheets: A<n>:… drops the values from row n down.
        for rng in ranges:
            m = re.match(r"^[A-Z]+(\d+):", rng)
            if m:
                self._values = self._values[:int(m.group(1)) - 1]


class FakeSS:
    def __init__(self, wss):
        self._wss = wss

    def worksheets(self):
        return self._wss

    def worksheet(self, title):
        return next(w for w in self._wss if w.title == title)

    def duplicate_sheet(self, source_sheet_id=None, new_sheet_name=None):
        src = next(w for w in self._wss if id(w) == source_sheet_id or w.title == source_sheet_id)
        # duplicate copies header + data + formatting
        new = FakeWS(new_sheet_name, [list(r) for r in src._values])
        new.row_count = max(len(new._values), 1000)
        new.col_count = len(HEADER)
        self._wss.append(new)
        return new


def test_tab_cancel_window():
    """The legacy 'Julio 2026' tab holds rows from every month. Judged against the
    whole fetch range they all look cancelled -- scope to the tab's own month."""
    coverage = ("2026-08-11", "2027-02-08")
    # July is entirely behind the coverage window -> no cancellation pass at all.
    assert sync.tab_cancel_window(coverage, "2026-07") is None
    # The current month starts at the coverage floor, not the 1st.
    assert sync.tab_cancel_window(coverage, "2026-08") == ("2026-08-11", "2026-08-31")
    # A whole month inside the window keeps its own bounds (30- and 31-day months).
    assert sync.tab_cancel_window(coverage, "2026-09") == ("2026-09-01", "2026-09-30")
    assert sync.tab_cancel_window(coverage, "2026-12") == ("2026-12-01", "2026-12-31")
    # The far edge is clipped by the coverage ceiling.
    assert sync.tab_cancel_window(coverage, "2027-02") == ("2027-02-01", "2027-02-08")
    # Beyond the window entirely, and the disabled case.
    assert sync.tab_cancel_window(coverage, "2027-03") is None
    assert sync.tab_cancel_window(None, "2026-09") is None
    print("OK tab_cancel_window: scoped per month, clipped to coverage")


def test_parse_titles():
    assert sheets_client.parse_month_title("Julio 2026") == (2026, 7)
    assert sheets_client.parse_month_title("Agosto 2026") == (2026, 8)
    assert sheets_client.parse_month_title("guesty_res (1)") is None
    assert sheets_client.parse_month_title("Pivot Table 3") is None
    print("OK parse_month_title")


def test_routing(fake, existing):
    # Two reservations: one lands in August, one spans July->September.
    reservations = [
        {"confirmationCode": "HA-AUG1", "status": "confirmed", "guest": {"fullName": "Aug Guest"},
         "listing": {"nickname": "1201 N Roman V2"},
         "checkInDateLocalized": "2026-08-05", "checkOutDateLocalized": "2026-08-09"},
        {"confirmationCode": "HA-JUL1", "status": "reserved", "guest": {"fullName": "Jul Guest"},
         "listing": {"nickname": "3223 Canal"},
         "checkInDateLocalized": "2026-07-27", "checkOutDateLocalized": "2026-09-03"},
    ]
    cfg = {"sheet_id": "x", "sa_json": "{}", "worksheet": None, "template_tab": None,
           "client_id": "x", "client_secret": "y",
           "lookback": 1, "lookahead": 180, "statuses": ["confirmed"]}

    rc = sync.run(dry_run=False, reservations=reservations, cfg=cfg)
    assert rc == 0, rc

    ago, jul, res_tab = existing["2026-08"], existing["2026-07"], existing["guesty"]

    # August + July tabs were written; the non-month tab was never touched.
    assert ago.updated is not None, "August tab should have been written"
    assert jul.updated is not None, "July tab should have been written"
    assert res_tab.updated is None, "guesty_res tab must NOT be written"
    assert ago.updated[0] == HEADER, ago.updated[0]

    # September event had no tab -> auto-created 'Septiembre 2026' and written.
    sept = next((w for w in fake._wss if w.title == "Septiembre 2026"), None)
    assert sept is not None, "Septiembre 2026 should have been auto-created"
    assert sept.updated is not None, "auto-created Sept tab should have been written"
    assert sept.updated[0] == HEADER, "auto-created tab keeps the template header"

    # Regression: a header-only tab (every auto-created one) used to fall back to
    # the pipeline's 10 columns, so the body was written one column short of the
    # header and everything from 'assigned' on landed in the wrong column.
    col = {name: i for i, name in enumerate(HEADER)}
    for tab in (jul, sept):
        for body_row in tab.updated[1:]:
            assert len(body_row) == len(HEADER), (tab.title, len(body_row), body_row)
            assert body_row[col["Property"]] == "3223 Canal", (tab.title, body_row)
            assert body_row[col["Check out - Time"]] in ("", "11:00 AM"), body_row
            for cb in ("assigned", "Verified", "OUT", "IN"):
                assert body_row[col[cb]] == "FALSE", (tab.title, cb, body_row)
    print("OK routing: Aug/Jul written, guesty_res untouched, Sept auto-created + written")
    print("OK alignment: body rows are header-width with checkboxes in their own columns")


# A row as the pre-fix write left it: the pipeline's 10 columns pasted under the
# sheet's 16-column header, so everything from `assigned` on sits one column left
# and the check-out time lands in `Property`.
SHIFTED_ROW = ["New Orleans", "Wednesday", "2026-08-05", "HA-OLD", "Old Guest",
               "1201 N Roman V2", "11:00 AM", "04:00 PM", "", "", "", "", "", "", "", ""]

AUG_RESERVATION = [{
    "confirmationCode": "HA-AUG1", "status": "confirmed",
    "guest": {"fullName": "Aug Guest"}, "listing": {"nickname": "1201 N Roman V2"},
    "checkInDateLocalized": "2026-08-05", "checkOutDateLocalized": "2026-08-09",
}]


def _run_against_shifted_tab(repair: bool, dry_run: bool = False):
    ago = FakeWS("Agosto 2026", [HEADER, list(SHIFTED_ROW), list(SHIFTED_ROW)])
    fake = FakeSS([ago])
    cfg = {"sheet_id": "x", "sa_json": "{}", "worksheet": None, "template_tab": None,
           "client_id": "x", "client_secret": "y", "lookback": 1, "lookahead": 180,
           "statuses": ["confirmed"], "repair_shifted": repair}
    orig = sheets_client.open_spreadsheet
    sheets_client.open_spreadsheet = lambda sheet_id, sa_json: fake
    try:
        assert sync.run(dry_run=dry_run, reservations=AUG_RESERVATION, cfg=cfg) == 0
    finally:
        sheets_client.open_spreadsheet = orig
    return ago


def test_shifted_tab_is_skipped_without_the_flag():
    ago = _run_against_shifted_tab(repair=False)
    assert ago.updated is None, "a shifted tab must never be merged into"
    assert ago.cleared == [], "and must never be cleared without the flag"
    print("OK: shifted tab skipped untouched when SYNC_REPAIR_SHIFTED_TABS is off")


def test_shifted_tab_dry_run_does_not_clear():
    ago = _run_against_shifted_tab(repair=True, dry_run=True)
    assert ago.cleared == [], "a dry run must not clear anything"
    assert ago.updated is None, "a dry run must not write"
    assert len(ago.get_all_values()) == 3, "the shifted rows are still there"
    print("OK: repair flag + dry run previews only, sheet untouched")


def test_shifted_tab_repair_clears_and_rebuilds():
    ago = _run_against_shifted_tab(repair=True)
    # Data rows wiped from row 2 down; the header row survives.
    assert ago.cleared == ["A2:P1000"], ago.cleared
    assert ago.updated is not None, "the repaired tab should have been rewritten"
    assert ago.updated[0] == HEADER, ago.updated[0]
    assert len(ago.updated) > 1, "the month should have been rebuilt from Guesty"

    col = {name: i for i, name in enumerate(HEADER)}
    for body_row in ago.updated[1:]:
        assert len(body_row) == len(HEADER), (len(body_row), body_row)
        # The whole point: `Property` holds an address again, not a time.
        # Processing strips the listing's version marker, so "1201 N Roman V2" -> "1201 N Roman".
        assert body_row[col["Property"]] == "1201 N Roman", body_row
        assert body_row[col["Check out - Time"]] in ("", "11:00 AM"), body_row
        assert body_row[col["Guest"]] == "Aug Guest", body_row
        for cb in ("assigned", "Verified", "OUT", "IN"):
            assert body_row[col[cb]] == "FALSE", (cb, body_row)
    print("OK: shifted tab cleared, month rebuilt, columns back in their header slots")


def _summary_of(run) -> str:
    """Run `run()` with GITHUB_STEP_SUMMARY pointed at a temp file; return what it wrote."""
    fd, path = tempfile.mkstemp(suffix=".md")
    os.close(fd)
    prev = os.environ.get("GITHUB_STEP_SUMMARY")
    os.environ["GITHUB_STEP_SUMMARY"] = path
    try:
        run()
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        if prev is None:
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
        else:
            os.environ["GITHUB_STEP_SUMMARY"] = prev
        os.unlink(path)


def test_repaired_tab_is_written_without_a_blanket_highlight():
    """A rebuild writes every row, so every row is "new" and the month comes out
    entirely amber -- true, and useless: the highlight means "this changed today",
    and a thousand of them trains people to ignore it."""
    class MarkingSS(FakeSS):
        def __init__(self, wss):
            super().__init__(wss)
            self.requests = []

        def batch_update(self, body):
            self.requests.extend(body["requests"])

        def fetch_sheet_metadata(self, params=None):
            return {"sheets": [{"data": [{"rowData": []}]}]}

    ago = FakeWS("Agosto 2026", [HEADER, list(SHIFTED_ROW), list(SHIFTED_ROW)])
    fake = MarkingSS([ago])
    ago.spreadsheet = fake
    cfg = {"sheet_id": "x", "sa_json": "{}", "worksheet": None, "template_tab": None,
           "client_id": "x", "client_secret": "y", "lookback": 1, "lookahead": 180,
           "statuses": ["confirmed"], "repair_shifted": True, "state_bucket": ""}
    orig = sheets_client.open_spreadsheet
    sheets_client.open_spreadsheet = lambda sheet_id, sa_json: fake
    try:
        assert sync.run(dry_run=False, reservations=AUG_RESERVATION, cfg=cfg) == 0
    finally:
        sheets_client.open_spreadsheet = orig

    assert ago.updated is not None, "the repaired tab should have been rewritten"
    fills = [r["repeatCell"] for r in fake.requests
             if r.get("repeatCell", {}).get("fields", "") == "userEnteredFormat.backgroundColor"]
    painted = [f for f in fills
               if f["cell"]["userEnteredFormat"]["backgroundColor"] != sheets_client.NO_FILL_RGB]
    assert not painted, f"a rebuilt month was highlighted: {painted}"
    # The tickbox rule still goes on, though -- that is the whole point of a repair.
    assert any("setDataValidation" in r for r in fake.requests), fake.requests
    print("OK: a rebuilt month is written unmarked, but still gets the tickbox rule")


def test_live_run_actually_paints_strikethrough_and_highlight():
    """End to end on a LIVE run: a cancelled row must come out struck and a new row
    highlighted. Every run so far has been a dry run, which paints nothing by
    design, so this asserts the marks reach the sheet once one actually writes."""
    class MarkingSS(FakeSS):
        def __init__(self, wss):
            super().__init__(wss)
            self.requests = []

        def batch_update(self, body):
            self.requests.extend(body["requests"])

        def fetch_sheet_metadata(self, params=None):
            return {"sheets": [{"data": [{"rowData": []}]}]}   # nothing marked yet

    # One live row that Guesty still has, one it no longer does.
    live = _row("New Orleans", "Wednesday", "2026-08-05", "HA-AUG1", "Aug Guest",
                "FALSE", "1201 N Roman", "11:00 AM", "04:00 PM", "", "")
    gone = _row("New Orleans", "Thursday", "2026-08-06", "HA-DEAD", "Ghost",
                "FALSE", "3223 Canal", "11:00 AM", "", "", "")
    ago = FakeWS("Agosto 2026", [HEADER, live, gone])
    fake = MarkingSS([ago])
    ago.spreadsheet = fake

    cfg = {"sheet_id": "x", "sa_json": "{}", "worksheet": None, "template_tab": None,
           "client_id": "x", "client_secret": "y", "lookback": 400, "lookahead": 400,
           "statuses": ["confirmed"], "state_bucket": ""}
    orig = sheets_client.open_spreadsheet
    sheets_client.open_spreadsheet = lambda sheet_id, sa_json: fake
    try:
        assert sync.run(dry_run=False, reservations=AUG_RESERVATION, cfg=cfg) == 0
    finally:
        sheets_client.open_spreadsheet = orig

    strikes = [r["repeatCell"] for r in fake.requests
               if r.get("repeatCell", {}).get("fields", "").find("strikethrough") >= 0]
    fills = [r["repeatCell"] for r in fake.requests
             if r.get("repeatCell", {}).get("fields", "") == "userEnteredFormat.backgroundColor"]

    assert strikes, "nothing was struck through on a live run"
    assert any(s["cell"]["userEnteredFormat"]["textFormat"]["strikethrough"] is True
               for s in strikes), strikes
    assert fills, "nothing was highlighted on a live run"
    painted = [f for f in fills
               if f["cell"]["userEnteredFormat"]["backgroundColor"] != sheets_client.NO_FILL_RGB]
    assert painted, f"a fill was requested but it was the no-fill colour: {fills}"
    print("OK: a live run really does paint strikethrough and highlight")


def test_dry_run_paints_nothing():
    """The other half of the same fact, and the reason the sheet looks unchanged:
    a dry run computes the marks and deliberately writes none of them."""
    class MarkingSS(FakeSS):
        def __init__(self, wss):
            super().__init__(wss)
            self.requests = []

        def batch_update(self, body):
            self.requests.extend(body["requests"])

        def fetch_sheet_metadata(self, params=None):
            return {"sheets": [{"data": [{"rowData": []}]}]}

    ago = FakeWS("Agosto 2026", [HEADER,
                                 _row("New Orleans", "Thursday", "2026-08-06",
                                      "HA-DEAD", "Ghost", "FALSE", "3223 Canal",
                                      "11:00 AM", "", "", "")])
    fake = MarkingSS([ago])
    ago.spreadsheet = fake
    cfg = {"sheet_id": "x", "sa_json": "{}", "worksheet": None, "template_tab": None,
           "client_id": "x", "client_secret": "y", "lookback": 400, "lookahead": 400,
           "statuses": ["confirmed"], "state_bucket": ""}
    orig = sheets_client.open_spreadsheet
    sheets_client.open_spreadsheet = lambda sheet_id, sa_json: fake
    try:
        assert sync.run(dry_run=True, reservations=AUG_RESERVATION, cfg=cfg) == 0
    finally:
        sheets_client.open_spreadsheet = orig

    assert ago.updated is None, "a dry run wrote values"
    assert not fake.requests, f"a dry run painted marks: {fake.requests}"
    print("OK: a dry run paints nothing -- which is why the sheet looks untouched")


def test_skipped_tab_is_reported_in_the_step_summary():
    """A skipped tab writes no per-tab report, so the grand-total warning is the ONLY
    place its month shows up. Silence there reads as 'nothing to sync' -- the bug."""
    summary = _summary_of(lambda: _run_against_shifted_tab(repair=False))

    assert "[!WARNING]" in summary, summary
    assert "1 tab(s) skipped" in summary, summary
    # The month's reservations went nowhere; the count has to say so. The one
    # reservation contributes two rows -- its check-out and its check-in.
    assert "2 reservation(s) were NOT synced" in summary, summary
    assert "`Agosto 2026`" in summary, summary
    assert "Property column holds times" in summary, summary
    # ... and it must point at the way out.
    assert "repair" in summary.lower(), summary
    # The warning precedes the totals, so nobody reads the numbers first.
    assert summary.index("[!WARNING]") < summary.index("| New |"), summary
    print("OK: skipped tab surfaces as a warning above the grand total")


def test_repaired_tab_is_not_reported_as_skipped():
    """Once the tab is repaired it syncs normally -- no leftover warning."""
    summary = _summary_of(lambda: _run_against_shifted_tab(repair=True))

    assert "[!WARNING]" not in summary, summary
    assert "NOT synced" not in summary, summary
    assert "repaired" in summary.lower(), summary
    print("OK: a repaired tab raises no skipped-tab warning")


def test_grid_grows_to_fit_and_new_rows_get_checkboxes():
    """Sheets rejects a values write taller than the grid ("exceeds grid limits"),
    so a month that outgrows its tab would fail outright -- Septiembre needed 1466
    rows in a 1318-row tab. Grow first, and carry the checkbox rule onto the rows
    that were added, or those cells render the literal text FALSE."""
    import pandas as pd
    import sheets_client as sc

    class GrowWS(FakeWS):
        def __init__(self, title, values, row_count):
            super().__init__(title, values)
            self.row_count = row_count
            self.col_count = len(HEADER)
            self.added = 0

        def add_rows(self, n):
            self.added += n
            self.row_count += n

    ws = GrowWS("Agosto 2026", [HEADER], row_count=10)
    ss = RecordingSS(); ws.spreadsheet = ss
    # 25 data rows + header = 26 > the 10-row grid.
    full = pd.DataFrame([dict(zip(HEADER, ["New Orleans", "Monday", "2026-08-03",
                                           f"HM{i:08d}", "G", "FALSE", "1022 Erato",
                                           "FALSE", "11:00 AM", "FALSE", "", "FALSE",
                                           "", "", "", ""])) for i in range(25)],
                        columns=HEADER)
    marks = sc.write_dataframe(ws, full, HEADER, row_flags=["new"] * 25,
                               was_highlighted=set(), checkbox_cols={5, 7, 9, 11})

    assert ws.added == 16, ws.added            # 10 -> 26
    assert marks["rows_added"] == 16, marks
    assert len(ws.updated) == 26, len(ws.updated)

    dv = [r["setDataValidation"] for r in ss.requests if "setDataValidation" in r]
    assert dv, "the new rows must get the checkbox rule"
    for r in dv:
        assert r["rule"]["condition"]["type"] == "BOOLEAN", r
        # Never the header, and never past the data.
        assert r["range"]["startRowIndex"] >= 1, r
        assert r["range"]["endRowIndex"] <= 26, r

    # The rule now covers EVERY data row, not only the ones just added. A rebuilt
    # tab has values but no rule, so applying it only to appended rows left the
    # rest showing the literal text FALSE.
    rows_covered = set()
    for r in dv:
        rows_covered |= set(range(r["range"]["startRowIndex"], r["range"]["endRowIndex"]))
    assert rows_covered == set(range(1, 26)), sorted(rows_covered)[:5]

    covered = {c for r in dv
               for c in range(r["range"]["startColumnIndex"], r["range"]["endColumnIndex"])}
    assert covered == {5, 7, 9, 11}, covered
    assert marks["checkbox_cols"] == 4, marks
    print("OK: grid grown to fit, tickbox rule applied across every data row")


def test_grid_untouched_when_it_already_fits():
    import pandas as pd
    import sheets_client as sc

    class GrowWS(FakeWS):
        def __init__(self):
            super().__init__("Agosto 2026", [HEADER])
            self.row_count, self.col_count, self.added = 1000, len(HEADER), 0

        def add_rows(self, n):
            self.added += n

    ws = GrowWS(); ws.spreadsheet = RecordingSS()
    full = pd.DataFrame([dict(zip(HEADER, ["New Orleans", "Monday", "2026-08-03",
                                           "HM1", "G", "FALSE", "1022 Erato", "FALSE",
                                           "11:00 AM", "FALSE", "", "FALSE",
                                           "", "", "", ""]))], columns=HEADER)
    marks = sc.write_dataframe(ws, full, HEADER, row_flags=["new"],
                               was_highlighted=set(), checkbox_cols={5, 7, 9, 11})
    assert ws.added == 0 and marks["rows_added"] == 0, (ws.added, marks)
    print("OK: a tab with room to spare is not resized")


class RecordingSS:
    """Minimal spreadsheet stand-in that captures batch_update payloads."""

    def __init__(self, row_marks=None):
        self.requests = []
        self._row_marks = row_marks or []

    def batch_update(self, body):
        self.requests.extend(body["requests"])

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [{"data": [{"rowData": self._row_marks}]}]}


def _cell(strike=False, bg=None):
    fmt = {"textFormat": {"strikethrough": strike}}
    if bg:
        fmt["backgroundColor"] = bg
    return {"values": [{"effectiveFormat": fmt}]}


def test_read_row_marks():
    ws = FakeWS("Noviembre 2026", [HEADER])
    ws.spreadsheet = RecordingSS(row_marks=[
        _cell(),                                            # header row, ignored
        _cell(),                                            # data row 0: clean
        _cell(strike=True),                                 # data row 1: struck
        _cell(bg=dict(sheets_client.HIGHLIGHT_RGB)),        # data row 2: highlighted
        _cell(bg={"red": 0.8, "green": 0.9, "blue": 1.0}),  # data row 3: someone else's fill
    ])
    struck, highlighted = sheets_client.read_row_marks(ws)
    assert struck == {1}, struck
    assert highlighted == {2}, highlighted
    print("OK read_row_marks: strikethrough + our highlight recognised, other fills ignored")


def test_apply_row_marks_requests():
    ws = FakeWS("Noviembre 2026", [HEADER])
    ws.spreadsheet = RecordingSS()
    # rows 0-1 carry yesterday's highlight; row 1 is still new today, row 0 isn't.
    flags = ["", "new", "cancelled", "cancelled", "struck", "new"]
    marks = sheets_client.apply_row_marks(ws, flags, was_highlighted={0, 1},
                                          n_cols=len(HEADER))
    assert marks == {"struck": 2, "highlighted": 1, "unhighlighted": 1}, marks

    got = [(r["repeatCell"]["fields"],
            r["repeatCell"]["range"]["startRowIndex"],
            r["repeatCell"]["range"]["endRowIndex"])
           for r in ws.spreadsheet.requests]
    # Rows 2-3 strike as ONE request (contiguous); row 4 was already struck -> skipped.
    assert ("userEnteredFormat.textFormat.strikethrough", 3, 5) in got, got
    # Row 5 is newly highlighted; row 1 already was, so it isn't repainted.
    assert ("userEnteredFormat.backgroundColor", 6, 7) in got, got
    # Row 0 kept yesterday's highlight but isn't new today -> cleared.
    assert ("userEnteredFormat.backgroundColor", 1, 2) in got, got
    assert all(r["repeatCell"]["range"]["endColumnIndex"] == len(HEADER)
               for r in ws.spreadsheet.requests)
    # Only the two cosmetic properties are ever touched -- checkbox validation lives.
    assert all(set(r["repeatCell"]["cell"]["userEnteredFormat"])
               <= {"textFormat", "backgroundColor"} for r in ws.spreadsheet.requests)
    print("OK apply_row_marks: contiguous runs, no redundant repaints, formatting scoped")


if __name__ == "__main__":
    test_parse_titles()
    test_tab_cancel_window()
    test_read_row_marks()
    test_apply_row_marks_requests()
    test_shifted_tab_is_skipped_without_the_flag()
    test_shifted_tab_dry_run_does_not_clear()
    test_shifted_tab_repair_clears_and_rebuilds()
    test_grid_grows_to_fit_and_new_rows_get_checkboxes()
    test_grid_untouched_when_it_already_fits()
    test_repaired_tab_is_written_without_a_blanket_highlight()
    test_live_run_actually_paints_strikethrough_and_highlight()
    test_dry_run_paints_nothing()
    test_skipped_tab_is_reported_in_the_step_summary()
    test_repaired_tab_is_not_reported_as_skipped()

    # Build fake workbook: Agosto has one existing (unchanged) row, Julio empty-ish,
    # plus a non-month tab that must be ignored. No September tab on purpose.
    ago = FakeWS("Agosto 2026", [HEADER,
                                 _row("New Orleans", "Wednesday", "2026-08-05", "HA-OLD", "Old Guest",
                                      "TRUE", "1201 N Roman V2", "11:00 AM", "04:00 PM", "", "")])
    jul = FakeWS("Julio 2026", [HEADER])
    res = FakeWS("guesty_res (1)", [["a", "b", "c"]])
    piv = FakeWS("Pivot Table 3", [["x"]])
    fake = FakeSS([res, ago, jul, piv])

    orig = sheets_client.open_spreadsheet
    sheets_client.open_spreadsheet = lambda sheet_id, sa_json: fake
    try:
        test_routing(fake, {"2026-08": ago, "2026-07": jul, "guesty": res})
    finally:
        sheets_client.open_spreadsheet = orig

    print("\nALL MONTHLY-ROUTING TESTS PASSED")
