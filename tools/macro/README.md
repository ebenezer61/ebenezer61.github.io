# tools/macro

The pipeline behind `macro/` (總經儀表板), `macro/flows/` (大戶流向) and `macro/ipo/`
(熱度 IPO). Four collectors, each writing one JSON file under `macro/data/` that a
page fetches. Every source is a public page or API that needs no key, and nothing
here needs a secret.

| Script | Output | Sources |
| --- | --- | --- |
| `markets.py` | `markets.json` | Yahoo Finance chart API (indices, index futures, commodities, FX, VIX, bitcoin), U.S. Treasury daily par yield curve, New York Fed EFFR and FOMC target range, BLS public API v2 (CPI, core CPI, unemployment, payrolls), and through `taiwan.py`: CBC statistics database API (overnight interbank rate, discount rate, M1B/M2, CPI, FX reserves, 10-year bond), NDC 景氣指標及燈號 and 臺灣採購經理人指數 and Customs 海關進出口貿易統計 from data.gov.tw, MOEA 外銷訂單, TWSE MI_MARGN |
| `flows.py` | `flows.json` | TWSE BFI82U, TPEx 三大法人彙總, TAIFEX 三大法人期貨 (TX), HKEX Stock Connect daily statistics, JPX 投資部門別売買状況 (weekly), CFTC Traders in Financial Futures (weekly) |
| `events.py` | `events.json`, `news/YYYY-MM.json`, `news/index.json` | `events/cb_decisions.json` and `events/calendar.json` (hand-checked), BEA release schedule, Fed/ECB/BoJ/BoE RSS, Google News RSS |
| `ipo.py` | `ipo.json` | `ipo.json` in this folder (hand-curated list), Yahoo Finance, Google News RSS |

`.github/workflows/macro.yml` runs all four at 06:37, 16:07, 21:07 and 03:37 Taipei
(the last one right after FOMC statements) and commits `macro/data/`,
`tools/macro/cache/` and `events/cb_decisions.json` as `github-actions[bot]` with the
message `macro: update YYYY-MM-DD`. The page is therefore **not live**: a number is
the last close (or last trade) when the run fetched it.

```bash
pip install -r tools/macro/requirements.txt
python tools/macro/markets.py
python tools/macro/flows.py            # first run backfills TWSE/TPEx day by day since 2020 (hours)
python tools/macro/flows.py --only tw_txf
python tools/macro/events.py
python tools/macro/ipo.py
python -m http.server                  # then open http://localhost:8000/macro/
```

## What each market actually publishes

There is no single "daily big-money flow" across markets, so `flows.py` keeps each
source at its real cadence:

- **Taiwan**, daily: net buying in NT$ by 外資 (incl. 外資自營商), 投信, 自營商 (自行買賣 + 避險),
  listed (TWSE) and OTC (TPEx), from 2020-01-02; TX futures net open interest by the
  same three groups. TAIFEX's CSV download only reaches back about three years.
- **China / Hong Kong**, daily: southbound buy and sell turnover (net = buy − sell);
  northbound turnover only, because net northbound buying has not been published
  since August 2024. HKEX keeps only recent daily files online, so the series
  starts where the files start and accumulates from there.
- **Japan**, weekly: JPX's investor-type table for the Tokyo & Nagoya markets, cash
  plus margin, net value in ¥1,000. Only the weeks listed on the JPX index page are
  read; the series accumulates from the first run (2026-09-14 week).
- **U.S.**, weekly: CFTC TFF net positions (long − short, contracts) of asset
  managers, leveraged funds and dealers in E-mini S&P 500 and E-mini Nasdaq-100.
  The U.S. has no daily public institutional-flow release.

Raw rows are cached as CSV in `cache/` and committed, so a run only asks for dates
it does not have. `*_closed.csv` lists weekdays a source answered with no data
(holidays), so they are not asked again.

## Taiwan series

`taiwan.py` adds sixteen Taiwan series (group `台灣`) to `markets.json`. All but the
margin balance are official releases that lag by weeks:

- **CBC** (`cpx.cbc.gov.tw/API/DataAPI/Get?FileName=…`, the 中央銀行統計資料庫): the
  overnight interbank rate is a daily series, but the CBC loads it once a month, so
  the latest point is the end of the previous month. EG37D01 (拆款利率), EG28D01
  (central-bank rates, one row per change), EG43M01 (10-year bond), EF01M01 (M1B/M2,
  daily-average y/y), EF07M01 (CPI y/y, FX reserves). Columns are the product of the
  table's `structure` headers, joined with `/`. The CBC's separate open-data set 6023
  (隔夜拆款利率) stopped updating in March 2026; do not switch to it.
- **NDC** 景氣指標及燈號 (data.gov.tw 6099): a zip whose address changes with each
  upload, so it is looked up on data.gov.tw every run. The unemployment rate comes
  from the same zip (`落後指標構成項目.csv`). PMI/NMI: data.gov.tw 6100, looked up the same way.
- **MOEA** 外銷訂單按地區分 (`service.moea.gov.tw/EE520/opendata/…`, the file name must be
  percent-encoded): region code `00` is the total, US$ million; the page shows y/y.
- **TWSE** MI_MARGN (`selectType=MS`): listed 融資金額 今日餘額, NT$ thousand, one request
  per trading day through `flows.daily_backfill` into `cache/tw_margin.csv`, at most
  300 new days per run. TWSE posts a day in the evening and may correct it the next
  day (the 前日餘額 column); the cache keeps the first figure.
- **Customs** 海關進出口貿易統計 (data.gov.tw 6053): exports in NT$ from 2014; the
  page shows y/y.

Python 3.13 refuses some Taiwan government certificates (no Subject Key Identifier);
`taiwan.py` mounts an adapter that keeps verification but drops `VERIFY_X509_STRICT`
for those hosts.

## Hand-maintained files

- `events/cb_decisions.json`: every policy decision since 2020 for Fed, ECB (deposit
  facility rate), BoJ, BoE, CBC Taiwan, BoK, and PBoC (1-year LPR changes, RRR cuts),
  each with the bank's own press-release URL and a `title_zh` and `title_en`. Fed rows
  are added automatically: for each FED date in `calendar.json` that has passed,
  `events.py` reads the statement (`monetaryYYYYMMDDa.htm`) and parses the target
  range. For the other banks, add a row by hand after each meeting. Rows marked `"unverified": true` are shown with a 待查證
  (unverified) badge.
- `events/calendar.json`: scheduled meetings and U.S. BLS release dates ahead, with
  `title_zh` and `title_en`.
  (bls.gov refuses automated requests, so these dates are entered by hand; BEA's
  GDP and PCE dates are read from bea.gov on every run.)
- `ipo.json`: the IPO watch list. To add a name, copy an entry; when a filed company
  prices and lists, set `status` to `listed` and fill `yahoo`, `ipo_date` and
  `offer_price`. `news_query` is the Google News search used for its headline count;
  `note_zh`/`note_en` and `sector`/`sector_en` are the two languages of its card.

## Languages

The pages are Traditional Chinese by default with an English toggle (top right, or
`?lang=en`; the choice is remembered per browser). Static text carries its English
in a `data-en` attribute that `macro/macro.js` swaps in; page scripts pick strings
with `M.t(zh, en)`; data files carry `name_en`, `title_en`, `note_en` and the like.
Headlines and IPO news stay in their original language.

## Headlines

Titles, source, time and link only, never article text. They cannot be backfilled
(RSS only carries recent items), so the archive starts on the first run and grows
one file per month in `macro/data/news/`, deduplicated by title.

## 熱度 (IPO heat)

For listed names only: the mean of four percentile ranks within the list (return
vs offer price, 1-month return, attention ratio = 5-day average traded value over
the average since listing, 7-day headline count). It ranks the names against each
other; it is not a forecast. Yahoo dates Taiwan emerging-board trading under the
same symbol, so bars before the listing date are dropped, and a close more than 20×
away from the median is treated as a bad bar (8303.T's listing day ships 5.5e10).
