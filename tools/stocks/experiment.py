#!/usr/bin/env python3
"""Compare feature groups on the same walk-forward evaluation predict.py uses.

Each configuration is a set of optional feature groups on top of the base
features. Every ticker is evaluated once per configuration over the same
test window, with the production model parameters, so the differences come
from the features alone. The report pools all tickers of a market, which
makes a 500-day window worth ~6000 forecasts per market and horizon (one
standard error of accuracy about 0.65 pp); single-ticker numbers are shown
too but are far noisier (about 2.2 pp).

    python tools/stocks/experiment.py                              # base, each group alone, all
    python tools/stocks/experiment.py --configs base,us_lead,all   # a subset
    python tools/stocks/experiment.py --test-days 250 --quick      # smoke test
    python tools/stocks/experiment.py --configs base,us_lead+macro # combine groups with '+'

Writes <out>/latest.json and <out>/latest.md plus dated copies. Runs from
.github/workflows/stocks-experiment.yml on GitHub Actions; results are
committed under tools/stocks/experiments/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
import predict as P  # noqa: E402

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "experiments"
DEFAULT_CONFIGS = ["base"] + list(P.FEATURE_GROUPS) + ["all"]
TEST_DAYS = 500


def parse_config(name: str) -> tuple[str, ...]:
    if name == "base":
        return ()
    if name == "all":
        return P.FEATURE_GROUPS
    groups = tuple(g.strip() for g in name.split("+"))
    bad = [g for g in groups if g not in P.FEATURE_GROUPS]
    if bad:
        raise SystemExit(f"unknown group(s) {bad} in config '{name}'; choose from {P.FEATURE_GROUPS}")
    return tuple(g for g in P.FEATURE_GROUPS if g in groups)


def evaluate(px: pd.DataFrame, ctx: P.Context, ticker: str, groups: tuple[str, ...],
             params: dict, test_days: int) -> tuple[list[str], dict]:
    """Walk-forward metrics and per-day series for one ticker under one config."""
    X = P.build_features(px, ctx, ticker, groups).iloc[P.WARMUP:]
    out = {}
    for h in P.HORIZONS:
        c = px["Close"]
        fwd = np.log(c.shift(-h) / c).iloc[P.WARMUP:].to_numpy()
        y = P.make_target(c, h).iloc[P.WARMUP:].to_numpy()
        metrics, series = P.walk_forward(X, y, fwd, h, params, test_days)
        out[str(h)] = {"metrics": metrics, "p": series["p"], "actual": series["actual"]}
    return list(X.columns), out


def pooled(entries: list[dict]) -> dict:
    """Pool the per-day forecasts of several tickers into one score."""
    p = np.concatenate([np.asarray(e["p"], dtype=float) for e in entries])
    y = np.concatenate([np.asarray(e["actual"], dtype=float) for e in entries])
    pred = (p > 0.5).astype(float)
    conf = np.abs(p - 0.5) >= 0.10
    acc = float(np.mean(pred == y))
    return {
        "n": int(len(y)),
        "acc": round(acc, 4),
        "acc_se": round(float(np.sqrt(acc * (1 - acc) / len(y))), 4),
        "auc": round(float(roc_auc_score(y, p)), 4) if len(np.unique(y)) == 2 else None,
        "brier": round(float(brier_score_loss(y, p)), 4),
        "logloss": round(float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6))), 4),
        "up_share": round(float(np.mean(y)), 4),
        "conf_n": int(conf.sum()),
        "conf_acc": round(float(np.mean(pred[conf] == y[conf])), 4) if conf.sum() else None,
    }


def summarise(per_ticker: dict, configs: list[str], markets: dict[str, list[str]]) -> dict:
    """per_ticker[config][ticker][h] -> pooled scores per config, market ('ALL' too) and horizon."""
    out: dict = {}
    groups = dict(markets)
    groups["ALL"] = [t for ts in markets.values() for t in ts]
    for cfg in configs:
        out[cfg] = {}
        for mk, tickers in groups.items():
            out[cfg][mk] = {}
            for h in P.HORIZONS:
                entries = [per_ticker[cfg][t][str(h)] for t in tickers if t in per_ticker[cfg]]
                if not entries:
                    continue
                s = pooled(entries)
                if cfg != "base" and "base" in per_ticker:
                    accs = [(per_ticker[cfg][t][str(h)]["metrics"]["acc"],
                             per_ticker["base"][t][str(h)]["metrics"]["acc"])
                            for t in tickers if t in per_ticker[cfg] and t in per_ticker["base"]]
                    s["better"] = sum(1 for a, b in accs if a > b)
                    s["worse"] = sum(1 for a, b in accs if a < b)
                    s["mean_delta_acc"] = round(float(np.mean([a - b for a, b in accs])), 4)
                out[cfg][mk][str(h)] = s
    return out


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "–" if x is None else f"{100 * x:.{digits}f}%"


def fmt_pp(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:+.1f} pp"


def report(res: dict, labels: dict[str, str]) -> str:
    """Markdown summary of a results dict (the JSON written next to it)."""
    configs = res["configs"]
    summary = res["summary"]
    per_ticker = res["per_ticker"]
    hname = {"1": "next day", "5": "five days"}
    L = []
    L.append(f"# Feature group experiment, {res['generated_at'][:10]}")
    L.append("")
    L.append(f"Walk-forward over the last {res['test_days']} labelled trading days of each ticker "
             f"(refit every {P.RETRAIN_EVERY} days), production XGBoost parameters, "
             f"{len(res['tickers'])} tickers. Pooled accuracy counts every ticker-day of a market; "
             "one standard error is shown next to it. Groups marked TW only add nothing for US "
             "tickers, so those rows repeat the base numbers.")
    L.append("")
    L.append("Configurations:")
    for c in configs:
        L.append(f"- `{c}`: {labels[c]}")
    L.append("")
    for mk in res["markets"]:
        for h in map(str, P.HORIZONS):
            L.append(f"## {labels.get(mk, mk)}, {hname[h]}")
            L.append("")
            L.append("| config | pooled acc | ± s.e. | Δ vs base | AUC | Brier | log loss | conf. acc (n) | tickers better / worse |")
            L.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            base = summary.get("base", {}).get(mk, {}).get(h)
            best_single = None
            for c in configs:
                s = summary[c].get(mk, {}).get(h)
                if not s:
                    continue
                delta = (s["acc"] - base["acc"]) if base and c != "base" else None
                if c not in ("base", "all") and "+" not in c:
                    best_single = max(best_single or -1, s["acc"])
                bw = f"{s['better']} / {s['worse']}" if "better" in s else "–"
                conf = f"{fmt_pct(s['conf_acc'])} ({s['conf_n']})"
                L.append(f"| `{c}` | {fmt_pct(s['acc'])} | {fmt_pct(s['acc_se'])} | {fmt_pp(delta)} | "
                         f"{s['auc']:.3f} | {s['brier']:.4f} | {s['logloss']:.4f} | {conf} | {bw} |")
            L.append("")
            all_s = summary.get("all", {}).get(mk, {}).get(h)
            if all_s and best_single is not None and base:
                gap = all_s["acc"] - best_single
                if gap < -0.005:
                    L.append(f"`all` is {fmt_pp(gap)} below the best single group here: "
                             "combining the groups costs accuracy, which points to the groups interfering "
                             "(more columns for the same ~2500 training rows).")
                elif gap > 0.005:
                    L.append(f"`all` beats the best single group by {fmt_pp(gap)}: the groups add up.")
                else:
                    L.append("`all` and the best single group are within half a point of each other.")
                L.append("")
            # per-ticker accuracy table
            tickers = res["markets"][mk]
            L.append("Per ticker accuracy (noisy, about ±2 pp each):")
            L.append("")
            L.append("| ticker | " + " | ".join(f"`{c}`" for c in configs) + " | always-up |")
            L.append("| --- | " + " | ".join("---:" for _ in configs) + " | ---: |")
            for t in tickers:
                cells = []
                b = per_ticker.get("base", {}).get(t, {}).get(h, {}).get("metrics", {}).get("acc")
                for c in configs:
                    m = per_ticker.get(c, {}).get(t, {}).get(h, {}).get("metrics")
                    if not m:
                        cells.append("–")
                        continue
                    v = fmt_pct(m["acc"])
                    if c != "base" and b is not None:
                        v = f"**{v}**" if m["acc"] - b >= 0.02 else v
                    cells.append(v)
                up = per_ticker.get("base", {}).get(t, {}).get(h, {}).get("metrics", {}).get("up_share")
                L.append(f"| {t} | " + " | ".join(cells) + f" | {fmt_pct(up)} |")
            L.append("")
    L.append("Bold: at least 2 pp above base for that ticker. `conf. acc` is accuracy on the days the "
             "model was at least 10 points from 50%.")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default=",".join(DEFAULT_CONFIGS),
                    help="comma-separated configs: base, a group, groups joined with '+', or all")
    ap.add_argument("--test-days", type=int, default=TEST_DAYS)
    ap.add_argument("--markets", default=None, help="comma-separated market keys (default: all in tickers.json)")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--start", default=P.START)
    ap.add_argument("--jobs", type=int, default=P.XGB_PARAMS["n_jobs"], help="xgboost threads")
    ap.add_argument("--quick", action="store_true", help="2 tickers per market, 60 trees")
    ap.add_argument("--twse-requests", type=int, default=0, help="TWSE fetches allowed to top up the cache")
    args = ap.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    if "base" not in configs:
        configs.insert(0, "base")
    cfg_groups = {c: parse_config(c) for c in configs}
    union = tuple(g for g in P.FEATURE_GROUPS if any(g in gs for gs in cfg_groups.values()))
    labels = {"base": "price, volume and index features only (production today)",
              "us_lead": "base + same-day US session for Taiwan (S&P 500, SOX, TSMC ADR, ADR premium); TW only",
              "macro": "base + VIX, US 10y yield, dollar index, USD/TWD, oil, gold",
              "xsec": "base + rest of the basket (mean return, breadth, dispersion, relative) and US sector ETFs",
              "twse": "base + TWSE institutional net buying (foreign, trust, dealer); TW only",
              "risk": "base + US-session risk gauges (VVIX, VIX over realised vol, HYG/LQD, IWM/SPY, XLU/SPY, yield curve, copper/gold)",
              "earnings": "base + the stock's earnings calendar (days to/since the reaction bar, reaction-bar flags, last and pending EPS surprise)",
              "all": "base + every group"}
    for c in configs:
        labels.setdefault(c, "base + " + " + ".join(cfg_groups[c]))

    cfg = json.loads(P.CONFIG.read_text(encoding="utf-8"))
    market_keys = [m.strip() for m in args.markets.split(",")] if args.markets else list(cfg)
    params = dict(P.XGB_PARAMS, n_jobs=args.jobs)
    if args.quick:
        params["n_estimators"] = 60

    t0 = time.time()
    extra_syms = P.needed_symbols(union, cfg)
    extra = {}
    if extra_syms:
        P.log(f"extra: downloading {len(extra_syms)} series: {extra_syms}")
        extra = {s: P.adjust(df) for s, df in P.download(extra_syms, args.start).items()}

    per_ticker: dict[str, dict] = {c: {} for c in configs}
    features: dict[str, list[str]] = {}
    markets: dict[str, list[str]] = {}
    for mk in market_keys:
        m = cfg[mk]
        stocks = m["stocks"][:2] if args.quick else m["stocks"]
        ctx, _ = P.load_market(mk, m, stocks, extra, union, args.start, args.twse_requests)
        labels[mk] = m.get("label", mk)
        markets[mk] = []
        for s in stocks:
            t = s["ticker"]
            px = ctx.px.get(t)
            if px is None:
                P.log(f"{t}: skipped, not enough history")
                continue
            markets[mk].append(t)
            done: dict[tuple[str, ...], tuple[list[str], dict]] = {}
            for c in configs:
                groups = cfg_groups[c]
                cols = list(P.build_features(px.iloc[:300], ctx, t, groups).columns)
                key = tuple(cols)
                if key in done:                       # same columns as an earlier config (TW-only groups on US)
                    names, res = done[key]
                else:
                    names, res = evaluate(px, ctx, t, groups, params, args.test_days)
                    done[key] = (names, res)
                    P.log(f"{t} [{c}] {len(names)} features: " + "  ".join(
                        f"h{h} acc={res[str(h)]['metrics']['acc']:.3f} auc={res[str(h)]['metrics']['auc']}"
                        for h in P.HORIZONS) + f"  ({time.time() - t0:.0f}s)")
                per_ticker[c][t] = res
                features[c] = names if len(names) > len(features.get(c, [])) else features[c]

    summary = summarise(per_ticker, configs, markets)
    now = datetime.now(timezone.utc)
    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "runtime_seconds": round(time.time() - t0, 1),
        "test_days": args.test_days,
        "quick": bool(args.quick),
        "params": params,
        "configs": configs,
        "config_groups": {c: list(g) for c, g in cfg_groups.items()},
        "features": features,
        "markets": markets,
        "tickers": [t for ts in markets.values() for t in ts],
        "summary": summary,
        "per_ticker": {c: {t: {h: {"metrics": r[h]["metrics"]} for h in r} for t, r in d.items()}
                       for c, d in per_ticker.items()},
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    md = report(result, labels)
    for name in ("latest", stamp):
        (out / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        (out / f"{name}.md").write_text(md, encoding="utf-8")
    P.log(f"wrote {out / 'latest.md'} in {time.time() - t0:.0f}s")

    # Console summary
    for mk in list(markets) + ["ALL"]:
        for h in map(str, P.HORIZONS):
            row = "  ".join(f"{c}={summary[c][mk][h]['acc']:.3f}" for c in configs if h in summary[c].get(mk, {}))
            P.log(f"{mk} h{h}: {row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
