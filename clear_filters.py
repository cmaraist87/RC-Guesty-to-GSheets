"""Remove the filters that stop the sync marking cancelled rows.

    python clear_filters.py            # show what is there
    python clear_filters.py --confirm  # actually remove them

WRITES when --confirm is given: it deletes the basic filter on each month tab.
No cell value, no formatting and no column layout is touched.

WHY
---
Google Sheets does not apply a repeatCell format to a row a filter is hiding.
The request is accepted, a reply comes back for it, and nothing changes. Measured
on 2026-09-20: Octubre asked for 66 strikes and carried 47, and all 26 rows that
refused the mark were hidden by the tab's filter, while none of the rows outside
the hidden set failed. The tabs with no hidden rows missed nothing at all.

The cost of leaving it is not cosmetic. A live booking that keeps a line it
should have lost is read back next run as already cancelled, skipped, and
re-added -- Octubre grew 1373 -> 1384 -> 1395 rows over three runs on one
afternoon.

Every spec is printed before it is deleted, so a filter someone wants back can be
rebuilt from this log.
"""
from __future__ import annotations

import argparse
import json
import sys

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="required; without it nothing is removed")
    args = ap.parse_args(argv)

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    meta = ss.fetch_sheet_metadata(
        {"fields": "sheets(properties(sheetId,title),basicFilter)"})

    tabs = {w.title: w for w in month_worksheets(ss).values()}
    targets = []
    for sh in meta.get("sheets") or []:
        title = (sh.get("properties") or {}).get("title", "")
        if title not in tabs or not sh.get("basicFilter"):
            continue
        targets.append((title, (sh.get("properties") or {})["sheetId"],
                        sh["basicFilter"]))

    if not targets:
        print("No month tab has a filter on it. Nothing to do.")
        return 0

    print(f"{len(targets)} tab(s) carry a filter:")
    for title, sheet_id, bf in targets:
        print("")
        print(f"  {title} (sheetId {sheet_id})")
        # The whole spec, so it can be rebuilt by hand from this log alone.
        for line in json.dumps(bf, indent=2, sort_keys=True).splitlines():
            print(f"    {line}")

    if not args.confirm:
        print("")
        print("--confirm not given; nothing was removed.")
        return 0

    requests = [{"clearBasicFilter": {"sheetId": sid}} for _t, sid, _b in targets]
    ss.batch_update({"requests": requests})

    after = ss.fetch_sheet_metadata(
        {"fields": "sheets(properties(title),basicFilter)"})
    left = [(sh.get("properties") or {}).get("title")
            for sh in (after.get("sheets") or [])
            if sh.get("basicFilter") and
            (sh.get("properties") or {}).get("title") in tabs]
    print("")
    print(f"Removed {len(requests)} filter(s). Month tabs still filtered: {left or 'none'}")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
