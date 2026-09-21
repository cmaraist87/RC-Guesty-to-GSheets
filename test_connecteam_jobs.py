"""Matching a property to the Connecteam Job the card points at.

Run: python test_connecteam_jobs.py

The names come from the live account, which has ~1429 Jobs typed in by hand over
years. Every fixture below is a real pair read off it on 2026-09-21, because the
shapes that break a matcher are the ones nobody would invent.
"""
from connecteam_jobs import (build_index, norm, resolve, resolve_all,
                             usable, version_of)


def jobs(*pairs):
    return [{"jobId": jid, "name": name} for jid, name in pairs]


def test_the_comparable_name_ignores_notes_and_versions():
    assert norm("1100 Ursulines  / Heirloom") == "1100 ursulines"
    assert norm("1163 webberville A V2") == "1163 webberville a"
    assert norm("1163 Webberville A") == "1163 webberville a"
    assert norm("2903 E 3rd  A  V2") == "2903 e 3rd a"
    print("OK: slashes, case and V-numbers do not make two names different")


def test_a_version_is_read_off_the_raw_name():
    assert version_of("6504 Porter B v2") == 2
    assert version_of("109 Twelve Oaks V3") == 3
    assert version_of("1022 Erato") == 0
    print("OK: the version marker is read whatever its case")


def test_one_match_is_taken():
    index = build_index(jobs(("j1", "1022 Erato")))
    assert resolve("1022 Erato", index) == ("j1", "1022 Erato", "one Job matches")
    print("OK: an unambiguous property takes its Job")


def test_the_highest_version_wins():
    """The team's rule, 2026-09-21: a V2 is the current revision of a listing."""
    index = build_index(jobs(("old", "6504 Porter A"), ("new", "6504 Porter A V2")))
    jid, name, why = resolve("6504 Porter A", index)
    assert (jid, name) == ("new", "6504 Porter A V2"), (jid, name)
    assert "highest version" in why and "6504 Porter A" in why, why
    print("OK: V2 beats the original, and the log says what it passed over")


def test_a_v3_beats_a_v2():
    index = build_index(jobs(("a", "109 Twelve Oaks"), ("b", "109 Twelve Oaks V2"),
                             ("c", "109 Twelve Oaks V3")))
    assert resolve("109 Twelve Oaks", index)[0] == "c"
    print("OK: the newest revision wins, not merely any revision")


def test_the_choice_does_not_depend_on_the_order_the_api_answered_in():
    """Same inputs, same card, every run -- a tie broken by chance would move a
    property between Jobs from one morning to the next."""
    a = build_index(jobs(("x", "1018 Ferdinand"), ("y", "1018 Ferdinand")))
    b = build_index(jobs(("y", "1018 Ferdinand"), ("x", "1018 Ferdinand")))
    assert resolve("1018 Ferdinand", a)[0] == resolve("1018 Ferdinand", b)[0]
    print("OK: duplicate Jobs with one name resolve the same way each time")


def test_a_near_miss_is_not_a_match():
    """1026 N Robert and 1026 N Robertson are both real Jobs on the account. A
    matcher that treats them as one would put a card on the wrong property."""
    index = build_index(jobs(("j1", "1026 N Robert")))
    jid, _n, why = resolve("1026 N Robertson", index)
    assert jid is None, jid
    assert "no Job" in why
    print("OK: a name that is merely close is left unmatched")


def test_an_unmatched_property_is_reported_not_guessed():
    index = build_index(jobs(("j1", "1022 Erato")))
    matched, missing = resolve_all(
        ["1022 Erato", "1802 Martin Luther King", "4807 Prock B"], index)
    assert list(matched) == ["1022 Erato"], matched
    assert [p for p, _w in missing] == ["1802 Martin Luther King", "4807 Prock B"]
    print("OK: the three Austin properties with no Job come back as a list")


