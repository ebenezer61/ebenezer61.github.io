#!/usr/bin/env python3
"""Daily direction forecasts for a fixed basket of Taiwan and US blue chips.

For every ticker listed in tickers.json the script

  1. downloads daily bars from Yahoo Finance (via yfinance) back to START,
     plus whatever extra series the enabled feature groups need,
  2. builds the base features (~35, from price, volume and the market index)
     and the enabled optional groups (see FEATURE_GROUPS),
  3. runs a walk-forward evaluation over the last TEST_DAYS trading days
     (retrain every RETRAIN_EVERY days; labels are embargoed by the horizon,
     so nothing the model trains on was unknown at forecast time),
  4. fits a final XGBoost classifier on every labelled row and scores the
     latest bar for each horizon (next trading day, five trading days),

and writes four files into stocks/data/:

  predictions.json   today's snapshot, the file the page renders
  history.json       every forecast ever published, with its realised
                     outcome filled in once the target bar exists
  backtest.json      per ticker and horizon, the walk-forward window's daily
                     predicted probability next to what happened (the chart
                     in each row's detail panel), packed: one date list per
                     ticker, probabilities in thousandths, outcomes as a
                     0/1 string, returns in basis points
  log.json           the resolved forecasts of the last LOG_DAYS trading
                     days, flattened for the page's forecast log

    python tools/stocks/predict.py            # full run, ~2 to 4 minutes
    python tools/stocks/predict.py --quick    # 2 tickers per market, smoke test
    python tools/stocks/predict.py --out DIR  # write somewhere else
    python tools/stocks/predict.py --features us_lead,macro   # enable groups

Feature groups (DEFAULT_FEATURES sets them per market; experiment.py compares them):

  us_lead   Taiwan only. The US session of the same calendar date closes at
            04:00 Taipei the next morning, before the forecast is made and
            before the Taiwan target bar opens: S&P 500, SOX and TSMC ADR
            returns, and the ADR premium over the Taipei close.
  macro     VIX, US 10-year yield, dollar index, USD/TWD, crude oil, gold:
            daily and 20-day changes, VIX z-score.
  xsec      Cross-section of the same market's basket: mean return of the
            other stocks, share of them up, dispersion, this stock relative
            to the basket; for US stocks also the sector ETF from tickers.json.
  twse      Taiwan only. Institutional net buying (foreign, investment trust,
            dealer) from the TWSE T86 report, cached by twse.py, scaled by
            the stock's 20-day volume.
  risk      Market internals from the US session: VVIX, VIX over realised
            S&P 500 volatility, high-yield versus investment-grade credit,
            small versus large caps, utilities versus the market, the yield
            curve slope, copper versus gold.
  earnings  The stock's own earnings calendar from Yahoo, cached by
            earnings.py: bars until and since the reaction bar, whether the
            next bar is the reaction bar, the last and the pending EPS surprise.

Every feature must be known at 07:47 Taipei the morning after `as_of`, when
the workflow runs, and is aligned the same way in training and inference.

The GitHub Actions workflow .github/workflows/stocks.yml runs the full
command once per weekday and commits the output back to main.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import xgboost as xgb
import yfinance as yf
from sklearn.metrics import brier_score_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import earnings  # noqa: E402
import twse  # noqa: E402

warnings.filterwarnings("ignore")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONFIG = HERE / "tickers.json"
DEFAULT_OUT = ROOT / "stocks" / "data"

START = "2014-01-01"          # first bar requested from Yahoo
HORIZONS = (1, 5)             # trading days ahead
TEST_DAYS = 250               # walk-forward window, about one trading year
RETRAIN_EVERY = 21            # refit inside the walk-forward every ~month
WARMUP = 260                  # rows dropped so 252-day features are defined
MIN_ROWS = 800                # skip a ticker with less history than this
SPARK_DAYS = 60               # closes shipped for the sparkline
HISTORY_KEEP_DAYS = 3 * 366   # history.json is pruned beyond this
LOG_DAYS = 60                 # trading days of resolved forecasts in log.json
TWSE_DAILY_REQUESTS = 10      # cap on TWSE fetches inside the daily run
SETTLE_MINUTES = 20           # a session's bar counts as final this long after the close

FEATURE_GROUPS = ("us_lead", "macro", "xsec", "twse", "risk", "earnings")
# What the daily run uses per market, decided from experiments/latest.md
# (2026-09-11, 500-day walk-forward pooled over the basket): every group for
# Taiwan lifts next-day accuracy from 53% to 61%, with no interference between
# groups; for US stocks no group moved accuracy by more than one standard
# error, so they keep the base set. Whenever a market has groups, the run also
# fits a base-only shadow model so the live track record shows both.
DEFAULT_FEATURES: dict[str, tuple[str, ...]] = {
    "TW": FEATURE_GROUPS,
    "US": (),
}

# Yahoo symbols each group needs beyond the basket and the market index.
EXTRA_SYMBOLS = {
    "us_lead": ["^GSPC", "^SOX", "TSM", "TWD=X"],
    "macro": ["^VIX", "^TNX", "DX-Y.NYB", "TWD=X", "CL=F", "GC=F"],
    "risk": ["^VVIX", "^VIX", "^GSPC", "HYG", "LQD", "IWM", "SPY", "XLU", "^TNX", "^IRX", "HG=F", "GC=F"],
}
ADR_SHARES = 5                # one TSM ADR = five 2330.TW shares
EARNINGS_CAP = 70             # trading days; "no report in sight" beyond this

XGB_PARAMS = dict(
    n_estimators=400,
    max_depth=3,
    learning_rate=0.02,
    subsample=0.8,
    colsample_bytree=0.7,
    min_child_weight=10,
    reg_lambda=2.0,
    objective="binary:logistic",
    eval_metric="logloss",
    tree_method="hist",
    n_jobs=2,
    random_state=0,
)


def log(msg: str) -> None:
    print(f"[stocks] {msg}", file=sys.stderr, flush=True)


def parse_groups(spec: str | None) -> tuple[str, ...] | None:
    """'us_lead,macro' -> ('us_lead', 'macro'); 'all' -> every group; 'none'/'' -> ();
    None (flag not given) -> None, meaning "use DEFAULT_FEATURES per market"."""
    if spec is None:
        return None
    spec = spec.strip().lower()
    if spec in ("", "none", "base"):
        return ()
    if spec == "all":
        return FEATURE_GROUPS
    groups = tuple(g.strip() for g in spec.split(",") if g.strip())
    bad = [g for g in groups if g not in FEATURE_GROUPS]
    if bad:
        raise SystemExit(f"unknown feature group(s) {bad}; choose from {FEATURE_GROUPS}")
    return tuple(g for g in FEATURE_GROUPS if g in groups)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def download(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """Download raw OHLCV (+ Adj Close) for every ticker; skip the ones Yahoo drops."""
    tickers = list(dict.fromkeys(tickers))
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            raw = yf.download(
                tickers, start=start, interval="1d", auto_adjust=False,
                actions=False, progress=False, group_by="ticker", threads=True,
            )
            break
        except Exception as e:  # network hiccups, rate limits
            last_err = e
            log(f"download attempt {attempt} failed: {e!r}")
            time.sleep(15 * attempt)
    else:
        raise RuntimeError(f"download failed after 3 attempts: {last_err!r}")

    out: dict[str, pd.DataFrame] = {}
    top = set(raw.columns.get_level_values(0)) if isinstance(raw.columns, pd.MultiIndex) else set()
    for t in tickers:
        if t not in top:
            log(f"{t}: no data returned")
            continue
        s = raw[t].dropna(subset=["Close"]).copy()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        s.index = idx.normalize()
        if len(s) == 0:
            log(f"{t}: empty after cleaning")
            continue
        out[t] = s
    return out


def last_completed_session(m: dict, now: datetime) -> pd.Timestamp | None:
    """The latest calendar date whose session in market `m` has closed (plus a
    settling margin) at `now`. Bars after it are in progress: Yahoo returns the
    partial bar of a session that is still open, and GitHub runs the 23:47 UTC
    schedule up to two hours late, inside the Taipei morning session, which
    once put opening prices into the record as closes.
    """
    tz, close = m.get("tz"), m.get("close")
    if not tz or not close:
        return None
    local = now.astimezone(ZoneInfo(tz))
    hh, mm = (int(x) for x in close.split(":"))
    cutoff = local.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(minutes=SETTLE_MINUTES)
    day = local.date() if local >= cutoff else local.date() - timedelta(days=1)
    return pd.Timestamp(day)


def adjust(raw: pd.DataFrame) -> pd.DataFrame:
    """Split- and dividend-adjusted OHLC for modelling; RawClose kept for display."""
    close = raw["Close"].astype(float)
    adj = raw["Adj Close"].astype(float) if "Adj Close" in raw.columns else close
    factor = (adj / close).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    return pd.DataFrame({
        "Open": raw["Open"].astype(float) * factor,
        "High": raw["High"].astype(float) * factor,
        "Low": raw["Low"].astype(float) * factor,
        "Close": adj,
        "Volume": raw["Volume"].astype(float),
        "RawClose": close,
    }, index=raw.index)


@dataclass
class Context:
    """Everything one market's feature builder needs besides the stock's own bars."""
    market: str
    groups: tuple[str, ...]
    idx_close: pd.Series | None = None
    extra: dict[str, pd.DataFrame] = field(default_factory=dict)     # symbol -> adjust()ed bars
    px: dict[str, pd.DataFrame] = field(default_factory=dict)        # basket ticker -> adjust()ed bars
    sector: dict[str, str] = field(default_factory=dict)             # ticker -> sector ETF symbol
    twse: dict[str, pd.DataFrame] = field(default_factory=dict)      # ticker -> T86 frame
    earnings: dict[str, pd.DataFrame] = field(default_factory=dict)  # ticker -> earnings events
    basket_ret: pd.DataFrame | None = None                           # daily log returns, one column per ticker
    basket_fi: pd.DataFrame | None = None                            # foreign net / 20d volume, per ticker

    def finish(self) -> "Context":
        if self.px:
            self.basket_ret = pd.DataFrame({t: np.log(p["Close"]).diff() for t, p in self.px.items()})
        if self.twse:
            cols = {}
            for t, p in self.px.items():
                f = self.twse.get(t)
                if f is None:
                    continue
                v20 = p["Volume"].rolling(20).mean()
                cols[t] = f["foreign"].reindex(p.index) / v20
            if cols:
                self.basket_fi = pd.DataFrame(cols)
        return self


def needed_symbols(groups: tuple[str, ...], cfg: dict) -> list[str]:
    syms: list[str] = []
    for g in groups:
        syms += EXTRA_SYMBOLS.get(g, [])
    if "xsec" in groups:
        for m in cfg.values():
            syms += [s["sector_etf"] for s in m["stocks"] if s.get("sector_etf")]
    return list(dict.fromkeys(syms))


# --------------------------------------------------------------------------
# Features and targets
# --------------------------------------------------------------------------

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def aligned(s: pd.Series | None, index: pd.Index) -> pd.Series | None:
    """`s` on the stock's calendar: exact date where it exists, else the last earlier value.

    A US bar dated d closes after the Taiwan bar dated d and before the Taiwan
    bar d+1 opens, so for a Taiwan stock the value dated d is what the forecast
    for d+1 may use. Bars after the stock's last date are dropped, which also
    discards the partial bar Yahoo returns for a session still in progress.
    """
    if s is None:
        return None
    return s.reindex(index).ffill()


def base_features(px: pd.DataFrame, idx_close: pd.Series | None) -> pd.DataFrame:
    c, o, h, l, v = px["Close"], px["Open"], px["High"], px["Low"], px["Volume"]
    r = np.log(c).diff()
    f = pd.DataFrame(index=px.index)

    # Recent daily returns, individually (lag1 is today's return)
    for k in (1, 2, 3, 4, 5):
        f[f"ret_lag{k}"] = r.shift(k - 1)
    # Cumulative returns and realised volatility over a few windows
    for k in (5, 10, 20, 60):
        f[f"ret_{k}d"] = np.log(c / c.shift(k))
    for k in (5, 20, 60):
        f[f"vol_{k}d"] = r.rolling(k).std()
    # Distance to moving averages
    for k in (5, 10, 20, 50, 200):
        f[f"sma{k}_gap"] = c / c.rolling(k).mean() - 1
    # Momentum oscillators
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = (ema12 - ema26) / c
    f["macd"] = macd
    f["macd_hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    f["rsi14"] = rsi(c, 14)
    f["bb_pctb"] = (c - c.rolling(20).mean()) / (2 * c.rolling(20).std())
    # Volume and intraday shape
    f["vol_ratio20"] = v / v.rolling(20).mean()
    f["range_1d"] = (h - l) / c
    f["gap_open"] = o / c.shift(1) - 1
    f["close_pos"] = (c - l) / (h - l)
    # 52-week position
    f["hi252_gap"] = c / c.rolling(252).max() - 1
    f["lo252_gap"] = c / c.rolling(252).min() - 1
    # Market context
    if idx_close is not None:
        ic = aligned(idx_close, px.index)
        ir = np.log(ic).diff()
        f["idx_ret_1d"] = ir
        f["idx_ret_5d"] = np.log(ic / ic.shift(5))
        f["idx_ret_20d"] = np.log(ic / ic.shift(20))
        f["idx_vol_20d"] = ir.rolling(20).std()
        f["idx_sma50_gap"] = ic / ic.rolling(50).mean() - 1
        f["rel_ret_20d"] = f["ret_20d"] - f["idx_ret_20d"]
    f["dow"] = px.index.dayofweek
    return f


def us_lead_features(px: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    """US session of the same date, for Taiwan stocks: closes after Taipei, before Taipei reopens."""
    f = pd.DataFrame(index=px.index)
    for name, sym in (("spx", "^GSPC"), ("sox", "^SOX"), ("adr", "TSM")):
        s = ctx.extra.get(sym)
        if s is None:
            continue
        c = aligned(s["Close"], px.index)
        f[f"us_{name}_1d"] = np.log(c).diff()
        f[f"us_{name}_5d"] = np.log(c / c.shift(5))
    tsm, fx, tw = ctx.extra.get("TSM"), ctx.extra.get("TWD=X"), ctx.px.get("2330.TW")
    if tsm is not None and fx is not None and tw is not None:
        adr_twd = aligned(tsm["RawClose"], px.index) * aligned(fx["RawClose"], px.index) / ADR_SHARES
        prem = adr_twd / aligned(tw["RawClose"], px.index) - 1
        f["adr_prem"] = prem
        f["adr_prem_chg"] = prem.diff()
    return f


def macro_features(px: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    f = pd.DataFrame(index=px.index)
    vix = ctx.extra.get("^VIX")
    if vix is not None:
        v = aligned(vix["Close"], px.index)
        f["vix"] = v
        f["vix_chg_1d"] = np.log(v).diff()
        f["vix_z20"] = (v - v.rolling(20).mean()) / v.rolling(20).std()
    tnx = ctx.extra.get("^TNX")
    if tnx is not None:
        y = aligned(tnx["Close"], px.index)
        f["tnx_chg_1d"] = y.diff()
        f["tnx_chg_20d"] = y.diff(20)
    for name, sym in (("dxy", "DX-Y.NYB"), ("twd", "TWD=X"), ("oil", "CL=F"), ("gold", "GC=F")):
        s = ctx.extra.get(sym)
        if s is None:
            continue
        c = aligned(s["Close"], px.index)
        f[f"{name}_1d"] = np.log(c).diff()
        f[f"{name}_20d"] = np.log(c / c.shift(20))
    return f


def xsec_features(px: pd.DataFrame, ctx: Context, ticker: str) -> pd.DataFrame:
    """The rest of the basket, and the sector ETF where one is configured."""
    f = pd.DataFrame(index=px.index)
    r = np.log(px["Close"]).diff()
    if ctx.basket_ret is not None and ctx.basket_ret.shape[1] > 1:
        others = ctx.basket_ret.drop(columns=[ticker], errors="ignore").reindex(px.index)
        bk = others.mean(axis=1)
        f["bk_ret_1d"] = bk
        f["bk_ret_5d"] = bk.rolling(5).sum()
        f["bk_ret_20d"] = bk.rolling(20).sum()
        f["bk_up_share"] = (others > 0).sum(axis=1) / others.notna().sum(axis=1).replace(0, np.nan)
        f["bk_disp"] = others.std(axis=1)
        f["rel_bk_1d"] = r - bk
        f["rel_bk_20d"] = r.rolling(20).sum() - f["bk_ret_20d"]
    etf = ctx.extra.get(ctx.sector.get(ticker, ""))
    if etf is not None:
        c = aligned(etf["Close"], px.index)
        f["sec_ret_1d"] = np.log(c).diff()
        f["sec_ret_5d"] = np.log(c / c.shift(5))
        f["sec_ret_20d"] = np.log(c / c.shift(20))
        f["sec_sma50_gap"] = c / c.rolling(50).mean() - 1
        f["rel_sec_20d"] = r.rolling(20).sum() - f["sec_ret_20d"]
    return f


def twse_features(px: pd.DataFrame, ctx: Context, ticker: str) -> pd.DataFrame:
    """Institutional net buying, in units of the stock's 20-day average volume."""
    f = pd.DataFrame(index=px.index)
    t86 = ctx.twse.get(ticker)
    if t86 is None:
        return f
    t86 = t86.reindex(px.index)
    v20 = px["Volume"].rolling(20).mean()
    for name, col in (("fi", "foreign"), ("it", "trust"), ("dl", "dealer"), ("inst", "total")):
        s = t86[col]
        f[f"{name}_net_1d"] = s / v20
        f[f"{name}_net_5d"] = s.rolling(5, min_periods=1).sum() / v20
        if name in ("fi", "it", "inst"):
            f[f"{name}_net_20d"] = s.rolling(20, min_periods=5).sum() / v20
    # Consecutive days of foreign net buying (+) or selling (-), capped at 10
    sign = np.sign(t86["foreign"].fillna(0)).to_numpy()
    streak, run = np.zeros(len(sign)), 0.0
    for i, s in enumerate(sign):
        run = run + s if s and np.sign(run) in (0, s) else s
        streak[i] = max(-10, min(10, run))
    f["fi_streak"] = streak
    if ctx.basket_fi is not None:
        others = ctx.basket_fi.drop(columns=[ticker], errors="ignore").reindex(px.index)
        f["bk_fi_net_1d"] = others.mean(axis=1)
        f["bk_fi_net_5d"] = f["bk_fi_net_1d"].rolling(5, min_periods=1).sum()
    return f


