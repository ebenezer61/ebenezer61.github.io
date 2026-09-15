# Stock forecast pipeline

Feeds the page at `/stocks/`. One script, one config file, one workflow.

| File | Purpose |
| --- | --- |
| `predict.py` | Download daily bars, build features, walk-forward evaluate, fit, write JSON |
| `experiment.py` | Compare optional feature groups on the same walk-forward evaluation |
| `twse.py` | TWSE T86 institutional net-buying fetcher and its CSV cache |
| `earnings.py` | Yahoo earnings-calendar fetcher and its CSV cache |
| `tickers.json` | The basket: 12 Taiwan and 14 US blue chips plus the two market indices; US entries carry a `sector_etf` |
| `requirements.txt` | xgboost, yfinance, pandas, numpy, scikit-learn, requests |
| `cache/twse_t86.csv` | Daily foreign / trust / dealer net shares for the Taiwan basket since 2014 |
| `cache/earnings.csv` | Earnings dates, times and EPS surprises for every ticker |
| `experiments/` | Reports written by `experiment.py`: `latest.md` and `latest.json` (the page reads the JSON) plus a dated copy of the markdown |
| `../../.github/workflows/stocks.yml` | Runs the script every weekday and commits the output |
| `../../.github/workflows/stocks-experiment.yml` | Runs `experiment.py` on demand or when the feature code changes |
| `../../stocks/data/predictions.json` | Today's snapshot, rendered by `stocks/index.html` |
| `../../stocks/data/history.json` | Every published forecast with its realised outcome |
| `../../stocks/data/backtest.json` | Per ticker and horizon: the walk-forward window's daily predicted probability, actual outcome and realised return, drawn in each row's detail panel. Packed (`format: packed`): one date list per ticker, per horizon an offset, probabilities in thousandths, outcomes as a 0/1 string, returns in basis points; about 200 KB instead of 370 |
| `../../stocks/data/log.json` | The resolved forecasts of the last 60 trading days, flattened for the page's forecast log |

## What the model does

For each ticker and each horizon (1 and 5 trading days) an `XGBClassifier`
predicts whether the adjusted close will be higher than today's. Features are
computed from the ticker's own OHLCV series and the market index (TAIEX for
Taiwan, S&P 500 for the US): lagged returns, cumulative returns and volatility
over several windows, distance to moving averages, MACD, RSI, Bollinger %B,
volume ratio, intraday range and close position, 52-week position, index
returns and volatility, relative strength versus the index, and day of week.

Validation is walk-forward over the last 250 labelled trading days: every 21
days the model is refit on all rows whose label was already known at that
point (rows up to `s - h`, so a 5-day label never leaks), then scores the next
21 rows. The page shows that out-of-sample accuracy next to each probability,
together with the accuracy of always predicting the majority class over the
same window. Expect low-to-mid fifties; daily direction of a large cap is close
to a coin flip, and the point of the page is to show that honestly.

The final model is refit on every labelled row and scores the latest bar. Its
gain-based feature importances are shipped as `top_features`.

## Optional feature groups

`predict.py --features us_lead,macro` (or `all`, or `none`) adds groups on top
of the base set; `DEFAULT_FEATURES` in `predict.py` is what the daily run uses.
Every feature is aligned to what is known at 07:47 Taipei the morning after
`as_of`, when the workflow runs, and the same code builds the training rows
and the row being scored, so a group is either in both or in neither.

| Group | Scope | What it adds | Why it is legitimate |
| --- | --- | --- | --- |
| `us_lead` | TW only | S&P 500, SOX and TSMC ADR returns of the same calendar date, ADR premium over the Taipei close | The US session dated `d` closes at 04:00 Taipei on `d+1`, after the Taipei close it is paired with and before the Taipei target bar opens |
| `macro` | both | VIX level, change and 20-day z-score; US 10-year yield, dollar index, USD/TWD, crude oil, gold changes over 1 and 20 days | All dated `d` and final before the run; for Taiwan the same overnight argument as above |
| `xsec` | both | Mean return, breadth and dispersion of the other stocks in the basket over 1, 5, 20 days, this stock relative to them; for US stocks the sector ETF in `tickers.json` | Same-day closes of the same market |
| `twse` | TW only | Foreign, investment-trust and dealer net buying from the TWSE T86 report, scaled by the stock's 20-day volume, over 1, 5, 20 days; foreign buying streak; basket-wide foreign flow | TWSE publishes T86 for `d` around 15:00 on `d` |
| `risk` | both | VVIX change and z-score; VIX over realised S&P 500 volatility; 5- and 20-day changes of HYG/LQD, IWM/SPY, XLU/SPY and copper/gold; yield-curve slope (10y minus 3m) and its 20-day change | US-session closes dated `d` |
| `earnings` | both | From the stock's Yahoo earnings calendar (`cache/earnings.csv`, refreshed daily by `earnings.py`): whether the next bar or this bar is the reaction bar, trading days since and until the reaction bar (capped at 70), the last EPS surprise, and the surprise of a report released after today's close whose reaction is tomorrow | A report before 16:00 New York moves that day's bar, one at or after 16:00 moves the next bar; the forecast at 18:47 New York knows both. Yahoo's Taiwan timestamps are less reliable, so the group mainly matters for US stocks |

