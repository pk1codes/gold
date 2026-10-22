"""Live inputs for the India gold outlook. Each feed fails on its own."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}

YAHOO = {
    "comex": "GC=F",
    "usdinr": "INR=X",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "crude": "CL=F",
    "silver": "SI=F",
    "goldbees": "GOLDBEES.NS",
    "niftybees": "NIFTYBEES.NS",
    "nifty": "^NSEI",
}


def _get(url, timeout=20, headers=None, session=None):
    client = session or requests
    response = client.get(url, headers=headers or HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def _yahoo(symbol, session=None):
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?interval=1d&range=6mo"
    )
    last_error = None
    for _ in range(2):
        try:
            client = session or requests
            response = client.get(url, headers=HEADERS, timeout=15)
            if response.status_code == 429:
                return {"ok": False, "symbol": symbol, "error": "429 Too Many Requests", "points": []}
            response.raise_for_status()
            payload = response.json()
            result = payload["chart"]["result"][0]
            meta = result["meta"]
            quote_block = result["indicators"]["quote"][0]
            closes = quote_block.get("close") or []
            stamps = result.get("timestamp") or []
            points = []
            for stamp, close in zip(stamps, closes):
                if close is None:
                    continue
                day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
                points.append({"date": day, "close": float(close)})
            price = meta.get("regularMarketPrice")
            if price is None and points:
                price = points[-1]["close"]
            prev = meta.get("chartPreviousClose")
            change_pct = meta.get("regularMarketChangePercent")
            if change_pct is None and price and prev:
                change_pct = (float(price) / float(prev) - 1) * 100
            return {
                "ok": True,
                "symbol": symbol,
                "price": float(price) if price is not None else None,
                "change_pct": float(change_pct) if change_pct is not None else None,
                "currency": meta.get("currency"),
                "name": meta.get("shortName") or meta.get("longName") or symbol,
                "points": points,
            }
        except Exception as exc:  # noqa: BLE001 — one feed must not sink the rest
            last_error = exc
    return {"ok": False, "symbol": symbol, "error": str(last_error), "points": []}


def fetch_goldm():
    """MCX Gold Mini: front month and the next two, rupees per 10 grams.

    The official MCX site blocks this network. NiftyTrader prints the same
    three GOLDM contracts from the MCX board in the page data.
    """
    try:
        response = _get(
            "https://www.niftytrader.in/commodity-futures/goldm",
            headers={"User-Agent": UA, "Accept": "text/html"},
        )
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            response.text,
            flags=re.S,
        )
        if not match:
            raise ValueError("GOLDM page had no data block")
        import json

        payload = json.loads(match.group(1))
        raw_rows = (payload.get("props") or {}).get("pageProps", {}).get("initialContracts") or []
        contracts = []
        for row in raw_rows:
            price = row.get("ltp")
            expiry = row.get("expiry_date")
            if price in (None, "", "-") or not expiry:
                continue
            board_day = str(row.get("created_at") or "")[:10]
            board_time = str(row.get("time") or "").strip()
            contracts.append(
                {
                    "symbol": "GOLDM",
                    "expiry": str(expiry)[:10],
                    "price": float(price),
                    "change": _num(row.get("change_ltp")),
                    "change_pct": _num(row.get("ltp_per")),
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "open": _num(row.get("open")),
                    "prev_close": _num(row.get("prev_close")),
                    "oi": _num(row.get("oi")),
                    "oi_change_pct": _num(row.get("change_oi_per")),
                    "volume": _num(row.get("volume")),
                    "board_time": f"{board_day} {board_time}".strip(),
                }
            )
        contracts.sort(key=lambda row: row["expiry"])
        contracts = contracts[:3]
        if len(contracts) < 3:
            raise ValueError(f"need 3 GOLDM contracts, got {len(contracts)}")
        return {
            "ok": True,
            "source": "MCX Gold Mini via NiftyTrader",
            "note": "Rupees per 10 grams. Lot is 100 grams. Current month and the next two.",
            "contracts": contracts,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "contracts": []}


def fetch_pulse():
    try:
        response = _get("https://pulse.zerodha.com/feed.php?n=40", headers={"User-Agent": UA})
        root = ET.fromstring(response.content)
        items = []
        for node in root.findall("./channel/item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            description = re.sub(r"\s+", " ", node.findtext("description") or "").strip()
            published = node.findtext("pubDate") or ""
            when = None
            if published:
                try:
                    when = parsedate_to_datetime(published).isoformat()
                except (TypeError, ValueError):
                    when = published
            if title:
                items.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": description,
                        "published": when,
                    }
                )
        return {"ok": True, "items": items}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "items": []}


def _strip(html):
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def fetch_tijori():
    """Public market monitor. Longer horizons on this page are behind a lock."""
    try:
        response = _get(
            "https://www.tijorifinance.com/markets",
            headers={"User-Agent": UA, "Accept": "text/html"},
        )
        rows = []
        for attrs, body in re.findall(r"<tr\b([^>]*)>(.*?)</tr>", response.text, flags=re.S):
            name = ""
            name_match = re.search(
                r'class="nameofmetriccol[^"]*"[^>]*>(.*?)</div>',
                body,
                flags=re.S,
            )
            if name_match:
                name = _strip(name_match.group(1))
                name = re.sub(r"\s*\+\s*$", "", name).strip()
            if not name:
                continue
            numbers = [
                _num(raw)
                for raw in re.findall(
                    r'class="knowledge numericvalue\s*">(.*?)</td>',
                    body,
                    flags=re.S,
                )
            ]
            numbers = [value for value in numbers if value is not None]
            if len(numbers) < 4:
                continue
            wanted = ("nifty 50", "jewellery", "jewelry", "metals")
            if not any(token in name.lower() for token in wanted):
                continue
            rows.append(
                {
                    "name": name,
                    "vs_52w_high_pct": numbers[0],
                    "day_pct": numbers[1],
                    "week_pct": numbers[2],
                    "month_pct": numbers[3],
                    "three_month_pct": numbers[4] if len(numbers) > 4 else None,
                }
            )
        # The page repeats some names. Keep the first of each.
        seen = set()
        unique = []
        for row in rows:
            key = row["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        if not unique:
            return {"ok": False, "error": "no Nifty or jewellery rows on the page", "rows": []}
        return {"ok": True, "rows": unique}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "rows": []}


def fetch_india_gold():
    """Zerodha Fund House publishes a live India rupee rate per 10 grams, plus daily history."""
    try:
        response = _get(
            "https://www.zerodhafundhouse.com/live-gold-price",
            headers={"User-Agent": UA, "Accept": "text/html"},
        )
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            response.text,
            flags=re.S,
        )
        if not match:
            raise ValueError("live gold page had no data block")
        import json

        payload = json.loads(match.group(1))
        data = payload["props"]["pageProps"]["data"]
        price_data = data.get("priceData") or {}
        points = []
        for point in (data.get("performanceData") or {}).get("points") or []:
            raw_ts = point.get("ts")
            value = point.get("val")
            if not raw_ts or value is None:
                continue
            day = str(raw_ts)[:10]
            points.append({"date": day, "close": float(value)})
        ohlc = price_data.get("ohlc") or {}
        return {
            "ok": True,
            "price": float(price_data["lastPrice"]),
            "as_of": price_data.get("ts"),
            "high": _num(ohlc.get("high")),
            "low": _num(ohlc.get("low")),
            "prev_close": _num(ohlc.get("close")),
            "points": points,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "points": []}


def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _candles(payload):
    points = []
    for row in payload.get("candles") or []:
        if not row or row[4] in (None, 0):
            continue
        stamp = row[0]
        if stamp > 10**12:
            stamp = stamp / 1000
        day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        points.append({"date": day, "close": float(row[4])})
    return points


def _tape_from_points(symbol, name, points):
    if len(points) < 2:
        raise ValueError(f"no {symbol} history")
    price = points[-1]["close"]
    prev = points[-2]["close"]
    change_pct = (price / prev - 1) * 100 if prev else None
    return {
        "ok": True,
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "name": name,
        "points": points,
    }


def _groww_equity(symbol, name):
    try:
        import time

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 200 * 24 * 3600 * 1000
        url = (
            "https://groww.in/v1/api/charting_service/v2/chart/exchange/NSE/segment/CASH/"
            f"{quote(symbol)}?intervalInMinutes=1440&startTimeInMillis={start_ms}&endTimeInMillis={now_ms}"
        )
        return _tape_from_points(symbol, name, _candles(_get(url).json()))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": symbol, "error": str(exc), "points": []}


def _frankfurter_inr():
    try:
        from datetime import timedelta

        start = (datetime.now(timezone.utc).date() - timedelta(days=200)).isoformat()
        payload = _get(f"https://api.frankfurter.app/{start}..?from=USD&to=INR").json()
        points = [
            {"date": day, "close": float(rates["INR"])}
            for day, rates in sorted((payload.get("rates") or {}).items())
            if rates.get("INR")
        ]
        tape = _tape_from_points("USDINR", "Rupee per dollar", points)
        tape["currency"] = "INR"
        return tape
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": "INR=X", "error": str(exc), "points": []}


def _treasury_yield():
    try:
        import csv
        from io import StringIO

        url = (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/all/{month}?type=daily_treasury_yield_curve"
            "&field_tdr_date_value_month={month}&page&_format=csv"
        )
        today = datetime.now(timezone.utc).date()
        months = []
        cursor = today.replace(day=1)
        for _ in range(5):
            months.append(cursor.strftime("%Y%m"))
            cursor = _shift_month(cursor)
        points = []
        errors = []
        for month in months:
            try:
                text = _get(url.format(month=month), timeout=25).text
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                continue
            reader = csv.DictReader(StringIO(text))
            for row in reader:
                raw_day = (row.get("Date") or "").strip()
                raw_yield = (row.get("10 Yr") or "").strip()
                if not raw_day or not raw_yield or raw_yield == "N/A":
                    continue
                month_n, day_n, year_n = raw_day.split("/")
                points.append(
                    {
                        "date": f"{int(year_n):04d}-{int(month_n):02d}-{int(day_n):02d}",
                        "close": float(raw_yield),
                    }
                )
        points.sort(key=lambda row: row["date"])
        deduped = []
        for row in points:
            if deduped and deduped[-1]["date"] == row["date"]:
                deduped[-1] = row
            else:
                deduped.append(row)
        tape = _tape_from_points("^TNX", "US 10-year yield", deduped)
        return tape
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": "^TNX", "error": str(exc), "points": []}


def _shift_month(day):
    month = day.month - 1 or 12
    year = day.year - 1 if day.month == 1 else day.year
    return day.replace(year=year, month=month, day=1)


def _dollar_basket():
    """ICE-style dollar direction from Frankfurter rates. Level is rebased to 100."""
    try:
        import math
        from datetime import timedelta

        weights = {
            "EUR": 0.576,
            "JPY": 0.136,
            "GBP": 0.119,
            "CAD": 0.091,
            "SEK": 0.042,
            "CHF": 0.036,
        }
        start = (datetime.now(timezone.utc).date() - timedelta(days=200)).isoformat()
        codes = ",".join(weights)
        payload = _get(f"https://api.frankfurter.app/{start}..?from=USD&to={codes}").json()
        points = []
        for day, rates in sorted((payload.get("rates") or {}).items()):
            if any(code not in rates or not rates[code] for code in weights):
                continue
            level = math.exp(sum(weight * math.log(float(rates[code])) for code, weight in weights.items()))
            points.append({"date": day, "close": level})
        base = points[0]["close"]
        for row in points:
            row["close"] = row["close"] / base * 100
        return _tape_from_points("DXY", "Dollar vs majors, 100 at the start of the window", points)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": "DX-Y.NYB", "error": str(exc), "points": []}


def _gold_api(symbol, name):
    try:
        payload = _get(f"https://api.gold-api.com/price/{symbol}").json()
        price = payload.get("price")
        if price is None:
            raise ValueError("no price")
        return {
            "ok": True,
            "symbol": symbol,
            "price": float(price),
            "change_pct": None,
            "name": name,
            "points": [],
            "as_of": payload.get("updatedAt"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "symbol": symbol, "error": str(exc), "points": []}


def fetch_markets():
    """World and NSE tapes. Yahoo first; the others cover a Yahoo block."""
    import time

    out = {}
    session = requests.Session()
    blocked = False
    for key, symbol in YAHOO.items():
        if blocked:
            out[key] = {"ok": False, "symbol": symbol, "error": "Yahoo rate limit", "points": []}
            continue
        out[key] = _yahoo(symbol, session=session)
        if "429" in (out[key].get("error") or ""):
            blocked = True
        else:
            time.sleep(0.25)
    fallbacks = {
        "comex": lambda: _gold_api("XAU", "Gold spot, US dollars per troy ounce"),
        "silver": lambda: _gold_api("XAG", "Silver spot, US dollars per troy ounce"),
        "usdinr": _frankfurter_inr,
        "dxy": _dollar_basket,
        "us10y": _treasury_yield,
        "goldbees": lambda: _groww_equity("GOLDBEES", "Nippon Gold BeES"),
        "niftybees": lambda: _groww_equity("NIFTYBEES", "Nippon Nifty BeES"),
        "nifty": lambda: _groww_equity("NIFTY", "Nifty 50"),
    }
    for key, fn in fallbacks.items():
        if out.get(key, {}).get("ok"):
            continue
        out[key] = fn()
    return out


def gather():
    jobs = {"markets": fetch_markets}
    jobs["goldm"] = fetch_goldm
    jobs["pulse"] = fetch_pulse
    jobs["tijori"] = fetch_tijori
    jobs["india_gold"] = fetch_india_gold
    out = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(fn): key for key, fn in jobs.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            if key == "markets" and isinstance(result, dict) and "comex" in result:
                out.update(result)
            else:
                out[key] = result
    return out
