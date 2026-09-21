"""No market board can be written to. Not by a flag, not by a workflow input.

Run: python test_market_board_locked.py

Chris, 2026-09-21: "Nothing goes anywhere else except Chris Test until I sign
off on testing the live boards."

Earlier that day I asked whether a live Austin write was acceptable, read the
answer as standing permission, and pushed 38 cards to the Austin board. They
were deleted again the same day. This file exists so the rule is enforced by
the code rather than remembered by me.
"""
import io
import re

import connecteam_push
from connecteam_map import CITY_SCHEDULERS, TEST_SCHEDULER

WORKFLOW = ".github/workflows/inspect.yml"


def test_live_without_test_is_refused():
    """The gate itself, exercised. It must refuse before anything is read --
    no sheet, no API key, no network."""
    rc = connecteam_push.main(["--city", "Austin", "--live"])
    assert rc == 2, ("--live without --test must be refused", rc)
    print("OK: --live alone is refused, for every market")


def test_every_market_is_refused_not_merely_austin():
    for city in sorted(CITY_SCHEDULERS):
        rc = connecteam_push.main(["--city", city, "--live"])
        assert rc == 2, (city, rc)
    print(f"OK: all {len(CITY_SCHEDULERS)} market names are refused with --live")


def test_the_workflow_offers_no_way_to_ask_for_a_market_write():
    """A rule that lives in a workflow input is one dispatch away from being
    picked. The input must not exist at all."""
    yml = io.open(WORKFLOW, encoding="utf-8").read()
    assert "WRITE-TO-MARKET-BOARD-LIVE" not in yml, \
        "the market-write option is back in the workflow"
    # Every --live in the workflow is bolted to --test on the same line.
    for line in yml.splitlines():
        if "--live" in line and "python" in line:
            assert "--test" in line, f"--live without --test in the workflow: {line.strip()}"
    print("OK: the workflow has no market-write input, and pairs --live with --test")


def test_the_test_board_is_not_a_market_board():
    assert TEST_SCHEDULER not in set(CITY_SCHEDULERS.values()), TEST_SCHEDULER
    print(f"OK: the test board {TEST_SCHEDULER} is not any market's board")


if __name__ == "__main__":
    test_live_without_test_is_refused()
    test_every_market_is_refused_not_merely_austin()
    test_the_workflow_offers_no_way_to_ask_for_a_market_write()
    test_the_test_board_is_not_a_market_board()
    print("\nALL MARKET-BOARD-LOCK TESTS PASSED")
