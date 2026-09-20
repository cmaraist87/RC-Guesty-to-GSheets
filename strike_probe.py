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


def _strike(fmt) -> bool:
    return bool(((fmt or {}).get("textFormat") or {}).get("strikethrough"))


def read_grid(ws, n_cols: int):
    """(per-column effective strikes, per-column user-entered strikes, rule count).

    Both formats, because they answer different questions. `userEnteredFormat` is
    what we wrote; `effectiveFormat` is what the cell ends up with once the sheet's
    own rules have had their say. If a row carries the line in the first and not
    the second, our write landed and something on the sheet is overriding it.
    """
    ss = ws.spreadsheet
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A:{_col_letter(n_cols)}"],
        "fields": "sheets(conditionalFormats,data(rowData(values("
                  "effectiveFormat/textFormat/strikethrough,"
                  "userEnteredFormat/textFormat/strikethrough))))",
    }
    meta = ss.fetch_sheet_metadata(params)
    sheets = meta.get("sheets") or []
    sheet0 = sheets[0] if sheets else {}
    data = ((sheet0.get("data") or [{}])[0])
    eff, user = {}, {}
    for i, rd in enumerate((data.get("rowData") or [])[1:]):  # skip header
        e = {c for c, v in enumerate(rd.get("values") or [])
             if _strike(v.get("effectiveFormat"))}
        u = {c for c, v in enumerate(rd.get("values") or [])
             if _strike(v.get("userEnteredFormat"))}
        if e:
            eff[i] = e
        if u:
            user[i] = u
    return eff, user, (sheet0.get("conditionalFormats") or [])


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
    by_col, by_col_user, rules = read_grid(ws, n_cols)
    any_col = set(by_col)

    print(f"  conditional format rules on this tab: {len(rules)}")
    for r in rules[:10]:
        rngs = ";".join(f"r{x.get('startRowIndex')}-{x.get('endRowIndex')}"
                        f"c{x.get('startColumnIndex')}-{x.get('endColumnIndex')}"
                        for x in (r.get("ranges") or []))
        print(f"    {rngs}  {list((r.get('booleanRule') or r.get('gradientRule') or {}))}")

    # The decisive comparison: what we wrote, against what the cell ends up with.
    wrote_not_shown = sorted(set(by_col_user) - any_col)
    shown_not_wrote = sorted(any_col - set(by_col_user))
    print(f"  userEnteredFormat says struck : {len(by_col_user)} rows")
    print(f"    written but NOT in effect   : {len(wrote_not_shown)} {wrote_not_shown[:20]}")
    print(f"    in effect but NOT written   : {len(shown_not_wrote)} {shown_not_wrote[:20]}")

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
