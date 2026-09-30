"""Can an existing card's colour be changed, and by which call?

    python shift_update_probe.py            # discover, on the TEST board only
    python shift_update_probe.py --keep     # leave the probe card behind

WRITES to the test board, and cleans up after itself.

WHY THIS EXISTS
---------------
Cancellations have to go light gray on cards that are ALREADY on the board. The
push has only ever created: it compares what it wants against what is there and
skips the matches. Skipping is why a card created green stays green after its
booking is cancelled.

So a card needs updating in place, and this repo has never updated one. The
endpoint is not documented for this account's plan, so it is discovered the same
way the Job endpoints were -- by trying the plausible calls against a throwaway
card and reading the result back.

HOW IT IS KEPT SAFE
-------------------
  * The test board, hardcoded. There is no --board.
  * A card it creates itself, far in the future (2027-12-30), titled so nobody
    could mistake it for work. It never touches a real card.
  * Every attempt is read back from the board. "HTTP 200" is not evidence: the
    Sheets half of this project spent days on an API that accepted formatting
    requests, returned 200, and did nothing. The colour is re-read and compared.
  * Every field is compared, not just the colour, so a call that changes the
    colour by wiping the jobId or re-assigning the card is reported as a
    failure. That matters most for isOpenShift -- a card that comes back
    assigned to somebody is the one outcome that must never ship.
  * The probe card is deleted at the end, --keep or not, unless --keep is given.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, TEST_SCHEDULER,
                            assert_unassigned)

TZ = "America/Chicago"
WHEN = datetime(2027, 12, 30, 9, 0, tzinfo=ZoneInfo(TZ))
TITLE = "ZZ COLOUR UPDATE PROBE - SAFE TO DELETE"

# What "unchanged" has to mean. A call that recolours the card by dropping the
# property, publishing it, or putting someone on it is not a working call.
MUST_NOT_MOVE = ("jobId", "isOpenShift", "startTime", "endTime", "title",
                 "openSpots", "isPublished", "assignedUserIds")


def read_back(client, shift_id: str):
    """The card as the BOARD has it, not as we hope it is."""
    lo = int((WHEN - timedelta(days=2)).timestamp())
    hi = int((WHEN + timedelta(days=2)).timestamp())
    for s in client.existing_shifts(TEST_SCHEDULER, lo, hi):
        if str(s.get("id")) == str(shift_id):
            return s
    return None


def attempts(board: str, sid: str, body: dict):
    """The calls worth trying, in the order worth trying them.

    PUT-with-full-body first: that is how /jobs/v1/jobs/{id} behaves on this
    account, so it is the house style rather than a guess.
    """
    colour_only = {"color": body["color"]}
    # openSpots removed: V1 rejects it outright on an update, and if V2 shares
    # the rule there is no reason to send it. Nothing else is dropped -- the card
    # goes back exactly as it was read, with one field changed.
    no_spots = {k: v for k, v in dict(body, shiftId=sid).items()
                if k != "openSpots"}
    return [
        ("PUT   full body", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts/{sid}", body),
        ("PATCH colour only", "PATCH",
         f"/scheduler/v1/schedulers/{board}/shifts/{sid}", colour_only),
        ("PUT   colour only", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts/{sid}", colour_only),
        ("PATCH full body", "PATCH",
         f"/scheduler/v1/schedulers/{board}/shifts/{sid}", body),
        # The collection endpoint answers 400, not 405: it EXISTS for PUT and
        # only the body was wrong. So the shapes below are worth walking through
        # one at a time -- the difference between "no such call" and "right call,
        # wrong envelope" is the whole question.
        ("PUT   [{id, ...all}]", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts", [dict(body, id=sid)]),
        # THE ONE THAT WORKS. `shiftId`, not `id`, and openSpots must be gone:
        # the API says "The open_spots parameter is not supported in V1 update"
        # -- an objection to one field, which is the endpoint working, not
        # refusing. Everything else goes back exactly as it was read.
        ("PUT   [{shiftId, no openSpots}]", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts",
         [{k: v for k, v in dict(body, shiftId=sid).items() if k != "openSpots"}]),
        ("PUT   [{shiftId, ...all}]", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts", [dict(body, shiftId=sid)]),
        ("PUT   [{id, color}]", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts",
         [{"id": sid, "color": colour_only["color"]}]),
        ("PUT   {shifts:[{id, ...all}]}", "PUT",
         f"/scheduler/v1/schedulers/{board}/shifts",
         {"shifts": [dict(body, id=sid)]}),
        # V1 says "not supported in V1 update", which is the API telling us a V2
        # exists. And it says "can't edit root open shift with multiple open
        # spots" -- ours has ONE spot, so "root open shift" is the objection, not
        # the count: an open shift is a parent record and V1 will not touch it.
        # Every card we make is an open shift, so V1 is a dead end by design and
        # V2 is the only thing left worth asking.
        ("PUT   v2 [{shiftId}]", "PUT",
         f"/scheduler/v2/schedulers/{board}/shifts", [no_spots]),
        ("PUT   v2 /{shiftId}", "PUT",
         f"/scheduler/v2/schedulers/{board}/shifts/{sid}", no_spots),
        ("PATCH v2 /{shiftId}", "PATCH",
         f"/scheduler/v2/schedulers/{board}/shifts/{sid}", no_spots),
        ("PUT   v2 [{shiftId, colour}]", "PUT",
         f"/scheduler/v2/schedulers/{board}/shifts",
         [{"shiftId": sid, "color": colour_only["color"]}]),
        # Left last on purpose: POST does not update, it CREATES, so this one
        # leaves a duplicate behind. Harmless on the test board and cleaned up
        # by title below, but it is why cleanup cannot go by id alone.
        ("POST  [{id, ...all}]", "POST",
         f"/scheduler/v1/schedulers/{board}/shifts", [dict(body, id=sid)]),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="leave the probe card on the board for inspection")
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))

    base = {
        "startTime": int(WHEN.timestamp()),
        "endTime": int((WHEN + timedelta(minutes=15)).timestamp()),
        "timezone": TZ,
        "title": TITLE,
        "color": STANDARD_COLOR,
        "isOpenShift": True,
        "assignedUserIds": [],
        "openSpots": 1,
        "isPublished": True,
    }
    assert_unassigned([base])

    print(f"Creating one probe card on the TEST board {TEST_SCHEDULER}, "
          f"{WHEN:%d %b %Y %H:%M}, {STANDARD_COLOR}.")
    try:
        got = client._request(
            "POST", f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/shifts", body=[base])
    except ConnecteamError as e:
        print(f"could not create the probe card: {e}", file=sys.stderr)
        return 2

    rows = (((got or {}).get("data") or {}).get("shifts")
            or (got or {}).get("data") or [])
    sid = None
    if isinstance(rows, list) and rows:
        sid = str(rows[0].get("id") or rows[0].get("shiftId") or "")
    if not sid:
        found = None
        for s in client.existing_shifts(
                TEST_SCHEDULER, int((WHEN - timedelta(days=2)).timestamp()),
                int((WHEN + timedelta(days=2)).timestamp())):
            if str(s.get("title")) == TITLE:
                found = s
        sid = str((found or {}).get("id") or "")
    if not sid:
        print("created the card but could not find its id; nothing to probe.",
              file=sys.stderr)
        return 2
    print(f"  probe card id {sid}")
    print("")

    before = read_back(client, sid)
    if before is None:
        print("the card cannot be read back at all; stopping.", file=sys.stderr)
        return 2
    print(f"  as the board has it: colour {before.get('color')!r}")
    print("")

    winner = None
    for label, method, path, body in attempts(TEST_SCHEDULER, sid, dict(base, color=CANCELLED_COLOR)):
        print(f"  {label:<26} ", end="")
        try:
            client._request(method, path, body=body, tries=1)
        except ConnecteamError as e:
            msg = str(e)
            body_txt = msg.split("HTTP", 1)[-1] if "HTTP" in msg else msg
            head = body_txt[:60].strip()
            print(f"refused  HTTP{head}")
            # A 405 means the call does not exist and there is nothing to learn.
            # A 400 means it does, and the rest of the message says what it
            # wanted -- print it whole rather than clipping the answer off.
            if "405" not in body_txt[:12]:
                for j in range(60, min(len(body_txt), 1600), 110):
                    print(f"        | {body_txt[j:j + 110].strip()}")
            continue
        after = read_back(client, sid)
        if after is None:
            print("accepted, but the card vanished from the board!")
            continue
        if str(after.get("color", "")).upper() != CANCELLED_COLOR.upper():
            # Accepted and did nothing. The exact failure the Sheets half of
            # this project lost days to, so it is checked rather than assumed.
            print(f"accepted (200) but colour is still "
                  f"{after.get('color')!r} -- no effect")
            continue
        moved = {k: (before.get(k), after.get(k)) for k in MUST_NOT_MOVE
                 if k in before and before.get(k) != after.get(k)}
        if moved:
            print(f"colour CHANGED but so did {json.dumps(moved, default=str)[:160]}")
            continue
        print(f"WORKS -- colour now {after.get('color')!r}, nothing else moved")
        winner = (method, path.replace(sid, "{shiftId}")
                  .replace(TEST_SCHEDULER, "{board}"), body is not None)
        break

    print("")
    if winner:
        print(f"USE THIS: {winner[0]} {winner[1]}")
    else:
        print("NOTHING WORKED. No call on this account's plan recolours a card in")
        print("place, so a cancelled card has to be deleted and re-created instead.")

    if args.keep:
        print(f"\n--keep given; probe card {sid} left on the test board.")
        return 0 if winner else 1

    print("")
    # By TITLE, not by the one id we started with. The POST attempt creates a
    # second card instead of updating the first, so going by id would leave a
    # stray probe card on the board -- which is exactly what happened on the
    # 2026-09-30 run before this loop existed.
    lo = int((WHEN - timedelta(days=2)).timestamp())
    hi = int((WHEN + timedelta(days=2)).timestamp())
    mine = [s for s in client.existing_shifts(TEST_SCHEDULER, lo, hi)
            if str(s.get("title")) == TITLE]
    print(f"cleaning up: {len(mine)} card(s) titled {TITLE!r} on the test board")
    failed = []
    for s in mine:
        one = str(s.get("id"))
        if s.get("assignedUserIds"):
            # Should be impossible for a card this probe made, but the rule is
            # the rule: an assigned card is somebody's shift.
            print(f"   REFUSING to delete {one}: somebody is assigned to it")
            failed.append(one)
            continue
        try:
            client._request(
                "DELETE", f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/shifts/{one}")
        except ConnecteamError as e:
            print(f"   could not delete {one}: {str(e)[:120]}")
            failed.append(one)
    left = [s for s in client.existing_shifts(TEST_SCHEDULER, lo, hi)
            if str(s.get("title")) == TITLE]
    if left or failed:
        print(f"   !! {len(left)} probe card(s) STILL on the board. Remove by "
              f"hand: titled {TITLE!r} on {WHEN:%d %b %Y}.", file=sys.stderr)
        return 1
    print("   board is as it was; nothing of the probe remains.")
    return 0 if winner else 1


if __name__ == "__main__":
    raise SystemExit(main())
