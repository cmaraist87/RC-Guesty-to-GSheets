"""What shape of shift will Connecteam actually accept?

    python shift_shape_probe.py            # show the payloads, send nothing
    python shift_shape_probe.py --confirm  # send them to the TEST board only

WRITES to the test board (19713722) with --confirm, and nowhere else. The board
id is verified against the account before anything is sent.

WHY
---
The card is changing: the property moves out of the shift title into the Job
field, and the end time goes away so a big property and a small one are not
forced into the same block. Both are guesses about the API until it answers:

  * a shift may or may not be accepted with no `endTime`
  * a shift may or may not be accepted with an empty `title`
  * `jobId` has to be a Job that already exists

So ask it. One shift per shape, each an hour apart on a date far enough out that
nobody mistakes it for work, and the server's own reply printed back -- including
what it stored for endTime when we did not send one.

These shifts are NOT cleaned up: there is no delete path in this repo yet. They
sit on the test board until someone removes them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import TEST_SCHEDULER

TZ = "America/Chicago"
WHEN = datetime(2027, 6, 1, 11, 0, tzinfo=ZoneInfo(TZ))


def shapes(job_id: str):
    """(name, what it is testing, payload) for each shape worth asking about."""
    def at(hours):
        s = WHEN + timedelta(hours=hours)
        return int(s.timestamp())

    base = {"timezone": TZ, "isOpenShift": True, "assignedUserIds": [],
            "openSpots": 1, "isPublished": True}
    return [
        ("baseline",
         "title + start + end -- what we send today, as a control",
         dict(base, title="PROBE baseline", startTime=at(0),
              endTime=at(0) + 3600)),
        ("job_and_title",
         "does jobId get accepted at all, alongside a title",
         dict(base, title="PROBE job+title", jobId=job_id, startTime=at(2),
              endTime=at(2) + 3600)),
        ("job_empty_title",
         "can the title be empty once the Job carries the property",
         dict(base, title="", jobId=job_id, startTime=at(4),
              endTime=at(4) + 3600)),
        ("job_no_title_key",
         "same, but omitting `title` rather than sending it empty",
         dict(base, jobId=job_id, startTime=at(6), endTime=at(6) + 3600)),
        ("job_no_end",
         "THE ONE THAT MATTERS: jobId, no title, and no endTime",
         dict(base, jobId=job_id, startTime=at(8))),
        ("title_no_end",
         "no endTime with a title, to separate the two variables",
         dict(base, title="PROBE no end", startTime=at(10))),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))

    boards = {str(b.get("id") or b.get("schedulerId")): b
              for b in client.list_schedulers()}
    if TEST_SCHEDULER not in boards:
        print(f"ERROR: test board {TEST_SCHEDULER} is not on this account.",
              file=sys.stderr)
        return 2
    name = boards[TEST_SCHEDULER].get("name", "?")
    print(f"TEST BOARD {TEST_SCHEDULER} ({name})")

    jobs, how = client.list_jobs(TEST_SCHEDULER)
    if not jobs:
        print(f"ERROR: no Jobs readable ({how}); cannot test jobId.", file=sys.stderr)
        return 2
    job = jobs[0]
    job_id = str(job.get("jobId") or job.get("id"))
    job_name = job.get("name") or job.get("title") or "?"
    print(f"using Job {job_id}  ({job_name})")
    print()

    for label, why, payload in shapes(job_id):
        print(f"--- {label}: {why}")
        print(f"    sending: {json.dumps(payload, sort_keys=True)}")
        if not args.confirm:
            continue
        try:
            # A bare ARRAY, the way create_shifts sends it. Wrapping it in
            # {"shifts": [...]} is rejected with error_code 1002 -- which the
            # baseline shape caught, since that one is known to work.
            got = client._request(
                "POST", f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/shifts",
                body=[payload], tries=1)
        except ConnecteamError as e:
            print(f"    REJECTED: {str(e)[:600]}")
            continue
        rows = client._rows(got) or []
        if not rows:
            print(f"    accepted, but nothing came back: {json.dumps(got)[:300]}")
            continue
        r = rows[0]
        kept = {k: r.get(k) for k in
                ("id", "shiftId", "title", "jobId", "startTime", "endTime")}
        print(f"    ACCEPTED: {json.dumps(kept, sort_keys=True)}")
        if payload.get("endTime") is None and r.get("endTime"):
            print(f"    NOTE: we sent no endTime and the server stored "
                  f"{r.get('endTime')} -- it fills one in.")
    if not args.confirm:
        print()
        print("--confirm not given; nothing was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
