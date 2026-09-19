"""Does reading the marks back see the WHOLE tab?

    python marks_audit.py

STRICTLY READ-ONLY.

Strikes leak only from the two tabs that are both the largest and the busiest:

    Septiembre 18th   48 struck at the end of a run, 33 seen by the next
    Octubre    18th   41 -> 33      Octubre 19th   44 -> 37
    Noviembre / Diciembre / Febrero    no loss at all

read_row_marks asks for the whole column with includeGridData. If Sheets caps that
response, the marks on later rows simply are not there -- and a struck row the read
cannot see looks unstruck to the merge, which strikes it again the next morning.

This compares, per tab: how many data rows the tab has, how many rows the marks
read actually returned, and where the last struck row sits.
"""
from __future__ import annotations

import sys

from sheets_client import (_col_letter, month_worksheets, open_spreadsheet,
                           read_as_dataframe, read_row_marks)
from sync import load_config


def raw_rowdata_count(ws) -> int | None:
    """How many rows the marks request actually came back with."""
    ss = getattr(ws, "spreadsheet", None)
    if ss is None or not hasattr(ss, "fetch_sheet_metadata"):
        return None
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A:{_col_letter(3)}"],
        "fields": "sheets(data(rowData(values(effectiveFormat("
                  "backgroundColor,textFormat/strikethrough)))))",
    }
    meta = ss.fetch_sheet_metadata(params)
    sheets = meta.get("sheets") or []
    data = ((sheets[0].get("data") or [{}])[0] if sheets else {})
    return len(data.get("rowData") or [])


def main(argv=None) -> int:
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])

    print(f"{'tab':<20}{'data rows':>10}{'marks read':>12}{'struck':>8}"
          f"{'last struck':>13}   verdict")
    print("-" * 78)
    short = 0
    for key in sorted(month_worksheets(ss)):
        ws = month_worksheets(ss)[key]
        rows, _ = read_as_dataframe(ws)
        n = len(rows)
        if not n:
            continue
        struck, _hl, _ac = read_row_marks(ws)
        got = raw_rowdata_count(ws)
        last = max(struck) if struck else -1
        # rowData includes the header, so n + 1 is a complete answer.
        complete = got is None or got >= n + 1
        if not complete:
            short += 1
        print(f"{ws.title:<20}{n:>10}{str(got):>12}{len(struck):>8}"
              f"{last if last >= 0 else '-':>13}   "
              f"{'' if complete else f'SHORT by {n + 1 - got} row(s)'}")

    print()
    if short:
        print(f"  {short} tab(s) returned fewer rows than they hold. A strike below")
        print("  the cut cannot be seen, so the merge treats the row as unstruck and")
        print("  strikes it again -- every morning, counted fresh each time.")
    else:
        print("  Every tab returned its full height. Truncation is not the cause.")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
