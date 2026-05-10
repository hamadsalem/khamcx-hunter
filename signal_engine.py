"""
Signal engine - Phase 1.

Reads token_overview snapshot from REST and decides:
- REJECT: fails safety or momentum filters
- WATCH: passes all filters, score >= min_score

Phase 2 will add the second confirmation step from TXS rolling window.
"""

import time
import logging
from typing import Tuple

import config

log = logging.getLogger("signal_engine")


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(v, default=0) -> int:
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def extract_metrics(overview: dict) -> dict:
    """
    Normalize Birdeye token_overview into our internal metric dict.

    Field names per Birdeye: liquidity, mc, fdv, price, priceChange*Percent,
    v5m, v1h, v24h, trade5m, trade1h, buy5m, sell5m.
    """
    m = {}
    m["price"] = _f(overview.get("price"))
    m["liquidity_usd"] = _f(overview.get("liquidity"))
    m["mcap_usd"] = _f(overview.get("mc") or overview.get("realMc") or overview.get("fdv"))

    # Volumes
    m["volume_5m_usd"] = _f(overview.get("v5m"))
    m["volume_30m_usd"] = _f(overview.get("v30m"))
    m["volume_1h_usd"] = _f(overview.get("v1h"))
    m["volume_24h_usd"] = _f(overview.get("v24h"))

    # Transactions
    m["txns_5m"] = _i(overview.get("trade5m"))
    m["txns_1h"] = _i(overview.get("trade1h"))
    m["buys_5m"] = _i(overview.get("buy5m"))
    m["sells_5m"] = _i(overview.get("sell5m"))

    # Price changes (percent values)
    m["price_change_5m"] = _f(overview.get("priceChange5mPercent") or overview.get("priceChange5m"))
    m["price_change_30m"] = _f(overview.get("priceChange30mPercent") or overview.get("priceChange30m"))
    m["price_change_1h"] = _f(overview.get("priceChange1hPercent") or overview.get("priceChange1h"))
    m["price_change_24h"] = _f(overview.get("priceChange24hPercent") or overview.get("priceChange24h"))

    # Age - createTime may be ms or seconds
    created = overview.get("createTime") or overview.get("createdAt")
    if created:
        try:
            ts = float(created)
            if ts > 1e12:
                ts /= 1000.0
            m["age_seconds"] = max(0.0, time.time() - ts)
        except (TypeError, ValueError):
            m["age_seconds"] = None
    else:
        m["age_seconds"] = None

    # Derived
    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)

    if m["mcap_usd"] > 0:
        m["vol_to_mcap_ratio"] = round(m["volume_24h_usd"] / m["mcap_usd"], 2)
    else:
        m["vol_to_mcap_ratio"] = 0.0

    if m["volume_1h_usd"] > 0:
        baseline = m["volume_1h_usd"] / 12.0
        m["volume_acceleration"] = round(m["volume_5m_usd"] / max(baseline, 1.0), 2)
    else:
        m["volume_acceleration"] = 0.0

    if m["txns_5m"] > 0:
        m["avg_trade_usd"] = round(m["volume_5m_usd"] / m["txns_5m"], 2)
    else:
        m["avg_trade_usd"] = 0.0

    return m


def calculate_score(m: dict) -> int:
    score = 0

    # Volume acceleration (most important)
    va = m["volume_acceleration"]
    if va >= 0.7:
        score += 10
    if va >= 1.2:
        score += 10
    if va >= 1.8:
        score += 10

    # Buy/sell ratio
    bs = m["buy_sell_ratio"]
    if bs >= 1.10:
        score += 10
    if bs >= 1.30:
        score += 10
    if bs >= 1.60:
        score += 5

    # Price action (slow build favored)
    pc5 = m["price_change_5m"]
    if 3 <= pc5 <= 25:
        score += 15
    elif 25 < pc5 <= 50:
        score += 10
    elif pc5 > 0:
        score += 5

    # Txns
    if m["txns_5m"] >= 25:
        score += 10
    if m["txns_5m"] >= 50:
        score += 10

    # Avg trade
    if m["avg_trade_usd"] >= 30:
        score += 8
    if m["avg_trade_usd"] >= 75:
        score += 7

    # Liquidity
    if m["liquidity_usd"] >= 10000:
        score += 8
    if m["liquidity_usd"] >= 25000:
        score += 7

    # 30m and 1h trend bonus
    if m["price_change_30m"] > 0:
        score += 5
    if m["price_change_1h"] > 0:
        score += 5

    return score


def apply_filters(m: dict) -> Tuple[bool, str, int]:
    s = config.SAFETY_FILTERS
    f = config.MOMENTUM_FILTERS

    # Safety
    if m["liquidity_usd"] < s["min_liquidity_usd"]:
        return False, "Liquidity below floor", 0
    if m["mcap_usd"] < s["min_mcap_usd"]:
        return False, "MCap too low (rug risk)", 0
    if m["mcap_usd"] > s["max_mcap_usd"]:
        return False, "MCap above ceiling", 0
    if m["vol_to_mcap_ratio"] > s["max_volume_to_mcap_ratio"]:
        return False, "Vol/MCap suggests fake volume", 0

    age = m.get("age_seconds")
    if age is not None:
        if age < s["min_age_seconds"]:
            return False, f"Token too young ({int(age)}s)", 0
        if age > s["max_age_seconds"]:
            return False, "Token too old", 0

    # Momentum
    if m["volume_5m_usd"] < f["min_volume_5m_usd"]:
        return False, f"Vol 5m too low ({int(m['volume_5m_usd'])})", 0
    if m["txns_5m"] < f["min_txns_5m"]:
        return False, f"Txns 5m too few ({m['txns_5m']})", 0
    if m["buy_sell_ratio"] < f["min_buy_sell_ratio"]:
        return False, f"B/S ratio too low ({m['buy_sell_ratio']})", 0
    if m["avg_trade_usd"] < f["min_avg_trade_usd"]:
        return False, f"Avg trade too small (${m['avg_trade_usd']})", 0
    if m["price_change_5m"] < f["min_price_change_5m"]:
        return False, f"Price 5m below min ({m['price_change_5m']}%)", 0
    if m["price_change_5m"] > f["max_price_change_5m"]:
        return False, f"Fast pump excluded ({m['price_change_5m']}%)", 0
    if m["volume_acceleration"] < f["min_volume_acceleration"]:
        return False, f"No volume acceleration ({m['volume_acceleration']})", 0
    if m["volume_acceleration"] > f["max_volume_acceleration"]:
        return False, f"Acceleration too high ({m['volume_acceleration']})", 0

    score = calculate_score(m)
    if score < f["min_score"]:
        return False, f"Score too low ({score})", score

    return True, f"WATCH (score {score})", score
