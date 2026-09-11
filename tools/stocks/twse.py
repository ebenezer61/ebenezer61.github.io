#!/usr/bin/env python3
"""Daily institutional net buying (三大法人買賣超) for the Taiwan basket, from TWSE.

TWSE publishes the T86 report every trading day around 15:00 Taipei, one JSON
document per date holding every listed stock. The daily forecast run needs
the full history of a dozen tickers, so this module keeps a CSV cache in the
repository and only asks TWSE for the dates it does not have yet:

    tools/stocks/cache/twse_t86.csv     date,ticker,foreign,trust,dealer,total
                                        (net shares bought; negative = net sold)

    python tools/stocks/twse.py --backfill               # fill every trading day since START
    python tools/stocks/twse.py --backfill --max 200     # stop after 200 requests (resumable)
    python tools/stocks/twse.py --date 2026-09-10        # fetch one day, print it

predict.py calls update() once per run to append the newest day. TWSE throttles
clients that hammer the endpoint, so requests are spaced SLEEP seconds apart
and the backfill is written to resume where it stopped.

Source: https://www.twse.com.tw/rwd/zh/fund/T86 (data starts 2012-05-02). The
column layout has changed over the years (foreign investors were split into
"外陸資" and "外資自營商" in 2017), so columns are matched by name, not position.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "twse_t86.csv"
URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
HEADERS = {"User-Agent": "Mozilla/5.0 (stocks-forecast; +https://ebenezer61.github.io/stocks/)",
           "Accept": "application/json"}
START = "2014-01-01"
SLEEP = 4.0                 # seconds between requests
COLUMNS = ["date", "ticker", "foreign", "trust", "dealer", "total"]

# Column-name patterns for each aggregate. Old layout: 外資買賣超股數; new layout:
# 外陸資買賣超股數(不含外資自營商) + 外資自營商買賣超股數 (summed below).
PATTERNS = {
    "foreign": re.compile(r"^外.*買賣超股數"),
    "trust": re.compile(r"^投信買賣超股數$"),
    "dealer": re.compile(r"^自營商買賣超股數$"),
    "total": re.compile(r"^三大法人買賣超股數$"),
}


def log(msg: str) -> None:
    print(f"[twse] {msg}", file=sys.stderr, flush=True)


def _num(s: str) -> int:
    s = s.strip().replace(",", "")
    return int(s) if s not in ("", "--") else 0


def fetch_day(date: str, tickers: set[str], session: requests.Session | None = None,
              retries: int = 3) -> list[dict] | None:
    """Rows for `tickers` (codes like "2330") on `date` (YYYY-MM-DD).

    Returns [] on a non-trading day and None if TWSE could not be reached.
    """
    ses = session or requests.Session()
    params = {"date": date.replace("-", ""), "selectType": "ALLBUT0999", "response": "json"}
    for attempt in range(1, retries + 1):
        try:
            r = ses.get(URL, params=params, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            j = r.json()
            break
        except Exception as e:
            log(f"{date}: attempt {attempt} failed: {e!r}")
            if attempt == retries:
                return None
            time.sleep(60 * attempt)
    if j.get("stat") != "OK" or not j.get("data"):
        return []                       # holiday, or a date TWSE has no report for
    fields = [f.strip() for f in j["fields"]]
    cols = {k: [i for i, f in enumerate(fields) if p.match(f)] for k, p in PATTERNS.items()}
    missing = [k for k, v in cols.items() if not v]
    if missing:
        raise RuntimeError(f"{date}: unrecognised T86 layout, no column for {missing}: {fields}")
    out = []
    for row in j["data"]:
        code = row[0].strip()
        if code not in tickers:
            continue
        out.append({"date": date, "ticker": code,
                    **{k: sum(_num(row[i]) for i in idx) for k, idx in cols.items()}})
    return out


def load_cache(path: Path = CACHE) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, dtype={"ticker": str})
        return df[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def save_cache(df: pd.DataFrame, path: Path = CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"])
    df[COLUMNS].to_csv(path, index=False, lineterminator="\n")


def update(dates: list[str], tickers: list[str], path: Path = CACHE, max_requests: int | None = None,
           sleep: float = SLEEP) -> pd.DataFrame:
    """Fetch every date in `dates` (YYYY-MM-DD, trading days) missing from the cache.

    `tickers` are Yahoo symbols ("2330.TW") or bare codes; both are accepted and
    the cache stores bare codes. Returns the updated cache.
    """
    codes = {t.split(".")[0] for t in tickers}
    df = load_cache(path)
    have = set(df["date"].astype(str)) if len(df) else set()
    todo = sorted(d for d in dates if d not in have)
    if max_requests is not None:
        todo = todo[:max_requests]
    if not todo:
        return df
    log(f"{len(todo)} dates to fetch ({todo[0]} .. {todo[-1]}), {len(have)} cached")
    ses = requests.Session()
    new: list[dict] = []
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        rows = fetch_day(d, codes, ses)
        if rows is None:
            log(f"{d}: giving up on this run; {len(new)} rows fetched so far")
            break
        new.extend(rows)
        if i % 25 == 0 or i == len(todo):
            df = pd.concat([df, pd.DataFrame(new, columns=COLUMNS)], ignore_index=True)
            save_cache(df, path)
            new = []
            rate = (time.time() - t0) / i
            log(f"{i}/{len(todo)} done ({d}), {rate:.1f}s per request, "
                f"~{(len(todo) - i) * rate / 60:.0f} min left")
        if i < len(todo):
            time.sleep(sleep)
    if new:
        df = pd.concat([df, pd.DataFrame(new, columns=COLUMNS)], ignore_index=True)
        save_cache(df, path)
    return load_cache(path)


def frame(ticker: str, path: Path = CACHE) -> pd.DataFrame | None:
    """The cached series of one ticker, indexed by date; None if nothing cached."""
    df = load_cache(path)
    code = ticker.split(".")[0]
    s = df[df["ticker"] == code]
    if s.empty:
        return None
    s = s.assign(date=pd.to_datetime(s["date"])).set_index("date").sort_index()
    return s[["foreign", "trust", "dealer", "total"]].astype(float)


def trading_days(start: str) -> list[str]:
    """TWSE trading days from `start`, taken from the 2330.TW price history."""
    import yfinance as yf
    h = yf.download("2330.TW", start=start, interval="1d", auto_adjust=False,
                    actions=False, progress=False)
    idx = pd.DatetimeIndex(h.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return [d.date().isoformat() for d in idx.normalize()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true", help="fetch every missing trading day since --start")
    ap.add_argument("--start", default=START)
    ap.add_argument("--max", type=int, default=None, help="stop after this many requests")
    ap.add_argument("--sleep", type=float, default=SLEEP)
    ap.add_argument("--date", help="fetch one day and print the basket's rows")
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()

    import json
    cfg = json.loads((HERE / "tickers.json").read_text(encoding="utf-8"))
    tickers = [s["ticker"] for s in cfg["TW"]["stocks"]]

    if args.date:
        rows = fetch_day(args.date, {t.split(".")[0] for t in tickers})
        print(pd.DataFrame(rows, columns=COLUMNS).to_string(index=False) if rows else f"no data for {args.date}")
        return 0
    if args.backfill:
        days = trading_days(args.start)
        df = update(days, tickers, Path(args.cache), max_requests=args.max, sleep=args.sleep)
        have = set(df["date"].astype(str))
        left = [d for d in days if d not in have]
        log(f"cache: {len(df)} rows, {len(have)} dates; {len(left)} trading days still missing")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
