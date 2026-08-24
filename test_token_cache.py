"""
Offline tests for the shared Guesty token cache (no network, no Cloud Storage).

Every test injects a counting `minter`, because the property that matters is not
"a token came back" but "how many times did we spend quota to get it".

Run: python test_token_cache.py
"""
import json
import os
import tempfile
import threading

from guesty_client import (GuestyError, SharedTokenCache, StoreUnavailable,
                           _EXPIRY_SKEW_SEC, _now)
from lease_lock import InMemoryObjectStore, LeaseLock


class CountingMinter:
    """Stands in for Guesty's token endpoint and counts every call."""

    def __init__(self, ttl=86400, clock=None):
        self.calls = 0
        self.ttl = ttl
        self._clock = clock
        self.lock = threading.Lock()

    def __call__(self, client_id, client_secret, session=None):
        with self.lock:
            self.calls += 1
            n = self.calls
        # Real time when no virtual clock is injected, or the minted token would
        # be stamped as expiring in 1970 and every caller would refresh it again.
        now = self._clock() if self._clock else _now()
        return f"token-{n}", now + self.ttl


class BrokenStore:
    """A store that is reachable by nobody -- stands in for a GCS outage."""

    def read(self, name):
        raise ConnectionError("bucket unreachable")

    def write(self, name, payload, if_generation_match):
        raise ConnectionError("bucket unreachable")


def _tmp_cache():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)          # want the PATH, not the file
    return path


def _cache(store, clock, minter, cache_path=None, **kw):
    return SharedTokenCache(
        store, cache_path=cache_path, clock=clock, sleep=lambda s: None,
        minter=minter, **kw)


def test_live_token_is_used_without_minting_or_locking():
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(clock=lambda: now[0])
    c = _cache(store, lambda: now[0], minter)

    first = c.get("id", "secret")
    assert minter.calls == 1, "the empty cache should have been filled once"

    # Every later caller reads the object and mints nothing.
    for _ in range(5):
        assert c.get("id", "secret") == first
    assert minter.calls == 1, minter.calls

    # ...and no lease was ever taken, since nothing needed refreshing.
    payload, _ = store.read("locks/guesty-token.json")
    state = json.loads(payload.decode()) if payload else {}
    assert state.get("token") in (None, ""), state
    print("OK: a live token is served straight from the cache, no lock, no mint")


def test_expired_token_is_refreshed_once():
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(ttl=3_600, clock=lambda: now[0])
    c = _cache(store, lambda: now[0], minter)

    assert c.get("id", "secret") == "token-1"
    now[0] += 3_601                      # past expiry
    assert c.get("id", "secret") == "token-2"
    assert minter.calls == 2, minter.calls
    print("OK: an expired token is refreshed exactly once")


def test_expiry_skew_is_preserved():
    """A token 60s from expiry is NOT usable -- the 120s skew covers clock drift
    and the latency of the request the token is about to be used for."""
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(ttl=_EXPIRY_SKEW_SEC + 60, clock=lambda: now[0])
    c = _cache(store, lambda: now[0], minter)

    assert c.get("id", "secret") == "token-1"
    now[0] += 61                         # 119s of life left: inside the skew
    assert c.get("id", "secret") == "token-2", "a token inside the skew was reused"
    assert minter.calls == 2
    print(f"OK: the {_EXPIRY_SKEW_SEC}s expiry skew still forces an early refresh")


