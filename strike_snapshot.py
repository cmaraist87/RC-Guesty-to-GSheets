"""Every struck row on the sheet, as a diffable list.

    python strike_snapshot.py

STRICTLY READ-ONLY.

Run it either side of a sync and diff the two outputs. That brackets the loss in
time and says WHERE it happens, which three eliminated hypotheses have not:

    run ends with 48 struck  ->  next run reads 33

Derived from the run logs' own arithmetic. This measures it directly instead, and
identifies each struck row by BOOKING rather than by position, so a row moving
does not look like a row losing its line.

Diff two runs of this with:  diff before.txt after.txt
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe, read_row_marks
from sync import load_config


def main(argv=None) -> int:
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"STRIKE SNAPSHOT  {stamp}")
    print("# tab | date | property | code | grid row")
    total = 0
    tabs = month_worksheets(ss)
    for key in sorted(tabs):
        ws = tabs[key]
        rows, _ = read_as_dataframe(ws)
        if not len(rows):
            continue
        struck, _hl, _ac = read_row_marks(ws)
        lines = []
        for i in sorted(struck):
            if i >= len(rows):
                # A mark below the last data row: nothing to identify it by, but
                # worth counting -- it is still a line on the grid.
                lines.append(f"{ws.title} | (past the last data row) | | | {i + 2}")
                continue
            r = rows.iloc[i]
            lines.append(
                f"{ws.title} | {str(r.get('Date',''))[:10]} | "
                f"{str(r.get('Property','')).strip()} | "
                f"{str(r.get('Confirmation Code','')).strip()} | {i + 2}")
        total += len(lines)
        for ln in sorted(lines):
            print(ln)
    print(f"# TOTAL STRUCK: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
