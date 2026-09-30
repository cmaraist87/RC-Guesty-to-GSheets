"""What colours will Connecteam actually accept for a job card?

    python color_palette.py

Sends ONE deliberately invalid colour to the test board and reads the full
rejection. The request is REFUSED, so nothing is ever created -- that refusal
is the point: the API answers by enumerating every colour it will take.

The list recorded in connecteam_map came from exactly this rejection on
2026-09-12, but the log truncated it, so the constant there is known to be
short. This asks again and prints the answer whole.

The colours are then sorted by hue and lightness, because a request arrives as
"dark blue" and "light green" and a bare list of nineteen hex codes does not
answer that.
"""
from __future__ import annotations

import colorsys
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import ALLOWED_COLORS, STANDARD_COLOR, TURNOVER_COLOR, TEST_SCHEDULER

TZ = "America/Chicago"
WHEN = datetime(2027, 6, 1, 9, 0, tzinfo=ZoneInfo(TZ))


def hsl(hex_code: str):
    h = hex_code.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, light * 100, sat * 100


def family(hue: float, sat: float) -> str:
    if sat < 12:
        return "grey"
    if hue < 20 or hue >= 340:
        return "red"
    if hue < 45:
        return "orange"
    if hue < 70:
        return "yellow"
    if hue < 160:
        return "green"
    if hue < 200:
        return "teal"
    if hue < 260:
        return "blue"
    return "purple"


def main(argv=None) -> int:
    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))

    # A colour that cannot be on any palette. The shift is otherwise valid, so
    # the ONLY thing wrong with it is the colour -- which is what makes the
    # error name the alternatives instead of complaining about something else.
    probe = {"timezone": TZ, "isOpenShift": True, "assignedUserIds": [],
             "openSpots": 1, "isPublished": True, "title": "PALETTE PROBE",
             "startTime": int(WHEN.timestamp()),
             "endTime": int((WHEN + timedelta(minutes=15)).timestamp()),
             "color": "#0F0F0F"}

    print("Asking the API which colours it accepts (the request is refused, so")
    print("nothing is created)...")
    print("")
    raw = ""
    try:
        client._request("POST",
                        f"/scheduler/v1/schedulers/{TEST_SCHEDULER}/shifts",
                        body=[probe], tries=1)
        print("UNEXPECTED: the invalid colour was ACCEPTED. A card now exists on")
        print("the test board and should be deleted with shift_delete.")
        return 1
    except ConnecteamError as e:
        raw = str(e)

    found = sorted({m.upper() for m in re.findall(r"#[0-9a-fA-F]{6}", raw)})
    print(f"the API named {len(found)} colour(s):")
    print("")
    if not found:
        print("  none -- the rejection did not enumerate them. Full text:")
        print("  " + raw[:1500])
        return 1

    rows = []
    for c in found:
        hue, light, sat = hsl(c)
        rows.append((family(hue, sat), light, c, hue, sat))
    rows.sort(key=lambda r: (r[0], r[1]))

    print(f"  {'family':<8}{'hex':<10}{'lightness':>10}{'saturation':>12}   reads as")
    print("  " + "-" * 62)
    last = None
    for fam, light, c, hue, sat in rows:
        if fam != last:
            print("")
            last = fam
        same = " <- in use" if c in (TURNOVER_COLOR.upper(), STANDARD_COLOR.upper()) else ""
        word = "very dark" if light < 30 else "dark" if light < 45 else \
               "mid" if light < 60 else "light" if light < 75 else "very light"
        print(f"  {fam:<8}{c:<10}{light:>9.0f}%{sat:>11.0f}%   {word}{same}")

    print("")
    missing = [c for c in found if c not in {x.upper() for x in ALLOWED_COLORS}]
    stale = [c for c in ALLOWED_COLORS if c.upper() not in set(found)]
    print(f"  recorded in code: {len(ALLOWED_COLORS)}   named by the API now: {len(found)}")
    if missing:
        print(f"  the API accepts these that the code does not list: {missing}")
    if stale:
        print(f"  the code lists these the API did not name: {stale}")
    print("")
    print("  Nothing was created. Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
