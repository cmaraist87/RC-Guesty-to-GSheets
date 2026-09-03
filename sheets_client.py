"""
Google Sheets I/O via a service account.

Read the current sheet into a DataFrame (with pandas-style de-duplicated headers so
the merge logic works), then write the full corrected sheet back starting at A1.

Writing only cell VALUES (not formatting) preserves the sheet's checkbox
data-validation -- exactly like the manual "paste over A1" step. Trailing rows that
are no longer used are value-cleared (checkbox formatting stays), which also removes
any superseded rows the manual paste would have left behind.

Credentials: pass the service-account key either as a file path or as the raw JSON
string (GitHub Actions stores it as a secret) via GOOGLE_SA_JSON.
"""
from __future__ import annotations

import json
import re
import os

import pandas as pd

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Visual diff for the daily run: rows written today are highlighted, rows whose
# reservation was cancelled are struck through (and left in place, not deleted).
HIGHLIGHT_RGB = {"red": 1.0, "green": 0.898, "blue": 0.6}   # soft amber
NO_FILL_RGB = {"red": 1.0, "green": 1.0, "blue": 1.0}
_HL_TOLERANCE = 0.04

_HIGHLIGHT_FLAGS = ("new", "updated")
# Struck for the first time this run. "moved" is a reassignment (the booking turned
# up at another listing/date); the row's slot is just as dead, so it strikes too.
_STRIKE_NEW_FLAGS = ("cancelled", "moved")
_STRIKE_OLD_FLAG = "struck"      # already struck by an earlier run
# Every flag that means "this row must carry a line through it when the run ends".
# Stated as an absolute set because the marks are now painted absolutely: a row
# struck by an earlier run has to be RE-asserted at whatever position it now sits
# at, not skipped on the assumption its old line is still underneath it.
_STRIKE_FLAGS = _STRIKE_NEW_FLAGS + (_STRIKE_OLD_FLAG,)


def service_account_info(sa_json: str) -> dict:
    """Parse GOOGLE_SA_JSON, which may be raw JSON or a path to a key file.

    Public because the same key now authenticates two different services -- Sheets
    here, Cloud Storage for the shared token cache -- and they need different
    scopes, so only the parsing is shared.
    """
    sa_json = (sa_json or "").strip()
    if not sa_json:
        raise RuntimeError(chr(10).join(lines)) from e
        return json.loads(sa_json)
    if os.path.exists(sa_json):
        with open(sa_json, encoding="utf-8") as fh:
            return json.load(fh)
    raise RuntimeError("GOOGLE_SA_JSON is neither valid JSON nor an existing file path.")


def _load_credentials(sa_json: str):
    from google.oauth2.service_account import Credentials

    return Credentials.from_service_account_info(service_account_info(sa_json),
                                                 scopes=SCOPES)


SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


def normalise_sheet_id(value: str) -> str:
    """Accept the whole URL as well as the bare id.

    Pasting the URL is the obvious thing to do, and the failure it used to cause --
    a bare 404 from deep inside gspread -- looks nothing like "you pasted too much".
    """
    value = (value or "").strip().strip('"').strip("'")
    found = SHEET_ID_RE.search(value)
    return found.group(1) if found else value


def open_spreadsheet(sheet_id: str, sa_json: str):
    """Open the spreadsheet (workbook) by ID, or by the URL it appears in."""
    import gspread

    if not sheet_id:
        raise RuntimeError("Missing SHEET_ID (the spreadsheet's ID from its URL).")
    key = normalise_sheet_id(sheet_id)
    gc = gspread.authorize(_load_credentials(sa_json))
    try:
        return gc.open_by_key(key)
    except gspread.exceptions.SpreadsheetNotFound as e:
        # Google answers 404 both for "no such sheet" and for "you may not see it",
        # deliberately, so the raw error cannot tell them apart. Say what the two
        # possibilities actually are, and show what this key CAN see -- which
        # usually makes the answer obvious.
        who = ""
        try:
            who = service_account_info(sa_json).get("client_email", "")
        except Exception:  # noqa: BLE001
            pass
        lines = [f"Could not open spreadsheet {key!r}.",
                 "Google returns the same 404 for 'no such sheet' and 'this account "
                 "cannot see it', so it is one of:",
                 f"  1. The id is wrong. It is the part of the URL between "
                 f"/spreadsheets/d/ and /edit ({len(key)} characters given; "
                 f"a real one is usually 44).",
                 f"  2. The sheet is not shared with {who or 'the service account'}."]
        try:
            visible = gc.list_spreadsheet_files()
            if visible:
                lines.append("This account CAN see:")
                for f in visible[:10]:
                    lines.append(f"  {f.get('id')}  {f.get('name')!r}")
            else:
                lines.append("This account can see no spreadsheets at all, so it is "
                             "almost certainly (2): share the sheet with it as Editor.")
        except Exception:  # noqa: BLE001 - diagnosis is best-effort
            pass
        raise RuntimeError(chr(10).join(lines)) from e


