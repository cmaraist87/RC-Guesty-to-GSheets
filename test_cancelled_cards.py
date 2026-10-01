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


def test_the_update_body_sends_the_card_back_whole():
    """PUT is a REPLACE. A field left out is a field volunteered for deletion, so
    the card goes back as it was read with exactly one value changed -- the same
    care job_share takes with a Job. openSpots is the one exception: v1 rejects
    it outright on an update and v2 does not want it either."""
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
        "notes": "something the team added",
    }
    Spy().recolour_shift("19713722", existing, CANCELLED_COLOR)

    assert sent["method"] == "PUT", sent["method"]
    assert sent["path"] == "/scheduler/v2/schedulers/19713722/shifts", sent["path"]
    assert isinstance(sent["body"], list) and len(sent["body"]) == 1
    body = sent["body"][0]
    assert body["shiftId"] == "6abda1fb827287b856d7f9db", body["shiftId"]
    assert body["color"] == CANCELLED_COLOR
    assert "openSpots" not in body, "v1 rejects open_spots on an update"
    assert "id" not in body, "the compound id must not travel beside shiftId"
    # Everything else, including a field we know nothing about, survives.
    for k in ("jobId", "title", "startTime", "endTime", "timezone",
              "isOpenShift", "isPublished", "notes"):
        assert body[k] == existing[k], (k, body.get(k), existing[k])
    assert body["assignedUserIds"] == []
    print("OK: the update replaces one field and carries the rest back verbatim")


def test_an_update_cannot_assign_anybody():
    """The standing rule holds on the update path too, not just on create."""
    from connecteam_client import ConnecteamClient

    class Spy(ConnecteamClient):
        def __init__(self):
            pass
        def _request(self, method, path, body=None, tries=4):
            raise AssertionError("the request must never be reached")

    poisoned = {"id": "a:b", "title": "Clean", "startTime": 1, "endTime": 2,
                "isOpenShift": True, "assignedUserIds": ["someone"], "openSpots": 1}
    try:
        Spy().recolour_shift("19713722", poisoned, CANCELLED_COLOR)
    except ValueError as e:
        print(f"OK: an update carrying an assignee is refused -> {str(e)[:60]}")
    else:
        raise AssertionError("an update with assignedUserIds was NOT refused")


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
    test_the_update_body_sends_the_card_back_whole()
    test_an_update_cannot_assign_anybody()
    print("\nALL CANCELLED-CARD TESTS PASSED")
