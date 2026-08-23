"""
Offline tests for the workbook lease lock (no network, no Cloud Storage).

Run: python test_lease_lock.py
"""
import threading
import time

from lease_lock import (InMemoryObjectStore, LeaseLock, LockBusy,
                        PreconditionFailed)


def test_second_acquirer_waits_for_the_first():
    """The whole point: while one writer holds the workbook, another blocks --
    and is let through only once the first releases, not before."""
    store = InMemoryObjectStore()
    first = LeaseLock(store, ttl=60, poll=0.02, holder="nightly-sync")
    second = LeaseLock(store, ttl=60, poll=0.02, holder="on-demand")

    token = first.acquire()
    acquired_at: list[float] = []

    def waiter():
        t = second.acquire(wait=5)
        acquired_at.append(time.monotonic())
        second.release(t)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()

    # Give the waiter real time to try and fail repeatedly.
    time.sleep(0.15)
    assert not acquired_at, "second writer got in while the first still held the lock"

    released_at = time.monotonic()
    assert first.release(token) is True
    thread.join(timeout=5)

    assert acquired_at, "second writer never acquired after the lock was released"
    assert acquired_at[0] >= released_at, (
        "second writer's acquire predates the release -- both held it at once")
    print("OK: a second writer blocks until the first releases")


def test_expired_lease_is_reclaimed():
    """A holder that dies mid-write must not wedge the workbook. Once the lease
    lapses, the next writer takes over -- and the dead holder cannot take it back."""
    now = [1_000.0]
    slept: list[float] = []

    def clock():
        return now[0]

    def sleep(seconds):          # virtual time: deterministic, no wall-clock waiting
        slept.append(seconds)
        now[0] += seconds

    store = InMemoryObjectStore()
    crashed = LeaseLock(store, ttl=30, poll=5, holder="crashed",
                        clock=clock, sleep=sleep)
    survivor = LeaseLock(store, ttl=30, poll=5, holder="survivor",
                         clock=clock, sleep=sleep)

    dead_token = crashed.acquire()          # ...and then never releases it

    # While the lease is live, nobody else gets in.
    try:
        survivor.acquire(wait=0)
        raise AssertionError("acquired a lease that was still held")
    except LockBusy as e:
        assert "crashed" in str(e), e

    now[0] += 31                            # lease lapses
    live_token = survivor.acquire(wait=0)
    assert live_token != dead_token

    # The crashed holder coming back must NOT clear someone else's lock.
    assert crashed.release(dead_token) is False, (
        "a stale holder released a lease it no longer owned")
    # ...and the rightful holder still can.
    assert survivor.release(live_token) is True
    print("OK: a stale lease is reclaimed, and the dead holder cannot steal it back")


def test_only_one_of_many_racers_wins():
    """Compare-and-swap, not read-then-write: simultaneous acquirers cannot both win."""
    store = InMemoryObjectStore()
    winners: list[str] = []
    barrier = threading.Barrier(8)

    def racer(n):
        lock = LeaseLock(store, ttl=60, poll=0.01, holder=f"w{n}")
        barrier.wait()
        try:
            winners.append(lock.acquire(wait=0))
        except LockBusy:
            pass

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(winners) == 1, f"{len(winners)} writers acquired the same lock"
    print("OK: eight simultaneous acquirers, exactly one winner")


def test_hold_releases_even_when_the_body_raises():
    store = InMemoryObjectStore()
    lock = LeaseLock(store, ttl=60, poll=0.01, holder="writer")
    try:
        with lock.hold():
            raise RuntimeError("write blew up")
    except RuntimeError:
        pass
    # If the lease leaked, this would block and then raise LockBusy.
    token = lock.acquire(wait=0)
    assert token
    print("OK: hold() releases the lease when the write raises")


def test_renew_extends_and_rejects_impostors():
    now = [500.0]
    store = InMemoryObjectStore()
    lock = LeaseLock(store, ttl=30, poll=1, holder="slow-writer",
                     clock=lambda: now[0], sleep=lambda s: None)
    token = lock.acquire()

    now[0] += 25
    assert lock.renew(token) is True
    now[0] += 20                     # would have lapsed without the renewal
    other = LeaseLock(store, ttl=30, poll=1, holder="other",
                      clock=lambda: now[0], sleep=lambda s: None)
    try:
        other.acquire(wait=0)
        raise AssertionError("renewed lease was overtaken")
    except LockBusy:
        pass
    assert lock.renew("not-the-token") is False
    print("OK: renew() extends a live lease and refuses a wrong token")


