"""Put a board back the way it was before a push. The undo.

    python card_restore.py --board 19713722                 # list only
    python card_restore.py --board 19713722 --confirm
    python card_restore.py --board 19713722 --snapshot connecteam/snapshots/...

WRITES with --confirm. Without it, lists and stops.

WHAT IT DOES
------------
A live push stores the board EXACTLY as it was before touching anything
(connecteam_cards.write_snapshot). This reads that back and undoes the
difference:

  * a card on the board now that the snapshot does not have -> the push CREATED
    it, so it is deleted.
  * a card whose colour, times, property or title have moved since the snapshot
    -> the push CHANGED it, so those fields go back.
  * a card in the snapshot that is gone from the board now -> reported and NOT
    recreated. The push has no delete path, so this should be impossible, and
    something that should be impossible is worth saying out loud rather than
    papering over.

WHAT IT WILL NOT TOUCH
----------------------
Only cards that could be OURS, by the same two tests the push uses: the title is
exactly one of ours AND the colour is one of the three we send. The crews' own
cards fail both.

That matters more here than in the push. Between the snapshot and the restore,
the team may have edited their own board -- and an undo that reverts THEIR work
to a state from two hours ago is a worse accident than the one being undone. So
this tool fixes our footprint and nothing else.

A card with anybody assigned to it is refused outright, as everywhere else: that
is somebody's shift.
"""
from __future__ import annotations

import argparse
import os
import sys

from connecteam_cards import ours, read_snapshot
from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import (CANCELLED_COLOR, OUR_TITLES, STANDARD_COLOR,
                            TURNOVER_COLOR)
from sync import load_config, state_store

OUR_COLORS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)

# What a push can change, so what an undo has to put back.
RESTORABLE = ("color", "startTime", "endTime", "jobId", "title")