def risk_features(px: pd.DataFrame, ctx: Context) -> pd.DataFrame:
    """Risk appetite gauges from the US session, all dated d and final before the run."""
    f = pd.DataFrame(index=px.index)

    def close(sym: str) -> pd.Series | None:
        s = ctx.extra.get(sym)
        return aligned(s["Close"], px.index) if s is not None else None

    vvix = close("^VVIX")
    if vvix is not None:
        f["vvix_1d"] = np.log(vvix).diff()
        f["vvix_z20"] = (vvix - vvix.rolling(20).mean()) / vvix.rolling(20).std()
    vix, spx = close("^VIX"), close("^GSPC")
    if vix is not None and spx is not None:
        realised = np.log(spx).diff().rolling(20).std() * np.sqrt(252) * 100
        f["vrp"] = vix / realised                       # implied over realised: >1 means fear priced in
    for name, a, b in (("hy_ig", "HYG", "LQD"), ("iwm_spy", "IWM", "SPY"),
                       ("xlu_spy", "XLU", "SPY"), ("cu_au", "HG=F", "GC=F")):
        ca, cb = close(a), close(b)
        if ca is None or cb is None:
            continue
        ratio = np.log(ca / cb)
        f[f"{name}_5d"] = ratio.diff(5)
        f[f"{name}_20d"] = ratio.diff(20)
    tnx, irx = close("^TNX"), close("^IRX")
    if tnx is not None and irx is not None:
        curve = tnx - irx
        f["curve"] = curve
        f["curve_chg_20d"] = curve.diff(20)
    return f


