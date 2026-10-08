"""Which bookings are grey on a board, and why. Read-only.

    python cancelled_cards.py --city Savannah --start 2026-10-12
    python cancelled_cards.py --city Austin

STRICTLY READ-ONLY. No board is written to and no Guesty token is spent.

Answers one question: the grey cards say a booking was cancelled -- is that true?
It lists each one with its confirmation code, property and date, so the codes can
be checked in Guesty directly.

A booking counts as cancelled here when EVERY row carrying its confirmation code
is struck through and it appears live nowhere in the tabs read. That distinction
is the whole point: the sheet strikes a row for a MOVE as well as for a
cancellation, so a struck row on its own proves nothing. Only the code does.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from connecteam_cards import booking_activity, cards_for_tab, code_of
from connecteam_map import (CANCELLED_COLOR, WINDOW_DAYS, months_in_window,
                            scheduler_for, timezone_for, window_bounds)
from sheet_merge import norm_city
from sheets_client import (month_worksheets, open_spreadsheet, read_as_dataframe,
                           read_row_marks)
from sync import _today_chicago, load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", required=True)
    ap.add_argument("--start", default="", metavar="YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    args = ap.parse_args(argv)

    board = scheduler_for(args.city)
    if board is None:
        print(f"ERROR: '{args.city}' is not a covered market.", file=sys.stderr)
        return 2
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    today = _today_chicago()
    floor = None
    if args.start:
        floor = date(int(args.start[:4]), int(args.start[5:7]),
                     int(args.start[8:10]))
    first, last = window_bounds(today, args.days, floor)
    tz = ZoneInfo(timezone_for(args.city))
    lo = int(datetime.combine(first, time(0, 0), tzinfo=tz).timestamp())
    hi = int(datetime.combine(last, time(23, 59, 59), tzinfo=tz).timestamp())
    print(f"{args.city}, board {board}, window {first} to {last}")

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    read_tabs = []
    for (y, m) in months_in_window(first, last):
        ws = tabs.get((y, m))
        if ws is None:
            continue
        frame, _h = read_as_dataframe(ws)
        struck, _hl, _a = read_row_marks(ws)
        read_tabs.append((ws.title, frame, struck))
    if not read_tabs:
        print("No tabs in that window.")
        return 0

    # The Job index, because a cancelled clean at a property with NO Job never
    # became a card at all. Without this the report counted sixteen "grey cards"
    # on a board that holds six -- ten of them at 311 W York, 31 Congress and
    # 2 Ashlyn, none of which has a Job. A report that does not match the board
    # is worse than none when the question is "is what I am looking at right?".
    import os
    from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
    from connecteam_jobs import build_index
    job_index = None
    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
        all_jobs, _how = client.list_jobs(board)
        job_index = build_index(all_jobs, board=board)
    except ConnecteamError as e:
        print(f"WARNING: could not read the board's Jobs ({e}).", file=sys.stderr)
        print("         Listing every cancelled CLEAN instead, including ones "
              "that have no card.", file=sys.stderr)

    active = booking_activity([(f, st) for _t, f, st in read_tabs])
    dead = sum(1 for v in active.values() if not v)
    print(f"{len(active)} booking(s) across {len(read_tabs)} tab(s); "
          f"{dead} cancelled everywhere")
    print("")

    rows = []
    for title, frame, struck in read_tabs:
        if not len(frame) or "City" not in frame.columns:
            continue
        mine = frame[frame["City"].map(norm_city) == norm_city(args.city)]
        for row, code, payload in cards_for_tab(mine, struck, active,
                                                job_index=job_index,
                                                grey_cancellations=True):
            if not (lo <= int(payload["startTime"]) <= hi):
                continue
            if str(payload.get("color", "")).upper() != CANCELLED_COLOR.upper():
                continue
            rows.append((title, str(row.get("Date", ""))[:10], code,
                         str(row.get("Property", "")).strip(),
                         str(row.get("Guest", "")).strip(),
                         str(row.get("Check out - Time", "")
                             or row.get("Check-out Time", "")).strip()))

    if not rows:
        print("No grey cards in this window: no cancelled booking has one.")
        return 0

    what = "grey card(s) on the board" if job_index is not None else            "cancelled clean(s), some with no card because the property has no Job"
    print(f"{len(rows)} {what} -- every row for these codes is struck, and none "
          f"appears live anywhere:")
    print("")
    print(f"  {'date':<12}{'code':<28}{'property':<24}{'out':<10}guest")
    print("  " + "-" * 86)
    for _tab, d, code, prop, guest, out in sorted(rows):
        print(f"  {d:<12}{code[:27]:<28}{prop[:23]:<24}{out:<10}{guest[:20]}")
    print("")
    print("  Check these codes in Guesty. If any is NOT cancelled, say so and "
          "the colour is wrong -- the booking would be live and shown as dead.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
