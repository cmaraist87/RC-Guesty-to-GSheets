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
    # title is in here because a booking can BECOME a turnover. Without it the
    # colour went blue and the title stayed "Clean" -- 2026-10-07 left seven blue
    # cards on the test board with five Turnover titles between them, which reads
    # as a bug to anyone looking at the board and is one.
    for field in ("startTime", "endTime", "jobId", "title"):
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


# ---------------------------------------------------------------- the plan

def slot_key(shift) -> tuple:
    """The OLD identity, kept only to adopt cards made before codes were tracked.

    Ninety-five cards were already on the test board when identity moved to the
    Confirmation Code, and they carry no record of which booking they belong to.
    Rather than delete and rebuild the board, a card whose code we do not know is
    matched on the slot it occupies -- which is what the old code matched on -- and
    from then on it is in the map like any other.
    """
    # NO title. Title is one of the things a booking can CHANGE -- a clean
    # becoming a turnover changes it -- so keying adoption on it means such a
    # card cannot be recognised, gets greyed as "no booking claims this", and a
    # second card is created beside it. That happened to 6504 Porter A on
    # 31 December, seen on the test board on 2026-10-07.
    #
    # (jobId, startTime) is the stable pair: the property and the instant. The
    # create-skip key in connecteam_client keeps the title, deliberately -- there,
    # a Clean and a Turnover at one slot really are two different cards.
    return (str(shift.get("jobId") or ""), int(shift.get("startTime") or 0))


def ours(shift, our_titles=(), our_colors=()) -> bool:
    """Could this card have been made by us? Title AND colour must both match.

    THE SAFETY RAIL, and the reason a live market board can be promised that its
    existing cards are untouched. Austin's own cards are titled in Spanish --
    "sale no entran huespedes", "Vacia no entra gente" -- and carry no colour at
    all. Ours are titled exactly "Clean" or "Turnover" and always carry one of
    three colours. A card has to clear both tests, so a team card fails twice.

    Both, not either, deliberately. A title test alone would claim a team card
    that happened to be called Clean; a colour test alone would claim one they
    had tinted green themselves.
    """
    if our_titles and str(shift.get("title") or "") not in our_titles:
        return False
    if our_colors:
        have = str(shift.get("color") or "").upper()
        if have not in {str(c).upper() for c in our_colors}:
            return False
    return True


def plan(desired, board, card_map, our_titles=(), stale_color=None,
         our_colors=()):
    """What to do to the board. Pure, so every branch is testable offline.

    desired  : [(code, payload)] -- every card the sheet wants in the window
    board    : [shift] -- every card actually on the board in the window
    card_map : {code: [shift id, ...]} -- what an earlier run recorded

    Returns (updates, creates, greys, new_map):
      updates : [(shift, payload, changes)] -- move or recolour in place
      creates : [(code, payload)]           -- raise a new card
      greys   : [shift]                     -- ours, wanted by nobody: removed
      new_map : {code: [shift id, ...]}     -- to store for the next run

    A card is only GREYED when no booking claims it at all. That is the "removed"
    case, which should never happen because cancellations stay in the sheet. A
    cancelled booking does not come through here as a grey: it comes through as a
    desired card that is already grey, and so is a normal update.
    """
    by_id = {str(s.get("id")): s for s in board}
    claimed: set = set()

    want_by_code: dict = {}
    for code, payload in desired:
        want_by_code.setdefault(code, []).append(({}, code, payload))

    # Cards an earlier run recorded against each code, still on the board.
    have_by_code: dict = {}
    for code in want_by_code:
        for sid in card_map.get(code, ()):
            s = by_id.get(str(sid))
            if s is not None and str(sid) not in claimed:
                have_by_code.setdefault(code, []).append(s)
                claimed.add(str(sid))

    # Adoption. A code with fewer recorded cards than it wants looks for its
    # cards where the old code would have put them.
    #
    # ONLY cards that could be ours are adoptable, by `ours` above. This is what
    # makes "a live market board's existing cards are not touched" a property of
    # the code and not an observation about today's data: every card that can be
    # MOVED, RECOLOURED or GREYED below comes from this dict, and this dict holds
    # only cards the map already recorded or cards that pass both tests.
    by_slot: dict = {}
    for s in board:
        if str(s.get("id")) in claimed:
            continue
        if not ours(s, our_titles, our_colors):
            continue
        by_slot.setdefault(slot_key(s), []).append(s)
    for code, wants in want_by_code.items():
        if len(have_by_code.get(code, ())) >= len(wants):
            continue
        for _r, _c, payload in wants:
            pool = by_slot.get(slot_key(payload)) or []
            while pool:
                s = pool.pop(0)
                if str(s.get("id")) in claimed:
                    continue
                have_by_code.setdefault(code, []).append(s)
                claimed.add(str(s.get("id")))
                break

    updates, creates, new_map = [], [], {}
    for code, wants in want_by_code.items():
        pairs, to_create, orphans = pair_by_code(wants, have_by_code.get(code, []))
        kept = []
        for (_r, _c, payload), have in pairs:
            diff = changes(payload, have)
            if diff:
                updates.append((have, payload, diff))
            kept.append(str(have.get("id")))
        for _r, _c, payload in to_create:
            creates.append((code, payload))
        # An orphan here means the booking wants FEWER cards than it has, which
        # only happens if a combined listing stopped being combined. Reported by
        # being left out of the map rather than acted on; deleting a card for a
        # LIVE booking is not something to do on a guess.
        for have in orphans:
            kept.append(str(have.get("id")))
        if kept:
            new_map[code] = kept

    greys = []
    if stale_color:
        for s in board:
            if str(s.get("id")) in claimed:
                continue
            if not ours(s, our_titles, our_colors):
                continue                  # the team's own card; not ours to touch
            if str(s.get("color") or "").upper() == stale_color.upper():
                continue                  # already grey
            greys.append(s)
    return updates, creates, greys, new_map


