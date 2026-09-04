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
    SYNC_MARK_CANCELLED  (default 1)         strike through rows whose reservation
                                             disappeared from Guesty instead of
                                             leaving them silently in the sheet
    SYNC_REPAIR_SHIFTED_TABS  (default 0)    DESTRUCTIVE, one-off. A tab written
                                             with the pre-fix shifted layout is
                                             normally skipped; set this to wipe its
                                             data rows and repopulate the month from
                                             Guesty. Clear it again once the affected
                                             tabs are healthy.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields, FIELD_MAP, _dig
from processing import _canonical_key, process_reservations
from sheet_merge import ShiftedLayoutError, merge_reservations_into_sheet, norm_city

# The markets this sheet covers. Guesty holds listings well outside them (Phoenix,
# New England); those reservations are not this team's work and must never reach a
# tab. Until the City lookup was fixed, property_to_city.csv was accidentally doing
# this job -- an unlisted property got a blank City and stood out -- so the filter
# has to be explicit now that every reservation arrives with a real city.
DEFAULT_CITIES = ("New Orleans", "Bay St. Louis", "Austin", "Savannah", "Thunderbolt")

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

    # GitHub Actions passes unset repo variables as EMPTY strings (not missing), so
    # os.environ.get(key, default) would return "" and skip the default. _env treats
    # a blank/whitespace value as "not set" and falls back to the default.
    def _env(key: str, default: str = "") -> str:
        v = os.environ.get(key)
        return v if (v is not None and v.strip() != "") else default

    return {
        "client_id": _env("GUESTY_CLIENT_ID"),
        "client_secret": _env("GUESTY_CLIENT_SECRET"),
        "sheet_id": _env("SHEET_ID"),
        "worksheet": _env("WORKSHEET_NAME") or None,
        "template_tab": _env("TEMPLATE_TAB") or None,
        "sa_json": _env("GOOGLE_SA_JSON"),
        "lookback": int(_env("SYNC_LOOKBACK_DAYS", "1")),
        "lookahead": int(_env("SYNC_LOOKAHEAD_DAYS", "180")),
        "statuses": [s.strip() for s in
                     _env("SYNC_STATUSES", "confirmed,reserved,checkedIn").split(",")
                     if s.strip()],
        "mark_cancelled": _env("SYNC_MARK_CANCELLED", "1").lower()
                          not in ("0", "false", "no", "off"),
        "repair_shifted": _env("SYNC_REPAIR_SHIFTED_TABS", "0").lower()
                          in ("1", "true", "yes", "on"),
        # One-off: forget the strikethrough already on the grid and re-derive every
        # cancellation from Guesty. Needed once, because the lines currently on the
        # tabs were painted by grid position and slid onto the wrong rows.
        "repair_strikes": _env("SYNC_REPAIR_STRIKES", "0").lower()
                          in ("1", "true", "yes", "on"),
        # Housekeeping that DELETES rows. Both off by default: every other change
        # this sync makes is reversible on the next run, a deleted row is not.
        "delete_out_of_scope": _env("SYNC_DELETE_OUT_OF_SCOPE", "0").lower()
                               in ("1", "true", "yes", "on"),
        "collapse_duplicates": _env("SYNC_COLLAPSE_DUPLICATES", "0").lower()
                               in ("1", "true", "yes", "on"),
        "cities": tuple(c.strip() for c in _env(
            "SYNC_CITIES", ",".join(DEFAULT_CITIES)).split(",") if c.strip()),
        # Shared state (Guesty token today, more later). Blank disables it and
        # falls back to the single-process on-disk token cache.
        "state_bucket": _env("STATE_BUCKET", "rc-guesty-connecteam-state"),
    }


def coverage_window(cfg: dict) -> tuple[str, str]:
    """The date range the Guesty fetch fully covers -- outside it, a sheet row with
    no matching reservation means 'out of range', not 'cancelled'."""
    today = _today_chicago()
    return ((today - timedelta(days=cfg["lookback"])).isoformat(),
            (today + timedelta(days=cfg["lookahead"])).isoformat())


def tab_cancel_window(coverage: tuple[str, str] | None, ym: str) -> tuple[str, str] | None:
    """
    Narrow the coverage window to the tab's OWN month.

    A month tab is only authoritative for its own month. `Julio 2026` predates
    monthly routing and still holds ~790 rows dated August onwards; judged against
    the full coverage window every one of them looks cancelled, because those
    reservations now live in their own tabs. Scoping to the month means such strays
    are simply ignored. Returns None when the two ranges don't overlap.
    """
    if not coverage:
        return None
    import calendar

    y, m = int(ym[:4]), int(ym[5:7])
    lo = max(coverage[0], f"{ym}-01")
    hi = min(coverage[1], f"{ym}-{calendar.monthrange(y, m)[1]:02d}")
    return (lo, hi) if lo <= hi else None


def build_filters(cfg: dict) -> list[dict]:
    start, end = coverage_window(cfg)
    return [
        {"field": "checkOut", "operator": "$gte", "value": start},
        {"field": "checkIn", "operator": "$lte", "value": end},
        {"field": "status", "operator": "$in", "value": cfg["statuses"]},
    ]


