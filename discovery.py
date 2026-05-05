"""
KHAMCX Hunter Bot - Discovery Engine V5 Multi-Source (Fixed)
=============================================================
تعديلات V5:
- تقليل calls GMGN من 8 إلى 4 لتجنب الـ 403 rate limit
- دعم MIN_SCORE_TO_ACCEPT_EARLY من config
- تحسينات بسيطة في logging و stability
- يعمل مع config_v5_fixed.py

المشكلة السابقة: GMGN كان يعطي 403 بسبب كثرة الـ parallel calls كل 8 ثواني.
الحل: قللنا الـ calls + زدنا الـ interval إلى 15s.
"""

import asyncio
import json
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("khamcx")

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DEFAULT_SAFE_RUN_SECONDS = 75
RUN_DURATION_SECONDS = min(getattr(config, "RUN_DURATION_SECONDS", DEFAULT_SAFE_RUN_SECONDS), DEFAULT_SAFE_RUN_SECONDS)
POLL_INTERVAL_SEC = getattr(config, "POLL_INTERVAL_SEC", 15)
MAX_TOKENS_PER_RUN = getattr(config, "MAX_TOKENS_PER_RUN", 150)

WATCH_SCORE_POST = getattr(config, "WATCH_SCORE_POST", 75)
WATCH_SCORE_EARLY = getattr(config, "WATCH_SCORE_EARLY", 73)
ACCEPT_SCORE_POST = getattr(config, "MIN_SCORE_TO_ACCEPT", 78)
ACCEPT_SCORE_EARLY = getattr(config, "MIN_SCORE_TO_ACCEPT_EARLY", 80)

DEXSCREENER_BASE = getattr(config, "DEXSCREENER_BASE", "https://api.dexscreener.com")
ENDPOINT_LATEST_PROFILES = getattr(config, "ENDPOINT_LATEST_PROFILES", DEXSCREENER_BASE + "/token-profiles/latest/v1")
ENDPOINT_LATEST_BOOSTS = getattr(config, "ENDPOINT_LATEST_BOOSTS", DEXSCREENER_BASE + "/token-boosts/latest/v1")
ENDPOINT_TOP_BOOSTS = getattr(config, "ENDPOINT_TOP_BOOSTS", DEXSCREENER_BASE + "/token-boosts/top/v1")
ENDPOINT_LATEST_COMMUNITY_TAKEOVERS = getattr(config, "ENDPOINT_LATEST_COMMUNITY_TAKEOVERS", DEXSCREENER_BASE + "/community-takeovers/latest/v1")
ENDPOINT_LATEST_ADS = getattr(config, "ENDPOINT_LATEST_ADS", DEXSCREENER_BASE + "/ads/latest/v1")
ENDPOINT_TOKEN_PAIRS = getattr(config, "ENDPOINT_TOKEN_PAIRS", DEXSCREENER_BASE + "/tokens/v1/solana")

SIGNALS_LOG_FILE = getattr(config, "SIGNALS_LOG_FILE", "khamcx_signals.jsonl")
SEEN_TOKENS_FILE = getattr(config, "SEEN_TOKENS_FILE", "khamcx_seen.json")
SEEN_TOKENS_TTL_SEC = getattr(config, "SEEN_TOKENS_TTL_SEC", 14400)
VERBOSE_REJECTS = getattr(config, "VERBOSE_REJECTS", True)


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_utc_hour():
    return datetime.now(timezone.utc).hour


def clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def safe_number(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value, default=0):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return default


def normalize_token_address(item):
    if not isinstance(item, dict):
        return None

    keys = [
        "tokenAddress", "token_address", "address", "base_address",
        "baseTokenAddress", "mint", "ca", "contract_address"
    ]
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and len(value) >= 32:
            return value

    base = item.get("baseToken") or item.get("base_token") or {}
    if isinstance(base, dict):
        value = base.get("address") or base.get("tokenAddress")
        if isinstance(value, str) and len(value) >= 32:
            return value

    return None


def source_record(address, source, raw=None):
    return {"tokenAddress": address, "source": source, "raw": raw or {}}


# ============================================================
# TELEGRAM
# ============================================================

