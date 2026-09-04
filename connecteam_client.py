"""
The only code in this repo that can write to Connecteam. It will not do so unless
asked twice.

DESIGN
------
Creating a shift is not like writing a sheet cell. A cell can be corrected on the
next run; a job card has already appeared on a crew's phone. So:

  * `live` defaults to False. Without it, every call prints exactly what it would
    send and returns nothing. That is the mode the rollout runs in first, one city
    at a time, until the client has read the list and said go.
  * Every payload passes connecteam_map.assert_unassigned immediately before the
    request. The standing instruction is that no job is ever put against a named
    person, and a rule that lives only in a comment is one refactor away from lost.
  * Creating is IDEMPOTENT. Before writing, the shifts already on the board for that
    window are read back, and anything matching one is skipped. Running twice must
    not double a cleaner's day, and the sheet has no column to remember shift ids
    in, so the board itself is the memory.

API shape confirmed against the live account on 2026-09-02 and the published
reference: POST /scheduler/v1/schedulers/{schedulerId}/shifts takes an ARRAY of
shift objects, up to 500 per request, and returns them with ids assigned.
"""
from __future__ import annotations

import json
import time

import requests

from connecteam_map import assert_unassigned

BASE = "https://api.connecteam.com"
TIMEOUT = 30
MAX_PER_REQUEST = 500          # the documented bulk ceiling
RETRY_STATUS = (408, 429, 500, 502, 503, 504)


class ConnecteamError(RuntimeError):
    """A request failed in a way that retrying will not fix."""


def _shift_key(shift: dict) -> tuple:
    """What makes two shifts "the same job" for the purpose of not duplicating it.

    Title and start instant. The title carries the property and its codes, so two
    cleans of different properties at the same minute stay distinct, while the same
    clean posted twice collapses. End time is deliberately excluded: a turnover's
    window can be re-derived slightly differently without it becoming a new job.
    """
    return (str(shift.get("title", "")).strip(), int(shift.get("startTime", 0) or 0))


def check_api_key(api_key) -> str:
    """Reject a key that cannot possibly be one, and say why.

    Placeholder text pasted straight out of an instruction is the common mistake --
    it produces a 403 that reads exactly like a permissions or plan problem, which
    is a genuinely expensive thing to go and investigate. A real key has no spaces
    and no angle brackets, so this costs nothing and saves that trip.
    """
    key = str(api_key or "").strip()
    if not key:
        raise ConnecteamError(
            "CONNECTEAM_API_KEY is empty. Set it to the SECRET key from "
            "General Settings -> API keys (the key's name is only a label).")
    if key.startswith("<") or key.endswith(">") or any(c.isspace() for c in key):
        raise ConnecteamError(
            f"CONNECTEAM_API_KEY does not look like a key: {key[:4]}...{key[-4:]} "
            f"({len(key)} chars). That is placeholder text, not your key. "
            "A real one is a single run of letters and digits, around 36 "
            "characters, with no spaces or angle brackets. Copy the SECRET key "
            "from Connecteam: General Settings -> API keys.")
    return key


class ConnecteamClient:
    def __init__(self, api_key: str, session: requests.Session | None = None):
        key = check_api_key(api_key)
        self.session = session or requests.Session()
        self.session.headers.update({
            "X-API-KEY": key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    # --- plumbing ---------------------------------------------------------
    def _request(self, method: str, path: str, body=None, tries: int = 4):
        delay = 1.0
        for attempt in range(1, tries + 1):
            resp = self.session.request(method, BASE + path, timeout=TIMEOUT,
                                        data=json.dumps(body) if body is not None else None)
            if resp.status_code in RETRY_STATUS and attempt < tries:
                wait = float(resp.headers.get("Retry-After") or delay)
                print(f"   Connecteam returned {resp.status_code}; retrying in {wait:.0f}s "
                      f"(attempt {attempt} of {tries})")
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if resp.status_code >= 400:
                raise ConnecteamError(
                    f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:400]}")
            try:
                return resp.json()
            except ValueError:
                return {}
        raise ConnecteamError(f"{method} {path} kept failing after {tries} attempts.")

    @staticmethod
    def _rows(payload) -> list:
        """Connecteam nests its lists under data/<something>; find the list."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data") or payload
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                for value in data.values():
                    if isinstance(value, list):
                        return value
        return []

    # --- reading ----------------------------------------------------------
    def existing_shifts(self, scheduler_id: str, start: int, end: int) -> list[dict]:
        """Shifts already on this board between two epoch-second instants."""
        path = (f"/scheduler/v1/schedulers/{scheduler_id}/shifts"
                f"?startTime={int(start)}&endTime={int(end)}")
        return self._rows(self._request("GET", path))

    # --- writing ----------------------------------------------------------
    def create_shifts(self, scheduler_id: str, shifts: list[dict],
                      live: bool = False, skip_existing: bool = True) -> list[dict]:
        """Create shifts on one board. Returns what was created (empty when not live).

        `live=False` -- the default, and the whole point -- prints what would be sent
        and touches nothing. Nobody should be able to reach the write path by
        forgetting an argument.
        """
        if not shifts:
            print(f"   scheduler {scheduler_id}: nothing to create.")
            return []

        # Refuse before the network, not after. This is the standing rule.
        assert_unassigned(shifts)

        wanted = list(shifts)
        if skip_existing:
            lo = min(int(s["startTime"]) for s in wanted)
            hi = max(int(s["endTime"]) for s in wanted)
            try:
                seen = {_shift_key(s) for s in self.existing_shifts(scheduler_id, lo, hi)}
            except ConnecteamError as e:
                # Better to stop than to risk doubling a crew's day.
                raise ConnecteamError(
                    f"Could not read the board before writing to it, so duplicates "
                    f"cannot be ruled out: {e}") from e
            before = len(wanted)
            wanted = [s for s in wanted if _shift_key(s) not in seen]
            if before != len(wanted):
                print(f"   scheduler {scheduler_id}: {before - len(wanted)} job(s) are "
                      f"already on the board; skipping those.")

        if not live:
            print(f"   scheduler {scheduler_id}: WOULD create {len(wanted)} job(s). "
                  f"Nothing was sent.")
            # Every one of them. This list exists to be checked before anything
            # becomes real, and a truncated list cannot be checked.
            for s in wanted:
                print(f"     {_fmt(s)}")
            return []

        created: list[dict] = []
        for i in range(0, len(wanted), MAX_PER_REQUEST):
            batch = wanted[i:i + MAX_PER_REQUEST]
            assert_unassigned(batch)      # again, per batch, immediately before the POST
            body = self._request(
                "POST", f"/scheduler/v1/schedulers/{scheduler_id}/shifts", body=batch)
            got = self._rows(body)
            created.extend(got)
            print(f"   scheduler {scheduler_id}: created {len(got)} job(s) "
                  f"({i + len(batch)} of {len(wanted)}).")
        return created


def _fmt(shift: dict) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(shift.get("timezone", "America/Chicago"))
    start = datetime.fromtimestamp(int(shift["startTime"]), tz)
    end = datetime.fromtimestamp(int(shift["endTime"]), tz)
    return (f"{start:%a %d %b %H:%M}-{end:%H:%M} {shift.get('timezone','')}  "
            f"{shift.get('title','')}  [Unassigned]")