def state_store(cfg: dict):
    """The shared-state object store, or None when it isn't configured/reachable.

    Same service-account key as the Sheets write, different scope: Sheets access
    comes from sharing the spreadsheet with the account, Cloud Storage access from
    roles/storage.objectAdmin on the bucket.
    """
    bucket = (cfg.get("state_bucket") or "").strip()
    if not bucket:
        return None
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        from lease_lock import GCSObjectStore
        from sheets_client import service_account_info

        creds = service_account.Credentials.from_service_account_info(
            service_account_info(cfg["sa_json"]),
            scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
        return GCSObjectStore(bucket, session=AuthorizedSession(creds))
    except Exception as e:  # noqa: BLE001 - never let state plumbing fail the sync
        print(f"!! Could not reach the shared state bucket {bucket!r}: {e}")
        print("   Falling back to the local token cache for this run.")
        return None


def guesty_token(cfg: dict) -> str:
    """A Guesty token, shared with every other runtime on this account.

    The cron MAY mint: it is one process, so a fallback costs at most one request
    and the daily sync has to survive a Cloud Storage outage. An on-demand handler
    scales to many instances and must pass may_mint=False instead.
    """
    from guesty_client import SharedTokenCache, get_access_token

    store = state_store(cfg)
    if store is None:
        return get_access_token(cfg["client_id"], cfg["client_secret"])
    return SharedTokenCache(store).get(cfg["client_id"], cfg["client_secret"],
                                       may_mint=True)


def _note_summary(text: str) -> None:
    """One line at the top of the GitHub run Summary. Never fails the sync."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + chr(10) + chr(10))
    except OSError:
        pass


def record_reservation_snapshot(reservations: list[dict], cfg: dict,
                                dry_run: bool = False) -> dict:
    """Fingerprint tonight's reservations, report what moved since last night, store it.

    Costs no API call and no token quota -- every field comes from the fetch that has
    already happened. Its job is to answer, from real data, whether an edited
    reservation keeps its confirmation code, which decides what the sheet may safely
    key rows on. It is also a standing audit trail of what changed each night.

    Never fails the sync: an unreachable bucket degrades to "no diff this run".
    A dry run reports the diff but does not advance the stored baseline, so a dry run
    cannot consume the comparison a later live run was going to make.
    """
    from reservation_snapshot import diff, fingerprint, format_report, load, save

    store = state_store(cfg)
    before, generation, taken_at = load(store)
    after = fingerprint(reservations)
    # Tonight's coverage, so a reservation that merely aged out of the sliding
    # fetch window is not announced as a cancellation.
    d = diff(before, after, window=coverage_window(cfg))
    print()
    print(format_report(d, taken_at=taken_at))
    if dry_run:
        print("   (dry run: baseline left as it was)")
    elif not save(store, after, generation):
        print("   (snapshot not stored; tomorrow will compare against an older run)")
    return d


def fetch_from_guesty(cfg: dict) -> list[dict]:
    from guesty_client import fetch_reservations
    token = guesty_token(cfg)
    filters = build_filters(cfg)
    print(f"Fetching reservations with filters: {json.dumps(filters)}")
    reservations = fetch_reservations(token, filters=filters, fields=requested_fields())
    print(f"Fetched {len(reservations)} reservations.")
    report_city_coverage(reservations)
    return reservations


def report_city_coverage(reservations: list[dict]) -> None:
    """
    How many reservations arrived with a city on their listing?

    `listing.address.city` outranks property_to_city.csv (see process_reservations),
    so if Guesty supplies it the CSV only ever needs to cover the gaps. Hundreds of
    rows with no City therefore means one of two very different things, and guessing
    which has been the blocker: either those listings have no address in Guesty, or
    the `fields` request isn't bringing the nested address back at all. This says
    which, in one line, without dumping any guest data.
    """
    if not reservations:
        return
    with_city = sum(1 for r in reservations if str(_first_city(r)).strip())
    n = len(reservations)
    print(f"\n--- City source check ---")
    print(f"  {with_city}/{n} reservation(s) carried a city from Guesty "
          f"({with_city / n:.0%}).")
    if not with_city:
        # Nothing came back at all -> almost certainly the request, not the data.
        listing = reservations[0].get("listing") or reservations[0].get("listingId")
        shape = (sorted(listing.keys()) if isinstance(listing, dict)
                 else f"{type(listing).__name__}: {str(listing)[:60]}")
        print("  NONE. The listing object we received looks like:")
        print(f"    {shape}")
        print("  If 'address' is absent, the `fields` param is trimming it -- ask for "
              "the dotted paths (listing.address.city) rather than the bare object.")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n**City source:** {with_city}/{n} reservation(s) "
                         f"({with_city / n:.0%}) carried a city from Guesty; the rest "
                         "fall back to `property_to_city.csv`.\n")
        except OSError:
            pass


def _first_city(res: dict):
    for path in FIELD_MAP["city"]:
        v = _dig(res, path)
        if v:
            return v
    return ""


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


_SPANISH_MONTHS = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                   "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _is_finished_month(ym: str) -> bool:
    """Has this month already ended, in Chicago time?

    The current month is not finished -- work is still being done in it -- so only
    strictly earlier months count. Chicago rather than UTC because that is the day
    the team is living in; on the 1st of a month those differ for five hours, and
    getting it wrong would refuse to create the tab everyone is about to use.
    """
    today = _today_chicago()
    return (int(ym[:4]), int(ym[5:7])) < (today.year, today.month)


def _spanish_tab(ym: str) -> str:
    """'2026-08' -> 'Agosto 2026' (the tab name to create for that month)."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{_SPANISH_MONTHS[m]} {y}"


def listing_city_seed(cfg: dict) -> dict:
    """
    {property name -> city} built from Guesty's LISTINGS, not its reservations.

    A listing's city belongs to the listing, so fetching it once per run beats
    projecting it onto 3000 bookings -- and it sidesteps the nested-field request
    that /reservations rejected. Whatever this returns seeds `process_reservations`;
    property_to_city.csv still fills anything left blank at merge time, so the CSV
    goes back to covering exceptions instead of the whole portfolio.

    OFF by default (SYNC_LISTING_CITIES=1 to enable) and deliberately fail-soft:
    the City column is a convenience, and no problem with it justifies failing a
    sync that is otherwise fine. Any error is reported and returns {}.
    """
    # Unset repo variables arrive as "" from Actions, so blank means off.
    flag = (os.environ.get("SYNC_LISTING_CITIES") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return {}
    try:
        from guesty_client import fetch_listings
        from processing import normalize_property

        token = guesty_token(cfg)
        # No `fields` param on purpose: asking Guesty to project a nested address
        # is exactly what failed on /reservations. Full objects, ~a few hundred.
        listings = fetch_listings(token)
        print(f"\nFetched {len(listings)} listing(s) for the City lookup.")

        seed, no_city = {}, []
        for lst in listings:
            name = str(lst.get("nickname") or lst.get("title") or "").strip()
            city = str(((lst.get("address") or {}).get("city") or "")).strip()
            if not name:
                continue
            if not city:
                no_city.append(name)
                continue
            seed[name] = city
            # A combo listing ("311 W York 1&2") is split by the pipeline, so seed
            # each unit it becomes as well or the split rows resolve to nothing.
            for prop in normalize_property(name):
                seed.setdefault(prop, city)

        print(f"  -> {len(seed)} property name(s) mapped to a city.")
        if no_city:
            print(f"  -> {len(no_city)} listing(s) have NO city in Guesty; "
                  "these still need property_to_city.csv:")
            for n in sorted(no_city)[:30]:
                print(f"       {n}")
        return seed
    except Exception as e:  # noqa: BLE001 - never fail the sync over the City column
        print(f"\n!! Could not build the City lookup from Guesty listings: {e}")
        print("   Falling back to property_to_city.csv alone.")
        return {}


def filter_to_cities(candidates: pd.DataFrame, cities) -> tuple[pd.DataFrame, dict]:
    """
    Keep only rows whose City is one this sheet covers, and say what was dropped.

    Guesty carries listings in markets this team does not service. Those rows are
    not "missing a city" -- they are somebody else's work, and they were only ever
    kept out by accident: an unlisted property got a blank City, which looked like a
    data gap. Now that every reservation arrives with a real city, the exclusion has
    to be stated outright.

    Rows with a BLANK city are dropped too, and reported separately: a row whose
    market cannot be confirmed must not be assumed in scope.

    Returns (kept, removed) where `removed` maps each dropped property's canonical
    key to the city that disqualified it. The merge needs that map: an existing
    sheet row for one of these produces no candidate, and without knowing WHY it
    would be struck as a cancellation -- telling the team a job was called off when
    the truth is it was never theirs.
    """
    if "City" not in candidates.columns or candidates.empty:
        return candidates, {}
    allowed = {norm_city(c) for c in cities}
    keys = candidates["City"].map(norm_city)
    keep = keys.isin(allowed)

    print(f"\n--- City filter: {', '.join(cities)} ---")
    print(f"  Kept {int(keep.sum())} of {len(candidates)} row(s).")
    dropped = candidates.loc[~keep]
    if len(dropped):
        blank = dropped[keys[~keep] == ""]
        named = dropped[keys[~keep] != ""]
        for city, grp in sorted(named.groupby(named["City"].str.strip()),
                                key=lambda kv: -len(kv[1])):
            props = sorted(set(grp["Property"].astype(str).str.strip()))
            print(f"    {len(grp):5d} row(s)  {city}  ({len(props)} propertie(s))")
            for p in props[:8]:
                print(f"             - {p}")
            if len(props) > 8:
                print(f"             ... and {len(props) - 8} more")
        if len(blank):
            props = sorted(set(blank["Property"].astype(str).str.strip()))
            print(f"    {len(blank):5d} row(s)  (NO CITY -- dropped, cannot confirm "
                  f"scope): {', '.join(props[:8])}"
                  + (f" ... and {len(props) - 8} more" if len(props) > 8 else ""))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path and len(dropped):
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n**City filter:** kept {int(keep.sum())} of "
                         f"{len(candidates)} row(s); cities covered: "
                         + ", ".join(f"`{c}`" for c in cities) + "\n\n")
                fh.write("| Dropped city | Rows | Properties |\n|---|----:|---|\n")
                lab = dropped["City"].astype(str).str.strip().replace("", "(no city)")
                for city, grp in sorted(dropped.groupby(lab),
                                        key=lambda kv: -len(kv[1])):
                    props = sorted(set(grp["Property"].astype(str).str.strip()))
                    shown = ", ".join(f"`{p}`" for p in props[:6])
                    if len(props) > 6:
                        shown += f" …+{len(props) - 6}"
                    fh.write(f"| {city} | {len(grp)} | {shown} |\n")
        except OSError:
            pass

    # Only properties with NO surviving row are out of scope; one that kept any row
    # is in scope and must stay eligible for ordinary cancellation detection.
    kept_props = {_canonical_key(p) for p in candidates.loc[keep, "Property"]}
    removed: dict[str, str] = {}
    for _, r in dropped.iterrows():
        key = _canonical_key(r["Property"])
        if key not in kept_props:
            removed.setdefault(key, str(r["City"]).strip() or "unknown city")
    return candidates.loc[keep].reset_index(drop=True), removed


def run(dry_run: bool, reservations: list[dict], cfg: dict) -> int:
    df_co, df_ci = reservations_to_frames(reservations)
    print(f"Adapter produced {len(df_co)} check-out rows, {len(df_ci)} check-in rows.")

    candidates = process_reservations(df_co, df_ci, city_seed=listing_city_seed(cfg))
    print(f"Processing produced {len(candidates)} schedule rows.")
    # Resolve City to its best-known value BEFORE filtering, using the same
    # Guesty-then-CSV priority the merge applies. Filtering on the raw value would
    # drop any listing Guesty has no address for, even one the CSV can place.
    if "City" in candidates.columns:
        from sheet_merge import build_city_resolver
        resolve = build_city_resolver(pd.DataFrame())
        candidates["City"] = [str(c).strip() or resolve(p) for c, p
                              in zip(candidates["City"], candidates["Property"])]
    candidates, out_of_scope_props = filter_to_cities(
        candidates, cfg.get("cities") or DEFAULT_CITIES)
    if candidates.empty:
        print("!! Nothing left after the city filter -- check SYNC_CITIES. "
              "Refusing to treat this as a month of cancellations.")
        return 1
    candidates = candidates.copy()
    candidates["_ym"] = candidates["Date"].astype(str).str.strip().str[:7]  # 'YYYY-MM'

    # No creds -> offline preview: merge everything against an empty sheet, write CSV.
    if not (cfg["sheet_id"] and cfg["sa_json"]):
        full, stats, changes = merge_reservations_into_sheet(
            candidates.drop(columns=["_ym"]), pd.DataFrame())
        full.to_csv(OUTPUT_CSV, index=False)
        emit_change_report(stats, changes, will_write=False, label="no-sheet")
        print("\nNo SHEET_ID/GOOGLE_SA_JSON set -> preview only (merged against empty sheet).")
        return 0

    from sheets_client import (open_spreadsheet, month_worksheets, read_as_dataframe,
                               read_row_marks, write_dataframe, create_month_tab,
                               clear_data_rows, read_checkbox_columns)
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    month_ws = month_worksheets(ss)
    if not month_ws:
        print("ERROR: no month-named tabs (e.g. 'Agosto 2026') found in the workbook. "
              "Tabs seen: " + ", ".join(w.title for w in ss.worksheets()), file=sys.stderr)
        return 3
    print(f"Found {len(month_ws)} monthly tab(s): "
          + ", ".join(sorted(w.title for w in month_ws.values())))

    # Template tab to duplicate when auto-creating a missing month (formatting/checkboxes).
    template_title = cfg["template_tab"] or month_ws[max(month_ws.keys())].title
    print(f"Template tab for auto-creating missing months: '{template_title}'")

    cancel_window = coverage_window(cfg) if cfg.get("mark_cancelled", True) else None
    if cancel_window:
        print(f"Cancellation detection ON for dates {cancel_window[0]} .. {cancel_window[1]}, "
              "further narrowed to each tab's own month (rows outside are never struck).")
    else:
        print("Cancellation detection OFF (SYNC_MARK_CANCELLED).")

    if cfg.get("repair_shifted"):
        print("SYNC_REPAIR_SHIFTED_TABS is ON: a tab still on the old shifted layout "
              "will have its data rows CLEARED and the month rebuilt from Guesty.")

    if cfg.get("delete_out_of_scope"):
        print("SYNC_DELETE_OUT_OF_SCOPE is ON: rows for cities this sheet does not "
              "cover will be DELETED, not just reported.")
    if cfg.get("collapse_duplicates"):
        print("SYNC_COLLAPSE_DUPLICATES is ON: repeated copies of one booking are "
              "collapsed to a single row, with every tick carried onto it.")

    if cfg.get("repair_strikes"):
        print("SYNC_REPAIR_STRIKES is ON: the strikethrough already on each tab is "
              "IGNORED and every cancellation is re-derived from Guesty.")
        print("   Run this ONCE. The existing lines were painted by grid position "
              "and slid onto the wrong rows as rows shifted, so preserving them "
              "would preserve the error. Re-deriving is the only way to get an "
              "honest month-end count.")
        print("   A row cancelled BEFORE the current fetch window cannot be "
              "re-derived and will lose its line -- check the window covers the "
              "months you care about.")

    grand = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0,
             "cancelled": 0, "moved": 0, "out_of_scope": 0, "missing_city": 0}
    skipped = []      # (ym, count): months with data but no tab (dry-run only)
    bygone = []       # (ym, count): finished months with no tab -- deliberately not created
    created = []      # titles of tabs auto-created this run
    repaired = []     # titles of shifted-layout tabs rebuilt from scratch
    # Tabs that exist but couldn't be merged into. These produce no per-tab report
    # at all, so without collecting them here the run Summary would read as though
    # the month simply had nothing to do -- see _write_grand_summary.
    layout_skipped = []   # dicts: title, ym, rows, reason, repairable
    # Every property with no City, across every tab. The per-tab lists are capped
    # for readability, which hid the true size of the gap (one tab reported 391 and
    # showed 50), so the whole set is reported once at the end instead.
    missing_city_props: set[str] = set()
    snapshots = []
    n_written = 0

    for ym, grp in candidates.groupby("_ym"):
        y, mth = int(ym[:4]), int(ym[5:7])
        cand = grp.drop(columns=["_ym"]).reset_index(drop=True)
        ws = month_ws.get((y, mth))
        if ws is None:
            if _is_finished_month(ym):
                # A month that has already ended gets no new tab. These rows are
                # real -- a long stay that began in May and ends in August still
                # produces a May check-in row -- but that clean happened months ago
                # and nobody will work it. Creating a whole tab for it is clutter,
                # and a wider lookback would spawn one per past month.
                #
                # A past tab that ALREADY exists is still written to, so July keeps
                # updating. This only declines to bring a finished month into being.
                bygone.append((ym, len(cand)))
                continue
            if dry_run:
                skipped.append((ym, len(cand)))
                continue
            new_title = _spanish_tab(ym)
            try:
                ws = create_month_tab(ss, template_title, new_title)
                created.append(new_title)
                print(f"\nAuto-created tab '{new_title}' from template '{template_title}'.")
            except Exception as e:  # noqa: BLE001 - report + skip this month, keep going
                print(f"\n!! Could not auto-create tab '{new_title}': {e}")
                skipped.append((ym, len(cand)))
                continue
        sheet_df, header_raw = read_as_dataframe(ws)
        prior_struck, prior_highlight = read_row_marks(ws)
        # Which columns really are checkboxes, per the tab's own data-validation.
        # Empty (unreadable tab, or none defined) -> merge falls back to guessing.
        cb_idx = read_checkbox_columns(ws)
        if not cb_idx:
            # The tab carries no tickbox rule at all -- which is the state a
            # rebuilt tab is left in, and why TRUE/FALSE showed as text. Fall back
            # to the header names so the write can put the rule ON rather than
            # silently producing a column of words.
            from sheet_merge import _is_checkbox_header
            cb_idx = {j for j, c in enumerate(sheet_df.columns)
                      if _is_checkbox_header(c)}
            if cb_idx:
                print(f"   ('{ws.title}' has no checkbox rule; identified "
                      f"{len(cb_idx)} checkbox column(s) by header name)")
        cb_cols = frozenset(sheet_df.columns[j] for j in cb_idx
                            if j < len(sheet_df.columns))
        try:
            full, stats, changes = merge_reservations_into_sheet(
                cand, sheet_df,
                cancel_window=tab_cancel_window(cancel_window, ym),
                # On a repair run, start from "nothing is struck" so every row is
                # re-examined against Guesty. The guard has to be relaxed to match:
                # a month's accumulated cancellations arriving in one run is exactly
                # the mass strike it exists to block, and here it is legitimate.
                struck_rows=frozenset() if cfg.get("repair_strikes") else prior_struck,
                delete_out_of_scope=bool(cfg.get("delete_out_of_scope")),
                collapse_duplicates=bool(cfg.get("collapse_duplicates")),
                cancel_guard=(1.0, 10 ** 9) if cfg.get("repair_strikes") else (0.5, 10),
                validated_checkboxes=cb_cols or None,
                allowed_cities=frozenset(cfg.get("cities") or DEFAULT_CITIES),
                out_of_scope_properties=out_of_scope_props,
            )
        except ShiftedLayoutError as e:
            if not cfg.get("repair_shifted"):
                print(f"\n!! Tab '{ws.title}': SKIPPED (not a reservations layout) -- {e}")
                layout_skipped.append({"title": ws.title, "ym": ym, "rows": len(cand),
                                       "reason": str(e), "repairable": True})
                continue
            # Nothing in a shifted tab can be matched, so the only repair is to drop
            # its rows and rebuild the month. The header (and every bit of formatting)
            # survives, which is what the merge needs to align the new rows.
            if dry_run:
                print(f"\n!! Tab '{ws.title}': WOULD BE REPAIRED -- {len(sheet_df)} "
                      f"shifted data row(s) cleared, month rebuilt from Guesty.")
            else:
                n_cleared = clear_data_rows(ws)
                print(f"\n!! Tab '{ws.title}': REPAIRED -- cleared {n_cleared} data "
                      f"row(s) of the old shifted layout; rebuilding from Guesty.")
            sheet_df = pd.DataFrame(columns=sheet_df.columns)
            prior_struck, prior_highlight = set(), set()
            repaired.append(ws.title)
            # No prior rows left, so nothing can be cancelled on this pass.
            full, stats, changes = merge_reservations_into_sheet(
                cand, sheet_df, cancel_window=None, struck_rows=frozenset(),
                validated_checkboxes=cb_cols or None,
                allowed_cities=frozenset(cfg.get("cities") or DEFAULT_CITIES),
                out_of_scope_properties=out_of_scope_props)
            # A rebuild writes every row, so every row is technically "new" and the
            # whole month comes out amber. That is true and useless: the highlight
            # means "look here, this changed today", and a thousand of them trains
            # people to ignore it. Write the rebuilt month unmarked, so the next
            # ordinary run produces the first meaningful diff.
            changes["row_flags"] = [""] * len(changes["row_flags"])
        except ValueError as e:
            print(f"\n!! Tab '{ws.title}': SKIPPED (not a reservations layout) -- {e}")
            layout_skipped.append({"title": ws.title, "ym": ym, "rows": len(cand),
                                   "reason": str(e), "repairable": False})
            continue
        for k in grand:
            grand[k] += stats.get(k, 0)
        missing_city_props.update(changes["missing_city_properties"])
        emit_change_report(stats, changes, will_write=(not dry_run), label=ws.title)
        snap = full.copy(); snap.insert(0, "_tab", ws.title)
        snap.insert(1, "_mark", changes["row_flags"]); snapshots.append(snap)
        if not dry_run:
            # The marks already on the grid, passed through as read. No remapping:
            # apply_row_marks now states the marks absolutely, and both these sets
            # and row_flags are indexed by the same grid positions. Remapping them
            # to the rows' NEW positions was the bug -- it cleared marks where the
            # rows had moved TO and left the real ones where they had moved FROM.
            marks = write_dataframe(ws, full, header_raw,
                                    row_flags=changes["row_flags"],
                                    prior_highlight=prior_highlight,
                                    prior_struck=prior_struck,
                                    checkbox_cols=cb_idx)
            n_written += 1
            print(f"   -> wrote {len(full)} rows to tab '{ws.title}'"
                  + (f" (highlighted {marks.get('highlighted', 0)}, "
                     f"struck {marks.get('struck', 0)}, "
                     f"cleared {marks.get('unhighlighted', 0)} old highlight(s)"
                     # Lines lifted off rows that must NOT carry one. On the first
                     # run after the anchoring fix this is the repair count: every
                     # live row wearing a line that slid down onto it. Steady state
                     # is 0, so a non-zero number later is worth a look.
                     + (f", lifted {marks['unstruck']} stale line(s)"
                        if marks.get("unstruck") else "")
                     + f" -- {marks.get('struck_total', 0)} row(s) struck in total)."
                     if marks else "."))
            if marks.get("rows_added"):
                print(f"      grew the tab by {marks['rows_added']} row(s) to fit.")
            if marks.get("checkbox_cols"):
                print(f"      applied the tickbox rule to "
                      f"{marks['checkbox_cols']} checkbox column(s).")

    if snapshots:
        pd.concat(snapshots, ignore_index=True).to_csv(OUTPUT_CSV, index=False)
        print(f"\nLocal snapshot of all tabs -> {OUTPUT_CSV}")

    print("\n" + "#" * 60)
    print("  GRAND TOTAL across monthly tabs")
    print(f"    New {grand['new']} | Updated {grand['updated']} | Removed {grand['removed']} "
          f"| Cancelled {grand['cancelled']} | Moved {grand['moved']} "
          f"| Unchanged {grand['unchanged']} | Missing City {grand['missing_city']}")
    if created:
        print("  Auto-created tabs: " + ", ".join(created))
    if bygone:
        n = sum(c for _, c in bygone)
        print(f"  {n} row(s) belong to {len(bygone)} month(s) that have already "
              f"ended and have no tab. No tab was created for them:")
        for ym, c in sorted(bygone):
            print(f"    {ym}: {c} row(s)  (would have been '{_spanish_tab(ym)}')")
        print("    These are mostly long stays that began before the window. If you "
              "do want one of these months, create the tab by hand and the next run "
              "will fill it.")
    if repaired:
        print(("  Tabs repaired (shifted layout cleared + rebuilt): "
               if not dry_run else
               "  Tabs that WOULD be repaired (shifted layout cleared + rebuilt): ")
              + ", ".join(repaired))
    if layout_skipped:
        n_rows = sum(t["rows"] for t in layout_skipped)
        print(f"  !! {len(layout_skipped)} TAB(S) SKIPPED -- {n_rows} reservation(s) "
              f"had nowhere to go:")
        for t in sorted(layout_skipped, key=lambda t: t["ym"]):
            print(f"    '{t['title']}': {t['rows']} rows not synced -- {t['reason']}")
        if any(t["repairable"] for t in layout_skipped) and not cfg.get("repair_shifted"):
            print("    Re-run with SYNC_REPAIR_SHIFTED_TABS=1 to rebuild the shifted "
                  "tab(s) from Guesty.")
    if skipped:
        print("  Months with reservations but NO tab yet "
              "(will be auto-created on the live run):")
        for ym, n in sorted(skipped):
            print(f"    {ym}: {n} rows  ->  '{_spanish_tab(ym)}'")
    print("#" * 60)

    if missing_city_props:
        print(f"  {len(missing_city_props)} distinct propertie(s) have no City "
              "-- see the paste-ready list in the run Summary.")

    _write_grand_summary(grand, skipped, created, repaired, layout_skipped,
                         will_write=(not dry_run),
                         repair_on=bool(cfg.get("repair_shifted")),
                         missing_city_props=missing_city_props)

    tail = (f" {len(layout_skipped)} tab(s) were SKIPPED and are unchanged."
            if layout_skipped else "")
    if dry_run:
        print("\nDRY RUN: no tabs were created, cleared or modified." + tail)
    else:
        print(f"\nLIVE: wrote {n_written} monthly tab(s)"
              + (f", auto-created {len(created)} new tab(s)" if created else "")
              + (f", repaired {len(repaired)} shifted tab(s)" if repaired else "")
              + "." + tail)
    return 0


