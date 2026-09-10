"""Dump every row the sheet holds for one date, exactly as it stands.

    python date_rows.py --date 2026-09-07

Shows the checkout and check-in halves of each row separately, because the two are
merged into a single row per (property, date) and a row can carry one without the
other. A property whose CHECKOUT is blank has no departure clean scheduled that
day, whatever else the row says.

Read-only. Touches Sheets only -- no Guesty call, no token spent.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe, read_row_marks
from sync import load_config


def col(row, *names):
    for n in names:
        if n in row.index:
            v = str(row[n]).strip()
            if v:
                return v
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args(argv)

    y, m, _d = (int(x) for x in args.date.split("-"))
    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = month_worksheets(ss).get((y, m))
    if ws is None:
        print(f"No tab for {y}-{m:02d}.", file=sys.stderr)
        return 2

    rows, _ = read_as_dataframe(ws)
    struck, _hl, _ac = read_row_marks(ws)

    hits = [(i, r) for i, r in rows.iterrows()
            if str(r.get("Date", ""))[:10] == args.date]
    print(f"{ws.title}: {len(hits)} row(s) dated {args.date}\n")
    print(f"  {'row':<6}{'Property':<24}{'Guest':<22}"
          f"{'CHECK-OUT':<11}{'CHECK-IN':<11}{'T/O':<5}mark")
    print("  " + "-" * 86)
    no_out = 0
    for i, r in hits:
        out = col(r, "Check out - Time", "Check-out Time", "Check out Time")
        inn = col(r, "Check-in Time", "Check in - Time", "Check in Time")
        to = col(r, "T/O")
        if not out:
            no_out += 1
        print(f"  {i + 2:<6}{col(r, 'Property')[:23]:<24}{col(r, 'Guest')[:21]:<22}"
              f"{out or '-- none --':<11}{inn or '--':<11}{to or '-':<5}"
              f"{'STRUCK' if i in struck else ''}")
    print(f"\n  {len(hits)} row(s); {no_out} with NO checkout time recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
