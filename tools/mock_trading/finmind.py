#!/usr/bin/env python3
"""Daily bars from FinMind (https://finmindtrade.com) for the mock-trading backtests.

Every frame comes back in one layout:

    columns Open, High, Low, Close, Adj Close, Volume; one row per session,
    naive DatetimeIndex at midnight, ascending.

Symbols and the FinMind datasets behind them (free "register" tier, checked
2026-09-19):

    2330.TW, 0050.TW   TaiwanStockPrice, unadjusted. Adj Close is computed here:
                       for every ex-date the reference price over the previous
                       close, from TaiwanStockDividendResult (cash and stock
                       dividends), TaiwanStockSplitPrice and
                       TaiwanStockCapitalReductionReferencePrice, multiplied into
                       every earlier close, which is what Yahoo's Adj Close does.
    ^TWII              TaiwanStockPrice TAIEX (price index)
    TAIEX_TR           TaiwanStockTotalReturnIndex TAIEX (dividends reinvested)

The token is read from FINMIND_API_TOKEN, else from the file FINMIND_TOKEN_FILE
names, else from ~/.config/finmind/token or <workspace>/.config/finmind/token
(the directory above the repository). It is never logged.

A registered free account gets 600 requests per hour; one request returns a
symbol's whole history, and a Taiwan stock costs four (price, dividends, splits,
capital reductions), so the basket in universe.json costs about a hundred.
Responses are cached under finmind_cache/ (ignored by git) for CACHE_HOURS so
repeated local runs do not touch the quota.

    python tools/mock_trading/finmind.py --check          # fetch every symbol in universe.json
    python tools/mock_trading/finmind.py --show 2330.TW   # print the last rows
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
URL = "https://api.finmindtrade.com/api/v4/data"
CACHE_DIR = HERE / "finmind_cache"
CACHE_HOURS = 6.0
TOKEN_FILES = [
    Path.home() / ".config" / "finmind" / "token",
    ROOT.parent / ".config" / "finmind" / "token",
]
COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
CAPRED_DATA_START = pd.Timestamp("2011-01-01")   # FinMind's capital-reduction table begins here
JUMP = float(np.log(1.12))                         # beyond any daily price limit (7% before 2015-06, 10% since)


class FinMindError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[finmind] {msg}", file=sys.stderr, flush=True)


def token() -> str | None:
    """The API token, or None when none is configured."""
    t = os.environ.get("FINMIND_API_TOKEN", "").strip()
    if t:
        return t
    files = [Path(os.environ["FINMIND_TOKEN_FILE"])] if os.environ.get("FINMIND_TOKEN_FILE") else []
    for p in files + TOKEN_FILES:
        try:
            t = p.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if t:
            return t
    return None


def locate(symbol: str) -> tuple[str, str] | None:
    """(dataset, data_id) FinMind serves `symbol` from, or None."""
    if symbol == "^TWII":
        return "TaiwanStockPrice", "TAIEX"
    if symbol == "TAIEX_TR":
        return "TaiwanStockTotalReturnIndex", "TAIEX"
    if symbol.endswith((".TW", ".TWO")):
        return "TaiwanStockPrice", symbol.rsplit(".", 1)[0]
    return None


# --------------------------------------------------------------------------
# Requests and the local cache
# --------------------------------------------------------------------------

def _cache_path(dataset: str, data_id: str | None, start: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in (data_id or "all"))
    return CACHE_DIR / f"{dataset}__{safe}__{start}.json"


def fetch(dataset: str, data_id: str | None, start: str, session: requests.Session | None = None,
          retries: int = 3, use_cache: bool = True) -> list[dict]:
    """Rows of `dataset` for `data_id` from `start`; raises FinMindError when refused."""
    tok = token()
    if not tok:
        raise FinMindError("no FinMind token configured")
    path = _cache_path(dataset, data_id, start)
    if use_cache and path.exists() and time.time() - path.stat().st_mtime < CACHE_HOURS * 3600:
        return json.loads(path.read_text(encoding="utf-8"))

    params = {"dataset": dataset, "start_date": start}
    if data_id is not None:
        params["data_id"] = data_id
    ses = session or requests.Session()
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = ses.get(URL, params=params, headers={"Authorization": f"Bearer {tok}"}, timeout=90)
            try:
                body = r.json()
            except ValueError:
                body = {}
            msg = str(body.get("msg", ""))
            if r.status_code == 200 and body.get("status") == 200:
                rows = body.get("data") or []
                if use_cache:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
                return rows
            if r.status_code in (400, 401, 402, 403):
                raise FinMindError(f"{dataset}[{data_id}] refused (HTTP {r.status_code}): {msg[:160]}")
            raise RuntimeError(f"HTTP {r.status_code}: {msg[:160]}")
        except FinMindError:
            raise
        except Exception as e:  # network trouble or a 5xx: wait and retry
            last = e
            log(f"{dataset}[{data_id}] attempt {attempt} failed: {e!r}")
            if attempt < retries:
                time.sleep(15 * attempt)
    raise FinMindError(f"{dataset}[{data_id}] failed after {retries} attempts: {last!r}")


def _frame(rows: list[dict]) -> pd.DataFrame:
    """The rows as a frame indexed by their (normalised) date."""
    df = pd.DataFrame(rows)
    if df.empty or "date" not in df.columns:
        return pd.DataFrame()
    df.index = pd.to_datetime(df["date"]).dt.normalize()
    return df.drop(columns=["date"])


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[df["Close"].notna() & (df["Close"] > 0)].copy()


# --------------------------------------------------------------------------
# Taiwan adjustment factors
# --------------------------------------------------------------------------

def tw_factors(code: str, start: str, session: requests.Session | None = None) -> pd.Series:
    """Per ex-date, the reference price over the previous close for stock `code`."""
    out: dict[pd.Timestamp, float] = {}

    def add(rows: list[dict], before: str, after: str, only_code: bool = False) -> None:
        for r in rows:
            if only_code and str(r.get("stock_id")) != code:
                continue
            try:
                b, a = float(r[before]), float(r[after])
            except (KeyError, TypeError, ValueError):
                continue
            if b > 0 and a > 0:
                d = pd.Timestamp(r["date"]).normalize()
                out[d] = out.get(d, 1.0) * (a / b)

    add(fetch("TaiwanStockDividendResult", code, start, session), "before_price", "reference_price")
    add(fetch("TaiwanStockSplitPrice", None, start, session), "before_price", "after_price", only_code=True)
    add(fetch("TaiwanStockCapitalReductionReferencePrice", code, start, session),
        "ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice")
    return pd.Series(out, dtype=float).sort_index()


def unexplained_jumps(code: str, close: pd.Series, factors: pd.Series) -> pd.Series:
    """Sessions before CAPRED_DATA_START whose close moved beyond any daily price
    limit with no ex-date on record: FinMind's capital-reduction table starts in
    2011, and a capital reduction is the one event that moves a Taiwan stock
    that far in a day (ASUS on 2010-06-24, Chunghwa Telecom in 2008 and 2009).
    Each is treated as a reduction whose reference price was that session's
    close, so the day's move is set to zero. Logged so they can be audited."""
    r = np.log(close.astype(float)).diff()
    hits = r[(r.index < CAPRED_DATA_START) & (r.abs() > JUMP) & ~r.index.isin(factors.index)]
    for d, v in hits.items():
        log(f"{code}: {d.date()} moved {np.expm1(v):+.0%} with no ex-date on record, treated as a capital reduction")
    return pd.Series({d: float(close[d] / close.shift(1)[d]) for d in hits.index}, dtype=float)


