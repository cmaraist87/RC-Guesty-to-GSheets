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

# Which Connecteam scheduler each market's jobs belong on, read off the live account
# on 2026-09-02 rather than assumed. The account has seven schedulers; only these
# three are cleaning boards. The rest -- "Nola Check list", "Savannah Check List",
# "Proyect and Supply", "Snapclean" -- are deliberately never written to.
#
# Note the two collapses, both confirmed with the client:
#   * New Orleans and Bay St. Louis share one board. Both Central, so no clash.
#   * Thunderbolt has no board of its own and rides with Savannah. Adjacent town,
#     same Eastern zone, same crews.
CITY_SCHEDULERS = {
    "new orleans":  "2520975",     # 'NOLA / Bay st Louis MS'
    "bay st louis": "2520975",
    "austin":       "10540759",    # 'Austin TX'
    "savannah":     "10540737",    # 'Savannah'
    "thunderbolt":  "10540737",
}

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


def scheduler_for(city: str) -> str | None:
    """The board this city's jobs belong on, or None if we do not serve it.

    None is a refusal, not a default. Falling back to some scheduler would post a
    Boston clean onto the New Orleans board, where a crew would see it and have no
    way to know it was never theirs.
    """
    return CITY_SCHEDULERS.get(norm_city(city))


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


def shifts_by_scheduler(rows, clean_hours: float = DEFAULT_CLEAN_HOURS,
                        only_city: str | None = None) -> dict[str, list]:
    """{scheduler id -> [(row, payload)]}, ready to post one board at a time.

    Grouped by board rather than returned flat because that is how the rollout has
    to happen: one city proved correct before the next is switched on. `only_city`
    narrows it to a single market for exactly that.

    A row whose city has no board is DROPPED, not defaulted. Silence is the right
    failure here -- a guessed board puts a clean in front of a crew who cannot know
    it was never theirs.
    """
    wanted = norm_city(only_city) if only_city else None
    out: dict[str, list] = {}
    for row, payload in shifts_for_rows(rows, clean_hours=clean_hours):
        city = row.get("City", "")
        if wanted is not None and norm_city(city) != wanted:
            continue
        board = scheduler_for(city)
        if board is None:
            continue
        out.setdefault(board, []).append((row, payload))
    return out


def assert_unassigned(payloads) -> None:
    """Raise unless every payload is an open, unassigned shift.

    A last gate before anything reaches the network. The rule -- no job is ever put
    against a named person -- is the client's standing instruction, and a rule that
    lives only in a comment is one refactor away from being lost. Anything that even
    LOOKS like a user reference is rejected, rather than only the field name we
    happen to use today.
    """
    for p in payloads:
        if not p.get("isOpenShift"):
            raise ValueError(f"shift is not an open shift: {p!r}")
        for key, value in p.items():
            if "user" in key.lower() or "assign" in key.lower():
                if value:
                    raise ValueError(
                        f"shift carries an assignment in {key!r}: {value!r}. Every "
                        f"job must land in Unassigned.")
