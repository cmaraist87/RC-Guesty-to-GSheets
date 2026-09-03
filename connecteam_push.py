"""
Turn one month of one city's schedule rows into Connecteam jobs.

By default it CREATES NOTHING. It reads the sheet, works out the jobs, and prints
them. Writing needs --live, and one city at a time, because a job card that reaches
a crew's phone cannot be taken back the way a sheet cell can.

    python connecteam_push.py --city Austin
    python connecteam_push.py --city Austin --month 2026-09
    python connecteam_push.py --city Austin --live        # after reading the list

Needs CONNECTEAM_API_KEY, plus the same SHEET_ID / GOOGLE_SA_JSON the sync uses.
On the office machine HTTPS also needs the rebuilt Windows CA bundle:
    $env:REQUESTS_CA_BUNDLE = "$HOME\\win-ca-bundle.pem"
"""
from __future__ import annotations

import argparse
import os
import sys

from connecteam_client import ConnecteamClient, ConnecteamError
from connecteam_map import CITY_SCHEDULERS, scheduler_for, shifts_by_scheduler
from sheet_merge import norm_city
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import _spanish_tab, _today_chicago, load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", required=True,
                    help="One market at a time: " + ", ".join(sorted(CITY_SCHEDULERS)))
    ap.add_argument("--month", default=None, metavar="YYYY-MM",
                    help="Which month tab to read. Defaults to the current month.")
    ap.add_argument("--live", action="store_true",
                    help="Actually create the jobs. Without it, nothing is sent.")
    args = ap.parse_args(argv)

    board = scheduler_for(args.city)
    if board is None:
        print(f"ERROR: '{args.city}' is not one of the covered markets.", file=sys.stderr)
        print("       Covered: " + ", ".join(sorted(CITY_SCHEDULERS)), file=sys.stderr)
        return 2

    key = os.environ.get("CONNECTEAM_API_KEY", "").strip()
    if not key:
        print("ERROR: CONNECTEAM_API_KEY is not set.", file=sys.stderr)
        return 2

    ym = args.month or _today_chicago().strftime("%Y-%m")
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    ws = tabs.get((int(ym[:4]), int(ym[5:7])))
    if ws is None:
        print(f"ERROR: no tab for {ym} (expected '{_spanish_tab(ym)}').", file=sys.stderr)
        return 2

    rows, _ = read_as_dataframe(ws)
    print(f"Read {len(rows)} row(s) from '{ws.title}'.")

    # The sheet holds every market; take only the one being switched on. Thunderbolt
    # and Savannah share a board but are separate markets, so this is by CITY, not
    # by board -- one can be proved before the other goes anywhere near it.
    groups = shifts_by_scheduler(rows, only_city=args.city)
    jobs = groups.get(board, [])
    in_city = int((rows.get("City", "").map(norm_city) == norm_city(args.city)).sum()) \
        if "City" in rows.columns else 0
    print(f"{in_city} row(s) are {args.city}; {len(jobs)} of them are cleans "
          f"(a row with no check-out is not a job).")
    if not jobs:
        print("Nothing to do.")
        return 0

    payloads = [p for _, p in jobs]
    client = ConnecteamClient(key)
    print(f"\nBoard {board} ({args.city}) -- "
          + ("CREATING" if args.live else "PREVIEW, nothing will be sent") + ":\n")
    try:
        created = client.create_shifts(board, payloads, live=args.live)
    except ConnecteamError as e:
        print(f"\n!! {e}", file=sys.stderr)
        return 1
    except ValueError as e:            # the unassigned gate
        print(f"\n!! REFUSED: {e}", file=sys.stderr)
        return 1

    if args.live:
        print(f"\nCreated {len(created)} job(s) in {args.city}, all Unassigned.")
    else:
        print("\nNothing was created. Re-run with --live once this list looks right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
