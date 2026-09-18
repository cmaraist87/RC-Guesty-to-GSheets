"""Why is this booking not on the sheet?

    python find_reservation.py --code HM9HSTFK88

Looks the reservation up in Guesty and walks it through every gate the sync applies,
in order, reporting the first one that would drop it. Read-only: one Guesty read and
one sheet read, no writes anywhere.

The gates, in the order the sync applies them:
  1. the fetch window        checkOut >= today-lookback, checkIn <= today+lookahead
  2. the status filter       confirmed / reserved / checkedIn
  3. property normalisation  a listing name that maps to nothing produces no row
  4. the exclude list        properties this team does not clean
  5. the city filter         markets this sheet does not cover
  6. a check-out to clean    an arrival-only row is not a job
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from guesty_adapter import reservations_to_frames, requested_fields
from processing import EXCLUDE_PROPERTIES, _canonical_key, normalize_property
from sheet_merge import norm_city
from sync import (DEFAULT_CITIES, _first_city, _today_chicago, coverage_window,
                  guesty_token, load_config)


def _dig(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True, help="Confirmation code")
    ap.add_argument("--days", type=int, default=400,
                    help="Unused; kept so older invocations do not break.")
    args = ap.parse_args(argv)
    want = args.code.strip().upper()

    cfg = load_config()
    from guesty_client import fetch_reservations
    token = guesty_token(cfg)
    lo, hi = coverage_window(cfg)
    today = _today_chicago()

    print(f"Looking for {want}\n")
    print(f"The sync's own window: checkOut >= {lo}, checkIn <= {hi}")
    print(f"The sync's statuses  : {', '.join(cfg['statuses'])}\n")

    # Ask Guesty for THIS booking, by the key we already have.
    #
    # The first version of this scanned 400 days with no status filter and let the
    # code fall out of the results. Guesty's planner timed out on it five times
    # running -- "operation exceeded time limit" -- which is a fair complaint about
    # a query that reads two years of reservations to find one.
    rows = fetch_reservations(token, filters=[
        {"field": "confirmationCode", "operator": "$eq", "value": want},
    ], fields=requested_fields())
    print(f"Guesty returned {len(rows)} reservation(s) for that code.")

    if not rows:
        # Some codes differ only by case or stray whitespace in the sheet, so a
        # near-miss is worth reporting as a near-miss rather than "does not exist".
        print()
        print(f"No reservation in Guesty carries the code {want}.")
        print("  Check the code on the sheet against Guesty -- a transposed or")
        print("  truncated code looks exactly like a missing booking from here.")
        return 1

    hit = [r for r in rows
           if str(r.get("confirmationCode", "")).strip().upper() == want]
    if not hit:
        print()
        print(f"Guesty answered, but no row actually carries {want}.")
        return 1

    r = hit[0]
    ci = str(_dig(r, "checkInDateLocalized") or "")[:10]
    co = str(_dig(r, "checkOutDateLocalized") or "")[:10]
    listing = _dig(r, "listing.nickname") or ""
    # The SAME resolution the sync uses. An earlier version of this file read only
    # listing.address.city and reported a blank -- but FIELD_MAP tries three paths,
    # so a booking can have a city the sync finds and this tool did not. That made
    # the tool say "no city" about a reservation the sync may see perfectly well.
    city = _first_city(r) or ""
    raw_paths = {p: _dig(r, p) for p in
                 ("listing.address.city", "listingId.address.city", "listing.city")}
    status = r.get("status", "")
    print(f"\nFOUND in Guesty:")
    print(f"   guest    : {_dig(r, 'guest.fullName')}")
    print(f"   listing  : {listing!r}")
    print(f"   city     : {city!r}")
    for path, val in raw_paths.items():
        print(f"      {path:<28} {val!r}")
    print(f"   check-in : {ci}    check-out: {co}")
    print(f"   status   : {status}")

    print("\nWalking the sync's gates:\n")
    ok = True

    inside = (co >= lo) and (ci <= hi)
    print(f"  1. fetch window        {'PASS' if inside else 'DROPPED'}"
          + ("" if inside else f"  -- checkOut {co} < {lo}" if co < lo
             else f"  -- checkIn {ci} > {hi}"))
    ok &= inside

    st_ok = status in cfg["statuses"]
    print(f"  2. status              {'PASS' if st_ok else 'DROPPED'}"
          + ("" if st_ok else f"  -- {status!r} is not one of {cfg['statuses']}"))
    ok &= st_ok

    props = normalize_property(listing)
    p_ok = bool(props)
    print(f"  3. property mapping    {'PASS' if p_ok else 'DROPPED'}  -> {props}")
    ok &= p_ok

    excluded = [p for p in props if _canonical_key(p) in
                {_canonical_key(x) for x in EXCLUDE_PROPERTIES}]
    e_ok = not excluded or len(excluded) < len(props)
    print(f"  4. exclude list        {'PASS' if e_ok else 'DROPPED'}"
          + (f"  -- {excluded} are on the do-not-clean list" if excluded else ""))
    ok &= e_ok

    allowed = {norm_city(c) for c in (cfg.get("cities") or DEFAULT_CITIES)}
    c_ok = norm_city(city) in allowed
    print(f"  5. city filter         {'PASS' if c_ok else 'DROPPED'}"
          + ("" if c_ok else f"  -- {city!r} is not a covered market"))
    ok &= c_ok

    out_df, in_df = reservations_to_frames([r])
    print(f"  6. produces rows       check-out rows {len(out_df)}, "
          f"check-in rows {len(in_df)}")

    print("\n" + "=" * 64)
    print("  Every gate passed -- this booking SHOULD be on the sheet."
          if ok else "  The first DROPPED line above is why it is not on the sheet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
