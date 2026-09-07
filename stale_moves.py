"""
Find struck rows that are probably NOT cancellations.

WHY
---
Until 2026-09-02 a booking that Guesty reassigned had its old row struck through,
exactly like a cancellation. The repair that re-derived every strike ran at 02:45
that day; the fix that stopped moves being struck landed at 13:11. So any tab
repaired that morning -- Septiembre included -- carries some struck rows that were
moves, and they inflate the month's cancellation count.

The signature is a struck row whose confirmation code ALSO appears on a live row in
the same tab: the booking is still there, somewhere else.

WHY THIS ONLY REPORTS
---------------------
That signature is not proof, and the ambiguity is real. Multi-unit listings share
one confirmation code -- "402 W Hall" and "404 W Hall" are both HMCQ45A53A. If one
half genuinely cancelled while the other stands, it matches the same pattern
exactly: same code, same date, a different property. No rule separates those two
cases, so this reports rather than deletes, and says which candidates are
unambiguous and which need a human eye.

    python stale_moves.py            # current month and later
    python stale_moves.py --all      # every month tab

Read-only. Touches Sheets only -- no Guesty call, no token spent.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from sheets_client import (month_worksheets, open_spreadsheet, read_as_dataframe,
                           read_row_marks)
from sync import _today_chicago, load_config


def norm(s) -> str:
    return str(s or "").strip()


def describe(struck_row, live_rows) -> tuple[str, str]:
    """(signal, detail) for one struck row against the live rows sharing its code.

    Deliberately DESCRIPTIVE, not a verdict. A reassignment and one unit of a
    multi-unit booking cancelling are structurally identical -- same code, same
    date, a different property -- so any confident label here would be a guess
    wearing a uniform. What can be said honestly is how far the booking moved, and
    that is what a person needs in order to judge.
    """
    s_prop, s_date = norm(struck_row.get("Property")), norm(struck_row.get("Date"))
    dates = {norm(r.get("Date")) for r in live_rows}
    props = {norm(r.get("Property")) for r in live_rows}
    where = ", ".join(sorted(f"{p} on {d}"
                             for p in props for d in dates
                             for r in live_rows
                             if norm(r.get("Property")) == p and norm(r.get("Date")) == d))

    if s_date not in dates:
        # The booking is live on a different DAY. Nothing about a multi-unit listing
        # produces that -- its units are cleaned the same day -- so this is a move.
        return ("MOVED TO ANOTHER DAY", f"now at {where}")

    # Same day, different property. Could be a reassignment, or could be one unit of
    # a combined listing that stopped being part of the booking. Only somebody who
    # knows the properties can say which.
    return ("SAME DAY, DIFFERENT PROPERTY",
            f"also live at {where} — a reassignment, or one unit of a combined "
            f"listing dropping out")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="Include months that have already ended (default: current month on).")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    today = _today_chicago()
    tabs = month_worksheets(ss)
    wanted = sorted(k for k in tabs if args.all or k >= (today.year, today.month))
    if not wanted:
        print("No month tabs to check.")
        return 0

    grand = defaultdict(int)
    for key in wanted:
        ws = tabs[key]
        rows, _ = read_as_dataframe(ws)
        struck, _highlighted, _accents = read_row_marks(ws)
        if not len(rows):
            continue

        by_code = defaultdict(list)
        for i, r in rows.iterrows():
            code = norm(r.get("Confirmation Code")).upper()
            if code and i not in struck:
                by_code[code].append(r)

        found = []
        for i in sorted(struck):
            if i >= len(rows):
                continue
            r = rows.iloc[i]
            code = norm(r.get("Confirmation Code")).upper()
            if not code or code not in by_code:
                continue           # nothing live under this code: a real cancellation
            verdict, why = describe(r, by_code[code])
            found.append((i + 2, r, verdict, why))
            grand[verdict] += 1

        print(f"\n{ws.title}: {len(struck)} struck row(s), "
              f"{len(found)} with the booking still live elsewhere")
        if not found:
            print("  Nothing suspect -- every struck row's booking is genuinely gone.")
            continue
        for grid_row, r, verdict, why in found:
            print(f"  row {grid_row:<5} {norm(r.get('Date')):<11} "
                  f"{norm(r.get('Property'))[:26]:<26} {norm(r.get('Guest'))[:22]:<22} "
                  f"{norm(r.get('Confirmation Code'))}")
            print(f"      {verdict}: {why}")

    print("\n" + "=" * 66)
    if not grand:
        print("  No struck row anywhere has its booking still live. Nothing to clean.")
        return 0
    for verdict, n in sorted(grand.items()):
        print(f"  {n:>4}  {verdict}")
    print("=" * 66)
    print("  MOVED TO ANOTHER DAY is safe to un-strike or delete: the booking is")
    print("  still on the sheet on a different day, so the line through the old row")
    print("  counts a cancellation that never happened.")
    print("")
    print("  SAME DAY, DIFFERENT PROPERTY needs your eye. A reassignment and one unit")
    print("  of a combined listing dropping out look identical from here, and only")
    print("  one of them should keep its line. Check the property pair: units of the")
    print("  same building are the second case, and should stay struck.")
    print("")
    print("  Nothing here was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