def differs(before: dict, now: dict) -> dict:
    out = {}
    for f in RESTORABLE:
        if f not in before:
            continue
        a, b = before.get(f), now.get(f)
        if f in ("startTime", "endTime"):
            same = int(a or 0) == int(b or 0)
        else:
            same = str(a or "").upper() == str(b or "").upper()
        if not same:
            out[f] = (b, a)              # (what it is now, what it should be)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True,
                    help="the board to put back. Never defaults.")
    ap.add_argument("--snapshot", default="",
                    help="which snapshot to restore from. Defaults to this "
                         "board's latest.")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    store = state_store(load_config())
    if store is None:
        print("ERROR: no shared-state store is configured (STATE_BUCKET), so "
              "there are no snapshots to restore from.", file=sys.stderr)
        return 2

    snap = read_snapshot(store, args.board, args.snapshot)
    if not snap:
        print(f"ERROR: no snapshot found for board {args.board}"
              + (f" at {args.snapshot}" if args.snapshot else " (latest)"),
              file=sys.stderr)
        return 2

    before = {str(s.get("id")): s for s in (snap.get("shifts") or [])}
    print(f"Snapshot of board {snap.get('board')} taken {snap.get('taken')}")
    print(f"  window {snap.get('window', '?')}, city {snap.get('city', '?')}, "
          f"{len(before)} card(s) on the board at the time")
    print(f"  the push then planned: {snap.get('planned', {})}")
    print("")

    lo = min((int(s.get("startTime") or 0) for s in before.values()), default=0)
    hi = max((int(s.get("endTime") or 0) for s in before.values()), default=0)
    if not before:
        # An empty snapshot means the board had nothing in the window, so
        # everything there now was created by the push. Its own window is the
        # only thing that can bound the read.
        print("The snapshot is empty: the board held nothing in that window, so "
              "every card of ours there now was created by the push.")
        w = str(snap.get("window") or "")
        if ".." not in w:
            print("ERROR: and the snapshot does not record its window, so there "
                  "is no safe range to read. Pass --snapshot for an older one.",
                  file=sys.stderr)
            return 2
        from datetime import datetime, time, timedelta
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Chicago")
        a, b = w.split("..", 1)
        lo = int(datetime.combine(datetime.fromisoformat(a).date(), time(0, 0),
                                  tzinfo=tz).timestamp())
        hi = int(datetime.combine(datetime.fromisoformat(b).date(), time(23, 59),
                                  tzinfo=tz).timestamp())
    else:
        lo, hi = lo - 86400, hi + 86400

    now = {str(s.get("id")): s for s in client.existing_shifts(args.board, lo, hi)}

    to_delete, to_restore, vanished, refused = [], [], [], []
    for sid, card in now.items():
        if sid in before:
            continue
        if not ours(card, OUR_TITLES, OUR_COLORS):
            continue                      # the team's, or made by someone else
        if card.get("assignedUserIds"):
            refused.append((card, "somebody is assigned to it"))
            continue
        to_delete.append(card)
    for sid, was in before.items():
        card = now.get(sid)
        if card is None:
            vanished.append(was)
            continue
        if not ours(card, OUR_TITLES, OUR_COLORS) and not ours(was, OUR_TITLES,
                                                               OUR_COLORS):
            continue
        if card.get("assignedUserIds"):
            diff = differs(was, card)
            if diff:
                refused.append((card, "somebody is assigned to it"))
            continue
        diff = differs(was, card)
        if diff:
            to_restore.append((card, was, diff))

    print(f"{len(to_delete)} card(s) the push CREATED, to delete:")
    for c in to_delete:
        print(f"   {c.get('id')}  {c.get('title')!r}  {c.get('color')}")
    print(f"{len(to_restore)} card(s) the push CHANGED, to put back:")
    for c, _was, diff in to_restore:
        how = ", ".join(f"{k}: {a} -> {b}" for k, (a, b) in sorted(diff.items()))
        print(f"   {c.get('id')}  {how}")
    if vanished:
        print(f"{len(vanished)} card(s) in the snapshot are NOT on the board now. "
              f"The push has no delete path, so this should be impossible:")
        for c in vanished:
            print(f"   {c.get('id')}  {c.get('title')!r}")
    if refused:
        print(f"{len(refused)} card(s) REFUSED:")
        for c, why in refused:
            print(f"   {c.get('id')}  {why}")

    if not to_delete and not to_restore:
        print("")
        print("Nothing of ours differs from the snapshot. The board already "
              "matches it.")
        return 0
    if not args.confirm:
        print("")
        print("--confirm not given; nothing was changed.")
        return 0

    print("")
    bad = 0
    for card in to_delete:
        sid = str(card.get("id"))
        try:
            client._request(
                "DELETE", f"/scheduler/v1/schedulers/{args.board}/shifts/{sid}")
            print(f"   deleted {sid}")
        except ConnecteamError as e:
            print(f"   !! could not delete {sid}: {str(e)[:140]}")
            bad += 1
    for card, was, diff in to_restore:
        fields = {k: was.get(k) for k in diff}
        try:
            client.update_shift(args.board, card, fields)
            print(f"   put back {card.get('id')}: {sorted(diff)}")
        except (ConnecteamError, ValueError) as e:
            print(f"   !! could not restore {card.get('id')}: {str(e)[:140]}")
            bad += 1

    # Read it back. An accepted request is not a changed board.
    after = {str(s.get("id")): s
             for s in client.existing_shifts(args.board, lo, hi)}
    left = [c for c in to_delete if str(c.get("id")) in after]
    wrong = [c for c, was, diff in to_restore
             if differs(was, after.get(str(c.get("id")), {}))]
    print("")
    print(f"{len(to_delete) - len(left)} of {len(to_delete)} deleted, "
          f"{len(to_restore) - len(wrong)} of {len(to_restore)} restored, "
          f"confirmed by reading the board back.")
    if left or wrong:
        print("   !! some cards did not come back:", file=sys.stderr)
        for c in left:
            print(f"      {c.get('id')} still on the board", file=sys.stderr)
        for c in wrong:
            print(f"      {c.get('id')} still differs", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