def apply_factors(close: pd.Series, factors: pd.Series) -> pd.Series:
    """close × the product of the factors of every ex-date after each bar."""
    close = close.astype(float)
    if factors.empty:
        return close
    f = factors[factors.index > close.index[0]]
    if f.empty:
        return close
    cum = f.iloc[::-1].cumprod().iloc[::-1]              # product of this and every later ex-date
    pos = np.searchsorted(f.index.values, close.index.values, side="right")
    mult = np.where(pos < len(cum), cum.values[np.minimum(pos, len(cum) - 1)], 1.0)
    return close * mult


# --------------------------------------------------------------------------
# Bars
# --------------------------------------------------------------------------

def bars(symbol: str, start: str, session: requests.Session | None = None) -> pd.DataFrame | None:
    """OHLCV plus Adj Close for `symbol` from `start`, or None if FinMind does not serve it."""
    loc = locate(symbol)
    if loc is None:
        return None
    dataset, data_id = loc
    df = _frame(fetch(dataset, data_id, start, session))
    if df.empty:
        return None
    if dataset == "TaiwanStockPrice":
        out = _clean(pd.DataFrame({
            "Open": _num(df, "open"), "High": _num(df, "max"), "Low": _num(df, "min"),
            "Close": _num(df, "close"), "Volume": _num(df, "Trading_Volume"),
        }))
        if data_id == "TAIEX":
            out["Adj Close"] = out["Close"]
        else:
            factors = tw_factors(data_id, start, session)
            factors = pd.concat([factors, unexplained_jumps(data_id, out["Close"], factors)]).sort_index()
            out["Adj Close"] = apply_factors(out["Close"], factors)
    elif dataset == "TaiwanStockTotalReturnIndex":
        v = _num(df, "price")
        out = _clean(pd.DataFrame({"Open": v, "High": v, "Low": v, "Close": v, "Adj Close": v, "Volume": 0.0}))
    else:  # pragma: no cover
        return None
    out = out[COLUMNS]
    out.index.name = "Date"
    return out if len(out) else None


