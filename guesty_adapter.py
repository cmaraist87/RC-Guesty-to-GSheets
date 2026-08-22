"""
Adapter: Guesty Open API reservation objects -> the two DataFrames that
`processing.process_reservations` already expects (check-out + check-in exports).

This is the ONLY piece that has to know Guesty's payload shape. The rest of the
pipeline (processing.py) stays untouched: it takes a check-out DataFrame and a
check-in DataFrame with the same column names the manual CSV exports used.

Column contract expected by processing.process_reservations:
    check-out df : LISTING, CHECK-OUT DATE, CHECK-OUT TIME, CONFIRMATION CODE, GUEST, LISTING'S CITY
    check-in  df : LISTING, CHECK-IN DATE,  CHECK-IN TIME,  CONFIRMATION CODE, GUEST, LISTING'S CITY

Where DATE is "YYYY-MM-DD" and TIME is "HH:MM AM/PM" (parse_dt in processing.py
accepts "%Y-%m-%d %I:%M %p").

FIELD_MAP below is intentionally easy to edit: run `sync.py --dry-run` once against
the live API to print a real reservation's keys, then confirm/adjust these paths.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Iterable

import pandas as pd

# --- Standard turnover times (used as a fallback when the API doesn't carry an
#     explicit per-reservation time). These match processing.py's own standards,
#     so a fallback produces NO false early/late (ECO/LCO/ECI/LCI) adjustment. ---
STD_CHECKOUT_TIME = "11:00 AM"
STD_CHECKIN_TIME = "04:00 PM"

# --- Dotted paths into a Guesty reservation object. Each entry is a list of
#     candidate paths tried in order; first non-empty wins. Adjust after a
#     --dry-run against the real payload. ---
FIELD_MAP: dict[str, list[str]] = {
    # Human-facing listing name; must resemble the old CSV "LISTING" values so
    # processing.normalize_property parses it the same way.
    "listing":     ["listing.nickname", "listing.title", "listingId.nickname", "listing.name"],
    "guest":       ["guest.fullName", "guest.firstName", "guestId.fullName"],
    "conf_code":   ["confirmationCode", "reservationId", "_id"],
    "city":        ["listing.address.city", "listingId.address.city", "listing.city"],
    # Localized (listing-timezone) check-in / check-out. May be date-only
    # ("2026-06-19") or a full local datetime; both are handled below.
    "checkin_dt":  ["checkInDateLocalized", "plannedArrivalLocalized", "checkIn"],
    "checkout_dt": ["checkOutDateLocalized", "plannedDepartureLocalized", "checkOut"],
    # Optional explicit time-of-day fields, if the account exposes them.
    "checkin_time":  ["plannedArrival", "checkInTime"],
    "checkout_time": ["plannedDeparture", "checkOutTime"],
}

CHECKOUT_COLUMNS = ["LISTING", "CHECK-OUT DATE", "CHECK-OUT TIME",
                    "CONFIRMATION CODE", "GUEST", "LISTING'S CITY"]
CHECKIN_COLUMNS = ["LISTING", "CHECK-IN DATE", "CHECK-IN TIME",
                   "CONFIRMATION CODE", "GUEST", "LISTING'S CITY"]


def _dig(obj: Any, dotted: str) -> Any:
    """Walk a dotted path through nested dicts/lists; return None if any hop misses."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def _first(obj: Any, paths: Iterable[str]) -> str:
    for p in paths:
        val = _dig(obj, p)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