async def send_telegram(session, message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return True
            log.warning("Telegram send failed: " + str(resp.status))
            return False
    except Exception as e:
        log.error("Telegram error: " + str(e))
        return False


def format_telegram_message(token_addr, metrics, score_data, decision="ACCEPT"):
    age_min = (metrics.get("age_seconds") or 0) / 60
    score = score_data["total"]
    base_score = score_data.get("base_total", score)
    adjustment = score_data.get("adjustment", 0)
    breakdown = score_data["breakdown"]
    reasons = score_data.get("reasons", [])
    mode = metrics.get("mode", "UNKNOWN")

    if decision == "ACCEPT":
        title = "🎯 *KHAMCX Hunter - ACCEPT*"
        if score >= 90:
            status_emoji = "🟢"
            status_text = "EXCELLENT - فرصة قوية"
        else:
            status_emoji = "🟡"
            status_text = "GOOD - فرصة مقبولة"
    else:
        title = "👀 *KHAMCX Hunter - WATCH*"
        status_emoji = "🟠"
        status_text = "WATCH - قريبة من القبول، راقب فقط"

    msg = title + "\n"
    msg += status_emoji + " *" + status_text + "*\n"
    msg += "*Mode: " + mode + "*\n"
    msg += "*Score: " + str(score) + "/100*"
    if adjustment:
        msg += "  (Base " + str(base_score) + ", Adj " + format(adjustment, "+d") + ")"
    msg += "\n\n"

    msg += "*Contract:*\n`" + token_addr + "`\n\n"

    msg += "📊 *Score Breakdown:*\n"
    msg += "├ Chart Strength: " + str(breakdown["chart_strength"]) + "/25\n"
    msg += "├ Volume/Liquidity: " + str(breakdown["volume_liquidity"]) + "/20\n"
    msg += "├ Buyers Strength: " + str(breakdown["buyers_strength"]) + "/15\n"
    msg += "├ Token Safety: " + str(breakdown["token_safety"]) + "/15\n"
    msg += "├ Growth Room: " + str(breakdown["growth_room"]) + "/15\n"
    msg += "└ Trade Timing: " + str(breakdown["trade_timing"]) + "/10\n\n"

    if reasons:
        msg += "🧠 *Scoring Notes:*\n"
        for r in reasons[:7]:
            msg += "• " + r + "\n"
        msg += "\n"

    msg += "📊 *Market Data:*\n"
    msg += "├ MCap: $" + format(int(metrics.get("mcap_usd", 0)), ",") + "\n"
    msg += "├ Liquidity: $" + format(int(metrics.get("liquidity_usd", 0)), ",") + "\n"
    msg += "├ DEX: " + str(metrics.get("dex_id", "")) + "\n"
    msg += "└ Age: " + str(round(age_min, 1)) + " min\n\n"

    msg += "📈 *Volume:*\n"
    msg += "├ 5m: $" + format(int(metrics.get("volume_5m_usd", 0)), ",") + "\n"
    msg += "├ 1h: $" + format(int(metrics.get("volume_1h_usd", 0)), ",") + "\n"
    msg += "└ 24h: $" + format(int(metrics.get("volume_24h_usd", 0)), ",") + "\n\n"

    msg += "🔄 *Activity:*\n"
    msg += "├ Buys 5m: " + str(metrics.get("buys_5m", 0)) + "\n"
    msg += "├ Sells 5m: " + str(metrics.get("sells_5m", 0)) + "\n"
    msg += "├ Txns 5m: " + str(metrics.get("txns_5m_total", 0)) + "\n"
    msg += "├ B/S Ratio: " + str(metrics.get("buy_sell_ratio", 0)) + "\n"
    msg += "└ Txns 1h: " + str(metrics.get("txns_1h_total", 0)) + "\n\n"

    msg += "⚡ *Momentum:*\n"
    msg += "├ 5m: " + format(metrics.get("price_change_5m", 0), "+.1f") + "%\n"
    msg += "├ 1h: " + format(metrics.get("price_change_1h", 0), "+.1f") + "%\n"
    msg += "└ 24h: " + format(metrics.get("price_change_24h", 0), "+.1f") + "%\n\n"

    msg += "🔗 *Links:*\n"
    msg += "[Dexscreener](https://dexscreener.com/solana/" + token_addr + ") | "
    msg += "[GMGN](https://gmgn.ai/sol/token/" + token_addr + ") | "
    msg += "[Photon](https://photon-sol.tinyastro.io/en/lp/" + token_addr + ")\n\n"

    if decision == "WATCH":
        msg += "⚠️ *WATCH يعني لا تدخل تلقائياً:*\n"
        msg += "راقب ثبات السعر 1-3 دقائق، وادخل فقط إذا زاد الشراء بدون شمعة عمودية.\n\n"
    else:
        msg += "⚠️ *قائمة التحقق اليدوي:*\n"
        msg += "1. لا تدخل إذا الشارت عمودي بدون إعادة اختبار\n"
        msg += "2. تحقق من ضغط البيع آخر 1-3 دقائق\n"
        msg += "3. تأكد أن السيولة لا تهبط\n"
        msg += "4. لا تطارد بعد شمعة خضراء طويلة\n\n"

    msg += "_⚠️ Paper mode - data only_"
    return msg


# ============================================================
# STORAGE
# ============================================================

class SeenTokens:
    def __init__(self, path, ttl_sec):
        self.path = Path(path)
        self.ttl_sec = ttl_sec
        self._seen = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                self._seen = {k: t for k, t in data.items() if now - t < self.ttl_sec}
            except (json.JSONDecodeError, OSError):
                self._seen = {}

    def save(self):
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._seen, f)
        except OSError:
            pass

    def is_known(self, token_addr):
        return token_addr in self._seen

    def mark_seen(self, token_addr):
        self._seen[token_addr] = time.time()

    def __len__(self):
        return len(self._seen)


class SignalLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.accepted = 0
        self.watched = 0
        self.rejected = 0
        self.kill_switched = 0

    def log(self, decision, token_addr, reason, metrics, score_data=None):
        entry = {
            "timestamp": now_iso(),
            "decision": decision,
            "token": token_addr,
            "reason": reason,
            "score": score_data["total"] if score_data else None,
            "score_data": score_data,
            "metrics": metrics,
        }

        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if decision == "ACCEPT":
            self.accepted += 1
            log.info("✅ ACCEPT " + token_addr[:8] + "... Score=" + str(score_data["total"]))
        elif decision == "WATCH":
            self.watched += 1
            log.info("👀 WATCH " + token_addr[:8] + "... Score=" + str(score_data["total"]))
        elif decision == "KILL":
            self.kill_switched += 1
        else:
            self.rejected += 1
            if VERBOSE_REJECTS:
                log.info("❌ REJECT " + token_addr[:8] + "... " + reason)


# ============================================================
# METRICS
# ============================================================

def extract_metrics(pair):
    m = {}

    created_at_ms = pair.get("pairCreatedAt")
    if created_at_ms:
        age_seconds = (time.time() * 1000 - safe_number(created_at_ms)) / 1000
        m["age_seconds"] = round(age_seconds, 1)
    else:
        open_ts = safe_number(pair.get("open_timestamp") or pair.get("pool_creation_timestamp") or 0)
        m["age_seconds"] = round(time.time() - open_ts, 1) if open_ts > 0 else None

    m["mcap_usd"] = safe_number(pair.get("marketCap") or pair.get("fdv") or pair.get("market_cap") or 0)

    liquidity = pair.get("liquidity", {}) or {}
    if isinstance(liquidity, dict):
        m["liquidity_usd"] = safe_number(liquidity.get("usd", 0))
    else:
        m["liquidity_usd"] = safe_number(pair.get("liquidity", 0))

    volume = pair.get("volume", {}) or {}
    if isinstance(volume, dict):
        m["volume_5m_usd"] = safe_number(volume.get("m5", 0))
        m["volume_1h_usd"] = safe_number(volume.get("h1", 0))
        m["volume_24h_usd"] = safe_number(volume.get("h24", 0))
    else:
        total_volume = safe_number(pair.get("volume", 0))
        m["volume_5m_usd"] = safe_number(pair.get("volume_5m", 0))
        m["volume_1h_usd"] = safe_number(pair.get("volume_1h", total_volume))
        m["volume_24h_usd"] = safe_number(pair.get("volume_24h", total_volume))

    txns = pair.get("txns", {}) or {}
    m5 = txns.get("m5", {}) if isinstance(txns, dict) else {}
    h1 = txns.get("h1", {}) if isinstance(txns, dict) else {}

    m["buys_5m"] = as_int(m5.get("buys", pair.get("buys_5m", 0)))
    m["sells_5m"] = as_int(m5.get("sells", pair.get("sells_5m", 0)))
    m["buys_1h"] = as_int(h1.get("buys", pair.get("buys", pair.get("buys_1h", 0))))
    m["sells_1h"] = as_int(h1.get("sells", pair.get("sells", pair.get("sells_1h", 0))))

    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)
    m["txns_5m_total"] = m["buys_5m"] + m["sells_5m"]
    m["txns_1h_total"] = m["buys_1h"] + m["sells_1h"]

    price_change = pair.get("priceChange", {}) or {}
    if isinstance(price_change, dict):
        m["price_change_5m"] = safe_number(price_change.get("m5", 0))
        m["price_change_1h"] = safe_number(price_change.get("h1", 0))
        m["price_change_24h"] = safe_number(price_change.get("h24", 0))
    else:
        m["price_change_5m"] = safe_number(pair.get("price_change_percent5m", 0))
        m["price_change_1h"] = safe_number(pair.get("price_change_percent1h", 0))
        m["price_change_24h"] = safe_number(pair.get("price_change_percent", 0))

    m["price_usd"] = safe_number(pair.get("priceUsd") or pair.get("price") or 0)
    m["pair_address"] = pair.get("pairAddress", "")
    m["dex_id"] = str(pair.get("dexId", pair.get("dex_id", "")) or "").lower()

    return m


def determine_mode(token_addr, metrics):
    dex_id = (metrics.get("dex_id") or "").lower()
    age = metrics.get("age_seconds")
    liq = metrics.get("liquidity_usd", 0)

    if dex_id == "pumpfun" or (str(token_addr).endswith("pump") and liq <= 1):
        return "EARLY_PUMPFUN"
    if age is not None and age < 1800:
        return "EARLY_LIQUID"
    return "POST_GRADUATION"


def pair_quality_score(pair):
    metrics = extract_metrics(pair)
    liquidity = metrics["liquidity_usd"]
    volume_5m = metrics["volume_5m_usd"]
    volume_1h = metrics["volume_1h_usd"]
    txns_5m = metrics["txns_5m_total"]
    age = metrics.get("age_seconds")
    dex_id = metrics.get("dex_id", "")

    score = 0
    score += min(liquidity / 1000, 80)
    score += min(volume_5m / 500, 45)
    score += min(volume_1h / 5000, 25)
    score += min(txns_5m / 2, 45)

    if dex_id in ("pumpswap", "raydium", "meteora", "orca"):
        score += 10
    elif dex_id == "pumpfun":
        score += 3

    if age is not None:
        if 300 <= age <= 28800:
            score += 15
        elif age < 300:
            score -= 10
        else:
            score -= 15

    return score


