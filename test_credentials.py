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




# --- transient Google failures ---------------------------------------------------

class _ApiError(Exception):
    def __init__(self, code):
        self.response = type("R", (), {"status_code": code})()


def test_transient_google_failures_are_retried():
    """2026-09-07: a bare 503 from Sheets killed the whole morning's run, after a
    Guesty token had already been spent. Every one of these means 'try again
    shortly', and the run is worth several attempts."""
    from sheets_client import TRANSIENT_STATUS, with_retry

    for code in TRANSIENT_STATUS:
        seen = {"n": 0}

        def flaky(code=code):
            seen["n"] += 1
            if seen["n"] < 3:
                raise _ApiError(code)
            return "written"

        assert with_retry(flaky, "a write", base=1.0) == "written", code
        assert seen["n"] == 3, (code, seen)
    print("OK: 429/500/502/503/504 are retried until they succeed")


def test_permanent_failures_are_not_retried():
    """Waiting does not fix a permission error or a missing sheet, and retrying
    one only delays the report."""
    from sheets_client import with_retry

    for code in (400, 401, 403, 404):
        calls = {"n": 0}

        def doomed(code=code):
            calls["n"] += 1
            raise _ApiError(code)

        try:
            with_retry(doomed, "a write", base=1.0)
        except _ApiError:
            assert calls["n"] == 1, (code, calls)
            continue
        raise AssertionError(f"{code} should have been raised straight away")
    print("OK: 400/401/403/404 are raised at once, never retried")


def test_a_transient_failure_that_never_clears_still_gives_up():
    from sheets_client import with_retry

    calls = {"n": 0}

    def always(**_):
        calls["n"] += 1
        raise _ApiError(503)

    try:
        with_retry(always, "a write", tries=3, base=1.0)
    except _ApiError:
        assert calls["n"] == 3, calls
        print("OK: a failure that never clears gives up rather than looping forever")
        return
    raise AssertionError("should have raised after exhausting attempts")


if __name__ == "__main__":
    test_raw_json_is_accepted()
    test_a_file_path_is_accepted()
    test_nothing_and_nonsense_are_refused_with_distinct_messages()
    test_transient_google_failures_are_retried()
    test_permanent_failures_are_not_retried()
    test_a_transient_failure_that_never_clears_still_gives_up()
    print("\nALL CREDENTIAL TESTS PASSED")
