"""A cancelled booking's card goes light gray -- on Chris Test, for now.

Run: python test_cancelled_cards.py

Asked for by Chris on 2026-09-30: "Cancellations should be painted the light
gray, only in Chris Test for now."

Three separate things have to hold, and they fail in different ways:

  * the colour is #969696, and the API accepts it. It only became reachable on
    2026-09-30: the client truncated error bodies at 400 characters, the
    rejection that ENUMERATES the palette is longer than that, and so 14 of the
    33 colours -- including all three greys -- were invisible. ALLOWED_COLORS
    said 19 and the probe written to check it read the same truncated string and
    agreed. Hence test_the_palette_is_the_whole_palette below.

  * a cancellation cannot be read off a row. It is strikethrough on the cells,
    not a value in them, so `cancelled` is passed IN by a caller that has read
    the sheet's FORMAT. A test that builds a row dict and expects grey would be
    testing a thing the code cannot do.

  * the gate is per BOARD and separate from the card lock. The card lock says
    which boards may have cards at all; this says which get grey cancellations
    once they do.
"""
import pandas as pd

from connecteam_map import (ALLOWED_COLORS, CANCELLED_COLOR,
                            CANCELLED_COLOR_BOARDS, CITY_SCHEDULERS,
                            STANDARD_COLOR, TEST_SCHEDULER, TURNOVER_COLOR,
                            shift_for_row, shifts_for_rows)


def _row(**over):
    r = {"Property": "1802 Martin Luther King", "City": "Austin",
         "Date": "2026-10-05", "Check-out Time": "11:00 AM", "T/O": ""}
    r.update(over)
    return r


def test_a_cancelled_clean_is_light_gray():
    assert shift_for_row(_row(), cancelled=True)["color"] == CANCELLED_COLOR
    assert CANCELLED_COLOR == "#969696", CANCELLED_COLOR
    print("OK: a cancelled clean is #969696")


def test_cancelled_beats_turnover():
    """The precedence that matters. A cancelled turnover is not urgent work with
    a caveat -- it is not work. Painting it light royal blue because the booking
    it replaced would have been tight is exactly backwards."""
    t = _row(**{"T/O": "yes"})
    assert shift_for_row(t)["color"] == TURNOVER_COLOR
    assert shift_for_row(t, cancelled=True)["color"] == CANCELLED_COLOR
    print("OK: cancelled wins over turnover")


def test_a_live_booking_is_untouched():
    """The regression that would matter most: everything that is not cancelled
    must keep the colour it had before any of this existed."""
    assert shift_for_row(_row())["color"] == STANDARD_COLOR
    assert shift_for_row(_row(), cancelled=False)["color"] == STANDARD_COLOR
    assert shift_for_row(_row(**{"T/O": "yes"}))["color"] == TURNOVER_COLOR
    print("OK: live cleans stay green, live turnovers stay blue")


def test_the_three_colours_are_distinguishable():
    """Not a palette check -- a human check. Three cards on one board have to be
    tellable apart at a glance, so no two may be the same value."""
    three = {CANCELLED_COLOR, STANDARD_COLOR, TURNOVER_COLOR}
    assert len(three) == 3, three
    print(f"OK: three distinct colours in use -> {sorted(three)}")


def test_positions_select_the_right_rows():
    """cancelled_pos holds 0-based DATA-row positions, which is the frame's own
    index. Off by one here paints the wrong booking grey and leaves a real
    cancellation looking live, so it is pinned per row rather than in aggregate."""
    df = pd.DataFrame([_row(Property="A"), _row(Property="B"), _row(Property="C")])
    got = [p["color"] for _r, p in shifts_for_rows(df, cancelled_pos={1})]
    assert got == [STANDARD_COLOR, CANCELLED_COLOR, STANDARD_COLOR], got

    # And nothing at all when nothing is struck.
    none = [p["color"] for _r, p in shifts_for_rows(df)]
    assert none == [STANDARD_COLOR] * 3, none
    print("OK: only the struck position goes gray, and by default none do")


