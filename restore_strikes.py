"""Put back the lines repair_strikes removed from rows it had no evidence about.

    python restore_strikes.py --tab "Septiembre 2026" --codes A,B,C   # look only
    python restore_strikes.py --tab "Septiembre 2026" --codes A,B,C --confirm

WRITES with --confirm, and only the strikethrough, only on rows whose
confirmation code is in --codes AND which Guesty reports as cancelled.

WHY
---
The sync fetches checkOut >= yesterday. SYNC_REPAIR_STRIKES re-derives every
cancellation from that fetch, so a booking whose checkout is older is not in the
data at all -- and the repair reads "absent from the fetch" as "not cancelled"
and lifts the line. On 2026-09-20 that stripped 24 Septiembre rows dated 09-01 to
09-18, among them Ryan Gentry HMQ4JEA5CQ, which Guesty reports as canceled on
2026-09-09.

The old sheet state is not the authority here: those rows sat behind a filter for
an unknown period, and a filter-hidden row could not be lifted either, so some of
those lines may themselves have been stale. So ask Guesty for each code directly,
with no date window, and restore only what it confirms is cancelled.
"""
from __future__ import annotations

import argparse
import sys

from guesty_client import fetch_reservations
from guesty_adapter import requested_fields
from sheets_client import (_fmt_request, _runs, month_worksheets, open_spreadsheet,
                           read_as_dataframe, read_row_marks)
from sync import guesty_token, load_config


def guesty_status(token, codes: list[str]) -> dict[str, dict]:
    """{UPPERCASED code -> {status, canceledAt}} straight from Guesty.

    One query for the lot, filtered on the codes themselves, so no date window is
    involved and a checkout from last week is as visible as one from next month.
    """
    rows = fetch_reservations(token, filters=[
        {"field": "confirmationCode", "operator": "$in", "value": codes},
    ], fields=requested_fields() + " status canceledAt cancelledAt")
    out = {}
    for r in rows:
        code = str(r.get("confirmationCode", "")).strip()
        if code:
            out[code.upper()] = {
                "status": r.get("status"),
                "canceledAt": r.get("canceledAt") or r.get("cancelledAt"),
            }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", required=True)
    ap.add_argument("--codes", required=True,
                    help="comma-separated confirmation codes, exactly as Guesty "
                         "has them (they are case-sensitive)")
    ap.add_argument("--live-statuses", default="confirmed,reserved,checkedIn",
                    help="anything NOT in this list counts as cancelled")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        print("no codes given", file=sys.stderr)
        return 2
    live = {s.strip().lower() for s in args.live_statuses.split(",") if s.strip()}

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = next((w for w in month_worksheets(ss).values() if w.title == args.tab), None)
    if ws is None:
        print(f"no tab named {args.tab!r}", file=sys.stderr)
        return 2

    print(f"Asking Guesty about {len(codes)} code(s), with no date window.")
    found = guesty_status(guesty_token(cfg), codes)
    cancelled, still_live, unknown = set(), [], []
    for c in codes:
        rec = found.get(c.upper())
        if rec is None:
            unknown.append(c)
        elif str(rec["status"] or "").lower() in live:
            still_live.append((c, rec["status"]))
        else:
            cancelled.add(c.upper())
            print(f"   {c:<14} {rec['status']:<12} canceledAt={rec['canceledAt']}")
    for c, st in still_live:
        print(f"   {c:<14} {st:<12} -- LIVE, leaving it alone")
    for c in unknown:
        print(f"   {c:<14} {'(not returned)':<12} -- leaving it alone")

    rows, header = read_as_dataframe(ws)
    n_cols = max(len(header), 1)
    struck, _hl, _ac = read_row_marks(ws)

    targets = []
    for i in range(len(rows)):
        code = str(rows.iloc[i].get("Confirmation Code", "")).strip().upper()
        if code and code in cancelled and i not in struck:
            targets.append(i)

    print()
    print(f"{len(cancelled)} code(s) are cancelled; "
          f"{len(targets)} row(s) on '{ws.title}' carry one and are not struck:")
    for i in targets:
        r = rows.iloc[i]
        print(f"   row {i + 2:<6} {str(r.get('Date',''))[:10]}  "
              f"{str(r.get('Property','')).strip():<26} "
              f"{str(r.get('Confirmation Code','')).strip()}")

    if not targets:
        print("\nNothing to restore.")
        return 0
    if not args.confirm:
        print("\n--confirm not given; nothing was written.")
        return 0

    requests = [{"clearBasicFilter": {"sheetId": ws.id}}]
    for a, b in _runs(targets):
        requests.append(_fmt_request(ws, a + 1, b + 2, n_cols,
                                     {"textFormat": {"strikethrough": True}},
                                     "userEnteredFormat.textFormat.strikethrough"))
    ss.batch_update({"requests": requests})

    after, _h, _a = read_row_marks(ws)
    missed = [i for i in targets if i not in after]
    print(f"\nRestored {len(targets) - len(missed)} of {len(targets)} line(s).")
    if missed:
        print(f"   did NOT take: {[i + 2 for i in missed]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
