"""Two Guesty listings that are one physical place.

Run: python test_listing_aliases.py

1401 Carondelet is let through "1401 Caron U V1" and "1401 Caron A U V2", which
normalize to two different property names. The sheet showed them as two
properties that never overlap and hand off to each other -- Blynn Austin out at
11:00 on 27 September, Lareina Kostenchuk in at 16:00 the same day. One
turnover, shown as two unrelated jobs, and again on 16 October.

The team confirmed on 2026-09-29 that it is one unit called "1401 Carondelet".

The danger in fixing it is over-reach: 1409 Caron A, 1413 Caron B, 1417 Caron A
and 1421 Caron B are REAL separate units in the same building. A rule that
strips a trailing letter would merge genuinely different places, and a cleaner
would be sent to one flat for a job in another. Hence an alias on the listing
ID, and hence most of this file.
"""
from processing import LISTING_ALIASES, normalize_property, property_names

V1 = "6499c8299c3108003a52143a"   # 1401 Caron U V1
V2 = "64a306d203677d002cd4f956"   # 1401 Caron A U V2


def row(listing, listing_id=""):
    return {"LISTING": listing, "LISTING ID": listing_id}


def test_both_listings_become_one_property():
    assert property_names(row("1401 Caron U V1", V1)) == ["1401 Carondelet"]
    assert property_names(row("1401 Caron A U V2", V2)) == ["1401 Carondelet"]
    print("OK: both 1401 listings resolve to one property")


def test_the_alias_survives_the_listing_being_renamed():
    """The whole reason it is keyed on the id. Guesty nicknames are renamed
    constantly -- this account has renamed listings mid-booking."""
    assert property_names(row("1401 Caron A U V3 renovated", V2)) == ["1401 Carondelet"]
    assert property_names(row("anything at all", V1)) == ["1401 Carondelet"]
    print("OK: a renamed listing still resolves, because the id is the key")


def test_the_other_carondelet_units_are_left_alone():
    """The over-reach guard. These share the building and a naming pattern with
    1401, and they are different flats."""
    for nick, expect in (("1409 Caron A U V1", "1409 Carondelet A"),
                         ("1413 Caron B U V1", "1413 Carondelet B"),
                         ("1417 Caron A U V1", "1417 Carondelet A"),
                         ("1421 Caron B U V1", "1421 Carondelet B")):
        got = property_names(row(nick, "some-other-id"))
        assert got == [expect], (nick, got)
    print("OK: 1409/1413/1417/1421 A and B stay separate properties")


def test_a_listing_with_no_id_still_works():
    """Older frames, or a reservation Guesty returned without the id."""
    assert property_names(row("3223 Canal V1")) == ["3223 Canal"]
    assert property_names(row("3223 Canal V1", "")) == ["3223 Canal"]
    print("OK: a row with no listing id falls back to the nickname, as before")


def test_an_unaliased_listing_is_untouched():
    for nick in ("1123 Marais V2", "6504 Porter A V2", "31 Con 301&2 V3"):
        assert property_names(row(nick, "unknown-id")) == normalize_property(nick)
        print(f"   {nick!r} -> {property_names(row(nick, 'unknown-id'))}")
    print("OK: every other listing behaves exactly as it did before")


def test_a_combined_listing_still_splits_into_units():
    """An alias returns one name; everything else may still return several."""
    got = property_names(row("31 Con 203&4 V1", "another-id"))
    assert len(got) == 2, got
    print(f"OK: a combined listing still splits -> {got}")


def test_the_alias_table_names_only_what_the_team_confirmed():
    """A guard on the table itself. Adding an entry merges two places on the
    schedule, so it should never happen by accident."""
    assert set(LISTING_ALIASES.values()) == {"1401 Carondelet"}, LISTING_ALIASES
    assert set(LISTING_ALIASES) == {V1, V2}, LISTING_ALIASES
    print("OK: the table holds exactly the one merge the team asked for")


if __name__ == "__main__":
    test_both_listings_become_one_property()
    test_the_alias_survives_the_listing_being_renamed()
    test_the_other_carondelet_units_are_left_alone()
    test_a_listing_with_no_id_still_works()
    test_an_unaliased_listing_is_untouched()
    test_a_combined_listing_still_splits_into_units()
    test_the_alias_table_names_only_what_the_team_confirmed()
    print("\nALL LISTING-ALIAS TESTS PASSED")
