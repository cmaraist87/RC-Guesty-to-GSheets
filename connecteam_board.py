"""Read what is ALREADY on the Connecteam boards, and compare it to the sheet.

    python connecteam_board.py --from 2026-09-01 --to 2026-09-30
    python connecteam_board.py --city Austin --from 2026-09-01 --to 2026-09-30

STRICTLY READ-ONLY. It issues GETs and nothing else -- there is no write path in
this file at all, not even behind a flag.

The point is to learn how the team actually schedules before we push anything:
whether jobs are assigned or left open, what the titles look like, how far ahead
they are created, and -- the question the preview raised -- whether an arrival-only
day gets a clean. Asking someone to recall that is guesswork; the board is the
record.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError
from connecteam_map import CITY_SCHEDULERS, scheduler_for, shift_for_row, timezone_for
from sheet_merge import norm_city
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import load_config


def _epoch(d: date, tz: str, end: bool = False) -> int:
    t = datetime(d.year, d.month, d.day, 23, 59, 59) if end else datetime(d.year, d.month, d.day)
    return int(t.replace(tzinfo=ZoneInfo(tz)).timestamp())


def _local(ts, tz) -> datetime:
    return datetime.fromtimestamp(int(ts), ZoneInfo(tz))


def describe_board(shifts, tz) -> None:
    """What this board's own habits look like."""
    if not shifts:
        print("   (no jobs in this window)")
        return

    per_day = Counter()
    assigned = open_ = 0
    durations = Counter()
    starts = Counter()
    for s in shifts:
        st = _local(s.get("startTime", 0), tz)
        per_day[st.date()] += 1
        starts[st.strftime("%H:%M")] += 1
        try:
            hrs = (int(s["endTime"]) - int(s["startTime"])) / 3600
            durations[round(hrs * 2) / 2] += 1
        except Exception:
            pass
        users = s.get("assignedUserIds") or []
        if s.get("isOpenShift") and not users:
            open_ += 1
        else:
            assigned += 1

    print(f"   {len(shifts)} job(s) across {len(per_day)} day(s), "
          f"{min(per_day)} .. {max(per_day)}")
    print(f"   assigned to someone: {assigned}    left open/unassigned: {open_}")
    print("   start times: " + ", ".join(f"{t} x{n}" for t, n in starts.most_common(6)))
    print("   durations  : " + ", ".join(f"{h}h x{n}" for h, n in
                                         sorted(durations.items())))
    print("   titles     : " + "; ".join(
        f"{str(s.get('title',''))[:38]!r}" for s in shifts[:4]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default=None, help="One market; default is every board.")
    ap.add_argument("--from", dest="frm", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", required=True, metavar="YYYY-MM-DD")
    ap.add_argument("--keys", action="store_true",
                    help="Print the FIELD NAMES a shift carries, never the values. "
                         "Needed to see which field the team puts the property in, "
                         "without pulling employee data into a log.")
    args = ap.parse_args(argv)

    key = os.environ.get("CONNECTEAM_API_KEY", "").strip()
    if not key:
        print("ERROR: CONNECTEAM_API_KEY is not set.", file=sys.stderr)
        return 2
    lo, hi = date.fromisoformat(args.frm), date.fromisoformat(args.to)
    cities = [args.city] if args.city else sorted(CITY_SCHEDULERS)

    client = ConnecteamClient(key)
    boards: dict = {}
    for city in cities:
        board = scheduler_for(city)
        if board is None:
            print(f"ERROR: '{city}' is not a covered market.", file=sys.stderr)
            return 2
        boards.setdefault(board, []).append(city)

    print(f"Reading {lo} .. {hi}  (READ-ONLY)\n")
    on_board: dict = {}
    for board, cs in boards.items():
        tz = timezone_for(cs[0])
        try:
            shifts = client.existing_shifts(board, _epoch(lo, tz),
                                            _epoch(hi, tz, end=True))
        except ConnecteamError as e:
            print(f"Board {board} ({', '.join(cs)}): could not read -- {e}")
            continue
        on_board[board] = (cs, tz, shifts)
        print(f"Board {board}  ({', '.join(cs)})")
        if args.keys and shifts:
            seen_keys: dict = {}
            for sh in shifts:
                for k, v in sh.items():
                    filled = v not in (None, "", [], {}, 0)
                    seen_keys[k] = seen_keys.get(k, 0) + (1 if filled else 0)
            print("   fields present (name: how many of the "
                  f"{len(shifts)} have it filled)")
            for k, n in sorted(seen_keys.items()):
                print(f"     {k:<28} {n}/{len(shifts)}")
        describe_board(shifts, tz)
        print()

    # --- the comparison ------------------------------------------------------
    cfg = load_config()
    if not cfg["sheet_id"]:
        print("SHEET_ID not set; skipping the sheet comparison.", file=sys.stderr)
        return 0
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)

    months, cur = [], lo.replace(day=1)
    while cur <= hi:
        months.append((cur.year, cur.month))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    rows_by_city: dict = defaultdict(list)
    for key_ in months:
        ws = tabs.get(key_)
        if ws is None:
            continue
        rows, _ = read_as_dataframe(ws)
        for _, r in rows.iterrows():
            d = str(r.get("Date", ""))[:10]
            if not (str(lo) <= d <= str(hi)):
                continue
            rows_by_city[norm_city(r.get("City", ""))].append(r)

    print("=" * 72)
    print("  SHEET vs BOARD")
    print("=" * 72)
    for board, (cs, tz, shifts) in on_board.items():
        sheet_rows = [r for c in cs for r in rows_by_city.get(norm_city(c), [])]
        cleans = [r for r in sheet_rows if shift_for_row(r)]
        arrival_only = len(sheet_rows) - len(cleans)

        board_days = Counter(_local(s.get("startTime", 0), tz).date() for s in shifts)
        sheet_days = Counter(date.fromisoformat(str(r.get("Date", ""))[:10])
                             for r in cleans)

        print(f"\nBoard {board} ({', '.join(cs)})")
        print(f"   sheet rows in range      : {len(sheet_rows)}")
        print(f"   of those, cleans         : {len(cleans)}")
        print(f"   arrival-only (no job)    : {arrival_only}")
        print(f"   jobs already on board    : {len(shifts)}")
        if not shifts:
            continue
        both = sorted(set(board_days) | set(sheet_days))
        print(f"\n   {'date':<12}{'board':>7}{'sheet':>7}   note")
        for d in both:
            b, sh = board_days.get(d, 0), sheet_days.get(d, 0)
            note = "" if b == sh else ("board has more" if b > sh else "sheet has more")
            print(f"   {d!s:<12}{b:>7}{sh:>7}   {note}")
    print("\nNothing was written. This file has no write path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
