"""
Read-only look at the Connecteam account. Makes no changes, ever.

WHY THIS EXISTS
---------------
connecteam_map.py already turns a schedule row into a job card, but it cannot say
WHERE that card goes. Connecteam accounts model "the New Orleans board" in more
than one way -- a scheduler per city, one scheduler with a job per city, or a
single scheduler using location data -- and the shape of the client we write next
depends on which one you actually use. Guessing would mean building the wrong
thing and finding out by creating jobs in the wrong place.

So: look first. This script only ever issues GETs.

USAGE
-----
    CONNECTEAM_API_KEY=... python connecteam_probe.py

On this machine HTTPS also needs the rebuilt Windows CA bundle:
    export REQUESTS_CA_BUNDLE="$HOME/win-ca-bundle.pem"

Two things it is looking for:
  1. Does the key work at all, and is the account on a plan that allows API access?
     (Connecteam gates this behind Enterprise; a 401/403 here is the answer.)
  2. How are the five cities represented -- schedulers, jobs, or locations?
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests

BASE = "https://api.connecteam.com"
TIMEOUT = 30

# The five markets the sheet covers, lowercased for matching against whatever
# Connecteam calls things.
CITIES = ("new orleans", "bay st. louis", "bay st louis", "austin",
          "savannah", "thunderbolt")

# Every path here is a GET. Nothing in this file writes.
ENDPOINTS = [
    ("schedulers", "/scheduler/v1/schedulers"),
    ("users", "/users/v1/users?limit=1"),
    ("groups", "/users/v1/groups"),
]


# Connecteam documents X-API-KEY, but gateways and accounts vary -- and a wrong
# scheme looks identical to a wrong key or the wrong plan, three problems with three
# different fixes. Try each once and report which one authenticated, so the answer
# is a fact rather than an inference.
AUTH_SCHEMES = [
    ("X-API-KEY header", lambda k: {"X-API-KEY": k}),
    ("Authorization: Bearer", lambda k: {"Authorization": "Bearer " + k}),
    ("Authorization: raw key", lambda k: {"Authorization": k}),
]


def authenticate(key):
    """Find the auth scheme this account accepts. Read-only: one GET per scheme."""
    last = (None, "", 0, None)
    for label, build in AUTH_SCHEMES:
        session = requests.Session()
        headers = build(key)
        headers["Accept"] = "application/json"
        session.headers.update(headers)
        status, body = _get(session, "/scheduler/v1/schedulers")
        print("  trying %-24s -> HTTP %s" % (label, status))
        if status == 200:
            return session, label, status, body
        last = (None, label, status, body)
    return last



def _get(session: requests.Session, path: str) -> tuple[int, object]:
    r = session.get(BASE + path, timeout=TIMEOUT)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:400]


def _walk(node, depth=0, hits=None):
    """Collect any string in the payload that looks like one of our cities.

    The point is to find WHERE a city name lives -- a scheduler name, a job name,
    a location field -- without assuming the account's structure up front.
    """
    hits = [] if hits is None else hits
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and v.strip().lower() in CITIES:
                hits.append((k, v))
            else:
                _walk(v, depth + 1, hits)
    elif isinstance(node, list):
        for v in node:
            _walk(v, depth + 1, hits)
    return hits


def main() -> int:
    key = os.environ.get("CONNECTEAM_API_KEY", "").strip()
    if not key:
        print("ERROR: CONNECTEAM_API_KEY is not set.", file=sys.stderr)
        print("  Generate one as the account owner: General Settings -> API keys.",
              file=sys.stderr)
        return 2

    print("Connecteam read-only probe -- " + BASE)
    print("Key: %s...%s (%d chars)" % (key[:4], key[-4:], len(key)))
    print("Only the SECRET key goes here -- the key NAME is just a label.")
    print("")

    session, label, status, body = authenticate(key)
    if session is None:
        print("")
        print("No auth scheme worked. Last response: HTTP %s" % status)
        print("  " + json.dumps(body)[:400])
        if status in (401, 403):
            print("")
            print("401/403 means one of three things, each with a different fix:")
            print("  1. The key is wrong, or was copied with stray whitespace.")
            print("  2. The account is not on Enterprise -- Connecteam gates the API there.")
            print("  3. The key exists but has no scheduler access.")
            print("Check the plan first: it is the one that changes our approach.")
        return 1
    print("")
    print("Authenticated with: " + label)
    print("")

    schedulers = None
    for name, path in ENDPOINTS:
        status, body = _get(session, path)
        print(f"GET {path}\n  -> HTTP {status}")
        if status in (401, 403):
            print("  Rejected. Either the key is wrong, or the account is not on a "
                  "plan that allows API access (Connecteam gates this behind "
                  "Enterprise). This is the answer we needed either way.")
            print(f"  Body: {json.dumps(body)[:300]}")
            return 1
        if status != 200:
            print(f"  Body: {json.dumps(body)[:300]}")
            continue
        if name == "schedulers":
            schedulers = body
        found = _walk(body)
        if found:
            print("  City names appear under these fields: "
                  + ", ".join(sorted({k for k, _ in found})))
            for k, v in found[:12]:
                print(f"    {k} = {v!r}")
        else:
            print("  (no city names in this payload)")
        print()

    # --- Which scheduler is which, and what is already in them ------------------
    # A scheduler per market is the shape we hoped for, but two questions remain:
    # what the shift sub-resource is actually called (the /jobs guess 404s), and
    # which of these schedulers are live cleaning boards versus checklists.
    if isinstance(schedulers, dict):
        items = (schedulers.get("data") or {}).get("schedulers") or []
    elif isinstance(schedulers, list):
        items = schedulers
    else:
        items = []

    now = int(time.time())
    window = "startTime=%d&endTime=%d" % (now - 30 * 86400, now + 30 * 86400)
    # Candidate sub-resources, most likely first. Every one of them a GET.
    CANDIDATES = ["shifts", "jobs", "shift", "schedulerShifts"]

    print("=" * 62)
    print("  %d scheduler(s)" % len(items))
    print("=" * 62)
    working = None
    for s in items:
        sid = s.get("schedulerId") or s.get("id") or "?"
        print("  [%s] %r" % (sid, s.get("name", "(unnamed)")))
        for extra in ("timezone", "isArchived", "archived", "type", "color"):
            if extra in s:
                print("      %-11s %r" % (extra, s[extra]))

        for sub in (CANDIDATES if working is None else [working]):
            path = "/scheduler/v1/schedulers/%s/%s?%s" % (sid, sub, window)
            status, body = _get(session, path)
            if status != 200:
                if working is None:
                    print("      %-11s HTTP %s" % (sub, status))
                continue
            working = sub
            rows = body
            if isinstance(body, dict):
                data = body.get("data") or body
                if isinstance(data, dict):
                    rows = next((v for v in data.values() if isinstance(v, list)), [])
                else:
                    rows = data
            rows = rows if isinstance(rows, list) else []
            print("      %-11s HTTP 200 -- %d in the last/next 30 days" % (sub, len(rows)))
            if rows and isinstance(rows[0], dict):
                sample = rows[0]
                # These field names are what the writer must produce. Print the
                # real keys rather than trusting documentation.
                print("      sample keys: " + ", ".join(sorted(sample)[:18]))
                for k in ("title", "jobId", "job", "locationData", "timezone",
                          "isOpenShift", "assignedUserIds", "startTime", "endTime"):
                    if k in sample:
                        print("        %-16s %r" % (k, sample[k]))
            break

    print("")
    if working:
        print("Shift sub-resource is %r." % working)
    else:
        print("No shift sub-resource responded. The writer cannot be built until")
        print("we know the right path -- the API version may differ.")

    print("\nNothing was created, changed or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