def choose_best_pair(pairs):
    valid_pairs = [p for p in pairs if (p.get("chainId") == "solana" or not p.get("chainId"))]
    if not valid_pairs:
        valid_pairs = pairs
    return max(valid_pairs, key=pair_quality_score)


# ============================================================
# KILL SWITCHES + FILTERS
# ============================================================

def check_kill_switches(metrics):
    mode = metrics.get("mode", "POST_GRADUATION")
    pc5 = metrics["price_change_5m"]
    pc1 = metrics["price_change_1h"]
    pc24 = metrics["price_change_24h"]
    liq = metrics["liquidity_usd"]
    buys5 = metrics.get("buys_5m", 0)
    sells5 = metrics.get("sells_5m", 0)
    txns5 = metrics.get("txns_5m_total", 0)

    if mode == "EARLY_PUMPFUN":
        if pc5 > 120:
            return True, "Early pump vertical candle (>120% 5m)"
        if pc1 > 700:
            return True, "Early token already extremely pumped (>700% 1h)"
        if txns5 < 20:
            return True, "Dead early activity (txns 5m < 20)"
        if sells5 >= 2.2 * max(buys5, 1):
            return True, "Sell pressure too high in early stage"
        return False, None

    k = getattr(config, "KILL_SWITCHES", {})
    max_pc5 = k.get("max_price_change_5m_kill", 80.0)
    max_pc24 = k.get("max_price_change_24h", 400.0)
    min_liq = k.get("min_liquidity_kill", 5000)

    if pc5 > max_pc5:
        return True, "Long green candle (5m > 80%)"
    if pc24 > max_pc24:
        return True, "Already pumped >400% (likely missed)"
    if liq < min_liq:
        return True, "Critical low liquidity"
    if txns5 < 20:
        return True, "Dead recent activity (txns 5m < 20)"
    if sells5 >= 2 * max(buys5, 1):
        return True, "Sell pressure too high in 5m"

    return False, None


def apply_filters(metrics):
    mode = metrics.get("mode", "POST_GRADUATION")
    age = metrics.get("age_seconds")
    if age is None:
        return False, "Age unknown"

    mcap = metrics["mcap_usd"]
    liq = metrics["liquidity_usd"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]
    buys5 = metrics["buys_5m"]
    txns5 = metrics["txns_5m_total"]
    txns1 = metrics["txns_1h_total"]
    ratio = metrics["buy_sell_ratio"]
    pc5 = metrics["price_change_5m"]

    if mode == "EARLY_PUMPFUN":
        if age < 120:
            return False, "Too fresh (<2m)"
        if age > 1800:
            return False, "Early pumpfun too old (>30m)"
        if mcap < 7_000:
            return False, "Early MCap too low"
        if mcap > 70_000:
            return False, "Early MCap too high"
        if vol5 < 5_000:
            return False, "Early Vol 5m too low"
        if buys5 < 50:
            return False, "Early Buys 5m too few"
        if txns5 < 100:
            return False, "Early Txns 5m too few"
        if ratio < 1.05:
            return False, "Early B/S too low"
        if ratio > 2.8:
            return False, "Early B/S suspicious"
        if pc5 < -45:
            return False, "Early crash too strong"
        if pc5 > 80:
            return False, "Early 5m pump too extended"
        return True, "Early pumpfun filters passed"

    if mode == "EARLY_LIQUID":
        if age < 300:
            return False, "Too fresh with liquidity (<5m)"
        if age >= 1800:
            return False, "Not early liquid"
        if mcap < 12_000 or mcap > 120_000:
            return False, "Early liquid MCap out of range"
        if liq < 8_000:
            return False, "Early liquid Liq too low"
        if vol5 < 8_000:
            return False, "Early liquid Vol 5m too low"
        if buys5 < 60:
            return False, "Early liquid Buys too few"
        if txns5 < 100:
            return False, "Early liquid Txns 5m too few"
        if ratio < 1.05 or ratio > 2.7:
            return False, "Early liquid B/S out of range"
        if pc5 < -45 or pc5 > 60:
            return False, "Early liquid price move out of range"
        return True, "Early liquid filters passed"

    # POST_GRADUATION - V5 relaxed filters
    f = getattr(config, "HUNTER_FILTERS", {})
    if age < f.get("min_age_seconds", 1200):
        return False, "Too young (<20 min)"
    if age > f.get("max_age_seconds", 43200):
        return False, "Too old (>12h)"
    if mcap < f.get("min_mcap_usd", 15000):
        return False, "MCap too low"
    if mcap > f.get("max_mcap_usd", 300000):
        return False, "MCap too high"
    if liq < f.get("min_liquidity_usd", 6000):
        return False, "Liq too low"
    if liq > f.get("max_liquidity_usd", 100000):
        return False, "Liq too high"
    if vol5 < f.get("min_volume_5m_usd", 1500):
        return False, "Vol 5m too low"
    if vol1 < f.get("min_volume_1h_usd", 15000):
        return False, "Vol 1h too low"
    if buys5 < f.get("min_buys_5m", 18):
        return False, "Buys 5m too few"
    if txns5 < getattr(config, "MIN_TXNS_5M_TOTAL", 30):
        return False, "Txns 5m too few"
    if txns1 < f.get("min_txns_1h", 90):
        return False, "Txns 1h too few"
    if ratio < f.get("min_buy_sell_ratio", 1.1):
        return False, "B/S ratio too low"
    if ratio > f.get("max_buy_sell_ratio", 3.5):
        return False, "B/S ratio suspicious"
    if pc5 < f.get("min_price_change_5m", -20.0):
        return False, "5m drop too big"
    if pc5 > f.get("max_price_change_5m", 35.0):
        return False, "5m pump too big"

    return True, "Post-graduation filters passed"


