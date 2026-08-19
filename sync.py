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
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

from guesty_adapter import reservations_to_frames, requested_fields, FIELD_MAP, _dig
from processing import process_reservations
from sheet_merge import ShiftedLayoutError, merge_reservations_into_sheet

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


_SPANISH_MONTHS = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                   "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _spanish_tab(ym: str) -> str:
    """'2026-08' -> 'Agosto 2026' (the tab name to create for that month)."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{_SPANISH_MONTHS[m]} {y}"


def run(dry_run: bool, reservations: list[dict], cfg: dict) -> int:
    df_co, df_ci = reservations_to_frames(reservations)
    print(f"Adapter produced {len(df_co)} check-out rows, {len(df_ci)} check-in rows.")

    candidates = process_reservations(df_co, df_ci)
    print(f"Processing produced {len(candidates)} schedule rows.")
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
                               clear_data_rows)
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

    grand = {"new": 0, "updated": 0, "removed": 0, "unchanged": 0,
             "cancelled": 0, "moved": 0, "missing_city": 0}
    skipped = []      # (ym, count): months with data but no tab (dry-run only)
    created = []      # titles of tabs auto-created this run
    repaired = []     # titles of shifted-layout tabs rebuilt from scratch
    # Tabs that exist but couldn't be merged into. These produce no per-tab report
    # at all, so without collecting them here the run Summary would read as though
    # the month simply had nothing to do -- see _write_grand_summary.
    layout_skipped = []   # dicts: title, ym, rows, reason, repairable
    snapshots = []
    n_written = 0

    for ym, grp in candidates.groupby("_ym"):
        y, mth = int(ym[:4]), int(ym[5:7])
        cand = grp.drop(columns=["_ym"]).reset_index(drop=True)
        ws = month_ws.get((y, mth))
        if ws is None:
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
        try:
            full, stats, changes = merge_reservations_into_sheet(
                cand, sheet_df,
                cancel_window=tab_cancel_window(cancel_window, ym),
                struck_rows=prior_struck,
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
                cand, sheet_df, cancel_window=None, struck_rows=frozenset())
        except ValueError as e:
            print(f"\n!! Tab '{ws.title}': SKIPPED (not a reservations layout) -- {e}")
            layout_skipped.append({"title": ws.title, "ym": ym, "rows": len(cand),
                                   "reason": str(e), "repairable": False})
            continue
        for k in grand:
            grand[k] += stats.get(k, 0)
        emit_change_report(stats, changes, will_write=(not dry_run), label=ws.title)
        snap = full.copy(); snap.insert(0, "_tab", ws.title)
        snap.insert(1, "_mark", changes["row_flags"]); snapshots.append(snap)
        if not dry_run:
            # Rows carried over from the last run that still wear yesterday's
            # highlight, remapped to their position in the block we're about to write.
            was_highlighted = {i for i, pos in enumerate(changes["kept_positions"])
                               if pos in prior_highlight}
            marks = write_dataframe(ws, full, header_raw,
                                    row_flags=changes["row_flags"],
                                    was_highlighted=was_highlighted)
            n_written += 1
            print(f"   -> wrote {len(full)} rows to tab '{ws.title}'"
                  + (f" (highlighted {marks.get('highlighted', 0)}, "
                     f"struck {marks.get('struck', 0)}, "
                     f"cleared {marks.get('unhighlighted', 0)} old highlight(s))."
                     if marks else "."))

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

    _write_grand_summary(grand, skipped, created, repaired, layout_skipped,
                         will_write=(not dry_run),
                         repair_on=bool(cfg.get("repair_shifted")))

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
                         repair_on: bool = False) -> None:
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
    print(f"  Moved rows      : {stats.get('moved', 0)}  (reassigned in Guesty; old slot struck)")
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
    print("\n  --- Moved (same booking, new listing/date -> old slot struck) ---\n"
          + _fmt_rows(changes.get("moved", []), mcols))
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
                fh.write("\n### Moved (reassigned in Guesty; old slot struck)\n"
                         + _md_table(changes.get("moved", []), mcols))
                if stats.get("cancel_guard_tripped"):
                    fh.write("\n> ⚠️ **Cancellation guard tripped:** "
                             + stats["cancel_guard_tripped"] + "\n")
                if changes["missing_city_properties"]:
                    fh.write("\n### Properties missing City (add to property_to_city.csv)\n")
                    for p in changes["missing_city_properties"][:50]:
                        fh.write(f"- {p}\n")
        except OSError:
            pass


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