# ------------------------------------------------------- where the map lives

def map_name(board: str) -> str:
    """The state-bucket object holding {code -> card ids} for one board.

    Per board, not one file for all of them. The boards go live at different
    times, and a bad write while proving one market must not be able to take the
    others' mappings with it.
    """
    return f"connecteam/cards-{board}.json"


def read_map(store, board: str):
    """({code: [id, ...]}, generation). ({}, 0) when there is nothing yet.

    The map is a HINT, never an authority. Every id it returns is checked against
    the board before anything is done with it, because a card deleted in the
    Connecteam UI leaves the map stale and nothing tells us. An unreadable map is
    not fatal either: the plan falls back to adopting cards by their slot, which
    is how the first run after this change behaves anyway.
    """
    import json
    if store is None:
        return {}, 0
    try:
        raw, gen = store.read(map_name(board))
    except Exception as e:  # noqa: BLE001 - a missing map must not stop the push
        print(f"   (could not read the card map: {e}; falling back to slots)")
        return {}, 0
    if not raw:
        return {}, 0
    try:
        got = json.loads(raw)
    except ValueError as e:
        print(f"   (card map is not valid JSON: {e}; falling back to slots)")
        return {}, 0
    if not isinstance(got, dict):
        return {}, 0
    return {str(k): [str(v) for v in (vs or [])] for k, vs in got.items()}, gen


def write_map(store, board: str, mapping: dict, generation: int) -> bool:
    """Store the map. False if it could not be written, which is not fatal."""
    import json
    if store is None:
        return False
    payload = json.dumps(mapping, indent=1, sort_keys=True).encode("utf-8")
    try:
        store.write(map_name(board), payload, if_generation_match=generation)
    except Exception as e:  # noqa: BLE001 - losing the map costs a slot-adoption
        print(f"   (could not write the card map: {e}. The next run adopts cards "
              f"by their slot instead, which is slower to reason about but "
              f"correct.)")
        return False
    return True


# ----------------------------------------------- a snapshot, so undo is cheap

SNAPSHOT_DIR = "connecteam/snapshots"


def snapshot_names(board: str, when) -> tuple:
    """(dated archive, latest pointer) for one board's before-snapshot.

    Two objects, not one. The dated copy is the record -- never overwritten, so a
    run from three days ago is still recoverable. The `latest` copy is what an
    undo reaches for by default, because the thing somebody wants to undo at 6am
    is almost always the last thing that happened.

    Two rather than a listing because the object store here does read and write
    and nothing else, and adding a list operation to it to support a convenience
    is the wrong trade.
    """
    stamp = when.strftime("%Y%m%d-%H%M%S")
    return (f"{SNAPSHOT_DIR}/{board}-{stamp}.json",
            f"{SNAPSHOT_DIR}/{board}-latest.json")


def write_snapshot(store, board: str, shifts, meta: dict, when=None) -> str:
    """Record the board EXACTLY as it is, before anything is written to it.

    Returns the dated object's name, or "" if it could not be stored.

    Called before the first write of a live run, never on a preview. The whole
    point is to be able to answer "what did it look like before?" without reading
    a log and squinting, so it holds the cards whole rather than a summary -- a
    summary cannot be restored from.
    """
    import json
    from datetime import datetime, timezone
    if store is None:
        return ""
    when = when or datetime.now(timezone.utc)
    dated, latest = snapshot_names(board, when)
    body = {"board": str(board), "taken": when.isoformat(),
            "count": len(shifts), **meta, "shifts": list(shifts)}
    payload = json.dumps(body, indent=1, sort_keys=True, default=str).encode("utf-8")
    try:
        store.write(dated, payload, if_generation_match=0)
    except Exception as e:  # noqa: BLE001 - see below; this must not block a write
        print(f"   (could not store the before-snapshot: {e})")
        return ""
    try:
        _old, gen = store.read(latest)
        store.write(latest, payload, if_generation_match=gen)
    except Exception as e:  # noqa: BLE001
        print(f"   (snapshot stored as {dated} but the 'latest' pointer did not "
              f"move: {e})")
    return dated


def read_snapshot(store, board: str, name: str = ""):
    """The snapshot to undo from. `name` defaults to this board's latest."""
    import json
    if store is None:
        return None
    want = name or snapshot_names(board, __import__("datetime").datetime.now())[1]
    raw, _gen = store.read(want)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