# ============================================================
# SCORING
# ============================================================

def score_chart_strength(metrics):
    score = 0
    notes = []
    mode = metrics.get("mode", "POST_GRADUATION")
    pc5 = metrics["price_change_5m"]
    pc1 = metrics["price_change_1h"]
    pc24 = metrics["price_change_24h"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]

    if mode.startswith("EARLY"):
        if -15 <= pc5 <= 25:
            score += 10
            notes.append("Early 5m price action is tradable")
        elif -35 <= pc5 < -15:
            score += 6
            notes.append("Early pullback acceptable but risky")
        elif 25 < pc5 <= 60:
            score += 4
            notes.append("Early move extended")
        if -30 <= pc1 <= 250:
            score += 6
        elif 250 < pc1 <= 500:
            score += 3
        if pc24 < 300:
            score += 4
        if vol5 >= 10_000:
            score += 5
        elif vol5 >= 5_000:
            score += 3
        return min(score, 25), notes

    if -5 <= pc5 <= 10:
        score += 10
        notes.append("5m price action healthy")
    elif -12 <= pc5 < -5:
        score += 5
        notes.append("5m pullback acceptable")
    elif 10 < pc5 <= 20:
        score += 4
        notes.append("5m move slightly extended")

    if -20 <= pc1 <= 80:
        score += 6
    elif 80 < pc1 <= 150:
        score += 3

    if pc24 < 100:
        score += 4
    elif pc24 < 200:
        score += 2

    expected_5m = vol1 / 12 if vol1 > 0 else 0
    if expected_5m > 0 and vol5 >= expected_5m * 0.8:
        score += 5
        notes.append("5m volume confirms activity")
    elif vol5 >= 5_000:
        score += 3

    return min(score, 25), notes


def score_volume_liquidity(metrics):
    score = 0
    notes = []
    mode = metrics.get("mode", "POST_GRADUATION")
    liq = metrics["liquidity_usd"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]

    if mode == "EARLY_PUMPFUN":
        score += 5
        notes.append("Pumpfun liquidity ignored in early mode")
    else:
        if 15_000 <= liq <= 40_000:
            score += 8
            notes.append("Liquidity in best range")
        elif 8_000 <= liq < 15_000:
            score += 5
        elif 40_000 < liq <= 80_000:
            score += 6

    if vol5 >= 50_000 and mode.startswith("EARLY"):
        score += 8
    elif vol5 >= 15_000:
        score += 7
    elif vol5 >= 5_000:
        score += 5
    elif vol5 >= 2_000:
        score += 3

    if vol1 >= 100_000 and mode.startswith("EARLY"):
        score += 5
    elif vol1 >= 60_000:
        score += 5
    elif vol1 >= 20_000:
        score += 3

    if liq > 0:
        vl_ratio = vol1 / liq
        if 0.6 <= vl_ratio <= 5:
            score += 2
        elif vl_ratio > 10:
            score -= 2
            notes.append("Volume/Liquidity ratio too aggressive")

    return clamp(score, 0, 20), notes


def score_buyers_strength(metrics):
    score = 0
    notes = []
    ratio = metrics["buy_sell_ratio"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]
    txns5 = metrics["txns_5m_total"]
    txns1 = metrics["txns_1h_total"]

    if 1.3 <= ratio <= 2.2:
        score += 6
        notes.append("B/S ratio healthy")
    elif 1.1 <= ratio < 1.3:
        score += 4
    elif 2.2 < ratio <= 3.0:
        score += 3
        notes.append("B/S ratio high; monitor exhaustion")

    if buys5 >= 250:
        score += 5
    elif buys5 >= 70:
        score += 5
    elif buys5 >= 40:
        score += 4
    elif buys5 >= 25:
        score += 3

    if txns5 >= 300:
        score += 3
    elif txns5 >= 80:
        score += 2
    elif txns5 >= 40:
        score += 1

    if txns1 >= 1000:
        score += 2
    elif txns1 >= 300:
        score += 2
    elif txns1 >= 120:
        score += 1

    if sells5 > buys5:
        score -= 4
        notes.append("Sells exceed buys in 5m")

    return clamp(score, 0, 15), notes


