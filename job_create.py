"""Create the Jobs a board is missing, so cards can point at them.

    python job_create.py --city Austin --month 2026-10            # list only
    python job_create.py --city Austin --month 2026-10 --confirm  # create them

WRITES to the TEST board only. A market board's Jobs are the team's own and are
not ours to add to; the same rule that governs cards governs these.

WHY
---
A card carries its property as a `jobId`, and that Job has to exist on the board
being written to. Chris Test owned 3 Jobs and none of them were Austin
properties, so every Austin card resolved to nothing and the board sat empty.

Verified 2026-09-21: POST /jobs/v1/jobs takes an ARRAY, the Job's name field is
`title`, and `instanceIds` is the list of boards it belongs to.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_jobs import resolve, build_index
from connecteam_map import TEST_SCHEDULER
from sheet_merge import norm_city
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import _today_chicago, load_config


def missing_for(rows, city: str, index) -> list[str]:
    """Properties with a clean in this month and no Job on the board."""
    out = set()
    for _i, r in rows.iterrows():
        if norm_city(r.get("City", "")) != norm_city(city):
            continue
        if not str(r.get("Check out - Time", "")
                   or r.get("Check-out Time", "")).strip():
            continue                      # arrival-only day: no clean, no card
        prop = str(r.get("Property", "")).strip()
        if prop and resolve(prop, index)[0] is None:
            out.add(prop)
    return sorted(out)


def create_jobs(client, board: str, names):
    """(created, [(name, why not)]) -- one request PER NAME, deliberately.

    Job titles are unique account-wide, including against the 506 soft-deleted
    ones, so a name Austin already uses cannot be created again. Sent as a batch
    the whole request is refused with "one or more parent names already exist"
    and nothing says which, so the names that ARE free never get created. One
    call per name costs a little more and says exactly where we stand.
    """
    made, refused = [], []
    for n in names:
        body = [{"title": n, "instanceIds": [int(board)]}]
        try:
            got = client._request("POST", "/jobs/v1/jobs", body=body, tries=1)
        except ConnecteamError as e:
            detail = str(e)
            why = ("the name is already taken account-wide"
                   if "already exist" in detail else detail[:120])
            refused.append((n, why))
            continue
        rows = ((got or {}).get("data") or {}).get("jobs") or client._rows(got) or []
        made.extend(rows)
    return made, refused



def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--month", default=None, metavar="YYYY-MM")
    ap.add_argument("--board", default=TEST_SCHEDULER,
                    help="defaults to the test board; a market board is refused")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    if str(args.board) != TEST_SCHEDULER:
        print(f"REFUSED: Jobs are only created on the test board "
              f"({TEST_SCHEDULER}). A market board's Jobs are the team's.",
              file=sys.stderr)
        return 2

    cfg = load_config()
    ym = args.month or _today_chicago().strftime("%Y-%m")
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = month_worksheets(ss).get((int(ym[:4]), int(ym[5:7])))
    if ws is None:
        print(f"ERROR: no tab for {ym}.", file=sys.stderr)
        return 2
    rows, _ = read_as_dataframe(ws)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    all_jobs, how = client.list_jobs(args.board)
    index = build_index(all_jobs, board=args.board)
    print(f"board {args.board}: {len(index)} distinct Job name(s) usable [{how}]")

    names = missing_for(rows, args.city, index)
    print(f"{args.city} {ym}: {len(names)} propertie(s) need a Job here:")
    for n in names:
        print(f"   {n}")
    if not names:
        print("\nNothing to create.")
        return 0
    if not args.confirm:
        print("\n--confirm not given; nothing was created.")
        return 0

    made, refused = create_jobs(client, args.board, names)
    print("")
    print(f"Created {len(made)} Job(s) on board {args.board}:")
    for j in made:
        print(f"   {j.get('jobId')}  {j.get('title')}")
    if refused:
        print("")
        print(f"{len(refused)} could NOT be created:")
        for n, why in refused:
            print(f"   {n:<30} {why}")
        print("   A name Austin already uses cannot be created a second time.")
        print("   Those Jobs have to be SHARED onto this board instead, by")
        print("   adding this board to their instanceIds -- which edits a")
        print("   record the team owns, so it is not done here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
