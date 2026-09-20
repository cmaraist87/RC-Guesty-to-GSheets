"""Is a filter hiding rows -- and is it the reason the marks will not stick?

    python filter_audit.py [--rows 50,54,55,219,220,226]

STRICTLY READ-ONLY.

On 2026-09-20 a single repeatCell on Octubre row 50 came back 200 OK and changed
nothing, three times, while the identical call on blank row 1595 worked every
time. The rows that refused the mark all carried hiddenByFilter=True; the rows
beside them that did not were unaffected.

A basic filter is shared by everyone who opens the workbook -- it is not a
per-viewer filter view. So rows hidden by one are hidden for the whole team, and
this asks, per tab, whether a filter is set, what it is filtering on, and how
many rows it is hiding.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import _col_letter, month_worksheets, open_spreadsheet, read_as_dataframe
from sync import load_config


def filter_state(ws, n_cols: int):
    """(basicFilter dict or None, set of 1-based grid rows hidden by it)."""
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A:{_col_letter(n_cols)}"],
        "fields": "sheets(basicFilter,data(rowMetadata(hiddenByFilter,hiddenByUser)))",
    }
    meta = ws.spreadsheet.fetch_sheet_metadata(params)
    sheet0 = (meta.get("sheets") or [{}])[0]
    data = (sheet0.get("data") or [{}])[0]
    hidden_f, hidden_u = set(), set()
    for i, m in enumerate(data.get("rowMetadata") or []):
        if m.get("hiddenByFilter"):
            hidden_f.add(i + 1)          # rowMetadata[0] is grid row 1
        if m.get("hiddenByUser"):
            hidden_u.add(i + 1)
    return sheet0.get("basicFilter"), hidden_f, hidden_u


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", default="",
                    help="grid rows to check individually against the hidden set")
    args = ap.parse_args(argv)

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    want = [int(x) for x in args.rows.split(",") if x.strip().isdigit()]

    print(f"{'tab':<20}{'rows':>7}{'filter':>9}{'hidden':>9}{'by user':>9}   filtering on")
    print("-" * 82)
    total_hidden = 0
    for key in sorted(month_worksheets(ss)):
        ws = month_worksheets(ss)[key]
        rows, header = read_as_dataframe(ws)
        if not len(rows):
            continue
        bf, hidden, hidden_u = filter_state(ws, max(len(header), 1))
        total_hidden += len(hidden)
        crit = ""
        if bf:
            keys = sorted((bf.get("criteria") or {}).keys())
            named = [f"{_col_letter(int(k) + 1)}" for k in keys if k.isdigit()]
            crit = f"columns {','.join(named)}" if named else "(no column criteria)"
            if bf.get("filterSpecs"):
                crit += f" +{len(bf['filterSpecs'])} spec(s)"
        print(f"{ws.title:<20}{len(rows):>7}{('YES' if bf else 'no'):>9}"
              f"{len(hidden):>9}{len(hidden_u):>9}   {crit}")
        if want and ws.title.startswith(("Septiembre", "Octubre")):
            inside = [r for r in want if r in hidden]
            outside = [r for r in want if r not in hidden]
            print(f"    of the rows given: hidden {inside}")
            print(f"                       not hidden {outside}")

    print()
    if total_hidden:
        print(f"  {total_hidden} row(s) are hidden by a filter. A basic filter is shared")
        print("  by everyone who opens the workbook, so these are hidden for the whole")
        print("  team, not just whoever set it.")
    else:
        print("  No rows are hidden by a filter.")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
