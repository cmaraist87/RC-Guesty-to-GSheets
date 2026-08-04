"""
Merge freshly-processed reservations into the current sheet -- a faithful port of
the logic in reservations.ipynb, extracted so both the notebook and the automated
sync can share it.

Given:
  candidates : DataFrame from processing.process_reservations (the 10 pipeline cols)
  sheet      : DataFrame of the CURRENT sheet (all string, .fillna("")), whatever
               column layout it has (checkbox/spacer columns included)
Returns:
  full  : the FULL corrected sheet, aligned to `sheet`'s exact columns --
          existing rows kept verbatim, superseded rows dropped, new/updated added,
          checkbox ticks preserved (carried over on updates, FALSE for new).
  stats : dict of counts (new / updated / unchanged / removed / missing_city).

Writing `full` starting at cell A1 (via paste or the Sheets API) preserves the
sheet's checkbox data-validation because only cell VALUES are replaced.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import pandas as pd

from processing import _canonical_key

SIGNATURE_COLS = {"Confirmation Code", "Guest", "Property", "Date"}


# ---- City resolution (exact -> canonical -> street level) --------------------
_unit_tok = re.compile(r"^(?:CH|[A-Za-z](?:-[A-Za-z])?|\d+|[0-9A-Za-z]*&[0-9A-Za-z&\-]*)$")


def _street_key(prop: str) -> str:
    toks = str(prop).strip().split()
    while len(toks) > 2 and _unit_tok.match(toks[-1]):
        toks.pop()
    return " ".join(toks).lower()


def build_city_resolver(sheet: pd.DataFrame, city_ref_csv: str = "property_to_city.csv"):
    exact, canon, street = {}, {}, {}

    def add(p, c):
        if str(c).strip():
            exact.setdefault(str(p).strip(), c)
            canon.setdefault(_canonical_key(p), c)
            street.setdefault(_street_key(p), c)

    if city_ref_csv and os.path.exists(city_ref_csv):
        ref = pd.read_csv(city_ref_csv, dtype=str).fillna("")
        for _, r in ref.iterrows():
            add(r["Property"], r["City"])
    if len(sheet) and {"Property", "City"} <= set(sheet.columns):
        for _, r in sheet.iterrows():
            add(r["Property"], r["City"])

    def resolve(p):
        return (exact.get(str(p).strip())
                or canon.get(_canonical_key(p))
                or street.get(_street_key(p), ""))

    return resolve


# ---- Normalisation helpers for signature comparison -------------------------
def _norm(c) -> str:
    return "".join(ch for ch in str(c).lower() if ch.isalnum())


def _norm_time(t) -> str:
    t = str(t).strip()
    if not t:
        return ""
    for fmt in ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(t, fmt).strftime("%I:%M %p")
        except ValueError:
            pass
    return t.upper().replace(" ", "")


def _norm_guest(g) -> str:
    return " ".join(str(g).split()).casefold()


def _date_key(v) -> str:
    return str(v).strip()[:10]


def merge_reservations_into_sheet(
    candidates: pd.DataFrame,
    sheet: pd.DataFrame,
    city_ref_csv: str = "property_to_city.csv",
) -> tuple[pd.DataFrame, dict]:
    candidates = candidates.copy()

    if sheet is None or not len(sheet):
        sheet = pd.DataFrame(columns=list(candidates.columns))
    else:
        # Safety guard: is this really the reservations layout?
        missing = SIGNATURE_COLS - set(sheet.columns)
        if missing:
            raise ValueError(
                f"Sheet doesn't look like the reservations layout "
                f"(missing columns: {sorted(missing)}). Columns found: {list(sheet.columns)}"
            )

    # Fill City
    resolve_city = build_city_resolver(sheet, city_ref_csv)
    candidates["City"] = [resolve_city(p) for p in candidates["Property"]]

    # Match the sheet's Date display format (append ' 00:00:00' if the sheet uses it)
    if len(sheet) and sheet["Date"].astype(str).str.contains(r"\d\d:\d\d").any():
        candidates["Date"] = candidates["Date"].map(lambda v: f"{_date_key(v)} 00:00:00")

    # Locate the sheet's check-out / check-in time columns (punctuation-insensitive)
    def sheet_col(pipeline_name):
        tgt = _norm(pipeline_name)
        return next((c for c in sheet.columns if _norm(c) == tgt), None)

    sheet_co_col = sheet_col("Check-out Time")
    sheet_ci_col = sheet_col("Check-in Time")

    def sig(row, co_col, ci_col):
        return (
            str(row.get("Confirmation Code", "")).strip().upper(),
            _norm_guest(row.get("Guest", "")),
            _norm_time(row.get(co_col, "")) if co_col else "",
            _norm_time(row.get(ci_col, "")) if ci_col else "",
        )

    existing_by_key: dict = {}
    for i, r in sheet.iterrows():
        key = (str(r["Property"]).strip(), _date_key(r["Date"]))
        existing_by_key.setdefault(key, []).append({
            "row": i + 2,  # Google Sheet row number (header is row 1)
            "sig": sig(r, sheet_co_col, sheet_ci_col),
            "data": r,
        })

    keep_idx, carry_rows, delete_rows = [], [], []
    n_new = n_updated = n_unchanged = 0
    for j, c in candidates.iterrows():
        key = (str(c["Property"]).strip(), _date_key(c["Date"]))
        matches = existing_by_key.get(key)
        if not matches:
            keep_idx.append(j); carry_rows.append(None); n_new += 1; continue
        csig = sig(c, "Check-out Time", "Check-in Time")
        if any(m["sig"] == csig for m in matches):
            n_unchanged += 1; continue
        keep_idx.append(j); carry_rows.append(matches[0]["data"]); n_updated += 1
        delete_rows.extend(matches)

    new_rows = candidates.loc[keep_idx].reset_index(drop=True)

    # Detect checkbox columns (values are TRUE/FALSE)
    def is_checkbox(col):
        v = sheet[col].astype(str).str.strip()
        v = v[v != ""]
        return len(v) > 0 and v.str.upper().isin(["TRUE", "FALSE"]).mean() >= 0.8

    checkbox_cols = [c for c in sheet.columns if is_checkbox(c)] if len(sheet) else []

    # Build new/updated rows aligned to the sheet's exact columns
    pipe_by_norm = {_norm(c): c for c in new_rows.columns}
    target_cols = list(sheet.columns) if len(sheet.columns) else list(candidates.columns)
    to_append = pd.DataFrame("", index=range(len(new_rows)), columns=target_cols)
    for col in target_cols:
        src = pipe_by_norm.get(_norm(col))
        if src is not None:
            to_append[col] = new_rows[src].values
        elif col in checkbox_cols:
            vals = []
            for carry in carry_rows:
                old = str(carry[col]).strip() if (carry is not None and col in carry.index) else ""
                vals.append(old if old else "FALSE")
            to_append[col] = vals

    # Full corrected sheet = kept existing (minus superseded + empty filler) + new/updated
    delete_row_nums = {m["row"] for m in delete_rows}
    if len(sheet):
        not_empty = ~((sheet["Date"].astype(str).str.strip() == "")
                      & (sheet["Property"].astype(str).str.strip() == ""))
        not_deleted = pd.Series([(i + 2) not in delete_row_nums for i in range(len(sheet))],
                                index=sheet.index)
        kept_existing = sheet[not_empty & not_deleted]
    else:
        kept_existing = sheet

    full = (pd.concat([kept_existing, to_append], ignore_index=True)
            if len(kept_existing) else to_append.copy())
    full = full.reindex(columns=target_cols).fillna("")

    missing_city = int(
        (to_append.get("City", pd.Series(dtype=str)).astype(str).str.strip() == "").sum()
    )
    stats = {
        "existing_rows": len(sheet),
        "new": n_new,
        "updated": n_updated,
        "unchanged": n_unchanged,
        "removed": len(delete_rows),
        "missing_city": missing_city,
        "total_rows": len(full),
    }
    return full, stats
