"""
Merge freshly-processed reservations into the current sheet -- a faithful port of
the logic in reservations.ipynb, extracted so both the notebook and the automated
sync can share it.

Given:
  candidates : DataFrame from processing.process_reservations (the 10 pipeline cols)
  sheet      : DataFrame of the CURRENT sheet (all string, .fillna("")), whatever
               column layout it has (checkbox/spacer columns included)
Returns:
  full    : the FULL corrected sheet, aligned to `sheet`'s exact columns --
            existing rows kept verbatim, superseded rows dropped, new/updated added,
            checkbox ticks preserved (carried over on updates, FALSE for new).
  stats   : dict of counts (new / updated / unchanged / removed / cancelled / missing_city).
  changes : the records behind those counts, plus the two lists the caller needs to
            paint the sheet -- `row_flags` (one flag per row of `full`) and
            `kept_positions` (where each carried-over row sat in the old sheet).

Row flags drive the visual diff the ops team asked for:
    "new" / "updated"  -> highlighted (this run wrote the row)
    "cancelled"        -> struck through for the first time this run
    "moved"            -> same, but the reservation reappeared elsewhere in the month
    "struck"           -> already struck by an earlier run; leave as is
    ""                 -> untouched

IMPORTANT: `sheet` must keep its real header even when it has no data rows (a
freshly auto-created month tab). Substituting the pipeline's own column names
there is what shifted every column from `assigned` onward by one.

Writing `full` starting at cell A1 (via paste or the Sheets API) preserves the
sheet's checkbox data-validation because only cell VALUES are replaced.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime

import pandas as pd

from processing import _canonical_key

SIGNATURE_COLS = {"Confirmation Code", "Guest", "Property", "Date"}


class ShiftedLayoutError(ValueError):
    """The tab's data sits one column left of its header (pre-fix write).

    A distinct type so the caller can offer to repair the tab instead of only
    reporting it -- every other layout complaint stays a plain ValueError.
    """

# Headers of the sheet's checkbox columns. Used only when the tab has no data rows
# to inspect (an auto-created month tab), so TRUE/FALSE detection has nothing to go
# on. "." / "FALSE" are how older sheet exports named the same four columns.
CHECKBOX_HEADER_NAMES = {"assigned", "verified", "out", "in", "true", "false"}


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


def _nearest_destination(dests: list[tuple[str, str]], from_day: str) -> tuple[str, str]:
    """Of the (Property, Date) slots a confirmation code now occupies, the one closest
    in time to the row being struck -- that's the slot it most plausibly moved to.

    A stay contributes both a check-out and a check-in row, so a code routinely has
    two or more destinations; reporting the nearest keeps the operator's eye on the
    one that replaced this row.
    """
    try:
        base = date.fromisoformat(from_day)
    except ValueError:
        return dests[0]

    def gap(dest: tuple[str, str]) -> int:
        try:
            return abs((date.fromisoformat(dest[1]) - base).days)
        except ValueError:
            return 10 ** 6

    return min(dests, key=gap)


def _carry_checkbox(carry, col) -> str:
    """The tick a re-written row inherits, ORed across every existing row it replaces.

    One (Property, Date) slot can hold duplicate rows -- Agosto 2026 had ~75 -- and
    all of them are dropped in favour of the single new row. Carrying only the first
    would silently discard a tick the team had set on a later copy, so a TRUE
    anywhere in the group wins. `carry` is None for a genuinely new row.
    """
    if carry is None:
        return "FALSE"
    seen = ""
    for row in carry:
        if col not in row.index:
            continue
        v = str(row[col]).strip()
        if v.upper() == "TRUE":
            return "TRUE"
        if v:
            seen = v  # keep a non-blank non-TRUE value (FALSE, or free text)
    return seen or "FALSE"


def _carry_operator(carry, col) -> str:
    """The value an OPERATOR column keeps when its row is rewritten.

    A column the sync does not own -- Shift ID, notes, anything added alongside the
    pipeline's own -- used to be blanked whenever the merge rebuilt a row as
    "updated", because the builder started every cell empty and only filled the
    columns it recognised. That silently severed such a column from its row on the
    next nightly run, and only for rows that happened to change.

    Ownership is decided by exclusion, deliberately: a column the pipeline writes is
    pipeline-owned, a column carrying checkbox validation is a tick, and ANYTHING
    ELSE belongs to whoever put it there. That way a new operator column works the
    day it is added, with no list here to keep in step.

    Like the checkbox carry, the first non-empty value across every superseded
    duplicate wins, so a value set on a later copy is not lost. Empty for a
    genuinely new row.
    """
    if carry is None:
        return ""
    for row in carry:
        if col in row.index:
            v = str(row[col]).strip()
            if v:
                return v
    return ""


def norm_city(v) -> str:
    """Canonical form for comparing city names.

    Case, punctuation and spacing drift between Guesty and the sheet ("Bay St. Louis"
    / "Bay St Louis" / "bay saint louis"), and a city that fails to match silently
    drops a whole market's rows, so the comparison is deliberately forgiving.
    """
    s = str(v).strip().casefold().replace(".", " ")
    s = " ".join(s.split())
    return s.replace("saint ", "st ")


def _is_checkbox_header(col) -> bool:
    """Does this column header name one of the sheet's checkbox columns?"""
    raw = str(col).strip()
    if raw == ".":  # older exports named the 'assigned' column "."
        return True
    # read_as_dataframe de-duplicates repeated headers as FALSE, FALSE.1, FALSE.2
    base = raw.split(".")[0] if raw.upper().startswith(("TRUE", "FALSE")) else raw
    return _norm(base) in CHECKBOX_HEADER_NAMES


_TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*[AP]M$", re.I)


def _reject_shifted_layout(sheet: pd.DataFrame) -> None:
    """
    Refuse to merge into a tab whose columns are off by one.

    Tabs written before the empty-tab fix got the pipeline's 10 columns under the
    sheet's wider header, so check-out times ended up under `Property`. Merging into
    one of those would double every row instead of correcting it.
    """
    if not len(sheet) or "Property" not in sheet.columns:
        return
    prop = sheet["Property"].astype(str).str.strip()
    prop = prop[prop != ""]
    if len(prop) and prop.map(lambda v: bool(_TIME_RE.match(v))).mean() > 0.5:
        raise ShiftedLayoutError(
            "the Property column holds times, not addresses -- this tab was written "
            "with the old shifted layout. Clear its data rows (row 2 downwards) and "
            "re-run; the sync will repopulate the month correctly."
        )


def merge_reservations_into_sheet(
    candidates: pd.DataFrame,
    sheet: pd.DataFrame,
    city_ref_csv: str = "property_to_city.csv",
    cancel_window: tuple[str, str] | None = None,
    struck_rows: set | frozenset = frozenset(),
    cancel_guard: tuple[float, int] = (0.5, 10),
    validated_checkboxes: frozenset[str] | None = None,
    allowed_cities: frozenset[str] | None = None,
    out_of_scope_properties: dict | None = None,
) -> tuple[pd.DataFrame, dict, dict]:
    """
    cancel_window : (start_iso, end_iso) -- the date range the Guesty fetch fully
        covers. An existing sheet row dated inside it whose (Property, Date) no
        longer appears in `candidates` is treated as CANCELLED: kept in place and
        flagged for strikethrough rather than silently left behind. Pass None to
        disable cancellation detection entirely.

        Such a row is reported as MOVED, not cancelled, when its confirmation code
        turns up elsewhere in `candidates` -- Guesty reassigned the booking to
        another listing or date. The sheet treatment is identical (old slot struck,
        new slot appended); only the label and the guard maths differ, because a
        move proves the fetch carried that reservation.
    struck_rows : 0-based positions of `sheet` rows an earlier run already struck
        through. They are excluded from matching (a re-booking must land as a new
        row) and are not re-reported as cancellations.
    cancel_guard : (max_ratio, min_count) circuit-breaker. If a single run would
        cancel more than `max_ratio` of the tab's in-window rows AND more than
        `min_count` of them, the whole month almost certainly went missing because
        the fetch came back short -- not because guests cancelled overnight. No
        rows are struck and `stats["cancel_guard_tripped"]` is set instead.
    validated_checkboxes : names of the columns the TAB ITSELF reports as carrying
        checkbox data-validation (see sheets_client.read_checkbox_columns). When
        given, these are the checkbox columns -- no guessing from header names or
        from the TRUE/FALSE values that happen to be present. Pass None (or an empty
        set, which is what an unreadable tab yields) to fall back to that heuristic.
    allowed_cities : the cities this sheet covers. Existing rows for any OTHER city
        are left alone rather than struck -- they produced no candidate because the
        market is out of scope, not because a guest cancelled -- and are reported
        under `changes["out_of_scope"]` for deliberate deletion. Candidates are
        expected to be filtered upstream; this only governs the existing rows.
    out_of_scope_properties : {canonical property key -> the city that disqualified
        it}, from the city filter that ran over THIS fetch. Authoritative, because
        the rows that need this most have a blank City in the sheet and no entry in
        property_to_city.csv -- both of the other signals are silent on exactly the
        rows that would otherwise be mislabelled as cancellations.
    """
    candidates = candidates.copy()

    # A tab with a header but no data rows (freshly auto-created month) still
    # defines the column layout -- only fall back to the pipeline's own columns
    # when there is genuinely no header to align to.
    if sheet is None or not len(sheet.columns):
        sheet = pd.DataFrame(columns=list(candidates.columns))
    else:
        # Safety guard: is this really the reservations layout?
        missing = SIGNATURE_COLS - set(sheet.columns)
        if missing:
            raise ValueError(
                f"Sheet doesn't look like the reservations layout "
                f"(missing columns: {sorted(missing)}). Columns found: {list(sheet.columns)}"
            )
    sheet = sheet.reset_index(drop=True)  # positions must be 0..n-1 for row maths
    _reject_shifted_layout(sheet)

    # Fill City -- FILL, not overwrite. The pipeline may already have resolved it
    # upstream (Guesty's listing.address.city, which is authoritative and covers
    # properties no CSV knows about); replacing that with a CSV lookup threw the
    # good value away and reported the property as missing a city.
    resolve_city = build_city_resolver(sheet, city_ref_csv)
    have = (candidates["City"].astype(str) if "City" in candidates.columns
            else pd.Series([""] * len(candidates), index=candidates.index))
    candidates["City"] = [c.strip() or resolve_city(p)
                          for c, p in zip(have, candidates["Property"])]

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
        if i in struck_rows:
            continue  # already cancelled: must not match, so a re-booking reads as new
        key = (str(r["Property"]).strip(), _date_key(r["Date"]))
        existing_by_key.setdefault(key, []).append({
            "row": i + 2,  # Google Sheet row number (header is row 1)
            "sig": sig(r, sheet_co_col, sheet_ci_col),
            "data": r,
        })

    def _rec(row) -> dict:
        return {
            "Date": _date_key(row.get("Date", "")),
            "Property": str(row.get("Property", "")).strip(),
            "Guest": str(row.get("Guest", "")).strip(),
            "Confirmation Code": str(row.get("Confirmation Code", "")).strip(),
            "T/O": str(row.get("T/O", "")).strip(),
        }

    keep_idx, carry_rows, delete_rows, append_flags = [], [], [], []
    new_records, updated_records = [], []
    n_new = n_updated = n_unchanged = 0
    live_keys, live_canon = set(), set()
    live_by_code: dict[str, list[tuple[str, str]]] = {}
    for j, c in candidates.iterrows():
        prop, day = str(c["Property"]).strip(), _date_key(c["Date"])
        key = (prop, day)
        live_keys.add(key)
        # Cosmetic spelling drift ("520 E Harris&CH" vs "520 E Harris CH") must not
        # read as a cancellation, so keep a canonical index alongside the exact one.
        live_canon.add((_canonical_key(prop), day))
        # Where each confirmation code lives now -- used to tell a reassignment
        # apart from a real cancellation.
        code = str(c.get("Confirmation Code", "")).strip().upper()
        if code:
            live_by_code.setdefault(code, []).append(key)
        matches = existing_by_key.get(key)
        if not matches:
            keep_idx.append(j); carry_rows.append(None); append_flags.append("new")
            n_new += 1
            new_records.append(_rec(c)); continue
        csig = sig(c, "Check-out Time", "Check-in Time")
        if any(m["sig"] == csig for m in matches):
            n_unchanged += 1; continue
        # EVERY match is dropped in favour of this one row (see delete_rows below),
        # so the ticks of all of them have to be carried, not just the first one's.
        keep_idx.append(j); carry_rows.append([m["data"] for m in matches])
        append_flags.append("updated"); n_updated += 1
        updated_records.append(_rec(c))
        delete_rows.extend(matches)

    new_rows = candidates.loc[keep_idx].reset_index(drop=True)

    # --- Cancellations -------------------------------------------------------
    # A row still sitting in the sheet, dated inside the window the Guesty fetch
    # fully covers, whose (Property, Date) produced no candidate at all. The
    # reservation was cancelled (or the listing was excluded) upstream. Rows
    # superseded above are NOT cancellations -- their key is in `live_keys`.
    # `moved_pos` is a subset of `cancelled_pos`: same strikethrough, different label.
    cancelled_pos: set[int] = set()
    moved_pos: set[int] = set()
    out_of_scope_pos: set[int] = set()
    out_of_scope_city: dict[int, str] = {}   # the city that put each row out of scope
    guard_tripped = ""
    allowed = frozenset(norm_city(c) for c in (allowed_cities or ()))
    has_city = "City" in sheet.columns
    if cancel_window and len(sheet):
        lo, hi = cancel_window
        in_window = 0
        for i, r in sheet.iterrows():
            if i in struck_rows:
                continue
            prop, d = str(r["Property"]).strip(), _date_key(r["Date"])
            if not prop or not (lo <= d <= hi):
                continue
            # A row for a city this sheet no longer covers produced no candidate by
            # definition. Striking it would label an out-of-scope property as a
            # CANCELLED booking, which is simply untrue -- and 70-odd such rows would
            # trip the short-fetch guard and suppress the real cancellations too.
            # Report them separately so they can be deleted deliberately.
            #
            # A BLANK City means "unknown", never "out of scope": plenty of older
            # rows predate the City column being filled, and treating them as foreign
            # quietly excused real New Orleans rows from cancellation detection. Fall
            # back to the resolver, and if the city still cannot be established treat
            # the row as in scope so it goes through the normal checks.
            # Strongest evidence first: THIS run's fetch saw this property and
            # dropped it for its city. The sheet's own City cell is often blank on
            # exactly these rows -- they were written before the City lookup worked
            # -- and property_to_city.csv has never heard of them either, so relying
            # on either would let a Boston row be struck as a New Orleans
            # cancellation. That is what happened: 157 "cancellations" where 37 were
            # real, the rest simply not this sheet's work.
            dropped_city = (out_of_scope_properties or {}).get(_canonical_key(prop))
            if dropped_city:
                out_of_scope_pos.add(i)
                out_of_scope_city[i] = dropped_city
                continue

            row_city = str(r["City"]).strip() if has_city else ""
            if not row_city:
                row_city = resolve_city(prop)
            if allowed and row_city and norm_city(row_city) not in allowed:
                out_of_scope_pos.add(i)
                out_of_scope_city[i] = row_city
                continue
            in_window += 1
            if (prop, d) not in live_keys and (_canonical_key(prop), d) not in live_canon:
                cancelled_pos.add(i)
                if str(r["Confirmation Code"]).strip().upper() in live_by_code:
                    moved_pos.add(i)

        # The guard exists to catch a SHORT FETCH, where reservations vanish from the
        # payload entirely. A moved row proves its reservation did arrive, so it must
        # not inflate the ratio -- and it stays struck even if the guard trips.
        gone = cancelled_pos - moved_pos
        max_ratio, min_count = cancel_guard
        if len(gone) > min_count and in_window and len(gone) / in_window > max_ratio:
            guard_tripped = (
                f"{len(gone)} of {in_window} in-window rows would be struck "
                f"(> {max_ratio:.0%}); treating this as a short fetch, not a mass "
                f"cancellation. Nothing struck on this tab"
                + (f" beyond {len(moved_pos)} confirmed reassignment(s)."
                   if moved_pos else ".")
            )
            cancelled_pos = set(moved_pos)

    # Build new/updated rows aligned to the sheet's exact columns
    pipe_by_norm = {_norm(c): c for c in new_rows.columns}
    target_cols = list(sheet.columns) if len(sheet.columns) else list(candidates.columns)

    # Checkbox columns: TRUE/FALSE in the data where there is data, else by header
    # name (an auto-created tab has no data rows to sample).
    def is_checkbox(col):
        if pipe_by_norm.get(_norm(col)) is not None:
            return False  # a real data column, never a checkbox
        if validated_checkboxes:
            # The tab told us which columns carry checkbox validation. Trust it over
            # any guess -- it is the same fact, read instead of inferred.
            return col in validated_checkboxes
        v = sheet[col].astype(str).str.strip() if len(sheet) else pd.Series(dtype=str)
        v = v[v != ""]
        if len(v):
            return bool(v.str.upper().isin(["TRUE", "FALSE"]).mean() >= 0.8)
        return _is_checkbox_header(col)

    checkbox_cols = [c for c in target_cols if is_checkbox(c)]

    to_append = pd.DataFrame("", index=range(len(new_rows)), columns=target_cols)
    for col in target_cols:
        src = pipe_by_norm.get(_norm(col))
        if src is not None:
            to_append[col] = new_rows[src].values
        elif col in checkbox_cols:
            to_append[col] = [_carry_checkbox(carry, col) for carry in carry_rows]
        else:
            # Not written by the pipeline and not a tick -> an operator column.
            # Carry it, never blank it (see _carry_operator).
            to_append[col] = [_carry_operator(carry, col) for carry in carry_rows]

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

    # Where each carried-over row sat in the old sheet -- lets the writer tell an
    # already-highlighted row from one it must newly paint.
    kept_positions = [int(i) for i in kept_existing.index]
    row_flags = ["moved" if p in moved_pos
                 else "cancelled" if p in cancelled_pos
                 else "struck" if p in struck_rows
                 else ""
                 for p in kept_positions] + append_flags

    full = (pd.concat([kept_existing, to_append], ignore_index=True)
            if len(kept_existing) else to_append.copy())
    full = full.reindex(columns=target_cols).fillna("")
    assert len(row_flags) == len(full), (len(row_flags), len(full))

    missing_city = int(
        (to_append.get("City", pd.Series(dtype=str)).astype(str).str.strip() == "").sum()
    )
    removed_records = [{
        "Date": _date_key(m["data"].get("Date", "")),
        "Property": str(m["data"].get("Property", "")).strip(),
        "Guest": str(m["data"].get("Guest", "")).strip(),
        "Confirmation Code": str(m["data"].get("Confirmation Code", "")).strip(),
        "sheet_row": m["row"],
    } for m in delete_rows]

    # Which new/updated rows still have no City (property not in property_to_city.csv
    # / the existing sheet). Only rows whose City is actually blank.
    missing_city_props = sorted({
        str(to_append.iloc[i].get("Property", "")).strip()
        for i in range(len(to_append))
        if "City" in to_append.columns
        and str(to_append.iloc[i].get("City", "")).strip() == ""
        and str(to_append.iloc[i].get("Property", "")).strip() != ""
    })

    def _struck_rec(i: int) -> dict:
        return {
            "Date": _date_key(sheet.at[i, "Date"]),
            "Property": str(sheet.at[i, "Property"]).strip(),
            "Guest": str(sheet.at[i, "Guest"]).strip(),
            "Confirmation Code": str(sheet.at[i, "Confirmation Code"]).strip(),
            "sheet_row": i + 2,
        }

    cancelled_records = [_struck_rec(i) for i in sorted(cancelled_pos - moved_pos)]
    out_of_scope_records = []
    for i in sorted(out_of_scope_pos):
        rec = _struck_rec(i)
        # The resolved city, not the raw cell -- the cell is often blank, which is
        # exactly why the row needed resolving in the first place.
        rec["City"] = out_of_scope_city.get(i, "")
        out_of_scope_records.append(rec)
    moved_records = []
    for i in sorted(moved_pos):
        rec = _struck_rec(i)
        prop, day = _nearest_destination(
            live_by_code[rec["Confirmation Code"].upper()], rec["Date"])
        rec["Now at"] = f"{prop}  {day}"
        moved_records.append(rec)

    stats = {
        "existing_rows": len(sheet),
        "new": n_new,
        "updated": n_updated,
        "unchanged": n_unchanged,
        "removed": len(delete_rows),
        "cancelled": len(cancelled_pos) - len(moved_pos),
        "moved": len(moved_pos),
        "out_of_scope": len(out_of_scope_pos),
        "cancel_guard_tripped": guard_tripped,
        "missing_city": missing_city,
        "total_rows": len(full),
    }
    changes = {
        "new": new_records,
        "updated": updated_records,
        "removed": removed_records,
        "cancelled": cancelled_records,
        "moved": moved_records,
        "out_of_scope": out_of_scope_records,
        "missing_city_properties": missing_city_props,
        # Painting instructions for the writer (see the module docstring).
        "row_flags": row_flags,
        "kept_positions": kept_positions,
    }
    return full, stats, changes