def earnings_features(px: pd.DataFrame, ctx: Context, ticker: str) -> pd.DataFrame:
    """Where each bar sits relative to the stock's earnings reports.

    A report before 16:00 New York moves the bar of that date, one at or after
    16:00 moves the next bar. At the close of bar i the forecast knows every
    report announced up to 16:00 that day, including one released after the
    close whose reaction bar is i+1 (`pending_surprise`); the scheduled date
    of the next report is public well in advance.
    """
    f = pd.DataFrame(index=px.index)
    ev = ctx.earnings.get(ticker)
    if ev is None or ev.empty:
        return f
    n = len(px)
    dates = px.index.normalize()
    day_of = ev["when"].dt.normalize().to_numpy()
    after_close = (ev["when"].dt.hour >= 16).to_numpy()
    # react[i]: position of the first bar whose close reflects report i. Reports
    # after the last bar have no position yet; count business days past it.
    react = np.searchsorted(dates.to_numpy(), day_of, side="left") + after_close.astype(int)
    beyond = day_of > dates[-1].to_numpy()
    if beyond.any():
        ahead = np.busday_count(dates[-1].date(), day_of[beyond].astype("datetime64[D]"))
        react[beyond] = n - 1 + ahead + after_close[beyond].astype(int)
    known = react - after_close.astype(int)         # first bar at whose close the report is known
    surprise = ev["surprise_pct"].clip(-100, 100).to_numpy(dtype=float)

    react_next = np.zeros(n)
    react_today = np.zeros(n)
    for r in react:
        if 0 <= r < n:
            react_today[r] = 1
        if 1 <= r <= n:
            react_next[r - 1] = 1
    pos = np.arange(n)
    # last reaction at or before i, next reaction after i
    react_sorted = np.sort(react)
    last_idx = np.searchsorted(react_sorted, pos, side="right") - 1
    days_since = np.where(last_idx >= 0, pos - react_sorted[np.clip(last_idx, 0, None)], EARNINGS_CAP)
    next_idx = np.searchsorted(react_sorted, pos, side="right")
    days_to = np.where(next_idx < len(react_sorted),
                       react_sorted[np.clip(next_idx, None, len(react_sorted) - 1)] - pos, EARNINGS_CAP)
    # last report known at the close of i, and its surprise if the reaction is still to come
    order = np.argsort(known, kind="stable")
    known_sorted, surprise_sorted, react_by_known = known[order], surprise[order], react[order]
    k_idx = np.searchsorted(known_sorted, pos, side="right") - 1
    valid = k_idx >= 0
    kk = np.clip(k_idx, 0, None)
    last_surprise = np.where(valid, surprise_sorted[kk], np.nan)
    pending = np.where(valid & (react_by_known[kk] > pos), surprise_sorted[kk], 0.0)

    f["earn_react_next"] = react_next
    f["earn_react_today"] = react_today
    f["days_since_earn"] = np.minimum(days_since, EARNINGS_CAP)
    f["days_to_earn"] = np.minimum(days_to, EARNINGS_CAP)
    f["last_surprise"] = last_surprise
    f["pending_surprise"] = np.nan_to_num(pending, nan=0.0)
    return f


