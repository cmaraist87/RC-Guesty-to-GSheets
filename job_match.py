"""Can every property the sheet schedules be matched to a Connecteam Job?

    python job_match.py --city Austin [--month 2026-10]

STRICTLY READ-ONLY.

The card is moving the property out of the shift title and into the Job field,
which means a shift must carry a `jobId` -- and a jobId has to already exist.
The account has ~1429 Jobs, entered by hand over years, and they are not clean:

    1018 Ferdinan / 1018 Ferdinand (x2) / 1018 Ferdinand V2 (x3)
    1026 N Robert (x2) / 1026 N Robertson / 1026 N Robertson V2
    1100 Ursulines  / Heirloom

So matching cannot be a lookup. This reports, per property, whether there is
exactly one Job, several, or none -- and never picks for you when it is not
certain. What comes out is the list the team needs to tidy, and the size of the
problem before anything is written.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from collections import defaultdict

from connecteam_client import ConnecteamClient, check_api_key
from connecteam_map import scheduler_for
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import load_config


def norm(name: str) -> str:
    """Compare on the part of the name that identifies the property.

    Everything after a slash is somebody's note -- a building, a lockbox, a
    contact -- and a trailing V2/V3 is a listing revision, not a different place.
    """
    s = str(name or "").strip().lower()
    s = s.split("/")[0]
    s = re.sub(r"\bv[0-9]+\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--month", default="", help="YYYY-MM; blank = every future tab")
    args = ap.parse_args(argv)

    cfg = load_config()
    board = scheduler_for(args.city)
    if not board:
        print(f"no Connecteam board mapped for {args.city!r}", file=sys.stderr)
        return 2

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    wanted = []
    for key in sorted(month_worksheets(ss)):
        ws = month_worksheets(ss)[key]
        if args.month and not ws.title.lower().startswith(
                _MONTHS.get(args.month[5:7], "")):
            continue
        rows, _ = read_as_dataframe(ws)
        if not len(rows):
            continue
        for _i, r in rows.iterrows():
            if str(r.get("City", "")).strip().lower() != args.city.lower():
                continue
            checkout = str(r.get("Check out - Time", "")
                           or r.get("Check-out Time", "")).strip()
            if not checkout:
                continue              # arrival-only day: no clean, no card
            prop = str(r.get("Property", "")).strip()
            if prop:
                wanted.append(prop)

    props = sorted(set(wanted))
    print(f"{args.city}: {len(wanted)} job row(s) across {len(props)} distinct "
          f"propertie(s)")

    client = ConnecteamClient(check_api_key(
        os.environ.get("CONNECTEAM_API_KEY", "")))
    jobs, how = client.list_jobs(board)
    print(f"board {board}: {len(jobs)} Job(s) defined   [{how}]")

    by_norm = defaultdict(list)
    for j in jobs:
        name = j.get("name") or j.get("title") or j.get("jobName") or ""
        jid = j.get("jobId") or j.get("id")
        if name and jid:
            by_norm[norm(name)].append((str(jid), name))

    exact, several, missing = [], [], []
    for p in props:
        hits = by_norm.get(norm(p), [])
        if len(hits) == 1:
            exact.append((p, hits[0]))
        elif hits:
            several.append((p, hits))
        else:
            missing.append(p)

    print()
    print(f"  one Job, unambiguous : {len(exact)}")
    print(f"  SEVERAL Jobs match   : {len(several)}")
    print(f"  NO Job at all        : {len(missing)}")

    if several:
        print()
        print("SEVERAL -- the team has to say which one is current:")
        for p, hits in several:
            print(f"   {p}")
            for jid, name in hits:
                print(f"      {jid}  {name}")
    if missing:
        print()
        print("NONE -- nearest existing names, for spotting a typo:")
        keys = list(by_norm)
        for p in missing:
            near = difflib.get_close_matches(norm(p), keys, n=3, cutoff=0.8)
            shown = "; ".join(by_norm[k][0][1] for k in near) or "(nothing close)"
            print(f"   {p:<34} -> {shown}")
    print()
    print("  Nothing was changed.")
    return 0


_MONTHS = {"01": "enero", "02": "febrero", "03": "marzo", "04": "abril",
           "05": "mayo", "06": "junio", "07": "julio", "08": "agosto",
           "09": "septiembre", "10": "octubre", "11": "noviembre",
           "12": "diciembre"}


if __name__ == "__main__":
    raise SystemExit(main())
