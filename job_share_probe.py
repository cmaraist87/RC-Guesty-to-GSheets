"""Can a Job be added to a second board, and by which verb?

    python job_share_probe.py --confirm

Touches ONLY the throwaway Job "ZZ Ramos Sync Test" created on 2026-09-21. No
Job the team owns is read into this or written by it.

WHY
---
Job titles are unique account-wide: creating "1163 Webberville A" for the test
board is refused with "one or more parent names already exist", because Austin
already has one. A Job can belong to several boards -- instanceIds is a list and
some Jobs carry two -- so the way to put an Austin property on the test board is
to add the test board to that Job, not to make a second Job.

That would edit a record the team owns, so the verb and body are established
here, on a Job that is ours, before anything is proposed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import TEST_SCHEDULER

MINE = "ZZ Ramos Sync Test"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    allj, _how = client.list_jobs(TEST_SCHEDULER)
    mine = [j for j in allj
            if (j.get("title") or j.get("name")) == MINE and not j.get("isDeleted")]
    if not mine:
        print(f"ERROR: no live Job titled {MINE!r} to experiment on.", file=sys.stderr)
        return 2
    job = mine[0]
    jid = str(job.get("jobId") or job.get("id"))
    print(f"experimenting on OUR job only: {jid}  {job.get('title')!r}")
    print(f"   instanceIds now: {job.get('instanceIds')}")

    # Add a second board to it. Austin is used as the second id purely because
    # it is a real board id; nothing is written TO Austin by this -- a Job's
    # board list is account config, not a card on anyone's schedule. If this
    # succeeds the same verb can put an Austin property onto the test board.
    target = sorted({int(TEST_SCHEDULER), 10540759})
    if not args.confirm:
        print(f"\n--confirm not given. Would try to set instanceIds={target}.")
        return 0

    attempts = [
        ("PUT array", "PUT", "/jobs/v1/jobs",
         [{"jobId": jid, "title": MINE, "instanceIds": target}]),
        ("PATCH array", "PATCH", "/jobs/v1/jobs",
         [{"jobId": jid, "title": MINE, "instanceIds": target}]),
        ("PUT by id", "PUT", f"/jobs/v1/jobs/{jid}",
         {"title": MINE, "instanceIds": target}),
        ("PATCH by id", "PATCH", f"/jobs/v1/jobs/{jid}",
         {"title": MINE, "instanceIds": target}),
    ]
    for label, method, path, body in attempts:
        print(f"\n--- {label}: {method} {path}")
        try:
            got = client._request(method, path, body=body, tries=1)
            print(f"    ACCEPTED: {json.dumps(got)[:300]}")
            return 0
        except ConnecteamError as e:
            print(f"    REJECTED: {str(e)[:220]}")
    print("\nNo update verb answered; a Job cannot be re-boarded through the API.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
