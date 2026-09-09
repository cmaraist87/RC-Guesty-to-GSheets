"""Find rows whose property carries an invented, zero-padded unit number.

    427 Gravier 002   should be   427 Gravier 302
    31 Congress 004   should be   31 Congress 204

These come from combined listings ("427 Grav 301&2") that the expansion used to
zero-pad instead of continuing the number before it. The expansion is fixed, so
Guesty produces the right names now -- this reports the rows already written to the
sheet under the old spelling.

The correct number is taken from the SIBLING row: the same confirmation code at a
real unit. "002" beside "301" is "302". Where there is no sibling, or the siblings
disagree, this says so instead of guessing.

    python bad_units.py           # current month and later
    python bad_units.py --all     # every month tab

Read-only. Touches Sheets only -- no Guesty call, no token spent.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import _today_chicago, load_config

# A unit number that starts with 0 is never a real one -- no building numbers its
# rooms 002. Anything else is left alone.
BAD_UNIT = re.compile(r"^(.*?)\s+(0\d+)$")


def repair(prop: str, siblings: list[str]) -> tuple[str | None, str]:
    """The name this row should carry, and why. None when it cannot be told."""
    m = BAD_UNIT.match(prop)
    if not m:
        return None, "not a zero-padded unit"
    base, bad = m.group(1), m.group(2)
    frag = bad.lstrip("0") or "0"

    refs = set()
    for s in siblings:
        sm = re.match(rf"^{re.escape(base)}\s+(\d+)$", s)
        if sm and not sm.group(1).startswith("0"):
            refs.add(sm.group(1))
    if not refs:
        return None, "no sibling row at this address to take the number from"

    fixed = {r[:len(r) - len(frag)] + frag for r in refs if len(frag) < len(r)}
    if len(fixed) != 1:
        return None, f"siblings disagree: {sorted(refs)}"
    return f"{base} {fixed.pop()}", f"from sibling {sorted(refs)[0]}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    today = _today_chicago()
    tabs = month_worksheets(ss)
    wanted = sorted(k for k in tabs if args.all or k >= (today.year, today.month))

    total = unfixable = 0
    for key in wanted:
        ws = tabs[key]
        rows, _ = read_as_dataframe(ws)
        if not len(rows):
            continue

        by_code = defaultdict(list)
        for _, r in rows.iterrows():
            code = str(r.get("Confirmation Code", "")).strip().upper()
            if code:
                by_code[code].append(str(r.get("Property", "")).strip())

        hits = []
        for i, r in rows.iterrows():
            prop = str(r.get("Property", "")).strip()
            if not BAD_UNIT.match(prop):
                continue
            code = str(r.get("Confirmation Code", "")).strip().upper()
            sibs = [p for p in by_code.get(code, []) if p != prop]
            fixed, why = repair(prop, sibs)
            hits.append((i + 2, str(r.get("Date", ""))[:10], prop,
                         str(r.get("Guest", "")).strip(), code, fixed, why))

        if not hits:
            continue
        print(f"\n{ws.title}: {len(hits)} row(s) with an invented unit number")
        for grid, date, prop, guest, code, fixed, why in hits:
            total += 1
            if fixed:
                print(f"  row {grid:<5} {date}  {prop:<22} -> {fixed:<22} "
                      f"{guest[:20]:<20} {code}  ({why})")
            else:
                unfixable += 1
                print(f"  row {grid:<5} {date}  {prop:<22} -> ????"
                      f"{'':<22}{guest[:20]:<20} {code}  ({why})")

    print("\n" + "=" * 70)
    if not total:
        print("  No invented unit numbers anywhere. Nothing to repair.")
    else:
        print(f"  {total} row(s) carry an invented unit number; "
              f"{total - unfixable} can be named from a sibling row, "
              f"{unfixable} cannot.")
    print("  Nothing here was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
