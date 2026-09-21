"""Every field on a real card, ours beside the team's.

    python shift_fields.py --board 10540759 --mine 2026-10-01 2026-11-01 \
                           --theirs 2026-09-01 2026-10-01

STRICTLY READ-ONLY.

We set `jobId` and Chris still sees the property under Shift Title. Either he is
looking at older cards, or `jobId` is not the field the UI labels "Job". The
team's own cards render the way he wants, so the answer is in the difference
between one of theirs and one of ours -- printed whole, not summarised, because
the field we are missing is by definition one we did not think to look at.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from connecteam_client import ConnecteamClient, check_api_key


def stamp(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--mine", nargs=2, required=True, metavar=("FROM", "TO"))
    ap.add_argument("--theirs", nargs=2, required=True, metavar=("FROM", "TO"))
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))

    out = {}
    for label, (frm, to) in (("OURS", args.mine), ("THEIRS", args.theirs)):
        shifts = client.existing_shifts(args.board, stamp(frm), stamp(to))
        print(f"{label}: {len(shifts)} shift(s) {frm}..{to}")
        out[label] = shifts
        if shifts:
            keys = sorted({k for s in shifts for k in s})
            print(f"   fields present: {keys}")
            print(f"   one card, in full:")
            print("   " + json.dumps(shifts[0], indent=2, sort_keys=True)
                  .replace("\n", "\n   "))
        print()

    a = {k for s in out.get("OURS", []) for k in s}
    b = {k for s in out.get("THEIRS", []) for k in s}
    print(f"fields THEY have and we do not: {sorted(b - a)}")
    print(f"fields WE have and they do not: {sorted(a - b)}")

    # Where does the property actually live on a card that renders correctly?
    print()
    print("on THEIR cards, any field whose value looks like a property name:")
    for s in out.get("THEIRS", [])[:4]:
        for k, v in sorted(s.items()):
            if isinstance(v, str) and any(ch.isdigit() for ch in v) and len(v) < 60:
                print(f"   {k} = {v!r}")
        print("   ---")
    print()
    print("   Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