def test_an_empty_position_set_changes_nothing():
    """The gate's closed state. A board not in CANCELLED_COLOR_BOARDS gets an
    empty set, and that must behave exactly like the old code."""
    df = pd.DataFrame([_row(), _row(**{"T/O": "yes"})])
    for empty in (frozenset(), set()):
        got = [p["color"] for _r, p in shifts_for_rows(df, cancelled_pos=empty)]
        assert got == [STANDARD_COLOR, TURNOVER_COLOR], got
    print("OK: an empty position set is indistinguishable from before")


def test_the_gate_is_the_test_board_only():
    """"only in Chris Test for now" -- 2026-09-30."""
    assert CANCELLED_COLOR_BOARDS == frozenset({TEST_SCHEDULER}), CANCELLED_COLOR_BOARDS
    for city, board in CITY_SCHEDULERS.items():
        assert str(board) not in CANCELLED_COLOR_BOARDS, (
            f"{city}'s board {board} would grey cancellations; Chris said Chris "
            f"Test only")
    print(f"OK: grey cancellations are gated to {TEST_SCHEDULER} and no market board")


def test_the_palette_is_the_whole_palette():
    """33, not 19. The three greys are the last three, and none were reachable
    while the client clipped error bodies at 400 characters."""
    assert len(ALLOWED_COLORS) == 33, len(ALLOWED_COLORS)
    assert len(set(ALLOWED_COLORS)) == 33, "a duplicate crept into the palette"
    for grey in ("#3a3a3a", "#616161", "#969696"):
        assert grey in ALLOWED_COLORS, f"{grey} went missing from the palette"
    print("OK: all 33 colours recorded, greys included")


def test_every_colour_we_send_is_one_the_api_named():
    """One bad colour fails the whole batch, so this is not cosmetic."""
    for name, value in (("CANCELLED_COLOR", CANCELLED_COLOR),
                        ("STANDARD_COLOR", STANDARD_COLOR),
                        ("TURNOVER_COLOR", TURNOVER_COLOR)):
        assert value in ALLOWED_COLORS, f"{name}={value} would be rejected (1002)"
    for cancelled in (True, False):
        for to in ("", "yes"):
            c = shift_for_row(_row(**{"T/O": to}), cancelled=cancelled)["color"]
            assert c in ALLOWED_COLORS, c
    print("OK: every colour any row can produce is on the palette")


def test_a_cancelled_card_is_still_unassigned():
    """Greying a card must not become a back door around the standing rule."""
    from connecteam_map import assert_unassigned
    s = shift_for_row(_row(), cancelled=True)
    assert s["isOpenShift"] is True
    assert s["assignedUserIds"] == []
    assert_unassigned([s])              # raises if it is not an open shift
    print("OK: a cancelled card is Unassigned like every other card")


def test_a_cancelled_row_with_no_checkout_is_still_not_a_job():
    """Cancellation does not conjure a card. Most struck rows are ARRIVAL rows
    with no check-out -- 1802 Martin Luther King on 2026-10-05 is one -- and a
    row with no departure has nothing to clean whether it is cancelled or not."""
    assert shift_for_row(_row(**{"Check-out Time": ""}), cancelled=True) is None
    print("OK: a struck arrival row still produces no card")


def test_the_update_id_is_the_half_before_the_colon():
    """The single most arbitrary fact in all of this, so it is pinned.

    A card's id on this board is compound. DELETE wants the WHOLE string; v2's
    update wants only the part before the colon and answers "shift id is
    invalid" (1004) for anything else. Nothing documents it; shift_update_probe
    established it on 2026-09-30 by trying both halves.
    """
    from connecteam_client import ConnecteamClient
    full = "6abda1fb827287b856d7f9db:4acf6287-ee55-4e94-a479-f2a99e246463"
    assert ConnecteamClient.update_id({"id": full}) == "6abda1fb827287b856d7f9db"
    # Already short, or carried under the other key -- both must still work.
    assert ConnecteamClient.update_id({"id": "abc123"}) == "abc123"
    assert ConnecteamClient.update_id({"shiftId": full}) == full.split(":")[0]
    assert ConnecteamClient.update_id({}) == ""
    print("OK: the update id is the half before the colon")


