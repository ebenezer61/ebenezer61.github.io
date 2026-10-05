# Mock trading pipeline

Feeds the page at `/mock_trading/`: a handful of trading rules run on one basket
of Taiwan large caps over twenty years, with costs, so any window of years can be
compared against the market. No order is ever placed; the "trading" is arithmetic
on daily bars.

| File | Purpose |
| --- | --- |
| `backtest.py` | The engine and the strategies; writes `../../mock_trading/data/backtest.json` |
| `finmind.py` | Daily bars from FinMind in the yfinance layout, with Taiwan dividend, split and capital-reduction adjustment computed from the exchange's ex-date reference prices |
| `callauction.py` | Records the exchange's call-auction trial matches (試撮) live, 08:29 to 09:03 and 13:24 to 13:32, into `cache/callauction/YYYY-MM-DD.csv`; no source keeps this history |
| `tw50.json` | The 50 constituents of 0050 with their weights, a snapshot of Yuanta's published holdings on 2026-09-23; `callauction.py --list tw50` records them into `cache/callauction_tw50/` |
| `competition.json` | The stock competition's designated Top-50 (`info.json` of `tw_stock_competition`, with 7769 replaced by 5871 as announced on 2026-10-05), two of them on TPEx; `callauction.py --list competition` records them into `cache/callauction_competition/` |
| `universe.json` | The basket (24 stocks) and the two benchmarks (0050 ETF, TAIEX total return index) |
| `requirements.txt` | pandas, numpy, requests |
| `finmind_cache/` | Local response cache, ignored by git, six hours |
| `../../.github/workflows/mock-trading.yml` | Reruns the backtest every Saturday and commits the JSON |

## The engine

Bars start in 2005; every equity curve starts on the first session of 2006 so
each rule has a year of history. Every rule rebalances on the first session of
each month: signals come from every close before that session and trades fill at
that session's adjusted close, so nothing is bought with information from its
own bar. Brokerage of 0.1425% is charged each way and the securities transaction
tax of 0.3% (0.1% for the ETF) on every sale; cash earns nothing; shares are
fractional; there is no slippage. A stock becomes tradable once it has 250
sessions of history. The TAIEX total return index is drawn as-is, without costs.

The strategies live in `STRATEGIES` in `backtest.py`. Each is a function
`(hist, avail) -> {symbol: weight}` where `hist` is the adjusted-close frame up
to the previous session and `avail` the tradable symbols; weights that do not
sum to one leave the rest in cash. Families: `benchmark` (the index, the ETF,
equal weight), `rule` (momentum 12-1, one-month reversal, low volatility,
200-day trend filter) and `model`, which is empty until a model-driven rule is
added the same way.

## The data

`finmind.py` reads the token from `FINMIND_API_TOKEN`, else from the file
`FINMIND_TOKEN_FILE` names, else from `~/.config/finmind/token` or
`<workspace>/.config/finmind/token` (the directory above the repository). It is
never logged or written anywhere. In GitHub Actions the token is the repository
secret `FINMIND_API_TOKEN`.

The free tier serves unadjusted Taiwan prices, so the adjusted close is computed
here: for every ex-date the reference price over the previous close, from
`TaiwanStockDividendResult`, `TaiwanStockSplitPrice` and
`TaiwanStockCapitalReductionReferencePrice`, multiplied into every earlier close.
Checked against Yahoo's adjusted close for the basket on 2026-09-19: daily
returns agree to within 0.01% except on days where Yahoo's own Taiwan bars are
wrong (a few dates in 2016 and 2021). A registered free account gets 600
requests per hour; a full run makes about a hundred.

FinMind's capital-reduction table starts in 2011. Before that date a session
whose close moved beyond any daily price limit (7% then) with no ex-date on
record is treated as a capital reduction whose reference price was that
session's close, so the day's move is set to zero; each is logged by
`finmind.py` (`unexplained_jumps`). On the current basket that rule fires five
times: UMC 2007-10-09, Chunghwa Telecom 2008-01-09 and 2009-03-20, ASUS
2010-06-24 (the Pegatron spin-off) and Realtek 2007-06-25. Without it the
one-month reversal rule "earned" a 277% day from ASUS.

## Output

`mock_trading/data/backtest.json` (`format: log-returns-bp`): the session dates,
the universe, the trading rules, and per strategy its label, family, description,
annual one-way turnover, the weights chosen at the last rebalance, full-period
metrics, and `ret_bp`, the daily log return in basis points as integers. The
page compounds those, slices any window of years, and computes total return,
CAGR, volatility, Sharpe, max drawdown and calendar-year returns in the browser,
so a new window never needs a new run. About 200 KB.

## Running it

```
pip install -r tools/mock_trading/requirements.txt
python tools/mock_trading/finmind.py --check          # every symbol in the basket, one line each
python tools/mock_trading/backtest.py                  # ~15 s; writes mock_trading/data/backtest.json
python tools/mock_trading/backtest.py --out /tmp/x     # keep the repo file untouched
python -m http.server 8000                             # then open http://localhost:8000/mock_trading/
```

Opening `mock_trading/index.html` from disk does not work: the page fetches its
JSON and browsers block `fetch` on `file://`.

## Call-auction trial matches

Before the open (08:30 to 09:00) and the close (13:25 to 13:30) TWSE publishes
every few seconds the price and volume the auction would clear at, with the best
five bids and asks. Neither the exchange nor FinMind keeps that history, so
`callauction.py` records it from the exchange's public quote endpoint every 15
seconds, every 5 seconds from 08:57 to the open, one CSV per day, kept out of git (`.gitignore`). It runs from the user crontab on the workstation, not
from GitHub Actions, whose schedules drift by up to half an hour, more than the
auction lasts. Three lists run side by side: the backtest basket (`--list basket`,
from `universe.json`), the 0050 constituents (`--list tw50`, from
`tw50.json`, a fixed snapshot to refresh by hand after each quarterly index
review in March, June, September and December) and the stock competition's
Top-50 (`--list competition`, from `competition.json`, from 2026-10-06; edit it
when the organisers change the pool), whose predictions are due by 08:59, so
the trial matches of 08:57 to 08:59 are the last ones they can use. Recording started on 2026-09-23; it becomes a feature only after
a few months have accumulated. The open auction cannot feed the 07:47 forecasts
of `stocks/`; it suits a rule that trades at the open.

## Known limits

The basket is today's large caps, so every name survived and grew; absolute
returns are flattered and only comparisons between rules on the same basket and
window are fair. Parameters are the first ones anyone would write down, not
tuned. Daily bars only, so nothing intraday. Fractional shares and no slippage
overstate what a small account with round lots would get.
