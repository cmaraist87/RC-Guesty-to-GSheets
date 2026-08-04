"""
Minimal Guesty Open API client: OAuth2 token (cached) + paginated reservation fetch.

Guesty allows only ~5 access-token requests per API key per 24h, so the token is
cached both in-memory and (optionally) on disk with its expiry. A once-a-day sync
uses a single token request per run -- well within the quota -- and disk caching
keeps repeated local/dry runs from burning the quota.

Docs: https://open-api-docs.guesty.com/docs/quick-start-guide
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import requests

TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
BASE_URL = "https://open-api.guesty.com/v1"
DEFAULT_TOKEN_CACHE = ".guesty_token.json"

# refresh a little before the real expiry
_EXPIRY_SKEW_SEC = 120


class GuestyError(RuntimeError):
    pass


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def get_access_token(
    client_id: str,
    client_secret: str,
    cache_path: str | None = DEFAULT_TOKEN_CACHE,
    session: requests.Session | None = None,
) -> str:
    """Return a valid bearer token, reusing a cached one until it nears expiry."""
    if not client_id or not client_secret:
        raise GuestyError("Missing Guesty client_id / client_secret.")

    # 1) disk cache
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("access_token") and cached.get("expires_at", 0) - _EXPIRY_SKEW_SEC > _now():
                return cached["access_token"]
        except (ValueError, OSError):
            pass  # ignore a corrupt cache

    # 2) request a new token (OAuth2 client-credentials, form-encoded)
    sess = session or requests.Session()
    resp = sess.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": "open-api",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise GuestyError(
            f"Token request failed ({resp.status_code}). "
            f"Check GUESTY_CLIENT_ID/SECRET. Body: {resp.text[:300]}"
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise GuestyError(f"No access_token in token response: {str(data)[:300]}")
    expires_in = int(data.get("expires_in", 86400))

    if cache_path:
        try:
            with open(cache_path, "w", encoding="utf-8") as fh:
                json.dump({"access_token": token, "expires_at": _now() + expires_in}, fh)
        except OSError:
            pass
    return token


def fetch_reservations(
    token: str,
    filters: list[dict] | None = None,
    fields: str | None = None,
    sort: str = "checkIn",
    page_size: int = 100,
    extra_params: dict | None = None,
    session: requests.Session | None = None,
    max_pages: int = 200,
) -> list[dict]:
    """
    Fetch all reservations matching `filters` (Guesty filter-object list),
    following limit/skip pagination.

    filters example:
        [{"field": "checkOut", "operator": "$gte", "value": "2026-08-01"},
         {"field": "status",   "operator": "$in",  "value": ["confirmed", "reserved"]}]
    """
    sess = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    results: list[dict] = []
    skip = 0
    for _ in range(max_pages):
        params = {"limit": page_size, "skip": skip, "sort": sort}
        if fields:
            params["fields"] = fields
        if filters:
            params["filters"] = json.dumps(filters)
        if extra_params:
            params.update(extra_params)

        resp = _get_with_retry(sess, f"{BASE_URL}/reservations", headers, params)
        payload = resp.json()
        page = payload.get("results")
        if page is None:  # some deployments nest under data/results
            page = (payload.get("data") or {}).get("results", []) if isinstance(payload.get("data"), dict) else []
        results.extend(page)

        total = payload.get("count")
        skip += page_size
        if not page or (total is not None and skip >= total):
            break

    return results


def _get_with_retry(sess, url, headers, params, tries: int = 5) -> requests.Response:
    delay = 2.0
    last = None
    for attempt in range(tries):
        resp = sess.get(url, headers=headers, params=params, timeout=60)
        if resp.status_code == 200:
            return resp
        last = resp
        if resp.status_code == 429 or resp.status_code >= 500:
            # honour Retry-After when present, else exponential backoff
            wait = float(resp.headers.get("Retry-After", delay))
            time.sleep(min(wait, 60))
            delay *= 2
            continue
        raise GuestyError(f"Reservations GET failed ({resp.status_code}): {resp.text[:300]}")
    raise GuestyError(
        f"Reservations GET failed after {tries} retries "
        f"({last.status_code if last else '?'}): {last.text[:300] if last else ''}"
    )