def build_features(px: pd.DataFrame, ctx: Context, ticker: str,
                   groups: tuple[str, ...] | None = None) -> pd.DataFrame:
    groups = ctx.groups if groups is None else groups
    parts = [base_features(px, ctx.idx_close)]
    if "us_lead" in groups and ctx.market == "TW":
        parts.append(us_lead_features(px, ctx))
    if "macro" in groups:
        parts.append(macro_features(px, ctx))
    if "xsec" in groups:
        parts.append(xsec_features(px, ctx, ticker))
    if "twse" in groups and ctx.market == "TW":
        parts.append(twse_features(px, ctx, ticker))
    if "risk" in groups:
        parts.append(risk_features(px, ctx))
    if "earnings" in groups:
        parts.append(earnings_features(px, ctx, ticker))
    f = pd.concat([p for p in parts if len(p.columns)], axis=1)
    return f.replace([np.inf, -np.inf], np.nan)


def make_target(close: pd.Series, h: int) -> pd.Series:
    fwd = np.log(close.shift(-h) / close)
    y = (fwd > 0).astype(float)
    y[fwd.isna()] = np.nan
    return y


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def walk_forward(X: pd.DataFrame, y: np.ndarray, fwd: np.ndarray, h: int,
                 params: dict, test_days: int) -> tuple[dict, dict]:
    """Rolling out-of-sample evaluation over the last `test_days` labelled rows.

    At forecast time s the label of row i is known only if i + h <= s, so each
    refit trains on rows [0, s - h] and scores rows [s, s + RETRAIN_EVERY).
    `fwd` is the h-day forward log return of every row (NaN where unknown).

    Returns the metrics dict and the per-day series of the test window
    (dates, predicted probability, actual outcome, realised return).
    """
    labelled = np.flatnonzero(~np.isnan(y))
    last = int(labelled[-1])
    test_start = last + 1 - test_days
    if test_start < 500:
        raise ValueError(f"not enough history for a {test_days}-day test window")

    p = np.full(len(y), np.nan)
    fits = 0
    for s in range(test_start, last + 1, RETRAIN_EVERY):
        e = min(s + RETRAIN_EVERY, last + 1)
        cut = s - h + 1
        m = xgb.XGBClassifier(**params).fit(X.iloc[:cut], y[:cut])
        p[s:e] = m.predict_proba(X.iloc[s:e])[:, 1]
        fits += 1

    yt = y[test_start:last + 1]
    pt = p[test_start:last + 1]
    pred = (pt > 0.5).astype(float)
    majority = 1.0 if np.nanmean(y[:test_start - h + 1]) >= 0.5 else 0.0
    conf = np.abs(pt - 0.5) >= 0.10

    metrics = {
        "test_start": X.index[test_start].date().isoformat(),
        "test_end": X.index[last].date().isoformat(),
        "n": int(len(yt)),
        "acc": round(float(np.mean(pred == yt)), 4),
        "baseline_acc": round(float(np.mean(yt == majority)), 4),
        "baseline_label": "up" if majority == 1.0 else "down",
        "up_share": round(float(np.mean(yt == 1.0)), 4),
        "auc": round(float(roc_auc_score(yt, pt)), 4) if len(np.unique(yt)) == 2 else None,
        "brier": round(float(brier_score_loss(yt, pt)), 4),
        "conf_n": int(conf.sum()),
        "conf_acc": round(float(np.mean(pred[conf] == yt[conf])), 4) if conf.sum() else None,
        "fits": fits,
    }
    series = {
        "dates": [d.date().isoformat() for d in X.index[test_start:last + 1]],
        "p": [round(float(v), 3) for v in pt],
        "actual": [int(v) for v in yt],
        "ret": [round(float(np.expm1(v)), 4) for v in fwd[test_start:last + 1]],
    }
    return metrics, series


