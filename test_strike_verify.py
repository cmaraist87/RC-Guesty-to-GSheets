"""A strike the sheet did not take must be repainted before the run ends.

Run: python test_strike_verify.py

On 2026-09-20 the live run asked Octubre for 60 strikes and the sheet read back
47; Septiembre asked for 60 and read back 50. Every smaller tab matched exactly.
The misses were scattered through the tab, not pooled at the end, and a probe of
every column on every row found no row struck on part of its width -- so the
lines genuinely were not there.

By then the read, the request arithmetic, the ordering inside the batch and the
possibility of a later write had all been eliminated by measurement. What was
left was the batch itself not always applying in full. This file does not try to
say why; it pins the behaviour that makes the sync survive it either way.
"""
import sheets_client
from sheets_client import apply_row_marks

N_COLS = 14


class Grid:
    """A worksheet that applies only SOME of the strikes it is asked for.

    `drop` is called with the batch number (1, 2, 3...) and the data row, and
    returns True to swallow that request. That is the whole point: a sheet that
    quietly ignores part of a batch is exactly what production looks like.
    """

    def __init__(self, n_rows, drop=lambda batch, row: False, struck=(),
                 hidden=()):
        self.title = "Octubre 2026"
        self.id = 1
        self.row_count, self.col_count = n_rows + 50, N_COLS
        self.n_rows = n_rows
        self.struck = set(struck)
        self.spreadsheet = self
        self.drop = drop
        self.batches = 0
        self.reads = 0
        # Data rows a filter is hiding. Sheets accepts a repeatCell over one and
        # does not apply it -- which is the whole bug, so the fake does the same.
        self.hidden = set(hidden)
        self.has_filter = bool(hidden)

    def batch_update(self, body):
        self.batches += 1
        replies = []
        for req in body.get("requests", []):
            replies.append({})
            if "clearBasicFilter" in req:
                self.has_filter = False
                self.hidden = set()      # nothing is hidden once the filter goes
                continue
            rc = req.get("repeatCell")
            if not rc or "strikethrough" not in rc.get("fields", ""):
                continue
            on = rc["cell"]["userEnteredFormat"]["textFormat"]["strikethrough"]
            rng = rc["range"]
            for grid_row in range(rng["startRowIndex"], rng["endRowIndex"]):
                data_row = grid_row - 1
                if data_row < 0 or self.drop(self.batches, data_row):
                    continue
                if data_row in self.hidden:
                    continue             # accepted, replied to, silently ignored
                self.struck.add(data_row) if on else self.struck.discard(data_row)
        return {"replies": replies}

    def fetch_sheet_metadata(self, params):
        if "basicFilter" in (params or {}).get("fields", ""):
            sh = {"properties": {"sheetId": self.id}}
            if self.has_filter:
                sh["basicFilter"] = {"range": {"sheetId": self.id}}
            return {"sheets": [sh]}
        return self._grid_metadata()

    def _grid_metadata(self):
        self.reads += 1
        rows = [{"values": [{} for _ in range(N_COLS)]}]          # header
        for i in range(self.n_rows):
            fmt = {"textFormat": {"strikethrough": True}} if i in self.struck else {}
            rows.append({"values": [{"effectiveFormat": fmt}
                                    for _ in range(N_COLS)]})
        return {"sheets": [{"data": [{"rowData": rows}]}]}


def flags(n, cancelled):
    return ["cancelled" if i in cancelled else "" for i in range(n)]


def test_a_batch_that_half_applies_is_repaired():
    """The production shape: some of the strikes asked for simply do not appear."""
    want = {8, 12, 46, 118, 531}
    # The first batch swallows three of the five. A second one gets them.
    grid = Grid(600, drop=lambda batch, row: batch == 1 and row in {8, 12, 46})
    out = apply_row_marks(grid, flags(600, want), set(), set(), N_COLS)

    assert grid.struck == want, sorted(grid.struck)
    passes = out["verify"]["passes"]
    assert passes[0]["missing"] == 3, passes
    assert passes[-1]["missing"] == 0 and passes[-1]["extra"] == 0, passes
    print(f"OK: 3 strikes the sheet swallowed were repainted ({len(passes)} read(s))")


def test_a_lift_that_does_not_stick_is_repeated():
    """The other half of absolute painting: a line that should be GONE."""
    grid = Grid(600, drop=lambda batch, row: batch == 1 and row in {20, 21},
                struck={20, 21, 99})
    out = apply_row_marks(grid, flags(600, {99}), set(), {20, 21, 99}, N_COLS)

    assert grid.struck == {99}, sorted(grid.struck)
    assert out["verify"]["passes"][0]["extra"] == 2, out["verify"]
    assert out["verify"]["passes"][-1]["extra"] == 0, out["verify"]
    print("OK: two lines that refused to lift were cleared on the repair pass")


