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

    session = requests.Session()
    session.headers.update({"X-API-KEY": key, "Accept": "application/json"})

    print(f"Connecteam read-only probe -- {BASE}")
    print(f"Key: {key[:4]}...{key[-4:]} ({len(key)} chars)\n")

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

    # The structural question: one scheduler per city, or jobs inside one scheduler?
    if isinstance(schedulers, dict):
        items = (schedulers.get("data") or {}).get("schedulers") or []
    elif isinstance(schedulers, list):
        items = schedulers
    else:
        items = []

    print("=" * 62)
    print(f"  {len(items)} scheduler(s) found")
    print("=" * 62)
    for s in items:
        sid = s.get("schedulerId") or s.get("id") or "?"
        print(f"  {sid}  {s.get('name', '(unnamed)')!r}")
        status, jobs = _get(session, f"/scheduler/v1/schedulers/{sid}/jobs")
        if status != 200:
            print(f"    jobs -> HTTP {status} (may simply not be in use)")
            continue
        jl = jobs.get("data", {}).get("jobs", jobs) if isinstance(jobs, dict) else jobs
        jl = jl if isinstance(jl, list) else []
        print(f"    {len(jl)} job(s)")
        for j in jl[:15]:
            print(f"      {j.get('jobId') or j.get('id', '?')}  {j.get('name', '')!r}")

    print("\nNothing was created, changed or deleted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
