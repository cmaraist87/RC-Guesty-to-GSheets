"""The rule that keeps a delete from taking somebody's shift.

Run: python test_shift_delete.py

Deleting arrived on 2026-09-21 so the 37 stale cards on the test board could
go. Three of the five it refused that day were old cards an admin had assigned
while testing -- which is exactly the case worth being certain about, because a
filter is an argument and "never delete somebody's shift" is a rule.
"""
from shift_delete import deletable


def card(title="", users=None, start=0):
    return {"title": title, "assignedUserIds": users or [], "startTime": start}


def test_an_assigned_card_is_never_deletable():
    safe, refused = deletable([card("2901 E 3rd A", users=[3188776])])
    assert safe == [] and len(refused) == 1, (safe, refused)
    print("OK: a card with somebody on it is refused")


def test_an_assigned_card_is_refused_even_when_the_title_filter_selects_it():
    """The filter narrows what may go; it never overrides the rule."""
    safe, refused = deletable([card("PROBE baseline", users=[42])], "PROBE")
    assert safe == [] and len(refused) == 1, (safe, refused)
    print("OK: a title filter cannot reach past the assignment rule")


def test_an_open_card_is_deletable():
    safe, refused = deletable([card("PROBE 15min"), card("Clean")])
    assert len(safe) == 2 and refused == []
    print("OK: unassigned cards are fair game")


def test_the_title_filter_narrows_and_does_not_widen():
    cards = [card("PROBE baseline"), card("Clean"), card("1163 Webberville A")]
    safe, _ = deletable(cards, "probe")
    assert [c["title"] for c in safe] == ["PROBE baseline"], safe
    print("OK: only cards matching the filter are selected, case-insensitively")


def test_no_filter_means_everything_unassigned_in_the_window():
    cards = [card("a"), card("b", users=[1]), card("c")]
    safe, refused = deletable(cards)
    assert [c["title"] for c in safe] == ["a", "c"], safe
    assert [c["title"] for c in refused] == ["b"], refused
    print("OK: with no filter the window is the only bound, minus assignments")


if __name__ == "__main__":
    test_an_assigned_card_is_never_deletable()
    test_an_assigned_card_is_refused_even_when_the_title_filter_selects_it()
    test_an_open_card_is_deletable()
    test_the_title_filter_narrows_and_does_not_widen()
    test_no_filter_means_everything_unassigned_in_the_window()
    print("\nALL SHIFT-DELETE TESTS PASSED")
