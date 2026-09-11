#!/usr/bin/env python3
"""Earnings calendar for the basket, from Yahoo Finance, cached in the repository.

Yahoo returns at most 100 earnings events per ticker (past and scheduled),
with the announcement time of day and the EPS surprise. That covers 2014 to
today for every stock in the basket, but the rows can change or vanish, so
each run merges what Yahoo returns into a CSV that is committed:

    tools/stocks/cache/earnings.csv    ticker,when,eps_estimate,eps_reported,surprise_pct

`when` is the announcement time in New York, minute resolution, naive. It
decides which bar first reacts to the report: a report before 16:00 moves the
bar of that date, a report at or after 16:00 moves the next trading day's bar.

    python tools/stocks/earnings.py --update        # refresh every ticker
    python tools/stocks/earnings.py --show AAPL     # print the cached rows
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache" / "earnings.csv"
COLUMNS = ["ticker", "when", "eps_estimate", "eps_reported", "surprise_pct"]
NY = "America/New_York"


def log(msg: str) -> None:
    print(f"[earnings] {msg}", file=sys.stderr, flush=True)


def load_cache(path: Path = CACHE) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, dtype={"ticker": str, "when": str})[COLUMNS]
    return pd.DataFrame(columns=COLUMNS)


def save_cache(df: pd.DataFrame, path: Path = CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.drop_duplicates(["ticker", "when"], keep="last").sort_values(["ticker", "when"])
    df[COLUMNS].to_csv(path, index=False, lineterminator="\n", float_format="%.4f")


def fetch(ticker: str) -> pd.DataFrame | None:
    """Every earnings event Yahoo has for `ticker`, or None if the call failed."""
    import yfinance as yf
    try:
        e = yf.Ticker(ticker).get_earnings_dates(limit=100)
    except Exception as ex:
        log(f"{ticker}: fetch failed: {ex!r}")
        return None
    if e is None or len(e) == 0:
        return pd.DataFrame(columns=COLUMNS)
    idx = pd.DatetimeIndex(e.index)
    idx = idx.tz_convert(NY) if idx.tz is not None else idx.tz_localize(NY)
    out = pd.DataFrame({
        "ticker": ticker,
        "when": idx.tz_localize(None).strftime("%Y-%m-%d %H:%M"),
        "eps_estimate": pd.to_numeric(e.get("EPS Estimate"), errors="coerce").to_numpy(),
        "eps_reported": pd.to_numeric(e.get("Reported EPS"), errors="coerce").to_numpy(),
        "surprise_pct": pd.to_numeric(e.get("Surprise(%)"), errors="coerce").to_numpy(),
    })
    # Yahoo sometimes lists the same quarter twice; keep the row that has a reported EPS.
    out = out.sort_values(["when", "eps_reported"]).drop_duplicates(["ticker", "when"], keep="last")
    return out


def update(tickers: list[str], path: Path = CACHE, sleep: float = 0.5) -> pd.DataFrame:
    """Merge Yahoo's current rows for every ticker into the cache; return the cache."""
    df = load_cache(path)
    n_new = 0
    for t in tickers:
        rows = fetch(t)
        if rows is None:
            continue
        before = set(df.loc[df["ticker"] == t, "when"])
        n_new += sum(1 for w in rows["when"] if w not in before)
        keep = df[df["ticker"] != t]
        df = pd.concat([x for x in (keep, rows) if len(x)], ignore_index=True) if len(keep) else rows
        time.sleep(sleep)
    save_cache(df, path)
    log(f"{len(df)} rows cached for {df['ticker'].nunique()} tickers, {n_new} new")
    return load_cache(path)


def events(ticker: str, path: Path = CACHE) -> pd.DataFrame | None:
    """The cached events of one ticker, sorted, with `when` as a naive NY timestamp."""
    df = load_cache(path)
    s = df[df["ticker"] == ticker]
    if s.empty:
        return None
    s = s.assign(when=pd.to_datetime(s["when"])).sort_values("when").reset_index(drop=True)
    return s[["when", "eps_estimate", "eps_reported", "surprise_pct"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="refresh the cache for every ticker in tickers.json")
    ap.add_argument("--show", metavar="TICKER", help="print the cached rows of one ticker")
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()
    cfg = json.loads((HERE / "tickers.json").read_text(encoding="utf-8"))
    tickers = [s["ticker"] for m in cfg.values() for s in m["stocks"]]
    if args.update:
        update(tickers, Path(args.cache))
        return 0
    if args.show:
        e = events(args.show, Path(args.cache))
        print(e.to_string(index=False) if e is not None else f"nothing cached for {args.show}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
