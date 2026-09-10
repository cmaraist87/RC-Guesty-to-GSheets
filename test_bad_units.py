"""Naming a row from its sibling: "427 Gravier 002" beside "301" is "302".

Run: python test_bad_units.py

A combined listing books every unit under one confirmation code, so the sibling row
is the same building at a real unit number -- and that number supplies the digits
the zero-padded fragment is missing.
"""
from bad_units import BAD_UNIT, repair


def test_the_case_the_client_raised():
    """HMDAJQTTNM, Emily Stadler: 427 Gravier 002 beside 427 Gravier 301."""
    fixed, why = repair("427 Gravier 002", ["427 Gravier 301"])
    assert fixed == "427 Gravier 302", (fixed, why)
    print("OK 427 Gravier 002 + sibling 301 -> 427 Gravier 302")


def test_a_three_digit_building():
    assert repair("31 Congress 004", ["31 Congress 203"])[0] == "31 Congress 204"
    assert repair("31 Congress 002", ["31 Congress 201"])[0] == "31 Congress 202"
    assert repair("422 Gravier 003", ["422 Gravier 101"])[0] == "422 Gravier 103"
    print("OK 004+203 -> 204,  002+201 -> 202,  003+101 -> 103")


def test_several_siblings_that_agree():
    """A booking across three units still gives one answer."""
    fixed, _ = repair("31 Congress 004", ["31 Congress 203", "31 Congress 203"])
    assert fixed == "31 Congress 204", fixed
    print("OK repeated siblings agree -> one answer")


def test_siblings_that_disagree_are_refused():
    """Julio 806/855, Janelle Harris: siblings at 201 AND 304 give 202 or 304.
    A coin toss must not be written to the sheet."""
    fixed, why = repair("31 Congress 002", ["31 Congress 201", "31 Congress 304"])
    assert fixed is None, fixed
    assert "disagree" in why, why
    print("OK siblings 201 + 304 -> refused, not guessed")


def test_no_sibling_is_refused():
    fixed, why = repair("31 Congress 002", [])
    assert fixed is None and "no sibling" in why, (fixed, why)
    fixed, why = repair("31 Congress 002", ["1204 Louisa"])
    assert fixed is None, "a different building is not a sibling"
    print("OK no sibling, or a different address -> refused")


def test_a_sibling_that_is_itself_broken_is_not_a_reference():
    fixed, why = repair("427 Gravier 002", ["427 Gravier 003"])
    assert fixed is None, f"took its number from another broken row: {fixed}"
    print("OK a zero-padded sibling is not used as the reference")


def test_real_unit_numbers_are_never_touched():
    for good in ("427 Gravier 301", "31 Congress 204", "1204 Louisa",
                 "1130 Baronne 3", "56 Turner Mill 1", "2 Mayflower CH"):
        assert not BAD_UNIT.match(good), good
        assert repair(good, ["427 Gravier 301"])[0] is None, good
    print("OK real unit numbers and plain addresses are left alone")


if __name__ == "__main__":
    test_the_case_the_client_raised()
    test_a_three_digit_building()
    test_several_siblings_that_agree()
    test_siblings_that_disagree_are_refused()
    test_no_sibling_is_refused()
    test_a_sibling_that_is_itself_broken_is_not_a_reference()
    test_real_unit_numbers_are_never_touched()
    print("\nALL BAD-UNIT TESTS PASSED")