def score_token_safety(metrics):
    score = 4
    notes = ["Safety conservative; no holder/mint RPC check yet"]
    mode = metrics.get("mode", "POST_GRADUATION")
    liq = metrics["liquidity_usd"]
    mcap = metrics["mcap_usd"]
    txns1 = metrics["txns_1h_total"]
    ratio = metrics["buy_sell_ratio"]

    if mode == "EARLY_PUMPFUN":
        if txns1 >= 300:
            score += 3
        if 1.05 <= ratio <= 2.3:
            score += 3
        if metrics["volume_5m_usd"] >= 5_000:
            score += 2
        return clamp(score, 0, 12), notes

    if liq >= 15_000:
        score += 2
    if txns1 >= 200:
        score += 2
    if mcap > 0 and liq / mcap >= 0.10:
        score += 2
    if metrics["volume_1h_usd"] >= 20_000:
        score += 1
    if 1.1 <= ratio <= 2.5:
        score += 2

    return clamp(score, 0, 15), notes


def score_growth_room(metrics):
    score = 0
    notes = []
    mode = metrics.get("mode", "POST_GRADUATION")
    mcap = metrics["mcap_usd"]
    pc24 = metrics["price_change_24h"]
    liq = metrics["liquidity_usd"]

    if mode.startswith("EARLY"):
        if 7_000 <= mcap < 20_000:
            score += 8
            notes.append("Early MCap has strong room")
        elif 20_000 <= mcap < 45_000:
            score += 7
        elif 45_000 <= mcap <= 70_000:
            score += 4
        if pc24 < 150:
            score += 4
        elif pc24 < 350:
            score += 2
        if liq >= 8_000 or mode == "EARLY_PUMPFUN":
            score += 3
        return min(score, 15), notes

    if 25_000 <= mcap < 50_000:
        score += 8
        notes.append("MCap has strong growth room")
    elif 50_000 <= mcap < 100_000:
        score += 6
    elif 100_000 <= mcap < 150_000:
        score += 4
    elif 150_000 <= mcap <= 220_000:
        score += 2

    if pc24 < 80:
        score += 4
    elif pc24 < 150:
        score += 2
    elif pc24 < 250:
        score += 1

    if liq >= 10_000:
        score += 3

    return min(score, 15), notes


def score_trade_timing():
    current_hour = now_utc_hour()
    golden = getattr(config, "GOLDEN_HOURS_UTC", list(range(0, 12)))
    avoid = getattr(config, "AVOID_HOURS_UTC", list(range(18, 23)))
    if current_hour in golden:
        return 10, ["Golden trading window"]
    if current_hour in avoid:
        return 2, ["Avoid trading window"]
    return 6, ["Neutral trading window"]