def pack_backtest(series_by_h: dict[str, dict]) -> dict:
    """Compact form of one ticker's walk-forward series for backtest.json.

    Both horizons are slices of the same calendar, so the dates are stored
    once and each horizon keeps an offset: {"dates": [...], "1": {"off": k,
    "p": [thousandths], "a": "0110...", "r": [basis points]}}. The page
    unpacks it in renderBacktest(). About 45% smaller than the plain lists.
    """
    starts = {h: s["dates"][0] for h, s in series_by_h.items()}
    ends = {h: s["dates"][-1] for h, s in series_by_h.items()}
    first, last = min(starts.values()), max(ends.values())
    # the union calendar, taken from the horizon that starts first and extended
    # by the one that ends last (they overlap, so this is exact)
    h_first = min(series_by_h, key=lambda h: starts[h])
    dates = list(series_by_h[h_first]["dates"])
    for h, s in series_by_h.items():
        for d in s["dates"]:
            if d > dates[-1]:
                dates.append(d)
    out = {"dates": dates}
    for h, s in series_by_h.items():
        out[h] = {
            "off": dates.index(s["dates"][0]),
            "p": [int(round(v * 1000)) for v in s["p"]],
            "a": "".join(str(int(v)) for v in s["actual"]),
            "r": [int(round(v * 10000)) for v in s["ret"]],
        }
    return out


def final_fit(X: pd.DataFrame, y: np.ndarray, params: dict) -> dict:
    labelled = np.flatnonzero(~np.isnan(y))
    cut = int(labelled[-1]) + 1
    m = xgb.XGBClassifier(**params).fit(X.iloc[:cut], y[:cut])
    p_up = float(m.predict_proba(X.iloc[[-1]])[0, 1])
    gain = m.get_booster().get_score(importance_type="gain")
    total = sum(gain.values()) or 1.0
    top = sorted(gain.items(), key=lambda kv: -kv[1])[:6]
    return {
        "p_up": round(p_up, 4),
        "train_rows": cut,
        "top_features": [[k, round(v / total, 4)] for k, v in top],
    }


