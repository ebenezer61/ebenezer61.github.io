#!/usr/bin/env python3
"""Market and macro series since 2020 for macro/index.html -> macro/data/markets.json.

Sources (all public, no key):
  Yahoo Finance chart API   indices, index futures, commodities, FX, VIX, bitcoin (daily close)
  U.S. Treasury             daily par yield curve, one CSV per year (3M, 2Y, 10Y, 30Y)
  New York Fed              effective federal funds rate and the FOMC target range (daily)
  BLS public API v2         CPI-U and core CPI (NSA index -> y/y), unemployment rate, nonfarm payrolls

Output, one entry per series:
  {"id", "name", "group", "unit", "src", "freq": "d"|"m", "d0": first date,
   "t": [days since d0], "v": [values]}
Dates are the trading date on the series' own exchange, so an Asian close and
a U.S. close with the same date are about 13 hours apart.

    python tools/macro/markets.py
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json

from common import CACHE, START, get, post, log, now_taipei, read_json, rnd, write_json, yahoo_daily

# (yahoo symbol, id, name shown on the page, group, unit)
YAHOO = [
    ("^GSPC", "spx", "S&P 500", "美股", "pt"),
    ("^IXIC", "ixic", "Nasdaq 綜合", "美股", "pt"),
    ("^DJI", "dji", "道瓊工業", "美股", "pt"),
    ("^SOX", "sox", "費城半導體", "美股", "pt"),
    ("^RUT", "rut", "Russell 2000", "美股", "pt"),
    ("ES=F", "es", "S&P 500 期貨 (ES)", "期貨", "pt"),
    ("NQ=F", "nq", "Nasdaq-100 期貨 (NQ)", "期貨", "pt"),
    ("YM=F", "ym", "道瓊期貨 (YM)", "期貨", "pt"),
    ("NIY=F", "niy", "日經期貨 (CME, 日圓)", "期貨", "pt"),
    ("^TWII", "twii", "台灣加權", "亞洲", "pt"),
    ("^N225", "n225", "日經 225", "亞洲", "pt"),
    ("^KS11", "kospi", "韓國 KOSPI", "亞洲", "pt"),
    ("^HSI", "hsi", "恒生指數", "亞洲", "pt"),
    ("000001.SS", "sse", "上證綜合", "亞洲", "pt"),
    ("399001.SZ", "szse", "深證成指", "亞洲", "pt"),
    ("^STOXX50E", "sx5e", "Euro Stoxx 50", "歐洲", "pt"),
    ("^GDAXI", "dax", "德國 DAX", "歐洲", "pt"),
    ("^FTSE", "ftse", "英國 FTSE 100", "歐洲", "pt"),
    ("^VIX", "vix", "VIX 恐慌指數", "波動", "pt"),
    ("CL=F", "wti", "WTI 原油", "商品", "USD/bbl"),
    ("BZ=F", "brent", "Brent 原油", "商品", "USD/bbl"),
    ("GC=F", "gold", "黃金期貨", "商品", "USD/oz"),
    ("SI=F", "silver", "白銀期貨", "商品", "USD/oz"),
    ("HG=F", "copper", "銅期貨", "商品", "USD/lb"),
    ("NG=F", "natgas", "天然氣期貨", "商品", "USD/MMBtu"),
    ("BTC-USD", "btc", "比特幣", "商品", "USD"),
    ("DX-Y.NYB", "dxy", "美元指數 DXY", "匯率", "pt"),
    ("TWD=X", "usdtwd", "美元/台幣", "匯率", "TWD"),
    ("JPY=X", "usdjpy", "美元/日圓", "匯率", "JPY"),
    ("KRW=X", "usdkrw", "美元/韓元", "匯率", "KRW"),
    ("CNY=X", "usdcny", "美元/人民幣", "匯率", "CNY"),
    ("EURUSD=X", "eurusd", "歐元/美元", "匯率", "USD"),
]

TREASURY = [("3 Mo", "ust3m", "美債 3 個月"), ("2 Yr", "ust2y", "美債 2 年"),
            ("10 Yr", "ust10y", "美債 10 年"), ("30 Yr", "ust30y", "美債 30 年")]

BLS = [("CUUR0000SA0", "cpi_yoy", "CPI 年增率", "yoy"),
       ("CUUR0000SA0L1E", "core_cpi_yoy", "核心 CPI 年增率", "yoy"),
       ("LNS14000000", "unrate", "失業率", "level"),
       ("CES0000000001", "nfp", "非農就業 月增", "diff")]


# English names for the page's language toggle, by series id.
NAME_EN = {
    "spx": "S&P 500",
    "ixic": "Nasdaq Composite",
    "dji": "Dow Jones",
    "sox": "PHLX Semiconductor",
    "rut": "Russell 2000",
    "es": "S&P 500 futures (ES)",
    "nq": "Nasdaq-100 futures (NQ)",
    "ym": "Dow futures (YM)",
    "niy": "Nikkei futures (CME, yen)",
    "twii": "TAIEX",
    "n225": "Nikkei 225",
    "kospi": "KOSPI",
    "hsi": "Hang Seng",
    "sse": "Shanghai Composite",
    "szse": "Shenzhen Component",
    "sx5e": "Euro Stoxx 50",
    "dax": "DAX",
    "ftse": "FTSE 100",
    "vix": "VIX",
    "wti": "WTI crude",
    "brent": "Brent crude",
    "gold": "Gold futures",
    "silver": "Silver futures",
    "copper": "Copper futures",
    "natgas": "Natural gas futures",
    "btc": "Bitcoin",
    "dxy": "US Dollar Index",
    "usdtwd": "USD/TWD",
    "usdjpy": "USD/JPY",
    "usdkrw": "USD/KRW",
    "usdcny": "USD/CNY",
    "eurusd": "EUR/USD",
    "ust3m": "Treasury 3M",
    "ust2y": "Treasury 2Y",
    "ust10y": "Treasury 10Y",
    "ust30y": "Treasury 30Y",
    "spread_10y2y": "10Y–2Y spread",
    "effr": "Effective fed funds (EFFR)",
    "fed_upper": "FOMC target, upper",
    "fed_lower": "FOMC target, lower",
    "cpi_yoy": "CPI y/y",
    "core_cpi_yoy": "Core CPI y/y",
    "unrate": "Unemployment rate",
    "nfp": "Nonfarm payrolls, m/m",
}

GROUP_EN = {
    "美股": "US equities",
    "期貨": "Index futures",
    "亞洲": "Asia",
    "歐洲": "Europe",
    "波動": "Volatility",
    "商品": "Commodities",
    "匯率": "FX",
    "利率": "Rates",
    "總經": "US macro",
}


def pack(id_, name, group, unit, src, freq, rows):
    """rows: sorted list of (iso date, value) -> compact series dict."""
    rows = [(d, v) for d, v in rows if v is not None and d >= START.isoformat()]
    if not rows:
        return None
    d0 = dt.date.fromisoformat(rows[0][0])
    return {"id": id_, "name": name, "name_en": NAME_EN.get(id_, name), "group": group,
            "group_en": GROUP_EN.get(group, group), "unit": unit, "src": src, "freq": freq,
            "d0": rows[0][0],
            "t": [(dt.date.fromisoformat(d) - d0).days for d, _ in rows],
            "v": [rnd(v) for _, v in rows]}


def yahoo_series():
    out = []
    for sym, id_, name, group, unit in YAHOO:
        try:
            bars, _ = yahoo_daily(sym)
        except Exception as e:  # keep going; the page shows what it has
            log(f"yahoo {sym}: {e}")
            continue
        s = pack(id_, name, group, unit, f"Yahoo Finance ({sym})", "d",
                 sorted((d, c) for d, (c, _) in bars.items()))
        if s:
            out.append(s)
    return out


def treasury_year(year):
    """One year of the daily par yield curve. Past years are cached on disk."""
    path = CACHE / f"treasury_{year}.csv"
    if path.exists() and year < dt.date.today().year:
        return path.read_text()
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{year}/all")
    text = get(url, params={"type": "daily_treasury_yield_curve",
                            "field_tdr_date_value": year, "page": "", "_format": "csv"}).text
    CACHE.mkdir(exist_ok=True)
    path.write_text(text)
    return text


def treasury_series():
    cols = {c: [] for c, _, _ in TREASURY}
    for year in range(START.year, dt.date.today().year + 1):
        try:
            text = treasury_year(year)
        except Exception as e:
            log(f"treasury {year}: {e}")
            continue
        for row in csv.DictReader(io.StringIO(text)):
            m, d, y = row["Date"].split("/")
            iso = f"{y}-{m}-{d}"
            for c in cols:
                if row.get(c):
                    cols[c].append((iso, float(row[c])))
    out = []
    for c, id_, name in TREASURY:
        s = pack(id_, name, "利率", "%", "U.S. Treasury 每日殖利率曲線", "d", sorted(cols[c]))
        if s:
            out.append(s)
    two, ten = dict(cols["2 Yr"]), dict(cols["10 Yr"])
    spread = sorted((d, ten[d] - two[d]) for d in ten if d in two)
    s = pack("spread_10y2y", "10年-2年 利差", "利率", "%", "U.S. Treasury（計算）", "d", spread)
    if s:
        out.append(s)
    return out


def nyfed_series():
    r = get("https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json",
            params={"startDate": START.isoformat(), "endDate": dt.date.today().isoformat()})
    rows = sorted(r.json()["refRates"], key=lambda x: x["effectiveDate"])
    src = "New York Fed (EFFR)"
    return [s for s in (
        pack("effr", "有效聯邦基金利率 EFFR", "利率", "%", src, "d",
             [(x["effectiveDate"], x["percentRate"]) for x in rows]),
        pack("fed_upper", "FOMC 目標區間上緣", "利率", "%", src, "d",
             [(x["effectiveDate"], x.get("targetRateTo")) for x in rows]),
        pack("fed_lower", "FOMC 目標區間下緣", "利率", "%", src, "d",
             [(x["effectiveDate"], x.get("targetRateFrom")) for x in rows]),
    ) if s]


def bls_series():
    """Monthly BLS series. The keyless API allows 25 queries a day, so one query."""
    this = dt.date.today().year
    body = {"seriesid": [s for s, *_ in BLS], "startyear": str(START.year - 1), "endyear": str(this)}
    r = post("https://api.bls.gov/publicAPI/v2/timeseries/data/", body)
    data = r.json()
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {data.get('message')}")
    by_id = {s["seriesID"]: s["data"] for s in data["Results"]["series"]}
    out = []
    for sid, id_, name, kind in BLS:
        pts = {}
        for x in by_id.get(sid, []):
            if not x["period"].startswith("M") or x["period"] == "M13" or x["value"] in ("-", ""):
                continue
            pts[f"{x['year']}-{x['period'][1:]}-01"] = float(x["value"])
        keys = sorted(pts)
        if kind == "level":
            rows = [(k, pts[k]) for k in keys]
            unit = "%"
        elif kind == "diff":
            rows = [(k, pts[k] - pts[p]) for p, k in zip(keys, keys[1:])]
            unit = "千人"
        else:
            rows = []
            for k in keys:
                prev = f"{int(k[:4]) - 1}{k[4:]}"
                if prev in pts:
                    rows.append((k, (pts[k] / pts[prev] - 1) * 100))
            unit = "%"
        s = pack(id_, name, "總經", unit, f"BLS ({sid})", "m", rows)
        if s:
            out.append(s)
    return out


def main():
    old = {s["id"]: s for s in (read_json("markets.json", {}) or {}).get("series", [])}
    series = []
    for name, fn in [("yahoo", yahoo_series), ("treasury", treasury_series),
                     ("nyfed", nyfed_series), ("bls", bls_series)]:
        try:
            got = fn()
        except Exception as e:
            log(f"{name}: {e}")
            got = []
        series += got
    # A source that failed this run keeps its previous data instead of vanishing.
    have = {s["id"] for s in series}
    order = [i for _, i, *_ in YAHOO] + [i for _, i, _ in TREASURY] + ["spread_10y2y", "effr", "fed_upper", "fed_lower"] + [i for _, i, *_ in BLS]
    for id_ in order:
        if id_ not in have and id_ in old:
            log(f"keeping previous {id_}")
            series.append(old[id_])
    series.sort(key=lambda s: order.index(s["id"]) if s["id"] in order else 999)
    write_json("markets.json", {"updated": now_taipei(), "series": series})


if __name__ == "__main__":
    main()
