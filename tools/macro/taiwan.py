"""Taiwan macro series for macro/index.html, merged into markets.json by markets.py.

Sources (all public, no key):
  CBC statistics database (cpx.cbc.gov.tw)   overnight interbank rate (daily rows, published
                                              monthly), discount rate, M1B/M2, CPI, FX reserves,
                                              10-year government bond yield
  NDC 景氣指標及燈號 (data.gov.tw 6099)        monitoring-indicator score and light, leading and
                                              coincident indices (trend-free)
  Customs 海關進出口貿易統計 (data.gov.tw 6053)  exports, NT$, from 2014 (y/y from 2015)
  NDC 臺灣採購經理人指數 (data.gov.tw 6100)    manufacturing PMI and non-manufacturing NMI
  MOEA 外銷訂單按地區分                        export orders, US$, total of all regions
  TWSE 信用交易統計 (MI_MARGN)                 listed margin-loan balance, daily, cached in cache/

Each function returns a list of series dicts built by markets.pack(); markets.py
calls them one by one, so a failing source keeps its previous data.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import ssl
import zipfile
from urllib.parse import quote

from requests.adapters import HTTPAdapter

from common import SESSION, START, get, log
from flows import daily_backfill, num

CBC_API = "https://cpx.cbc.gov.tw/API/DataAPI/Get"
GROUP = "台灣"


class _LaxX509(HTTPAdapter):
    """Certificates are still verified, but without Python 3.13's VERIFY_X509_STRICT:
    several Taiwan government certificates lack the Subject Key Identifier it demands."""

    def init_poolmanager(self, *a, **kw):
        ctx = ssl.create_default_context()
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
        kw["ssl_context"] = ctx
        super().init_poolmanager(*a, **kw)


for _host in ("https://cpx.cbc.gov.tw/", "https://ws.ndc.gov.tw/", "https://opendata.customs.gov.tw/",
              "https://service.moea.gov.tw/"):
    SESSION.mount(_host, _LaxX509())


def cbc_table(name):
    """One CBC table as (column names, rows). Column names join the nested
    headers of structure.Table1 x Table2 x ... with "/"; rows are [date, *values]
    with "-" turned into None."""
    j = json.loads(get(CBC_API, params={"FileName": name}).content.decode("utf-8-sig"))
    st = j["data"]["structure"]
    cols = [""]
    for k in sorted(st, key=lambda k: int(k[5:])):
        cols = [f"{a}/{b['data'].strip()}" if a else b["data"].strip() for a in cols for b in st[k]]
    rows = []
    for r in j["data"]["dataSets"]:
        vals = [None if x in ("-", "", None) else float(x) for x in r[1:]]
        rows.append([r[0], *vals])
    return cols, rows


def cbc_date(s):
    """'20260930' -> '2026-09-30'; '2026M09' -> '2026-09-01'."""
    if "M" in s:
        y, m = s.split("M")
        return f"{y}-{int(m):02d}-01"
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def cbc_col(name, col):
    """(iso date, value) rows of one column of a CBC table, oldest first."""
    cols, rows = cbc_table(name)
    i = cols.index(col)
    return sorted((cbc_date(r[0]), r[1 + i]) for r in rows)


def cbc_series(pack):
    out = []
    src = "中央銀行統計資料庫"
    out.append(pack("tw_on", "金融業隔夜拆款利率", GROUP, "%", f"{src} (EG37D01)", "d",
                    cbc_col("EG37D01", "隔夜-加權平均/原始值")))
    # Discount rate: one row per change. Start with the rate in force on START and
    # carry the last value to today, so the step line spans the whole chart.
    disc = cbc_col("EG28D01", "重貼現")
    before = [v for d, v in disc if d < START.isoformat()]
    if before:
        disc = sorted(disc + [(START.isoformat(), before[-1])])
    if disc:
        disc.append((dt.date.today().isoformat(), disc[-1][1]))
    out.append(pack("tw_disc", "央行重貼現率", GROUP, "%", f"{src} (EG28D01)", "d", disc))
    out.append(pack("tw_10y", "台灣 10 年期公債殖利率", GROUP, "%", f"{src} (EG43M01)", "m",
                    cbc_col("EG43M01", "十年期政府公債次級市場利率")))
    out.append(pack("tw_m1b", "M1B 年增率", GROUP, "%", f"{src} (EF01M01，日平均)", "m",
                    cbc_col("EF01M01", "貨幣總計數-M1B/日平均/年增率")))
    out.append(pack("tw_m2", "M2 年增率", GROUP, "%", f"{src} (EF01M01，日平均)", "m",
                    cbc_col("EF01M01", "貨幣總計數-M2/日平均/年增率")))
    out.append(pack("tw_cpi", "台灣 CPI 年增率", GROUP, "%", f"{src} (EF07M01，主計總處)", "m",
                    cbc_col("EF07M01", "消費者物價指數年增率")))
    reserves = [(d, v / 100) for d, v in cbc_col("EF07M01", "外匯存底(百萬美元)") if v is not None]
    out.append(pack("tw_fxres", "外匯存底", GROUP, "億美元", f"{src} (EF07M01)", "m", reserves))
    return [s for s in out if s]


def data_gov_url(dataset):
    """Download address of a data.gov.tw dataset's first resource. NDC's files get a
    new address with each upload, so it is looked up every run."""
    meta = get(f"https://data.gov.tw/api/v2/rest/dataset/{dataset}").json()
    return meta["result"]["distribution"][0]["resourceDownloadUrl"]


def ym_col(rows, key):
    """(iso month, value) rows of one column of an NDC CSV whose Date is YYYYMM."""
    out = []
    for r in rows:
        v = (r.get(key) or "").strip()
        if v and v != "-":
            out.append((f"{r['Date'][:4]}-{r['Date'][4:6]}-01", float(v)))
    return sorted(out)


def ndc_series(pack):
    """NDC's monitoring indicators, and the unemployment rate from the lagging-index components."""
    z = zipfile.ZipFile(io.BytesIO(get(data_gov_url(6099)).content))

    def rows(name):
        return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))

    main, lag = rows("景氣指標與燈號.csv"), rows("落後指標構成項目.csv")
    src = "國發會 景氣指標及燈號"
    return [s for s in (
        pack("tw_signal", "景氣對策信號（分數）", GROUP, "分", src, "m", ym_col(main, "景氣對策信號綜合分數")),
        pack("tw_lead", "領先指標（不含趨勢）", GROUP, "pt", src, "m", ym_col(main, "領先指標不含趨勢指數")),
        pack("tw_coin", "同時指標（不含趨勢）", GROUP, "pt", src, "m", ym_col(main, "同時指標不含趨勢指數")),
        pack("tw_unrate", "台灣失業率", GROUP, "%", f"{src}（落後指標構成項目，主計總處）", "m",
             ym_col(lag, "失業率(%)")),
    ) if s]


