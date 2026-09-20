"""Is the strike actually on the row, or only on part of it?

    python strike_probe.py --tab "Octubre 2026" [--rows 9,10,11,14,15,47,48,49]

STRICTLY READ-ONLY.

read_row_marks decides a row is struck by looking at column A alone. Every other
explanation for the leak has been eliminated by measurement:

  * the mark batch is the last write of the run (nothing repaints after it)
  * batchUpdate is atomic, and no request in it can clear a row that an earlier
    request in the same batch struck
  * marks_audit showed the read returns the tab's full height

Yet the 2026-09-20 20:40 run asked for 57 strikes on Octubre and the sheet read
back 47, with the misses scattered through the tab rather than pooled at the end.

If a row carries the line on columns B..H but not on A, it IS struck on screen and
reads back clean -- which is exactly the shape of the loss. This asks the API for
strikethrough on EVERY column, so a partial row cannot hide.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import _col_letter, month_worksheets, open_spreadsheet, read_as_dataframe, read_row_marks
from sync import load_config


def per_column_strikes(ws, n_cols: int) -> dict[int, set[int]]:
    """{data row -> set of column indices carrying a strikethrough}."""
    ss = ws.spreadsheet
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A:{_col_letter(n_cols)}"],
        "fields": "sheets(data(rowData(values(effectiveFormat/textFormat/strikethrough))))",
    }
    meta = ss.fetch_sheet_metadata(params)
    sheets = meta.get("sheets") or []
    data = ((sheets[0].get("data") or [{}])[0] if sheets else {})
    out = {}
    for i, rd in enumerate((data.get("rowData") or [])[1:]):  # skip header
        cols = set()
        for c, v in enumerate(rd.get("values") or []):
            if ((v.get("effectiveFormat") or {}).get("textFormat") or {}).get("strikethrough"):
                cols.add(c)
        if cols:
            out[i] = cols
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="Octubre 2026")
    ap.add_argument("--rows", default="", help="grid rows to dump in full")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    ws = next((w for k, w in tabs.items() if w.title == args.tab), None)
    if ws is None:
        print(f"no tab named {args.tab!r}; have: {sorted(w.title for w in tabs.values())}")
        return 2

    rows, header = read_as_dataframe(ws)
    n_cols = max(len(header), 1)
    print(f"{ws.title}: {len(rows)} data rows, {len(header)} header columns, "
          f"grid is {ws.row_count} x {ws.col_count}")

    struck_a, _hl, _ac = read_row_marks(ws)
    by_col = per_column_strikes(ws, n_cols)
    any_col = set(by_col)

    print(f"  read_row_marks (column A only) : {len(struck_a)} rows")
    print(f"  struck on ANY column           : {len(any_col)} rows")
    full = {i for i, cs in by_col.items() if len(cs) >= n_cols}
    print(f"  struck on ALL {n_cols} columns         : {len(full)} rows")

    partial = sorted(any_col - full)
    print(f"  PARTIAL rows (some columns only): {len(partial)}")
    for i in partial[:40]:
        cs = sorted(by_col[i])
        r = rows.iloc[i] if i < len(rows) else None
        who = "" if r is None else (f"{str(r.get('Date',''))[:10]} "
                                    f"{str(r.get('Property','')).strip()} "
                                    f"{str(r.get('Confirmation Code','')).strip()}")
        print(f"    grid {i + 2:<6} cols {cs}  {who}")

    hidden = sorted(any_col - struck_a)
    print(f"  struck on screen but INVISIBLE to read_row_marks: {len(hidden)}")
    for i in hidden[:40]:
        print(f"    grid {i + 2:<6} cols {sorted(by_col[i])}")

    want = [int(x) for x in args.rows.split(",") if x.strip().isdigit()]
    if want:
        print("\n  requested rows in detail (grid row -> columns struck):")
        for g in want:
            i = g - 2
            r = rows.iloc[i] if 0 <= i < len(rows) else None
            who = "" if r is None else (f"{str(r.get('Date',''))[:10]} "
                                        f"{str(r.get('Property','')).strip()} "
                                        f"{str(r.get('Confirmation Code','')).strip()}")
            print(f"    grid {g:<6} {sorted(by_col.get(i, ())) or 'none'}   {who}")
    print("\n  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
