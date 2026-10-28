# ebenezer61.github.io

Personal homepage for Anna.

Live at **<https://ebenezer61.github.io/>**, served by GitHub Pages from the `main` branch.

## Contents

| Path | Purpose |
| --- | --- |
| `index.html` | The homepage: About, Research Interests, Projects, Tools, Publications, Teaching & Outreach |
| `style.css` | All styling for the homepage. Light/dark aware, responsive, no external dependencies |
| `interpretability/` | PyCon TW 2026 poster hub: `theory/` (timeline, attention as a kernel, the word "circuit") and `practice/` (the write-up, the graph, the probes) |
| `stocks/` | Daily XGBoost direction forecasts for 12 Taiwan and 14 US blue chips; `data/` holds the JSON the page renders |
| `mock_trading/` | Trading rules backtested on one basket of Taiwan large caps since 2006, compared over any window of years; `data/backtest.json` holds the daily returns the page slices |
| `macro/` | 總經儀表板: world indices, futures, commodities, FX, yields, Fed rate, U.S. CPI and jobs since 2020, central-bank decisions, headlines; `flows/` (大戶流向) and `ipo/` (熱度 IPO) beside it; `data/` holds the JSON |
| `headset/` | Headset check: microphone meter and playback, left/right channel test, and a clarity report (speech level, noise floor, clipping, effective bandwidth); pure client-side Web Audio |
| `tools/stocks/` | The pipeline behind `stocks/`: `predict.py`, the ticker basket, requirements, its own README |
| `tools/mock_trading/` | The pipeline behind `mock_trading/`: `backtest.py`, the FinMind data layer, the basket, its own README |
| `tools/macro/` | The pipeline behind `macro/`: four collectors (markets, flows, events, IPO), the hand-checked event and IPO lists, its own README |
| `tools/figures/` | Scripts that generated the inline SVG charts on the interpretability pages |
| `.github/workflows/stocks.yml` | Runs the stock pipeline every weekday and commits the refreshed JSON |
| `.github/workflows/macro.yml` | Refreshes the macro section three times a day and commits its JSON |
| `.github/workflows/mock-trading.yml` | Reruns the mock-trading backtest every Saturday and commits the JSON |
| `.nojekyll` | Empty marker telling GitHub Pages to serve the files as-is, without a Jekyll pass |

There is still no build step, no framework, and nothing to install: everything is plain
HTML, CSS and static data, so opening a page in a browser shows exactly what visitors
see. The exceptions are `stocks/` and `mock_trading/`, whose pages fetch their JSON and
so need to be served over HTTP (`python -m http.server` from the repository root works).

### `stocks/`

<https://ebenezer61.github.io/stocks/> shows, for each stock, the probability that the
adjusted close is higher one and five trading days ahead, the model's walk-forward
accuracy over the last trading year against an always-majority baseline, and a track
record of every forecast the page has ever published, scored once the target bar
exists. The page is static; a GitHub Actions workflow does the work at 23:30 UTC every
weekday (07:30 Taipei, after both markets close) and commits `stocks/data/*.json` back
to `main` as `github-actions[bot]` with the message `stocks: daily update YYYY-MM-DD`.
Two of those files exist so the page can show predictions next to outcomes:
`backtest.json` holds the walk-forward window's daily probabilities against what
happened (drawn when a row is opened), and `log.json` the resolved forecasts of the
last 60 trading days (the forecast log). Model, features, validation and how to
change the basket are documented in `tools/stocks/README.md`.

### `mock_trading/`

<https://ebenezer61.github.io/mock_trading/> runs a few trading rules (momentum,
reversal, low volatility, a trend filter) and three benchmarks on one basket of 24
Taiwan large caps from 2006 on, with brokerage and transaction tax, and lets the
reader pick any window of years to compare growth, drawdown, CAGR, Sharpe and
calendar-year returns. Nothing places an order. Bars come from FinMind (the token
is a repository secret; never in the tree). `.github/workflows/mock-trading.yml`
reruns `tools/mock_trading/backtest.py` every Saturday and commits
`mock_trading/data/backtest.json` as `github-actions[bot]` with the message
`mock_trading: weekly backtest YYYY-MM-DD`. Engine, rules and limits are in
`tools/mock_trading/README.md`.

### `macro/`

<https://ebenezer61.github.io/macro/> is a macro dashboard in Traditional Chinese, with an
English toggle (`?lang=en`), built only from public data: tiles and a returns table for world indices, index futures, oil and
gold, currencies, Treasury yields and the Fed's target range; charts since 2020 with
central-bank decisions marked on them; U.S. CPI, payrolls and unemployment; a table of
every Fed, ECB, BoJ, BoE, CBC, BoK and PBoC decision since 2020 linked to the bank's own
release; the calendar ahead; and public headlines (titles and links only), archived
monthly from the first run. `macro/flows/` shows institutional flows at each market's
real cadence (Taiwan daily, Stock Connect daily, Japan weekly, CFTC weekly) and
`macro/ipo/` tracks hot IPOs against their offer price. It is not a live feed:
`.github/workflows/macro.yml` refreshes it at 06:37, 16:07 and 21:07 Taipei and commits
`macro/data/` as `github-actions[bot]` with the message `macro: update YYYY-MM-DD`.
Sources, cadences and the hand-maintained lists are in `tools/macro/README.md`.

