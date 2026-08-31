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

# lease_lock has no dependency on this module, so the import is one-directional.
# Its Cloud Storage client is loaded lazily inside GCSObjectStore, so importing
# here does not pull google-auth into a run that never touches the shared cache.
from lease_lock import LeaseLock, LockBusy, PreconditionFailed

TOKEN_URL = "https://open-api.guesty.com/oauth2/token"
BASE_URL = "https://open-api.guesty.com/v1"
DEFAULT_TOKEN_CACHE = ".guesty_token.json"

# refresh a little before the real expiry
_EXPIRY_SKEW_SEC = 120


class GuestyError(RuntimeError):
    pass


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_live(expires_at, clock=None) -> bool:
    """Is this token still good, allowing for clock skew and request latency?"""
    now = (clock or _now)()
    return bool(expires_at) and float(expires_at) - _EXPIRY_SKEW_SEC > now


def read_local_token(cache_path: str | None = DEFAULT_TOKEN_CACHE) -> tuple[str, float]:
    """(token, expires_at) from the on-disk cache, or ("", 0) if unusable."""
    if not cache_path or not os.path.exists(cache_path):
        return "", 0.0
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        return str(cached.get("access_token") or ""), float(cached.get("expires_at") or 0)
    except (ValueError, OSError, TypeError):
        return "", 0.0  # a corrupt cache is the same as no cache


def write_local_token(cache_path: str | None, token: str, expires_at: float) -> None:
    if not cache_path:
        return
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump({"access_token": token, "expires_at": expires_at}, fh)
    except OSError:
        pass  # a cache we cannot write is a slower run, not a failed one


