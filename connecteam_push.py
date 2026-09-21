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
from connecteam_jobs import build_index, resolve, usable
from connecteam_map import (CITY_SCHEDULERS, TEST_SCHEDULER, scheduler_for,
                            shifts_by_scheduler)
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
    ap.add_argument("--test", action="store_true",
                    help="Send to the test board instead of the city's real one. "
                         "The board has no crew, so nothing reaches a phone.")
    ap.add_argument("--live", action="store_true",
                    help="Actually create the jobs. Without it, nothing is sent. "
                         "Only valid together with --test: see below.")
    args = ap.parse_args(argv)

    # --live WITHOUT --test is refused, here, in code.
    #
    # Chris' instruction, 2026-09-21: "Nothing goes anywhere else except Chris
    # Test until I sign off on testing the live boards." Earlier the same day I
    # asked whether a live Austin write was acceptable, read the answer as
    # standing permission, and pushed 38 cards to the Austin board. It was not
    # that, and they had to be deleted again.
    #
    # A rule that lives in a workflow input is one dispatch away from being
    # picked, and a rule in a comment is worth nothing at all. So the market
    # boards are unreachable from every entry point until this branch is
    # deliberately removed -- which is what "signs off" has to mean.
    if args.live and not args.test:
        print("REFUSED: --live is only allowed with --test.", file=sys.stderr)
        print("         Cards go to Chris Test and nowhere else until Chris "
              "signs off on writing to a market board.", file=sys.stderr)
        print("         Removing this check is that sign-off; nothing else is.",
              file=sys.stderr)
        return 2

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
    # The board's Jobs, so the card can point the property at one. Loaded BEFORE
    # the shifts are built, because a property with no Job produces no card at
    # all -- the property is no longer in the title, so a card without a Job
    # would name no place whatsoever.
    client = ConnecteamClient(key)
    board_for_jobs = TEST_SCHEDULER if args.test else board
    all_jobs, how = client.list_jobs(board_for_jobs)
    # Scoped to the board being written to, and with the soft-deleted dropped.
    # The account-wide list is a superset three times over: 506 of 1429 are
    # deleted, and the rest belong to nine different boards.
    job_index = build_index(all_jobs, board=board_for_jobs)
    print(f"{len(all_jobs)} Job(s) on the account [{how}]; "
          f"{len(usable(all_jobs, board_for_jobs))} usable on board "
          f"{board_for_jobs}; {len(job_index)} distinct name(s).")

    groups = shifts_by_scheduler(rows, only_city=args.city, job_index=job_index)
    jobs = groups.get(board, [])
    in_city = int((rows.get("City", "").map(norm_city) == norm_city(args.city)).sum()) \
        if "City" in rows.columns else 0
    print(f"{in_city} row(s) are {args.city}; {len(jobs)} of them are cleans "
          f"with a Job to point at (a row with no check-out is not a job).")

    # Every property that produced no card, and why. These are the ones the team
    # has to create in Connecteam before their cleans can reach anybody.
    unmatched = sorted({str(r.get("Property", "")).strip()
                        for _i, r in rows.iterrows()
                        if norm_city(r.get("City", "")) == norm_city(args.city)
                        and str(r.get("Check out - Time", "")
                                or r.get("Check-out Time", "")).strip()
                        and resolve(r.get("Property", ""), job_index)[0] is None})
    if unmatched:
        print("")
        print(f"  {len(unmatched)} propertie(s) have NO Job on this board, so "
              f"their cleans are not being sent:")
        for prop in unmatched:
            print(f"     {prop}")
        print("  Create these as Jobs in Connecteam and they are picked up "
              "on the next run.")

    # What each card will actually point at, so the choice can be read before it
    # is made. Where several Jobs matched, the reason says what was passed over.
    shown = {}
    for row, payload in jobs:
        prop = str(row.get("Property", "")).strip()
        if prop not in shown:
            _jid, name, why = resolve(prop, job_index)
            shown[prop] = (name, why)
    if shown:
        print("")
        print(f"  {len(shown)} propertie(s) -> Job:")
        for prop, (name, why) in sorted(shown.items()):
            mark = "  " if why == "one Job matches" else " *"
            print(f"   {mark} {prop:<30} -> {name}")
            if mark == " *":
                print(f"        {why}")
    if not jobs:
        print("Nothing to do.")
        return 0

    payloads = [p for _, p in jobs]
    if args.test:
        # Verify before redirecting. The test board's id has already changed twice --
        # once because a group id was mistaken for it, once because the board was
        # deleted and rebuilt -- so a pinned id that has gone stale must say so here
        # rather than 404 in the middle of a write.
        boards = {str(b.get("schedulerId") or b.get("id")): (b.get("name") or "")
                  for b in client.list_schedulers()}
        if TEST_SCHEDULER not in boards:
            print(f"ERROR: the test board {TEST_SCHEDULER} is not on this account any "
                  f"more.", file=sys.stderr)
            print("       Boards that do exist:", file=sys.stderr)
            for bid, name in sorted(boards.items()):
                print(f"         {bid:<12} {name}", file=sys.stderr)
            return 2
        board = TEST_SCHEDULER
        print(f"TEST BOARD: sending {args.city}'s jobs to {board} "
              f"({boards[board]!r}), not to {args.city}'s own board.")
    print(f"\nBoard {board} ({args.city}) -- "
          + ("CREATING" if args.live else "PREVIEW, nothing will be sent") + ":\n")
    try:
        created = client.create_shifts(
            board, payloads, live=args.live,
            # So the preview names the property, not thirty-eight "Clean"s.
            job_names={str(j.get("jobId") or j.get("id")):
                       (j.get("name") or j.get("title") or "")
                       for j in all_jobs})
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
