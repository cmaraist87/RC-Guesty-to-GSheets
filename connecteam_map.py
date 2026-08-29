"""
Sheet rows -> Connecteam shift payloads. Pure mapping, no network.

Kept separate from any API client so the business rules -- which rows become jobs,
what the card says, when the cleaner is expected -- are testable offline and can be
reviewed without a Connecteam key.

Two decisions are baked in here, both deliberate:

  * ONLY ROWS WITH A CHECK-OUT become jobs. A clean is needed when a guest leaves.
    A check-in-only row is somebody arriving at a unit that was already cleaned when
    the previous guest left, so it is not a second job.
  * EVERY SHIFT IS UNASSIGNED. `isOpenShift` with no assignedUserIds puts it in the
    Unassigned row for a scheduler to hand out. Nothing here ever names a person.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sheet_merge import norm_city

# The five covered markets straddle two zones. Getting this wrong puts every
# Savannah job an hour out, which is invisible in the data and obvious to a cleaner
# standing outside a locked door.
CITY_TIMEZONES = {
    "new orleans": "America/Chicago",
    "bay st louis": "America/Chicago",
    "austin": "America/Chicago",
    "savannah": "America/New_York",
    "thunderbolt": "America/New_York",
}
DEFAULT_TIMEZONE = "America/Chicago"

# How long a clean is assumed to take when nothing bounds it -- a departure with no
# arrival the same day. A turnover ignores this: its window is the real gap between
# the guest leaving and the next one arriving.
DEFAULT_CLEAN_HOURS = 4.0

# Turnovers are the tight ones: someone arrives the same day, so the window is fixed
# and short. Colour is the only thing that reads at a glance on a packed board.
TURNOVER_COLOR = "#B23B3B"
STANDARD_COLOR = "#3B6FB2"

_TIME_FORMATS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")


def timezone_for(city: str) -> str:
    return CITY_TIMEZONES.get(norm_city(city), DEFAULT_TIMEZONE)


def _parse_local(date_str: str, time_str: str, tz: str) -> datetime | None:
    """A sheet date + a sheet time -> an aware datetime in the property's own zone."""
    date_str, time_str = str(date_str).strip()[:10], str(time_str).strip()
    if not date_str or not time_str:
        return None
    for fmt in _TIME_FORMATS:
        try:
            t = datetime.strptime(time_str, fmt).time()
        except ValueError:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime.combine(d, t, tzinfo=ZoneInfo(tz))
    return None


def shift_title(row) -> str:
    """What the cleaner reads without opening the card.

    Property first because that is what they navigate by; the codes follow because a
    turnover or an early check-out changes when they have to be there.
    """
    prop = str(row.get("Property", "")).strip()
    codes = []
    if str(row.get("T/O", "")).strip().lower() == "yes":
        codes.append("T/O")
    adj = str(row.get("Adjustments", "")).strip()
    if adj:
        codes.extend(c.strip() for c in adj.split(",") if c.strip())
    return f"{prop} — {' · '.join(codes)}" if codes else prop


def shift_for_row(row, clean_hours: float = DEFAULT_CLEAN_HOURS) -> dict | None:
    """One sheet row -> one Connecteam shift payload, or None if it is not a job.

    Returns the payload only; the scheduler it belongs to is the caller's business,
    since that is how the city is expressed in Connecteam's URL.
    """
    checkout = str(row.get("Check-out Time", "") or row.get("Check out - Time", "")).strip()
    if not checkout:
        return None                      # no departure -> nothing to clean

    tz = timezone_for(row.get("City", ""))
    start = _parse_local(row.get("Date", ""), checkout, tz)
    if start is None:
        return None                      # unparseable date/time: skip, never guess

    checkin = str(row.get("Check-in Time", "")).strip()
    end = _parse_local(row.get("Date", ""), checkin, tz) if checkin else None
    if end is None or end <= start:
        # No arrival today, or times that do not make sense in order. Fall back to a
        # nominal duration rather than emitting a zero- or negative-length shift.
        end = start + timedelta(hours=clean_hours)

    is_turnover = str(row.get("T/O", "")).strip().lower() == "yes"
    return {
        "startTime": int(start.timestamp()),   # epoch SECONDS; ms is rejected
        "endTime": int(end.timestamp()),
        "timezone": tz,
        "title": shift_title(row),
        "color": TURNOVER_COLOR if is_turnover else STANDARD_COLOR,
        "isOpenShift": True,               # -> Unassigned
        "isPublished": True,
        # assignedUserIds deliberately absent: an open shift must have none.
    }


def shifts_for_rows(rows, clean_hours: float = DEFAULT_CLEAN_HOURS):
    """[(row, payload)] for every row that is a job. Pairs so the caller can write
    the resulting shift id back to the row it came from."""
    out = []
    for _, row in rows.iterrows():
        payload = shift_for_row(row, clean_hours=clean_hours)
        if payload is not None:
            out.append((row, payload))
    return out