def get_worksheet(sheet_id: str, worksheet_name: str | None, sa_json: str):
    """Open a worksheet by spreadsheet ID and tab name (or first tab if None)."""
    ss = open_spreadsheet(sheet_id, sa_json)
    return ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1


# Spanish month names -> month number (the schedule tabs are named e.g. "Julio 2026").
# Includes English names too, so mixed naming still resolves.
_MONTH_NAMES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_month_title(title: str) -> tuple[int, int] | None:
    """'Julio 2026' -> (2026, 7). Returns None for non-month tabs (guesty_res, pivots)."""
    import re

    m = re.match(r"^\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\.?\s+(\d{4})\s*$", str(title).strip())
    if not m:
        return None
    mon = _MONTH_NAMES.get(m.group(1).lower())
    if not mon:
        return None
    return (int(m.group(2)), mon)


def month_worksheets(ss) -> dict:
    """Map (year, month) -> worksheet for every month-named tab in the workbook."""
    out = {}
    for ws in ss.worksheets():
        ym = parse_month_title(ws.title)
        if ym:
            out[ym] = ws
    return out


def create_month_tab(ss, template_title: str, new_title: str):
    """
    Create a new month tab by duplicating `template_title` (which copies its checkbox
    data-validation and all formatting), then value-clearing the copied data rows so the
    new tab starts empty but keeps the header row + checkbox formatting. Returns the new ws.
    """
    src = ss.worksheet(template_title)
    new_ws = ss.duplicate_sheet(source_sheet_id=src.id, new_sheet_name=new_title)
    # duplicate_sheet copies the template's data AND its strikethrough/highlight
    # marks; clear both, or the new month opens pre-painted with last month's diff.
    clear_data_rows(new_ws)
    return new_ws


def clear_data_rows(ws) -> int:
    """
    Value-clear row 2 downwards and reset the marks this sync paints, leaving the
    header row and every piece of formatting (checkbox data-validation, column
    widths, borders, fonts) untouched. Returns the number of data rows cleared.

    Used both to empty a freshly duplicated month tab and to repair a tab written
    with the old shifted layout, which has to start from a clean slate because its
    rows can't be matched against anything the pipeline now produces.
    """
    n_rows, n_cols = ws.row_count, ws.col_count
    if n_rows <= 1 or n_cols < 1:
        return 0
    ws.batch_clear([f"A2:{_col_letter(n_cols)}{n_rows}"])
    _apply_requests(ws, [
        _fmt_request(ws, 1, n_rows, n_cols,
                     {"textFormat": {"strikethrough": False},
                      "backgroundColor": dict(NO_FILL_RGB)},
                     "userEnteredFormat.textFormat.strikethrough,"
                     "userEnteredFormat.backgroundColor"),
    ])
    return n_rows - 1


def _dedupe_headers(header: list[str]) -> list[str]:
    """Mimic pandas read_csv: blanks -> 'Unnamed: i', duplicates -> name, name.1, ..."""
    out, seen = [], {}
    for i, h in enumerate(header):
        name = str(h).strip()
        if name == "":
            name = f"Unnamed: {i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def read_as_dataframe(ws) -> tuple[pd.DataFrame, list[str]]:
    """Return (df with de-duplicated string columns, original raw header row)."""
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(), []
    header_raw = values[0]
    cols = _dedupe_headers(header_raw)
    rows = values[1:]
    # pad/truncate each row to header width
    width = len(cols)
    norm_rows = [(r + [""] * width)[:width] for r in rows]
    df = pd.DataFrame(norm_rows, columns=cols).fillna("").astype(str)
    return df, header_raw