def pmi_series(pack):
    rows = list(csv.DictReader(io.StringIO(get(data_gov_url(6100)).content.decode("utf-8-sig"))))
    src = "國發會 臺灣採購經理人指數（中經院編製）"
    return [s for s in (
        pack("tw_pmi", "製造業 PMI", GROUP, "pt", src, "m", ym_col(rows, "PMI")),
        pack("tw_nmi", "非製造業 NMI", GROUP, "pt", src, "m", ym_col(rows, "NMI")),
    ) if s]


def orders_series(pack):
    """Export orders in US$, all regions, against the same month a year earlier."""
    url = "https://service.moea.gov.tw/EE520/opendata/" + quote("經濟部統計處_外銷訂單_按地區分.csv")
    lvl = {}
    for r in csv.DictReader(io.StringIO(get(url).content.decode("utf-8-sig"))):
        if r.get("地區代碼") != "00" or r.get("統計項目") != "外銷訂單金額_美元":
            continue
        p = r["資料期(民國年)"]  # 'YYYMM', ROC year
        lvl[(int(p[:-2]) + 1911, int(p[-2:]))] = float(r["統計值(金額)"])
    yoy = sorted((f"{y}-{m:02d}-01", (v / lvl[(y - 1, m)] - 1) * 100)
                 for (y, m), v in lvl.items() if lvl.get((y - 1, m)))
    return [s for s in (
        pack("tw_orders_yoy", "外銷訂單年增率（美元）", GROUP, "%", "經濟部統計處 外銷訂單（按地區分，總計）", "m", yoy),
    ) if s]


MARGIN_FIELDS = ["date", "margin"]


def fetch_margin(d):
    """Listed margin-loan balance at the close of day d, in NT$ thousand."""
    j = get("https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN",
            params={"date": d.strftime("%Y%m%d"), "selectType": "MS", "response": "json"}).json()
    if j.get("stat") != "OK" or not j.get("tables"):
        return None
    for row in j["tables"][0].get("data", []):
        if row[0].startswith("融資金額"):
            return {"margin": num(row[5])}
    return None


def margin_series(pack, budget=300):
    """Day by day from 2020 through the flows.py cache (tw_margin.csv), a few hundred
    days per run until it has caught up. TWSE posts a day's figure in the evening."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    rows = daily_backfill("tw_margin", MARGIN_FIELDS, fetch_margin, budget, today)
    pts = sorted((k, float(r["margin"]) / 1e5) for k, r in rows.items() if r.get("margin"))
    if not pts:
        log("tw_margin: nothing cached yet")
    return [s for s in (
        pack("tw_margin", "上市融資餘額", GROUP, "億元", "臺灣證券交易所 信用交易統計 (MI_MARGN)", "d", pts),
    ) if s]


def customs_series(pack):
    """Exports in NT$ and their change on the same month a year earlier."""
    text = get("https://opendata.customs.gov.tw/data/6053/csv.csv").content.decode("utf-8-sig")
    lvl = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            y, m, v = int(r["年度"]) + 1911, int(r["月份"]), float(r["出口總值(新臺幣千元)"])
        except (KeyError, ValueError):
            continue
        lvl[(y, m)] = v
    yoy = sorted((f"{y}-{m:02d}-01", (v / lvl[(y - 1, m)] - 1) * 100)
                 for (y, m), v in lvl.items() if lvl.get((y - 1, m)))
    src = "財政部關務署 海關進出口貿易統計"
    return [s for s in (
        pack("tw_exp_yoy", "出口年增率（新台幣）", GROUP, "%", src, "m", yoy),
    ) if s]