def analyse(meta: dict, px: pd.DataFrame | None, ctx: Context,
            params: dict, test_days: int) -> tuple[dict, list[str], pd.Series, dict]:
    """Returns (page record, feature names, adjusted close, backtest series by horizon)."""
    t = meta["ticker"]
    if px is None or len(px) < MIN_ROWS:
        raise ValueError(f"only {0 if px is None else len(px)} rows of history")
    X = build_features(px, ctx, t).iloc[WARMUP:]
    # Shadow model on the base features alone, so the live track record can
    # show what the extra groups add on the same days (only when there are any).
    X_base = build_features(px, ctx, t, ()).iloc[WARMUP:] if ctx.groups else None

    forecast, backtest = {}, {}
    for h in HORIZONS:
        c = px["Close"]
        fwd = np.log(c.shift(-h) / c).iloc[WARMUP:].to_numpy()
        y = make_target(c, h).iloc[WARMUP:].to_numpy()
        ev, series = walk_forward(X, y, fwd, h, params, test_days)
        fin = final_fit(X, y, params)
        forecast[str(h)] = {**fin, "eval": ev}
        if X_base is not None:
            forecast[str(h)]["p_up_base"] = final_fit(X_base, y, params)["p_up"]
        backtest[str(h)] = series
        log(f"{t}: h={h} p_up={fin['p_up']:.3f} acc={ev['acc']:.3f} base={ev['baseline_acc']:.3f} auc={ev['auc']}"
            + (f" shadow={forecast[str(h)]['p_up_base']:.3f}" if X_base is not None else ""))

    rc = px["RawClose"]
    rec = {
        "ticker": t,
        "name": meta.get("name", t),
        "en": meta.get("en", ""),
        "as_of": px.index[-1].date().isoformat(),
        "close": round(float(rc.iloc[-1]), 2),
        "change_pct": round(float(rc.iloc[-1] / rc.iloc[-2] - 1) * 100, 2),
        "closes": [round(float(v), 2) for v in rc.iloc[-SPARK_DAYS:]],
        "dates": [d.date().isoformat() for d in rc.index[-SPARK_DAYS:]],
        "first_date": px.index[0].date().isoformat(),
        "rows": int(len(X)),
        "forecast": forecast,
    }
    return rec, list(X.columns), px["Close"], backtest


# --------------------------------------------------------------------------
# Loading everything a run needs (shared with experiment.py)
# --------------------------------------------------------------------------

def load_market(mkey: str, m: dict, stocks: list[dict], extra: dict[str, pd.DataFrame],
                groups: tuple[str, ...], start: str, twse_requests: int | None,
                now: datetime | None = None) -> tuple[Context, dict[str, pd.DataFrame]]:
    """Download one market's bars and build its Context. Returns (context, raw bars by ticker)."""
    tickers = [s["ticker"] for s in stocks] + [m["index"]]
    log(f"{mkey}: downloading {len(tickers)} series from {start}")
    data = download(tickers, start)

    # Keep only sessions that have closed: a bar dated after the last completed
    # session is the partial bar of a session still in progress.
    limit = last_completed_session(m, now or datetime.now(timezone.utc))
    if limit is not None:
        dropped = {t: df.index[-1].date().isoformat() for t, df in data.items() if df.index[-1] > limit}
        if dropped:
            log(f"{mkey}: dropping in-progress bars after {limit.date()}: {sorted(set(dropped.values()))} for {len(dropped)} series")
            data = {t: df[df.index <= limit] for t, df in data.items()}
        stale = [t for t, df in data.items() if df.index[-1] < limit - timedelta(days=4)]
        if stale:
            log(f"{mkey}: WARNING last bar older than {limit.date()} minus 4 days for {stale}")

    idx_raw = data.get(m["index"])
    idx_close = adjust(idx_raw)["Close"] if idx_raw is not None else None
    if idx_close is None:
        log(f"{mkey}: index {m['index']} missing, market features skipped")

    ctx = Context(market=mkey, groups=groups, idx_close=idx_close, extra=extra,
                  sector={s["ticker"]: s["sector_etf"] for s in stocks if s.get("sector_etf")})
    for s in stocks:
        raw = data.get(s["ticker"])
        if raw is not None and len(raw) >= MIN_ROWS:
            ctx.px[s["ticker"]] = adjust(raw)

    if mkey == "TW" and ("twse" in groups or twse.CACHE.exists()):
        # Keep the T86 cache current (the daily run fetches at most a few days;
        # the backfill is twse.py --backfill). Needed as features only when enabled.
        ref = ctx.px.get("2330.TW")
        if ref is not None:
            days = [d.date().isoformat() for d in ref.index if d.date().isoformat() >= start]
            try:
                twse.update(days, list(ctx.px), max_requests=twse_requests)
            except Exception as e:
                log(f"TWSE update failed, using the cache as is: {e!r}")
        if "twse" in groups:
            for t in ctx.px:
                fr = twse.frame(t)
                if fr is None:
                    log(f"{t}: no T86 rows cached, twse features will be NaN")
                else:
                    ctx.twse[t] = fr

    if "earnings" in groups or (earnings.CACHE.exists() and twse_requests):
        # Refresh the earnings calendar on the daily run (twse_requests == 0
        # means "cache only", which experiment.py uses); read it when enabled.
        if twse_requests:
            try:
                earnings.update(list(ctx.px))
            except Exception as e:
                log(f"earnings update failed, using the cache as is: {e!r}")
        if "earnings" in groups:
            for t in ctx.px:
                ev = earnings.events(t)
                if ev is None:
                    log(f"{t}: no earnings rows cached, earnings features skipped")
                else:
                    ctx.earnings[t] = ev
    return ctx.finish(), data


