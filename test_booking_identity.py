"""Colour follows the BOOKING, not the slot. A moved booking keeps its card.

Run: python test_booking_identity.py

Chris, 2026-10-06, correcting me: "a booking whose time changes stays green.
Active bookings stay green or the blue color if turnover. Only cancelled or
removed bookings turn gray. So based on the Confirmation Code..."

What I had built keyed on the slot -- (property, title, start instant) -- so a
booking whose time moved looked like one card vanishing and another appearing,
and the vanished one went grey. A crew would have read that as "this clean is
off" when it was merely an hour later.

The reason the Confirmation Code is necessary rather than just tidier is that
STRIKETHROUGH IS AMBIGUOUS. The sheet strikes a row for `moved` as well as for
`cancelled` (sheets_client._STRIKE_NEW_FLAGS), and both are only a line through
the cells. Reading the format, those two are identical. Reading the code, they
are not: a moved booking still has a live row somewhere.
"""
import pandas as pd

from connecteam_cards import (booking_activity, cards_for_tab, changes, code_of,
                              colour_for, pair_by_code)
from connecteam_map import CANCELLED_COLOR, STANDARD_COLOR, TURNOVER_COLOR

COL = "Confirmation Code"


def _row(code, date="2026-10-13", out="11:00 AM", to="", prop="6504 Porter A"):
    return {COL: code, "Property": prop, "City": "Austin", "Date": date,
            "Check-out Time": out, "T/O": to}


def _frame(*rows):
    return pd.DataFrame(list(rows))


# ------------------------------------------------------------------- the code

def test_the_code_is_normalised():
    """The sheet is hand-edited; the same code turns up padded and in odd case."""
    assert code_of({COL: " hmabc123 "}) == "HMABC123"
    assert code_of({COL: "HMABC123"}) == "HMABC123"
    assert code_of({"CONFIRMATION CODE": "gy-xyz"}) == "GY-XYZ"
    assert code_of({COL: ""}) == ""
    assert code_of({"Property": "x"}) == ""
    print("OK: codes normalise, and a row with none says so")


# --------------------------------------------------------------- active or not

def test_one_live_row_makes_the_booking_active():
    """The whole point. A booking with a struck row AND a live row is ACTIVE."""
    f = _frame(_row("HMA"), _row("HMA", date="2026-10-15"))
    act = booking_activity([(f, {0})])          # row 0 struck, row 1 live
    assert act["HMA"] is True, act
    print("OK: struck + live for one code reads as active")


def test_a_code_struck_everywhere_is_cancelled():
    f = _frame(_row("HMB"), _row("HMB", date="2026-10-15"))
    act = booking_activity([(f, {0, 1})])
    assert act["HMB"] is False, act
    print("OK: struck on every row reads as cancelled")


def test_activity_is_judged_across_every_tab():
    """A booking can move from one month into the next. Judging tab by tab, the
    October half would look cancelled and go grey for a job still happening."""
    oct_tab = _frame(_row("HMC", date="2026-10-30"))
    nov_tab = _frame(_row("HMC", date="2026-11-02"))
    act = booking_activity([(oct_tab, {0}), (nov_tab, set())])
    assert act["HMC"] is True, act
    print("OK: a booking that moved into the next month is still active")


def test_an_empty_tab_is_harmless():
    act = booking_activity([(pd.DataFrame(), set()), (None, set())])
    assert act == {}, act
    print("OK: an empty or missing tab contributes nothing")


# ------------------------------------------------------------------- colouring

def test_active_bookings_keep_their_colour():
    act = {"HMD": True}
    assert colour_for(_row("HMD"), act) == STANDARD_COLOR
    assert colour_for(_row("HMD", to="yes"), act) == TURNOVER_COLOR
    print("OK: active = green, or blue for a turnover")


def test_only_a_cancelled_booking_is_grey():
    act = {"HME": False}
    assert colour_for(_row("HME"), act) == CANCELLED_COLOR
    # Even a turnover. Cancelled beats turnover: it is not urgent work, it is
    # not work.
    assert colour_for(_row("HME", to="yes"), act) == CANCELLED_COLOR
    print("OK: cancelled = grey, turnover or not")


def test_a_code_nobody_has_seen_is_grey():
    """The 'removed' safety net. Should never fire, because cancellations stay in
    the sheet, which is exactly why it must not be silent when it does."""
    assert colour_for(_row("HMZZ"), {}) == CANCELLED_COLOR
    print("OK: a code absent from the sheet is treated as removed")


# ------------------------------------------------- the move, end to end

