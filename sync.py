"""
Daily Guesty -> Google Sheet sync (entry point for the 4 AM CST GitHub Action).

Flow:
    Guesty /reservations  (guesty_client)
      -> reservation JSON -> checkout_df + checkin_df   (guesty_adapter)
      -> processing.process_reservations                 (existing engine, untouched)
      -> merge vs current sheet, preserve checkboxes      (sheet_merge)
      -> write back to the live sheet                      (sheets_client)

Usage:
    python sync.py             # full run: fetch -> merge -> WRITE to the sheet
    python sync.py --dry-run   # fetch + process + merge, write a local CSV, but
                               # DO NOT touch the Google Sheet. Prints the shape of
                               # the first reservation so you can verify FIELD_MAP.
    python sync.py --from-json sample.json   # offline: read reservations from a file
                                             # (skips the Guesty API entirely)

Config (environment variables):
    GUESTY_CLIENT_ID, GUESTY_CLIENT_SECRET   Guesty Open API credentials
    SHEET_ID                                 target spreadsheet ID (from its URL)
    WORKSHEET_NAME                           tab name (optional; default first tab)
    GOOGLE_SA_JSON                           service-account key (path or raw JSON)
    SYNC_LOOKBACK_DAYS   (default 1)         include check-outs from N days ago
    SYNC_LOOKAHEAD_DAYS  (default 180)       include check-ins up to N days ahead
    SYNC_STATUSES        (default confirmed,reserved,checkedIn)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields, FIELD_MAP, _dig
from processing import process_reservations
from sheet_merge import merge_reservations_into_sheet

# Local snapshot of what was (or would be) written to the sheet. Deliberately NOT
# "sheet_updated.csv" so automated runs never clobber the notebook's own output.
OUTPUT_CSV = "sync_output.csv"


def _today_chicago() -> date:
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        return datetime.now(ZoneInfo("America/Chicago")).date()
    except Exception:
        from datetime import datetime
        return datetime.utcnow().date()


def load_config() -> dict:
    # Convenience: pull Guesty creds from variables.env if not already in the env.
    if os.path.exists("variables.env") and not os.environ.get("GUESTY_CLIENT_ID"):
        for line in open("variables.env", encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "client_ID":
                os.environ.setdefault("GUESTY_CLIENT_ID", v)
            elif k == "SECRET_KEY":
                os.environ.setdefault("GUESTY_CLIENT_SECRET", v)

    return {
        "client_id": os.environ.get("GUESTY_CLIENT_ID", ""),
        "client_secret": os.environ.get("GUESTY_CLIENT_SECRET", ""),
        "sheet_id": os.environ.get("SHEET_ID", ""),
        "worksheet": os.environ.get("WORKSHEET_NAME") or None,
        "sa_json": os.environ.get("GOOGLE_SA_JSON", ""),
        "lookback": int(os.environ.get("SYNC_LOOKBACK_DAYS", "1")),
        "lookahead": int(os.environ.get("SYNC_LOOKAHEAD_DAYS", "180")),
        "statuses": [s.strip() for s in
                     os.environ.get("SYNC_STATUSES", "confirmed,reserved,checkedIn").split(",")
                     if s.strip()],
    }


def build_filters(cfg: dict) -> list[dict]:
    today = _today_chicago()
    start = (today - timedelta(days=cfg["lookback"])).isoformat()
    end = (today + timedelta(days=cfg["lookahead"])).isoformat()
    return [
        {"field": "checkOut", "operator": "$gte", "value": start},
        {"field": "checkIn", "operator": "$lte", "value": end},
        {"field": "status", "operator": "$in", "value": cfg["statuses"]},
    ]


def fetch_from_guesty(cfg: dict) -> list[dict]:
    from guesty_client import get_access_token, fetch_reservations
    token = get_access_token(cfg["client_id"], cfg["client_secret"])
    filters = build_filters(cfg)
    print(f"Fetching reservations with filters: {json.dumps(filters)}")
    reservations = fetch_reservations(token, filters=filters, fields=requested_fields())
    print(f"Fetched {len(reservations)} reservations.")
    return reservations


def describe_first(reservations: list[dict]) -> None:
    """Print the shape of the first reservation so FIELD_MAP can be verified."""
    if not reservations:
        print("(no reservations returned -- nothing to inspect)")
        return
    r0 = reservations[0]
    print("\n--- First reservation: top-level keys ---")
    print(sorted(r0.keys()))
    print("\n--- FIELD_MAP resolution on the first reservation ---")
    for logical, paths in FIELD_MAP.items():
        hit = next(((p, _dig(r0, p)) for p in paths
                    if _dig(r0, p) not in (None, "")), None)
        print(f"  {logical:14s} -> {hit if hit else 'NOT FOUND (checked ' + str(paths) + ')'}")
    print()


def run(dry_run: bool, reservations: list[dict], cfg: dict) -> int:
    df_co, df_ci = reservations_to_frames(reservations)
    print(f"Adapter produced {len(df_co)} check-out rows, {len(df_ci)} check-in rows.")

    candidates = process_reservations(df_co, df_ci)
    print(f"Processing produced {len(candidates)} schedule rows.")

    # Read current sheet (skip the live read in dry-run if no creds configured)
    sheet = pd.DataFrame()
    ws = None
    if cfg["sheet_id"] and cfg["sa_json"]:
        from sheets_client import get_worksheet, read_as_dataframe
        ws = get_worksheet(cfg["sheet_id"], cfg["worksheet"], cfg["sa_json"])
        sheet, header_raw = read_as_dataframe(ws)
        print(f"Read current sheet: {len(sheet)} rows, {len(sheet.columns)} columns.")
    else:
        header_raw = list(candidates.columns)
        print("No SHEET_ID/GOOGLE_SA_JSON set -> merging against an empty sheet.")

    full, stats = merge_reservations_into_sheet(candidates, sheet)
    print("Merge stats:", json.dumps(stats))

    full.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote local snapshot -> {OUTPUT_CSV} ({len(full)} rows).")

    if dry_run:
        print("\nDRY RUN: the Google Sheet was NOT modified.")
        return 0

    if ws is None:
        print("\nNo Sheet configured -> nothing written live. "
              "Set SHEET_ID + GOOGLE_SA_JSON to enable the live write.")
        return 0

    from sheets_client import write_dataframe
    write_dataframe(ws, full, header_raw)
    print(f"Wrote {len(full)} rows to the live sheet (checkbox formatting preserved).")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily Guesty -> Google Sheet sync.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + process + merge, write local CSV, but do NOT modify the sheet.")
    ap.add_argument("--from-json", metavar="PATH",
                    help="Read reservations from a local JSON file instead of the Guesty API.")
    args = ap.parse_args(argv)

    cfg = load_config()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as fh:
            data = json.load(fh)
        reservations = data.get("results", data) if isinstance(data, dict) else data
        print(f"Loaded {len(reservations)} reservations from {args.from_json}.")
    else:
        if not cfg["client_id"] or not cfg["client_secret"]:
            print("ERROR: GUESTY_CLIENT_ID / GUESTY_CLIENT_SECRET not set. "
                  "Set them (or use --from-json) to run.", file=sys.stderr)
            return 2
        reservations = fetch_from_guesty(cfg)

    describe_first(reservations)
    return run(args.dry_run, reservations, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