`experiment.py` scores every configuration on the same walk-forward window
(500 trading days by default, pooled across the basket so one standard error
of accuracy is about 0.65 pp) and writes `experiments/latest.md`. Run it
before changing `DEFAULT_FEATURES`; the `all` row versus the best single
group shows whether the groups interfere.

Decision of 2026-09-11 (`experiments/2026-09-11.md`): Taiwan uses every
group (next-day pooled accuracy 52.9% to 61.0%, 12 of 12 tickers better, no
interference between groups; `us_lead`, `macro` and `risk` carry most of it,
all three being the same overnight US-session information); US stocks keep
the base set, since no group moved their accuracy by more than one standard
error.

Whenever a market has extra groups, the daily run also fits a shadow model on
the base features and stores its probability next to the production one
(`h1_base`, `h5_base` in `history.json`). `track_record` then carries both hit
rates and `features_since`, the first forecast date with extra groups per
market, so the page draws the base-feature line and marks the switch on the
cumulative chart. The page also renders a short ablation table from
`experiments/latest.json` under Validation.

The T86 cache is filled once with `python tools/stocks/twse.py --backfill`
(about 3100 requests at 4 s each; resumable) and topped up by the daily run,
which fetches at most `TWSE_DAILY_REQUESTS` missing days. TWSE data starts
2012-05-02.

## Running it

```
pip install -r tools/stocks/requirements.txt
python tools/stocks/predict.py            # full run, 2 to 4 minutes on 2 cores
python tools/stocks/predict.py --quick    # smoke test: 2 tickers per market
python tools/stocks/predict.py --out /tmp/x   # keep the repo files untouched
```

Then preview the page with any static server from the repository root, for
example `python -m http.server 8000` and open <http://localhost:8000/stocks/>.
Opening `stocks/index.html` directly from disk does not work: the page fetches
its JSON, and browsers block `fetch` on `file://`.

### macOS (Apple Silicon)

The pipeline runs comfortably on an 8-core MacBook Air: `predict.py` in one to
two minutes, `experiment.py --jobs 8` in about 30 to 40 minutes for six
configurations at 500 days (the Air is fanless and throttles under sustained
load, but finishes). Memory stays under 1 GB. Setup:

```
brew install libomp                        # xgboost's wheel needs OpenMP; without it import fails
python3 -m venv .venv && source .venv/bin/activate
pip install -r tools/stocks/requirements.txt
```

Pushes go out as `ebenezer61` with a fine-grained token (Contents: read and
write on this repository). On macOS let the keychain hold it and pin the
username for this repository so the right token is picked:

```
git config --global credential.helper osxkeychain
git config credential.https://github.com.username ebenezer61   # inside the repo
git push origin main                                            # prompts once for the token
```

The daily update and the site stay on GitHub Actions and GitHub Pages; nothing
needs to be scheduled on the laptop.

## Schedule

The workflow runs at 23:47 UTC Monday to Friday (07:47 Taipei the next
morning), after both markets have closed, again at 03:47 UTC Tuesday to
Saturday (Yahoo sometimes publishes a US session's bar hours late), a third
time at 06:17 UTC Monday to Friday (14:17 Taipei, after the close) so the
morning's Taiwan forecasts are scored the same afternoon, and can also be
started by hand from the Actions tab (`workflow_dispatch`). A market whose
inputs for the latest session are not complete yet (Taiwan before the US
session of that date has closed) keeps its previous forecasts in
`predictions.json`; only outcomes are resolved. Runs are
idempotent: a forecast already in `history.json` for the same ticker and
`as_of` is kept.

GitHub often starts the 23:47 schedule one to two hours late, inside the
Taipei morning session, and Yahoo then returns the partial bar of that
session. `predict.py` therefore keeps only bars up to the last completed
session of each market (`tz` and `close` in `tickers.json`, plus a
20-minute settling margin), so a late run still forecasts from the last
close. Before this guard existed (2026-09-09 to 09-11) the Taiwan forecasts
were built from opening prices; those three days were removed from
`history.json` on 2026-09-14. It commits `stocks/data` and the T86
cache as `github-actions[bot]` with the message `stocks: daily update
YYYY-MM-DD`; a day with no new bars produces no commit. To hide those commits when reading history:

```
git log --invert-grep --grep='^stocks: daily update'
```

GitHub pauses scheduled workflows in repositories with no activity for 60 days;
the bot's own pushes count as activity, so the schedule keeps itself alive.

## Changing the basket

Edit `tickers.json`. Each entry needs a Yahoo Finance `ticker`, a display
`name`, and an optional `en` (the English name shown next to a Chinese one).
Taiwan listings use the `.TW` suffix. The next run picks the change up; a new
ticker starts accumulating its track record from that day.