def _close(a, b) -> bool:
    return a is not None and abs(a - b) <= _HL_TOLERANCE


def _is_highlight(bg) -> bool:
    if not isinstance(bg, dict):
        return False
    return (_close(bg.get("red", 1.0), HIGHLIGHT_RGB["red"])
            and _close(bg.get("green", 1.0), HIGHLIGHT_RGB["green"])
            and _close(bg.get("blue", 1.0), HIGHLIGHT_RGB["blue"]))


def read_row_marks(ws) -> tuple[set[int], set[int]]:
    """
    Read back the marks this sync painted on a previous run.

    Returns (struck, highlighted) as 0-based DATA-row positions (data row 0 is
    grid row 2). Only column A is sampled -- marks are applied to whole rows.
    Returns empty sets if the workbook can't be queried (offline tests, fakes).
    """
    ss = getattr(ws, "spreadsheet", None)
    if ss is None or not hasattr(ss, "fetch_sheet_metadata"):
        return set(), set()
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A:A"],
        "fields": "sheets(data(rowData(values(effectiveFormat("
                  "backgroundColor,textFormat/strikethrough)))))",
    }
    try:
        meta = ss.fetch_sheet_metadata(params)
    except Exception as e:  # noqa: BLE001 - marks are cosmetic; never fail the sync
        print(f"   (could not read existing row marks on '{ws.title}': {e})")
        return set(), set()

    sheets = meta.get("sheets") or []
    data = ((sheets[0].get("data") or [{}])[0] if sheets else {})
    row_data = data.get("rowData") or []

    struck, highlighted = set(), set()
    for i, rd in enumerate(row_data[1:]):  # skip the header row
        values = rd.get("values") or []
        if not values:
            continue
        fmt = values[0].get("effectiveFormat") or {}
        if (fmt.get("textFormat") or {}).get("strikethrough"):
            struck.add(i)
        if _is_highlight(fmt.get("backgroundColor")):
            highlighted.add(i)
    return struck, highlighted


def read_checkbox_columns(ws, probe_rows: int = 25) -> set[int]:
    """
    0-based column indices whose cells carry Sheets' checkbox (BOOLEAN) data
    validation -- read from the tab itself instead of guessed from header names or
    the TRUE/FALSE values that happen to be sitting there.

    A band of data rows is sampled rather than one: a row's validation can be
    cleared by hand, and a freshly duplicated tab may only carry the template's
    validation part of the way down. A column counts if ANY probed row has it.

    Returns an empty set when the workbook can't be queried (offline tests, fakes)
    or when the tab genuinely has no checkboxes -- either way the caller falls back
    to its own heuristic, so this can only ever improve the guess.
    """
    ss = getattr(ws, "spreadsheet", None)
    if ss is None or not hasattr(ss, "fetch_sheet_metadata"):
        return set()
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!2:{probe_rows + 1}"],
        "fields": "sheets(data(rowData(values(dataValidation/condition/type))))",
    }
    try:
        meta = ss.fetch_sheet_metadata(params)
    except Exception as e:  # noqa: BLE001 - fall back to the heuristic, never fail
        print(f"   (could not read checkbox validation on '{ws.title}': {e})")
        return set()

    sheets = meta.get("sheets") or []
    data = ((sheets[0].get("data") or [{}])[0] if sheets else {})
    cols = set()
    for rd in data.get("rowData") or []:
        for j, cell in enumerate(rd.get("values") or []):
            cond = (cell.get("dataValidation") or {}).get("condition") or {}
            if cond.get("type") == "BOOLEAN":
                cols.add(j)
    return cols


def _runs(indices) -> list[tuple[int, int]]:
    """[1,2,3,7,8] -> [(1,3), (7,8)] -- contiguous blocks, so one request each."""
    out: list[list[int]] = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [(a, b) for a, b in out]


def _fmt_request(ws, start_row: int, end_row: int, n_cols: int,
                 cell_format: dict, fields: str) -> dict:
    """repeatCell over grid rows [start_row, end_row) -- 0-based, header is row 0."""
    return {"repeatCell": {
        "range": {"sheetId": ws.id,
                  "startRowIndex": start_row, "endRowIndex": end_row,
                  "startColumnIndex": 0, "endColumnIndex": n_cols},
        "cell": {"userEnteredFormat": cell_format},
        "fields": fields,
    }}


