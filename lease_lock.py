"""
Mutual exclusion for the writers that share the workbook.

`sheets_client.write_dataframe` rewrites a whole tab from A1 down and value-clears
the rows below it. That is safe for one writer and only one writer: if a second
write lands mid-rewrite, whichever finishes last wins wholesale and the other's rows
are simply gone. Today the 4 AM sync is the only writer, so the problem is latent.
It stops being latent the moment anything else can write on demand.

The lock deliberately favours simplicity over throughput, because the traffic
justifies it: one scheduled run a day plus occasional event-driven writes. A waiter
blocking for a few minutes during the nightly run costs nothing; hand-built
cell-level merging of concurrent writers would cost a great deal.

Design notes
------------
* A LEASE, not a lock. A holder that crashes -- runner killed, container evicted --
  must not wedge the workbook until someone notices. The lease carries an expiry and
  anyone may take over once it passes.
* Compare-and-swap, not read-then-write. Acquisition is a conditional write that
  fails if anyone else wrote first, so two simultaneous acquirers cannot both win.
  This is the same primitive the token cache needs, hence the shared store.
* The store is injected. The lock has no idea it is talking to Cloud Storage, which
  keeps it testable offline and leaves room for a different backend later.
"""
from __future__ import annotations

import json
import random
import threading
import time
import uuid
from contextlib import contextmanager

DEFAULT_LOCK_NAME = "locks/workbook.json"


class LockBusy(RuntimeError):
    """Someone else holds the lease and it did not free up within the wait."""


class PreconditionFailed(RuntimeError):
    """The object changed underneath us -- another writer got there first."""


# --------------------------------------------------------------------------
# Stores. A store is any object with:
#     read(name)  -> (payload_bytes_or_None, generation_int)
#     write(name, payload_bytes, if_generation_match) -> new_generation
# where generation 0 means "does not exist", matching Cloud Storage's own
# convention for ifGenerationMatch=0 ("create only if absent").
# --------------------------------------------------------------------------
class InMemoryObjectStore:
    """A store that behaves like GCS for tests and single-process use.

    Thread-safe on purpose: the blocking test drives it from two threads, and a
    store that serialised its own compare-and-swap incorrectly would make the lock
    look correct when it is not.
    """

    def __init__(self):
        self._objects: dict[str, tuple[bytes, int]] = {}
        self._generation = 0
        self._mutex = threading.Lock()

    def read(self, name: str) -> tuple[bytes | None, int]:
        with self._mutex:
            payload, generation = self._objects.get(name, (None, 0))
            return payload, generation

    def write(self, name: str, payload: bytes, if_generation_match: int) -> int:
        with self._mutex:
            _, generation = self._objects.get(name, (None, 0))
            if generation != if_generation_match:
                raise PreconditionFailed(
                    f"{name}: generation {generation} != expected {if_generation_match}")
            self._generation += 1
            self._objects[name] = (payload, self._generation)
            return self._generation


class GCSObjectStore:
    """Cloud Storage via its JSON API, using credentials already in the stack.

    Deliberately not `google-cloud-storage`: `google-auth` and `requests` are
    already dependencies for the Sheets write, and this needs two calls.

    NOT covered by the offline tests -- there is no network in them. Smoke-test it
    against a real bucket before relying on it.
    """

    _READ = "https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}?alt=media"
    _WRITE = ("https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
              "?uploadType=media&name={name}&ifGenerationMatch={gen}")

    def __init__(self, bucket: str, session=None, credentials=None):
        self.bucket = bucket
        if session is None:
            from google.auth.transport.requests import AuthorizedSession
            if credentials is None:
                import google.auth
                credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
            session = AuthorizedSession(credentials)
        self.session = session

    @staticmethod
    def _quote(name: str) -> str:
        from urllib.parse import quote
        return quote(name, safe="")

    # Cloud Storage rate-limits MUTATIONS PER OBJECT (roughly one write a second),
    # separately from any project-wide quota. A lock is by definition one object
    # that contended callers all write at once, so a burst of acquirers earns a 429
    # -- observed with eight racers against a real bucket, where three died. The
    # in-memory store has no such limit, which is why no offline test could find it.
    #
    # Retrying a CONDITIONAL write is safe: ifGenerationMatch makes it idempotent,
    # so a retry of a write that actually succeeded comes back 412, which is the
    # correct answer anyway. Jitter breaks up the synchronised herd that caused it.
    _RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

    def _sleep_before_retry(self, resp, delay: float) -> float:
        wait = delay
        try:
            wait = float(resp.headers.get("Retry-After", delay))
        except (TypeError, ValueError):
            pass
        time.sleep(min(wait, 30.0) + random.uniform(0, 0.25))
        return min(delay * 2, 16.0)

    def read(self, name: str, tries: int = 5) -> tuple[bytes | None, int]:
        delay, last = 0.5, None
        for _ in range(tries):
            resp = self.session.get(
                self._READ.format(bucket=self.bucket, name=self._quote(name)),
                timeout=30)
            if resp.status_code == 404:
                return None, 0
            if resp.status_code == 200:
                # Generation of the bytes just read; the conditional write needs it.
                return resp.content, int(resp.headers.get("x-goog-generation", 0))
            if resp.status_code in self._RETRY_STATUSES:
                last = resp
                delay = self._sleep_before_retry(resp, delay)
                continue
            raise RuntimeError(f"GCS read {name} failed ({resp.status_code}): "
                               f"{resp.text[:200]}")
        raise RuntimeError(
            f"GCS read {name} failed after {tries} attempts "
            f"({last.status_code if last else '?'}): "
            f"{last.text[:200] if last else ''}")

    def write(self, name: str, payload: bytes, if_generation_match: int,
              tries: int = 5) -> int:
        delay, last = 0.5, None
        for _ in range(tries):
            resp = self.session.post(
                self._WRITE.format(bucket=self.bucket, name=self._quote(name),
                                   gen=if_generation_match),
                data=payload, headers={"Content-Type": "application/json"},
                timeout=30)
            if resp.status_code == 412:
                # Lost the race. Never retried -- it is an answer, not a failure.
                raise PreconditionFailed(f"{name}: generation moved on")
            if resp.status_code in (200, 201):
                return int(resp.json().get("generation", 0))
            if resp.status_code in self._RETRY_STATUSES:
                last = resp
                delay = self._sleep_before_retry(resp, delay)
                continue
            raise RuntimeError(f"GCS write {name} failed ({resp.status_code}): "
                               f"{resp.text[:200]}")
        raise RuntimeError(
            f"GCS write {name} failed after {tries} attempts "
            f"({last.status_code if last else '?'}): "
            f"{last.text[:200] if last else ''}")