def calculate_dynamic_adjustment(metrics):
    adjustment = 0
    notes = []
    mode = metrics.get("mode", "POST_GRADUATION")
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]
    pc5 = metrics["price_change_5m"]
    ratio = metrics["buy_sell_ratio"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    expected_5m = vol1 / 12 if vol1 > 0 else 0

    if mode.startswith("EARLY"):
        if buys5 >= 100 and 1.15 <= ratio <= 2.2 and -20 <= pc5 <= 35:
            adjustment += 5
            notes.append("Early momentum boost: active buyers with controlled price")
        if vol5 >= 10_000 and -25 <= pc5 <= 35:
            adjustment += 3
            notes.append("Early momentum boost: strong 5m volume")
        if pc5 > 45:
            adjustment -= 5
            notes.append("Penalty: early move extended")
        if sells5 >= buys5 * 0.95:
            adjustment -= 6
            notes.append("Penalty: early sell pressure close to buys")
        return adjustment, notes

    if expected_5m > 0 and vol5 >= expected_5m * 1.5 and -5 <= pc5 <= 15:
        adjustment += 5
        notes.append("Momentum boost: 5m volume acceleration")
    if buys5 >= 50 and 1.3 <= ratio <= 2.5:
        adjustment += 3
        notes.append("Momentum boost: strong buyers without extreme ratio")
    if pc5 > 15:
        adjustment -= 4
        notes.append("Penalty: short-term move is extended")
    if sells5 >= buys5 * 0.9:
        adjustment -= 5
        notes.append("Penalty: sell pressure close to buys")
    if ratio > 2.7:
        adjustment -= 3
        notes.append("Penalty: B/S ratio may be artificially high")

    return adjustment, notes


def calculate_score(metrics):
    breakdown = {
        "chart_strength": 0,
        "volume_liquidity": 0,
        "buyers_strength": 0,
        "token_safety": 0,
        "growth_room": 0,
        "trade_timing": 0,
    }
    reasons = []

    for key, func in [
        ("chart_strength", score_chart_strength),
        ("volume_liquidity", score_volume_liquidity),
        ("buyers_strength", score_buyers_strength),
        ("token_safety", score_token_safety),
        ("growth_room", score_growth_room),
    ]:
        breakdown[key], notes = func(metrics)
        reasons.extend(notes)

    breakdown["trade_timing"], notes = score_trade_timing()
    reasons.extend(notes)

    base_total = sum(breakdown.values())
    adjustment, adjustment_notes = calculate_dynamic_adjustment(metrics)
    reasons.extend(adjustment_notes)
    total = clamp(base_total + adjustment, 0, 100)

    return {
        "total": int(total),
        "base_total": int(base_total),
        "adjustment": int(adjustment),
        "breakdown": breakdown,
        "reasons": reasons,
    }


# ============================================================
# DISCOVERY CLIENTS
# ============================================================

class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def _get_token_list(self, url, label):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    log.warning(label + " rate limited")
                    await asyncio.sleep(3)
                    return []
                if resp.status != 200:
                    log.warning(label + " status " + str(resp.status))
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                out = []
                for item in data:
                    if item.get("chainId") == "solana":
                        addr = normalize_token_address(item)
                        if addr:
                            out.append(source_record(addr, label, item))
                return out
        except Exception as e:
            log.warning(label + " error: " + str(e))
            return []

    async def get_sources(self):
        calls = [
            self._get_token_list(ENDPOINT_LATEST_PROFILES, "dex_profiles"),
            self._get_token_list(ENDPOINT_LATEST_BOOSTS, "dex_latest_boosts"),
            self._get_token_list(ENDPOINT_TOP_BOOSTS, "dex_top_boosts"),
            self._get_token_list(ENDPOINT_LATEST_COMMUNITY_TAKEOVERS, "dex_community"),
            self._get_token_list(ENDPOINT_LATEST_ADS, "dex_ads"),
        ]
        results = await asyncio.gather(*calls)
        return results

    async def get_pairs_batch(self, token_addrs):
        if not token_addrs:
            return {}

        result = {addr: [] for addr in token_addrs}

        for i in range(0, len(token_addrs), 30):
            batch = token_addrs[i:i + 30]
            url = ENDPOINT_TOKEN_PAIRS + "/" + ",".join(batch)
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 429:
                        await asyncio.sleep(3)
                        continue
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if isinstance(data, list):
                        pairs = data
                    elif isinstance(data, dict) and "pairs" in data:
                        pairs = data["pairs"] or []
                    else:
                        pairs = []

                    for pair in pairs:
                        base = (pair.get("baseToken") or {}).get("address")
                        quote = (pair.get("quoteToken") or {}).get("address")
                        for addr in batch:
                            if addr == base or addr == quote:
                                result.setdefault(addr, []).append(pair)
            except Exception as e:
                log.warning("get_pairs_batch error: " + str(e))

        return result


class GmgnPublicClient:
    """
    V5 Fixed: تم تقليل الـ calls من 8 إلى 4 لتجنب الـ rate limit 403
    """
    BASE = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps"

    def __init__(self, session):
        self.session = session

    async def get_rank(self, period, orderby, direction="desc"):
        params = {
            "orderby": orderby,
            "direction": direction,
            "filters[]": ["not_honeypot"],
        }
        url = self.BASE + "/" + period + "?" + urlencode(params, doseq=True)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://gmgn.ai/",
            "User-Agent": "Mozilla/5.0 KHAMCXHunter/5.0",
        }
        label = "gmgn_" + period + "_" + orderby
        try:
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status in (403, 429):
                    log.warning(label + " blocked/rate-limited: " + str(resp.status))
                    return []
                if resp.status != 200:
                    log.warning(label + " status " + str(resp.status))
                    return []
                data = await resp.json(content_type=None)
                rank = []
                if isinstance(data, dict):
                    payload = data.get("data") or {}
                    rank = payload.get("rank") or payload.get("list") or []
                if not isinstance(rank, list):
                    return []
                out = []
                for item in rank:
                    addr = normalize_token_address(item)
                    if addr:
                        out.append(source_record(addr, label, item))
                return out
        except Exception as e:
            log.warning(label + " error: " + str(e))
            return []

    async def get_sources(self):
        # V5: فقط 4 calls مهمة بدل 8 (لتقليل الضغط وتجنب 403)
        calls = [
            self.get_rank("5m", "open_timestamp"),
            self.get_rank("1h", "volume"),
            self.get_rank("1h", "smartmoney"),
            self.get_rank("1h", "change5m"),
        ]
        return await asyncio.gather(*calls)


# ============================================================
# MAIN
# ============================================================

async def collect_candidates(dex_client, gmgn_client):
    dex_results, gmgn_results = await asyncio.gather(
        dex_client.get_sources(),
        gmgn_client.get_sources(),
    )

    all_source_lists = []
    for group in dex_results + gmgn_results:
        all_source_lists.append(group)

    merged = {}
    source_counts = {}
    for source_items in all_source_lists:
        for item in source_items:
            addr = item.get("tokenAddress")
            source = item.get("source", "unknown")
            if not addr:
                continue
            source_counts[source] = source_counts.get(source, 0) + 1
            if addr not in merged:
                merged[addr] = {"tokenAddress": addr, "sources": set(), "raw": []}
            merged[addr]["sources"].add(source)
            if item.get("raw"):
                merged[addr]["raw"].append(item.get("raw"))

    for source, count in sorted(source_counts.items()):
        log.info(source + ": " + str(count) + " tokens")

    records = []
    for addr, rec in merged.items():
        rec["sources"] = sorted(list(rec["sources"]))
        records.append(rec)
    return records


