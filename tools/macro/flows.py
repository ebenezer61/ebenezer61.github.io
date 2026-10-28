#!/usr/bin/env python3
"""Big-money flows (大戶流向) by market -> macro/data/flows.json.

Each market publishes something different, at a different cadence, so the page
shows each source as what it is rather than forcing one "daily flow" number:

  Taiwan  daily   TWSE BFI82U  listed-market net buying by 外資 / 投信 / 自營商 (NT$)
          daily   TPEx 3itrdsum OTC-market net buying by the same three groups (NT$)
          daily   TAIFEX        net open interest in TAIEX futures (TXF) by the same groups (contracts);
                                TAIFEX's CSV download only reaches back about three years
  China/HK daily  HKEX Stock Connect daily statistics: southbound buy minus sell (HK$),
                                northbound turnover only (net northbound buying is no longer
                                published since August 2024)
  Japan   weekly  JPX 投資部門別売買状況, Tokyo & Nagoya markets: net buying by foreigners,
                                individuals, investment trusts, business companies, proprietary (JPY)
  U.S.    weekly  CFTC Traders in Financial Futures: net positions of asset managers, leveraged
                                funds and dealers in E-mini S&P 500 and E-mini Nasdaq-100 (contracts);
                                the U.S. has no daily public institutional-flow release

Raw rows are cached as CSV under tools/macro/cache/ (committed), so a run only
asks each source for dates it does not have yet. The first run backfills from
2020; TWSE and TPEx are asked one day at a time, SLEEP seconds apart.

    python tools/macro/flows.py                 # update everything
    python tools/macro/flows.py --only tw_twse  # one source
    python tools/macro/flows.py --max 300       # cap per-day requests (resumable backfill)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import time

from common import CACHE, START, get, log, now_taipei, write_json

SLEEP = 1.5


# ---------------------------------------------------------------- CSV cache

def load(name, fields):
    path = CACHE / f"{name}.csv"
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {r[fields[0]]: r for r in csv.DictReader(f)}


def save(name, fields, rows):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({x: rows[k].get(x, "") for x in fields})


def num(s):
    return float(str(s).replace(",", "").strip() or 0)


def weekdays(since, until):
    d = since
    while d <= until:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def daily_backfill(name, fields, fetch, budget, today):
    """Ask fetch(date) for every weekday not yet cached (or marked as a holiday)."""
    rows = load(name, fields)
    holidays = load(name + "_closed", ["date"])
    last = max(rows) if rows else None
    n = 0
    for d in weekdays(START, today):
        key = d.isoformat()
        if key in rows or key in holidays:
            continue
        # Data for today appears in the afternoon; do not mark today closed early.
        if n >= budget:
            log(f"{name}: request budget reached, stopping at {key}")
            break
        n += 1
        try:
            row = fetch(d)
        except Exception as e:
            log(f"{name} {key}: {e}")
            time.sleep(SLEEP * 4)
            continue
        if row:
            rows[key] = {"date": key, **row}
        elif d < today:
            holidays[key] = {"date": key}
        if n % 25 == 0:
            save(name, fields, rows)
            save(name + "_closed", ["date"], holidays)
            log(f"{name}: {key} ({len(rows)} days)")
        time.sleep(SLEEP)
    save(name, fields, rows)
    save(name + "_closed", ["date"], holidays)
    if rows and max(rows) != last:
        log(f"{name}: now through {max(rows)}")
    return rows


# ---------------------------------------------------------------- Taiwan

TW_FIELDS = ["date", "foreign", "trust", "dealer"]


def fetch_twse(d):
    r = get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
            params={"type": "day", "dayDate": d.strftime("%Y%m%d"), "response": "json"})
    j = r.json()
    if j.get("stat") != "OK":
        return None
    net = {row[0]: num(row[3]) for row in j["data"]}
    foreign = sum(v for k, v in net.items() if k.startswith("外資"))
    dealer = sum(v for k, v in net.items() if k.startswith("自營商"))
    return {"foreign": foreign, "trust": net.get("投信", 0), "dealer": dealer}


def fetch_tpex(d):
    roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    r = get("https://www.tpex.org.tw/web/stock/3insti/3insti_summary/3itrdsum_result.php",
            params={"l": "zh-tw", "t": "D", "d": roc, "o": "json"})
    tables = r.json().get("tables") or []
    if not tables or not tables[0].get("data"):
        return None
    net = {row[0].strip().replace("　", ""): num(row[3]) for row in tables[0]["data"]}
    return {"foreign": net.get("外資及陸資合計", 0), "trust": net.get("投信", 0),
            "dealer": net.get("自營商合計", 0)}


TXF_FIELDS = ["date", "foreign", "trust", "dealer"]
TXF_NAMES = {"外資及陸資": "foreign", "外資": "foreign", "投信": "trust", "自營商": "dealer"}


def update_txf(today):
    """TAIEX futures net open interest by investor type, a month per request."""
    rows = load("tw_txf", TXF_FIELDS)
    have = max(rows) if rows else None
    # The CSV download only serves about three years back; older months return
    # an HTML error page, so the first run starts there instead of at START.
    start = dt.date.fromisoformat(have) + dt.timedelta(days=1) if have else max(START, today.replace(day=1) - dt.timedelta(days=3 * 365))
    m = dt.date(start.year, start.month, 1)
    while m <= today:
        end = (dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1) - dt.timedelta(days=1))
        try:
            # An end date with no data yet (today, before ~15:00) makes TAIFEX
            # answer with an error page for the whole range; retry up to yesterday.
            for stop in (min(end, today), min(end, today - dt.timedelta(days=1))):
                r = get("https://www.taifex.com.tw/cht/3/futContractsDateDown",
                        params={"queryStartDate": m.strftime("%Y/%m/%d"),
                                "queryEndDate": stop.strftime("%Y/%m/%d"),
                                "commodityId": "TXF"})
                text = r.content.decode("big5", errors="replace")
                if text.lstrip().startswith("日期") or stop < m:
                    break
            got = 0
            if text.lstrip().startswith("日期"):
                for rec in csv.DictReader(io.StringIO(text)):
                    who = TXF_NAMES.get((rec.get("身份別") or "").strip())
                    if not who:
                        continue
                    key = rec["日期"].strip().replace("/", "-")
                    rows.setdefault(key, {"date": key})[who] = num(rec["多空未平倉口數淨額"])
                    got += 1
            if not got and m.year >= today.year - 2:
                log(f"tw_txf {m:%Y-%m}: no rows")
        except Exception as e:
            log(f"tw_txf {m:%Y-%m}: {e}")
        m = end + dt.timedelta(days=1)
        save("tw_txf", TXF_FIELDS, rows)
        time.sleep(SLEEP)
    return rows


# ---------------------------------------------------------------- Stock Connect

HK_FIELDS = ["date", "sb_sse_buy", "sb_sse_sell", "sb_szse_buy", "sb_szse_sell",
             "nb_sse_turnover", "nb_szse_turnover"]


def fetch_hkex(d):
    r = get(f"https://www.hkex.com.hk/eng/csm/DailyStat/data_tab_daily_{d:%Y%m%d}e.js", tries=1)
    text = r.text
    data = json.loads(text[text.index("["):].rstrip().rstrip(";"))
    out = {}
    for block in data:
        if not block.get("tradingDay"):
            continue
        tab = block["content"][0]["table"]
        head = tab["schema"][0]
        vals = [num(td["td"][0][0]) for td in tab["tr"]]
        rec = dict(zip(head, vals))
        mkt = block["market"]  # "SSE Northbound", "SZSE Southbound", ...
        ex = "sse" if mkt.startswith("SSE") else "szse"
        if "Southbound" in mkt:
            out[f"sb_{ex}_buy"] = rec.get("Buy Turnover", "")
            out[f"sb_{ex}_sell"] = rec.get("Sell Turnover", "")
        else:
            out[f"nb_{ex}_turnover"] = rec.get("Total Turnover", "")
    return out or None


def update_hkex(today, budget):
    """HKEX keeps only recent daily files online, so a 404 just means 'too old or closed'."""
    rows = load("hk_connect", HK_FIELDS)
    closed = load("hk_connect_closed", ["date"])
    first = min(rows) if rows else None
    # Once the cache has a first day, older files are gone for good: never ask again.
    since = dt.date.fromisoformat(first) if first else max(START, today - dt.timedelta(days=400))
    n = 0
    for d in weekdays(since, today):
        key = d.isoformat()
        if key in rows or key in closed or n >= budget:
            continue
        n += 1
        try:
            row = fetch_hkex(d)
        except Exception:
            row = None
        if row:
            rows[key] = {"date": key, **row}
        elif d < today - dt.timedelta(days=2) and (first and key > first):
            closed[key] = {"date": key}
        time.sleep(0.5)
    save("hk_connect", HK_FIELDS, rows)
    save("hk_connect_closed", ["date"], closed)
    return rows


# ---------------------------------------------------------------- Japan

JP_FIELDS = ["week", "start", "end", "foreigners", "individuals", "trusts", "business", "proprietary"]
JP_INDEX = "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html"


def parse_jpx(content):
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(content), data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    for i, r in enumerate(rows):
        if r[1] and "Tokyo & Nagoya" in str(r[1]):
            v = rows[i + 1]  # the 金額 Value row under the shares row
            g = lambda c: float(v[c] or 0)
            return {"foreigners": g(21) + g(25), "individuals": g(13) + g(17), "trusts": g(33),
                    "business": g(37), "proprietary": g(5) + g(9)}
    raise ValueError("Tokyo & Nagoya row not found")


def update_jpx():
    rows = load("jp_weekly", JP_FIELDS)
    html = get(JP_INDEX).text
    links = sorted(set(re.findall(r'href="([^"]+stock_1_w_(\d{8})_(\d{8})\.xlsx)"', html)))
    for href, a, b in links:
        start, end = f"{a[:4]}-{a[4:6]}-{a[6:]}", f"{b[:4]}-{b[4:6]}-{b[6:]}"
        if start in rows:
            continue
        try:
            rec = parse_jpx(get("https://www.jpx.co.jp" + href).content)
        except Exception as e:
            log(f"jpx {start}: {e}")
            continue
        rows[start] = {"week": start, "start": start, "end": end, **rec}
        time.sleep(SLEEP)
    save("jp_weekly", JP_FIELDS, rows)
    return rows


# ---------------------------------------------------------------- U.S.

US_FIELDS = ["key", "date", "contract", "asset_mgr", "lev_money", "dealer", "oi"]
COT = {"13874A": "E-mini S&P 500", "209742": "E-mini Nasdaq-100"}


def update_cot():
    rows = load("us_cot", US_FIELDS)
    since = max((r["date"] for r in rows.values() if float(r["asset_mgr"] or 0)), default=START.isoformat())
    codes = ",".join(f"'{c}'" for c in COT)
    r = get("https://publicreporting.cftc.gov/resource/gpe5-46if.json",
            params={"$where": f"cftc_contract_market_code in({codes}) AND report_date_as_yyyy_mm_dd >= '{since}T00:00:00'",
                    "$order": "report_date_as_yyyy_mm_dd", "$limit": 5000})
    for x in r.json():
        d = x["report_date_as_yyyy_mm_dd"][:10]
        c = x["cftc_contract_market_code"].strip()
        # Field names are not uniform in this dataset (dealer_..._long_all vs asset_mgr_..._long).
        pos = lambda a, side: num(x.get(f"{a}_positions_{side}_all", x.get(f"{a}_positions_{side}", 0)))
        n = lambda a: pos(a, "long") - pos(a, "short")
        rows[f"{d}|{c}"] = {"key": f"{d}|{c}", "date": d, "contract": COT.get(c, c),
                            "asset_mgr": n("asset_mgr"), "lev_money": n("lev_money"),
                            "dealer": n("dealer"), "oi": num(x.get("open_interest_all", 0))}
    save("us_cot", US_FIELDS, rows)
    return rows


# ---------------------------------------------------------------- output

def cols(rows, fields, scale=1.0, key="date"):
    ks = sorted(rows)
    out = {"d": [rows[k][key] for k in ks]}
    for f in fields:
        out[f] = [round(float(rows[k][f]) / scale, 2) if rows[k].get(f) not in (None, "") else None for k in ks]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--max", type=int, default=4000, help="cap on per-day requests per source")
    a = ap.parse_args()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    want = lambda n: a.only in (None, n)
    E8 = 1e8  # NT$ / HK$ ... -> 億

    prev = {}
    try:
        from common import read_json
        prev = read_json("flows.json", {}) or {}
    except Exception:
        pass
    out = {"updated": now_taipei()}

    def run(name, fn):
        try:
            return fn()
        except Exception as e:
            log(f"{name}: {e}")
            return None

    twse = run("tw_twse", lambda: daily_backfill("tw_twse", TW_FIELDS, fetch_twse, a.max, today)) if want("tw_twse") else load("tw_twse", TW_FIELDS)
    tpex = run("tw_tpex", lambda: daily_backfill("tw_tpex", TW_FIELDS, fetch_tpex, a.max, today)) if want("tw_tpex") else load("tw_tpex", TW_FIELDS)
    txf = run("tw_txf", lambda: update_txf(today)) if want("tw_txf") else load("tw_txf", TXF_FIELDS)
    hk = run("hk", lambda: update_hkex(today, a.max)) if want("hk") else load("hk_connect", HK_FIELDS)
    jp = run("jp", update_jpx) if want("jp") else load("jp_weekly", JP_FIELDS)
    us = run("us", update_cot) if want("us") else load("us_cot", US_FIELDS)

    if twse:
        out["tw_twse"] = cols(twse, ["foreign", "trust", "dealer"], E8)
    if tpex:
        out["tw_tpex"] = cols(tpex, ["foreign", "trust", "dealer"], E8)
    if txf:
        out["tw_txf"] = cols(txf, ["foreign", "trust", "dealer"])
    if hk:
        for r in hk.values():
            for ex in ("sse", "szse"):
                b, s = r.get(f"sb_{ex}_buy"), r.get(f"sb_{ex}_sell")
                r[f"sb_{ex}_net"] = float(b) - float(s) if b not in (None, "") and s not in (None, "") else ""
        # HKEX reports turnover in millions; /100 gives 億.
        out["hk"] = cols(hk, ["sb_sse_net", "sb_szse_net", "nb_sse_turnover", "nb_szse_turnover"], 100)
    if jp:
        o = cols(jp, ["foreigners", "individuals", "trusts", "business", "proprietary"], 1e5, key="start")  # 千円 -> 億円
        o["end"] = [jp[k]["end"] for k in sorted(jp)]
        out["jp"] = o
    if us:
        out["us"] = {}
        for c in COT.values():
            sub = {k: v for k, v in us.items() if v["contract"] == c}
            out["us"][c] = cols(sub, ["asset_mgr", "lev_money", "dealer", "oi"])
    for k in ("tw_twse", "tw_tpex", "tw_txf", "hk", "jp", "us"):
        if k not in out and k in prev:
            out[k] = prev[k]
    write_json("flows.json", out)


if __name__ == "__main__":
    main()