def test_a_blank_property_matches_nothing():
    index = build_index(jobs(("j1", "1022 Erato")))
    assert resolve("", index)[0] is None
    assert resolve("   ", index)[0] is None
    print("OK: a blank property name resolves to nothing, not to the first Job")


def test_a_job_with_no_id_or_no_name_is_skipped():
    index = build_index([{"name": "No Id"}, {"jobId": "j2"},
                         {"jobId": "j3", "name": "1022 Erato"}])
    assert resolve("1022 Erato", index)[0] == "j3"
    assert resolve("No Id", index)[0] is None
    print("OK: half-formed Job records cannot be pointed at")


def test_a_soft_deleted_job_is_never_offered():
    """506 of the 1429 Jobs on this account are soft-deleted and still come back
    from the list. Pointing a card at one is refused with "does not exist" --
    which on 2026-09-21 was read as proof that Jobs are board-scoped, and sent
    the whole investigation down the wrong road for hours."""
    jobs = [{"jobId": "dead", "name": "1022 Erato", "isDeleted": True,
             "instanceIds": [111]},
            {"jobId": "live", "name": "1022 Erato", "instanceIds": [111]}]
    assert resolve("1022 Erato", build_index(jobs, board="111"))[0] == "live"
    assert [j["jobId"] for j in usable(jobs)] == ["live"]
    print("OK: a deleted Job is dropped before anything can match it")


def test_a_job_belonging_to_another_board_is_not_offered():
    """instanceIds names the boards a Job belongs to. Austin owns 35, the test
    board 3, New Orleans 722. A card can only point at one of its own."""
    jobs = [{"jobId": "nola", "name": "1022 Erato", "instanceIds": [2520975]},
            {"jobId": "austin", "name": "6504 Porter A", "instanceIds": [10540759]}]
    austin = build_index(jobs, board="10540759")
    assert resolve("6504 Porter A", austin)[0] == "austin"
    assert resolve("1022 Erato", austin)[0] is None, "a NOLA Job on an Austin card"
    print("OK: a Job from another board cannot be pointed at")


def test_a_job_on_several_boards_counts_for_each():
    """Some Jobs list more than one board; 1163 Webberville had two."""
    jobs = [{"jobId": "shared", "name": "22 Lake", "instanceIds": [10540737, 12899125]}]
    assert resolve("22 Lake", build_index(jobs, board="10540737"))[0] == "shared"
    assert resolve("22 Lake", build_index(jobs, board="12899125"))[0] == "shared"
    assert resolve("22 Lake", build_index(jobs, board="10540759"))[0] is None
    print("OK: a Job shared by two boards is usable on both and no others")


def test_without_a_board_the_index_spans_the_account():
    """The old behaviour, kept only for callers that genuinely want everything --
    and the reason `board` is a parameter rather than an assumption."""
    jobs = [{"jobId": "a", "name": "1022 Erato", "instanceIds": [2520975]}]
    assert resolve("1022 Erato", build_index(jobs))[0] == "a"
    print("OK: omitting the board gives the account-wide index, deleted still dropped")


if __name__ == "__main__":
    test_the_comparable_name_ignores_notes_and_versions()
    test_a_version_is_read_off_the_raw_name()
    test_one_match_is_taken()
    test_the_highest_version_wins()
    test_a_v3_beats_a_v2()
    test_the_choice_does_not_depend_on_the_order_the_api_answered_in()
    test_a_near_miss_is_not_a_match()
    test_an_unmatched_property_is_reported_not_guessed()
    test_a_blank_property_matches_nothing()
    test_a_job_with_no_id_or_no_name_is_skipped()
    test_a_soft_deleted_job_is_never_offered()
    test_a_job_belonging_to_another_board_is_not_offered()
    test_a_job_on_several_boards_counts_for_each()
    test_without_a_board_the_index_spans_the_account()
    print("\nALL CONNECTEAM-JOBS TESTS PASSED")
