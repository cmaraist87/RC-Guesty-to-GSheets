"""Is a market ready to go live on Connecteam, and how big is it?

    python market_readiness.py [--month 2026-10]

STRICTLY READ-ONLY, and no Guesty call, so no token is spent.

Answers the two questions the rollout turns on, per market AND per board:

  * SIZE -- how many cards a month would produce. This is the blast radius if
    something is wrong, and it is what decides the order markets go live in.
  * COVERAGE -- how many properties have no Connecteam Job. A property without
    one produces NO CARD AT ALL, silently: the clean simply never reaches a
    crew. That has to be zero before a market goes live.

Reported per board as well as per market because two markets can share one --
New Orleans with Bay St. Louis, Savannah with Thunderbolt -- so a mistake on
either lands on both crews at once.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict

from connecteam_client import ConnecteamClient, check_api_key
from connecteam_jobs import build_index, resolve, usable
from connecteam_map import CITY_SCHEDULERS, scheduler_for
from processing import EXCLUDE_PROPERTIES, _canonical_key
from sheet_merge import norm_city
from sheets_client import month_worksheets, open_spreadsheet, read_as_dataframe
from sync import _today_chicago, load_config

EXCLUDED = {_canonical_key(p) for p in EXCLUDE_PROPERTIES}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default=None, metavar="YYYY-MM")
    args = ap.parse_args(argv)

    cfg = load_config()
    ym = args.month or _today_chicago().strftime("%Y-%m")
    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    ws = month_worksheets(ss).get((int(ym[:4]), int(ym[5:7])))
    if ws is None:
        print(f"no tab for {ym}")
        return 2
    rows, _ = read_as_dataframe(ws)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    all_jobs, _how = client.list_jobs(next(iter(CITY_SCHEDULERS.values())))

    per_board_index = {}
    for board in sorted(set(CITY_SCHEDULERS.values())):
        per_board_index[board] = build_index(all_jobs, board=board)

    stats = {}
    for city in sorted(CITY_SCHEDULERS):
        board = scheduler_for(city)
        index = per_board_index[board]
        cards, props, missing = 0, set(), set()
        for _i, r in rows.iterrows():
            if norm_city(r.get("City", "")) != norm_city(city):
                continue
            prop = str(r.get("Property", "")).strip()
            if not prop or _canonical_key(prop) in EXCLUDED:
                continue
            props.add(prop)
            if not str(r.get("Check out - Time", "")
                       or r.get("Check-out Time", "")).strip():
                continue                       # arrival-only: no clean, no card
            if resolve(prop, index)[0]:
                cards += 1
            else:
                missing.add(prop)
        stats[city] = dict(board=board, cards=cards, props=len(props),
                           missing=sorted(missing),
                           jobs=len(usable(all_jobs, board)))

    print(f"Connecteam readiness for {ws.title}")
    print("")
    print(f"{'market':<16}{'board':<11}{'cards':>7}{'props':>7}{'Jobs':>7}"
          f"{'no Job':>8}   ready")
    print("-" * 72)
    for city, s in sorted(stats.items(), key=lambda kv: kv[1]["cards"]):
        ready = "YES" if not s["missing"] else f"no ({len(s['missing'])} missing)"
        print(f"{city:<16}{s['board']:<11}{s['cards']:>7}{s['props']:>7}"
              f"{s['jobs']:>7}{len(s['missing']):>8}   {ready}")

    print("")
    print("by BOARD -- what actually goes live together:")
    by_board = defaultdict(list)
    for city, s in stats.items():
        by_board[s["board"]].append(city)
    order = sorted(by_board, key=lambda b: sum(stats[c]["cards"] for c in by_board[b]))
    for n, board in enumerate(order, 1):
        cities = sorted(by_board[board])
        cards = sum(stats[c]["cards"] for c in cities)
        miss = sorted({m for c in cities for m in stats[c]["missing"]})
        print(f"  {n}. board {board}  {', '.join(cities):<32} {cards:>5} card(s)"
              + (f"   {len(miss)} propertie(s) need a Job" if miss else "   ready"))
        for m in miss:
            print(f"        {m}")
    print("")
    print("  Smallest board first is the rollout order: a mistake on a shared")
    print("  board reaches every crew on it at once.")
    print("  Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
