"""Make an existing Job usable on a second board, changing nothing else.

    python job_share.py --city Austin --month 2026-10            # list only
    python job_share.py --city Austin --month 2026-10 --confirm
    python job_share.py ... --revert                             # take it back off

Adds the TEST board to a Job's `instanceIds`. It does NOT remove any board, does
NOT move a Job, and puts no card on anybody's schedule.

WHY
---
Job titles are unique account-wide, so the eight Austin properties cannot be
created a second time for the test board: "one or more parent names already
exist". A Job can belong to several boards -- instanceIds is a list and plenty
of them carry two -- so the only way to test an Austin property's card on Chris
Test is to let Chris Test see the Job Austin already has.

CARE
----
PUT /jobs/v1/jobs/{jobId} REPLACES the record. So each Job is read whole, only
instanceIds is touched, and everything else -- colour, groups, sub-jobs, custom
fields, description, GPS -- is written back exactly as found. The result is read
again and compared field by field; anything that moved is reported loudly.

--revert removes the test board again, leaving the Job as it was.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_jobs import build_index, norm, resolve, usable
from connecteam_map import TEST_SCHEDULER
from sheet_merge import norm_city
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import _today_chicago, load_config

# Never written back; the API owns them.
READ_ONLY = {"jobId", "id", "isDeleted"}


def wanted_properties(rows, city: str) -> list[str]:
    out = set()
    for _i, r in rows.iterrows():
        if norm_city(r.get("City", "")) != norm_city(city):
            continue
        if not str(r.get("Check out - Time", "")
                   or r.get("Check-out Time", "")).strip():
            continue
        prop = str(r.get("Property", "")).strip()
        if prop:
            out.add(prop)
    return sorted(out)


def put_instance_ids(client, job: dict, ids: list[int]) -> dict:
    """Write the job back with a new board list and nothing else changed."""
    body = {k: v for k, v in job.items() if k not in READ_ONLY}
    body["instanceIds"] = ids
    jid = str(job.get("jobId") or job.get("id"))
    got = client._request("PUT", f"/jobs/v1/jobs/{jid}", body=body)
    return ((got or {}).get("data") or {}).get("job") or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--month", default=None)
    ap.add_argument("--limit", type=int, default=0,
                    help="only touch this many, for proving it on one first")
    ap.add_argument("--revert", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    tid = int(TEST_SCHEDULER)
    cfg = load_config()
    ym = args.month or _today_chicago().strftime("%Y-%m")
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = month_worksheets(ss).get((int(ym[:4]), int(ym[5:7])))
    if ws is None:
        print(f"ERROR: no tab for {ym}.", file=sys.stderr)
        return 2
    rows, _ = read_as_dataframe(ws)
    props = wanted_properties(rows, args.city)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    all_jobs, _how = client.list_jobs(TEST_SCHEDULER)
    live = usable(all_jobs)                       # deleted ones are never touched
    by_norm = {}
    for j in live:
        by_norm.setdefault(norm(j.get("title") or j.get("name") or ""), []).append(j)

    on_test = build_index(all_jobs, board=TEST_SCHEDULER)
    todo = []
    for p in props:
        if not args.revert and resolve(p, on_test)[0]:
            continue                              # already usable on the test board
        cands = by_norm.get(norm(p)) or []
        if not cands:
            continue
        best = max(cands, key=lambda j: str(j.get("title", "")))
        ids = [int(i) for i in (best.get("instanceIds") or [])]
        if args.revert and tid not in ids:
            continue
        todo.append((p, best, ids))

    verb = "remove the test board from" if args.revert else "add the test board to"
    print(f"{args.city} {ym}: {len(todo)} Job(s) to {verb}:")
    for p, j, ids in todo:
        print(f"   {p:<30} {j.get('title')!r}  instanceIds {ids}")
    if args.limit:
        todo = todo[:args.limit]
        print(f"   (limited to the first {len(todo)})")
    if not todo:
        print("\nNothing to do.")
        return 0
    if not args.confirm:
        print("\n--confirm not given; nothing was changed.")
        return 0

    bad = 0
    for p, job, ids in todo:
        new_ids = ([i for i in ids if i != tid] if args.revert
                   else sorted(set(ids) | {tid}))
        before = {k: v for k, v in job.items() if k not in READ_ONLY
                  and k != "instanceIds"}
        try:
            got = put_instance_ids(client, job, new_ids)
        except ConnecteamError as e:
            print(f"   !! {p}: {str(e)[:160]}")
            bad += 1
            continue
        after_ids = [int(i) for i in (got.get("instanceIds") or [])]
        moved = {k: (v, got.get(k)) for k, v in before.items()
                 if k in got and got.get(k) != v}
        ok = (tid in after_ids) != bool(args.revert)
        print(f"   {'ok ' if ok and not moved else '!! '}{p:<30} "
              f"instanceIds -> {after_ids}")
        if moved:
            bad += 1
            print(f"       FIELDS CHANGED THAT SHOULD NOT HAVE: "
                  f"{json.dumps(moved, default=str)[:300]}")
    print("")
    print(f"{len(todo) - bad} of {len(todo)} Job(s) updated cleanly.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
