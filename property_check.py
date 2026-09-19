"""What the sync WOULD write for a property, against what the sheet holds.

    python property_check.py --property "224 Ogle"

STRICTLY READ-ONLY.

It does not re-implement any of the sync's rules. It calls the same functions in
the same order the sync does -- fetch, adapter, processing, the CSV city fill, the
city filter -- and then reads the sheet. Anything it says about what "should" be
there is what the real pipeline actually produced.

That matters because every wrong answer in this investigation came from a
diagnostic that approximated the sync instead of running it: one that read a single
city path where the sync reads three, and one that tested Guesty's raw city and
skipped the CSV fallback. Both produced confident nonsense.
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields
from processing import process_reservations
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe, read_row_marks
from sync import (DEFAULT_CITIES, coverage_window, filter_to_cities, guesty_token,
                  listing_city_seed, load_config)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--property", required=True,
                    help="Substring match, e.g. '224 Ogle' for every unit.")
    args = ap.parse_args(argv)
    needle = args.property.strip().lower()

    cfg = load_config()
    from guesty_client import fetch_reservations

    lo, hi = coverage_window(cfg)
    token = guesty_token(cfg)
    reservations = fetch_reservations(token, filters=[
        {"field": "checkOut", "operator": "$gte", "value": lo},
        {"field": "checkIn", "operator": "$lte", "value": hi},
        {"field": "status", "operator": "$in", "value": cfg["statuses"]},
    ], fields=requested_fields())
    print(f"Fetched {len(reservations)} reservation(s), window {lo} .. {hi}\n")

    # --- exactly what sync.run does, in the same order -----------------------
    df_co, df_ci = reservations_to_frames(reservations)
    candidates = process_reservations(df_co, df_ci, city_seed=listing_city_seed(cfg))
    from sheet_merge import build_city_resolver

    resolve = build_city_resolver(pd.DataFrame())
    candidates["City"] = [str(c).strip() or resolve(p) for c, p
                          in zip(candidates["City"], candidates["Property"])]
    kept, _dropped = filter_to_cities(candidates,
                                      cfg.get("cities") or DEFAULT_CITIES)

    want = kept[kept["Property"].astype(str).str.lower().str.contains(needle)]
    before = candidates[candidates["Property"].astype(str).str.lower()
                        .str.contains(needle)]
    print(f"\n=== the pipeline produced {len(want)} row(s) for {args.property!r} ===")
    if len(before) != len(want):
        print(f"    ({len(before) - len(want)} were dropped by the city filter)")
    for _, r in want.sort_values("Date").iterrows():
        print(f"   {r['Date']}  {r['Property']:<18}{str(r['Guest'])[:22]:<24}"
              f"{r['Confirmation Code']:<14}out={r['Check-out Time'] or '--':<9}"
              f"in={r['Check-in Time'] or '--'}")

    # --- what the sheet holds -------------------------------------------------
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    months = sorted({(int(d[:4]), int(d[5:7])) for d in want["Date"].astype(str)})
    print(f"\n=== what the sheet holds for {args.property!r} ===")
    on_sheet = set()
    for key in months:
        ws = tabs.get(key)
        if ws is None:
            print(f"   (no tab for {key[0]}-{key[1]:02d})")
            continue
        rows, _ = read_as_dataframe(ws)
        struck, _hl, _ac = read_row_marks(ws)
        hits = [(i, r) for i, r in rows.iterrows()
                if needle in str(r.get("Property", "")).lower()]
        print(f"   {ws.title}: {len(hits)} row(s)")
        for i, r in hits:
            d = str(r.get("Date", ""))[:10]
            on_sheet.add((str(r.get("Property", "")).strip(), d))
            print(f"      row {i + 2:<6}{d}  {str(r.get('Property','')):<18}"
                  f"{str(r.get('Guest',''))[:22]:<24}"
                  f"{str(r.get('Confirmation Code','')):<14}"
                  f"{'STRUCK' if i in struck else ''}")

    # --- and what the MERGE decides for it ------------------------------------
    #
    # The comparison above says what should exist. This says what the merge does
    # with it -- the flag it assigns and where the row lands -- because a row can
    # be reported "cancelled" in the log and still not be struck on the sheet, and
    # only the merge's own output distinguishes those.
    from sheets_client import _STRIKE_FLAGS, read_row_marks as _rm
    from sheet_merge import merge_reservations_into_sheet
    from sync import tab_cancel_window

    print()
    print("=== what the merge decides ===")
    for key in months:
        ws = tabs.get(key)
        if ws is None:
            continue
        ym = f"{key[0]}-{key[1]:02d}"
        sheet_df, header_raw = read_as_dataframe(ws)
        prior_struck, _phl, _pac = _rm(ws)
        month_cands = kept[kept["Date"].astype(str).str[:7] == ym]
        full, stats, ch = merge_reservations_into_sheet(
            month_cands.reset_index(drop=True), sheet_df,
            cancel_window=tab_cancel_window(coverage_window(cfg), ym),
            struck_rows=frozenset(prior_struck),
            allowed_cities=frozenset(cfg.get("cities") or DEFAULT_CITIES))
        flags = ch["row_flags"]
        hits = [i for i in range(len(full))
                if needle in str(full.iloc[i].get("Property", "")).lower()]
        print(f"   {ws.title}: {len(hits)} row(s) matched")
        for i in hits:
            r = full.iloc[i]
            f = flags[i]
            print(f"      out-row {i + 2:<6}{str(r.get('Date'))[:10]}  "
                  f"{str(r.get('Property')):<18}{str(r.get('Guest'))[:20]:<22}"
                  f"flag={f or '(none)':<12}"
                  f"{'-> WILL BE STRUCK' if f in _STRIKE_FLAGS else ''}")

    missing = [(r["Property"], r["Date"]) for _, r in want.iterrows()
               if (str(r["Property"]).strip(), str(r["Date"])[:10]) not in on_sheet]
    print("\n" + "=" * 70)
    if missing:
        print(f"  {len(missing)} row(s) the pipeline produced are NOT on the sheet:")
        for p, d in sorted(missing, key=lambda x: x[1]):
            print(f"     {d}  {p}")
    else:
        print("  Every row the pipeline produced is on the sheet.")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