def _write_grand_summary(grand: dict, skipped: list, created: list, repaired: list,
                         layout_skipped: list, will_write: bool,
                         repair_on: bool = False,
                         missing_city_props: set | None = None) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(f"\n## GRAND TOTAL — {'LIVE (tabs written)' if will_write else 'DRY RUN'}\n\n")
            # First thing in the section: a skipped tab produces no per-tab report of
            # its own, so this warning is the ONLY place its month appears. Without it
            # the Summary reads as if that month had nothing to sync.
            if layout_skipped:
                n_rows = sum(t["rows"] for t in layout_skipped)
                fh.write(f"> [!WARNING]\n> **{len(layout_skipped)} tab(s) skipped — "
                         f"{n_rows} reservation(s) were NOT synced.**\n> "
                         "These tabs exist but could not be merged into, so the totals "
                         "below exclude them entirely.\n\n")
                fh.write("| Tab | Rows not synced | Why |\n|---|----:|---|\n")
                for t in sorted(layout_skipped, key=lambda t: t["ym"]):
                    fh.write(f"| `{t['title']}` | {t['rows']} | {t['reason']} |\n")
                if any(t["repairable"] for t in layout_skipped) and not repair_on:
                    fh.write("\nRe-run this workflow with the **repair** checkbox ticked "
                             "to clear the shifted tab(s) and rebuild them from Guesty.\n")
                fh.write("\n")
            fh.write("| New | Updated | Removed | Cancelled | Moved | Unchanged | Missing City |\n")
            fh.write("|----:|--------:|--------:|----------:|------:|----------:|-------------:|\n")
            fh.write(f"| {grand['new']} | {grand['updated']} | {grand['removed']} | "
                     f"{grand['cancelled']} | {grand['moved']} | {grand['unchanged']} | "
                     f"{grand['missing_city']} |\n")
            if created:
                fh.write("\n**Auto-created tabs:** " + ", ".join(f"`{t}`" for t in created) + "\n")
            if repaired:
                fh.write(f"\n**Tabs {'repaired' if will_write else 'that would be repaired'} "
                         "(shifted layout cleared + rebuilt):** "
                         + ", ".join(f"`{t}`" for t in repaired) + "\n")
            if skipped:
                fh.write("\n**Months with reservations but no tab yet "
                         "(auto-created on the live run):**\n")
                for ym, n in sorted(skipped):
                    fh.write(f"- {ym}: {n} rows -> `{_spanish_tab(ym)}`\n")
            if missing_city_props:
                # Paste-ready and COMPLETE -- the per-tab lists above are capped, so
                # this is the only place the whole gap is visible. Collapsed because
                # it can run to hundreds of lines.
                props = sorted(missing_city_props)
                fh.write(f"\n<details><summary><b>{len(props)} propertie(s) with no "
                         "City</b> — click to expand, then fill in the city and paste "
                         "into <code>property_to_city.csv</code></summary>\n\n")
                # csv.writer, not an f-string: property names like
                # "56 Turner 2,3,4&5" contain commas and MUST come out quoted, or
                # the pasted row silently parses as four columns and never matches.
                buf = io.StringIO()
                csv.writer(buf, lineterminator="\n").writerows([[p, ""] for p in props])
                fh.write("```csv\n" + buf.getvalue() + "```\n\n</details>\n")
    except OSError:
        pass


