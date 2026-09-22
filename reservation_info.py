"""Everything known about one or more reservations, from Guesty and the sheet.

    python reservation_info.py --codes HMKMRDCK9Y,HMPJTAMCDS

STRICTLY READ-ONLY. Reads Guesty and reads the sheet. Writes nothing to either.

Guesty returns a TRIMMED reservation when no `fields` projection is given, so a
generous explicit list is asked for rather than none -- that mistake once made
every field look empty and every gate look failed.

Codes are sent exactly as typed: Guesty matches confirmationCode case
sensitively, and asking for an uppercased form of a mixed-case code returns
nothing at all.
"""
from __future__ import annotations

import argparse
import json
import sys

from guesty_adapter import requested_fields
from guesty_client import fetch_reservations
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import guesty_token, load_config

# Beyond the sync's own projection: everything that tends to matter when someone
# asks "what is going on with this booking".
EXTRA = ("status canceledAt cancelledAt createdAt confirmedAt lastUpdatedAt "
         "checkIn checkOut checkInDateLocalized checkOutDateLocalized "
         "nightsCount guestsCount plannedArrival plannedDeparture "
         "source channel integration tags customFields notes "
         "money guest listing listingId confirmationCode _id")


def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", required=True, help="comma-separated")
    args = ap.parse_args(argv)
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        print("no codes given", file=sys.stderr)
        return 2

    cfg = load_config()
    token = guesty_token(cfg)
    rows = fetch_reservations(token, filters=[
        {"field": "confirmationCode", "operator": "$in", "value": codes},
    ], fields=requested_fields() + " " + EXTRA)
    found = {str(r.get("confirmationCode", "")).strip().upper(): r for r in rows}
    print(f"Guesty returned {len(rows)} reservation(s) for {len(codes)} code(s).")

    for code in codes:
        r = found.get(code.upper())
        print("")
        print("=" * 70)
        print(f"  {code}")
        print("=" * 70)
        if r is None:
            print("  NOT FOUND in Guesty for that confirmation code.")
            print("  (codes are case-sensitive; check the spelling against Guesty)")
        else:
            print("  -- headline --")
            for label, path in (
                ("guest", "guest.fullName"),
                ("listing", "listing.nickname"),
                ("listing id", "listing._id"),
                ("city", "listing.address.city"),
                ("address", "listing.address.full"),
                ("status", "status"),
                ("check-in", "checkInDateLocalized"),
                ("check-out", "checkOutDateLocalized"),
                ("nights", "nightsCount"),
                ("guests", "guestsCount"),
                ("source", "source"),
                ("channel", "integration.platform"),
                ("created", "createdAt"),
                ("confirmed", "confirmedAt"),
                ("cancelled", "canceledAt"),
                ("last updated", "lastUpdatedAt"),
                ("planned arrival", "plannedArrival"),
                ("reservation id", "_id"),
            ):
                v = dig(r, path)
                if v not in (None, "", [], {}):
                    print(f"     {label:<16} {v}")
            print("")
            print("  -- everything Guesty returned --")
            print("     " + json.dumps(r, indent=2, sort_keys=True,
                                       default=str).replace("\n", "\n     "))

        # And what the sheet has for it, tab by tab.
        ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
        print("")
        print("  -- rows on the sheet --")
        hits = 0
        tabs = month_worksheets(ss)
        for key in sorted(tabs):
            ws = tabs[key]
            sheet, _hdr = read_as_dataframe(ws)
            if not len(sheet) or "Confirmation Code" not in sheet.columns:
                continue
            for i, row in sheet.iterrows():
                if str(row.get("Confirmation Code", "")).strip().upper() != code.upper():
                    continue
                hits += 1
                vals = {c: str(row.get(c, "")).strip() for c in sheet.columns}
                vals = {k: v for k, v in vals.items() if v}
                print(f"     {ws.title} row {i + 2}: "
                      + "  ".join(f"{k}={v!r}" for k, v in vals.items()))
        if not hits:
            print("     (no row on any tab carries this code)")
    print("")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
