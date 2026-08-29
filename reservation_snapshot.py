"""
A nightly fingerprint of every reservation the fetch saw, and the diff against the
run before it.

WHY THIS EXISTS
---------------
The sheet currently keys a row on (Property, Date). That makes an ordinary edit --
a guest moving a checkout from the 2nd to the 4th -- look like two separate events:
the old key vanished (read as a cancellation, struck through) and a new key appeared
(read as a brand-new booking). It is the same reservation and should simply update
in place.

The fix is to key on the reservation's own id. Before switching, we want evidence
from real data about what actually moves when a reservation is edited -- in
particular whether `confirmationCode` survives an edit, since that code comes from
the booking channel (Airbnb, Booking.com, ...) and its stability is the channel's
policy, not Guesty's. It may well differ per channel.

This module answers that empirically and costs NOTHING extra: the sync already
fetches every field below, so no additional API call and no token quota is spent.
The snapshot doubles as a permanent audit trail of what changed each night.

The snapshot is keyed by `_id` -- Guesty's own identifier, which does not change --
so a code that DOES change is visible as a change rather than as a new reservation.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from guesty_adapter import FIELD_MAP, _as_id, _first

SNAPSHOT_NAME = "guesty/reservations-snapshot.json"

# What we fingerprint per reservation. Deliberately small -- this is an identity and
# change record, not a copy of the data. Every one of these is already fetched.
TRACKED = ("code", "status", "listing_id", "listing", "checkin", "checkout", "guest")


def fingerprint(reservations: list[dict]) -> dict[str, dict]:
    """{reservation _id -> the handful of fields we watch for change}.

    Reservations with no `_id` are dropped: without a stable key there is nothing to
    compare them against, and guessing one would manufacture false changes.
    """
    out: dict[str, dict] = {}
    for res in reservations:
        rid = _as_id(_first(res, FIELD_MAP["res_id"]))
        if not rid:
            continue
        out[rid] = {
            "code": str(_first(res, FIELD_MAP["conf_code"]) or "").strip(),
            "status": str(res.get("status") or "").strip(),
            "listing_id": _as_id(_first(res, FIELD_MAP["listing_id"])),
            "listing": str(_first(res, FIELD_MAP["listing"]) or "").strip(),
            "checkin": str(_first(res, FIELD_MAP["checkin_dt"]) or "").strip(),
            "checkout": str(_first(res, FIELD_MAP["checkout_dt"]) or "").strip(),
            "guest": str(_first(res, FIELD_MAP["guest"]) or "").strip(),
        }
    return out


def _in_window(rec: dict, window: tuple[str, str] | None) -> bool:
    """Would this reservation still be inside the fetch window?

    The window is lookback/lookahead days around TODAY, so it slides every night. A
    reservation that simply aged out of it is absent from tonight's fetch while being
    perfectly alive. Counting that as a cancellation would put a fictional number at
    the top of the report every single morning, which is exactly the kind of noise
    that teaches people to ignore a report.
    """
    if not window:
        return True
    lo, hi = window
    checkout, checkin = rec.get("checkout") or "", rec.get("checkin") or ""
    if checkout and checkout[:10] < lo:
        return False
    if checkin and checkin[:10] > hi:
        return False
    return True


def diff(before: dict[str, dict], after: dict[str, dict],
         window: tuple[str, str] | None = None) -> dict:
    """What changed between two fingerprints, grouped by what it tells us.

    `code_changed` is the one we are actually asking about: same reservation,
    different confirmation code. If that list stays empty across a few runs in which
    `edited` is non-empty, the code survives edits and either key would work. If it
    is ever non-empty, keying on the confirmation code would have silently split one
    booking into two rows -- and `_id` is the only safe key.

    `window` is tonight's fetch coverage. Reservations that fell out of it are
    reported separately as `left_window`, never as cancellations.
    """
    added = sorted(set(after) - set(before))
    vanished = sorted(set(before) - set(after))
    gone = [r for r in vanished if _in_window(before[r], window)]
    left_window = [r for r in vanished if r not in set(gone)]
    edited, code_changed = [], []
    for rid in sorted(set(before) & set(after)):
        b, a = before[rid], after[rid]
        fields = [f for f in TRACKED if b.get(f) != a.get(f)]
        if not fields:
            continue
        rec = {"id": rid, "guest": a.get("guest", ""), "code": a.get("code", ""),
               "changed": fields,
               "from": {f: b.get(f) for f in fields},
               "to": {f: a.get(f) for f in fields}}
        edited.append(rec)
        if "code" in fields:
            code_changed.append(rec)
    return {"added": added, "gone": gone, "left_window": left_window,
            "edited": edited, "code_changed": code_changed,
            "counts": {"added": len(added), "gone": len(gone),
                       "left_window": len(left_window),
                       "edited": len(edited), "code_changed": len(code_changed),
                       "total": len(after)}}


def _payload(fp: dict[str, dict]) -> bytes:
    return json.dumps({
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(fp),
        "reservations": fp,
    }, sort_keys=True).encode()


def load(store, name: str = SNAPSHOT_NAME) -> tuple[dict[str, dict], int, str]:
    """(fingerprint, generation, taken_at) of the stored snapshot.

    A missing or unreadable snapshot is the normal first-run case, not an error:
    return an empty fingerprint so the caller records a baseline and diffs tomorrow.
    """
    if store is None:
        return {}, 0, ""
    try:
        raw, generation = store.read(name)
    except Exception as e:  # noqa: BLE001 - an audit trail must never fail the sync
        print(f"   (could not read the reservation snapshot: {e})")
        return {}, 0, ""
    if not raw:
        return {}, generation, ""
    try:
        doc = json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError) as e:
        print(f"   (reservation snapshot is unreadable, starting a fresh one: {e})")
        return {}, generation, ""
    return doc.get("reservations") or {}, generation, doc.get("taken_at", "")


def save(store, fp: dict[str, dict], generation: int,
         name: str = SNAPSHOT_NAME) -> bool:
    """Write the new snapshot. False if it could not be stored.

    Written with the generation the diff was computed against, so two runtimes
    racing cannot interleave and lose one run's worth of history.
    """
    if store is None:
        return False
    try:
        store.write(name, _payload(fp), if_generation_match=generation)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"   (could not store the reservation snapshot: {e})")
        return False


def format_report(d: dict, taken_at: str = "", limit: int = 10) -> str:
    """The run-log section. Leads with the question we are trying to answer."""
    c = d["counts"]
    if not taken_at:
        return (f"   Reservation snapshot: baseline recorded, {c['total']} reservation(s).\n"
                f"   The first diff appears on the next run.")
    lines = [f"   Reservation snapshot: {c['total']} now, vs {taken_at}",
             f"     {c['added']} appeared | {c['gone']} cancelled | "
             f"{c['edited']} edited in place"
             + (f" | {c['left_window']} aged out of the fetch window"
                if c.get("left_window") else "")]
    if c["code_changed"]:
        lines.append(f"     !! {c['code_changed']} reservation(s) CHANGED CONFIRMATION CODE "
                     f"-- the code is NOT a safe key:")
        for r in d["code_changed"][:limit]:
            lines.append(f"        {r['guest']}: {r['from'].get('code')} -> {r['to'].get('code')}")
    elif c["edited"]:
        lines.append("     confirmation code held on every edit this run.")
    for r in d["edited"][:limit]:
        moves = ", ".join(f"{f}: {r['from'][f]!r} -> {r['to'][f]!r}" for f in r["changed"])
        lines.append(f"     edited  {r['guest'] or r['id']} [{r['code']}] {moves}")
    if c["edited"] > limit:
        lines.append(f"     ... and {c['edited'] - limit} more edit(s)")
    return "\n".join(lines)
