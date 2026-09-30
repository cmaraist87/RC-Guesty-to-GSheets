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
# Chris' Test Scheduler -- a board with no crew on it, created 2026-09-12 so the
# write path can be exercised for real without a job card reaching anyone's phone.
#
# Named, not passed as a raw id, so sending somewhere by mistake takes a code change
# rather than a mistyped argument. The live boards above have 31, 882 and 194 jobs
# in September; none of them is a place to find out whether our payload is accepted.
#
# 19713722, confirmed against the account's own scheduler list ("Chris Test").
#
# It has moved twice already: first committed as 16642349, which is a GROUP id off a
# Connecteam URL and names no board at all; then 19710485, which was the board before
# it was deleted and recreated. A scheduler id is not stable across a board being
# rebuilt, and the number in a URL is often not a scheduler id in the first place.
#
# So the push VERIFIES this id against the live list and prints the board's name
# before it writes anything -- a stale id should say so, not 404 halfway through.
TEST_SCHEDULER = "19713722"

# How long the CARD is -- not how long the clean takes.
#
# Connecteam will not accept a shift without an endTime, and will not accept one
# where the end equals the start; both were tried against the API on 2026-09-21
# and refused. A short block is the nearest thing to no block: the card says be
# there at 11:00 and stops short of claiming how long the property should take,
# which is the point, since square footage varies and a fixed window implied
# otherwise. 15 minutes was verified as accepted.
CARD_MINUTES = 15
DEFAULT_CLEAN_HOURS = CARD_MINUTES / 60.0     # kept: callers pass hours

# Turnovers are the tight ones: someone arrives the same day, so the window is fixed
# and short. Colour is the only thing that reads at a glance on a packed board.
# Connecteam validates the colour against a fixed palette and rejects anything else
# with HTTP 400 (error_code 1002). Our first write to the test board was refused for
# exactly this: "#3B6FB2" and "#B23B3B" are reasonable blues and reds and neither is
# on the list.
#
# These are the values the API itself named in that rejection. The list is longer
# than this -- the error was truncated in the log -- but every entry here came from
# the API, not from a colour picker.
# All 33, verbatim from the API's own rejection on 2026-09-30, in its order and
# its casing -- the greys really do come back lowercase.
#
# This was 19 for three weeks. The rejection that enumerates the palette was
# being truncated at 400 characters by the client, and 19 is simply where the
# cut fell; the probe written to check the constant read the same truncated
# string and "confirmed" it. The last three are the row Chris described as
# black / dark gray / light gray, and none of them were reachable.
ALLOWED_COLORS = (
    "#4B7AC5", "#801A1A", "#AE2121", "#DC7A7A", "#B0712E", "#D4985A", "#E4B37F",
    "#AE8E2D", "#CBA73A", "#D9B443", "#487037", "#6F9B5C", "#91B282", "#365C64",
    "#5687B3", "#7C9BA2", "#3968BB", "#85A6DA", "#225A8C", "#548CBE", "#81A8CC",
    "#4E3F75", "#604E8E", "#8679AA", "#983D73", "#A43778", "#D178AD", "#6B2E4C",
    "#925071", "#B57D9A", "#3a3a3a", "#616161", "#969696",
)

# Set by the client: blue for turnovers, palest green for every other clean.
# The exact blue is theirs to pick and has moved once -- dark (#225A8C) on
# 2026-09-29, light royal (#85A6DA) on 2026-09-30.
#
# #85A6DA is the only light royal blue the palette holds: hue 217, eight
# degrees off CSS royalblue and the one blue lighter than it.
#
# Note what the lighter blue costs, in case it ever needs revisiting. The dark
# one separated turnovers from ordinary cleans by 142 in RGB terms; this one by
# 90, against a green of almost the same lightness (69% vs 60%). Turnovers are
# the tight jobs -- a guest arrives the same day -- so they no longer carry as
# far on a packed board. The client asked for it with the boards in front of
# them, so it stands; it is recorded here and not re-argued.
#
# RED IS NOT OURS TO USE. The team colour their own day-off cards red, so a red
# job card would read as a day off at a glance. That is why the turnover colour
# moved off #AE2121 in the first place rather than staying a strong warm one.
TURNOVER_COLOR = "#85A6DA"     # light royal blue: a turnover, same-day arrival
STANDARD_COLOR = "#91B282"     # palest green: an ordinary departure clean
CANCELLED_COLOR = "#969696"    # light gray: the booking was cancelled

# CHRIS TEST ONLY, for now. Asked for on 2026-09-30 in those words.
#
# A separate gate from the card lock in connecteam_push, and deliberately so.
# That lock says WHICH BOARDS may receive cards at all; this says which boards
# get the cancelled colour once they can. When a market board is eventually
# signed off for cards, greying cancellations there is a second decision, and
# this set is where it gets made -- not something that arrives with the first.
CANCELLED_COLOR_BOARDS = frozenset({TEST_SCHEDULER})

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


# Whether the card's job name carries the T/O and ECO/ECI/LCO/LCI codes as well as
# the property. OFF for the first rollout: the client asked for the job field to hold
# the property name and nothing else "for now". The code that builds the codes is
# kept, not deleted -- turning this back on is the whole change when they want them.
INCLUDE_CODES_IN_TITLE = False

# What the title says now that the property lives in the Job field. Connecteam
# requires a non-empty title, so it carries the KIND of job instead of the place.
STANDARD_TITLE = "Clean"
TURNOVER_TITLE = "Turnover"



