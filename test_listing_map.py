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


def test_two_ids_sharing_a_nickname_are_shared_not_clean():
    """The real failure, from the live catalogue: 'Billing 2348 Constance V3' exists
    under two distinct listing ids. Keyed by id they are correctly SHARED. Keyed by
    name they collapse into one entry and report CLEAN -- a false clean, which is
    the dangerous direction: a caller would query one listing, miss the other's
    reservations, and could clear a turnover that is still justified."""
    same_name = "2348 Constance V3"
    by_id = ListingIndex([("5ed5c327d14a6f00293de0e4", same_name),
                          ("5fdb9330bd48050031e8f554", same_name)])

    a = by_id.resolve("5ed5c327d14a6f00293de0e4")
    b = by_id.resolve("5fdb9330bd48050031e8f554")
    assert a.kind == SHARED and b.kind == SHARED, (a.kind, b.kind)
    assert a.siblings == ("5fdb9330bd48050031e8f554",), a
    assert b.siblings == ("5ed5c327d14a6f00293de0e4",), b
    assert a.is_clean is False and b.is_clean is False

    # The same two listings keyed by name -- one entry, and it looks safe.
    by_name = ListingIndex([(same_name, same_name)], allow_name_keys=True)
    assert by_name.resolve(same_name).kind == CLEAN, "fixture no longer reproduces the bug"
    assert len(by_name) == 1 and len(by_id) == 2
    print("OK: two ids under one nickname are shared by id, falsely clean by name")


def test_name_keyed_index_is_refused():
    """The mistake that produced the false clean was silent. It is now loud."""
    try:
        ListingIndex([("1022 Mandeville", "1022 Mandeville")])
        raise AssertionError("a name-keyed index was accepted")
    except ValueError as e:
        assert "name-keyed" in str(e), e
    # Offline analysis with no ids available must opt in explicitly.
    idx = ListingIndex([("1022 Mandeville", "1022 Mandeville")], allow_name_keys=True)
    assert idx.resolve("1022 Mandeville").is_clean
    print("OK: an accidentally name-keyed index raises instead of answering")


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
    test_two_ids_sharing_a_nickname_are_shared_not_clean()
    test_name_keyed_index_is_refused()
    test_combined_lists_everything_needing_review()
    test_incomplete_catalogue_entries_are_skipped()
    print("\nALL LISTING-MAP TESTS PASSED")
