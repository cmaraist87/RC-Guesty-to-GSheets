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
                           read_row_marks, with_retry)
from sync import _today_chicago, load_config


def norm(s) -> str:
    return str(s or "").strip()


def pair_history(rows) -> dict:
    """How many DISTINCT confirmation codes each pair of properties has shared.

    This is the evidence that separates the two cases a struck row cannot be read
    from on its own.

    A combined listing books its units together every single time, so its
    properties share dozens of codes over a month -- "704 N 2nd" and "706 N 2nd"
    are one booking, and one unit cancelling while the other stands is a REAL
    cancellation that must keep its line.

    A reassignment is a one-off: Guesty moved this booking from one property to
    another, and those two addresses have no other history together.

    Guessing from the address text cannot tell them apart. 704 and 706 N 2nd read
    as "different addresses" and would be deleted as a move; they are nothing of
    the kind.
    """
    from collections import defaultdict

    by_code = defaultdict(set)
    for _, r in rows.iterrows():
        code = norm(r.get("Confirmation Code")).upper()
        prop = norm(r.get("Property"))
        if code and prop:
            by_code[code].add(prop)

    pairs = defaultdict(set)
    for code, props in by_code.items():
        ps = sorted(props)
        for i, x in enumerate(ps):
            for y in ps[i + 1:]:
                pairs[(x, y)].add(code)
    return {k: len(v) for k, v in pairs.items()}


def classify(struck_row, live_rows, pairs) -> tuple[str, str]:
    """(verdict, why) for one struck row. Verdict is 'move', 'cancellation' or
    'unclear' -- only 'move' is ever safe to delete."""
    s_prop, s_date = norm(struck_row.get("Property")), norm(struck_row.get("Date"))
    dests = [(norm(r.get("Property")), norm(r.get("Date"))) for r in live_rows]
    where = ", ".join(sorted(f"{p} on {d}" for p, d in dests))

    if s_date not in {d for _p, d in dests}:
        # Live on a different DAY. A combined listing's units are cleaned the same
        # day, so nothing about one produces this. Unambiguous.
        return ("move", f"live on another day -- now at {where}")

    # Same day, different property. Ask what these two addresses have done together.
    others = [p for p, d in dests if d == s_date and p != s_prop]
    shared = max((pairs.get(tuple(sorted((s_prop, o))), 0) for o in others),
                 default=0)
    if shared > 1:
        return ("cancellation",
                f"{s_prop} and {others[0]} share {shared} bookings -- a combined "
                f"listing, so this unit really was cancelled")
    if shared == 1:
        return ("move", f"{s_prop} and {others[0]} share only this one booking -- "
                        f"a reassignment; now at {where}")
    return ("unclear", f"no shared history with {where}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="Include months that have already ended (default: current month on).")
    ap.add_argument("--fix", action="store_true",
                    help="DELETE the rows proved to be moves. Cancellations and "
                         "anything unclear are always left alone.")
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
    deleted: dict = {}
    for key in wanted:
        ws = tabs[key]
        rows, _ = read_as_dataframe(ws)
        struck, _highlighted, _accents = read_row_marks(ws)
        if not len(rows):
            continue

        pairs = pair_history(rows)
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
            verdict, why = classify(r, by_code[code], pairs)
            found.append((i, i + 2, r, verdict, why))
            grand[verdict] += 1

        print(f"\n{ws.title}: {len(struck)} struck row(s), "
              f"{len(found)} with the booking still live elsewhere")
        if not found:
            print("  Nothing suspect -- every struck row's booking is genuinely gone.")
            continue
        for _pos, grid_row, r, verdict, why in found:
            print(f"  row {grid_row:<5} {norm(r.get('Date')):<11} "
                  f"{norm(r.get('Property'))[:26]:<26} {norm(r.get('Guest'))[:22]:<22} "
                  f"{norm(r.get('Confirmation Code'))}")
            print(f"      {verdict.upper()}: {why}")

        if args.fix:
            # Bottom-up, and in ONE request per tab.
            #
            # A call per row blew Sheets' "write requests per minute per user"
            # quota (60) partway through Agosto's 117 deletions on 2026-09-11, so
            # the tab was left half-cleaned. Sheets applies the requests in the
            # order given, and deleting from the bottom up means each index is
            # still valid when its turn comes.
            doomed = sorted((g for _p, g, _r, v, _w in found if v == "move"),
                            reverse=True)
            if doomed:
                reqs = [{"deleteDimension": {"range": {
                    "sheetId": ws.id, "dimension": "ROWS",
                    "startIndex": gr - 1, "endIndex": gr}}} for gr in doomed]
                for start in range(0, len(reqs), 500):
                    chunk = reqs[start:start + 500]
                    with_retry(
                        lambda c=chunk: ws.spreadsheet.batch_update({"requests": c}),
                        f"deleting {len(chunk)} row(s) from '{ws.title}'")
            deleted[ws.title] = len(doomed)
            print(f"   -> deleted {len(doomed)} row(s) proved to be moves; "
                  f"left {len(found) - len(doomed)} alone.")

    print("\n" + "=" * 66)
    if not grand:
        print("  No struck row anywhere has its booking still live. Nothing to clean.")
        return 0
    for verdict, n in sorted(grand.items()):
        print(f"  {n:>4}  {verdict}")
    print("=" * 66)
    print("  MOVE        the booking is live elsewhere and these two addresses have")
    print("              no other history together. The line counts a cancellation")
    print("              that never happened -- safe to delete.")
    print("")
    print("  CANCELLATION  the two properties are booked together again and again:")
    print("              a combined listing. One unit dropping out IS a cancellation")
    print("              and keeps its line.")
    print("")
    print("  UNCLEAR     left alone; needs a person.")
    print("")
    if args.fix:
        print(f"  Deleted {sum(deleted.values())} row(s): "
              + ", ".join(f"{t} {n}" for t, n in deleted.items() if n))
    else:
        print("  Nothing was changed. Re-run with --fix to delete the MOVE rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
