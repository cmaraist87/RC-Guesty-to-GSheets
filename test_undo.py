"""The before-snapshot, and putting a board back from it.

Run: python test_undo.py

Chris asked, before the first live push to a crew board: "What is the plan if
something bad happens? Can we undo any missteps?"

The answer is only worth having if the undo is mechanical, so this covers the two
halves of it -- the snapshot being written whole, and the diff that decides what
to put back.

The hazard specific to an undo, and the reason for most of this file: between the
snapshot and the restore, the TEAM may have edited their own board. An undo that
reverts their work to a state from two hours ago is a worse accident than the one
being undone. So the restore only ever touches cards that could be ours.
"""
from datetime import datetime, timezone

from card_restore import OUR_COLORS, OUR_TITLES, RESTORABLE, differs
from connecteam_cards import ours, read_snapshot, snapshot_names, write_snapshot
from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, STANDARD_TITLE,
                            TURNOVER_COLOR)

WHEN = datetime(2026, 10, 7, 14, 30, 5, tzinfo=timezone.utc)


class FakeStore:
    """read/write only, which is all the real object store offers."""

    def __init__(self, fail_on=""):
        self.objects = {}
        self.fail_on = fail_on

    def read(self, name):
        if name == self.fail_on:
            raise RuntimeError("boom")
        return self.objects.get(name), len(self.objects.get(name) or b"")

    def write(self, name, payload, if_generation_match=0):
        if name == self.fail_on:
            raise RuntimeError("boom")
        self.objects[name] = payload
        return 1


def _card(sid, **over):
    c = {"id": sid, "jobId": "J1", "title": STANDARD_TITLE,
         "color": STANDARD_COLOR, "startTime": 1000, "endTime": 1900,
         "assignedUserIds": [], "notes": [], "openSpots": 1}
    c.update(over)
    return c


# ------------------------------------------------------------- the snapshot

def test_the_snapshot_names_are_dated_and_pointed_at():
    dated, latest = snapshot_names("10540759", WHEN)
    assert dated == "connecteam/snapshots/10540759-20261007-143005.json", dated
    assert latest == "connecteam/snapshots/10540759-latest.json", latest
    assert dated != latest
    print("OK: a dated record and a 'latest' pointer, per board")


def test_the_snapshot_holds_the_cards_whole():
    """A summary cannot be restored from, which is the whole reason it exists."""
    store = FakeStore()
    cards = [_card("a", notes=["a crew note"]), _card("b", color=TURNOVER_COLOR)]
    name = write_snapshot(store, "10540759", cards,
                          {"window": "2026-10-07..2026-11-21", "city": "Austin"},
                          when=WHEN)
    assert name, "nothing was stored"
    got = read_snapshot(store, "10540759", name)
    assert got["count"] == 2 and got["city"] == "Austin"
    assert got["window"] == "2026-10-07..2026-11-21"
    back = {c["id"]: c for c in got["shifts"]}
    assert back["a"]["notes"] == ["a crew note"], back["a"]
    assert back["b"]["color"] == TURNOVER_COLOR
    for field in RESTORABLE:
        assert field in back["a"], f"{field} would not be restorable"
    print("OK: every restorable field survives the round trip, notes included")


def test_the_latest_pointer_is_moved_too():
    store = FakeStore()
    write_snapshot(store, "777", [_card("a")], {}, when=WHEN)
    _dated, latest = snapshot_names("777", WHEN)
    assert latest in store.objects, list(store.objects)
    assert read_snapshot(store, "777")["count"] == 1
    print("OK: 'latest' is written as well, so an undo needs no name")


def test_a_store_that_fails_does_not_stop_the_push():
    """A snapshot is insurance, not a prerequisite. Refusing to push because the
    bucket is unreachable would turn a storage problem into a missing schedule --
    the push says loudly that there is no undo and carries on."""
    store = FakeStore(fail_on=snapshot_names("888", WHEN)[0])
    assert write_snapshot(store, "888", [_card("a")], {}, when=WHEN) == ""
    assert write_snapshot(None, "888", [_card("a")], {}, when=WHEN) == ""
    print("OK: an unwritable snapshot returns empty rather than raising")


