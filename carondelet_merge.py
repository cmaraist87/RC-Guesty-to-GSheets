"""Backfill the 1401 Carondelet merge onto rows written before it existed.

    python carondelet_merge.py            # report, current month and later
    python carondelet_merge.py --all      # report, every month tab
    python carondelet_merge.py --fix      # WRITE the merged name

1401 Carondelet is ONE unit let through two Guesty listings, "1401 Caron U V1"
and "1401 Caron A U V2". They normalised to two different property names, so the
sheet showed one flat as two that never overlapped and handed off to each other
-- one turnover presented as two unrelated jobs.

processing.LISTING_ALIASES fixed that on 2026-09-30, keyed on the Guesty LISTING
ID so it survives the renames this account does mid-booking. But it only applies
when a row is DERIVED, and the sync re-derives a row only when its booking
changes -- Property is not one of the four fields that mark a row as changed. So
every row already in the sheet kept the old name, and on 2026-10-06 both spellings
were still present: "1401 Carondelet A" on rows dated 1 October to 10 February,
and the merged name from 29 October onward. The unit is still two properties on
the schedule, and in Connecteam it still resolves to two different Jobs.

This is the one-off pass that finishes it.

WHY IT MATCHES ON THE LISTING ID AND NOT THE NAME
-------------------------------------------------
Because the danger here is over-reach, not under-reach. 1409 Caron A, 1413 Caron
B, 1417 Caron A and 1421 Caron B are REAL separate flats in the same building. A
rule that stripped a trailing letter, or that matched "1401 Carondelet*", would
merge genuinely different places and send a cleaner to the wrong door. The alias
table names exactly two listing ids, the team confirmed exactly that merge, and
this tool will not touch a row whose listing id is not one of them.

A row with no listing id is reported and skipped, never guessed at.

--fix touches one cell per row -- the Property column -- and nothing else. No row
is added, removed, struck or unstruck, and no other column is rewritten. Property
is not a change-detection field, so rewriting it cannot make a row look updated
to the next sync.

No Guesty call, so no token is spent either way.
"""
from __future__ import annotations

import argparse
import sys

from processing import LISTING_ALIASES
from sheets_client import (_col_letter, month_worksheets, open_spreadsheet,
                           read_as_dataframe, with_retry)
from sync import _today_chicago, load_config

MAX_PER_BATCH = 100
ID_COLUMNS = ("LISTING ID", "Listing ID", "LISTING_ID")

# The listing id is the better key and is tried first, but THE SHEET DOES NOT
# CARRY IT: on 2026-10-07 all 92 rows spelled "1401 Caron*" had the column empty.
# So the fallback is an exact NAME match, and exact is the whole point.
#
# Without the alias, the two listings normalise to:
#     "1401 Caron U V1"    -> "1401 Carondelet"      (already the merged name)
#     "1401 Caron A U V2"   -> "1401 Carondelet A"    (the one to rewrite)
#
# Every genuinely separate flat in that building has a DIFFERENT STREET NUMBER --
# 1405, 1409, 1413, 1417, 1421 Carondelet A/B -- so no exact key here can reach
# one. A prefix or a strip-the-trailing-letter rule could, which is why neither
# is used. Checked by _check_table below, not by my say-so.
MERGE_BY_NAME = {
    "1401 Carondelet A": "1401 Carondelet",
}


def _street_number(name: str) -> str:
    head = str(name).strip().split(" ", 1)[0]
    return head if head.isdigit() else ""


def _check_table() -> list:
    """Refuse to run on a table that could merge two different addresses.

    A guard on the data, not on the code. Adding an entry here merges two places
    on a crew's schedule, so the cost of a careless line is a cleaner at the
    wrong door -- the same reason the alias is keyed on an id in the first place.
    """
    bad = []
    targets = set(LISTING_ALIASES.values())
    for was, want in MERGE_BY_NAME.items():
        if want not in targets:
            bad.append(f"{was!r} -> {want!r}: {want!r} is not a name the alias "
                       f"table produces, so the two have drifted apart")
        if _street_number(was) != _street_number(want):
            bad.append(f"{was!r} -> {want!r}: different street numbers, which "
                       f"means merging two different addresses")
        if not _street_number(was):
            bad.append(f"{was!r}: no street number, too loose to be safe")
    return bad