### `headset/`

<https://ebenezer61.github.io/headset/> is a pre-call check for a headset, built after a
headset microphone kept making meetings hard for everyone else. Section 1 opens the
microphone (device picker, echo-cancellation / noise-suppression / auto-gain toggles),
shows RMS and peak in dBFS with a clipping flag and a waveform, and records five seconds
for playback. Section 2 routes a tone or pink noise to a hard left or hard right channel
through a `ChannelMergerNode`, with an output-device picker where the browser supports
`AudioContext.setSinkId`. Section 3 records a noise floor and a read-aloud passage
(Mandarin and English), then reports speech level, noise floor, signal-to-noise ratio,
clipped-sample share, effective bandwidth (highest third-octave band still carrying
speech) and a 4 to 8 kHz "presence" figure, with an average-spectrum chart. Bandwidth
below about 4.5 kHz is the signature of a Bluetooth headset in hands-free (HFP) mode,
which is what the other side of a call hears. Everything is one HTML file with an
inline FFT; audio never leaves the browser. Needs https or localhost for microphone
access.

### `interpretability/`

The hub at <https://ebenezer61.github.io/interpretability/> is the landing page for the
PyCon TW 2026 poster *How LLM reads your Python?* Every page reuses the site's
`style.css` (relative path adjusted for depth) and adds a page-local `<style>` block;
nothing in here affects the rest of the site. The poster's QR codes point at the hub,
`practice/p4/`, `theory/attention/` and `theory/circuits/`, so those four paths should
not move.

| Path | Purpose |
| --- | --- |
| `interpretability/index.html` | Hub: title, abstract, two cards (theory, practice), materials |
| `interpretability/poster.pdf` | The PyCon TW 2026 poster, 78 x 109 cm, built from LaTeX with tectonic |
| `interpretability/theory/index.html` | The timeline 2020 to 2025 in two tracks (how to think, how to look), with SAE and transcoder definitions |
| `interpretability/theory/attention/` | Nadaraya-Watson to softmax attention to QK/OV, one step per screen |
| `interpretability/theory/attention/tensors/` | Shapes, einsum, QK/OV, virtual heads, induction head (the one page that loads KaTeX from a CDN) |
| `interpretability/theory/circuits/` | Where the word "circuit" comes from: Zoom In 2020, the 2021 framework, attribution graphs |
| `interpretability/practice/index.html` | The write-up: prompts, findings, interventions, reproduction steps |
| `interpretability/practice/p4/` | Redirect stub to the pinned P4 graph on Neuronpedia (the QR target; the long URL lives here, not in the QR) |
| `interpretability/practice/py-*_report.json` | The raw attribution reports the tables are built from (14 KB each) |
| `interpretability/practice/traps/`, `gotchas/` | Companion pages: where the binding rules break; the expression is not enough |
| `interpretability/practice/graph/` | Anthropic's attribution-graph viewer (MIT, licence kept), served statically |
| `interpretability/practice/graph/graph_data/` | The pruned P1 attribution graph, 4.5 MB, the largest file in the repository |

The viewer needs a live connection: it loads d3, dagre and pako from
`transformer-circuits.pub` and `unpkg`. The write-up page itself has no external
dependencies and reads fine without them.

## Editing

Everything you would normally want to change lives in `index.html`, and the places
that need your attention are marked with `TODO (Anna)` comments:

```
grep -n "TODO (Anna)" index.html
```

Common edits:

- **Add a publication**: copy a whole `<li>` block inside `<ol class="entries">` and
  put the new one at the top. For work in progress, write `Under review` or `Preprint`
  where the venue would go.
- **Add a research interest**: copy an `<article class="topic">` block. The grid
  reflows to two or three columns on its own.
- **Add a link to the header**: uncomment one of the examples in `<ul class="links">`
  (GitHub, LinkedIn and CV are already stubbed out there).
- **Add a photo**: drop the image in the repository, then add an `<img>` to the hero.
  The portrait styles were removed when the layout went text-only, so this needs a
  little CSS as well.

Colors, fonts, spacing and the max content width are all CSS custom properties at the
top of `style.css`, under `:root` for light mode and the `prefers-color-scheme: dark`
block for dark mode.

## Publishing

Commit and push to `main`; GitHub Pages rebuilds within a minute or two.

```
git add -A
git commit -m "Update publications"
git push
```

If the live page still looks stale, it is browser or CDN caching rather than a failed
deploy; a hard refresh, or `curl -s https://ebenezer61.github.io/ | grep title`,
will tell you which.

## History

This repository previously hosted a devotional site built on the
[Story](https://html5up.net/story) template by HTML5 UP. It was replaced in August 2026
by the current homepage, and the template's assets (jQuery, Font Awesome, the Unsplash
imagery) were removed at that point. Everything remains in the git history if it is
ever needed again.