async def batch_run():
    start_time = time.time()
    deadline = start_time + RUN_DURATION_SECONDS

    signal_logger = SignalLogger(SIGNALS_LOG_FILE)
    seen = SeenTokens(SEEN_TOKENS_FILE, SEEN_TOKENS_TTL_SEC)

    log.info("=" * 60)
    log.info("KHAMCX Hunter Bot V5 Multi-Source - Started (Fixed)")
    log.info("Discovery: Dexscreener + GMGN (reduced calls)")
    log.info("Modes: EARLY_PUMPFUN / EARLY_LIQUID / POST_GRADUATION")
    log.info("Accept score post: " + str(ACCEPT_SCORE_POST) + "/100")
    log.info("Accept score early: " + str(ACCEPT_SCORE_EARLY) + "/100")
    log.info("Runtime cap: " + str(RUN_DURATION_SECONDS) + "s")
    log.info("Loaded " + str(len(seen)) + " previously seen tokens")
    log.info("Telegram: " + ("ENABLED" if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID else "DISABLED"))
    log.info("=" * 60)

    processed = 0

    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
        headers={"User-Agent": "KHAMCXHunter/5.0"},
    ) as session:
        dex_client = DexscreenerClient(session)
        gmgn_client = GmgnPublicClient(session)

        while time.time() < deadline and processed < MAX_TOKENS_PER_RUN:
            records = await collect_candidates(dex_client, gmgn_client)
            candidate_records = [r for r in records if not seen.is_known(r["tokenAddress"])]

            if candidate_records:
                log.info("Candidates before processing: " + str(len(candidate_records)))
            else:
                remaining = deadline - time.time()
                if remaining > POLL_INTERVAL_SEC:
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    continue
                break

            remaining_slots = MAX_TOKENS_PER_RUN - processed
            current_records = candidate_records[:remaining_slots]
            current_addrs = [r["tokenAddress"] for r in current_records]

            pairs_by_token = await dex_client.get_pairs_batch(current_addrs)

            for rec in current_records:
                if time.time() >= deadline or processed >= MAX_TOKENS_PER_RUN:
                    break

                token_addr = rec["tokenAddress"]
                sources = rec.get("sources", [])
                pairs = pairs_by_token.get(token_addr, [])
                processed += 1
                seen.mark_seen(token_addr)

                if not pairs:
                    signal_logger.log("REJECT", token_addr, "No Dexscreener pair data", {
                        "sources": sources,
                    })
                    continue

                best_pair = choose_best_pair(pairs)
                metrics = extract_metrics(best_pair)
                metrics["sources"] = sources
                metrics["mode"] = determine_mode(token_addr, metrics)

                killed, kill_reason = check_kill_switches(metrics)
                if killed:
                    signal_logger.log("KILL", token_addr, kill_reason, metrics)
                    continue

                passed, reason = apply_filters(metrics)
                if not passed:
                    signal_logger.log("REJECT", token_addr, reason, metrics)
                    continue

                score_data = calculate_score(metrics)
                mode = metrics.get("mode", "POST_GRADUATION")
                accept_score = ACCEPT_SCORE_EARLY if mode.startswith("EARLY") else ACCEPT_SCORE_POST
                watch_score = WATCH_SCORE_EARLY if mode.startswith("EARLY") else WATCH_SCORE_POST

                if score_data["total"] >= accept_score:
                    signal_logger.log(
                        "ACCEPT",
                        token_addr,
                        "Passed " + mode + " filters + score >= " + str(accept_score),
                        metrics,
                        score_data,
                    )
                    msg = format_telegram_message(token_addr, metrics, score_data, "ACCEPT")
                    await send_telegram(session, msg)
                elif score_data["total"] >= watch_score:
                    signal_logger.log(
                        "WATCH",
                        token_addr,
                        "Near match: score >= " + str(watch_score),
                        metrics,
                        score_data,
                    )
                    msg = format_telegram_message(token_addr, metrics, score_data, "WATCH")
                    await send_telegram(session, msg)
                else:
                    signal_logger.log(
                        "REJECT",
                        token_addr,
                        "Score too low: " + str(score_data["total"]),
                        metrics,
                        score_data,
                    )

                await asyncio.sleep(0.03)

            remaining = deadline - time.time()
            if remaining > POLL_INTERVAL_SEC:
                await asyncio.sleep(POLL_INTERVAL_SEC)
            else:
                break

    seen.save()

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("Batch complete in " + str(round(elapsed, 1)) + "s")
    log.info("Processed: " + str(processed))
    log.info("✅ Accepted: " + str(signal_logger.accepted))
    log.info("👀 Watched: " + str(signal_logger.watched))
    log.info("❌ Rejected: " + str(signal_logger.rejected))
    log.info("💀 Killed: " + str(signal_logger.kill_switched))
    log.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(batch_run())
    except KeyboardInterrupt:
        log.info("Interrupted")
