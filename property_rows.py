"""Every row the sheet holds for properties matching a name, across all tabs.

    python property_rows.py --like "1401 Carondelet"

STRICTLY READ-ONLY, and sheet-only: no Guesty call, so no token is spent.
"""
from __future__ import annotations

import argparse
from collections import Counter

from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--like", required=True)
    ap.add_argument("--show", type=int, default=40)
    args = ap.parse_args(argv)
    needle = args.like.strip().lower()

    cfg = load_config()
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    counts, shown = Counter(), []
    for key in sorted(month_worksheets(ss)):
        ws = month_worksheets(ss)[key]
        rows, _ = read_as_dataframe(ws)
        if not len(rows) or "Property" not in rows.columns:
            continue
        for i, r in rows.iterrows():
            prop = str(r.get("Property", "")).strip()
            if needle not in prop.lower():
                continue
            counts[prop] += 1
            shown.append((ws.title, i + 2, str(r.get("Date", ""))[:10], prop,
                          str(r.get("Confirmation Code", "")).strip(),
                          str(r.get("Guest", "")).strip(),
                          str(r.get("Check out - Time", "")).strip(),
                          str(r.get("Check-in Time", "")).strip(),
                          str(r.get("T/O", "")).strip()))

    print(f"properties matching {args.like!r}:")
    for p, n in sorted(counts.items()):
        print(f"   {n:>5}  {p}")
    print(f"   {sum(counts.values()):>5}  TOTAL rows")

    print("")
    print(f"rows (most recent {args.show}):")
    print(f"   {'tab':<18}{'row':>6}  {'date':<12}{'property':<24}"
          f"{'code':<14}{'out':<10}{'in':<10}{'T/O':<5}guest")
    for t, i, d, p, c, g, out, inn, to in sorted(shown, key=lambda x: x[2])[-args.show:]:
        print(f"   {t:<18}{i:>6}  {d:<12}{p:<24}{c:<14}{out:<10}{inn:<10}{to:<5}{g}")
    print("")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
