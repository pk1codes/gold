"""30-day India gold scenario. A range, not a promise."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
TROY_OZ_GRAMS = 31.1034768
WEIGHTS = {
    "gold": 0.30,
    "goldm": 0.10,
    "dollar": 0.18,
    "yield": 0.14,
    "rupee": 0.15,
    "nifty": 0.08,
    "pulse": 0.05,
}
MOVE_CAP = 0.08

BULLISH = (
    "gold rise", "gold rises", "gold rose", "gold jump", "gold jumps", "gold surge",
    "gold surges", "gold gain", "gold gains", "gold rally", "gold higher", "gold climbs",
    "gold soars", "safe haven", "rate cut", "cuts rates", "rupee falls", "rupee weakens",
    "rupee slips", "rupee drops", "dollar slips", "dollar falls", "dollar weakens",
    "dovish", "gold demand", "bullish on gold",
)
BEARISH = (
    "gold fall", "gold falls", "gold fell", "gold drop", "gold drops", "gold slip",
    "gold slips", "gold ease", "gold eases", "gold lower", "gold decline", "gold tumbles",
    "gold sinks", "rate hike", "hikes rates", "hawkish", "rupee gains", "rupee rises",
    "rupee strengthens", "dollar rises", "dollar gains", "dollar strengthens",
    "dollar climbs", "sell gold",
)
SCORE_WORDS = ("gold", "bullion", "silver", "rupee", "dollar", "fed", "yield")
SHOW_WORDS = SCORE_WORDS


def build_outlook(raw):
    now = datetime.now(IST)
    comex = raw.get("comex") or {}
    usdinr = raw.get("usdinr") or {}
    intl = _intl_per_10g(comex.get("price"), usdinr.get("price"))
    bees = raw.get("goldbees") or {}
    scale = _bees_scale(bees.get("price"), intl)
    india = raw.get("india_gold") or {}
    anchor = _anchor(india, scale, intl)

    if anchor["price"] is None:
        return {
            "ok": False,
            "error": "No India gold rate and no world gold price to build one.",
            "as_of": now.isoformat(),
        }

    history, history_note = _history(
        india, comex, usdinr, raw.get("goldbees") or {}, anchor["price"], now.date()
    )
    impulses = _impulses(raw, anchor)
    available = {key: value for key, value in impulses.items() if value["used"]}
    weight_sum = sum(WEIGHTS[key] for key in available) or 1
    expected = sum(WEIGHTS[key] / weight_sum * available[key]["impulse"] for key in available)
    expected = _clamp(expected, -MOVE_CAP, MOVE_CAP)
    band_pct = _band_pct(comex.get("points") or [])
    price = anchor["price"]
    ahead = price * (1 + expected)
    low = ahead - price * band_pct
    high = ahead + price * band_pct
    # The low side of the landing zone can sit under today even when the base case is higher.
    low = max(low, price * 0.5)

    target_day = now.date() + timedelta(days=30)
    forward = []
    for step in range(0, 31):
        share = step / 30
        base = price + (ahead - price) * share
        width = price * band_pct * share
        day = now.date() + timedelta(days=step)
        forward.append(
            {
                "date": day.isoformat(),
                "base": round(base, 2),
                "low": round(base - width, 2),
                "high": round(base + width, 2),
            }
        )

    goldm = _goldm(raw.get("goldm") or {}, price, now.date())
    pulse = _pulse(raw.get("pulse") or {})
    tijori = raw.get("tijori") or {}
    drivers = _drivers(available, weight_sum, tijori)

    return {
        "ok": True,
        "as_of": now.isoformat(),
        "price_as_of": india.get("as_of"),
        "target_date": target_day.isoformat(),
        "disclaimer": (
            "This is a 30-day model scenario with a low and a high, not a promised rate "
            "and not a buy or sell instruction. Gold can move outside the band."
        ),
        "now": _quote(price, india),
        "ahead": {
            **_levels(ahead),
            "low_10g": round(low, 2),
            "high_10g": round(high, 2),
            "low_22k_10g": round(low * 22 / 24, 2),
            "high_22k_10g": round(high * 22 / 24, 2),
            "change_inr": round(ahead - price, 2),
            "change_pct": (ahead / price - 1) if price else None,
            "expected_pct": expected,
            "band_pct": band_pct,
            "mcx_10g": goldm.get("implied_30d"),
        },
        "anchor": anchor,
        "scale_check": scale,
        "series": {"history": history, "forward": forward, "history_note": history_note},
        "drivers": drivers,
        "weights_renormalised": abs(weight_sum - 1) > 0.001,
        "india": {
            "niftybees": _tape(raw.get("niftybees")),
            "nifty": _tape(raw.get("nifty")),
            "goldm": goldm,
        },
        "world": {
            "comex": _tape(comex),
            "goldbees": _tape(raw.get("goldbees")),
            "usdinr": _tape(usdinr),
            "dxy": _tape(raw.get("dxy")),
            "us10y": _tape(raw.get("us10y")),
            "crude": _tape(raw.get("crude")),
            "silver": _tape(raw.get("silver")),
            "gold_silver_ratio": _ratio(comex.get("price"), (raw.get("silver") or {}).get("price")),
        },
        "pulse": pulse,
        "tijori": {
            "ok": bool(tijori.get("ok")),
            "error": tijori.get("error"),
            "rows": tijori.get("rows") or [],
        },
        "feeds": _feeds(raw),
    }


def _intl_per_10g(usd_oz, rupee):
    if not usd_oz or not rupee:
        return None
    return (usd_oz / TROY_OZ_GRAMS) * 10 * rupee


def _bees_scale(bees_price, intl):
    if not bees_price or not intl:
        return {"ok": False}
    low, high = intl * 1.15, intl * 1.30
    center = (low + high) / 2
    scales = {factor: bees_price * factor for factor in (10, 100, 1000)}
    best = min(scales, key=lambda factor: abs(scales[factor] - center))
    return {
        "ok": True,
        "bees_price": bees_price,
        "factor": best,
        "inr_10g": scales[best],
        "band_low": low,
        "band_high": high,
        "inside_band": low <= scales[best] <= high,
        "international_10g": intl,
    }


def _anchor(india, scale, intl):
    if india.get("ok") and india.get("price"):
        return {
            "price": float(india["price"]),
            "source": "Zerodha Fund House live India gold, ₹ per 10 grams",
            "why": (
                "Gold BeES × 10, × 100, or × 1,000 does not land inside the "
                "15–30% India landed band, so the jeweller rate uses the live "
                "rupee quote per 10 grams. Gold BeES stays on the page as the ETF."
                if scale.get("ok") and not scale.get("inside_band")
                else "Live India quote per 10 grams."
            ),
        }
    if scale.get("inside_band"):
        return {
            "price": scale["inr_10g"],
            "source": f"Gold BeES × {scale['factor']}",
            "why": "Scaled Gold BeES price sits inside the India landed band.",
        }
    if intl:
        return {
            "price": intl * 1.15,
            "source": "COMEX gold × rupee × 15% India landed factor",
            "why": "Live India quote was missing. 15% is the low end of the landed band.",
        }
    return {"price": None, "source": None, "why": None}


def _history(india, comex, usdinr, goldbees, price, today):
    note = "Daily India gold rate."
    points = []
    if india.get("ok"):
        points = [
            {"date": row["date"], "inr_10g": round(row["close"], 2)}
            for row in india.get("points") or []
            if row.get("date") and row.get("close")
        ]
    cutoff = (today - timedelta(days=30)).isoformat()
    recent = [row for row in points if row["date"] >= cutoff and row["date"] <= today.isoformat()]
    if len(recent) < 10:
        scaled = _scaled_history(goldbees.get("points"), price)
        if len(scaled) >= 10:
            points = scaled
            note = "Past line follows Gold BeES, scaled to today's India rate. The live India series on this feed is monthly."
        else:
            points = _synthetic_history(comex, usdinr, price)
            if points:
                note = "Past line is world gold times the rupee, scaled to today's India rate."
    recent = [row for row in points if row["date"] >= cutoff and row["date"] <= today.isoformat()]
    if not recent and points:
        recent = points[-22:]
    if recent and recent[-1]["date"] == today.isoformat():
        recent[-1]["inr_10g"] = round(price, 2)
    elif not recent or recent[-1]["date"] < today.isoformat():
        recent.append({"date": today.isoformat(), "inr_10g": round(price, 2)})
    return recent, note


def _scaled_history(points, price):
    closes = [row for row in points or [] if row.get("date") and row.get("close")]
    if len(closes) < 10 or not closes[-1]["close"]:
        return []
    factor = price / closes[-1]["close"]
    return [
        {"date": row["date"], "inr_10g": round(row["close"] * factor, 2)}
        for row in closes
    ]


def _synthetic_history(comex, usdinr, price):
    gold_points = {row["date"]: row["close"] for row in (comex.get("points") or [])}
    fx_points = {row["date"]: row["close"] for row in (usdinr.get("points") or [])}
    days = sorted(set(gold_points) & set(fx_points))
    if not days or not gold_points.get(days[-1]) or not fx_points.get(days[-1]):
        return []
    last_intl = _intl_per_10g(gold_points[days[-1]], fx_points[days[-1]])
    if not last_intl:
        return []
    premium = price / last_intl
    return [
        {
            "date": day,
            "inr_10g": round(_intl_per_10g(gold_points[day], fx_points[day]) * premium, 2),
        }
        for day in days
    ]


def _return(points, sessions=20):
    closes = [row["close"] for row in points or [] if row.get("close")]
    if len(closes) <= sessions or closes[-1 - sessions] == 0:
        return None
    return closes[-1] / closes[-1 - sessions] - 1


def _impulses(raw, anchor):
    gold_ret = _return((raw.get("comex") or {}).get("points"))
    gold_label = "World gold, 20 sessions"
    if gold_ret is None:
        gold_ret = _return((raw.get("goldbees") or {}).get("points"))
        gold_label = "Gold BeES, 20 sessions"
    dxy_ret = _return((raw.get("dxy") or {}).get("points"))
    fx_ret = _return((raw.get("usdinr") or {}).get("points"))
    yield_now = (raw.get("us10y") or {}).get("points") or []
    yield_delta = None
    closes = [row["close"] for row in yield_now if row.get("close")]
    if len(closes) > 20:
        yield_delta = closes[-1] - closes[-21]

    goldm_rows = (raw.get("goldm") or {}).get("contracts") or []
    front_day = None
    if goldm_rows and goldm_rows[0].get("change_pct") is not None and goldm_rows[0].get("price"):
        front_day = goldm_rows[0]["change_pct"] / 100
    bees_ret = _return((raw.get("niftybees") or {}).get("points"))
    nifty_ret = _return((raw.get("nifty") or {}).get("points"))
    equity_ret = bees_ret if bees_ret is not None else nifty_ret
    pulse_score = _pulse_score((raw.get("pulse") or {}).get("items") or [])

    def pack(used, impulse, detail):
        return {"used": used, "impulse": impulse if used else 0.0, "detail": detail}

    return {
        "gold": pack(
            gold_ret is not None,
            _clamp(gold_ret or 0, -0.06, 0.06),
            _signed(gold_ret, gold_label),
        ),
        "goldm": pack(
            front_day is not None,
            _clamp((front_day or 0) * 0.35, -0.03, 0.03),
            _goldm_detail(goldm_rows),
        ),
        "dollar": pack(
            dxy_ret is not None,
            _clamp(-(dxy_ret or 0), -0.04, 0.04),
            _signed(dxy_ret, (raw.get("dxy") or {}).get("name") or "Dollar, 20 sessions"),
        ),
        "yield": pack(
            yield_delta is not None,
            _clamp(-0.04 * (yield_delta or 0), -0.04, 0.04),
            (
                f"US 10-year {closes[-1]:.2f}%, {yield_delta:+.2f} points in 20 sessions"
                if yield_delta is not None
                else "US 10-year unavailable"
            ),
        ),
        "rupee": pack(
            fx_ret is not None,
            _clamp(fx_ret or 0, -0.04, 0.04),
            _signed(fx_ret, "Rupee per dollar, 20 sessions"),
        ),
        "nifty": pack(
            equity_ret is not None,
            _clamp(-(equity_ret or 0), -0.04, 0.04),
            _nifty_detail(equity_ret),
        ),
        "pulse": pack(
            pulse_score is not None,
            _clamp((pulse_score or 0) * 0.02, -0.02, 0.02),
            (
                f"Pulse headline score {pulse_score:+.2f} (−1 soft, +1 firm)"
                if pulse_score is not None
                else "Pulse unavailable"
            ),
        ),
    }


def _nifty_detail(equity_ret):
    if equity_ret is None:
        return "Nifty BeES unavailable"
    return f"Nifty BeES {equity_ret:+.1%} over 20 sessions"


def _goldm_detail(contracts):
    if not contracts or contracts[0].get("price") is None:
        return "MCX Gold Mini unavailable"
    front = contracts[0]
    day = front.get("change_pct")
    day_bit = f", {day / 100:+.1%} today" if day is not None else ""
    return (
        f"GOLDM {front.get('expiry')} {_inr(front['price'])} per 10g{day_bit}. "
        "One day is counted at about a third, so it cannot swing the whole month."
    )


def _band_pct(points):
    closes = [row["close"] for row in points or [] if row.get("close")]
    window = []
    for index in range(21, len(closes)):
        if closes[index - 21]:
            window.append(closes[index] / closes[index - 21] - 1)
    window = window[-63:]
    if len(window) >= 8:
        mean = sum(window) / len(window)
        var = sum((value - mean) ** 2 for value in window) / len(window)
        width = math.sqrt(var)
    elif len(closes) > 21 and closes[-22]:
        width = abs(closes[-1] / closes[-22] - 1)
    else:
        width = 0.04
    return _clamp(width, 0.015, 0.12)


def _drivers(available, weight_sum, tijori):
    labels = {
        "gold": ("Gold trend", "A rising gold price lifts the India rate."),
        "goldm": (
            "MCX Gold Mini",
            "Today's MCX move leans the month a little. Later-month premium is carrying cost, shown on the ladder.",
        ),
        "dollar": ("Dollar", "A firmer dollar usually leans against gold."),
        "yield": ("US yield", "Higher yields usually lean against gold."),
        "rupee": ("Rupee", "A weaker rupee raises gold in India."),
        "nifty": ("Nifty mood", "A softer share market is a mild bid for gold."),
        "pulse": ("Pulse news", "Headlines tilt the month by a small amount."),
    }
    cards = []
    for key, (title, plain) in labels.items():
        item = available.get(key)
        if not item:
            cards.append(
                {
                    "key": key,
                    "title": title,
                    "detail": "Unavailable this hour",
                    "plain": plain,
                    "contribution_pct": None,
                }
            )
            continue
        share = WEIGHTS[key] / weight_sum
        cards.append(
            {
                "key": key,
                "title": title,
                "detail": item["detail"],
                "plain": plain,
                "contribution_pct": share * item["impulse"],
            }
        )
    jewellery = next(
        (row for row in (tijori.get("rows") or []) if "jewell" in row["name"].lower()),
        None,
    )
    if jewellery and jewellery.get("month_pct") is not None:
        cards.append(
            {
                "key": "tijori",
                "title": "Tijori jewellery",
                "detail": f"{jewellery['name']} {jewellery['month_pct']:+.2f}% over 1 month",
                "plain": "Shown as India demand context. It is not inside the weight mix.",
                "contribution_pct": None,
            }
        )
    return cards


def _goldm(feed, anchor, today):
    roles = ("This month", "Next month", "Month after")
    contracts = []
    for index, row in enumerate(feed.get("contracts") or []):
        price = row.get("price")
        expiry = (row.get("expiry") or "")[:10]
        if price is None or not expiry:
            continue
        change_pct = row.get("change_pct")
        if change_pct is not None:
            change_pct = change_pct / 100
        try:
            days_left = (datetime.fromisoformat(expiry).date() - today).days
        except ValueError:
            days_left = None
        contracts.append(
            {
                "role": roles[index] if index < len(roles) else "Later",
                "symbol": row.get("symbol") or "GOLDM",
                "expiry": expiry,
                "days_left": days_left,
                "price": price,
                "change": row.get("change"),
                "change_pct": change_pct,
                "high": row.get("high"),
                "low": row.get("low"),
                "oi": row.get("oi"),
                "oi_change_pct": (
                    row["oi_change_pct"] / 100 if row.get("oi_change_pct") is not None else None
                ),
                "volume": row.get("volume"),
                "board_time": row.get("board_time"),
                "gap_vs_india": (price - anchor) if anchor else None,
            }
        )
    carry_pct = None
    carry_days = None
    if len(contracts) >= 2 and contracts[0]["price"] and contracts[0]["days_left"] is not None:
        carry_days = (contracts[1]["days_left"] or 0) - (contracts[0]["days_left"] or 0)
        if carry_days:
            carry_pct = (contracts[1]["price"] - contracts[0]["price"]) / contracts[0]["price"]
    return {
        "ok": bool(feed.get("ok")) and len(contracts) >= 3,
        "source": feed.get("source"),
        "note": feed.get("note"),
        "error": feed.get("error"),
        "contracts": contracts[:3],
        "implied_30d": _interp_goldm(contracts, today + timedelta(days=30)),
        "carry_pct": carry_pct,
        "carry_days": carry_days,
    }


def _interp_goldm(contracts, day):
    points = []
    for row in contracts:
        try:
            points.append((datetime.fromisoformat(row["expiry"]).date(), row["price"]))
        except (TypeError, ValueError):
            continue
    points.sort()
    if not points:
        return None
    if day <= points[0][0]:
        return round(points[0][1], 2)
    if day >= points[-1][0]:
        return round(points[-1][1], 2)
    for (left_day, left_price), (right_day, right_price) in zip(points, points[1:]):
        span = (right_day - left_day).days
        if left_day <= day <= right_day and span:
            share = (day - left_day).days / span
            return round(left_price + (right_price - left_price) * share, 2)
    return None


def _pulse(feed):
    items = feed.get("items") or []
    shown = []
    for item in items:
        haystack = f"{item.get('title','')} {item.get('summary','')}".lower()
        if any(word in haystack for word in SHOW_WORDS):
            tone = _headline_tone(haystack)
            shown.append({**item, "tone": tone})
        if len(shown) == 6:
            break
    score = _pulse_score(items)
    return {
        "ok": bool(feed.get("ok")),
        "error": feed.get("error"),
        "score": score,
        "headlines": shown,
    }


def _pulse_score(items):
    scores = []
    for item in items:
        haystack = f"{item.get('title','')} {item.get('summary','')}".lower()
        if not any(word in haystack for word in SCORE_WORDS):
            continue
        scores.append(_headline_tone(haystack))
    if not scores:
        return None
    return sum(scores) / len(scores)


def _headline_tone(text):
    bull = sum(1 for phrase in BULLISH if phrase in text)
    bear = sum(1 for phrase in BEARISH if phrase in text)
    if bull > bear:
        return 1
    if bear > bull:
        return -1
    return 0


def _feeds(raw):
    names = [
        ("comex", "COMEX gold"),
        ("usdinr", "Rupee"),
        ("dxy", "Dollar"),
        ("us10y", "US 10-year"),
        ("crude", "Crude"),
        ("silver", "Silver"),
        ("goldbees", "Gold BeES"),
        ("niftybees", "Nifty BeES"),
        ("nifty", "Nifty 50"),
        ("goldm", "MCX Gold Mini"),
        ("pulse", "Zerodha Pulse"),
        ("tijori", "Tijori Finance"),
        ("india_gold", "India gold rate"),
    ]
    rows = []
    for key, label in names:
        feed = raw.get(key) or {}
        rows.append({"key": key, "label": label, "ok": bool(feed.get("ok")), "error": feed.get("error")})
    return rows


def _tape(feed):
    feed = feed or {}
    if not feed.get("ok"):
        return {"ok": False, "error": feed.get("error")}
    return {
        "ok": True,
        "price": feed.get("price"),
        "change_pct": (
            feed["change_pct"] / 100 if feed.get("change_pct") is not None else None
        ),
        "name": feed.get("name"),
        "symbol": feed.get("symbol"),
        "return_20d": _return(feed.get("points")),
    }


def _quote(price, india):
    levels = _levels(price)
    prev = india.get("prev_close") if india.get("ok") else None
    change = (price - prev) if prev else None
    return {
        **levels,
        "high": india.get("high") if india.get("ok") else None,
        "low": india.get("low") if india.get("ok") else None,
        "prev_close": prev,
        "day_change_inr": change,
        "day_change_pct": (change / prev) if change is not None and prev else None,
    }


def _levels(price):
    return {
        "inr_10g_24k": round(price, 2),
        "inr_10g_22k": round(price * 22 / 24, 2),
        "inr_1g_24k": round(price / 10, 2),
        "inr_8g_24k": round(price * 0.8, 2),
    }


def _ratio(gold, silver):
    if not gold or not silver:
        return None
    return gold / silver


def _inr(price):
    whole = str(int(round(price)))
    sign = ""
    if whole.startswith("-"):
        sign = "-"
        whole = whole[1:]
    if len(whole) <= 3:
        body = whole
    else:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while head:
            parts.append(head[-2:])
            head = head[:-2]
        body = ",".join(reversed(parts)) + "," + tail
    return f"{sign}₹{body}"


def _signed(value, label):
    if value is None:
        return f"{label} unavailable"
    return f"{label} {value:+.1%}"


def _clamp(value, low, high):
    return max(low, min(high, value))