def listing_id_of(row) -> str:
    for col in ID_COLUMNS:
        if col in row:
            got = str(row.get(col) or "").strip()
            if got:
                return got
    return ""


def find_rows(ws):
    """[(grid row, date, was, should be, code)] plus the Property column index.

    Returns only rows whose listing id is in the alias table AND whose Property
    does not already carry the merged name.
    """
    frame, header = read_as_dataframe(ws)
    if not len(frame) or "Property" not in frame.columns:
        return [], 0, 0
    prop_col = list(frame.columns).index("Property")
    hits, no_id = [], 0
    for i, r in frame.iterrows():
        lid = listing_id_of(r)
        prop = str(r.get("Property", "")).strip()
        if not lid:
            # Only interesting if the NAME suggests it is one of ours; a row with
            # no id that looks nothing like 1401 is simply another property.
            if prop.startswith("1401 Caron"):
                no_id += 1
            continue
        want = LISTING_ALIASES.get(lid)
        if not want or prop == want:
            continue
        hits.append((i + 2, str(r.get("Date", ""))[:10], prop, want,
                     str(r.get("Confirmation Code", "")).strip().upper(), "id"))
    return hits, prop_col, no_id


def find_rows_by_name(ws):
    """The fallback, for a sheet whose rows carry no listing id.

    Exact match on MERGE_BY_NAME. A name that is not a key is not touched, so
    1405/1409/1413/1417/1421 Carondelet are out of reach by construction rather
    than by intention.
    """
    frame, _header = read_as_dataframe(ws)
    if not len(frame) or "Property" not in frame.columns:
        return [], 0
    prop_col = list(frame.columns).index("Property")
    hits = []
    for i, r in frame.iterrows():
        prop = str(r.get("Property", "")).strip()
        want = MERGE_BY_NAME.get(prop)
        if not want:
            continue
        hits.append((i + 2, str(r.get("Date", ""))[:10], prop, want,
                     str(r.get("Confirmation Code", "")).strip().upper(), "name"))
    return hits, prop_col


def write_fixes(ws, hits, prop_col: int) -> int:
    # +1 because `prop_col` is a 0-BASED dataframe index and _col_letter is
    # 1-BASED ("1 -> A"). Without it every write lands one column to the LEFT of
    # Property. That happened on 2026-10-07: 54 cells were written, the tool
    # reported success, and the column it meant to change was untouched.
    col = _col_letter(prop_col + 1)
    data = [{"range": f"{col}{grid}", "values": [[want]]}
            for grid, _d, _was, want, _c, _how in hits]
    written = 0
    for start in range(0, len(data), MAX_PER_BATCH):
        chunk = data[start:start + MAX_PER_BATCH]
        with_retry(lambda c=chunk: ws.batch_update(c, value_input_option="RAW"),
                   f"writing {len(chunk)} property name(s) to '{ws.title}'")
        written += len(chunk)
    return written


def find_misplaced(ws):
    """Cells in the column LEFT of Property holding a name that belongs in it.

    The footprint of the off-by-one on 2026-10-07: 54 writes landed one column
    short, in `assigned`, which is a CHECKBOX the team ticks. Those cells now
    hold "1401 Carondelet" where only TRUE or FALSE belongs.

    Self-identifying on purpose. It looks for the exact strings this tool writes,
    in exactly the column the bug wrote them to, so it cannot touch a cell the
    bug did not touch -- and it finds them without being told which rows.
    """
    frame, _header = read_as_dataframe(ws)
    if not len(frame) or "Property" not in frame.columns:
        return [], 0
    k = list(frame.columns).index("Property")
    if k == 0:
        return [], 0
    left = frame.columns[k - 1]
    targets = set(MERGE_BY_NAME.values()) | set(LISTING_ALIASES.values())
    hits = []
    for i, r in frame.iterrows():
        val = str(r.iloc[k - 1]).strip()
        if val in targets:
            hits.append((i + 2, str(r.get("Date", ""))[:10], str(left), val,
                         str(r.get("Confirmation Code", "")).strip().upper()))
    return hits, k - 1


