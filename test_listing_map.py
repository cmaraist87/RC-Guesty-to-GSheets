"""
Offline tests for combined-listing detection.

Every fixture below is a listing-name shape that appears in the live Guesty data,
not an invented edge case: `422 Gravier 201&202` and `2903 E 3rd A&B` genuinely
split; `1201 N Roman V2` / `V3` genuinely collapse onto one property; and the
`520 E Harris&CH` / `520 Harris CH` pair is the spelling drift that string equality
would miss.

Run: python test_listing_map.py
"""
from listing_map import (CLEAN, FANS_OUT, FANS_OUT_AND_SHARED, SHARED, UNKNOWN,
                         ListingIndex)

# (listing id, Guesty nickname)
LISTINGS = [
    ("L-CLEAN", "1022 Mandeville"),          # one listing, one property
    ("L-SPLIT", "422 Gravier 201&202"),      # one listing, two properties
    ("L-ROMAN2", "1201 N Roman V2"),         # version markers are stripped, so
    ("L-ROMAN3", "1201 N Roman V3"),         # ...these two collide on one property
    ("L-HARRIS-A", "520 E Harris&CH"),       # -> "520 E Harris CH"
    ("L-HARRIS-B", "520 Harris CH"),         # -> "520 Harris CH"  (same place)
    ("L-3RD-BOTH", "2903 E 3rd A&B"),        # splits AND overlaps the next one
    ("L-3RD-A", "2903 E 3rd A"),
]


def test_clean_one_to_one():
    idx = ListingIndex(LISTINGS)
    r = idx.resolve("L-CLEAN")

    assert r.kind == CLEAN, r
    assert r.is_clean is True
    assert r.properties == ("1022 Mandeville",), r
    assert r.sole_property == "1022 Mandeville"
    assert r.siblings == (), r
    print("OK: a listing owning exactly one property reports clean")


def test_one_listing_feeding_several_properties():
    idx = ListingIndex(LISTINGS)
    r = idx.resolve("L-SPLIT")

    assert r.kind == FANS_OUT, r
    assert r.is_clean is False
    assert r.properties == ("422 Gravier 201", "422 Gravier 202"), r
    assert r.sole_property == "", "a fanned-out listing must not offer a single property"
    assert "splits into 2" in r.reason, r.reason
    print("OK: a combined listing feeding several properties is flagged")


def test_several_listings_feeding_one_property():
    idx = ListingIndex(LISTINGS)
    r2, r3 = idx.resolve("L-ROMAN2"), idx.resolve("L-ROMAN3")

    # Both normalise to the same property, so neither owns it.
    assert r2.properties == ("1201 N Roman",) == r3.properties, (r2, r3)
    assert r2.kind == SHARED and r3.kind == SHARED, (r2.kind, r3.kind)
    assert r2.siblings == ("L-ROMAN3",), r2
    assert r3.siblings == ("L-ROMAN2",), r3
    assert idx.listings_for_property("1201 N Roman") == ("L-ROMAN2", "L-ROMAN3")
    print("OK: several listings feeding one property are flagged on both sides")


def test_sharing_is_detected_across_spelling_drift():
    """`520 E Harris&CH` and `520 Harris CH` produce different property STRINGS for
    the same unit. Comparing strings would call both clean and let two callers
    rewrite each other's rows."""
    idx = ListingIndex(LISTINGS)
    a, b = idx.resolve("L-HARRIS-A"), idx.resolve("L-HARRIS-B")

    assert a.properties != b.properties, "fixture no longer exercises spelling drift"
    assert a.kind == SHARED and b.kind == SHARED, (a, b)
    assert a.siblings == ("L-HARRIS-B",) and b.siblings == ("L-HARRIS-A",), (a, b)
    # Either spelling finds both listings.
    assert set(idx.listings_for_property("520 E Harris CH")) == {"L-HARRIS-A", "L-HARRIS-B"}
    assert set(idx.listings_for_property("520 Harris CH")) == {"L-HARRIS-A", "L-HARRIS-B"}
    print("OK: canonical matching catches sharing that string equality would miss")


def test_a_listing_can_be_both_split_and_shared():
    idx = ListingIndex(LISTINGS)
    r = idx.resolve("L-3RD-BOTH")

    assert r.properties == ("2903 E 3rd A", "2903 E 3rd B"), r
    assert r.kind == FANS_OUT_AND_SHARED, r
    assert r.siblings == ("L-3RD-A",), r
    assert r.is_clean is False
    print("OK: split-and-shared is reported as its own condition, not as one or the other")


def test_unknown_listing_is_reported_not_raised():
    """A caller reacting to an event for a listing we have never seen must get a
    refusal it can act on, not an exception or a confident wrong answer."""
    idx = ListingIndex(LISTINGS)
    r = idx.resolve("L-NOT-IN-CATALOGUE")

    assert r.kind == UNKNOWN and r.is_clean is False, r
    assert r.properties == (), r
    assert r.sole_property == ""
    assert "not in the index" in r.reason, r.reason
    print("OK: an unknown listing id reports unknown rather than raising")


def test_combined_lists_everything_needing_review():
    idx = ListingIndex(LISTINGS)
    flagged = {r.listing_id for r in idx.combined()}

    assert "L-CLEAN" not in flagged, flagged
    assert flagged == {"L-SPLIT", "L-ROMAN2", "L-ROMAN3", "L-HARRIS-A",
                       "L-HARRIS-B", "L-3RD-BOTH", "L-3RD-A"}, flagged
    assert len(idx) == len(LISTINGS)
    print(f"OK: combined() lists all {len(flagged)} listings needing review")


def test_incomplete_catalogue_entries_are_skipped():
    idx = ListingIndex([
        {"_id": "L1", "nickname": "1022 Mandeville"},
        {"_id": "L2", "nickname": ""},          # no name -> unmappable
        {"nickname": "6 Lake"},                 # no id -> unaddressable
    ])
    assert len(idx) == 1
    assert idx.resolve("L1").is_clean
    assert idx.resolve("L2").kind == UNKNOWN
    print("OK: catalogue entries missing an id or a name are skipped, not guessed at")


if __name__ == "__main__":
    test_clean_one_to_one()
    test_one_listing_feeding_several_properties()
    test_several_listings_feeding_one_property()
    test_sharing_is_detected_across_spelling_drift()
    test_a_listing_can_be_both_split_and_shared()
    test_unknown_listing_is_reported_not_raised()
    test_combined_lists_everything_needing_review()
    test_incomplete_catalogue_entries_are_skipped()
    print("\nALL LISTING-MAP TESTS PASSED")