# --------------------------------------------------------------------------
# History: every published forecast, resolved once the target bar exists
# --------------------------------------------------------------------------

def update_history(path: Path, adj_close: dict[str, pd.Series], today: list[dict],
                   now: datetime) -> dict:
    hist = {"forecasts": []}
    if path.exists():
        try:
            hist = json.loads(path.read_text())
        except Exception as e:
            log(f"history unreadable, starting fresh: {e!r}")
    forecasts = hist.get("forecasts", [])

    # 1. Resolve outcomes for forecasts whose target bar is now available.
    resolved = 0
    for f in forecasts:
        s = adj_close.get(f["ticker"])
        if s is None:
            continue
        as_of = pd.Timestamp(f["as_of"])
        if as_of not in s.index:
            continue
        pos = s.index.get_loc(as_of)
        for h in HORIZONS:
            k = f"h{h}"
            if f.get(k) is None or f.get(f"{k}_actual") is not None:
                continue
            if pos + h < len(s):
                ret = float(s.iloc[pos + h] / s.iloc[pos] - 1)
                f[f"{k}_actual"] = 1 if ret > 0 else 0
                f[f"{k}_ret"] = round(ret, 5)
                f[f"{k}_target_date"] = s.index[pos + h].date().isoformat()
                resolved += 1

    # 2. Append today's forecasts (idempotent on re-runs for the same bar).
    seen = {(f["ticker"], f["as_of"]) for f in forecasts}
    added = 0
    for rec in today:
        if (rec["ticker"], rec["as_of"]) in seen:
            continue
        forecasts.append(rec)
        added += 1

    # 3. Prune and sort.
    cutoff = (now - timedelta(days=HISTORY_KEEP_DAYS)).date().isoformat()
    forecasts = sorted((f for f in forecasts if f["as_of"] >= cutoff),
                       key=lambda f: (f["as_of"], f["market"], f["ticker"]))
    log(f"history: {added} added, {resolved} outcomes resolved, {len(forecasts)} kept")

    hist = {"updated_at": now.isoformat(timespec="seconds"), "forecasts": forecasts}
    path.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")) + "\n")
    return hist


def summarise_history(hist: dict) -> dict:
    """Hit rates of the published forecasts, per market and overall."""
    out: dict = {}
    groups: dict[str, list[dict]] = {"ALL": []}
    for f in hist.get("forecasts", []):
        groups.setdefault(f["market"], []).append(f)
        groups["ALL"].append(f)
    for g, fs in groups.items():
        out[g] = {}
        for h in HORIZONS:
            k = f"h{h}"
            done = [f for f in fs if f.get(f"{k}_actual") is not None and f.get(k) is not None]
            n = len(done)
            hits = sum(1 for f in done if (f[k] > 0.5) == (f[f"{k}_actual"] == 1))
            ups = sum(f[f"{k}_actual"] for f in done)
            # The base-feature shadow forecast; before extra groups existed (or
            # for a market without any) the production forecast is the base one.
            base_hits = sum(1 for f in done if (f.get(f"{k}_base", f[k]) > 0.5) == (f[f"{k}_actual"] == 1))
            shadow_n = sum(1 for f in done if f.get(f"{k}_base") is not None)
            # per forecast date: [count, hits, actual ups, base-feature hits], so
            # the page can draw the cumulative hit rate of both models against
            # the always-up baseline
            by_date: dict[str, list[int]] = {}
            for f in done:
                d = by_date.setdefault(f["as_of"], [0, 0, 0, 0])
                d[0] += 1
                d[1] += int((f[k] > 0.5) == (f[f"{k}_actual"] == 1))
                d[2] += int(f[f"{k}_actual"])
                d[3] += int((f.get(f"{k}_base", f[k]) > 0.5) == (f[f"{k}_actual"] == 1))
            out[g][k] = {
                "n": n,
                "hits": hits,
                "hit_rate": round(hits / n, 4) if n else None,
                "up_share": round(ups / n, 4) if n else None,
                "base_hits": base_hits,
                "base_hit_rate": round(base_hits / n, 4) if n else None,
                "shadow_n": shadow_n,
                "pending": sum(1 for f in fs if f.get(k) is not None and f.get(f"{k}_actual") is None),
                "by_date": [[d, v[0], v[1], v[2], v[3]] for d, v in sorted(by_date.items())],
            }
    first = min((f["as_of"] for f in hist.get("forecasts", [])), default=None)
    out["first_forecast"] = first
    # First forecast date made with extra feature groups, per market: the page
    # marks it on the charts, since the record before it is the base model's.
    since: dict[str, str] = {}
    for f in hist.get("forecasts", []):
        if any(f.get(f"h{h}_base") is not None for h in HORIZONS):
            since[f["market"]] = min(since.get(f["market"], f["as_of"]), f["as_of"])
    out["features_since"] = since
    return out


