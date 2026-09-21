#!/usr/bin/env python3
"""Mock trading: the same rules, the same basket, twenty years, side by side.

Every strategy in STRATEGIES is run on the basket in universe.json with daily
bars from FinMind (finmind.py), and the daily returns of each are written to
mock_trading/data/backtest.json, which the page renders for any window of years
the reader picks. Nothing here places an order anywhere.

How a run works

  * Bars from START; a strategy's equity begins on the first session of
    EQUITY_START so every rule has a year of history behind it.
  * A stock is in the tradable set once it has WARMUP sessions of history.
  * Rebalance on the first session of every month. Signals use every close up
    to the previous session; trades happen at the rebalance session's adjusted
    close, so nothing is bought with information from its own bar.
  * Costs on every trade: brokerage FEE_BP each way, plus TAX_BP on stock sales
    and ETF_TAX_BP on ETF sales (Taiwan securities transaction tax). Cash earns
    nothing. Fractional shares, no slippage, no market impact.
  * The TAIEX total return index is drawn as-is, with no costs, as the yardstick.

Adding a strategy: write a function `(hist, avail) -> dict[symbol, weight]`
where `hist` is the adjusted-close frame up to the previous session and
`avail` the tradable symbols, then register it in STRATEGIES with an id, a
label, a family (benchmark, rule or model) and one sentence of description.
Weights need not sum to one; whatever is left is cash.

    python tools/mock_trading/backtest.py                 # full run, well under a minute
    python tools/mock_trading/backtest.py --out /tmp/x    # write elsewhere
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finmind  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
UNIVERSE = HERE / "universe.json"
DEFAULT_OUT = ROOT / "mock_trading" / "data"

START = "2005-01-01"          # first bar requested
EQUITY_START = "2006-01-01"   # first session every equity curve starts on
WARMUP = 250                  # sessions a stock needs before it is tradable
TOP_N = 5                     # names held by the ranking rules
FEE_BP = 14.25                # brokerage, each way
TAX_BP = 30.0                 # securities transaction tax on stock sales
ETF_TAX_BP = 10.0             # ... on ETF sales
ETF_SYMBOLS = {"0050.TW"}
INDEX_SYMBOL = "TAIEX_TR"

Weights = dict[str, float]
Rule = Callable[[pd.DataFrame, list[str]], Weights]


def log(msg: str) -> None:
    print(f"[mock] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Strategies
# --------------------------------------------------------------------------

def equal(symbols: list[str]) -> Weights:
    return {s: 1.0 / len(symbols) for s in symbols} if symbols else {}


def hold_equal(hist: pd.DataFrame, avail: list[str]) -> Weights:
    return equal(avail)


def hold_0050(hist: pd.DataFrame, avail: list[str]) -> Weights:
    return {"0050.TW": 1.0}


def momentum_12_1(hist: pd.DataFrame, avail: list[str]) -> Weights:
    """Top TOP_N by return over the last 12 months skipping the most recent one."""
    c = hist[avail]
    if len(c) < 253:
        return {}
    score = (c.iloc[-22] / c.iloc[-253] - 1).dropna()
    return equal(list(score.nlargest(TOP_N).index))


def reversal_1m(hist: pd.DataFrame, avail: list[str]) -> Weights:
    """Bottom TOP_N by return over the last month: buy what just fell."""
    c = hist[avail]
    if len(c) < 22:
        return {}
    score = (c.iloc[-1] / c.iloc[-22] - 1).dropna()
    return equal(list(score.nsmallest(TOP_N).index))


def low_vol(hist: pd.DataFrame, avail: list[str]) -> Weights:
    """The TOP_N names with the lowest 60-session volatility of daily returns."""
    c = hist[avail]
    if len(c) < 61:
        return {}
    vol = np.log(c).diff().iloc[-60:].std().dropna()
    return equal(list(vol.nsmallest(TOP_N).index))


def trend_200(hist: pd.DataFrame, avail: list[str]) -> Weights:
    """Equal weight in every name above its 200-session average; the rest in cash."""
    c = hist[avail]
    if len(c) < 200:
        return {}
    above = c.iloc[-1] > c.iloc[-200:].mean()
    held = list(above[above].index)
    return {s: 1.0 / len(avail) for s in held}


STRATEGIES: list[dict] = [
    {"id": "taiex_tr", "label": "TAIEX total return", "family": "benchmark", "rule": None,
     "desc": "The market index with dividends reinvested, no costs. The yardstick every other line is measured against."},
    {"id": "etf_0050", "label": "Hold 0050", "family": "benchmark", "rule": hold_0050,
     "desc": "Buy the Yuanta Taiwan 50 ETF once and hold it; dividends reinvested through the adjusted price."},
    {"id": "hold_eq", "label": "Equal weight, monthly", "family": "benchmark", "rule": hold_equal,
     "desc": "Every stock in the basket at equal weight, rebalanced monthly. What a rule has to beat on this basket."},
    {"id": "mom_12_1", "label": "Momentum 12-1", "family": "rule", "rule": momentum_12_1,
     "desc": f"The {TOP_N} stocks with the highest return over the past year, skipping the latest month; equal weight, monthly."},
    {"id": "rev_1m", "label": "Reversal 1m", "family": "rule", "rule": reversal_1m,
     "desc": f"The {TOP_N} stocks that fell most over the past month; equal weight, monthly."},
    {"id": "low_vol", "label": "Low volatility", "family": "rule", "rule": low_vol,
     "desc": f"The {TOP_N} stocks with the calmest daily returns over the past 60 sessions; equal weight, monthly."},
    {"id": "trend_200", "label": "Trend 200d", "family": "rule", "rule": trend_200,
     "desc": "Each stock held only while it closes above its 200-session average, otherwise its slot sits in cash."},
]


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

def rebalance_days(index: pd.DatetimeIndex, first: pd.Timestamp) -> set[pd.Timestamp]:
    """The first session of every month from `first` on."""
    idx = index[index >= first]
    months = pd.Series(idx.to_period("M"), index=idx)
    return set(months.groupby(months).apply(lambda s: s.index[0]))


def simulate(rule: Rule, px: pd.DataFrame, first_bar: pd.Series, equity_index: pd.DatetimeIndex,
             rebal: set[pd.Timestamp]) -> tuple[pd.Series, float, dict]:
    """Daily equity (starting at 1.0), annual one-way turnover, and the last target weights."""
    units: dict[str, float] = {}
    cash = 1.0
    values = np.empty(len(equity_index))
    turnover = 0.0
    latest: dict = {}
    pos = px.index.get_indexer(equity_index)
    for k, (t, i) in enumerate(zip(equity_index, pos)):
        row = px.iloc[i]
        value = cash + sum(u * row[s] for s, u in units.items())
        if t in rebal:
            hist = px.iloc[:i]                      # every close before this session
            avail = [s for s in px.columns if s != INDEX_SYMBOL and first_bar[s] <= i - WARMUP
                     and not np.isnan(row[s])]
            target = rule(hist, avail)
            latest = {"date": t.date().isoformat(), "weights": {s: round(w, 4) for s, w in target.items() if w > 0}}
            traded = 0.0
            cost = 0.0
            for s in set(units) | set(target):
                price = row[s]
                if np.isnan(price):
                    continue
                cur = units.get(s, 0.0) * price
                want = target.get(s, 0.0) * value
                delta = want - cur
                if abs(delta) < 1e-9:
                    continue
                tax = ETF_TAX_BP if s in ETF_SYMBOLS else TAX_BP
                cost += abs(delta) * (FEE_BP + (tax if delta < 0 else 0.0)) / 1e4
                cash -= delta
                traded += abs(delta)
                units[s] = want / price
                if units[s] <= 0:
                    del units[s]
            cash -= cost
            turnover += traded / value / 2
            value = cash + sum(u * row[s] for s, u in units.items())
        values[k] = value
    years = (equity_index[-1] - equity_index[0]).days / 365.25
    return pd.Series(values, index=equity_index), turnover / max(years, 1e-9), latest


def metrics(eq: pd.Series) -> dict:
    r = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    dd = (eq / eq.cummax() - 1).min()
    vol = r.std() * np.sqrt(252)
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")
    return {"total": round(float(total), 4), "cagr": round(float(cagr), 4), "vol": round(float(vol), 4),
            "sharpe": round(float(cagr / vol), 2) if vol > 0 else None, "max_dd": round(float(dd), 4)}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--start", default=START)
    args = ap.parse_args()
    t0 = time.time()

    uni = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    symbols = [s["symbol"] for s in uni["stocks"]] + [b["symbol"] for b in uni["benchmarks"]]
    log(f"downloading {len(symbols)} series from {args.start}")
    data = finmind.download(symbols, args.start)
    missing = [s for s in symbols if s not in data]
    if INDEX_SYMBOL not in data or len(data) < len(symbols) // 2:
        log(f"not enough data (missing {missing}); leaving the existing file untouched")
        return 1
    if missing:
        log(f"missing, skipped: {missing}")

    # One calendar (the index's sessions); a stock missing a session keeps its last close.
    px = pd.DataFrame({s: df["Adj Close"] for s, df in data.items()}).reindex(data[INDEX_SYMBOL].index)
    px = px.ffill()
    first_bar = pd.Series({s: int(np.argmax(px[s].notna().values)) for s in px.columns})
    equity_index = px.index[px.index >= pd.Timestamp(EQUITY_START)]
    rebal = rebalance_days(px.index, equity_index[0])
    log(f"{len(px)} sessions, equity from {equity_index[0].date()} to {equity_index[-1].date()}, "
        f"{len(rebal)} rebalances")

    out_strats = []
    for st in STRATEGIES:
        if st["rule"] is None:
            eq = px.loc[equity_index, INDEX_SYMBOL]
            eq = eq / eq.iloc[0]
            turnover, latest = 0.0, {}
        else:
            eq, turnover, latest = simulate(st["rule"], px, first_bar, equity_index, rebal)
        m = metrics(eq)
        log(f"{st['id']:10s} total {m['total']:+8.1%}  cagr {m['cagr']:+6.1%}  vol {m['vol']:5.1%}  "
            f"maxdd {m['max_dd']:6.1%}  turnover {turnover:4.2f}/yr")
        ret_bp = np.round(np.log(eq).diff().fillna(0.0).to_numpy() * 1e4).astype(int)
        out_strats.append({
            "id": st["id"], "label": st["label"], "family": st["family"], "desc": st["desc"],
            "annual_turnover": round(float(turnover), 3),
            "latest": latest,
            "full_period": m,
            "ret_bp": ret_bp.tolist(),
        })

    names = {s["symbol"]: s for s in uni["stocks"] + uni["benchmarks"]}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "format": "log-returns-bp",
        "start": equity_index[0].date().isoformat(),
        "end": equity_index[-1].date().isoformat(),
        "dates": [d.date().isoformat() for d in equity_index],
        "universe": {
            "label": uni["label"], "currency": uni["currency"], "note": uni["note"],
            "stocks": [{"symbol": s, **{k: names[s][k] for k in ("name", "en")}} for s in symbols
                       if s in data and s not in {b["symbol"] for b in uni["benchmarks"]}],
            "benchmarks": [{"symbol": b["symbol"], "name": b["name"], "en": b["en"]} for b in uni["benchmarks"]],
        },
        "rules": {
            "rebalance": "first session of every month; signals from every close before it, trades at its close",
            "warmup_sessions": WARMUP, "top_n": TOP_N,
            "fee_bp": FEE_BP, "tax_bp": TAX_BP, "etf_tax_bp": ETF_TAX_BP,
            "data_source": "FinMind (TaiwanStockPrice, TaiwanStockDividendResult, TaiwanStockTotalReturnIndex)",
        },
        "strategies": out_strats,
    }
    path = out_dir / "backtest.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"wrote {path} ({path.stat().st_size // 1024} KB) in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