# --------------------------------------------------------------------------
class LeaseLock:
    """One writer at a time, with a lease so a crash cannot wedge it forever.

    ttl  : seconds a lease stays valid. Must comfortably exceed the longest write
           a holder can legitimately perform, or a slow holder gets overtaken
           mid-write -- precisely the situation the lock exists to prevent.
    poll : seconds between attempts while waiting.
    """

    def __init__(self, store, name: str = DEFAULT_LOCK_NAME, ttl: float = 600.0,
                 poll: float = 2.0, holder: str = "", clock=time.time,
                 sleep=time.sleep):
        self.store = store
        self.name = name
        self.ttl = float(ttl)
        self.poll = float(poll)
        self.holder = holder or "unnamed"
        self._clock = clock
        self._sleep = sleep

    # -- internals ---------------------------------------------------------
    def _state(self):
        payload, generation = self.store.read(self.name)
        if not payload:
            return None, generation
        try:
            return json.loads(payload.decode("utf-8")), generation
        except (ValueError, UnicodeDecodeError):
            # Unreadable lock file: treat as free rather than blocking every
            # writer forever on one corrupt object.
            return None, generation

    def _claim(self, generation: int, token: str) -> bool:
        body = json.dumps({
            "holder": self.holder,
            "token": token,
            "acquired_at": self._clock(),
            "expires_at": self._clock() + self.ttl,
        }).encode("utf-8")
        try:
            self.store.write(self.name, body, if_generation_match=generation)
            return True
        except PreconditionFailed:
            return False

    # -- api ---------------------------------------------------------------
    def acquire(self, wait: float = 900.0) -> str:
        """Block until the lease is ours, returning the token that proves it."""
        deadline = self._clock() + float(wait)
        token = uuid.uuid4().hex
        while True:
            state, generation = self._state()
            now = self._clock()
            free = state is None or float(state.get("expires_at", 0)) <= now
            if free and self._claim(generation, token):
                return token
            if self._clock() >= deadline:
                who = (state or {}).get("holder", "unknown")
                raise LockBusy(
                    f"'{self.name}' still held by {who!r} after {wait:g}s. "
                    "Another writer is mid-run; try again once it finishes.")
            self._sleep(min(self.poll, max(deadline - self._clock(), 0)))

    def release(self, token: str) -> bool:
        """Give up the lease. False if we no longer hold it.

        The token check is the point: if our lease expired and someone else took
        over, releasing must NOT clear their lock. A slow holder coming back to
        tidy up would otherwise hand the workbook to a third writer mid-write.
        """
        state, generation = self._state()
        if not state or state.get("token") != token:
            return False
        body = json.dumps({"holder": None, "token": None, "expires_at": 0}).encode("utf-8")
        try:
            self.store.write(self.name, body, if_generation_match=generation)
            return True
        except PreconditionFailed:
            return False

    def renew(self, token: str) -> bool:
        """Extend our lease. For a holder that legitimately runs long."""
        state, generation = self._state()
        if not state or state.get("token") != token:
            return False
        state["expires_at"] = self._clock() + self.ttl
        try:
            self.store.write(self.name, json.dumps(state).encode("utf-8"),
                             if_generation_match=generation)
            return True
        except PreconditionFailed:
            return False

    @contextmanager
    def hold(self, wait: float = 900.0):
        """`with lock.hold(): ...` -- released even if the body raises."""
        token = self.acquire(wait=wait)
        try:
            yield token
        finally:
            self.release(token)