def test_a_moved_booking_yields_ONE_green_card_at_the_new_time():
    """The regression this file exists for.

    The sheet holds the old slot struck and the new slot live. Before this
    change that produced two cards: a grey one at the old time and a green one
    at the new. It must produce exactly one, green, at the new time.
    """
    f = _frame(_row("HMF", date="2026-10-13", out="11:00 AM"),
               _row("HMF", date="2026-10-15", out="11:00 AM"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    assert len(got) == 1, [(c, p["color"]) for _r, c, p in got]
    _row_obj, code, payload = got[0]
    assert code == "HMF"
    assert payload["color"] == STANDARD_COLOR, payload["color"]
    print("OK: a moved booking gives one green card, and it is the live row's")


def test_a_time_change_on_the_same_day_stays_green():
    """Chris' exact words: 'a booking whose time changes stays green.' Here the
    row is UPDATED in place, so it is amber rather than struck."""
    f = _frame(_row("HMG", out="12:00 PM"))
    act = booking_activity([(f, set())])
    got = cards_for_tab(f, set(), act)
    assert len(got) == 1 and got[0][2]["color"] == STANDARD_COLOR
    print("OK: an updated (amber) row stays green")


def test_a_cancelled_booking_still_gets_its_grey_card():
    f = _frame(_row("HMH"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    assert len(got) == 1, got
    assert got[0][2]["color"] == CANCELLED_COLOR
    print("OK: a cancelled booking keeps a card, in grey")


def test_a_struck_row_with_no_checkout_still_makes_no_card():
    """Most struck rows are ARRIVAL rows. No departure, nothing to clean."""
    f = _frame(_row("HMI", out=""))
    act = booking_activity([(f, {0})])
    assert cards_for_tab(f, {0}, act) == []
    print("OK: a struck arrival row produces no card")


def test_a_cancellation_and_its_replacement_are_two_bookings():
    """Same property, same day, two codes: one cancelled, one live. Two cards,
    one grey and one green, because they are two different bookings."""
    f = _frame(_row("HMJ"), _row("HMK"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    colours = sorted(p["color"] for _r, _c, p in got)
    assert colours == sorted([CANCELLED_COLOR, STANDARD_COLOR]), colours
    print("OK: a cancellation and its replacement stay distinct")


# --------------------------------------------------------------------- pairing

def _have(jid, start, colour=STANDARD_COLOR, sid="s1"):
    return {"id": sid, "jobId": jid, "startTime": start, "endTime": start + 900,
            "color": colour, "title": "Clean"}


def _want(jid, start, colour=STANDARD_COLOR):
    return ({}, "HM1", {"jobId": jid, "startTime": start, "endTime": start + 900,
                        "color": colour, "title": "Clean"})


def test_a_moved_booking_is_paired_with_its_old_card():
    pairs, create, orphan = pair_by_code([_want("J1", 2000)], [_have("J1", 1000)])
    assert len(pairs) == 1 and not create and not orphan
    assert changes(pairs[0][0][2], pairs[0][1]) == {"startTime": (1000, 2000),
                                                   "endTime": (1900, 2900)}
    print("OK: the same booking at a new time pairs with the card it already has")


def test_a_combined_listing_pairs_by_property_not_by_time():
    """One booking, two units, identical instants. Pairing by time alone would
    shuffle a card from one flat to the other."""
    want = [_want("JA", 1000), _want("JB", 1000)]
    have = [_have("JB", 1000, sid="b"), _have("JA", 1000, sid="a")]
    pairs, create, orphan = pair_by_code(want, have)
    assert not create and not orphan
    for w, h in pairs:
        assert w[2]["jobId"] == h["jobId"], (w[2]["jobId"], h["jobId"])
    print("OK: cards stay with their own unit")


def test_a_booking_that_changes_property_keeps_its_card():
    """No jobId in common, so it falls through to order -- which is how 'move the
    existing card' survives a change of unit."""
    pairs, create, orphan = pair_by_code([_want("JNEW", 1000)],
                                         [_have("JOLD", 1000)])
    assert len(pairs) == 1 and not create and not orphan
    assert changes(pairs[0][0][2], pairs[0][1]) == {"jobId": ("JOLD", "JNEW")}
    print("OK: a booking that moved unit keeps its card, with a new jobId")


def test_extra_wanted_cards_are_created_and_extra_existing_are_orphans():
    pairs, create, orphan = pair_by_code(
        [_want("J1", 1000), _want("J2", 1000)], [_have("J1", 1000)])
    assert len(pairs) == 1 and len(create) == 1 and not orphan
    pairs, create, orphan = pair_by_code(
        [_want("J1", 1000)], [_have("J1", 1000), _have("J9", 5000, sid="x")])
    assert len(pairs) == 1 and not create and len(orphan) == 1
    print("OK: unmatched wants are created, unmatched cards are reported")


def test_changes_ignores_fields_the_team_own():
    """An update is a REPLACE, so anything the team added has to survive it. The
    diff only looks at what a booking can actually change."""
    have = _have("J1", 1000)
    have.update({"notes": ["crew note"], "tasks": [{"t": 1}],
                 "isRequireAdminApproval": True})
    want = _want("J1", 1000)[2]
    assert changes(want, have) == {}, changes(want, have)
    print("OK: notes, tasks and approval flags are not 'changes'")


def test_changes_sees_a_recolour_on_its_own():
    have = _have("J1", 1000, colour=STANDARD_COLOR)
    want = _want("J1", 1000, colour=CANCELLED_COLOR)[2]
    assert changes(want, have) == {"color": (STANDARD_COLOR, CANCELLED_COLOR)}
    print("OK: a cancellation with no time change is still a change")


if __name__ == "__main__":
    test_the_code_is_normalised()
    test_one_live_row_makes_the_booking_active()
    test_a_code_struck_everywhere_is_cancelled()
    test_activity_is_judged_across_every_tab()
    test_an_empty_tab_is_harmless()
    test_active_bookings_keep_their_colour()
    test_only_a_cancelled_booking_is_grey()
    test_a_code_nobody_has_seen_is_grey()
    test_a_moved_booking_yields_ONE_green_card_at_the_new_time()
    test_a_time_change_on_the_same_day_stays_green()
    test_a_cancelled_booking_still_gets_its_grey_card()
    test_a_struck_row_with_no_checkout_still_makes_no_card()
    test_a_cancellation_and_its_replacement_are_two_bookings()
    test_a_moved_booking_is_paired_with_its_old_card()
    test_a_combined_listing_pairs_by_property_not_by_time()
    test_a_booking_that_changes_property_keeps_its_card()
    test_extra_wanted_cards_are_created_and_extra_existing_are_orphans()
    test_changes_ignores_fields_the_team_own()
    test_changes_sees_a_recolour_on_its_own()
    print("\nALL BOOKING-IDENTITY TESTS PASSED")
