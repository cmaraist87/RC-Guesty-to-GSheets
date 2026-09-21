"""What is actually sitting on a board right now, card by card.

    python board_peek.py --board 19713722 --from 2026-09-01 --to 2027-12-31

STRICTLY READ-ONLY.

Two questions this answers before a first live write:

  * the test board still shows the property under Shift Title, because the 37
    cards on it were written on 2026-09-13 under the old design. Nothing with
    the new shape has been pushed anywhere. This shows which is which.
  * the Austin board already carries the team's own cards. Ours are matched for
    duplicates on (jobId, title, startTime), so a card the team wrote by hand
    with a different title would not collide -- and a crew would see the clean
    twice. This lists what is there so that can be judged, not assumed.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, check_api_key

TZ = ZoneInfo("America/Chicago")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args(argv)

    lo = int(datetime.strptime(args.frm, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.strptime(args.to, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    shifts = client.existing_shifts(args.board, lo, hi)
    print(f"board {args.board}: {len(shifts)} shift(s) between {args.frm} and {args.to}")

    with_job = sum(1 for s in shifts if s.get("jobId"))
    titled = sum(1 for s in shifts if str(s.get("title", "")).strip())
    print(f"   carrying a jobId : {with_job}")
    print(f"   carrying a title : {titled}")

    kinds = Counter(str(s.get("title", "")).strip() or "(no title)" for s in shifts)
    print("   titles in use:")
    for t, n in kinds.most_common(12):
        print(f"      {n:>4}  {t}")

    print()
    print("   most recent cards:")
    for s in sorted(shifts, key=lambda x: int(x.get("startTime", 0)))[-args.limit:]:
        start = datetime.fromtimestamp(int(s.get("startTime", 0)), TZ)
        end = datetime.fromtimestamp(int(s.get("endTime", 0)), TZ)
        print(f"      {start:%a %d %b %Y %H:%M}-{end:%H:%M}  "
              f"job={str(s.get('jobId') or '-')[:8]:<8} "
              f"title={str(s.get('title', '')).strip()!r}")
    print()
    print("   Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
