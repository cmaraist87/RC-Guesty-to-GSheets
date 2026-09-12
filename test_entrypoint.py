"""The scheduled entry point itself -- main(), not just run().

Run: python test_entrypoint.py

This file exists because of 2026-09-08. The preflight added the day before called
open_spreadsheet from main(), but that name is imported inside run(). Every suite
passed, because every suite called run() directly and none had ever called main().
Both of that morning's runs died on `name 'open_spreadsheet' is not defined`.

`import sync` does not catch it either: a NameError inside a function is a runtime
error, not an import error. Only executing the path finds it.
"""
import sys
import types

import sync


class FakeStore:
    """Enough of the object store for the daily gate to claim a day."""

    def __init__(self):
        self.blobs = {}

    def read(self, name):
        return self.blobs.get(name), len(self.blobs)

    def write(self, name, payload, if_generation_match=None):
        self.blobs[name] = payload
        return len(self.blobs)


def _patched(open_spreadsheet, store=None, fetch=None):
    """Run main(['--scheduled']) with the network stubbed, and return its code."""
    import datetime as _dt

    import daily_gate
    import sheets_client

    # Pin the clock inside the gate's window. Without this the whole file passes
    # before noon Chicago and fails after it, which is worse than no test at all:
    # it went green all morning and only broke when the afternoon suite ran.
    # The gate's own behaviour is covered by test_daily_gate.
    morning = _dt.datetime(2026, 9, 9, 4, 30, tzinfo=daily_gate.TZ)
    real_should_run = daily_gate.should_run

    real = (sheets_client.open_spreadsheet, sync.state_store,
            sync.fetch_from_guesty, sync.run, sync.load_config)
    sheets_client.open_spreadsheet = open_spreadsheet
    daily_gate.should_run = lambda st, now=None, **kw: real_should_run(st, now=morning, **kw)
    sync.state_store = lambda cfg: FakeStore() if store is None else store
    sync.fetch_from_guesty = fetch or (lambda cfg: [])
    sync.run = lambda dry, res, cfg, ss=None, **kw: 0
    sync.load_config = lambda: dict(real[4](), sheet_id="sheet-1", sa_json="{}",
                                    client_id="id", client_secret="secret")
    try:
        return sync.main(["--scheduled"])
    finally:
        daily_gate.should_run = real_should_run
        (sheets_client.open_spreadsheet, sync.state_store,
         sync.fetch_from_guesty, sync.run, sync.load_config) = real


def test_the_scheduled_path_runs_end_to_end():
    """The regression itself: main() must be able to open the spreadsheet."""
    opened = []
    rc = _patched(lambda sheet_id, sa_json: opened.append(sheet_id) or object())
    assert rc == 0, rc
    assert opened == ["sheet-1"], opened
    print("OK: main(--scheduled) reaches the sheet and completes")


def test_a_sheet_that_will_not_open_costs_no_guesty_token():
    """The point of the preflight: find out before spending one of ~5 daily tokens."""
    fetched = []

    def boom(sheet_id, sa_json):
        raise RuntimeError("503 service unavailable")

    rc = _patched(boom, fetch=lambda cfg: fetched.append(1) or [])
    assert rc == 5, rc
    assert fetched == [], "Guesty must not be called once the sheet has failed"
    print("OK: an unopenable sheet exits 5 with no token spent")


def test_a_second_trigger_stands_down():
    store = FakeStore()
    first = _patched(lambda s, j: object(), store=store)
    second = _patched(lambda s, j: object(), store=store)
    assert first == 0 and second == 0, (first, second)
    print("OK: the day is claimed once; a later trigger exits cleanly")


def _unresolvable_globals(fn):
    """Names `fn` loads as GLOBALS that are neither module globals nor builtins.

    LOAD_GLOBAL specifically -- co_names also lists attribute names, which are not
    globals at all and produce nothing but noise. A name imported inside the
    function body is a local, so it never shows up here.
    """
    import builtins
    import dis

    mod = sys.modules[fn.__module__].__dict__
    return sorted({i.argval for i in dis.get_instructions(fn)
                   if i.opname == "LOAD_GLOBAL"
                   and i.argval not in mod
                   and not hasattr(builtins, i.argval)})


def test_every_global_main_loads_actually_exists():
    """A cheap net for the whole class of bug that broke 2026-09-08.

    A NameError inside a function is invisible to `import sync`, and invisible to
    any test that only calls run(). Reading the bytecode finds it without running
    anything.
    """
    assert _unresolvable_globals(sync.main) == [],         f"main() loads globals that do not exist: {_unresolvable_globals(sync.main)}"
    for fn in (sync.run, sync.fetch_from_guesty, sync.record_reservation_snapshot):
        assert _unresolvable_globals(fn) == [],             f"{fn.__name__}() loads globals that do not exist: {_unresolvable_globals(fn)}"
    print("OK: every global these functions load resolves")


def test_the_check_would_have_caught_the_real_bug():
    """Proof the net has holes the right size: a function calling a name it never
    imported must be flagged."""
    src = "def broken():" + chr(10) + "    return open_spreadsheet(1, 2)" + chr(10)
    ns = {}
    exec(src, ns)
    ns["broken"].__module__ = __name__
    found = _unresolvable_globals(ns["broken"])
    assert found == ["open_spreadsheet"], found
    print("OK: the check flags exactly the mistake that was shipped")


if __name__ == "__main__":
    test_the_scheduled_path_runs_end_to_end()
    test_a_sheet_that_will_not_open_costs_no_guesty_token()
    test_a_second_trigger_stands_down()
    test_every_global_main_loads_actually_exists()
    test_the_check_would_have_caught_the_real_bug()
    print("\nALL ENTRYPOINT TESTS PASSED")
