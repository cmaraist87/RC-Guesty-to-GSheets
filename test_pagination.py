"""The fetch must not lose bookings to unstable paging.

Run: python test_pagination.py

A booking that never arrives is indistinguishable from a cancelled one, so a hole
in the fetch becomes a strikethrough on a live reservation in the sheet. That makes
paging correctness a data-integrity concern, not a performance one.
"""
import json

from guesty_client import fetch_reservations


class DriftingAPI:
    """A server that re-orders documents tied on the sort key between requests.

    This is what Guesty is entitled to do when asked to sort by `checkIn`: the
    order among equal keys is unspecified. Under skip/limit paging that means a
    document can be served twice while another is served never.
    """

    def __init__(self, n=250, page=100, unstable_key="checkIn"):
        self.docs = [{"_id": f"r{i:04d}", "checkIn": "2026-08-10",
                      "confirmationCode": f"HM{i:04d}"} for i in range(n)]
        self.page, self.unstable_key, self.calls = page, unstable_key, 0

    def order_for(self, sort):
        if sort == self.unstable_key:
            # Rotate by one each request: a stable-looking list that isn't.
            self.calls += 1
            k = self.calls
            return self.docs[k:] + self.docs[:k]
        return sorted(self.docs, key=lambda d: d["_id"])

    def get(self, url, headers=None, params=None, timeout=None):
        ordered = self.order_for(params.get("sort"))
        skip, limit = int(params["skip"]), int(params["limit"])
        body = {"results": ordered[skip:skip + limit], "count": len(self.docs)}

        class R:
            status_code = 200
            def json(self_inner):
                return body
            @property
            def text(self_inner):
                return json.dumps(body)
        return R()


def test_date_sort_would_lose_bookings():
    """Proves the failure mode is real before proving the fix."""
    api = DriftingAPI()
    got = fetch_reservations("tok", sort="checkIn", page_size=api.page, session=api)
    ids = [r["_id"] for r in got]
    assert len(ids) == len(set(ids)), "dedupe should still protect us"
    missing = {d["_id"] for d in api.docs} - set(ids)
    assert missing, "the drifting server should have produced holes"
    print(f"OK reproduced: sorting by a tied key lost {len(missing)} booking(s) "
          f"-- each would be struck through as a cancellation")


def test_id_sort_returns_every_booking():
    api = DriftingAPI()
    got = fetch_reservations("tok", page_size=api.page, session=api)   # default sort
    ids = [r["_id"] for r in got]
    assert len(ids) == len(set(ids)), "no duplicates"
    assert set(ids) == {d["_id"] for d in api.docs}, "every booking must arrive"
    assert len(ids) == 250, len(ids)
    print("OK default sort is unique: all 250 bookings arrive, none twice")


def test_duplicates_are_removed_not_written_twice():
    class Doubling(DriftingAPI):
        def order_for(self, sort):
            base = sorted(self.docs, key=lambda d: d["_id"])
            return base[:50] + base[:50] + base[50:]   # first 50 served twice

    api = Doubling(n=150, page=50)
    got = fetch_reservations("tok", page_size=50, session=api)
    ids = [r["_id"] for r in got]
    assert len(ids) == len(set(ids)), "duplicates must not reach the sheet"
    print("OK duplicates from the server are collapsed, not written twice")


def test_shortfall_is_reported(capsys=None):
    class Truncating(DriftingAPI):
        def order_for(self, sort):
            return sorted(self.docs, key=lambda d: d["_id"])[:180]   # 70 never served

    import io
    from contextlib import redirect_stdout
    api = Truncating(n=250, page=100)
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = fetch_reservations("tok", page_size=100, session=api)
    out = buf.getvalue()
    assert len(got) == 180, len(got)
    assert "missing" in out and "250" in out, out
    assert "look exactly like cancellations" in out, out
    print("OK a short fetch is announced loudly, naming the risk to cancellations")


if __name__ == "__main__":
    test_date_sort_would_lose_bookings()
    test_id_sort_returns_every_booking()
    test_duplicates_are_removed_not_written_twice()
    test_shortfall_is_reported()
    print("\nALL PAGINATION TESTS PASSED")
