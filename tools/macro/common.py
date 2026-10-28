"""Shared helpers for the macro pipeline: polite HTTP, Yahoo daily bars, JSON output.

Every source used here is a public page or API that needs no key. Requests
retry a few times with a pause, because a run that dies on one flaky
endpoint would leave the whole page stale; a source that still fails is
logged and skipped, and the previous JSON for it is kept.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "macro" / "data"
CACHE = Path(__file__).resolve().parent / "cache"
START = dt.date(2020, 1, 1)
TAIPEI = dt.timezone(dt.timedelta(hours=8))

HEADERS = {"User-Agent": "Mozilla/5.0 (macro-dashboard; +https://ebenezer61.github.io/macro/)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, *, params=None, tries=3, timeout=40, pause=3.0, **kw):
    """GET with retries; returns the Response or raises the last error."""
    err = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            err = RuntimeError(f"HTTP {r.status_code} for {r.url}")
        except requests.RequestException as e:
            err = e
        time.sleep(pause * (i + 1))
    raise err


def post(url, body, *, tries=3, timeout=60, pause=5.0):
    """POST a JSON body with retries (the BLS API)."""
    err = None
    for i in range(tries):
        try:
            r = SESSION.post(url, json=body, timeout=timeout)
            if r.status_code == 200:
                return r
            err = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as e:
            err = e
        time.sleep(pause * (i + 1))
    raise err


def yahoo_daily(symbol, start=START):
    """Daily closes (and volume) from Yahoo's chart API, as {date: (close, volume)}.

    The date is the exchange-local trading date. Rows with no close (holidays
    Yahoo pads, the bar of a session still in progress for some futures) are
    dropped.
    """
    p1 = int(dt.datetime.combine(start, dt.time(), dt.timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    r = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"period1": p1, "period2": p2, "interval": "1d", "includePrePost": "false"})
    res = r.json()["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    ts = res.get("timestamp") or []
    q = res["indicators"]["quote"][0]
    out = {}
    for t, c, v in zip(ts, q.get("close") or [], q.get("volume") or [None] * len(ts)):
        if c is None:
            continue
        d = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
        out[d] = (c, v)
    return out, res["meta"]


def now_taipei():
    return dt.datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M")


def write_json(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1024:.0f} KB)")


def read_json(name, default=None):
    path = OUT / name
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def rnd(x, nd=4):
    """Round to nd significant-ish decimals so the JSON stays small."""
    if x is None:
        return None
    a = abs(x)
    if a >= 1000:
        return round(x, 1)
    if a >= 10:
        return round(x, 2)
    return round(x, nd)