def test_a_batch_that_lands_costs_one_read_and_no_repair():
    """A normal morning must not pay for the guard beyond a single read back."""
    want = {3, 4, 5, 200}
    grid = Grid(400)
    out = apply_row_marks(grid, flags(400, want), set(), set(), N_COLS)

    assert grid.struck == want, sorted(grid.struck)
    assert grid.batches == 1, f"repaired a sheet that was already right: {grid.batches}"
    assert grid.reads == 1, grid.reads
    assert out["verify"]["passes"] == [{"pass": 0, "missing": 0, "extra": 0,
                                        "missing_rows": [], "extra_rows": []}]
    print("OK: a batch that landed is verified once and not repainted")


def test_a_sheet_that_never_takes_the_strike_gives_up_and_says_so():
    """It must not loop, and it must not take the run down with it."""
    grid = Grid(300, drop=lambda batch, row: True)
    out = apply_row_marks(grid, flags(300, {10, 11}), set(), set(), N_COLS)

    assert grid.struck == set(), grid.struck
    assert out["verify"]["passes"][-1]["missing"] == 2, out["verify"]
    assert grid.batches == 3, f"expected 1 paint + 2 repairs, got {grid.batches}"
    print("OK: a sheet that will not take the mark is reported, not retried forever")


def test_the_reply_count_is_reported():
    """A batch answered short is the one thing the API itself can tell us."""
    grid = Grid(300)
    out = apply_row_marks(grid, flags(300, {10, 40}), set(), set(), N_COLS)
    assert out["requests_sent"] == out["requests_replied"] == 2, out
    print("OK: requests sent and replied are both carried back to the log")


def test_an_offline_worksheet_still_works():
    """The fakes in the other suites have no metadata call; they must not break."""
    class Bare:
        title, id, row_count, col_count = "X", 1, 100, N_COLS
        spreadsheet = None

    out = apply_row_marks(Bare(), flags(10, {1}), set(), set(), N_COLS)
    assert out["verify"] == {}, out["verify"]
    print("OK: a worksheet with no API behind it verifies nothing and raises nothing")


def test_a_filter_hiding_rows_is_cleared_before_the_marks():
    """The 2026-09-20 root cause, pinned.

    Sheets accepts a repeatCell over a filter-hidden row, replies to it, and
    leaves the row alone. Octubre asked for 66 strikes and carried 47; all 26
    rows that refused were hidden, none outside the hidden set failed.
    """
    want = {8, 12, 46, 531}
    grid = Grid(600, hidden={8, 46})
    out = apply_row_marks(grid, flags(600, want), set(), set(), N_COLS)

    assert grid.struck == want, ("a hidden row must still end up struck",
                                 sorted(grid.struck))
    assert not grid.has_filter, "the filter should have been cleared"
    assert out["verify"]["passes"][-1]["missing"] == 0, out["verify"]
    print("OK: the filter comes off first, so hidden rows take the mark")


def test_the_clear_is_ordered_ahead_of_every_mark():
    """Order is the whole point: clearing after the paint would fix nothing."""
    seen = []
    grid = Grid(300, hidden={5})
    real = grid.batch_update

    def spy(body):
        seen.append([next(iter(r)) for r in body.get("requests", [])])
        return real(body)

    grid.batch_update = spy
    apply_row_marks(grid, flags(300, {5, 200}), set(), set(), N_COLS)
    assert seen[0][0] == "clearBasicFilter", seen[0][:3]
    assert "repeatCell" in seen[0][1:], seen[0][:3]
    print("OK: clearBasicFilter is the first request in the same batch")


def test_a_tab_with_no_filter_is_left_alone():
    """No filter, no clearBasicFilter -- the sync does not touch what is not there."""
    seen = []
    grid = Grid(300)
    real = grid.batch_update
    grid.batch_update = lambda b: (seen.append([next(iter(r)) for r in b["requests"]])
                                   or real(b))
    apply_row_marks(grid, flags(300, {7}), set(), set(), N_COLS)
    assert all("clearBasicFilter" not in reqs for reqs in seen), seen
    print("OK: a tab with no filter is left exactly as it was")


if __name__ == "__main__":
    test_a_batch_that_half_applies_is_repaired()
    test_a_lift_that_does_not_stick_is_repeated()
    test_a_batch_that_lands_costs_one_read_and_no_repair()
    test_a_sheet_that_never_takes_the_strike_gives_up_and_says_so()
    test_the_reply_count_is_reported()
    test_an_offline_worksheet_still_works()
    test_a_filter_hiding_rows_is_cleared_before_the_marks()
    test_the_clear_is_ordered_ahead_of_every_mark()
    test_a_tab_with_no_filter_is_left_alone()
    print("\nALL STRIKE VERIFY TESTS PASSED")
