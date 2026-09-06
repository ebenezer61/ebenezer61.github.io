#!/usr/bin/env python3
"""Daily direction forecasts for a fixed basket of Taiwan and US blue chips.

For every ticker listed in tickers.json the script

  1. downloads daily bars from Yahoo Finance (via yfinance) back to START,
  2. builds ~35 features from price, volume and the market index,
  3. runs a walk-forward evaluation over the last TEST_DAYS trading days
     (retrain every RETRAIN_EVERY days; labels are embargoed by the horizon,
     so nothing the model trains on was unknown at forecast time),
  4. fits a final XGBoost classifier on every labelled row and scores the
     latest bar for each horizon (next trading day, five trading days),

and writes two files into stocks/data/:

  predictions.json   today's snapshot, the file the page renders
  history.json       every forecast ever published, with its realised
                     outcome filled in once the target bar exists

    python tools/stocks/predict.py            # full run, ~2 to 4 minutes
    python tools/stocks/predict.py --quick    # 2 tickers per market, smoke test
    python tools/stocks/predict.py --out DIR  # write somewhere else

The GitHub Actions workflow .github/workflows/stocks.yml runs the full
command once per weekday and commits the two files back to main.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
import yfinance as yf
from sklearn.metrics import brier_score_loss, roc_auc_score

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


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def download(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """Download raw OHLCV (+ Adj Close) for every ticker; skip the ones Yahoo drops."""
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


# --------------------------------------------------------------------------
# Features and targets
# --------------------------------------------------------------------------

def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def build_features(px: pd.DataFrame, idx_close: pd.Series | None) -> pd.DataFrame:
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
        ic = idx_close.reindex(px.index).ffill()
        ir = np.log(ic).diff()
        f["idx_ret_1d"] = ir
        f["idx_ret_5d"] = np.log(ic / ic.shift(5))
        f["idx_ret_20d"] = np.log(ic / ic.shift(20))
        f["idx_vol_20d"] = ir.rolling(20).std()
        f["idx_sma50_gap"] = ic / ic.rolling(50).mean() - 1
        f["rel_ret_20d"] = f["ret_20d"] - f["idx_ret_20d"]
    f["dow"] = px.index.dayofweek

    return f.replace([np.inf, -np.inf], np.nan)


def make_target(close: pd.Series, h: int) -> pd.Series:
    fwd = np.log(close.shift(-h) / close)
    y = (fwd > 0).astype(float)
    y[fwd.isna()] = np.nan
    return y


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

def walk_forward(X: pd.DataFrame, y: np.ndarray, h: int, params: dict, test_days: int) -> dict:
    """Rolling out-of-sample evaluation over the last `test_days` labelled rows.

    At forecast time s the label of row i is known only if i + h <= s, so each
    refit trains on rows [0, s - h] and scores rows [s, s + RETRAIN_EVERY).
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

    return {
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


def analyse(meta: dict, raw: pd.DataFrame | None, idx_close: pd.Series | None,
            params: dict, test_days: int) -> tuple[dict, list[str], pd.Series]:
    t = meta["ticker"]
    if raw is None or len(raw) < MIN_ROWS:
        raise ValueError(f"only {0 if raw is None else len(raw)} rows of history")
    px = adjust(raw)
    X = build_features(px, idx_close).iloc[WARMUP:]

    forecast = {}
    for h in HORIZONS:
        y = make_target(px["Close"], h).iloc[WARMUP:].to_numpy()
        ev = walk_forward(X, y, h, params, test_days)
        fin = final_fit(X, y, params)
        forecast[str(h)] = {**fin, "eval": ev}
        log(f"{t}: h={h} p_up={fin['p_up']:.3f} acc={ev['acc']:.3f} base={ev['baseline_acc']:.3f} auc={ev['auc']}")

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
    return rec, list(X.columns), px["Close"]


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
            by_date: dict[str, list[int]] = {}
            for f in done:
                d = by_date.setdefault(f["as_of"], [0, 0])
                d[0] += 1
                d[1] += int((f[k] > 0.5) == (f[f"{k}_actual"] == 1))
            out[g][k] = {
                "n": n,
                "hits": hits,
                "hit_rate": round(hits / n, 4) if n else None,
                "up_share": round(ups / n, 4) if n else None,
                "pending": sum(1 for f in fs if f.get(k) is not None and f.get(f"{k}_actual") is None),
                "by_date": [[d, v[0], v[1]] for d, v in sorted(by_date.items())],
            }
    first = min((f["as_of"] for f in hist.get("forecasts", [])), default=None)
    out["first_forecast"] = first
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output directory (default: stocks/data)")
    ap.add_argument("--start", default=START, help=f"first bar to request (default: {START})")
    ap.add_argument("--quick", action="store_true", help="2 tickers per market, small model, short test window")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    params = dict(XGB_PARAMS)
    test_days = TEST_DAYS
    if args.quick:
        params["n_estimators"] = 60
        test_days = 60

    t0 = time.time()
    now = datetime.now(timezone.utc)
    markets_out: dict = {}
    adj_close: dict[str, pd.Series] = {}
    today: list[dict] = []
    feature_names: list[str] = []

    for mkey, m in cfg.items():
        stocks = m["stocks"][:2] if args.quick else m["stocks"]
        tickers = [s["ticker"] for s in stocks] + [m["index"]]
        log(f"{mkey}: downloading {len(tickers)} series from {args.start}")
        data = download(tickers, args.start)

        idx_raw = data.get(m["index"])
        idx_close = adjust(idx_raw)["Close"] if idx_raw is not None else None
        if idx_close is None:
            log(f"{mkey}: index {m['index']} missing, market features skipped")

        recs, errors = [], []
        for s in stocks:
            try:
                rec, feature_names, ac = analyse(s, data.get(s["ticker"]), idx_close, params, test_days)
            except Exception as e:
                log(f"{s['ticker']}: FAILED {e!r}")
                errors.append({"ticker": s["ticker"], "error": str(e)})
                continue
            recs.append(rec)
            adj_close[rec["ticker"]] = ac
            today.append({
                "ticker": rec["ticker"], "market": mkey, "as_of": rec["as_of"],
                "close": rec["close"],
                **{f"h{h}": rec["forecast"][str(h)]["p_up"] for h in HORIZONS},
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
            "start": args.start,
            "warmup_rows": WARMUP,
            "test_days": test_days,
            "retrain_every": RETRAIN_EVERY,
            "data_source": "Yahoo Finance via yfinance " + yf.__version__,
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