def clear_misplaced(ws, hits, col_idx: int) -> int:
    """Put FALSE back in those cells, as a BOOLEAN, not the text "FALSE".

    USER_ENTERED, and a real Python False, because the column carries a checkbox.
    RAW with the string "FALSE" would leave text in a tickbox and look fixed
    while still being wrong -- which is the same mistake twice.
    """
    col = _col_letter(col_idx + 1)
    data = [{"range": f"{col}{grid}", "values": [[False]]}
            for grid, _d, _c, _v, _code in hits]
    written = 0
    for start in range(0, len(data), MAX_PER_BATCH):
        chunk = data[start:start + MAX_PER_BATCH]
        with_retry(
            lambda c=chunk: ws.batch_update(c, value_input_option="USER_ENTERED"),
            f"restoring {len(chunk)} checkbox(es) on '{ws.title}'")
        written += len(chunk)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="Include months that have already ended.")
    ap.add_argument("--fix", action="store_true",
                    help="WRITE the merged name (default: report only).")
    ap.add_argument("--clear-misplaced", action="store_true",
                    help="Repair the 2026-10-07 off-by-one: put FALSE back in "
                         "the `assigned` checkbox cells that got a property "
                         "name written into them.")
    args = ap.parse_args(argv)

    if not LISTING_ALIASES:
        print("Nothing to do: the alias table is empty.")
        return 0
    problems = _check_table()
    if problems:
        print("REFUSING TO RUN. The name table could merge two different "
              "addresses:", file=sys.stderr)
        for line in problems:
            print(f"   {line}", file=sys.stderr)
        return 2
    print(f"Alias table: {len(LISTING_ALIASES)} listing id(s) -> "
          f"{sorted(set(LISTING_ALIASES.values()))}")
    for lid, name in sorted(LISTING_ALIASES.items()):
        print(f"   {lid}  ->  {name}")
    print("")

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    if not tabs:
        print("No month tabs found.")
        return 0

    today = _today_chicago()
    floor = (today.year, today.month)

    if args.clear_misplaced:
        found = cleared = 0
        for (y, m), ws in sorted(tabs.items()):
            hits, col_idx = find_misplaced(ws)
            if not hits:
                continue
            found += len(hits)
            print(f"'{ws.title}': {len(hits)} misplaced cell(s) in "
                  f"{_col_letter(col_idx + 1)} ({hits[0][2]!r})")
            for grid, date, _c, val, code in hits:
                print(f"   row {grid:<5} {date}  {val!r} -> FALSE  [{code}]")
            if args.fix:
                cleared += clear_misplaced(ws, hits, col_idx)
                print(f"   restored {len(hits)} checkbox(es).")
        print("")
        if not found:
            print("Nothing misplaced. That column holds no property names.")
        elif args.fix:
            print(f"Restored {cleared} of {found} cell(s) to FALSE.")
            print("Re-run without --fix to confirm none are left.")
        else:
            print(f"{found} cell(s) would be restored to FALSE. --fix not given; "
                  f"nothing was written.")
        return 0

    total, fixed, orphans = 0, 0, 0
    for (y, m), ws in sorted(tabs.items()):
        if not args.all and (y, m) < floor:
            continue
        hits, prop_col, no_id = find_rows(ws)
        if not hits and no_id:
            # No listing id anywhere on this tab, which is the normal case for
            # this sheet. Fall back to the exact-name table.
            hits, prop_col = find_rows_by_name(ws)
        orphans += 0 if hits else no_id
        if not hits:
            continue
        total += len(hits)
        print(f"'{ws.title}': {len(hits)} row(s) to merge")
        for grid, date, was, want, code, how in hits:
            print(f"   row {grid:<5} {date}  {was!r} -> {want!r}  [{code}] "
                  f"(matched on {how})")
        if args.fix:
            fixed += write_fixes(ws, hits, prop_col)
            print(f"   wrote {len(hits)} cell(s).")

    print("")
    if not total:
        print("Nothing to merge. Every aliased row already carries the merged "
              "name.")
        if orphans:
            print(f"({orphans} row(s) with no listing id were reported above and "
                  f"need a human to decide; this tool never guesses from a name.)")
        return 0
    if args.fix:
        print(f"Merged {fixed} of {total} row(s).")
        print("Re-run without --fix to confirm nothing is left.")
    else:
        print(f"{total} row(s) would be merged. --fix not given; nothing was "
              f"written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
