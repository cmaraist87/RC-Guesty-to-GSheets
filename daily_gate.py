"""
"Has today's sync already run?" -- asked of shared state, not of the clock.

WHY THIS EXISTS
---------------
GitHub's cron is UTC-only and has no daylight saving, so the workflow fires at both
09:00 and 10:00 UTC and a guard picks the one that is 4 AM in Chicago. That guard
demanded the hour be EXACTLY 4, which quietly assumed the trigger arrives on time.

It does not. Scheduled workflows are best-effort: on 2026-09-01 both triggers
arrived more than four hours late, at Chicago hours 8 and 9, and both were skipped.
The daily sync had never actually run from the schedule -- and it failed silently,
which is the worst way for a nightly job to fail.

So stop asking "is it 4 AM?" and start asking "has today's run happened yet?". A
trigger anywhere in a generous morning window may run, and a claim in shared state
keeps it to once a day however many triggers arrive.

A run that started but did not finish does NOT block a retry later the same day: a
crash at 4 AM should not cost the whole day's sync.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CLAIM_NAME = "guesty/daily-run.json"
TZ = ZoneInfo("America/Chicago")

# How late a trigger may arrive and still count as "this morning's run". Wide,
# because the observed delay was over four hours and the cost of running late is
# a fresher sheet, while the cost of not running is no sheet at all.
WINDOW = (3, 12)          # [start, end) in Chicago local hours

# How long a claim is assumed to mean "a run is still in flight". Shorter than the
# hour between the two crons, and far longer than a sync (about three minutes), so
# a crashed run is retried while a running one is never duplicated.
RETRY_AFTER = timedelta(minutes=30)


def chicago_now(clock=None) -> datetime:
    return (clock or (lambda: datetime.now(TZ)))()


def in_window(now: datetime, window: tuple[int, int] = WINDOW) -> bool:
    lo, hi = window
    return lo <= now.hour < hi


def _load(store, name: str) -> tuple[dict, int]:
    try:
        raw, generation = store.read(name)
    except Exception as e:  # noqa: BLE001 - never let the gate fail the sync
        print(f"   (could not read the daily-run record: {e})")
        return {}, -1
    if not raw:
        return {}, generation
    try:
        return json.loads(raw.decode()), generation
    except (ValueError, UnicodeDecodeError):
        return {}, generation


def should_run(store, now: datetime | None = None,
               window: tuple[int, int] = WINDOW,
               name: str = CLAIM_NAME) -> tuple[bool, str]:
    """(may_run, why) for a SCHEDULED trigger. Claims the day when it says yes.

    With no shared store there is nothing to claim against, so two triggers could
    both proceed and each spend a Guesty token. Fall back to the old exact-hour
    rule in that case: it misses runs, but it cannot double-run.
    """
    now = now or chicago_now()
    stamp = now.date().isoformat()

    if not in_window(now, window):
        return False, (f"Chicago hour {now.hour} is outside the {window[0]}:00-"
                       f"{window[1]}:00 window -- not this morning's run.")

    if store is None:
        if now.hour == 4:
            return True, "no shared state; falling back to the exact 4 AM rule."
        return False, ("no shared state to claim the day with, and it is "
                       f"{now.hour}:00 not 4:00. Falling back to the exact-hour "
                       "rule so two triggers cannot both run.")

    record, generation = _load(store, name)
    if generation < 0:
        return False, "the daily-run record is unreadable; skipping rather than risking a double run."

    if record.get("date") == stamp:
        if record.get("completed"):
            return False, f"today's sync ({stamp}) already completed at {record.get('finished', '?')}."
        # Claimed but never finished -- either a run is in flight right now, or an
        # earlier one died. Age decides: retrying a live run would double-spend a
        # Guesty token, while never retrying would let one crash cost the day.
        started = record.get("started", "")
        try:
            age = now - datetime.fromisoformat(started)
        except (TypeError, ValueError):
            age = RETRY_AFTER          # unparseable: treat as stale and retry
        if age < RETRY_AFTER:
            mins = int(age.total_seconds() // 60)
            return False, (f"today's run was claimed {mins} minute(s) ago and is "
                           f"probably still in flight.")
        payload = dict(record, started=now.isoformat(timespec="seconds"),
                       attempts=record.get("attempts", 1) + 1)
    else:
        payload = {"date": stamp, "started": now.isoformat(timespec="seconds"),
                   "completed": False, "attempts": 1}
        started = None

    try:
        store.write(name, json.dumps(payload, sort_keys=True).encode(),
                    if_generation_match=generation)
    except Exception as e:  # noqa: BLE001 - includes PreconditionFailed
        return False, f"another trigger claimed today first ({e.__class__.__name__})."

    if started:
        return True, (f"today's run was claimed at {started} but never finished; "
                      f"retrying (attempt {payload['attempts']}).")
    return True, f"first trigger of {stamp} -- proceeding."


def mark_complete(store, now: datetime | None = None,
                  name: str = CLAIM_NAME) -> bool:
    """Record that today's sync finished. Only then is the day closed."""
    if store is None:
        return False
    now = now or chicago_now()
    record, generation = _load(store, name)
    if generation < 0:
        return False
    record.update({"date": now.date().isoformat(), "completed": True,
                   "finished": now.isoformat(timespec="seconds")})
    try:
        store.write(name, json.dumps(record, sort_keys=True).encode(),
                    if_generation_match=generation)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"   (could not record the completed run: {e})")
        return False