def mint_token(client_id: str, client_secret: str,
               session: requests.Session | None = None) -> tuple[str, float]:
    """Spend one of the account's scarce token requests. (token, expires_at).

    Every caller of this is burning quota, so it lives in one place and every path
    that reaches it should have already failed to find a usable cached token.
    """
    if not client_id or not client_secret:
        raise GuestyError("Missing Guesty client_id / client_secret.")
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
    if resp.status_code == 429:
        # Not a credentials problem, and retrying will not help: the quota is ~5
        # token requests per key per 24h. Say so, because the generic "check your
        # credentials" message sends people to look in exactly the wrong place.
        raise GuestyError(
            "Token request rate-limited (429). Guesty allows only ~5 access-token "
            "requests per API key per 24h and this one is exhausted -- the "
            "credentials are fine. Each CI run burns one unless the token cache is "
            "restored, so several manual runs in a day will do it. Wait for the "
            "quota to roll over (up to 24h), then re-run. "
            f"Body: {resp.text[:200]}"
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
    return token, _now() + expires_in


def get_access_token(
    client_id: str,
    client_secret: str,
    cache_path: str | None = DEFAULT_TOKEN_CACHE,
    session: requests.Session | None = None,
) -> str:
    """A valid bearer token, reusing the on-disk cache until it nears expiry.

    Single-process behaviour, unchanged. When two runtimes share one account, use
    SharedTokenCache instead -- this path has no way to stop each of them minting
    its own token.
    """
    token, expires_at = read_local_token(cache_path)
    if token and _is_live(expires_at):
        return token
    token, expires_at = mint_token(client_id, client_secret, session)
    write_local_token(cache_path, token, expires_at)
    return token


def fetch_reservations(
    token: str,
    filters: list[dict] | None = None,
    fields: str | None = None,
    sort: str = "_id",
    page_size: int = 100,
    extra_params: dict | None = None,
    session: requests.Session | None = None,
    max_pages: int = 200,
) -> list[dict]:
    """
    Fetch all reservations matching `filters` (Guesty filter-object list),
    following limit/skip pagination.

    SORTS BY `_id`, NOT BY DATE. skip/limit paging is only stable when the sort key
    is unique. Sorting by `checkIn` ties constantly -- hundreds of bookings share a
    check-in date -- and the server is free to order tied documents differently on
    each page request. When it does, a document can land on two pages while another
    lands on none: the fetch comes back with duplicates AND silent holes.

    That is not theoretical. A 14,182-row fetch sorted by checkIn assembled only
    13,850 distinct reservations -- 332 duplicates, and by the same mechanism
    roughly 332 bookings never arrived at all. A booking that never arrives looks
    exactly like one that was cancelled, so the sheet would strike it through.
    `_id` is unique, so the ordering is total and paging cannot drift.

    Returns DISTINCT reservations, and complains loudly if the count Guesty reports
    disagrees with what was actually assembled.

    filters example:
        [{"field": "checkOut", "operator": "$gte", "value": "2026-08-01"},
         {"field": "status",   "operator": "$in",  "value": ["confirmed", "reserved"]}]
    """
    sess = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    results: list[dict] = []
    reported: int | None = None
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
        reported = total if total is not None else reported
        skip += page_size
        if not page or (total is not None and skip >= total):
            break

    # De-duplicate defensively. With a unique sort this should be a no-op; if it
    # ever is not, the paging has drifted again and we want to hear about it rather
    # than quietly write the same booking into the sheet twice.
    seen: set[str] = set()
    unique: list[dict] = []
    for r in results:
        rid = str(r.get("_id") or r.get("reservationId") or "").strip()
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        unique.append(r)

    if len(unique) != len(results):
        print(f"!! Pagination returned {len(results) - len(unique)} duplicate "
              f"reservation(s); de-duplicated to {len(unique)}.")
    if reported is not None and len(unique) < reported:
        # The dangerous direction. A booking that never arrived is indistinguishable
        # from one that was cancelled, so say so plainly instead of letting the
        # sheet strike it through.
        print(f"!! Guesty reports {reported} reservation(s) but only {len(unique)} "
              f"distinct one(s) were assembled -- {reported - len(unique)} missing.")
        print("   Missing bookings look exactly like cancellations. Do NOT trust "
              "this run's cancellations; re-run before writing to the sheet.")
    return unique


def fetch_listings(
    token: str,
    fields: str | None = None,
    page_size: int = 100,
    max_pages: int = 100,
    session: requests.Session | None = None,
) -> list[dict]:
    """
    Every listing on the account, for building a nickname -> city map.

    Separate from the reservation fetch on purpose: asking /reservations to project
    the nested listing address is what Guesty rejected, and a listing's city is a
    property of the LISTING, not of each booking -- so fetching it once per run is
    both cheaper and more honest than repeating it on 3000 reservations.
    """
    sess = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    results: list[dict] = []
    skip = 0
    for _ in range(max_pages):
        params = {"limit": page_size, "skip": skip}
        if fields:
            params["fields"] = fields
        resp = _get_with_retry(sess, f"{BASE_URL}/listings", headers, params,
                               what="Listings")
        payload = resp.json()
        page = payload.get("results")
        if page is None:
            page = (payload.get("data") or {}).get("results", []) \
                if isinstance(payload.get("data"), dict) else []
        results.extend(page)

        total = payload.get("count")
        skip += page_size
        if not page or (total is not None and skip >= total):
            break

    return results


def _get_with_retry(sess, url, headers, params, tries: int = 5,
                    what: str = "Reservations") -> requests.Response:
    """
    Retry 429s and 5xx, fail fast on anything else.

    The failure message deliberately carries the status of EVERY attempt and the
    `fields` projection that was sent. A run once died with "failed after 5 retries
    (?):" -- no status, no body, nothing to act on -- and the projection is exactly
    what tends to be at fault, so it belongs in the message. Neither the token nor
    any guest data is included; `fields` is a list of field names.
    """
    delay = 2.0
    last = None
    seen = []      # (status, body-snippet) per attempt, oldest first
    for attempt in range(tries):
        try:
            resp = sess.get(url, headers=headers, params=params, timeout=60)
        except requests.RequestException as e:  # DNS/TLS/timeout: retryable
            seen.append((type(e).__name__, str(e)[:120]))
            time.sleep(min(delay, 60))
            delay *= 2
            continue
        if resp.status_code == 200:
            return resp
        last = resp
        seen.append((resp.status_code, (resp.text or "").strip()[:200]))
        if resp.status_code == 429 or resp.status_code >= 500:
            # honour Retry-After when present, else exponential backoff
            wait = float(resp.headers.get("Retry-After", delay))
            time.sleep(min(wait, 60))
            delay *= 2
            continue
        raise GuestyError(_fail_msg(what, "failed", url, params, seen))
    raise GuestyError(_fail_msg(what, f"failed after {tries} attempts", url, params, seen))


def _fail_msg(what: str, outcome: str, url: str, params: dict, seen: list) -> str:
    attempts = "; ".join(f"[{i + 1}] {s}: {b or '(empty body)'}"
                         for i, (s, b) in enumerate(seen)) or "(no response at all)"
    fields = params.get("fields") or "(none)"
    return (f"Reservations GET {what}.\n"
            f"  url     : {url}\n"
            f"  attempts: {attempts}\n"
            f"  fields  : {fields}")


# --------------------------------------------------------------------------
# Shared token cache
#
# The 4 AM cron and an on-demand handler draw on ONE token quota. Left to
# themselves each would mint its own -- and a handler that scales to several
# instances would mint one per instance, exhausting the day's allowance in
# minutes and taking the cron down with it. So the token lives in a single
# object both runtimes can read, and exactly one of them may refresh it.
# --------------------------------------------------------------------------
TOKEN_OBJECT = "guesty/token.json"
TOKEN_LOCK_OBJECT = "locks/guesty-token.json"


class StoreUnavailable(RuntimeError):
    """The shared store could not be reached -- distinct from it saying 'no'."""


class SharedTokenCache:
    """One Guesty token, shared through a GCS object, refreshed by one caller.

    `may_mint` is the whole safety model, and it is asymmetric on purpose:

        cron    (may_mint=True)   one process, so falling back to its own token
                                  request costs at most one. The daily sync must
                                  survive a Cloud Storage outage.
        handler (may_mint=False)  N instances, so falling back would cost N. It
                                  fails the event instead and lets Guesty retry,
                                  by which time a minting caller has refreshed.

    Getting that backwards is how a burst of webhooks breaks the next morning's
    sync, which is the failure this class exists to prevent.
    """

    def __init__(self, store, name: str = TOKEN_OBJECT, lock=None,
                 cache_path: str | None = DEFAULT_TOKEN_CACHE,
                 lease_ttl: float = 120.0, wait: float = 90.0,
                 session: requests.Session | None = None, clock=None,
                 sleep=None, minter=None):
        self.store = store
        self.name = name
        self.cache_path = cache_path
        self.wait = float(wait)
        self.session = session
        self._clock = clock or _now
        self._sleep = sleep or time.sleep
        # Injectable so tests can prove "exactly one mint" without spending quota.
        self._mint = minter or mint_token
        self._last_expiry = 0.0
        self.lock = lock or LeaseLock(
            store, name=TOKEN_LOCK_OBJECT, ttl=lease_ttl, poll=1.0,
            holder="guesty-token-refresh", clock=self._clock, sleep=self._sleep)

    # -- store access ------------------------------------------------------
    def _read(self):
        """(token, expires_at, generation). Raises StoreUnavailable if unreachable."""
        try:
            payload, generation = self.store.read(self.name)
        except Exception as e:                        # noqa: BLE001
            raise StoreUnavailable(str(e)) from e
        if not payload:
            return "", 0.0, generation
        try:
            d = json.loads(payload.decode("utf-8"))
            return (str(d.get("access_token") or ""),
                    float(d.get("expires_at") or 0), generation)
        except (ValueError, UnicodeDecodeError, TypeError):
            # Corrupt object: treat as absent so a refresh can overwrite it,
            # rather than wedging every caller on unparseable bytes.
            return "", 0.0, generation

    def _live_token(self) -> str:
        token, expires_at, _ = self._read()
        if token and _is_live(expires_at, self._clock):
            self._last_expiry = expires_at
            return token
        return ""

    def _hours_left(self, expires_at) -> str:
        return f"{(float(expires_at) - self._clock()) / 3600:.1f}h"

    def _log(self, outcome: str, detail: str = "") -> None:
        """One line per run, prefixed so it can be grepped out of a CI log.

        Every path says whether quota was spent, because that is the question
        anyone reads these logs to answer.
        """
        print(f"Guesty token: {outcome}" + (f" ({detail})" if detail else ""))

    def _store_token(self, token: str, expires_at: float, generation: int) -> None:
        body = json.dumps({"access_token": token,
                           "expires_at": expires_at}).encode("utf-8")
        try:
            self.store.write(self.name, body, if_generation_match=generation)
        except PreconditionFailed:
            # Someone refreshed while we were minting. Theirs is just as valid and
            # ours still works for this call, so this is not an error.
            pass
        except Exception as e:                        # noqa: BLE001
            raise StoreUnavailable(str(e)) from e

    # -- fallback ----------------------------------------------------------
    def _fallback(self, client_id, client_secret, may_mint, why) -> str:
        token, expires_at = read_local_token(self.cache_path)
        if token and _is_live(expires_at, self._clock):
            self._log("shared cache UNREACHABLE, fell back to this machine's "
                      "local token, no request spent", why)
            return token
        if not may_mint:
            raise GuestyError(
                f"Shared token cache unreachable ({why}) and no valid local token. "
                "This caller may not mint one -- several instances doing so would "
                "exhaust the daily quota and break the scheduled sync. Failing this "
                "attempt instead; it is safe to retry once the cache is reachable.")
        self._log("shared cache UNREACHABLE and no local token, MINTED one -- "
                  "one of the daily quota spent", why)
        token, expires_at = self._mint(client_id, client_secret, self.session)
        write_local_token(self.cache_path, token, expires_at)
        return token

    # -- api ---------------------------------------------------------------
    def get(self, client_id: str = "", client_secret: str = "",
            may_mint: bool = True) -> str:
        """A valid token, minting one only if this caller is allowed to."""
        # 1) The overwhelmingly common path: a live token, no lock, no mint.
        try:
            token = self._live_token()
            if token:
                self._log("REUSED from the shared cache, no request spent",
                          f"expires in {self._hours_left(self._last_expiry)}")
                return token
        except StoreUnavailable as e:
            return self._fallback(client_id, client_secret, may_mint, str(e))

        # 2) Needs refreshing, and we may not. Someone else may be mid-refresh, so
        #    give them a moment -- but never take the lease, which would only get
        #    in the refresher's way.
        if not may_mint:
            deadline = self._clock() + self.wait
            while self._clock() < deadline:
                self._sleep(1.0)
                try:
                    token = self._live_token()
                except StoreUnavailable as e:
                    return self._fallback(client_id, client_secret, may_mint, str(e))
                if token:
                    self._log("picked up a refresh made by another runtime "
                              "while waiting, no request spent")
                    return token
            raise GuestyError(
                "No valid Guesty token in the shared cache and this caller may not "
                f"mint one (waited {self.wait:g}s for a refresh). Retry later; the "
                "scheduled sync refreshes the token daily.")

        # 3) We may mint. Take the lease so that only one caller does.
        try:
            with self.lock.hold(wait=self.wait):
                # Re-read INSIDE the lease: whoever we queued behind has very
                # likely just refreshed, and minting again would waste the quota
                # this whole mechanism exists to protect.
                token, expires_at, generation = self._read()
                if token and _is_live(expires_at, self._clock):
                    self._log("another runtime refreshed it while we held the "
                              "queue, no request spent")
                    return token
                # Before spending quota, check whether this machine already holds a
                # live token. On the very first run the shared object is empty while
                # a perfectly good token often sits on disk from a previous run --
                # minting there would waste a request purely because the cache is new.
                local, local_exp = read_local_token(self.cache_path)
                if local and _is_live(local_exp, self._clock):
                    self._log("SEEDED the shared cache from this machine's local "
                              "token, no request spent",
                              f"expires in {self._hours_left(local_exp)}")
                    self._store_token(local, local_exp, generation)
                    return local
                token, expires_at = self._mint(client_id, client_secret, self.session)
                self._log("MINTED a new token -- one of the daily quota spent",
                          f"valid for {self._hours_left(expires_at)}")
                self._store_token(token, expires_at, generation)
                write_local_token(self.cache_path, token, expires_at)
                return token
        except LockBusy:
            # The holder should have written a token by now.
            try:
                token = self._live_token()
            except StoreUnavailable as e:
                return self._fallback(client_id, client_secret, may_mint, str(e))
            if token:
                self._log("another runtime refreshed it while we waited for the "
                          "lease, no request spent")
                return token
            raise GuestyError(
                "Timed out waiting for another caller to refresh the Guesty token, "
                "and no valid token appeared. A refresh may have died mid-flight; "
                "the lease expires on its own, so a retry should succeed.")
        except StoreUnavailable as e:
            return self._fallback(client_id, client_secret, may_mint, str(e))
