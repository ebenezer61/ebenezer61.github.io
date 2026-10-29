#!/usr/bin/env python3
"""Events and headlines -> macro/data/events.json, macro/data/news/YYYY-MM.json.

Two kinds of entries, kept apart because they come from different places:

  events     central-bank decisions since 2020 (FOMC, ECB, BoJ, BoE, CBC Taiwan,
             BoK, PBoC LPR/RRR), hand-checked against each bank's own press release
             and kept in tools/macro/events/cb_decisions.json; scheduled dates ahead
             (meetings, U.S. data releases) in tools/macro/events/calendar.json; plus
             the BEA release schedule (GDP, PCE) read from bea.gov each run.
  headlines  titles and links only, never article text, from official RSS feeds
             (Fed, ECB, BoJ, BoE) and Google News RSS searches on a fixed set of
             topics. Headlines cannot be backfilled, so they accumulate from the
             first run on: one file per month under macro/data/news/, deduplicated
             by title, plus the newest 200 in events.json for the dashboard.

    python tools/macro/events.py
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from common import OUT, get, log, now_taipei, read_json, write_json

HERE = Path(__file__).resolve().parent / "events"

FEEDS = [
    ("Fed", "https://www.federalreserve.gov/feeds/press_monetary.xml", "央行"),
    ("Fed", "https://www.federalreserve.gov/feeds/speeches.xml", "央行"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html", "央行"),
    ("BoJ", "https://www.boj.or.jp/en/rss/whatsnew.xml", "央行"),
    ("BoE", "https://www.bankofengland.co.uk/rss/news", "央行"),
]

# (topic shown on the page, query, language)
TOPICS = [
    ("聯準會", "聯準會 OR FOMC OR 鮑爾 OR 華許", "zh"),
    ("美股", "美股 道瓊 那斯達克", "zh"),
    ("台股", "台股 加權指數", "zh"),
    ("日韓股", "日經 OR 韓股 KOSPI", "zh"),
    ("陸港股", "陸股 OR 港股 OR 上證", "zh"),
    ("油價黃金", "油價 OR 金價", "zh"),
    ("匯率", "新台幣 OR 美元指數 OR 日圓 匯率", "zh"),
    ("通膨就業", "美國 CPI OR 非農 OR PCE", "zh"),
    ("Fed", "Federal Reserve rates", "en"),
    ("Markets", "stock market today", "en"),
    ("Oil", "oil prices OPEC", "en"),
    ("Treasuries", "Treasury yields", "en"),
]


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_date(s):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    except Exception:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}:\d{2}))?", s)
    return f"{m.group(1)}T{m.group(2) or '00:00'}Z" if m else None


def rss(url):
    """Items of an RSS 2.0 or RSS 1.0/RDF feed as (title, link, date)."""
    root = ET.fromstring(get(url, tries=2).content)
    out = []
    for it in root.iter():
        if not it.tag.endswith("item"):
            continue
        f = {c.tag.split("}")[-1]: (c.text or "") for c in it}
        out.append((clean(f.get("title")), (f.get("link") or "").strip(),
                    parse_date(f.get("pubDate") or f.get("date")), clean(f.get("source")) or None))
    return out


def collect_headlines():
    got = []
    for src, url, topic in FEEDS:
        try:
            for title, link, when, _ in rss(url):
                got.append({"t": when, "title": title, "url": link, "source": src, "topic": topic, "official": True})
        except Exception as e:
            log(f"feed {src} {url}: {e}")
        time.sleep(1)
    for topic, query, lang in TOPICS:
        q = urllib.parse.quote(f"{query} when:2d")
        loc = "hl=zh-TW&gl=TW&ceid=TW:zh-Hant" if lang == "zh" else "hl=en-US&gl=US&ceid=US:en"
        try:
            for title, link, when, source in rss(f"https://news.google.com/rss/search?q={q}&{loc}")[:40]:
                # Google appends " - Source" to the title; the source has its own field.
                if source and title.endswith(" - " + source):
                    title = title[: -len(source) - 3]
                got.append({"t": when, "title": title, "url": link, "source": source or "Google News",
                            "topic": topic, "official": False})
        except Exception as e:
            log(f"news {topic}: {e}")
        time.sleep(1)
    return [g for g in got if g["t"] and g["title"]]


def archive(items):
    """Merge into macro/data/news/YYYY-MM.json, deduplicated by title; return the index."""
    by_month = {}
    for it in items:
        by_month.setdefault(it["t"][:7], []).append(it)
    for month, new in by_month.items():
        name = f"news/{month}.json"
        old = read_json(name, {"items": []})["items"]
        seen = {x["title"] for x in old}
        merged = old + [x for x in new if x["title"] not in seen and not seen.add(x["title"])]
        merged.sort(key=lambda x: x["t"], reverse=True)
        write_json(name, {"month": month, "items": merged})
    months = sorted((p.stem for p in (OUT / "news").glob("*.json") if p.stem != "index"), reverse=True)
    return months


def bea_schedule():
    """Upcoming BEA releases (GDP, PCE / personal income) from bea.gov/news/schedule."""
    page = get("https://www.bea.gov/news/schedule").text
    out = []
    today = dt.date.today()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        day = re.search(r'class="release-date">\s*([A-Z][a-z]+ \d{1,2})', row)
        title = re.search(r'class="release-title[^"]*"[^>]*>(.*?)</td>', row, flags=re.S)
        if not (day and title):
            continue
        title = clean(title.group(1))
        if not re.match(r"(GDP \(|Personal Income and Outlays)", title):
            continue
        d = dt.datetime.strptime(f"{day.group(1)} {today.year}", "%B %d %Y").date()
        if d < today - dt.timedelta(days=200):  # the table runs into next year
            d = d.replace(year=today.year + 1)
        if d >= today:
            zh = "美國 GDP " + title[4:] if title.startswith("GDP") else "美國 PCE 物價／個人所得 " + title.split(",")[-1].strip()
            out.append({"date": d.isoformat(), "bank": "BEA", "title_zh": zh, "title_en": "US " + title,
                        "url": "https://www.bea.gov/news/schedule"})
    return out


FED_RE = re.compile(r"decided to (maintain|raise|lower) the target range for the federal funds rate"
                    r"(?: by [\d/ -]+ percentage points?)? (?:at|to) ([\d/-]+) to ([\d/-]+) percent")


def fed_rate(s):
    """'3-3/4' -> 3.75, '4' -> 4.0"""
    whole, _, frac = s.partition("-")
    if "/" in whole:
        whole, frac = "0", whole
    n, _, d = frac.partition("/")
    return int(whole) + (int(n) / int(d) if frac else 0)


def fed_auto(decisions, calendar, today):
    """Add the FOMC decision for every scheduled meeting that has passed but has no
    row yet, parsed from the statement on federalreserve.gov
    (pressreleases/monetaryYYYYMMDDa.htm, published 2:00 p.m. New York time).
    Returns True when a row was added. Other banks' rows stay hand-entered."""
    have = {d["date"] for d in decisions if d["bank"] == "FED"}
    added = False
    for c in calendar:
        if c["bank"] != "FED" or c["date"] > today or c["date"] in have:
            continue
        url = f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{c['date'].replace('-', '')}a.htm"
        try:
            text = clean(get(url, tries=2).text)
        except Exception as e:
            log(f"fed {c['date']}: statement not out yet ({e})")
            continue
        m = FED_RE.search(text)
        if not m:
            log(f"fed {c['date']}: no decision sentence in {url}")
            continue
        lo, hi = fed_rate(m.group(2)), fed_rate(m.group(3))
        prev = [d for d in decisions if d["bank"] == "FED" and d["date"] < c["date"]]
        prev_hi = float(max(prev, key=lambda d: d["date"])["rate_after"].split("-")[1]) if prev else hi
        bp = round((hi - prev_hi) * 100)
        rng = f"{lo:.2f}–{hi:.2f}%"
        zh, en = ((f"FOMC 維持利率 {rng}", f"FOMC holds at {rng}") if bp == 0 else
                  (f"FOMC 升息 {bp}bp 至 {rng}", f"FOMC raises {bp} bp to {rng}") if bp > 0 else
                  (f"FOMC 降息 {-bp}bp 至 {rng}", f"FOMC cuts {-bp} bp to {rng}"))
        decisions.append({"date": c["date"], "bank": "FED", "type": "decision",
                          "rate_after": f"{lo:.2f}-{hi:.2f}", "change_bp": bp,
                          "title_zh": zh, "url": url, "title_en": en})
        log(f"fed {c['date']}: added {en}")
        added = True
    if added:
        decisions.sort(key=lambda d: d["date"])
        rows = ",\n".join(json.dumps(d, ensure_ascii=False) for d in decisions)
        (HERE / "cb_decisions.json").write_text(f"[\n{rows}\n]\n", encoding="utf-8")
    return added


def main():
    decisions = json.loads((HERE / "cb_decisions.json").read_text(encoding="utf-8"))
    calendar = json.loads((HERE / "calendar.json").read_text(encoding="utf-8"))
    today = dt.date.today().isoformat()
    try:
        fed_auto(decisions, calendar, today)
    except Exception as e:
        log(f"fed: {e}")
    upcoming = [c for c in calendar if c["date"] >= today]
    try:
        bea = bea_schedule()
        seen = {(c["date"], c["title_zh"]) for c in upcoming}
        upcoming += [b for b in bea if (b["date"], b["title_zh"]) not in seen]
    except Exception as e:
        log(f"bea: {e}")
    upcoming.sort(key=lambda c: c["date"])

    items = collect_headlines()
    months = archive(items)
    recent = []
    for m in months[:2]:
        recent += read_json(f"news/{m}.json", {"items": []})["items"]
    recent.sort(key=lambda x: x["t"], reverse=True)
    write_json("news/index.json", {"months": months})
    write_json("events.json", {"updated": now_taipei(),
                               "decisions": sorted(decisions, key=lambda d: d["date"]),
                               "upcoming": upcoming[:80],
                               "headlines": recent[:200]})


if __name__ == "__main__":
    main()
