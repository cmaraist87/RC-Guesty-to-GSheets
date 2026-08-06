"""
Google Sheets I/O via a service account.

Read the current sheet into a DataFrame (with pandas-style de-duplicated headers so
the merge logic works), then write the full corrected sheet back starting at A1.

Writing only cell VALUES (not formatting) preserves the sheet's checkbox
data-validation -- exactly like the manual "paste over A1" step. Trailing rows that
are no longer used are value-cleared (checkbox formatting stays), which also removes
any superseded rows the manual paste would have left behind.

Credentials: pass the service-account key either as a file path or as the raw JSON
string (GitHub Actions stores it as a secret) via GOOGLE_SA_JSON.
"""
from __future__ import annotations

import json
import os

import pandas as pd

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _load_credentials(sa_json: str):
    from google.oauth2.service_account import Credentials

    sa_json = (sa_json or "").strip()
    if not sa_json:
        raise RuntimeError("Missing GOOGLE_SA_JSON (service-account key path or JSON).")
    if sa_json.startswith("{"):
        info = json.loads(sa_json)
    elif os.path.exists(sa_json):
        with open(sa_json, encoding="utf-8") as fh:
            info = json.load(fh)
    else:
        raise RuntimeError("GOOGLE_SA_JSON is neither valid JSON nor an existing file path.")
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def open_spreadsheet(sheet_id: str, sa_json: str):
    """Open the spreadsheet (workbook) by ID."""
    import gspread

    if not sheet_id:
        raise RuntimeError("Missing SHEET_ID (the spreadsheet's ID from its URL).")
    gc = gspread.authorize(_load_credentials(sa_json))
    return gc.open_by_key(sheet_id)


def get_worksheet(sheet_id: str, worksheet_name: str | None, sa_json: str):
    """Open a worksheet by spreadsheet ID and tab name (or first tab if None)."""
    ss = open_spreadsheet(sheet_id, sa_json)
    return ss.worksheet(worksheet_name) if worksheet_name else ss.sheet1


# Spanish month names -> month number (the schedule tabs are named e.g. "Julio 2026").
# Includes English names too, so mixed naming still resolves.
_MONTH_NAMES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_month_title(title: str) -> tuple[int, int] | None:
    """'Julio 2026' -> (2026, 7). Returns None for non-month tabs (guesty_res, pivots)."""
    import re

    m = re.match(r"^\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\.?\s+(\d{4})\s*$", str(title).strip())
    if not m:
        return None
    mon = _MONTH_NAMES.get(m.group(1).lower())
    if not mon:
        return None
    return (int(m.group(2)), mon)


def month_worksheets(ss) -> dict:
    """Map (year, month) -> worksheet for every month-named tab in the workbook."""
    out = {}
    for ws in ss.worksheets():
        ym = parse_month_title(ws.title)
        if ym:
            out[ym] = ws
    return out


def _dedupe_headers(header: list[str]) -> list[str]:
    """Mimic pandas read_csv: blanks -> 'Unnamed: i', duplicates -> name, name.1, ..."""
    out, seen = [], {}
    for i, h in enumerate(header):
        name = str(h).strip()
        if name == "":
            name = f"Unnamed: {i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def read_as_dataframe(ws) -> tuple[pd.DataFrame, list[str]]:
    """Return (df with de-duplicated string columns, original raw header row)."""
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(), []
    header_raw = values[0]
    cols = _dedupe_headers(header_raw)
    rows = values[1:]
    # pad/truncate each row to header width
    width = len(cols)
    norm_rows = [(r + [""] * width)[:width] for r in rows]
    df = pd.DataFrame(norm_rows, columns=cols).fillna("").astype(str)
    return df, header_raw


def write_dataframe(ws, full: pd.DataFrame, header_raw: list[str]) -> None:
    """
    Write `full` starting at A1, keeping the sheet's ORIGINAL header row intact and
    value-clearing any rows below the new data (checkbox formatting preserved).
    """
    prev_row_count = len(ws.get_all_values())

    body = full.astype(str).values.tolist()
    matrix = [list(header_raw)] + body
    new_row_count = len(matrix)

    ws.update(
        range_name="A1",
        values=matrix,
        value_input_option="USER_ENTERED",  # so TRUE/FALSE become checkboxes, dates parse
    )

    # Value-clear leftover rows below the freshly written block (keeps formatting).
    if prev_row_count > new_row_count:
        n_cols = max(len(header_raw), full.shape[1])
        last_col = _col_letter(n_cols)
        ws.batch_clear([f"A{new_row_count + 1}:{last_col}{prev_row_count}"])


def _col_letter(n: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
