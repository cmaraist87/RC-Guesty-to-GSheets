"""Count rows that share a (Property, Date) — the key the merge assumes is unique.

    python duplicates.py            # current month and later
    python duplicates.py --all      # every month tab

STRICTLY READ-ONLY. No write path exists in this file.

WHY THIS MATTERS

sheet_merge keys every existing row on (Property, Date) and matches candidates
against it. Two rows sharing that key break the assumption quietly:

  * a booking that is UNCHANGED is never rewritten, so its duplicates persist run
    after run -- only a rewrite collapses them;
  * `struck_rows` excludes struck rows from matching, so with one copy struck and
    one not, the unstruck copy finds no candidate and is struck again -- every
    morning, counted as a fresh cancellation each time.

That last one is why the reconciliation check has failed daily since 17 September:
the same bookings struck over and over rather than new ones accumulating.

Each group is classified so the count means something:

  SAME BOOKING      identical confirmation code -- a straight duplicate row
  DIFFERENT CODES   two real bookings claiming one property on one day, which the
                    sheet's one-row-per-slot model cannot represent
  BLANK             at least one copy has no confirmation code (often hand-entered)
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe, read_row_marks
from sync import _today_chicago, load_config


def norm(v) -> str:
    return str(v or "").strip()


def classify(rows) -> str:
    codes = {norm(r.get("Confirmation Code")).upper() for r in rows}
    if "" in codes:
        return "BLANK"
    return "SAME BOOKING" if len(codes) == 1 else "DIFFERENT CODES"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="Include months that have already ended.")
    ap.add_argument("--show", type=int, default=12,
                    help="How many example groups to print per tab.")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    today = _today_chicago()
    tabs = month_worksheets(ss)
    wanted = sorted(k for k in tabs if args.all or k >= (today.year, today.month))

    grand = Counter()
    grand_rows = 0
    for key in wanted:
        ws = tabs[key]
        rows, _ = read_as_dataframe(ws)
        if not len(rows):
            continue
        struck, _hl, _ac = read_row_marks(ws)

        groups: dict = defaultdict(list)
        for i, r in rows.iterrows():
            prop, date = norm(r.get("Property")), norm(r.get("Date"))[:10]
            if prop and date:
                groups[(prop, date)].append((i, r))

        dups = {k: v for k, v in groups.items() if len(v) > 1}
        extra = sum(len(v) - 1 for v in dups.values())
        grand_rows += extra
        if not dups:
            print(f"\n{ws.title}: no duplicate (Property, Date) rows.")
            continue

        kinds = Counter(classify([r for _i, r in v]) for v in dups.values())
        for k, n in kinds.items():
            grand[k] += n
        print(f"\n{ws.title}: {len(dups)} duplicated slot(s), "
              f"{extra} row(s) more than the model allows")
        print("   " + ", ".join(f"{n} {k}" for k, n in kinds.most_common()))

        shown = 0
        for (prop, date), members in sorted(dups.items(), key=lambda kv: kv[0][1]):
            if shown >= args.show:
                print(f"   ... and {len(dups) - shown} more slot(s)")
                break
            shown += 1
            kind = classify([r for _i, r in members])
            print(f"   {date}  {prop}   [{kind}]")
            for i, r in members:
                mark = "STRUCK" if i in struck else ""
                print(f"      row {i + 2:<6} {norm(r.get('Guest'))[:24]:<26}"
                      f"{norm(r.get('Confirmation Code')):<14}"
                      f"{norm(r.get('Check out - Time')) or '--':<10}{mark}")

    print("\n" + "=" * 70)
    if not grand:
        print("  No duplicated slots anywhere. The merge's key holds.")
        return 0
    print(f"  {grand_rows} row(s) beyond one per (Property, Date):")
    for k, n in grand.most_common():
        print(f"     {n:>5}  slot(s)  {k}")
    print()
    print("  SAME BOOKING duplicates are safe to collapse -- one row is the row.")
    print("  DIFFERENT CODES need a decision: two bookings, one slot, and the sheet")
    print("  can only show one of them.")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
