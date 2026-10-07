"""Which card belongs to which BOOKING, and what colour it should be.

Pure functions over sheet rows and board cards. No network, so the rules can be
tested without touching Connecteam, which is the point: everything here decides
what a crew sees.

THE RULE, as Chris set it on 2026-10-06
---------------------------------------
Colour follows the BOOKING, identified by its Confirmation Code, not the slot it
happens to occupy:

  * the code has any live row  -> green, or blue if it is a turnover. INCLUDING
    when the date or time changed. A changed row is amber in the sheet, and amber
    means updated, not cancelled.
  * every row for the code is struck, and it appears nowhere live -> grey. The
    booking is cancelled.
  * the code is not in the sheet at all -> grey. Should never happen, because
    cancellations are kept, so it is a safety net rather than a path.

WHY THE CODE AND NOT THE SLOT
-----------------------------
Because strikethrough is ambiguous. The sheet strikes a row for `moved` as well
as for `cancelled` (sheets_client._STRIKE_NEW_FLAGS), and both are just a line
through the cells. Reading the format alone, a booking that moved to another date
is indistinguishable from one that was cancelled -- and colouring the first grey
tells a crew a job is off when it is merely later. Only the code can tell them
apart, by whether a live row for it exists somewhere else.

That same ambiguity means a struck row whose code is live elsewhere must produce
NO card. The live row owns the card. Otherwise one booking yields two.
"""
from __future__ import annotations

from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, TURNOVER_COLOR,
                            shift_for_row)

CODE_COLUMNS = ("Confirmation Code", "CONFIRMATION CODE", "Confirmation code")


def code_of(row) -> str:
    """The booking's Confirmation Code, normalised, or "" if the row has none.

    Upper-cased and stripped because the sheet is hand-edited and the same code
    turns up padded or in the wrong case. A row with no code cannot be tracked as
    a booking at all, and the caller has to decide what that means.
    """
    for col in CODE_COLUMNS:
        if col in row:
            got = str(row.get(col) or "").strip().upper()
            if got:
                return got
    return ""


def booking_activity(tabs) -> dict:
    """{code -> True if ANY row for it is live} across every tab read.

    `tabs` is an iterable of (frame, struck_positions), one pair per month tab,
    with positions 0-based over data rows as read_row_marks returns them.

    Judged across ALL tabs rather than only the push window, deliberately. A
    booking can move from inside the window to outside it, and its live row is
    then in a month the window does not cover. Judging on the window alone would
    read that as cancelled and grey a card for a job that is still happening.
    """
    out: dict = {}
    for frame, struck in tabs:
        if frame is None or not len(frame):
            continue
        for i, row in frame.iterrows():
            code = code_of(row)
            if not code:
                continue
            out[code] = out.get(code, False) or (i not in struck)
    return out


def is_turnover(row) -> bool:
    return str(row.get("T/O", "")).strip().lower() == "yes"


def colour_for(row, active: dict) -> str:
    """The colour this row's card should carry, by the rule above."""
    code = code_of(row)
    if code and not active.get(code, False):
        return CANCELLED_COLOR
    return TURNOVER_COLOR if is_turnover(row) else STANDARD_COLOR


def cards_for_tab(frame, struck, active, job_index=None, clean_hours=None):
    """[(row, code, payload)] for one tab. Skips rows that are not a job.

    A struck row whose code is live elsewhere is DROPPED, not coloured: that row
    is the old half of a move and the live row owns the card. Leaving it in would
    give one booking two cards, which is the fault this whole module exists to
    prevent.
    """
    out = []
    if frame is None or not len(frame):
        return out
    for i, row in frame.iterrows():
        code = code_of(row)
        struck_here = i in struck
        if struck_here and active.get(code, False):
            continue                      # moved; the live row carries this card
        kwargs = {"job_index": job_index}
        if clean_hours is not None:
            kwargs["clean_hours"] = clean_hours
        payload = shift_for_row(row, **kwargs)
        if payload is None:
            continue                      # no check-out, or no Job: not a card
        payload["color"] = colour_for(row, active)
        out.append((row, code, payload))
    return out


# ------------------------------------------------------------------ pairing

def pair_by_code(desired, existing):
    """Match this booking's wanted cards to the cards it already has.

    `desired` is [(row, code, payload)] for ONE code; `existing` is the board
    cards recorded against that same code. Returns (pairs, to_create, orphans).

    Paired on jobId first, then on start order. jobId first because a booking at
    a combined listing produces several cards at the same instant -- one per unit
    -- and pairing those by time alone would shuffle them between properties.
    Order second so a booking that moved to a different property still keeps its
    card, which is what "move the existing card" has to mean.
    """
    left = list(desired)
    free = list(existing)
    pairs = []

    for want in list(left):
        jid = str(want[2].get("jobId") or "")
        if not jid:
            continue
        for have in list(free):
            if str(have.get("jobId") or "") == jid:
                pairs.append((want, have))
                left.remove(want)
                free.remove(have)
                break

    left.sort(key=lambda w: int(w[2].get("startTime") or 0))
    free.sort(key=lambda h: int(h.get("startTime") or 0))
    while left and free:
        pairs.append((left.pop(0), free.pop(0)))

    return pairs, left, free


def changes(want_payload, have) -> dict:
    """{field: (from, to)} for everything about an existing card that must move.

    Only the fields a booking can actually change. Times, the property, and the
    colour. Nothing else is compared, because a difference in a field the team
    own -- notes, tasks, a break -- is theirs and must survive the update.
    """
    out = {}
    for field in ("startTime", "endTime", "jobId"):
        if field not in want_payload:
            continue
        a, b = have.get(field), want_payload.get(field)
        if field in ("startTime", "endTime"):
            a, b = int(a or 0), int(b or 0)
        else:
            a, b = str(a or ""), str(b or "")
        if a != b:
            out[field] = (a, b)
    a = str(have.get("color") or "").upper()
    b = str(want_payload.get("color") or "").upper()
    if b and a != b:
        out["color"] = (have.get("color"), want_payload.get("color"))
    return out
