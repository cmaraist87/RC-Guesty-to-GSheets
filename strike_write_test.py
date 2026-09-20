"""Does a strikethrough request land on this spreadsheet at all?

    python strike_write_test.py --tab "Octubre 2026" --confirm

WRITES, but only to BLANK rows far below the data, and clears them again.

Three runs have now sent repeatCell strikethrough for the same rows and had no
effect. Every request was answered (529 sent, 529 replies), no error was raised,
the tab has no conditional formats, merges or protected ranges, userEnteredFormat
matches effectiveFormat, and an independent probe minutes later agrees with the
read taken inside the run. A repair batch of 31 requests changed nothing.

So stop testing it through the sync. One request, one blank row, read it back.
Then the same with 10, 50, 200 and 450 rows at once, because a per-batch limit is
the only shape left that fits: the tabs that fail are the ones sending hundreds of
requests, and the tabs that send a handful never miss.

SAFETY: every target row is verified blank and below the last data row before
anything is written, and every mark is cleared in a finally block. Nothing the
team looks at is touched.
"""
from __future__ import annotations

import argparse
import sys

from sheets_client import (_col_letter, _fmt_request, month_worksheets,
                           open_spreadsheet, read_as_dataframe)
from sync import load_config

BATCHES = (1, 10, 50, 200, 450)


def strikes_in(ws, first_row: int, last_row: int, n_cols: int) -> set[int]:
    """1-based grid rows carrying a strikethrough in column A, over a range."""
    params = {
        "includeGridData": "true",
        "ranges": [f"'{ws.title}'!A{first_row}:{_col_letter(n_cols)}{last_row}"],
        "fields": "sheets(data(rowData(values(userEnteredFormat/textFormat/strikethrough))))",
    }
    meta = ws.spreadsheet.fetch_sheet_metadata(params)
    data = (((meta.get("sheets") or [{}])[0].get("data") or [{}])[0])
    out = set()
    for i, rd in enumerate(data.get("rowData") or []):
        vals = rd.get("values") or []
        if vals and ((vals[0].get("userEnteredFormat") or {})
                     .get("textFormat") or {}).get("strikethrough"):
            out.add(first_row + i)
    return out


def paint(ws, rows_1based, on: bool, n_cols: int) -> int:
    """One repeatCell PER ROW -- deliberately not merged into runs, because the
    question is how many requests a batch will honour, not how few we can send."""
    reqs = [_fmt_request(ws, r - 1, r, n_cols,
                         {"textFormat": {"strikethrough": on}},
                         "userEnteredFormat.textFormat.strikethrough")
            for r in rows_1based]
    resp = ws.spreadsheet.batch_update({"requests": reqs})
    return len((resp or {}).get("replies") or [])


def _sequence(ws, first: int, n_cols: int, already: set) -> int:
    """The sync's own order: write the values, then paint the marks.

    Painting 450 blank rows in one batch worked perfectly, so the API is not
    dropping requests and there is no batch-size limit in play. The only thing
    left that separates a blank scratch row from a row the sync fails on is that
    the sync writes VALUES to it moments earlier, with ws.update on A1 -- so do
    exactly that here, on rows nobody looks at, and see whether the paint that
    follows still lands.
    """
    size = 200
    targets = list(range(first, first + size))
    last = first + size - 1
    rng = f"A{first}:{_col_letter(n_cols)}{last}"
    body = [[f"seq-test {r}"] + [""] * (n_cols - 1) for r in targets]
    print(f"
SEQUENCE test: writing values to {rng}, then painting {size} rows")
    try:
        ws.update(range_name=rng, values=body, value_input_option="USER_ENTERED")
        replies = paint(ws, targets, True, n_cols)
        got = strikes_in(ws, first, last, n_cols) - already
        took = len(got & set(targets))
        print(f"  values then paint: {replies} replies, {took} of {size} landed   "
              + ("all landed" if took == size else f"*** {size - took} LOST ***"))
        if took < size:
            print(f"      first rows that did not take: "
                  f"{sorted(set(targets) - got)[:12]}")
    finally:
        paint(ws, targets, False, n_cols)
        ws.batch_clear([rng])
        left = strikes_in(ws, first, last, n_cols) - already
        print(f"  cleaned up: {len(left)} mark(s) and the values removed.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="Octubre 2026")
    ap.add_argument("--confirm", action="store_true",
                    help="required; without it nothing is written")
    ap.add_argument("--sequence", action="store_true",
                    help="write VALUES to the band first, the way the sync does, "
                         "then paint -- same rows, same order, same call shapes")
    args = ap.parse_args(argv)

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = next((w for w in month_worksheets(ss).values() if w.title == args.tab), None)
    if ws is None:
        print(f"no tab named {args.tab!r}")
        return 2

    rows, header = read_as_dataframe(ws)
    n_cols = max(len(header), 1)
    n_data = len(rows)
    first = n_data + 200                      # 1-based, well clear of the data
    last = min(ws.row_count, first + max(BATCHES))
    room = last - first
    print(f"{ws.title}: {n_data} data rows, grid {ws.row_count} x {ws.col_count}")
    print(f"scratch band: rows {first}..{last} ({room} blank rows below the data)")
    if room < max(BATCHES):
        print(f"ERROR: need {max(BATCHES)} blank rows, have {room}", file=sys.stderr)
        return 2

    # Refuse outright if anything down there is not blank.
    existing = ws.get(f"A{first}:{_col_letter(n_cols)}{last}")
    if any(any(str(c).strip() for c in r) for r in (existing or [])):
        print("ERROR: the scratch band is not empty; refusing to touch it",
              file=sys.stderr)
        return 2
    already = strikes_in(ws, first, last, n_cols)
    print(f"strikethroughs already in the band: {len(already)}")
    if not args.confirm:
        print("\n--confirm not given; nothing was written.")
        return 0

    if args.sequence:
        return _sequence(ws, first, n_cols, already)

    painted: list[int] = []
    try:
        for size in BATCHES:
            targets = list(range(first, first + size))
            painted = targets
            replies = paint(ws, targets, True, n_cols)
            got = strikes_in(ws, first, last, n_cols) - already
            took = len(got & set(targets))
            verdict = "all landed" if took == size else f"*** {size - took} LOST ***"
            print(f"  batch of {size:>4}: {replies:>4} replies, "
                  f"{took:>4} of {size} landed   {verdict}")
            if took < size:
                missed = sorted(set(targets) - got)
                print(f"      first rows that did not take: {missed[:12]}")
            paint(ws, targets, False, n_cols)
            painted = []
    finally:
        if painted:
            print("  clearing up after an error...")
            paint(ws, painted, False, n_cols)
        left = strikes_in(ws, first, last, n_cols) - already
        print(f"\nscratch band left with {len(left)} of our marks "
              f"(0 means fully cleaned up).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
