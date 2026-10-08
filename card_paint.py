"""Set a card's colour by hand, on the TEST board, to stage a proof.

    python card_paint.py --from 2026-12-19 --to 2026-12-21 --color "#91B282"
    python card_paint.py ... --title-contains Clean --confirm

WRITES with --confirm. THE TEST BOARD ONLY -- there is no --board, by design.

WHY THIS EXISTS
---------------
Some behaviour cannot be proved without putting the board into a state the sheet
would not currently produce. The one that matters is a card going from GREEN to
GREY because its booking was cancelled: the sheet's cancellations already have
grey cards, so the transition never happens on its own, and it is the colour
change a crew is most likely to misread if it goes wrong.

So this paints a card green again, and then the push is run and watched. It is a
test fixture with a real board behind it, which is why it is this narrow:

  * the test board, hardcoded. There is no argument for any other.
  * a date window is required and nothing outside it is read.
  * only a colour the API accepts, checked before any request.
  * a card with anybody assigned to it is refused, as everywhere else.
  * it refuses to touch a card that is not already one of ours, by the same
    title-and-colour test the push uses, so it cannot paint a crew's card even
    on the test board.
  * lists what it will do and stops, unless --confirm.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from connecteam_cards import ours
from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import (ALLOWED_COLORS, CANCELLED_COLOR, OUR_TITLES,
                            STANDARD_COLOR, TEST_SCHEDULER, TURNOVER_COLOR)

OUR_COLORS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)


def stamp(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="frm", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--color", required=True,
                    help="the colour to set; must be one the API accepts")
    ap.add_argument("--title-contains", default="",
                    help="narrow to cards whose title contains this")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    if args.color not in ALLOWED_COLORS:
        print(f"ERROR: {args.color} is not a colour the API accepts. It would be "
              f"refused with error_code 1002.", file=sys.stderr)
        return 2
    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    shifts = client.existing_shifts(TEST_SCHEDULER, stamp(args.frm), stamp(args.to))
    print(f"test board {TEST_SCHEDULER}: {len(shifts)} card(s) in "
          f"{args.frm}..{args.to}")

    todo, skipped = [], []
    for s in shifts:
        title = str(s.get("title") or "")
        if args.title_contains and args.title_contains.lower() not in title.lower():
            continue
        if not ours(s, OUR_TITLES, OUR_COLORS):
            skipped.append((s, "not one of ours (title or colour)"))
            continue
        if s.get("assignedUserIds"):
            skipped.append((s, "somebody is assigned to it"))
            continue
        if str(s.get("color") or "").upper() == args.color.upper():
            continue
        todo.append(s)

    for s, why in skipped:
        print(f"   skipping {str(s.get('id'))[:24]} {str(s.get('title'))!r}: {why}")
    print(f"{len(todo)} card(s) to paint {args.color}:")
    for s in todo:
        when = datetime.fromtimestamp(int(s["startTime"]), timezone.utc)
        print(f"   {when:%d %b %Y %H:%M}Z  {str(s.get('title'))!r}  "
              f"{s.get('color')} -> {args.color}")
    if not todo:
        print("")
        print("Nothing to paint.")
        return 0
    if not args.confirm:
        print("")
        print("--confirm not given; nothing was changed.")
        return 0

    print("")
    bad = 0
    for s in todo:
        try:
            client.update_shift(TEST_SCHEDULER, s, {"color": args.color})
            print(f"   painted {str(s.get('id'))[:24]}")
        except (ConnecteamError, ValueError) as e:
            print(f"   !! {str(s.get('id'))[:24]}: {str(e)[:140]}")
            bad += 1

    after = {str(x.get("id")): x for x in
             client.existing_shifts(TEST_SCHEDULER, stamp(args.frm), stamp(args.to))}
    stuck = [s for s in todo
             if str((after.get(str(s.get("id"))) or {}).get("color", "")).upper()
             != args.color.upper()]
    print("")
    print(f"{len(todo) - len(stuck)} of {len(todo)} painted, confirmed by reading "
          f"the board back.")
    if stuck:
        for s in stuck:
            print(f"   !! {str(s.get('id'))[:24]} did not change", file=sys.stderr)
        return 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
