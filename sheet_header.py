"""The sheet's real columns, with the letter each one actually lives in.

    python sheet_header.py --tab "Octubre 2026"
    python sheet_header.py --tab "Octubre 2026" --rows 150,339,493

STRICTLY READ-ONLY.

Written because a repair tool wrote 54 cells to the wrong column. It computed the
Property column's 0-BASED dataframe index and handed it to `_col_letter`, which is
1-BASED ("1 -> A"). Every write therefore landed one column to the LEFT of
Property, and the repair reported success while changing nothing it meant to.

The lesson is narrow and worth a tool: a column's position has two different
conventions in this codebase and nothing was printing the mapping, so an
off-by-one was invisible. `--rows` dumps named rows verbatim so the damage to a
specific cell can be seen rather than inferred.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import (_col_letter, month_worksheets, open_spreadsheet,
                           read_as_dataframe)
from sync import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tab", default="", help="tab title; blank = the first one")
    ap.add_argument("--rows", default="",
                    help="comma-separated GRID row numbers to dump verbatim")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    ws = None
    if args.tab:
        for w in tabs.values():
            if w.title == args.tab:
                ws = w
                break
        if ws is None:
            print(f"ERROR: no tab titled {args.tab!r}. Tabs: "
                  f"{sorted(w.title for w in tabs.values())}", file=sys.stderr)
            return 2
    else:
        ws = sorted(tabs.items())[0][1]

    frame, header_raw = read_as_dataframe(ws)
    print(f"'{ws.title}': {len(frame)} data row(s), {len(header_raw)} column(s)")
    print("")
    print(f"  {'letter':<8}{'0-based':<9}{'1-based':<9}name")
    print("  " + "-" * 56)
    for i, name in enumerate(frame.columns):
        letter = _col_letter(i + 1)
        mark = ""
        if str(name) == "Property":
            mark = "   <-- Property"
        print(f"  {letter:<8}{i:<9}{i + 1:<9}{name!r}{mark}")

    if "Property" in frame.columns:
        k = list(frame.columns).index("Property")
        print("")
        print(f"  Property is 0-based index {k}, so its letter is "
              f"_col_letter({k} + 1) = {_col_letter(k + 1)}.")
        print(f"  _col_letter({k}) would be {_col_letter(k)} -- the column to its "
              f"LEFT, {frame.columns[k - 1]!r}. That was the bug.")

    want = [int(x) for x in args.rows.split(",") if x.strip().isdigit()]
    if want:
        print("")
        print("  Rows dumped verbatim (grid numbering; data row 1 is grid row 2):")
        for grid in want:
            idx = grid - 2
            if idx < 0 or idx >= len(frame):
                print(f"   grid {grid}: out of range")
                continue
            row = frame.iloc[idx]
            print(f"   --- grid row {grid} ---")
            for i, name in enumerate(frame.columns):
                val = str(row.iloc[i])
                if val.strip():
                    print(f"      {_col_letter(i + 1):<4}{str(name)[:26]:<28}"
                          f"{val[:46]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
