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
                     str(r.get("Confirmation Code", "")).strip().upper()))
    return hits, prop_col, no_id


def write_fixes(ws, hits, prop_col: int) -> int:
    col = _col_letter(prop_col)
    data = [{"range": f"{col}{grid}", "values": [[want]]}
            for grid, _d, _was, want, _c in hits]
    written = 0
    for start in range(0, len(data), MAX_PER_BATCH):
        chunk = data[start:start + MAX_PER_BATCH]
        with_retry(lambda c=chunk: ws.batch_update(c, value_input_option="RAW"),
                   f"writing {len(chunk)} property name(s) to '{ws.title}'")
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
    args = ap.parse_args(argv)

    if not LISTING_ALIASES:
        print("Nothing to do: the alias table is empty.")
        return 0
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
    total, fixed, orphans = 0, 0, 0
    for (y, m), ws in sorted(tabs.items()):
        if not args.all and (y, m) < floor:
            continue
        hits, prop_col, no_id = find_rows(ws)
        orphans += no_id
        if no_id:
            print(f"'{ws.title}': {no_id} row(s) look like 1401 Caron* but carry "
                  f"NO listing id -- reported, not touched.")
        if not hits:
            continue
        total += len(hits)
        print(f"'{ws.title}': {len(hits)} row(s) to merge")
        for grid, date, was, want, code in hits:
            print(f"   row {grid:<5} {date}  {was!r} -> {want!r}  [{code}]")
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
