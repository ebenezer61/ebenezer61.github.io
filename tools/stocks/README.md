# Stock forecast pipeline

Feeds the page at `/stocks/`. One script, one config file, one workflow.

| File | Purpose |
| --- | --- |
| `predict.py` | Download daily bars, build features, walk-forward evaluate, fit, write JSON |
| `tickers.json` | The basket: 12 Taiwan and 12 US blue chips plus the two market indices |
| `requirements.txt` | xgboost, yfinance, pandas, numpy, scikit-learn |
| `../../.github/workflows/stocks.yml` | Runs the script every weekday and commits the output |
| `../../stocks/data/predictions.json` | Today's snapshot, rendered by `stocks/index.html` |
| `../../stocks/data/history.json` | Every published forecast with its realised outcome |
| `../../stocks/data/backtest.json` | Per ticker and horizon: the walk-forward window's daily predicted probability, actual outcome and realised return, drawn in each row's detail panel |
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

## Schedule

The workflow runs at 23:30 UTC Monday to Friday (07:30 Taipei the next
morning), after both markets have closed, and can also be started by hand from
the Actions tab (`workflow_dispatch`). It commits as `github-actions[bot]` with
the message `stocks: daily update YYYY-MM-DD`; a day with no new bars produces
no commit. To hide those commits when reading history:

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