def download(symbols: list[str], start: str) -> dict[str, pd.DataFrame]:
    """Bars for every symbol FinMind serves; a symbol that fails is logged and skipped."""
    out: dict[str, pd.DataFrame] = {}
    if not token():
        raise FinMindError("no FinMind token configured (FINMIND_API_TOKEN, FINMIND_TOKEN_FILE, "
                           "~/.config/finmind/token)")
    ses = requests.Session()
    for s in dict.fromkeys(symbols):
        try:
            df = bars(s, start, ses)
        except FinMindError as e:
            log(f"{s}: {e}")
            df = None
        if df is None:
            log(f"{s}: no data")
        else:
            out[s] = df
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fetch every symbol in universe.json and report")
    ap.add_argument("--show", metavar="SYMBOL", help="print the last rows of one symbol")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    if args.no_cache:
        global CACHE_HOURS
        CACHE_HOURS = 0.0
    if not token():
        print("no FinMind token found (FINMIND_API_TOKEN, FINMIND_TOKEN_FILE, ~/.config/finmind/token)")
        return 2
    if args.show:
        df = bars(args.show, args.start)
        print(df.tail(8).to_string() if df is not None else f"{args.show}: not served")
        return 0
    if args.check:
        uni = json.loads((HERE / "universe.json").read_text(encoding="utf-8"))
        syms = [s["symbol"] for s in uni["stocks"]] + [b["symbol"] for b in uni["benchmarks"]]
        t0 = time.time()
        got = download(syms, args.start)
        for s, df in got.items():
            print(f"{s:10s} {df.index[0].date()}..{df.index[-1].date()} {len(df):5d} rows"
                  f"  first adj/raw {df['Adj Close'].iloc[0] / df['Close'].iloc[0]:.4f}"
                  f"  last close {df['Close'].iloc[-1]:.2f}")
        missing = [s for s in syms if s not in got]
        print(f"{len(got)} served, missing {missing}  ({time.time() - t0:.0f}s)")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