def _apply_requests(ws, requests: list[dict]) -> None:
    if not requests:
        return
    ss = getattr(ws, "spreadsheet", None)
    if ss is None or not hasattr(ss, "batch_update"):
        return  # offline / fake worksheet: nothing to paint
    try:
        ss.batch_update({"requests": requests})
    except Exception as e:  # noqa: BLE001 - cosmetic; the values are already written
        print(f"   (could not apply row formatting on '{ws.title}': {e})")


def apply_row_marks(ws, row_flags: list[str], prior_highlight: set,
                    prior_struck: set, n_cols: int,
                    clear_from: int | None = None) -> dict:
    """
    Paint the daily diff, stating the marks ABSOLUTELY rather than incrementally.

    Both sets given are GRID data-row positions read back off the sheet before this
    run wrote to it (see read_row_marks), and `row_flags` is indexed by the same
    grid positions, because the new block is written from row 2 down. So the work is
    a plain set difference: paint what should be marked and isn't, clear what is
    marked and shouldn't be.

    That symmetry is the whole point. The previous version only ever ADDED
    strikethrough -- a row already struck was flagged "struck" and deliberately left
    alone. But an updated reservation is deleted from its row and re-appended at the
    bottom, so every row below it slides up one, while the strikethrough stays on the
    grid position it was painted on. The cancelled row slid out from under its own
    line and a live booking inherited it. Worse, the next run reads that line back,
    concludes the live booking was already cancelled, excludes it from matching, and
    re-adds it as new -- one duplicate per row, per run, compounding.

    Highlighting had the same flaw and survived only by luck: the stale amber sat in
    the tail of the block, which is exactly where the run's own appends land and get
    painted over. Stating it absolutely means it no longer depends on that.

    Only `strikethrough` and `backgroundColor` are touched, so checkbox validation,
    borders, fonts and column widths all survive.

    NOTE: this makes the sync the sole owner of both marks. A strikethrough or an
    amber fill applied by hand on a data row will be cleared on the next run, because
    nothing distinguishes it from a stale mark this code left behind. Any other fill
    colour is ignored and preserved (see _is_highlight).

    row_flags       : one flag per data row of the freshly written block.
    prior_highlight : data-row positions carrying OUR amber before this write.
    prior_struck    : data-row positions carrying a strikethrough before this write.
    clear_from      : first data row of the trailing region being value-cleared;
                      its marks are reset so no ghost formatting is left behind.
    """
    n = len(row_flags)
    should_strike = {i for i, f in enumerate(row_flags) if f in _STRIKE_FLAGS}
    should_light = {i for i, f in enumerate(row_flags) if f in _HIGHLIGHT_FLAGS}
    # Marks beyond the new block are the trailing region's problem (clear_from).
    have_strike = {i for i in prior_struck if i < n}
    have_light = {i for i in prior_highlight if i < n}

    strike_on = sorted(should_strike - have_strike)
    strike_off = sorted(have_strike - should_strike)
    highlight_on = sorted(should_light - have_light)
    highlight_off = sorted(have_light - should_light)

    requests = []
    for rows, cell_format, fields in (
        (strike_on, {"textFormat": {"strikethrough": True}},
         "userEnteredFormat.textFormat.strikethrough"),
        (strike_off, {"textFormat": {"strikethrough": False}},
         "userEnteredFormat.textFormat.strikethrough"),
        (highlight_on, {"backgroundColor": dict(HIGHLIGHT_RGB)},
         "userEnteredFormat.backgroundColor"),
        (highlight_off, {"backgroundColor": dict(NO_FILL_RGB)},
         "userEnteredFormat.backgroundColor"),
    ):
        for a, b in _runs(rows):
            requests.append(_fmt_request(ws, a + 1, b + 2, n_cols, cell_format, fields))

    if clear_from is not None and clear_from > n:
        requests.append(_fmt_request(
            ws, n + 1, clear_from + 1, n_cols,
            {"textFormat": {"strikethrough": False}, "backgroundColor": dict(NO_FILL_RGB)},
            "userEnteredFormat.textFormat.strikethrough,userEnteredFormat.backgroundColor"))

    _apply_requests(ws, requests)
    return {"struck": len(strike_on), "unstruck": len(strike_off),
            "struck_total": len(should_strike),
            "highlighted": len(highlight_on), "unhighlighted": len(highlight_off)}


