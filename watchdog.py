"""Did this morning's sync actually happen? Fail loudly if not.

    python watchdog.py [--deadline 06:45]

STRICTLY READ-ONLY. Reads the shared-state record the sync already writes and
reports. It cannot touch the sheet, Guesty or Connecteam.

WHY THIS EXISTS
---------------
Two failures look completely different from the outside:

  * a sync that RUNS AND FAILS -- already visible. The run goes red and GitHub
    emails Chris; he had one on 2026-09-23 and it arrived.
  * a sync that NEVER RUNS AT ALL -- completely silent. Nothing fails, because
    nothing happened. Nobody finds out until the crews work a stale sheet.

The second is the gap. This closes it by turning silence into a failure: the
deadline passes, the record does not say "done", the job exits non-zero, and
the same email that already works carries the news.

It deliberately does NOT look at the Google Sheet's "last modified" time. That
is a shared file -- anyone ticking a box updates it -- so it answers "was this
touched?" and not "did the sync run?". `guesty/daily-run.json` is written by
sync.py only after the sheet has been written, and nobody else can move it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from daily_gate import CLAIM_NAME, chicago_now

# A run that claimed the day but never finished is assumed dead after this --
# the same figure daily_gate uses to decide a crashed run may be retried.
IN_FLIGHT_GRACE = timedelta(minutes=30)


def assess(record, now, deadline="06:45"):
    """(ok, headline, detail) for one day's record. Pure, so it can be tested.

    `record` is the parsed daily-run.json, or None when there is no record at
    all. `now` is a Chicago-local datetime.
    """
    today = now.date().isoformat()
    hh, mm = (int(x) for x in deadline.split(":"))
    due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    before_deadline = now < due

    if record is None:
        return (False, "NO RECORD AT ALL",
                "Nothing has ever been written to the shared-state record. "
                "Either the sync has never completed or the state bucket is "
                "not reachable from here.")

    when = str(record.get("date") or "")
    done = bool(record.get("completed"))
    finished = record.get("finished") or "?"
    started = record.get("started") or "?"
    attempts = record.get("attempts", "?")

    if when == today and done:
        return (True, "OK",
                f"Today's sync ({today}) completed at {finished} "
                f"after {attempts} attempt(s).")

    if when == today and not done:
        # Claimed but unfinished. Recent means it is probably still running;
        # old means it died partway and the deadline is at risk either way.
        age = None
        try:
            age = now - datetime.fromisoformat(started)
        except (TypeError, ValueError):
            pass
        if age is not None and age < IN_FLIGHT_GRACE:
            mins = int(age.total_seconds() // 60)
            if before_deadline:
                return (True, "IN FLIGHT",
                        f"Today's sync started {mins} minute(s) ago at {started} "
                        f"and is still running. The deadline has not passed yet.")
            return (False, "STILL RUNNING AT THE DEADLINE",
                    f"Today's sync started at {started}, {mins} minute(s) ago, "
                    f"and has not finished. The {deadline} deadline has passed.")
        return (False, "STARTED BUT NEVER FINISHED",
                f"Today's sync was claimed at {started} (attempt {attempts}) and "
                f"never recorded completion. It has almost certainly died.")

    # The record belongs to an earlier day.
    if before_deadline:
        return (True, "NOT YET",
                f"Today ({today}) has not synced yet, but the {deadline} deadline "
                f"has not passed. Last completed run was {when} at {finished}.")
    return (False, "TODAY'S SYNC HAS NOT RUN",
            f"The most recent completed sync is {when} at {finished}. "
            f"Nothing has run for {today} and the {deadline} deadline has passed. "
            f"The sheet the crews are looking at is yesterday's.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline", default="06:45",
                    help="America/Chicago local time, HH:MM")
    ap.add_argument("--drill", action="store_true",
                    help="Force the alarm, to prove the alert actually reaches "
                         "somebody. Reads nothing and changes nothing.")
    args = ap.parse_args(argv)

    # A drill. The whole design rests on 'the run goes red and GitHub emails
    # Chris' -- an assumption worth proving once, and worth being able to
    # re-prove after anyone changes their notification settings.
    if args.drill:
        print("WATCHDOG DRILL -- THIS IS NOT A REAL ALERT", file=sys.stderr)
        print("  Nothing is wrong. This run was started by hand to check that "
              "the alarm reaches somebody.", file=sys.stderr)
        print("  A real alert names the date and says what to do about it.",
              file=sys.stderr)
        return 1

    from sync import load_config, state_store
    cfg = load_config()
    store = state_store(cfg)

    now = chicago_now()
    record = None
    if store is None:
        print("WATCHDOG CANNOT CHECK", file=sys.stderr)
        print("  No shared-state store is configured (STATE_BUCKET). Without it "
              "there is no way to tell whether the sync ran.", file=sys.stderr)
        return 2
    try:
        raw, _generation = store.read(CLAIM_NAME)
        if raw:
            record = json.loads(raw)
    except Exception as e:  # noqa: BLE001 - an unreadable record is itself news
        print("WATCHDOG CANNOT CHECK", file=sys.stderr)
        print(f"  Could not read {CLAIM_NAME}: {e}", file=sys.stderr)
        return 2

    ok, headline, detail = assess(record, now, args.deadline)
    stream = sys.stdout if ok else sys.stderr
    print(f"Watchdog  {now:%Y-%m-%d %H:%M %Z}  deadline {args.deadline}", file=stream)
    print(f"  {headline}", file=stream)
    print(f"  {detail}", file=stream)
    if not ok:
        print("", file=stream)
        print("  What to do: open the Actions tab and look at this morning's "
              "'Daily Guesty -> Sheet sync' runs. If none exist, the trigger "
              "never fired -- check the Cloud Scheduler job "
              "rc-guesty-daily-sync. A manual run can be started from the "
              "Actions tab at any time.", file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