def write_log(hist: dict, path: Path, now: datetime) -> int:
    """Flatten the resolved forecasts of the last LOG_DAYS trading days, newest first."""
    forecasts = hist.get("forecasts", [])
    keep = set(sorted({f["as_of"] for f in forecasts})[-LOG_DAYS:])
    rows = []
    for f in forecasts:
        if f["as_of"] not in keep:
            continue
        for h in HORIZONS:
            k = f"h{h}"
            if f.get(k) is None or f.get(f"{k}_actual") is None:
                continue
            rows.append({
                "as_of": f["as_of"], "ticker": f["ticker"], "market": f["market"], "h": h,
                "p": f[k], "p_base": f.get(f"{k}_base"), "actual": f[f"{k}_actual"],
                "ret": f.get(f"{k}_ret"), "target_date": f.get(f"{k}_target_date"),
            })
    rows.sort(key=lambda r: (r["market"], r["ticker"], r["h"]))
    rows.sort(key=lambda r: r["as_of"], reverse=True)
    path.write_text(json.dumps({
        "updated_at": now.isoformat(timespec="seconds"),
        "window_days": LOG_DAYS,
        "n_rows": len(rows),
        "rows": rows,
    }, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(rows)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory (default: stocks/data)")
    ap.add_argument("--start", default=START, help=f"first bar to request (default: {START})")
    ap.add_argument("--quick", action="store_true", help="2 tickers per market, small model, short test window")
    ap.add_argument("--features", default=None,
                    help=f"comma-separated feature groups from {FEATURE_GROUPS}, or 'all'/'none', applied to "
                         f"every market (default: per market, {DEFAULT_FEATURES})")
    ap.add_argument("--twse-requests", type=int, default=TWSE_DAILY_REQUESTS,
                    help=f"max TWSE T86 fetches to refresh the cache (default {TWSE_DAILY_REQUESTS}; 0 = cache only)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    override = parse_groups(args.features)
    groups_by_market = {mk: (override if override is not None else DEFAULT_FEATURES.get(mk, ())) for mk in cfg}
    all_groups = tuple(g for g in FEATURE_GROUPS if any(g in gs for gs in groups_by_market.values()))

    params = dict(XGB_PARAMS)
    test_days = TEST_DAYS
    if args.quick:
        params["n_estimators"] = 60
        test_days = 60

    t0 = time.time()
    now = datetime.now(timezone.utc)
    markets_out: dict = {}
    adj_close: dict[str, pd.Series] = {}
    backtest: dict[str, dict] = {}
    today: list[dict] = []
    feature_names: dict[str, list[str]] = {}

    extra_syms = needed_symbols(all_groups, cfg)
    extra = {}
    if extra_syms:
        log(f"extra: downloading {len(extra_syms)} series for {all_groups}: {extra_syms}")
        extra = {s: adjust(df) for s, df in download(extra_syms, args.start).items()}

    for mkey, m in cfg.items():
        stocks = m["stocks"][:2] if args.quick else m["stocks"]
        groups = groups_by_market[mkey]
        log(f"{mkey}: feature groups {list(groups) or 'base only'}")
        ctx, data = load_market(mkey, m, stocks, extra, groups, args.start, args.twse_requests, now)

        recs, errors = [], []
        for s in stocks:
            try:
                rec, names, ac, bt = analyse(s, ctx.px.get(s["ticker"]), ctx, params, test_days)
            except Exception as e:
                log(f"{s['ticker']}: FAILED {e!r}")
                errors.append({"ticker": s["ticker"], "error": str(e)})
                continue
            if len(names) > len(feature_names.get(mkey, [])):
                feature_names[mkey] = names
            recs.append(rec)
            adj_close[rec["ticker"]] = ac
            backtest[rec["ticker"]] = pack_backtest(bt)
            today.append({
                "ticker": rec["ticker"], "market": mkey, "as_of": rec["as_of"],
                "close": rec["close"],
                **{f"h{h}": rec["forecast"][str(h)]["p_up"] for h in HORIZONS},
                **{f"h{h}_base": rec["forecast"][str(h)]["p_up_base"] for h in HORIZONS
                   if "p_up_base" in rec["forecast"][str(h)]},
                **{f"h{h}_actual": None for h in HORIZONS},
            })

        market: dict = {
            "label": m.get("label", mkey),
            "exchange": m.get("exchange", ""),
            "currency": m.get("currency", ""),
            "index": m["index"],
            "index_name": m.get("index_name", m["index"]),
            "as_of": max((r["as_of"] for r in recs), default=None),
            "stocks": recs,
            "errors": errors,
        }
        idx_raw = data.get(m["index"])
        if idx_raw is not None and len(idx_raw) >= 2:
            ic = idx_raw["Close"].astype(float)
            market["index_close"] = round(float(ic.iloc[-1]), 2)
            market["index_change_pct"] = round(float(ic.iloc[-1] / ic.iloc[-2] - 1) * 100, 2)
            market["index_as_of"] = ic.index[-1].date().isoformat()
        markets_out[mkey] = market

    n_ok = sum(len(m["stocks"]) for m in markets_out.values())
    if n_ok == 0:
        log("every ticker failed; leaving the existing files untouched")
        return 1

    hist = update_history(out_dir / "history.json", adj_close, today, now)
    n_log = write_log(hist, out_dir / "log.json", now)
    log(f"log.json: {n_log} resolved forecasts in the last {LOG_DAYS} trading days")

    (out_dir / "backtest.json").write_text(json.dumps({
        "generated_at": now.isoformat(timespec="seconds"),
        "test_days": test_days,
        "horizons": list(HORIZONS),
        "format": "packed",
        "tickers": backtest,
    }, separators=(",", ":")) + "\n", encoding="utf-8")

    snapshot = {
        "generated_at": now.isoformat(timespec="seconds"),
        "runtime_seconds": round(time.time() - t0, 1),
        "quick": bool(args.quick),
        "model": {
            "library": "xgboost",
            "version": xgb.__version__,
            "params": params,
            "horizons": list(HORIZONS),
            "features": feature_names,
            "feature_groups": {mk: list(gs) for mk, gs in groups_by_market.items()},
            "start": args.start,
            "warmup_rows": WARMUP,
            "test_days": test_days,
            "retrain_every": RETRAIN_EVERY,
            "data_source": "Yahoo Finance via yfinance " + yf.__version__
                           + (", TWSE T86" if "twse" in all_groups else ""),
        },
        "markets": markets_out,
        "track_record": summarise_history(hist),
    }
    (out_dir / "predictions.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"wrote {out_dir / 'predictions.json'} ({n_ok} stocks) in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
