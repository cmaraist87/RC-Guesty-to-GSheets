"""Repair rows whose property carries an invented, zero-padded unit number.

    427 Gravier 002   should be   427 Gravier 302
    31 Congress 004   should be   31 Congress 204

These come from combined listings ("427 Grav 301&2") that the expansion used to
zero-pad instead of continuing the number before it. The expansion is fixed, so
Guesty produces the right names now -- but rows already written under the old
spelling stay wrong, because the sync only ever revisits a row it processes:

  * a STRUCK row is skipped by the merge by design, so a cancelled or moved
    booking keeps its old name for good; and
  * a row dated before the fetch window is never examined at all.

The correct number comes from the SIBLING row: the same confirmation code at a real
unit in the same building. "002" beside "301" is "302". Where there is no sibling,
or the siblings disagree, this refuses to guess and reports the row instead.

    python bad_units.py            # report, current month and later
    python bad_units.py --all      # report, every month tab
    python bad_units.py --fix      # WRITE the corrected names

--fix touches one cell per row -- the Property column -- and nothing else. No row is
added, removed, struck or unstruck, and no other column is read back or rewritten.

No Guesty call, so no token is spent either way.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict

from sheets_client import (_col_letter, month_worksheets, open_spreadsheet,
                           read_as_dataframe, with_retry)
from sync import _today_chicago, load_config

# A unit number with a leading zero is never a real one -- no building numbers its
# rooms 002. Anything else is left strictly alone.
BAD_UNIT = re.compile(r"^(.*?)\s+(0\d+)$")

MAX_PER_BATCH = 100


def repair(prop: str, siblings: list[str]) -> tuple[str | None, str]:
    """The name this row should carry, and why -- or (None, reason) if it can't be told.

    `siblings` are the OTHER properties booked under the same confirmation code. A
    combined listing books every unit at once, so the sibling is the same building
    at a real number, and that number supplies the digits the fragment is missing.
    """
    m = BAD_UNIT.match(prop)
    if not m:
        return None, "not a zero-padded unit"
    base, bad = m.group(1), m.group(2)
    frag = bad.lstrip("0") or "0"

    refs = {sm.group(1)
            for s in siblings
            if (sm := re.match(rf"^{re.escape(base)}\s+(\d+)$", s))
            and not sm.group(1).startswith("0")}
    if not refs:
        return None, "no sibling row at this address to take the number from"

    # Every sibling must agree on the answer. Two units in different lines of the
    # building (201 and 304) give 202 and 304 -- a coin toss, so refuse.
    fixed = {r[:len(r) - len(frag)] + frag for r in refs if len(frag) < len(r)}
    if len(fixed) != 1:
        return None, f"siblings disagree: {sorted(refs)}"
    return f"{base} {fixed.pop()}", f"from sibling {sorted(refs)[0]}"


def find_in_tab(ws) -> tuple[list[tuple], int | None]:
    """(hits, 1-based Property column). Each hit: (grid_row, date, prop, guest,
    code, fixed_name_or_None, why)."""
    rows, header = read_as_dataframe(ws)
    if not len(rows):
        return [], None
    try:
        prop_col = [str(h).strip().lower() for h in header].index("property") + 1
    except ValueError:
        return [], None

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
    return hits, prop_col


def write_fixes(ws, hits, prop_col: int) -> int:
    """Write the corrected Property cell for every hit that has one."""
    col = _col_letter(prop_col)
    data = [{"range": f"{col}{grid}", "values": [[fixed]]}
            for grid, _d, _p, _g, _c, fixed, _w in hits if fixed]
    written = 0
    for start in range(0, len(data), MAX_PER_BATCH):
        chunk = data[start:start + MAX_PER_BATCH]
        with_retry(lambda c=chunk: ws.batch_update(c, value_input_option="RAW"),
                   f"writing {len(chunk)} property name(s) to '{ws.title}'")
        written += len(chunk)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="Include months that have already ended.")
    ap.add_argument("--fix", action="store_true",
                    help="WRITE the corrected names (default: report only).")
    args = ap.parse_args(argv)

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    today = _today_chicago()
    tabs = month_worksheets(ss)
    wanted = sorted(k for k in tabs if args.all or k >= (today.year, today.month))

    total = unfixable = written = 0
    for key in wanted:
        ws = tabs[key]
        hits, prop_col = find_in_tab(ws)
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
                print(f"  row {grid:<5} {date}  {prop:<22} -> LEFT ALONE"
                      f"{'':<12}{guest[:20]:<20} {code}  ({why})")
        if args.fix:
            n = write_fixes(ws, hits, prop_col)
            written += n
            print(f"   -> rewrote {n} property name(s) on '{ws.title}'.")

    print("\n" + "=" * 70)
    if not total:
        print("  No invented unit numbers anywhere. Nothing to repair.")
    elif args.fix:
        print(f"  {total} row(s) carried an invented unit number. "
              f"Rewrote {written}; left {unfixable} alone.")
        if unfixable:
            print("  The ones left alone need a person: the siblings do not agree on")
            print("  which unit the row means.")
    else:
        print(f"  {total} row(s) carry an invented unit number; "
              f"{total - unfixable} can be named from a sibling row, "
              f"{unfixable} cannot.")
        print("  Nothing was changed. Re-run with --fix to write the corrected names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
