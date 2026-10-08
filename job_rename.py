"""Strip the Guesty version suffix from a Job's name. Preview by default.

    python job_rename.py --board 10540759
    python job_rename.py --board 10540759 --confirm

READ-ONLY without --confirm.

The Job is what Connecteam shows in the "Job" field on a card, and several carry
a Guesty listing-version marker that leaked in when they were named: "1163
webberville A V2", "2903 E 3rd  A  V2", "6504 Porter B v2". Chris asked on
2026-10-07 for just the address: number, street, unit.

READ THIS BEFORE RUNNING IT WITH --confirm
------------------------------------------
A Job is ONE account-wide record with a LIST of boards (`instanceIds`). It is not
per board. So a rename cannot be tried on Chris Test and judged there: the same
record is what Austin's crews see, and what the TEAM'S OWN cards point at. Four
of the six cards on Austin's board in the current window point at Jobs this would
rename.

And Job titles are unique account-wide, including against SOFT-DELETED Jobs --
506 of this account's 1429 are deleted and still hold their names. So a rename to
a name already in use is refused, and this lists those collisions rather than
attempting them.

Nothing is renamed without --confirm, and a collision is never renamed at all.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_jobs import norm, usable

# A trailing Guesty listing-version marker: "V1", "V2", "v2", "U V1".
VERSION_TAIL = re.compile(r"\s+[Uu]?\s*[Vv]\d+\s*$")


def clean_name(name: str) -> str:
    """The name with the version marker gone and the spacing tidied.

    Also collapses runs of spaces, because at least one Job is literally
    "2903 E 3rd  A  V2" and leaving double spaces behind would be a new mess
    rather than a fix.
    """
    out = VERSION_TAIL.sub("", str(name or ""))
    return re.sub(r"\s+", " ", out).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True,
                    help="which board's Jobs to consider. The RENAME is still "
                         "account-wide; this only chooses the list.")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    all_jobs, how = client.list_jobs(args.board)
    live = usable(all_jobs, args.board)
    print(f"{len(all_jobs)} Job(s) on the account [{how}]; "
          f"{len(live)} usable on board {args.board}")

    # Every name in use ANYWHERE, deleted Jobs included -- they still hold their
    # names and still block a rename.
    taken = {}
    for j in all_jobs:
        nm = str(j.get("name") or j.get("title") or "")
        if nm:
            taken.setdefault(norm(nm), []).append(
                (str(j.get("jobId") or j.get("id")), bool(j.get("isDeleted"))))

    todo, blocked = [], []
    for j in live:
        jid = str(j.get("jobId") or j.get("id"))
        was = str(j.get("name") or j.get("title") or "")
        want = clean_name(was)
        if not want or want == was:
            continue
        holders = [(i, d) for i, d in taken.get(norm(want), []) if i != jid]
        if holders:
            blocked.append((jid, was, want, holders))
        else:
            todo.append((jid, was, want, j))

    print("")
    print(f"{len(todo)} Job(s) can be renamed:")
    for jid, was, want, _j in todo:
        print(f"   {jid[:12]}  {was!r}")
        print(f"                 -> {want!r}")
    if blocked:
        print("")
        print(f"{len(blocked)} Job(s) CANNOT be renamed -- the name is already "
              f"taken account-wide:")
        for jid, was, want, holders in blocked:
            who = ", ".join(f"{i[:12]}{' (deleted)' if d else ''}"
                            for i, d in holders)
            print(f"   {was!r} -> {want!r}  held by {who}")
        print("   A deleted Job still holds its name. These need the team to "
              "rename or purge the holder first.")
    if not todo:
        print("")
        print("Nothing to rename.")
        return 0
    if not args.confirm:
        print("")
        print("--confirm not given; nothing was renamed.")
        print("NOTE: a rename is ACCOUNT-WIDE. It changes what every board shows, "
              "including cards the team made themselves.")
        return 0

    print("")
    bad = 0
    for jid, was, want, j in todo:
        body = {k: v for k, v in j.items()
                if k not in ("jobId", "id", "isDeleted")}
        # Both keys: the API has answered to each on this account, and sending
        # only one has silently left the other in place.
        for key in ("name", "title"):
            if key in body:
                body[key] = want
        try:
            client._request("PUT", f"/jobs/v1/jobs/{jid}", body=body)
        except ConnecteamError as e:
            print(f"   !! {was!r}: {str(e)[:150]}")
            bad += 1
            continue
        print(f"   renamed {was!r} -> {want!r}")

    # Read the list back. An accepted request is not a renamed Job.
    after, _how = client.list_jobs(args.board)
    by_id = {str(j.get("jobId") or j.get("id")): j for j in after}
    stuck = []
    for jid, was, want, _j in todo:
        got = by_id.get(jid) or {}
        now = str(got.get("name") or got.get("title") or "")
        if norm(now) != norm(want):
            stuck.append((jid, was, want, now))
    print("")
    print(f"{len(todo) - len(stuck)} of {len(todo)} renamed, confirmed by reading "
          f"the Job list back.")
    if stuck:
        for jid, was, want, now in stuck:
            print(f"   !! {jid[:12]} is {now!r}, wanted {want!r}", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
