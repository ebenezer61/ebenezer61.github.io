#!/usr/bin/env python3
"""IPO watch (熱度 IPO) -> macro/data/ipo.json, from the hand-curated tools/macro/ipo.json.

For every listed name: daily closes since the listing date from Yahoo, the
return against the offer price, the first-day close, the high since listing,
1-week and 1-month returns, and an attention ratio (average traded value of
the last 5 sessions over the average since listing). For every name, listed or
not: how many headlines Google News returns for it over the last 7 days, with
the five newest titles and links (titles and links only, never article text).

熱度 (heat), 0-100, for listed names only: the mean of four percentile ranks
within the list, each 0-100: return vs offer, 1-month return, attention ratio,
7-day headline count. It ranks the names against each other on public numbers;
it is not a forecast and not a sentiment model.

Yahoo dates Taiwan emerging-board (興櫃) trading under the same symbol, so
bars before the exchange listing date are dropped.

    python tools/macro/ipo.py
"""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from common import get, log, now_taipei, rnd, write_json, yahoo_daily

LIST = Path(__file__).resolve().parent / "ipo.json"


def headlines(query, lang):
    q = urllib.parse.quote(f"{query} when:7d")
    loc = "hl=zh-TW&gl=TW&ceid=TW:zh-Hant" if lang == "zh" else "hl=en-US&gl=US&ceid=US:en"
    r = get(f"https://news.google.com/rss/search?q={q}&{loc}", tries=2)
    items = ET.fromstring(r.content).findall(".//item")
    out = []
    for it in items:
        try:
            when = email.utils.parsedate_to_datetime(it.findtext("pubDate")).strftime("%Y-%m-%d")
        except Exception:
            when = None
        out.append({"title": it.findtext("title"), "url": it.findtext("link"),
                    "source": it.findtext("source"), "date": when})
    out.sort(key=lambda x: x["date"] or "", reverse=True)
    return len(items), out[:5]


def ret(closes, n):
    if len(closes) <= n:
        return None
    return closes[-1] / closes[-1 - n] - 1


def metrics(x):
    bars, meta = yahoo_daily(x["yahoo"], start=dt.date.fromisoformat(x["ipo_date"]))
    days = sorted(d for d in bars if d >= x["ipo_date"])
    # Yahoo occasionally ships a garbage bar (8303.T's listing day closes at 5.5e10);
    # drop closes more than 20x away from the median.
    if days:
        med = sorted(bars[d][0] for d in days)[len(days) // 2]
        days = [d for d in days if med / 20 < bars[d][0] < med * 20]
    if not days:
        return {}
    c = [bars[d][0] for d in days]
    tv = [(bars[d][0] * (bars[d][1] or 0)) for d in days]
    offer = x.get("offer_price")
    hi = max(c)
    avg_all = sum(tv) / len(tv) if tv else 0
    avg5 = sum(tv[-5:]) / len(tv[-5:]) if tv else 0
    return {
        "last": rnd(c[-1]), "last_date": days[-1],
        "chg_1d": ret(c, 1), "ret_1w": ret(c, 5), "ret_1m": ret(c, 21),
        "vs_offer": c[-1] / offer - 1 if offer else None,
        "day1_close": rnd(c[0]), "day1_ret": c[0] / offer - 1 if offer else None,
        "high": rnd(hi), "high_date": days[c.index(hi)], "from_high": c[-1] / hi - 1,
        "attention": avg5 / avg_all if avg_all else None,
        "sessions": len(days),
        "series": {"d0": days[0],
                   "t": [(dt.date.fromisoformat(d) - dt.date.fromisoformat(days[0])).days for d in days],
                   "v": [rnd(v) for v in c]},
        "currency": meta.get("currency") or x.get("currency"),
    }


def pct_rank(vals):
    """Percentile rank 0-100 of each value among the non-missing ones."""
    ok = sorted(v for v in vals if v is not None)
    if len(ok) < 2:
        return [50 if v is not None else None for v in vals]
    return [None if v is None else 100 * sum(o < v for o in ok) / (len(ok) - 1) for v in vals]


def main():
    spec = json.loads(LIST.read_text(encoding="utf-8"))["ipos"]
    out = []
    for x in spec:
        e = {k: x.get(k) for k in ("name", "name_zh", "market", "exchange", "yahoo", "status",
                                   "ipo_date", "offer_price", "currency", "raised", "sector",
                                   "sector_en", "note_zh", "note_en", "sources")}
        if x.get("yahoo") and x.get("ipo_date") and x["status"] == "listed":
            try:
                e.update(metrics(x))
            except Exception as err:
                log(f"{x['yahoo']}: {err}")
        try:
            e["news_7d"], e["news"] = headlines(x["news_query"], x.get("news_lang", "en"))
        except Exception as err:
            log(f"news {x['name']}: {err}")
            e["news_7d"], e["news"] = None, []
        time.sleep(1)
        out.append(e)

    listed = [e for e in out if e.get("last") is not None]
    parts = [pct_rank([e.get(k) for e in listed]) for k in ("vs_offer", "ret_1m", "attention", "news_7d")]
    for i, e in enumerate(listed):
        got = [p[i] for p in parts if p[i] is not None]
        e["heat"] = round(sum(got) / len(got)) if got else None
    for e in out:
        for k in ("chg_1d", "ret_1w", "ret_1m", "vs_offer", "day1_ret", "from_high", "attention"):
            if e.get(k) is not None:
                e[k] = round(e[k], 4)
    write_json("ipo.json", {"updated": now_taipei(), "ipos": out})


if __name__ == "__main__":
    main()