def test_the_update_sends_ONLY_what_changed():
    """v2 MERGES, so the body carries the id and the changed fields and nothing
    else.

    This test used to assert the opposite -- that the whole card goes back, on
    the assumption PUT replaces. That assumption was wrong twice over. It was
    unnecessary, and it was actively broken: a real card carries `locationData`
    derived from its Job, and v2 refuses it with error_code 1004, "can't set
    locat...". Every update against a real card failed that way on 2026-10-07,
    while the probe reported the call working, because the probe's card had no
    jobId and so had no location to set.

    Sending only what changed cannot hit a field the server refuses, and cannot
    drop a note or a task the team added, because neither is sent.
    """
    from connecteam_client import ConnecteamClient

    sent = {}

    class Spy(ConnecteamClient):
        def __init__(self):
            pass                       # no key, no session: nothing leaves here
        def _request(self, method, path, body=None, tries=4):
            sent.update({"method": method, "path": path, "body": body})
            return {}

    existing = {
        "id": "6abda1fb827287b856d7f9db:4acf6287-ee55",
        "jobId": "84ee8d80", "title": "Clean", "color": "#91B282",
        "startTime": 1790000000, "endTime": 1790000900,
        "timezone": "America/Chicago", "isOpenShift": True,
        "assignedUserIds": [], "openSpots": 1, "isPublished": True,
        "notes": ["something the team added"],
        "locationData": {"address": "1802 Martin Luther King"},
    }
    Spy().update_shift("19713722", existing, {"color": CANCELLED_COLOR})

    assert sent["method"] == "PUT", sent["method"]
    assert sent["path"] == "/scheduler/v2/schedulers/19713722/shifts", sent["path"]
    assert isinstance(sent["body"], list) and len(sent["body"]) == 1
    body = sent["body"][0]
    assert body == {"shiftId": "6abda1fb827287b856d7f9db",
                    "color": CANCELLED_COLOR}, body
    # The two that actually matter, stated separately so a failure names itself.
    assert "locationData" not in body, "locationData is refused with code 1004"
    assert "notes" not in body, "a note the team added must not be re-sent"
    print("OK: the update sends the id and the changed field, nothing else")


def test_a_move_sends_both_times_and_the_property():
    """A moved booking changes three things at once, and all three must travel."""
    from connecteam_client import ConnecteamClient

    sent = {}

    class Spy(ConnecteamClient):
        def __init__(self):
            pass
        def _request(self, method, path, body=None, tries=4):
            sent.update({"body": body})
            return {}

    existing = {"id": "a:b", "isOpenShift": True, "assignedUserIds": [],
                "startTime": 1, "endTime": 2, "jobId": "OLD", "title": "Clean"}
    Spy().update_shift("19713722", existing,
                       {"startTime": 99, "endTime": 999, "jobId": "NEW"})
    assert sent["body"][0] == {"shiftId": "a", "startTime": 99, "endTime": 999,
                               "jobId": "NEW"}, sent["body"][0]
    print("OK: a move carries startTime, endTime and jobId together")


def test_an_update_refuses_a_card_somebody_is_on():
    """Checked against the card as the BOARD has it, because the body no longer
    passes through assert_unassigned itself."""
    from connecteam_client import ConnecteamClient

    class Spy(ConnecteamClient):
        def __init__(self):
            pass
        def _request(self, method, path, body=None, tries=4):
            raise AssertionError("the request must never be reached")

    theirs = {"id": "a:b", "isOpenShift": True, "title": "Clean",
              "assignedUserIds": ["someone"]}
    try:
        Spy().update_shift("19713722", theirs, {"color": CANCELLED_COLOR})
    except ValueError as e:
        print(f"OK: a card with somebody on it is refused -> {str(e)[:52]}")
    else:
        raise AssertionError("an assigned card was NOT refused")