def shift_title(row, with_codes: bool | None = None) -> str:
    """The job name on the card. The property is what a cleaner navigates by.

    With codes off this is the bare property name, which is what the job field is
    asked to hold today. With them on, the codes follow the property, because a
    turnover or an early check-out changes when the cleaner has to be there.
    """
    # The property is NOT in the title any more -- it is the Job the card points
    # at. But Connecteam requires a title and refuses an empty one (tried both
    # ways on 2026-09-21), so the title says what KIND of job it is instead.
    prop = str(row.get("Property", "")).strip()
    is_to = str(row.get("T/O", "")).strip().lower() == "yes"
    if not (INCLUDE_CODES_IN_TITLE if with_codes is None else with_codes):
        return TURNOVER_TITLE if is_to else STANDARD_TITLE
    codes = []
    if str(row.get("T/O", "")).strip().lower() == "yes":
        codes.append("T/O")
    adj = str(row.get("Adjustments", "")).strip()
    if adj:
        codes.extend(c.strip() for c in adj.split(",") if c.strip())
    return f"{prop} — {' · '.join(codes)}" if codes else prop


def shift_for_row(row, clean_hours: float = DEFAULT_CLEAN_HOURS,
                  job_index=None, cancelled: bool = False) -> dict | None:
    """One sheet row -> one Connecteam shift payload, or None if it is not a job.

    Returns the payload only; the scheduler it belongs to is the caller's business,
    since that is how the city is expressed in Connecteam's URL.

    `cancelled` paints the card light gray. It has to be passed IN rather than
    read off the row, because a cancellation is not in the row's values at all --
    it is strikethrough on the cells, which means a second read of the sheet's
    FORMAT. The caller does that read (sheets_client.read_row_marks) and says so
    here. A row object alone cannot answer the question.
    """
    checkout = str(row.get("Check-out Time", "") or row.get("Check out - Time", "")).strip()
    if not checkout:
        return None                      # no departure -> nothing to clean

    tz = timezone_for(row.get("City", ""))
    start = _parse_local(row.get("Date", ""), checkout, tz)
    if start is None:
        return None                      # unparseable date/time: skip, never guess

    # The job starts when the guest leaves, and the card runs a short nominal
    # block from there -- see CARD_MINUTES.
    #
    # It used to end at the next guest's check-in, which made a turnover's card
    # as long as the gap happened to be -- 11:00-16:00 one day, 11:00-15:00 the
    # next, for the same work. The card is a cleaning job, not the vacancy it sits
    # in, so its length should not move with someone else's arrival time.
    end = start + timedelta(hours=clean_hours)

    is_turnover = str(row.get("T/O", "")).strip().lower() == "yes"
    # The property, as the Job the card points at. Without an index this stays
    # absent and the card is the old shape -- a caller that has not loaded the
    # board's Jobs must not silently produce cards with no property on them.
    job_id = None
    if job_index is not None:
        from connecteam_jobs import resolve
        job_id, _name, _why = resolve(row.get("Property", ""), job_index)
        if not job_id:
            return None            # no Job -> no card; the caller reports it
    payload = {
        "startTime": int(start.timestamp()),   # epoch SECONDS; ms is rejected
        "endTime": int(end.timestamp()),
        "timezone": tz,
        "title": shift_title(row),
        # Cancelled wins over turnover. A cancelled turnover is not urgent work
        # with a caveat; it is not work. Showing it light royal blue because the
        # booking it replaced would have been tight is exactly backwards.
        "color": (CANCELLED_COLOR if cancelled
                  else TURNOVER_COLOR if is_turnover else STANDARD_COLOR),
        "isOpenShift": True,               # -> Unassigned
        # Sent explicitly empty rather than omitted. The API documents
        # "must be empty for open shifts", and an empty list states that we meant
        # it; an absent key only says we never thought about it.
        "assignedUserIds": [],
        "openSpots": 1,                    # one cleaner per job
        "isPublished": True,
    }
    if job_id:
        payload["jobId"] = job_id
    return payload


def shifts_for_rows(rows, clean_hours: float = DEFAULT_CLEAN_HOURS,
                    job_index=None, cancelled_pos=frozenset()):
    """[(row, payload)] for every row that is a job. Pairs so the caller can write
    the resulting shift id back to the row it came from.

    `cancelled_pos` holds 0-based DATA-row positions, exactly as
    sheets_client.read_row_marks returns them -- data row 0 is grid row 2. That
    lines up with this frame's index because read_as_dataframe builds it from
    values[1:] with a default RangeIndex, so position, index and data row are
    the same number. Anything that reindexes or filters the frame before it gets
    here breaks that, which is why the positions are matched on the FULL frame
    and the city filter happens afterwards.
    """
    out = []
    for i, row in rows.iterrows():
        payload = shift_for_row(row, clean_hours=clean_hours,
                                job_index=job_index,
                                cancelled=i in cancelled_pos)
        if payload is not None:
            out.append((row, payload))
    return out


def shifts_by_scheduler(rows, clean_hours: float = DEFAULT_CLEAN_HOURS,
                        only_city: str | None = None,
                        job_index=None, cancelled_pos=frozenset()) -> dict[str, list]:
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
    for row, payload in shifts_for_rows(rows, clean_hours=clean_hours,
                                        job_index=job_index,
                                        cancelled_pos=cancelled_pos):
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