def test_a_missing_or_corrupt_snapshot_reads_as_nothing():
    store = FakeStore()
    assert read_snapshot(store, "nope") is None
    store.objects[snapshot_names("999", WHEN)[1]] = b"not json at all"
    assert read_snapshot(store, "999") is None
    assert read_snapshot(None, "999") is None
    print("OK: a missing or corrupt snapshot is None, not a crash")


# ------------------------------------------------------------------ the diff

def test_the_diff_reports_what_to_put_back_not_what_changed():
    """Direction matters. The pair is (what it is now, what it should be), so the
    caller can read the second element straight into the update."""
    was = _card("a", color=STANDARD_COLOR, startTime=1000)
    now = _card("a", color=CANCELLED_COLOR, startTime=2000)
    got = differs(was, now)
    assert got["color"] == (CANCELLED_COLOR, STANDARD_COLOR), got
    assert got["startTime"] == (2000, 1000), got
    print("OK: the diff gives (now, should-be), ready to apply")


def test_the_diff_ignores_what_a_push_cannot_change():
    """Notes, tasks and assignment are the team's. A push never writes them, so
    an undo must not either -- reverting those would be undoing their work."""
    was = _card("a", notes=[], tasks=[])
    now = _card("a", notes=["added by a supervisor"], tasks=[{"t": 1}],
                isRequireAdminApproval=True)
    assert differs(was, now) == {}, differs(was, now)
    print("OK: fields a push never writes are not part of the undo")


def test_an_unchanged_card_has_nothing_to_undo():
    c = _card("a")
    assert differs(c, dict(c)) == {}
    print("OK: an untouched card produces no action")


def test_a_moved_card_is_put_back_on_both_times():
    was = _card("a", startTime=1000, endTime=1900)
    now = _card("a", startTime=90000, endTime=90900)
    got = differs(was, now)
    assert set(got) == {"startTime", "endTime"}, got
    assert got["startTime"][1] == 1000 and got["endTime"][1] == 1900
    print("OK: a moved card is put back on start AND end")


# ------------------------------------------------- what the undo will not touch

def test_the_restore_shares_the_push_s_definition_of_ours():
    """If these two ever disagree, the undo either misses our own cards or
    reaches into the team's. Same constants, asserted here."""
    assert STANDARD_TITLE in OUR_TITLES and "Turnover" in OUR_TITLES, OUR_TITLES
    # The old name counts too, or the undo would not recognise a card it made
    # last week as its own.
    assert "Clean" in OUR_TITLES, OUR_TITLES
    assert set(OUR_COLORS) == {STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR}
    print("OK: the undo uses the same titles and colours as the push")


def test_a_real_austin_card_is_not_ours_to_restore():
    """Read off the live Austin board on 2026-10-07."""
    for title in ("sale no entran huespedes", "Vacia no entra gente", ""):
        card = {"title": title, "color": None, "jobId": "84ee8d80"}
        assert not ours(card, OUR_TITLES, OUR_COLORS), title
    print("OK: the crews' own cards are outside the undo's reach")


def test_our_own_card_is_within_reach():
    for colour in OUR_COLORS:
        assert ours(_card("a", color=colour), OUR_TITLES, OUR_COLORS), colour
    assert ours(_card("a", title="Turnover", color=TURNOVER_COLOR),
                OUR_TITLES, OUR_COLORS)
    print("OK: a card of ours in any of the three colours can be put back")


if __name__ == "__main__":
    test_the_snapshot_names_are_dated_and_pointed_at()
    test_the_snapshot_holds_the_cards_whole()
    test_the_latest_pointer_is_moved_too()
    test_a_store_that_fails_does_not_stop_the_push()
    test_a_missing_or_corrupt_snapshot_reads_as_nothing()
    test_the_diff_reports_what_to_put_back_not_what_changed()
    test_the_diff_ignores_what_a_push_cannot_change()
    test_an_unchanged_card_has_nothing_to_undo()
    test_a_moved_card_is_put_back_on_both_times()
    test_the_restore_shares_the_push_s_definition_of_ours()
    test_a_real_austin_card_is_not_ours_to_restore()
    test_our_own_card_is_within_reach()
    print("")
    print("ALL UNDO TESTS PASSED")