def test_the_board_gate_actually_reaches_the_colour():
    """The regression of 2026-10-08, pinned.

    CANCELLED_COLOR_BOARDS is Chris' choice of which boards grey their
    cancellations. Between 2026-10-06 and 2026-10-08 it controlled only the
    "removed booking" path: the colour of a CANCELLED booking came from
    colour_for, which had no gate, so a live Savannah push printed "cancellations
    are NOT greyed there" and greyed six cards anyway.

    A log line asserting the opposite of what the code did is worse than either
    behaviour on its own, which is why this is a test and not a comment.
    """
    from connecteam_cards import cards_for_tab, colour_for
    row = {"Confirmation Code": "HMDEAD", "T/O": ""}
    dead = {"HMDEAD": False}
    assert colour_for(row, dead, True) == CANCELLED_COLOR
    assert colour_for(row, dead, False) == STANDARD_COLOR, (
        "with the gate shut a cancelled booking must look like a live one; "
        "that is what 'not greyed there' means")

    to = {"Confirmation Code": "HMDEAD", "T/O": "yes"}
    assert colour_for(to, dead, False) == TURNOVER_COLOR
    assert colour_for(to, dead, True) == CANCELLED_COLOR
    print("OK: the gate reaches the cancelled colour, both ways")


def test_the_gate_travels_all_the_way_through_cards_for_tab():
    """Checked at the level the push calls, not just the leaf function -- the bug
    was a parameter that existed and was never passed."""
    import pandas as pd
    from connecteam_cards import cards_for_tab
    frame = pd.DataFrame([{"Confirmation Code": "HMD", "Property": "X",
                           "City": "Austin", "Date": "2026-10-20",
                           "Check-out Time": "11:00 AM", "T/O": ""}])
    active = {"HMD": False}            # cancelled
    on = cards_for_tab(frame, {0}, active, grey_cancellations=True)
    off = cards_for_tab(frame, {0}, active, grey_cancellations=False)
    assert on[0][2]["color"] == CANCELLED_COLOR, on[0][2]["color"]
    assert off[0][2]["color"] == STANDARD_COLOR, off[0][2]["color"]
    print("OK: cards_for_tab honours the gate it is given")


def test_the_default_is_to_grey():
    """A caller that says nothing gets greying, because a cancelled booking shown
    as live work is the dangerous direction to fail in."""
    from connecteam_cards import colour_for
    row = {"Confirmation Code": "HMD", "T/O": ""}
    assert colour_for(row, {"HMD": False}) == CANCELLED_COLOR
    print("OK: greying is the default; the gate has to be shut deliberately")


if __name__ == "__main__":
    test_a_cancelled_clean_is_light_gray()
    test_cancelled_beats_turnover()
    test_a_live_booking_is_untouched()
    test_the_three_colours_are_distinguishable()
    test_positions_select_the_right_rows()
    test_an_empty_position_set_changes_nothing()
    test_the_gate_is_the_test_board_only()
    test_the_palette_is_the_whole_palette()
    test_every_colour_we_send_is_one_the_api_named()
    test_a_cancelled_card_is_still_unassigned()
    test_a_cancelled_row_with_no_checkout_is_still_not_a_job()
    test_the_update_id_is_the_half_before_the_colon()
    test_the_update_sends_ONLY_what_changed()
    test_a_move_sends_both_times_and_the_property()
    test_an_update_refuses_a_card_somebody_is_on()
    test_the_board_gate_actually_reaches_the_colour()
    test_the_gate_travels_all_the_way_through_cards_for_tab()
    test_the_default_is_to_grey()
    print("\nALL CANCELLED-CARD TESTS PASSED")