def test_only_one_of_many_racers_mints():
    """The reason this class exists. Eight callers find an empty cache at the same
    moment; exactly one may spend quota and the rest must read what it wrote."""
    store = InMemoryObjectStore()
    minter = CountingMinter()
    tokens, barrier = [], threading.Barrier(8)

    def racer():
        c = SharedTokenCache(store, cache_path=None, minter=minter,
                             wait=30, lease_ttl=30)
        barrier.wait()
        tokens.append(c.get("id", "secret"))

    threads = [threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert minter.calls == 1, f"{minter.calls} callers minted a token"
    assert len(tokens) == 8 and len(set(tokens)) == 1, set(tokens)
    print("OK: eight simultaneous callers, one mint, everyone gets the same token")


def test_non_minting_caller_waits_then_fails():
    """A handler must never mint: N instances doing so is the exhaustion this
    whole design prevents. With nothing in the cache it fails, and quota is safe."""
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(clock=lambda: now[0])

    def advancing_sleep(seconds):        # let the wait loop reach its deadline
        now[0] += max(seconds, 1.0)

    c = SharedTokenCache(store, cache_path=None, clock=lambda: now[0],
                         sleep=advancing_sleep, minter=minter, wait=5)
    try:
        c.get("id", "secret", may_mint=False)
        raise AssertionError("a non-minting caller minted a token")
    except GuestyError as e:
        assert "may not mint" in str(e), e
    assert minter.calls == 0, "quota was spent by a caller that must not spend it"
    print("OK: a non-minting caller fails rather than spending quota")


def test_non_minting_caller_picks_up_a_refresh_in_flight():
    """It should not fail instantly either -- if a refresh lands while it waits,
    it uses it."""
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(clock=lambda: now[0])
    writer = _cache(store, lambda: now[0], minter)

    ticks = {"n": 0}

    def sleep_then_refresh(seconds):
        ticks["n"] += 1
        now[0] += max(seconds, 1.0)
        if ticks["n"] == 2:              # another runtime refreshes mid-wait
            writer.get("id", "secret")

    c = SharedTokenCache(store, cache_path=None, clock=lambda: now[0],
                         sleep=sleep_then_refresh, minter=minter, wait=30)
    assert c.get("id", "secret", may_mint=False) == "token-1"
    assert minter.calls == 1, "the waiting caller minted its own"
    print("OK: a non-minting caller picks up a refresh that lands while it waits")


def test_gcs_outage_cron_falls_back_and_may_mint():
    now = [1_000.0]
    minter = CountingMinter(clock=lambda: now[0])
    path = _tmp_cache()
    try:
        c = _cache(BrokenStore(), lambda: now[0], minter, cache_path=path)
        assert c.get("id", "secret", may_mint=True) == "token-1"
        assert minter.calls == 1
        # The local cache was written, so a second call costs nothing.
        assert c.get("id", "secret", may_mint=True) == "token-1"
        assert minter.calls == 1, "the local fallback cache was not reused"
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print("OK: during an outage the cron falls back locally and mints at most once")


def test_gcs_outage_handler_refuses_to_mint():
    now = [1_000.0]
    minter = CountingMinter(clock=lambda: now[0])
    path = _tmp_cache()
    try:
        c = _cache(BrokenStore(), lambda: now[0], minter, cache_path=path)
        try:
            c.get("id", "secret", may_mint=False)
            raise AssertionError("the handler minted during an outage")
        except GuestyError as e:
            assert "may not mint" in str(e), e
        assert minter.calls == 0

        # But a valid local token is still honoured -- no reason to fail then.
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"access_token": "local-tok", "expires_at": now[0] + 9_999}, fh)
        assert c.get("id", "secret", may_mint=False) == "local-tok"
        assert minter.calls == 0
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print("OK: during an outage the handler uses a local token or fails, never mints")


def test_corrupt_cache_object_is_replaced_not_fatal():
    now = [1_000.0]
    store = InMemoryObjectStore()
    store.write("guesty/token.json", b"{not json", if_generation_match=0)
    minter = CountingMinter(clock=lambda: now[0])
    c = _cache(store, lambda: now[0], minter)

    assert c.get("id", "secret") == "token-1"
    payload, _ = store.read("guesty/token.json")
    assert json.loads(payload.decode())["access_token"] == "token-1"
    print("OK: an unparseable cache object is overwritten rather than wedging callers")


def test_store_unavailable_is_distinct_from_a_refusal():
    """A refusal (PreconditionFailed) means someone else won a race and is normal.
    Unreachable means the bucket is gone. Conflating them would send a lost race
    down the fallback path and mint a token nobody needed."""
    store = InMemoryObjectStore()
    c = SharedTokenCache(store, cache_path=None, minter=CountingMinter())
    c.store = BrokenStore()
    try:
        c._read()
        raise AssertionError("a broken store read did not raise")
    except StoreUnavailable:
        pass
    print("OK: an unreachable store is reported as StoreUnavailable")


def test_lease_holder_that_dies_does_not_wedge_the_cache():
    """The lease is TTL'd, so a refresher killed mid-flight cannot block refreshes
    forever -- the next caller takes over once it lapses."""
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(clock=lambda: now[0])

    # A dead holder leaves the lease taken and no token behind.
    dead = LeaseLock(store, name="locks/guesty-token.json", ttl=30, poll=1,
                     holder="crashed", clock=lambda: now[0], sleep=lambda s: None)
    dead.acquire()

    c = _cache(store, lambda: now[0], minter, wait=5)
    now[0] += 31                          # the lease lapses
    assert c.get("id", "secret") == "token-1"
    assert minter.calls == 1
    print("OK: a lease left by a dead refresher lapses and the next caller proceeds")


