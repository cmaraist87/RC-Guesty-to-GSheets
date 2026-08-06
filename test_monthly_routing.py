"""
Offline test of per-month tab routing (no network) using a mocked spreadsheet.

Run: python test_monthly_routing.py
"""
import sheets_client
import sync

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", ".", "Property",
          "FALSE", "Check out - Time", "FALSE", "Check-in Time", "FALSE",
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
    print("OK routing: Aug/Jul written, guesty_res untouched, Sept auto-created + written")


if __name__ == "__main__":
    test_parse_titles()

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
