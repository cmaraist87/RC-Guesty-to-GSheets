"""What a Connecteam Job actually looks like, and whether we can make one.

    python job_fields.py                 # read only
    python job_fields.py --create        # try to CREATE a Job for the test board

The test board owns no Jobs, so a card carrying a property cannot be written
there -- which left it empty after the old-design cards were cleared. Either a
Job object says which board it belongs to (in which case the account-wide list
can be filtered, and maybe one can be created for the test board), or it does
not and the team has to add them in the UI.

The full object is printed because the field that answers this is by definition
one nobody thought to ask for.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import CITY_SCHEDULERS, TEST_SCHEDULER


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))

    rows = client._rows(client._request("GET", "/jobs/v1/jobs?limit=3", tries=1))
    print(f"{len(rows)} Job(s) sampled from /jobs/v1/jobs")
    for j in rows[:2]:
        print(json.dumps(j, indent=2, sort_keys=True))
        print("---")
    keys = sorted({k for j in rows for k in j})
    print(f"fields on a Job: {keys}")

    # Does anything on a Job name a board?
    hints = [k for k in keys
             if any(w in k.lower() for w in ("schedul", "board", "team", "group"))]
    print(f"fields that might name a board: {hints or 'NONE'}")
    for h in hints:
        print(f"   {h}: {[j.get(h) for j in rows]}")
    print(f"   (test board is {TEST_SCHEDULER}; markets are "
          f"{sorted(set(CITY_SCHEDULERS.values()))})")

    # How many Jobs each board actually owns, once the deleted ones are dropped.
    print("")
    print("Counting every Job on the account, by board...")
    allj, how = client.list_jobs(TEST_SCHEDULER)
    print(f"   {len(allj)} Job(s) via {how}")
    live = [j for j in allj if not j.get("isDeleted")]
    print(f"   {len(allj) - len(live)} are soft-DELETED and unusable")
    per = {}
    for j in live:
        for iid in (j.get("instanceIds") or []):
            per.setdefault(str(iid), []).append(j)
    names = {**{v: k for k, v in CITY_SCHEDULERS.items()},
             TEST_SCHEDULER: "CHRIS TEST"}
    for bid, js in sorted(per.items(), key=lambda kv: -len(kv[1])):
        print(f"   board {bid:<10} {len(js):>5} Job(s)   {names.get(bid, '')}")
    print(f"   Jobs usable on the TEST board {TEST_SCHEDULER}: "
          f"{len(per.get(TEST_SCHEDULER, []))}")

    if not args.create:
        print("\n--create not given; nothing was created.")
        return 0

    # Can a Job be created at all, and can it be aimed at a board?
    for label, path, body in (
        ("with schedulerId", "/jobs/v1/jobs",
         {"name": "ZZ TEST Ramos Sync", "schedulerId": TEST_SCHEDULER}),
        ("plain", "/jobs/v1/jobs", {"name": "ZZ TEST Ramos Sync"}),
    ):
        print(f"\n--- create {label}")
        try:
            got = client._request("POST", path, body=body, tries=1)
            print(f"    ACCEPTED: {json.dumps(got)[:400]}")
            return 0
        except ConnecteamError as e:
            print(f"    REJECTED: {str(e)[:300]}")
    print("\nNo Job could be created through the API.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
