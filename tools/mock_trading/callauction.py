#!/usr/bin/env python3
"""Record the TWSE and TPEx call-auction trial matches (試撮) for a list of stocks.

Between 08:30 and 09:00 Taipei the exchange publishes, every few seconds, the
price and volume the opening auction would clear at if it ran now, with the
best five bids and asks. The same happens before the close, 13:25 to 13:30.
Neither the exchange nor FinMind keeps this history, so the only way to have it
is to record it live. This script polls the exchange's public quote endpoint
(mis.twse.com.tw, the one the TWSE website uses; it serves TPEx stocks too) every
POLL seconds through a window, every FAST_POLL seconds in the last minutes before
the open, and appends every new snapshot to one CSV per day and symbol list:

    --list basket   universe.json (the backtest basket and 0050)
                    tools/mock_trading/cache/callauction/YYYY-MM-DD.csv
    --list tw50     tw50.json (the 50 constituents of 0050, snapshot of 2026-09-23)
                    tools/mock_trading/cache/callauction_tw50/YYYY-MM-DD.csv
    --list competition  competition.json (the competition's designated Top-50,
                    tw_stock_competition info.json, incl. the TPEx stocks)
                    tools/mock_trading/cache/callauction_competition/YYYY-MM-DD.csv

one row per symbol and exchange timestamp, columns:

    fetched_at   local wall-clock time of the request (Asia/Taipei, ISO)
    symbol       2330.TW, or 8069.TWO for a TPEx stock
    ex_time      the exchange's timestamp of the snapshot (HH:MM:SS)
    trial        1 while the snapshot is a trial match, 0 once trading is live
    price        trial match price (pz) during the auction, last trade (z) after
    volume       trial match volume in lots (ps) during the auction, else the trade volume
    prev_close   yesterday's close (y)
    bid_px, bid_sz, ask_px, ask_sz   best five, "_"-joined, as the exchange sends them

    python tools/mock_trading/callauction.py --window open               # 08:29 to 09:03, run by cron
    python tools/mock_trading/callauction.py --window close              # 13:24 to 13:32
    python tools/mock_trading/callauction.py --window open --list tw50   # the 0050 constituents
    python tools/mock_trading/callauction.py --window open --list competition
    python tools/mock_trading/callauction.py --once [--list tw50]        # one snapshot, print it

On a day with no session (holiday, typhoon) the exchange keeps serving the last
session's quotes; rows are kept only when their exchange date is today, so such
a day writes nothing and the script stops after NO_DATA_MINUTES.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
LISTS = {                        # --list name -> (symbol file, output folder)
    "basket": (HERE / "universe.json", HERE / "cache" / "callauction"),
    "tw50": (HERE / "tw50.json", HERE / "cache" / "callauction_tw50"),
    "competition": (HERE / "competition.json", HERE / "cache" / "callauction_competition"),
}
URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
HEADERS = {"User-Agent": "Mozilla/5.0 (mock-trading recorder; +https://ebenezer61.github.io/mock_trading/)",
           "Referer": "https://mis.twse.com.tw/stock/index.jsp"}
TPE = ZoneInfo("Asia/Taipei")
POLL = 15.0                      # seconds between requests
FAST_POLL = 5.0                  # seconds between requests from FAST_FROM on
FAST_FROM = {"open": "08:57"}    # the last trial matches before the open, the ones a 08:59 decision can use
NO_DATA_MINUTES = 8              # give up if nothing dated today shows up for this long
WINDOWS = {"open": ("08:29", "09:03"), "close": ("13:24", "13:32")}   # a couple of minutes past each auction so the first live print is kept
FIELDS = ["fetched_at", "symbol", "ex_time", "trial", "price", "volume", "prev_close",
          "bid_px", "bid_sz", "ask_px", "ask_sz"]


def log(msg: str) -> None:
    print(f"[callauction] {datetime.now(TPE):%H:%M:%S} {msg}", file=sys.stderr, flush=True)


def symbols(name: str) -> list[str]:
    doc = json.loads(LISTS[name][0].read_text(encoding="utf-8"))
    out = [s["symbol"] for s in doc["stocks"]] + [b["symbol"] for b in doc.get("benchmarks", [])]
    return [s for s in out if s.endswith((".TW", ".TWO"))]


def snapshot(syms: list[str], ses: requests.Session) -> list[dict]:
    """One request for every symbol; rows dated today only."""
    ex_ch = "|".join(f"{'otc' if s.endswith('.TWO') else 'tse'}_{s.split('.')[0]}.tw" for s in syms)
    r = ses.get(URL, params={"ex_ch": ex_ch, "json": "1", "delay": "0", "_": int(time.time() * 1000)},
                headers=HEADERS, timeout=15)
    r.raise_for_status()
    now = datetime.now(TPE)
    rows = []
    for q in r.json().get("msgArray", []):
        tlong = q.get("tlong")
        if not tlong or datetime.fromtimestamp(int(tlong) / 1000, TPE).date() != now.date():
            continue
        trial = q.get("ts") == "1"
        price = q.get("pz") if trial else q.get("z")
        rows.append({
            "fetched_at": now.isoformat(timespec="seconds"),
            "symbol": f"{q['c']}.{'TWO' if q.get('ex') == 'otc' else 'TW'}",
            "ex_time": q.get("t", ""),
            "trial": int(trial),
            "price": "" if price in (None, "-") else price,
            "volume": q.get("ps" if trial else "tv", "") or "",
            "prev_close": q.get("y", ""),
            "bid_px": q.get("b", "").rstrip("_"), "bid_sz": q.get("g", "").rstrip("_"),
            "ask_px": q.get("a", "").rstrip("_"), "ask_sz": q.get("f", "").rstrip("_"),
        })
    return rows


def append(rows: list[dict], seen: set[tuple[str, str]], path: Path) -> int:
    new = [r for r in rows if (r["symbol"], r["ex_time"]) not in seen]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        if fresh:
            w.writeheader()
        w.writerows(new)
    seen.update((r["symbol"], r["ex_time"]) for r in new)
    return len(new)


def load_seen(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as fh:
        return {(r["symbol"], r["ex_time"]) for r in csv.DictReader(fh)}


def at(hhmm: str, day: datetime) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def record(window: str, poll: float, list_name: str) -> int:
    syms = symbols(list_name)
    now = datetime.now(TPE)
    start, end = (at(x, now) for x in WINDOWS[window])
    fast = at(FAST_FROM[window], now) if window in FAST_FROM else end
    if now > end:
        log(f"the {window} window ended at {end:%H:%M}; nothing to do")
        return 0
    if now < start:
        log(f"waiting until {start:%H:%M}")
        time.sleep((start - now).total_seconds())
    path = LISTS[list_name][1] / f"{now:%Y-%m-%d}.csv"
    seen = load_seen(path)
    ses = requests.Session()
    total, last_data = 0, datetime.now(TPE)
    log(f"recording {len(syms)} symbols to {path.relative_to(HERE.parent.parent)} until {end:%H:%M}")
    while datetime.now(TPE) < end:
        t0 = time.monotonic()
        try:
            rows = snapshot(syms, ses)
            took = time.monotonic() - t0
            n = append(rows, seen, path)
            total += n
            if rows:
                last_data = datetime.now(TPE)
            # every poll is logged, also one that brought nothing new, so a gap in
            # the CSV can be told apart: a slow request, or the exchange not updating
            ex = [r["ex_time"] for r in rows]
            log(f"{list_name} {window} poll: {took:.1f}s, {len(rows)} dated today, {n} new, "
                f"{sum(r['trial'] for r in rows)} trial, ex_time {min(ex, default='-')} to {max(ex, default='-')}")
        except Exception as e:  # a missed poll is fine; the next one is seconds away
            log(f"{list_name} {window} poll failed after {time.monotonic() - t0:.1f}s: {type(e).__name__}: {str(e)[:160]}")
        if datetime.now(TPE) - last_data > timedelta(minutes=NO_DATA_MINUTES):
            log("nothing dated today; no session, stopping")
            break
        time.sleep(min(poll, FAST_POLL) if datetime.now(TPE) >= fast else poll)
    log(f"done: {total} rows")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", choices=sorted(WINDOWS))
    ap.add_argument("--once", action="store_true", help="take one snapshot and print it")
    ap.add_argument("--list", choices=sorted(LISTS), default="basket", help="which symbols to record")
    ap.add_argument("--poll", type=float, default=POLL)
    args = ap.parse_args()
    if args.once:
        rows = snapshot(symbols(args.list), requests.Session())
        w = csv.DictWriter(sys.stdout, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        log(f"{len(rows)} rows dated today")
        return 0
    if args.window:
        return record(args.window, args.poll, args.list)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
