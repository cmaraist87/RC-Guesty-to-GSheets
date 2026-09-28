"""Which Google Cloud project does this system actually run in?

    python gcp_whoami.py

STRICTLY READ-ONLY. Prints the project and service account the sync
authenticates as, and whether the state bucket answers.

The project id and the service-account address are identifiers, not
credentials -- both appear in console URLs and IAM screens. The key itself is
never printed, and nothing here can write.
"""
from __future__ import annotations

import os
import sys

from sheets_client import service_account_info


def main(argv=None) -> int:
    raw = os.environ.get("GOOGLE_SA_JSON", "")
    if not raw.strip():
        print("GOOGLE_SA_JSON is not set.", file=sys.stderr)
        return 2
    try:
        info = service_account_info(raw)
    except Exception as e:  # noqa: BLE001
        print(f"could not parse the service-account key: {e}", file=sys.stderr)
        return 2

    print("The Google Cloud project this system runs in")
    print("=" * 52)
    print(f"  project id       {info.get('project_id')}")
    print(f"  service account  {info.get('client_email')}")
    print(f"  key id (last 6)  ...{str(info.get('private_key_id', ''))[-6:]}")

    bucket = (os.environ.get("STATE_BUCKET") or "").strip()
    print(f"  state bucket     {bucket or '(not set)'}")
    if not bucket:
        return 0

    # Does that bucket actually answer to this key? A project id from the key
    # is only the right answer if the bucket lives with it.
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
        s = AuthorizedSession(creds)
        r = s.get(f"https://storage.googleapis.com/storage/v1/b/{bucket}",
                  params={"fields": "name,projectNumber,location"}, timeout=20)
        if r.status_code == 200:
            d = r.json()
            print(f"  bucket answers   YES  (project number {d.get('projectNumber')}, "
                  f"{d.get('location')})")
        else:
            print(f"  bucket answers   HTTP {r.status_code} -- the key may belong to "
                  f"a different project than the bucket")
    except Exception as e:  # noqa: BLE001
        print(f"  bucket answers   could not check ({e.__class__.__name__})")

    print()
    print("  Use this project id in Cloud Shell. Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
