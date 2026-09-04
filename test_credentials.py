"""GOOGLE_SA_JSON must be accepted in every form it is actually supplied in.

Run: python test_credentials.py

This file exists because the raw-JSON branch was deleted by accident and shipped.
CI supplies the key as raw JSON in a secret, so the sync could not authenticate to
anything -- and the daily gate then stood the run down quietly behind a green tick.
One booking-shaped mistake; a whole day with no sync and no signal.
"""
import json
import os
import tempfile

from sheets_client import service_account_info

KEY = {"type": "service_account",
       "client_email": "guesty-sheet-sync@guesty-sheet-sync.iam.gserviceaccount.com",
       "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"}


def test_raw_json_is_accepted():
    """How GitHub Actions supplies it: the whole key pasted into a secret."""
    got = service_account_info(json.dumps(KEY))
    assert got["client_email"] == KEY["client_email"], got
    # Secrets pick up stray whitespace and trailing newlines constantly.
    for pad in ("  {0}", "{0}\n", "\n\n{0}\n  "):
        got = service_account_info(pad.format(json.dumps(KEY)))
        assert got["client_email"] == KEY["client_email"], pad
    # And pretty-printed, which is how it looks when downloaded from Google.
    got = service_account_info(json.dumps(KEY, indent=2))
    assert got["private_key"] == KEY["private_key"], got
    print("OK: raw JSON is accepted, padded and pretty-printed included")


def test_a_file_path_is_accepted():
    """How a local run supplies it: the downloaded key file."""
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(KEY, fh)
    fh.close()
    try:
        assert service_account_info(fh.name)["client_email"] == KEY["client_email"]
    finally:
        os.unlink(fh.name)
    print("OK: a path to a key file is accepted")


def test_nothing_and_nonsense_are_refused_with_distinct_messages():
    for empty in ("", "   ", None):
        try:
            service_account_info(empty)
        except RuntimeError as e:
            assert "Missing GOOGLE_SA_JSON" in str(e), e
        else:
            raise AssertionError(f"accepted {empty!r}")
    try:
        service_account_info("/no/such/key.json")
    except RuntimeError as e:
        assert "neither valid JSON nor an existing file path" in str(e), e
    else:
        raise AssertionError("accepted a path that does not exist")
    print("OK: empty and nonsense are refused, and say which is which")


if __name__ == "__main__":
    test_raw_json_is_accepted()
    test_a_file_path_is_accepted()
    test_nothing_and_nonsense_are_refused_with_distinct_messages()
    print("\nALL CREDENTIAL TESTS PASSED")
