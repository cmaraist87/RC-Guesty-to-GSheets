"""Only a market Chris has signed off can be written to. One name, so far.

Run: python test_market_board_locked.py

Chris, 2026-09-21: "Nothing goes anywhere else except Chris Test until I sign
off on testing the live boards." Earlier that day I asked whether a live Austin
write was acceptable, read the answer as standing permission, and pushed 38 cards
to the Austin board. They were deleted again the same day.

Chris signed AUSTIN off on 2026-10-07, in answer to a question that named the
board, the window and the card count. The other four remain shut, and this file
is what keeps them shut.

WHY THIS FILE WAS REWRITTEN
---------------------------
It used to assert that every market is refused, and it kept PASSING after Austin
was signed off -- because it only checked the exit code, and Austin exits 2 as
well when there is no API key locally. A test that passes for the wrong reason is
worse than no test: it says the rule holds while the rule has changed underneath
it. So the refusal is now identified by its MESSAGE, which only the gate emits.
"""
import io

import connecteam_push
from connecteam_map import CITY_SCHEDULERS, LIVE_MARKETS, TEST_SCHEDULER

WORKFLOW = ".github/workflows/inspect.yml"
NIGHTLY = ".github/workflows/daily-sync.yml"
REFUSAL = "is not signed off for live writes"


def _refused(city, capsys_free=True):
    """Did the SIGN-OFF GATE refuse this city? Not merely 'did it fail'.

    Checked on the message, because a non-zero exit proves nothing here: a
    signed-off city exits non-zero too when the API key is absent, which is how
    the old version of this file passed after the rule changed.
    """
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = connecteam_push.main(["--city", city, "--live"])
    return REFUSAL in err.getvalue(), rc


def test_only_the_signed_off_markets_are_live():
    assert LIVE_MARKETS == frozenset({"austin"}), (
        f"LIVE_MARKETS is {set(LIVE_MARKETS)}. Every name in it can write to a "
        f"real crew board, so each one needs Chris saying so unprompted.")
    print(f"OK: signed off = {sorted(LIVE_MARKETS)}")


def test_every_market_that_is_not_signed_off_is_refused():
    locked = sorted(set(CITY_SCHEDULERS) - set(LIVE_MARKETS))
    assert locked, "no market is locked any more; that needs a deliberate check"
    for city in locked:
        hit, rc = _refused(city)
        assert hit, (f"{city} was NOT refused by the sign-off gate", rc)
        assert rc == 2, (city, rc)
    print(f"OK: {len(locked)} market(s) still refused -- {', '.join(locked)}")


def test_a_signed_off_market_is_not_refused_by_the_gate():
    """The other half. If this ever fails, the sign-off is not actually in force
    and a go-live would silently do nothing."""
    for city in sorted(LIVE_MARKETS):
        hit, _rc = _refused(city)
        assert not hit, f"{city} is in LIVE_MARKETS but the gate still refuses it"
    print(f"OK: {sorted(LIVE_MARKETS)} passes the gate, as signed off")


def test_the_workflow_offers_no_way_to_ask_for_a_market_write():
    """A rule that lives in a workflow input is one dispatch away from being
    picked. Every --live in either workflow is bolted to --test on the same line,
    so the only route to a market board is a deliberate command, not a dispatch."""
    for path in (WORKFLOW, NIGHTLY):
        yml = io.open(path, encoding="utf-8").read()
        assert "WRITE-TO-MARKET-BOARD-LIVE" not in yml, \
            f"the market-write option is back in {path}"
        for line in yml.splitlines():
            bare = line.strip()
            if bare.startswith("#"):
                continue        # a comment is not a command, and the nightly's
                                # comments discuss --live at length
            if "connecteam_push.py" in bare and "--live" in bare:
                assert "--test" in bare, \
                    f"--live without --test in {path}: {bare}"
    print("OK: neither workflow can ask for a market write; every --live has --test")


def test_the_test_board_is_not_a_market_board():
    assert TEST_SCHEDULER not in set(CITY_SCHEDULERS.values()), TEST_SCHEDULER
    print(f"OK: the test board {TEST_SCHEDULER} is not any market's board")


def test_greying_is_still_test_board_only():
    """Signing a market off for CARDS does not sign it off for grey
    cancellations. Chris asked for those on Chris Test "for now", and that is a
    separate decision that must not arrive with the first one."""
    from connecteam_map import CANCELLED_COLOR_BOARDS
    assert CANCELLED_COLOR_BOARDS == frozenset({TEST_SCHEDULER}), \
        CANCELLED_COLOR_BOARDS
    for city in sorted(LIVE_MARKETS):
        board = CITY_SCHEDULERS[city]
        assert str(board) not in CANCELLED_COLOR_BOARDS, (
            f"{city} is live for cards AND would grey cancellations; those are "
            f"two decisions")
    print("OK: a live market still does not grey cancellations")


if __name__ == "__main__":
    test_only_the_signed_off_markets_are_live()
    test_every_market_that_is_not_signed_off_is_refused()
    test_a_signed_off_market_is_not_refused_by_the_gate()
    test_the_workflow_offers_no_way_to_ask_for_a_market_write()
    test_the_test_board_is_not_a_market_board()
    test_greying_is_still_test_board_only()
    print("")
    print("ALL MARKET-BOARD-LOCK TESTS PASSED")