def test_store_rejects_a_stale_generation():
    """Guard on the test double itself -- if its compare-and-swap were wrong, the
    lock tests above would pass while the real lock was broken."""
    store = InMemoryObjectStore()
    gen = store.write("x", b"one", if_generation_match=0)
    try:
        store.write("x", b"two", if_generation_match=0)
        raise AssertionError("write succeeded against a stale generation")
    except PreconditionFailed:
        pass
    assert store.write("x", b"two", if_generation_match=gen) > gen
    assert store.read("x")[0] == b"two"
    print("OK: the in-memory store enforces compare-and-swap")


class _Resp:
    def __init__(self, code, text="", headers=None, js=None, content=b""):
        self.status_code, self.text = code, text
        self.headers, self._js, self.content = headers or {}, js or {}, content

    def json(self):
        return self._js


class _Session:
    """Replays a scripted sequence of responses and counts the attempts."""

    def __init__(self, responses):
        self.responses, self.i, self.calls = responses, 0, 0

    def _next(self):
        self.calls += 1
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return r

    def post(self, *a, **k):
        return self._next()

    def get(self, *a, **k):
        return self._next()


def test_rate_limited_write_is_retried():
    """Cloud Storage rate-limits mutations PER OBJECT -- about one write a second.
    A lock is one object that contended callers all write at once, so a burst of
    acquirers earns a 429. Eight racers against the real bucket killed three of
    them before this retry existed, and no in-memory test could have caught it,
    because the double has no such limit."""
    from lease_lock import GCSObjectStore
    import lease_lock as ll

    real_sleep, ll.time.sleep = ll.time.sleep, lambda s: None
    try:
        sess = _Session([_Resp(429, "rate limited", {"Retry-After": "0"}),
                         _Resp(429, "rate limited"),
                         _Resp(200, js={"generation": 42})])
        store = GCSObjectStore("bucket", session=sess)
        assert store.write("o", b"x", if_generation_match=0) == 42
        assert sess.calls == 3, sess.calls

        # A 412 is an ANSWER, not a failure -- retrying it would turn a correctly
        # lost race into a second write attempt.
        sess = _Session([_Resp(412, "precondition")])
        store = GCSObjectStore("bucket", session=sess)
        try:
            store.write("o", b"x", if_generation_match=1)
            raise AssertionError("a 412 was retried or swallowed")
        except PreconditionFailed:
            pass
        assert sess.calls == 1, sess.calls

        # Sustained limiting gives up with something actionable.
        sess = _Session([_Resp(429, "still limited")])
        store = GCSObjectStore("bucket", session=sess)
        try:
            store.write("o", b"x", if_generation_match=0, tries=3)
            raise AssertionError("persistent 429 did not raise")
        except RuntimeError as e:
            assert "after 3 attempts" in str(e), e

        # Reads retry on the same statuses.
        sess = _Session([_Resp(429, "rl"),
                         _Resp(200, headers={"x-goog-generation": "7"}, content=b"{}")])
        store = GCSObjectStore("bucket", session=sess)
        assert store.read("o") == (b"{}", 7)

        # A real error is not mistaken for a transient one.
        sess = _Session([_Resp(403, "forbidden")])
        store = GCSObjectStore("bucket", session=sess)
        try:
            store.write("o", b"x", if_generation_match=0)
            raise AssertionError("a 403 was swallowed")
        except RuntimeError as e:
            assert "403" in str(e) and "attempts" not in str(e), e
        assert sess.calls == 1, "a 403 was retried"
    finally:
        ll.time.sleep = real_sleep
    print("OK: per-object rate limiting is retried; 412 and 403 are not")


if __name__ == "__main__":
    test_store_rejects_a_stale_generation()
    test_rate_limited_write_is_retried()
    test_second_acquirer_waits_for_the_first()
    test_expired_lease_is_reclaimed()
    test_only_one_of_many_racers_wins()
    test_hold_releases_even_when_the_body_raises()
    test_renew_extends_and_rejects_impostors()
    print("\nALL LEASE-LOCK TESTS PASSED")