def test_empty_shared_cache_seeds_from_a_live_local_token():
    """Cutover case: the shared object is empty on its very first use, while a
    perfectly good token often sits on disk from an earlier run. Minting there
    would spend quota purely because the cache is new."""
    now = [1_000.0]
    store = InMemoryObjectStore()
    minter = CountingMinter(clock=lambda: now[0])
    path = _tmp_cache()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"access_token": "already-have-one",
                       "expires_at": now[0] + 9_999}, fh)
        c = _cache(store, lambda: now[0], minter, cache_path=path)

        assert c.get("id", "secret") == "already-have-one"
        assert minter.calls == 0, "spent quota despite holding a live local token"
        # ...and it is now shared, so the next runtime does not need the disk.
        stored = json.loads(store.read("guesty/token.json")[0].decode())
        assert stored["access_token"] == "already-have-one", stored

        # An EXPIRED local token must not be seeded -- that would share a dead token.
        store2 = InMemoryObjectStore()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"access_token": "stale", "expires_at": now[0] - 1}, fh)
        c2 = _cache(store2, lambda: now[0], minter, cache_path=path)
        assert c2.get("id", "secret") == "token-1"
        assert minter.calls == 1
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print("OK: an empty shared cache is seeded from a live local token, not a mint")


def test_every_path_logs_whether_quota_was_spent():
    """An Actions log must answer "did this run spend a token request?" without
    anyone opening the bucket. Every outcome says so in one greppable line."""
    import io
    from contextlib import redirect_stdout

    def line_for(fn):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn()
        out = [l for l in buf.getvalue().splitlines() if l.startswith("Guesty token:")]
        assert len(out) == 1, f"expected exactly one line, got {out}"
        return out[0]

    store = InMemoryObjectStore()
    minter = CountingMinter()
    c = SharedTokenCache(store, cache_path=None, minter=minter)

    minted = line_for(lambda: c.get("i", "s"))
    assert "MINTED" in minted and "quota spent" in minted, minted

    reused = line_for(lambda: c.get("i", "s"))
    assert "REUSED" in reused and "no request spent" in reused, reused

    path = _tmp_cache()
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"access_token": "local", "expires_at": _now() + 9_999}, fh)
        seeded = line_for(lambda: SharedTokenCache(
            InMemoryObjectStore(), cache_path=path, minter=CountingMinter()).get("i", "s"))
        assert "SEEDED" in seeded and "no request spent" in seeded, seeded

        fell_back = line_for(lambda: SharedTokenCache(
            BrokenStore(), cache_path=path, minter=CountingMinter()).get("i", "s"))
        assert "UNREACHABLE" in fell_back and "no request spent" in fell_back, fell_back
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print("OK: every token path logs one line saying whether quota was spent")


def test_sync_falls_back_when_no_bucket_is_configured():
    """STATE_BUCKET unset must keep the old single-process behaviour rather than
    failing -- that is the escape hatch if the bucket is ever unavailable."""
    import sync

    calls = []
    import guesty_client
    real = guesty_client.get_access_token
    guesty_client.get_access_token = lambda cid, sec, *a, **k: calls.append(cid) or "legacy"
    try:
        assert sync.state_store({"state_bucket": ""}) is None
        assert sync.guesty_token({"state_bucket": "", "client_id": "x",
                                  "client_secret": "y"}) == "legacy"
        assert calls == ["x"], calls
    finally:
        guesty_client.get_access_token = real
    print("OK: with no bucket configured the sync uses the local token path")


if __name__ == "__main__":
    test_live_token_is_used_without_minting_or_locking()
    test_expired_token_is_refreshed_once()
    test_expiry_skew_is_preserved()
    test_only_one_of_many_racers_mints()
    test_non_minting_caller_waits_then_fails()
    test_non_minting_caller_picks_up_a_refresh_in_flight()
    test_gcs_outage_cron_falls_back_and_may_mint()
    test_gcs_outage_handler_refuses_to_mint()
    test_corrupt_cache_object_is_replaced_not_fatal()
    test_store_unavailable_is_distinct_from_a_refusal()
    test_lease_holder_that_dies_does_not_wedge_the_cache()
    test_empty_shared_cache_seeds_from_a_live_local_token()
    test_every_path_logs_whether_quota_was_spent()
    test_sync_falls_back_when_no_bucket_is_configured()
    print("\nALL TOKEN-CACHE TESTS PASSED")
