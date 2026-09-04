"""
What the sync sees in the workbook, and what it will do with each tab.

Read-only, and it costs no Guesty quota -- it only talks to Sheets. Useful after
tabs have been added, hidden or deleted by hand, because two of those are invisible
to a person reading the sheet but change what the sync does:

  * The TEMPLATE for auto-creating a month is the LATEST month tab, unless
    TEMPLATE_TAB says otherwise. Delete the last one and the template silently
    becomes a different tab, and every month created from then on inherits its
    layout and its checkbox rules.
  * A tab that is not named as a month is ignored entirely.

    python list_tabs.py
"""
from __future__ import annotations

import sys

from sheets_client import (open_spreadsheet, parse_month_title,
                           read_checkbox_columns)
from sync import _is_finished_month, load_config


def main() -> int:
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = ss.worksheets()          # includes hidden ones, which the sync also uses
    months = {}
    other = []
    for ws in tabs:
        ym = parse_month_title(ws.title)
        (months.setdefault(ym, ws) if ym else other.append(ws))

    print(f"{len(tabs)} tab(s) in the workbook.\n")
    print(f"{len(months)} the sync writes to:")
    for ym in sorted(months):
        ws = months[ym]
        stamp = f"{ym[0]}-{ym[1]:02d}"
        hidden = " (hidden)" if getattr(ws, "isSheetHidden", False) else ""
        note = "already ended" if _is_finished_month(stamp) else "current or ahead"
        print(f"  {ws.title:<20}{hidden:<10} {stamp}  {note}")

    if other:
        print(f"\n{len(other)} the sync IGNORES completely (not named as a month):")
        for ws in other:
            print(f"  {ws.title}")
        print("  Nothing is ever read from or written to these. Keep or delete them "
              "as the team prefers.")

    if not months:
        print("\n!! No month tabs at all. The sync would stop with an error.")
        return 1

    template = cfg["template_tab"] or months[max(months)].title
    print(f"\nNew months are created by copying: '{template}'")
    if not cfg["template_tab"]:
        print("  That is simply the LATEST month tab. Deleting it changes the "
              "template without warning, so check this one looks right --")
        try:
            cols = read_checkbox_columns(months[max(months)])
            print(f"  it reports {len(cols)} checkbox column(s)"
                  + (", which is what a good tab looks like." if len(cols) == 4
                     else " -- expected 4 (Assigned, Verified, OUT, IN). Worth a look."))
        except Exception as e:  # noqa: BLE001
            print(f"  (could not read its checkbox rules: {e})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