def _fmt_rows(records: list[dict], cols: list[str], cap: int = 40) -> str:
    if not records:
        return "  (none)"
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in records[:cap])) for c in cols}
    head = "  " + "  ".join(c.ljust(widths[c]) for c in cols)
    lines = [head, "  " + "  ".join("-" * widths[c] for c in cols)]
    for r in records[:cap]:
        lines.append("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    if len(records) > cap:
        lines.append(f"  ... and {len(records) - cap} more")
    return "\n".join(lines)


def _md_table(records: list[dict], cols: list[str], cap: int = 50) -> str:
    if not records:
        return "_none_\n"
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in records[:cap]:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    if len(records) > cap:
        out.append(f"\n_…and {len(records) - cap} more_")
    return "\n".join(out) + "\n"


def emit_change_report(stats: dict, changes: dict, will_write: bool, label: str = "") -> None:
    """Print a human-readable 'what changed' summary and, on GitHub Actions, write
    a markdown panel to the run Summary page ($GITHUB_STEP_SUMMARY)."""
    verb = "WRITTEN TO TAB" if will_write else "PREVIEW ONLY (no write)"
    tag = f"[{label}] " if label else ""
    cols = ["Date", "Property", "Guest", "Confirmation Code", "T/O"]
    rcols = ["Date", "Property", "Guest", "Confirmation Code"]
    mcols = rcols + ["Now at"]

    print("\n" + "=" * 60)
    print(f"  {tag}WHAT CHANGED  --  {verb}")
    print("=" * 60)
    print(f"  New rows        : {stats['new']}  (highlighted)")
    print(f"  Updated rows    : {stats['updated']}  (highlighted)")
    print(f"  Removed rows    : {stats['removed']}")
    print(f"  Cancelled rows  : {stats.get('cancelled', 0)}  (struck through, kept in place)")
    print(f"  Moved rows      : {stats.get('moved', 0)}  (reassigned in Guesty; row moved, NOT a cancellation)")
    oos_del = stats.get("out_of_scope_deleted", 0)
    print(f"  Out of scope    : {stats.get('out_of_scope', 0)}  "
          + (f"(city this sheet does not cover; {oos_del} DELETED)" if oos_del
             else "(city this sheet does not cover; left alone)"))
    if stats.get("duplicates_removed"):
        print(f"  Duplicates      : {stats['duplicates_removed']}  "
              f"(repeated copies of one booking, collapsed; ticks carried over)")
    print(f"  Unchanged       : {stats['unchanged']}")
    print(f"  Total in sheet  : {stats['total_rows']}")
    print(f"  Missing City    : {stats['missing_city']}")
    print("\n  --- New (highlighted) ---\n" + _fmt_rows(changes["new"], cols))
    print("\n  --- Updated (old row dropped, checkbox carried over, highlighted) ---\n"
          + _fmt_rows(changes["updated"], cols))
    print("\n  --- Removed (superseded by an updated row) ---\n"
          + _fmt_rows(changes["removed"], rcols))
    print("\n  --- Cancelled (no longer in Guesty -> struck through) ---\n"
          + _fmt_rows(changes.get("cancelled", []), rcols))
    print("\n  --- Moved (same booking, new listing/date -> old row removed, new one highlighted) ---\n"
          + _fmt_rows(changes.get("moved", []), mcols))
    if changes.get("out_of_scope"):
        print("\n  --- Out of scope (city not covered; NOT struck, delete by hand) ---\n"
              + _fmt_rows(changes["out_of_scope"], rcols + ["City"]))
    if stats.get("cancel_guard_tripped"):
        print("\n  !! CANCELLATION GUARD: " + stats["cancel_guard_tripped"])
    if changes["missing_city_properties"]:
        print("\n  --- Properties with no City (add to property_to_city.csv) ---")
        for p in changes["missing_city_properties"][:40]:
            print(f"    {p}")
    print("=" * 60)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(f"## {tag}Guesty → Sheet sync — {verb}\n\n")
                fh.write(f"| New | Updated | Removed | Cancelled | Moved | Unchanged | Total | Missing City |\n")
                fh.write(f"|----:|--------:|--------:|----------:|------:|----------:|------:|-------------:|\n")
                fh.write(f"| {stats['new']} | {stats['updated']} | {stats['removed']} | "
                         f"{stats.get('cancelled', 0)} | {stats.get('moved', 0)} | "
                         f"{stats['unchanged']} | "
                         f"{stats['total_rows']} | {stats['missing_city']} |\n\n")
                fh.write("### New (highlighted)\n" + _md_table(changes["new"], cols))
                fh.write("\n### Updated (highlighted)\n" + _md_table(changes["updated"], cols))
                fh.write("\n### Removed (superseded)\n" + _md_table(changes["removed"], rcols))
                fh.write("\n### Cancelled (struck through)\n"
                         + _md_table(changes.get("cancelled", []), rcols))
                fh.write("\n### Moved (reassigned in Guesty; row moved, NOT a cancellation)\n"
                         + _md_table(changes.get("moved", []), mcols))
                if changes.get("out_of_scope"):
                    fh.write("\n### Out of scope — city not covered "
                             "(left alone; delete by hand)\n"
                             + _md_table(changes["out_of_scope"], rcols + ["City"]))
                if stats.get("cancel_guard_tripped"):
                    fh.write("\n> ⚠️ **Cancellation guard tripped:** "
                             + stats["cancel_guard_tripped"] + "\n")
                if changes["missing_city_properties"]:
                    # Per-tab list stays capped -- the complete, deduplicated set is
                    # emitted once in the grand total, ready to paste into the CSV.
                    shown = changes["missing_city_properties"][:50]
                    fh.write("\n### Properties missing City (add to property_to_city.csv)\n")
                    for p in shown:
                        fh.write(f"- {p}\n")
                    extra = len(changes["missing_city_properties"]) - len(shown)
                    if extra > 0:
                        fh.write(f"- _…and {extra} more — see the full list under "
                                 "GRAND TOTAL_\n")
        except OSError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily Guesty -> Google Sheet sync.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch + process + merge, write local CSV, but do NOT modify the sheet.")
    ap.add_argument("--scheduled", action="store_true",
                    help="This is the nightly cron trigger. Runs only if today's sync "
                         "has not already happened (see daily_gate), so a late or "
                         "duplicated trigger neither misses the day nor repeats it.")
    ap.add_argument("--from-json", metavar="PATH",
                    help="Read reservations from a local JSON file instead of the Guesty API.")
    args = ap.parse_args(argv)

    cfg = load_config()

    if args.scheduled:
        # Ask shared state whether today's run has happened, not the clock whether
        # it is 4 AM. GitHub delivered both of 2026-09-01's triggers more than four
        # hours late, and an exact-hour check skipped them both, silently.
        from daily_gate import mark_complete, should_run

        store = state_store(cfg)
        if store is None:
            # Not a stand-down -- the shared state is unreachable, and without it the
            # gate degrades to the exact-hour rule, which never matches because
            # GitHub delivers these triggers hours late. So the sync would silently
            # not run, every day, behind a green tick. That is the exact failure this
            # gate was built to end, so FAIL the run instead of passing quietly.
            print("!! The shared state bucket is unreachable, so today's run cannot "
                  "be claimed.", file=sys.stderr)
            print("   Refusing to continue: without it this job skips silently every "
                  "morning behind a green tick.", file=sys.stderr)
            print("   Check the GOOGLE_SA_JSON secret and STATE_BUCKET.", file=sys.stderr)
            _note_summary("# Sync did NOT run" + chr(10) * 2
                          + "The shared state bucket is unreachable, so today's run "
                            "could not be claimed. Check the GOOGLE_SA_JSON secret.")
            return 4
        go, why = should_run(store)
        print(f"Scheduled trigger: {why}")
        # Say so on the run's Summary page too. Two triggers fire every morning and
        # only one does the work; without this the pair are indistinguishable in the
        # Actions list, and reading the wrong one wastes a round trip.
        headline = "# Stood down" if not go else "# Running today's sync"
        _note_summary(headline + chr(10) + chr(10) + why)
        if not go:
            return 0

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
    if not args.from_json:
        # Only ever snapshot a real full fetch. A --from-json payload is a partial,
        # possibly stale dataset: diffing it would invent cancellations, and storing
        # it would destroy the baseline the next live run needs.
        record_reservation_snapshot(reservations, cfg, dry_run=args.dry_run)
    rc = run(args.dry_run, reservations, cfg)
    if args.scheduled and rc == 0:
        # Close the day only on success. A failed run leaves the claim open so a
        # later trigger retries rather than the whole day being lost.
        from daily_gate import mark_complete

        if mark_complete(state_store(cfg)):
            print("Recorded today's sync as complete.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
