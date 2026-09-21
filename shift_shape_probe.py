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
        ("no_end",
         "no endTime at all -- ANSWERED 2026-09-21: rejected, Field required",
         dict(base, title="PROBE no end", startTime=at(8))),
        # endTime cannot be dropped, so the question becomes how SHORT it may be.
        # A card that reads as a start time rather than a window is the nearest
        # thing to what was asked for: the crew sees when to be there, and the
        # block does not imply how long a property should take.
        ("zero_length",
         "endTime EQUAL to startTime -- a card with no duration at all",
         dict(base, title="PROBE zero", startTime=at(10), endTime=at(10))),
        ("fifteen_min",
         "a 15-minute block -- ANSWERED 2026-09-21: accepted",
         dict(base, title="PROBE 15min", startTime=at(12),
              endTime=at(12) + 900)),
        # The title question on its own. The earlier attempt bundled it with a
        # jobId and failed on the jobId, so it proved nothing. Once the property
        # moves to the Job field there is nothing left for the title to say.
        ("empty_title",
         "title sent as an empty string, no jobId in the way",
         dict(base, title="", startTime=at(14), endTime=at(14) + 900)),
        ("no_title_key",
         "title omitted entirely, no jobId in the way",
         dict(base, startTime=at(16), endTime=at(16) + 900)),
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

    # Which Jobs does THIS board accept? /jobs/v1/jobs answers for the whole
    # account -- 1429 of them -- but the test board rejected one of those with
    # "job_id ... does not exist", so Jobs are scoped to a board and the
    # account-wide list is the wrong question. Try the board-scoped paths too.
    print("Job lists, by path:")
    job_id = ""
    for path in (f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/jobs",
                 "/scheduler/v1/jobs",
                 "/jobs/v1/jobs"):
        try:
            rows = client._rows(client._request("GET", path + "?limit=100", tries=1))
        except ConnecteamError as e:
            print(f"   {path:<52} -> {str(e).split('HTTP ')[-1][:3]}")
            continue
        print(f"   {path:<52} -> {len(rows)} job(s)")
        for j in rows[:5]:
            print(f"        {j.get('jobId') or j.get('id')}  "
                  f"{j.get('name') or j.get('title') or '?'}")
        if rows and not job_id and "schedulers" in path:
            job_id = str(rows[0].get("jobId") or rows[0].get("id"))
    if not job_id:
        print("   no board-scoped Job found; jobId shapes will be skipped.")
    print()

    # Is the test board rejecting THAT Job, or every Job?
    #
    # The one tried on 2026-09-21 came from /jobs/v1/jobs, which answers for the
    # whole account, and the board said "does not exist". That is one data point
    # with one Job, and it could as easily mean the Job is archived as that Jobs
    # belong to boards. A jobId Connecteam itself put on the Austin board settles
    # it: if the test board refuses that too, Jobs are board-scoped and the test
    # board needs its own before it can test anything.
    from connecteam_map import CITY_SCHEDULERS
    austin = CITY_SCHEDULERS.get("austin") or CITY_SCHEDULERS.get("Austin")
    in_use = ""
    if austin:
        try:
            live_shifts = client.existing_shifts(austin, 1_756_684_800, 1_798_761_600)
            in_use = next((str(sh["jobId"]) for sh in live_shifts if sh.get("jobId")), "")
        except ConnecteamError as e:
            print(f"   (could not read the Austin board: {e})")
    if in_use:
        print(f"a Job in use on the Austin board: {in_use}")
        probe = {"timezone": TZ, "isOpenShift": True, "assignedUserIds": [],
                 "openSpots": 1, "isPublished": True, "title": "PROBE austin job",
                 "jobId": in_use,
                 "startTime": int((WHEN + timedelta(hours=20)).timestamp()),
                 "endTime": int((WHEN + timedelta(hours=20)).timestamp()) + 900}
        print("--- austin_job_on_test_board: is the Job valid anywhere, or only "
              "on its own board?")
        if args.confirm:
            try:
                client._request(
                    "POST", f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/shifts",
                    body=[probe], tries=1)
                print("    ACCEPTED -> Jobs are account-wide; the earlier refusal "
                      "was about that particular Job, not the board.")
            except ConnecteamError as e:
                print(f"    REJECTED -> {str(e)[:300]}")
                print("    Jobs are scoped to a board. The test board needs its "
                      "own before it can test a jobId.")
    print()

    for label, why, payload in shapes(job_id):
        if "jobId" in payload and not job_id:
            continue                    # nothing valid to point at on this board
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