def ensure_grid(ws, n_rows: int, n_cols: int, checkbox_cols=()) -> int:
    """
    Grow the tab so an `n_rows` x `n_cols` block fits, and carry the checkbox
    data-validation into the rows that were just created. Returns rows added.

    Sheets does NOT auto-expand for a values write: a block taller than the grid is
    rejected outright ("exceeds grid limits"), so a month that outgrows its tab would
    fail the whole write rather than truncate. Rebuilding Septiembre needed 1466 rows
    in a 1318-row tab.

    Rows added this way start with no data validation, so a checkbox column would
    render the literal text FALSE. `checkbox_cols` (0-based indices, from
    read_checkbox_columns) get the BOOLEAN rule extended over the new range.
    """
    have_rows = int(getattr(ws, "row_count", 0) or 0)
    have_cols = int(getattr(ws, "col_count", 0) or 0)

    if n_cols > have_cols and hasattr(ws, "add_cols"):
        ws.add_cols(n_cols - have_cols)
    if n_rows <= have_rows or not hasattr(ws, "add_rows"):
        return 0

    added = n_rows - have_rows
    ws.add_rows(added)
    apply_checkbox_validation(ws, checkbox_cols, have_rows, n_rows)
    return added


def apply_checkbox_validation(ws, columns, first_row: int, last_row: int) -> int:
    """Put the tickbox rule on `columns` over data rows [first_row, last_row).

    Rows are 0-based grid rows, so row 1 is the first data row.

    Writing TRUE/FALSE only gets you a tickbox if the cell carries BOOLEAN data
    validation. The sync deliberately writes values and not formatting, which is
    what protects your column widths and borders -- but it also means a tab that
    never had the rule shows the literal text FALSE forever. Rebuilt tabs land in
    exactly that state, so the rule is (re)applied on every write. setDataValidation
    is idempotent, so a tab that already has it is unaffected.
    """
    runs = _runs(sorted(columns))
    if not runs or last_row <= first_row:
        return 0
    _apply_requests(ws, [
        {"setDataValidation": {
            "range": {"sheetId": ws.id,
                      "startRowIndex": first_row, "endRowIndex": last_row,
                      "startColumnIndex": a, "endColumnIndex": b + 1},
            "rule": {"condition": {"type": "BOOLEAN"},
                     "strict": True, "showCustomUi": True}}}
        for a, b in runs
    ])
    return sum(b - a + 1 for a, b in runs)


def write_dataframe(ws, full: pd.DataFrame, header_raw: list[str],
                    row_flags: list[str] | None = None,
                    prior_highlight: set | None = None,
                    prior_struck: set | None = None,
                    checkbox_cols=()) -> dict:
    """
    Write `full` starting at A1, keeping the sheet's ORIGINAL header row intact and
    value-clearing any rows below the new data (checkbox formatting preserved).

    With `row_flags`, also repaint the daily diff (see apply_row_marks).
    """
    prev_row_count = len(ws.get_all_values())

    body = full.astype(str).values.tolist()
    matrix = [list(header_raw)] + body
    new_row_count = len(matrix)
    n_cols = max(len(header_raw), full.shape[1])

    grew = ensure_grid(ws, new_row_count, n_cols, checkbox_cols)
    # Every data row, not just any that were added: a rebuilt tab has values but
    # no rule, which is why TRUE/FALSE was showing as text instead of a tickbox.
    boxed = apply_checkbox_validation(ws, checkbox_cols, 1, new_row_count)

    ws.update(
        range_name="A1",
        values=matrix,
        value_input_option="USER_ENTERED",  # so TRUE/FALSE become checkboxes, dates parse
    )

    # Value-clear leftover rows below the freshly written block (keeps formatting).
    if prev_row_count > new_row_count:
        ws.batch_clear([f"A{new_row_count + 1}:{_col_letter(n_cols)}{prev_row_count}"])

    if row_flags is None:
        return {"rows_added": grew, "checkbox_cols": boxed}
    marks = apply_row_marks(ws, row_flags, prior_highlight or set(),
                            prior_struck or set(), n_cols,
                            clear_from=max(prev_row_count - 1, len(row_flags)))
    marks["rows_added"] = grew
    marks["checkbox_cols"] = boxed
    return marks


def _col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
