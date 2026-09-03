"""The client must not create a job unless asked twice, and never create one twice.

Run: python test_connecteam_client.py

A sheet cell can be corrected next run. A job card has already reached a crew's
phone, so the tests here are about refusing rather than about doing.
"""
import json

from connecteam_client import ConnecteamClient, ConnecteamError, _shift_key


class FakeAPI:
    """Records requests. Returns whatever `existing` says is already on the board."""

    def __init__(self, existing=None, status=200):
        self.existing = existing or []
        self.status = status
        self.calls = []
        self.headers = {}

    def request(self, method, url, timeout=None, data=None):
        body = json.loads(data) if data else None
        self.calls.append((method, url, body))
        outer = self

        class R:
            status_code = outer.status
            headers = {}

            @property
            def text(self_inner):
                return "boom"

            def json(self_inner):
                if method == "GET":
                    return {"data": {"shifts": outer.existing}}
                return {"data": {"shifts": [dict(s, id=f"sh_{i}")
                                            for i, s in enumerate(body or [])]}}
        return R()

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def shift(title="1201 N Roman", start=1_788_534_000, end=1_788_548_400):
    return {"startTime": start, "endTime": end, "timezone": "America/Chicago",
            "title": title, "isOpenShift": True, "assignedUserIds": [], "openSpots": 1}


def test_a_key_is_required():
    for bad in ("", "   ", None):
        try:
            ConnecteamClient(bad)
        except ConnecteamError:
            continue
        raise AssertionError(f"accepted {bad!r} as an API key")
    print("OK: refuses to build without an API key")


def test_nothing_is_sent_unless_live_is_asked_for():
    """The default must be the safe one. Forgetting an argument must not write."""
    api = FakeAPI()
    made = ConnecteamClient("k", session=api).create_shifts("2520975", [shift()])
    assert made == [], made
    assert api.writes == [], api.writes        # a GET to check the board is fine
    print("OK: the default is a preview -- no job reaches Connecteam")


def test_live_actually_creates_and_returns_ids():
    api = FakeAPI()
    made = ConnecteamClient("k", session=api).create_shifts(
        "2520975", [shift(), shift("3223 Canal", 1_788_620_400, 1_788_634_800)], live=True)
    assert len(made) == 2, made
    assert all("id" in m for m in made), made
    posts = api.writes
    assert len(posts) == 1 and posts[0][0] == "POST", posts
    assert "/scheduler/v1/schedulers/2520975/shifts" in posts[0][1], posts
    assert isinstance(posts[0][2], list), "the body must be an ARRAY of shifts"
    print("OK: live posts an array to the right board and returns the ids")


def test_a_job_already_on_the_board_is_not_created_twice():
    """Running twice must not double a cleaner's day. The board is the memory --
    the sheet has no column to remember shift ids in."""
    already = shift()
    api = FakeAPI(existing=[already])
    made = ConnecteamClient("k", session=api).create_shifts(
        "2520975", [already, shift("3223 Canal", 1_788_620_400, 1_788_634_800)], live=True)
    assert len(made) == 1, made
    posted = api.writes[0][2]
    assert [p["title"] for p in posted] == ["3223 Canal"], posted
    print("OK: a job already on the board is skipped, the new one still goes")


def test_it_stops_rather_than_risk_duplicates_when_the_board_cannot_be_read():
    api = FakeAPI(status=500)
    try:
        ConnecteamClient("k", session=api).create_shifts("2520975", [shift()], live=True)
    except ConnecteamError as e:
        assert "duplicates cannot be ruled out" in str(e), e
        assert api.writes == [], "nothing may be written after a failed read"
        print("OK: an unreadable board stops the write instead of risking duplicates")
        return
    raise AssertionError("wrote without being able to check for duplicates")


def test_an_assigned_shift_never_reaches_the_network():
    api = FakeAPI()
    bad = dict(shift(), assignedUserIds=["u_123"])
    try:
        ConnecteamClient("k", session=api).create_shifts("2520975", [bad], live=True)
    except ValueError as e:
        assert "Unassigned" in str(e), e
        assert api.calls == [], "not even a read should happen"
        print("OK: a shift naming a person is refused before any request is made")
        return
    raise AssertionError("an assigned shift was sent to Connecteam")


def test_more_than_500_is_split():
    many = [shift(f"Prop {i}", 1_788_534_000 + i * 60, 1_788_548_400 + i * 60)
            for i in range(1201)]
    api = FakeAPI()
    made = ConnecteamClient("k", session=api).create_shifts("2520975", many, live=True)
    posts = api.writes
    assert [len(p[2]) for p in posts] == [500, 500, 201], [len(p[2]) for p in posts]
    assert len(made) == 1201, len(made)
    print("OK: a big day is split into batches of 500, the documented ceiling")


def test_shift_identity_is_title_plus_start():
    a = shift("1201 N Roman", 100, 200)
    assert _shift_key(a) == _shift_key(shift("1201 N Roman", 100, 999)), \
        "a re-derived end time must not make it a different job"
    assert _shift_key(a) != _shift_key(shift("3223 Canal", 100, 200))
    assert _shift_key(a) != _shift_key(shift("1201 N Roman", 101, 200))
    print("OK: two cleans are the same job when property and start match")


if __name__ == "__main__":
    test_a_key_is_required()
    test_nothing_is_sent_unless_live_is_asked_for()
    test_live_actually_creates_and_returns_ids()
    test_a_job_already_on_the_board_is_not_created_twice()
    test_it_stops_rather_than_risk_duplicates_when_the_board_cannot_be_read()
    test_an_assigned_shift_never_reaches_the_network()
    test_more_than_500_is_split()
    test_shift_identity_is_title_plus_start()
    print("\nALL CONNECTEAM-CLIENT TESTS PASSED")
