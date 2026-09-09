"""Combined-unit listing names expand to units that actually exist.

Run: python test_combined_units.py

Guesty names a combined listing by its first unit plus only the digits that CHANGE:

    31 Con 203&4 V5   ->  31 Congress 203, 31 Congress 204

The "&4" means "the next one along". Zero-padding it instead produced
"31 Congress 004" -- a unit that does not exist, is absent from
property_to_city.csv (so the row carried no city), and still reached the team's
sheet as a cleaning job on a real date.
"""
from processing import normalize_property as prop


def test_a_short_fragment_continues_the_unit_before_it():
    assert prop("31 Con 203&4 V5") == ["31 Congress 203", "31 Congress 204"]
    assert prop("31 Con 201&2") == ["31 Congress 201", "31 Congress 202"]
    assert prop("427 Grav 201&2") == ["427 Gravier 201", "427 Gravier 202"]
    print("OK 203&4 -> 203, 204   (was 203, 004)")


def test_it_keeps_going_for_three_or_more():
    assert prop("31 Con 203&4&5") == ["31 Congress 203", "31 Congress 204",
                                      "31 Congress 205"]
    print("OK 203&4&5 -> 203, 204, 205")


def test_a_two_digit_fragment_replaces_two_digits():
    assert prop("31 Con 203&14") == ["31 Congress 203", "31 Congress 214"]
    print("OK 203&14 -> 203, 214")


def test_whole_unit_numbers_are_left_alone():
    """The case that already worked and must not regress."""
    assert prop("422 Gravier 201&202 V1") == ["422 Gravier 201", "422 Gravier 202"]
    assert prop("31 Con 1&2") == ["31 Congress 1", "31 Congress 2"]
    print("OK 201&202 and 1&2 unchanged")


def test_the_other_combining_rules_are_untouched():
    assert prop("1229 Dela A&B") == ["1229 Delano A", "1229 Delano B"]
    assert prop("1308&12 Baronne") == ["1308 Baronne", "1312 Baronne"]
    assert prop("704-715 & N 2nd") == ["704 N 2nd", "715 N 2nd"]
    print("OK letter units, street numbers and hyphen ranges unchanged")


def test_every_expanded_unit_is_a_property_the_city_map_knows():
    """The real symptom: an invented unit has no city, so the row lands on the
    sheet with a blank City and escapes the city filter's notice."""
    import csv

    with open("property_to_city.csv", encoding="utf-8") as fh:
        known = {r[0].strip() for r in csv.reader(fh) if r}

    for raw in ("31 Con 203&4 V5", "31 Con 201&2", "427 Grav 201&2",
                "422 Gravier 201&202 V1"):
        for unit in prop(raw):
            assert unit in known, f"{raw!r} produced {unit!r}, which no city map knows"
    print("OK every expanded unit resolves to a real property")


if __name__ == "__main__":
    test_a_short_fragment_continues_the_unit_before_it()
    test_it_keeps_going_for_three_or_more()
    test_a_two_digit_fragment_replaces_two_digits()
    test_whole_unit_numbers_are_left_alone()
    test_the_other_combining_rules_are_untouched()
    test_every_expanded_unit_is_a_property_the_city_map_knows()
    print("\nALL COMBINED-UNIT TESTS PASSED")