# Accepted incoming datetime/date formats from Guesty localized fields.
_IN_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _parse_any(value: str) -> datetime | None:
    value = str(value).strip()
    if not value:
        return None
    for fmt in _IN_DT_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _split_date_time(dt_value: str, explicit_time: str, std_time: str) -> tuple[str, str]:
    """
    Return (date 'YYYY-MM-DD', time 'HH:MM AM/PM') for one side of a reservation.

    Priority for the time-of-day: an explicit time field > a time carried in the
    datetime value > the standard turnover time (which yields no adjustment).
    """
    dt = _parse_any(dt_value)
    if dt is None:
        return "", ""
    date_str = dt.strftime("%Y-%m-%d")

    # 1) explicit time field (may be "16:00", "4:00 PM", or a full ISO datetime)
    if explicit_time:
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p"):
            try:
                return date_str, datetime.strptime(explicit_time, fmt).strftime("%I:%M %p")
            except ValueError:
                pass
        edt = _parse_any(explicit_time)
        if edt is not None and (edt.hour or edt.minute):
            return date_str, edt.strftime("%I:%M %p")

    # 2) time carried inside the datetime value itself (non-midnight)
    if dt.hour or dt.minute:
        return date_str, dt.strftime("%I:%M %p")

    # 3) fallback to the standard time
    return date_str, std_time


def reservations_to_frames(reservations: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Convert a list of Guesty reservation dicts into (df_checkout, df_checkin)
    ready for processing.process_reservations.

    Each reservation contributes one check-out row (keyed by its check-out date)
    and one check-in row (keyed by its check-in date) -- mirroring the two Guesty
    CSV exports the manual process used.
    """
    co_rows, ci_rows = [], []
    for res in reservations:
        listing = _first(res, FIELD_MAP["listing"])
        guest = _first(res, FIELD_MAP["guest"])
        conf = _first(res, FIELD_MAP["conf_code"])
        city = _first(res, FIELD_MAP["city"])
        if not listing:
            continue  # can't place a reservation without a listing name

        co_date, co_time = _split_date_time(
            _first(res, FIELD_MAP["checkout_dt"]),
            _first(res, FIELD_MAP["checkout_time"]),
            STD_CHECKOUT_TIME,
        )
        ci_date, ci_time = _split_date_time(
            _first(res, FIELD_MAP["checkin_dt"]),
            _first(res, FIELD_MAP["checkin_time"]),
            STD_CHECKIN_TIME,
        )

        if co_date:
            co_rows.append({
                "LISTING": listing, "CHECK-OUT DATE": co_date, "CHECK-OUT TIME": co_time,
                "CONFIRMATION CODE": conf, "GUEST": guest, "LISTING'S CITY": city,
            })
        if ci_date:
            ci_rows.append({
                "LISTING": listing, "CHECK-IN DATE": ci_date, "CHECK-IN TIME": ci_time,
                "CONFIRMATION CODE": conf, "GUEST": guest, "LISTING'S CITY": city,
            })

    df_co = pd.DataFrame(co_rows, columns=CHECKOUT_COLUMNS)
    df_ci = pd.DataFrame(ci_rows, columns=CHECKIN_COLUMNS)
    return df_co, df_ci


# Minimal set of fields to request from the API (keeps payloads small/fast).
# Passed to GET /reservations as the `fields` query param (space-separated).
def requested_fields() -> str:
    """
    Ask for the FULL dotted paths, not the objects that contain them.

    Requesting the bare `listing` object looked equivalent -- dig into it locally --
    but Guesty returns a TRIMMED listing for that projection, and `address` is not
    in the trim. That silently cost every row its City: a live run measured 0 of
    3287 reservations arriving with one, which is why property_to_city.csv had
    grown to carry the whole portfolio instead of just the exceptions.

    DEFAULT IS `objects` because the dotted projection was tried against the live
    API on 2026-08-21 and the request failed outright (repeated non-200s, so every
    page retried and the run died). Guesty does not accept this `fields` form on
    this account. Losing the City is bad; failing the whole sync every morning is
    worse, so the known-good request is the default until the City gap is solved a
    different way -- most likely by reading listings from /listings separately.

    Set SYNC_FIELDS_MODE=paths to retry the dotted projection; the "City source
    check" line in the run log says whether it actually worked.
    """
    mode = os.environ.get("SYNC_FIELDS_MODE", "").strip().lower() or "objects"
    paths = set()
    for candidates in FIELD_MAP.values():
        for p in candidates:
            paths.add(p if mode == "paths" else p.split(".")[0])
    # Always include status/dates used for filtering/sorting.
    paths.update({"status", "checkIn", "checkOut", "confirmationCode"})
    return " ".join(sorted(paths))
