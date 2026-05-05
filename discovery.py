"""
KHAMCX Hunter Bot - Discovery Engine V2
=======================================
بوت اكتشاف لتوكنات Solana يعتمد على:
- Dexscreener token profiles + boosts
- Kill Switches قبل أي تقييم
- فلاتر KHAMCX الأساسية
- نظام تقييم ديناميكي 100 نقطة
- Momentum Boost / Penalties
- إشعارات Telegram مفصلة

ملاحظة مهمة:
هذا الملف لا ينفذ شراء أو بيع. هو Paper Mode / Signal Engine فقط.
"""

import asyncio
import json
import os
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

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


# ============================================================
# HELPERS
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_utc_hour():
    return datetime.now(timezone.utc).hour


def clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def safe_number(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def format_telegram_message(token_addr, metrics, score_data):
    age_min = (metrics.get("age_seconds") or 0) / 60
    score = score_data["total"]
    base_score = score_data.get("base_total", score)
    adjustment = score_data.get("adjustment", 0)
    breakdown = score_data["breakdown"]
    reasons = score_data.get("reasons", [])

    if score >= 90:
        status_emoji = "🟢"
        status_text = "EXCELLENT - فرصة ممتازة"
    elif score >= 85:
        status_emoji = "🟡"
        status_text = "GOOD - فرصة جيدة"
    else:
        status_emoji = "🟠"
        status_text = "WATCH - مراقبة فقط"

    msg = "🎯 *KHAMCX Hunter - ACCEPT*\n"
    msg += status_emoji + " *" + status_text + "*\n"
    msg += "*Score: " + str(score) + "/100*"
    if adjustment:
        msg += "  (Base " + str(base_score) + ", Adj " + format(adjustment, "+d") + ")"
    msg += "\n\n"

    msg += "*Contract:*\n`" + token_addr + "`\n\n"

    msg += "📊 *Score Breakdown:*\n"
    msg += "├ Volume/Liquidity: " + str(breakdown["volume_liquidity"]) + "/20\n"
    msg += "├ Buyers Strength: " + str(breakdown["buyers_strength"]) + "/15\n"
    msg += "├ Growth Room: " + str(breakdown["growth_room"]) + "/15\n"
    msg += "├ Trade Timing: " + str(breakdown["trade_timing"]) + "/10\n"
    msg += "├ Token Safety: " + str(breakdown["token_safety"]) + "/15\n"
    msg += "└ Chart Strength: " + str(breakdown["chart_strength"]) + "/25\n\n"

    if reasons:
        msg += "🧠 *Scoring Notes:*\n"
        for r in reasons[:6]:
            msg += "• " + r + "\n"
        msg += "\n"

    msg += "📊 *Market Data:*\n"
    msg += "├ MCap: $" + format(int(metrics.get("mcap_usd", 0)), ",") + "\n"
    msg += "├ Liquidity: $" + format(int(metrics.get("liquidity_usd", 0)), ",") + "\n"
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

    msg += "⚠️ *قائمة التحقق اليدوي:*\n"
    msg += "1. افتح الشارت\n"
    msg += "2. تحقق: اندفاع → تصحيح هادئ → ارتداد\n"
    msg += "3. السعر فوق قاعدة سعرية واضحة\n"
    msg += "4. لا توجد شموع خضراء طويلة\n"
    msg += "5. لا تصريف واضح من Top Holders\n"
    msg += "6. لا تدخل إذا كان الشارت عمودي بدون إعادة اختبار\n\n"

    msg += "_⚠️ Paper mode - data only_"
    return msg


# ============================================================
# DATA STRUCTURES
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

    def is_new(self, token_addr):
        if token_addr in self._seen:
            return False
        self._seen[token_addr] = time.time()
        return True

    def __len__(self):
        return len(self._seen)


class SignalLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.accepted = 0
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
            log.info(
                "✅ ACCEPT " + token_addr[:8] + "..." +
                " Score=" + str(score_data["total"]) +
                " MCap=$" + str(int(metrics.get("mcap_usd", 0)))
            )
        elif decision == "KILL":
            self.kill_switched += 1
        else:
            self.rejected += 1
            if getattr(config, "VERBOSE_REJECTS", False):
                log.info("❌ REJECT " + token_addr[:8] + "... " + reason)


# ============================================================
# METRICS EXTRACTION
# ============================================================

def extract_metrics(pair):
    m = {}

    created_at_ms = pair.get("pairCreatedAt")
    if created_at_ms:
        age_seconds = (time.time() * 1000 - safe_number(created_at_ms)) / 1000
        m["age_seconds"] = round(age_seconds, 1)
    else:
        m["age_seconds"] = None

    m["mcap_usd"] = safe_number(pair.get("marketCap") or pair.get("fdv") or 0)

    liquidity = pair.get("liquidity", {}) or {}
    m["liquidity_usd"] = safe_number(liquidity.get("usd", 0))

    volume = pair.get("volume", {}) or {}
    m["volume_5m_usd"] = safe_number(volume.get("m5", 0))
    m["volume_1h_usd"] = safe_number(volume.get("h1", 0))
    m["volume_24h_usd"] = safe_number(volume.get("h24", 0))

    txns = pair.get("txns", {}) or {}
    m5 = txns.get("m5", {}) or {}
    h1 = txns.get("h1", {}) or {}

    m["buys_5m"] = int(safe_number(m5.get("buys", 0)))
    m["sells_5m"] = int(safe_number(m5.get("sells", 0)))
    m["buys_1h"] = int(safe_number(h1.get("buys", 0)))
    m["sells_1h"] = int(safe_number(h1.get("sells", 0)))

    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)
    m["txns_5m_total"] = m["buys_5m"] + m["sells_5m"]
    m["txns_1h_total"] = m["buys_1h"] + m["sells_1h"]

    price_change = pair.get("priceChange", {}) or {}
    m["price_change_5m"] = safe_number(price_change.get("m5", 0))
    m["price_change_1h"] = safe_number(price_change.get("h1", 0))
    m["price_change_24h"] = safe_number(price_change.get("h24", 0))

    m["price_usd"] = safe_number(pair.get("priceUsd", 0))
    m["pair_address"] = pair.get("pairAddress", "")
    m["dex_id"] = pair.get("dexId", "")

    return m


def pair_quality_score(pair):
    """
    اختيار أفضل Pair لا يعتمد على السيولة فقط.
    السيولة مهمة، لكن الفوليوم والنشاط والعمر مهمين أيضاً.
    """
    metrics = extract_metrics(pair)

    liquidity = metrics["liquidity_usd"]
    volume_5m = metrics["volume_5m_usd"]
    volume_1h = metrics["volume_1h_usd"]
    txns_5m = metrics["txns_5m_total"]
    age = metrics.get("age_seconds")

    score = 0
    score += min(liquidity / 1000, 80)       # سيولة حتى 80 نقطة
    score += min(volume_5m / 500, 40)        # زخم قصير
    score += min(volume_1h / 5000, 20)       # تأكيد ساعة
    score += min(txns_5m, 40)                # نشاط حديث

    if age is not None:
        # تفضيل أزواج داخل نافذة KHAMCX
        if config.HUNTER_FILTERS["min_age_seconds"] <= age <= config.HUNTER_FILTERS["max_age_seconds"]:
            score += 20
        elif age < config.HUNTER_FILTERS["min_age_seconds"]:
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
# KILL SWITCHES
# ============================================================

def check_kill_switches(metrics):
    k = config.KILL_SWITCHES

    if metrics["price_change_5m"] > k["max_price_change_5m_kill"]:
        return True, "Long green candle (5m > 50%)"

    if metrics["price_change_24h"] > k["max_price_change_24h"]:
        return True, "Already pumped >250% (likely missed)"

    if metrics["liquidity_usd"] < k["min_liquidity_kill"]:
        return True, "Critical low liquidity"

    # حماية إضافية: نشاط حديث ضعيف جداً، حتى لو مر حجم الساعة
    if metrics.get("txns_5m_total", 0) < 20:
        return True, "Dead recent activity (txns 5m < 20)"

    # حماية إضافية: بيع حديث أعلى من الشراء بشكل واضح
    if metrics.get("sells_5m", 0) >= 2 * max(metrics.get("buys_5m", 0), 1):
        return True, "Sell pressure too high in 5m"

    return False, None


# ============================================================
# FILTERS
# ============================================================

def apply_filters(metrics):
    f = config.HUNTER_FILTERS

    age = metrics.get("age_seconds")
    if age is None:
        return False, "Age unknown"
    if age < f["min_age_seconds"]:
        return False, "Too young (<30 min)"
    if age > f["max_age_seconds"]:
        return False, "Too old (>8h)"

    if metrics["mcap_usd"] < f["min_mcap_usd"]:
        return False, "MCap too low"
    if metrics["mcap_usd"] > f["max_mcap_usd"]:
        return False, "MCap too high"

    if metrics["liquidity_usd"] < f["min_liquidity_usd"]:
        return False, "Liq too low"
    if metrics["liquidity_usd"] > f["max_liquidity_usd"]:
        return False, "Liq too high"

    if metrics["volume_5m_usd"] < f["min_volume_5m_usd"]:
        return False, "Vol 5m too low"
    if metrics["volume_1h_usd"] < f["min_volume_1h_usd"]:
        return False, "Vol 1h too low"

    if metrics["buys_5m"] < f["min_buys_5m"]:
        return False, "Buys 5m too few"

    # إضافة مهمة: إجمالي معاملات آخر 5 دقائق، حتى لا تمر عملة بعدد شراء جيد لكن نشاطها العام ضعيف
    min_txns_5m = getattr(config, "MIN_TXNS_5M_TOTAL", 30)
    if metrics["txns_5m_total"] < min_txns_5m:
        return False, "Txns 5m too few"

    if metrics["txns_1h_total"] < f["min_txns_1h"]:
        return False, "Txns 1h too few"

    if metrics["buy_sell_ratio"] < f["min_buy_sell_ratio"]:
        return False, "B/S ratio too low"
    if metrics["buy_sell_ratio"] > f["max_buy_sell_ratio"]:
        return False, "B/S ratio suspicious"

    if metrics["price_change_5m"] < f["min_price_change_5m"]:
        return False, "5m drop too big"
    if metrics["price_change_5m"] > f["max_price_change_5m"]:
        return False, "5m pump too big"

    return True, "All filters passed"


# ============================================================
# SCORING SYSTEM V2
# ============================================================

def score_chart_strength(metrics):
    """
    بديل عن النقاط الافتراضية القديمة.
    هذا ليس تحليل شارت بصري حقيقي، لكنه أقرب لمنطق السوق من إعطاء 20 نقطة ثابتة.
    """
    score = 0
    notes = []

    pc5 = metrics["price_change_5m"]
    pc1 = metrics["price_change_1h"]
    pc24 = metrics["price_change_24h"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]

    # أفضل منطقة: حركة حديثة هادئة أو إيجابية خفيفة
    if -5 <= pc5 <= 10:
        score += 10
        notes.append("5m price action healthy")
    elif -12 <= pc5 < -5:
        score += 5
        notes.append("5m pullback acceptable")
    elif 10 < pc5 <= 20:
        score += 4
        notes.append("5m move slightly extended")

    # اتجاه الساعة لا يكون منهاراً ولا عمودياً جداً
    if -20 <= pc1 <= 80:
        score += 6
    elif 80 < pc1 <= 150:
        score += 3

    # الصعود اليومي ليس مفرطاً
    if pc24 < 100:
        score += 4
    elif pc24 < 200:
        score += 2

    # نشاط آخر 5 دقائق منطقي مقابل الساعة
    expected_5m = vol1 / 12 if vol1 > 0 else 0
    if expected_5m > 0 and vol5 >= expected_5m * 0.8:
        score += 5
        notes.append("5m volume confirms activity")
    elif vol5 >= 5000:
        score += 3

    return min(score, 25), notes


def score_volume_liquidity(metrics):
    score = 0
    notes = []

    liq = metrics["liquidity_usd"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]

    if 15_000 <= liq <= 40_000:
        score += 8
        notes.append("Liquidity in best range")
    elif 8_000 <= liq < 15_000:
        score += 5
    elif 40_000 < liq <= 80_000:
        score += 6

    if vol5 >= 15_000:
        score += 7
    elif vol5 >= 5_000:
        score += 5
    elif vol5 >= 2_000:
        score += 3

    if vol1 >= 60_000:
        score += 5
    elif vol1 >= 20_000:
        score += 3

    # Volume/Liquidity ratio: نشاط بدون مبالغة شديدة
    if liq > 0:
        vl_ratio = vol1 / liq
        if 0.6 <= vl_ratio <= 4:
            score += 2
        elif vl_ratio > 8:
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

    if 1.4 <= ratio <= 2.4:
        score += 6
        notes.append("B/S ratio healthy")
    elif 1.2 <= ratio < 1.4:
        score += 4
    elif 2.4 < ratio <= 3.0:
        score += 3
        notes.append("B/S ratio high; monitor for exhaustion")

    if buys5 >= 70:
        score += 5
    elif buys5 >= 40:
        score += 4
    elif buys5 >= 25:
        score += 3

    if txns5 >= 80:
        score += 2
    elif txns5 >= 40:
        score += 1

    if txns1 >= 300:
        score += 2
    elif txns1 >= 120:
        score += 1

    if sells5 > buys5:
        score -= 3
        notes.append("Sells exceed buys in 5m")

    return clamp(score, 0, 15), notes


def score_token_safety(metrics):
    """
    لا نعطي 12/15 افتراضي كما في النسخة السابقة.
    Dexscreener وحده لا يكفي لفحص mint/freeze/holders.
    لذلك نعطي نقاطاً محافظة بناءً على السوق فقط، ونترك الباقي للفحص اليدوي أو Solana RPC لاحقاً.
    """
    score = 5
    notes = ["Safety is conservative; no RPC holder/mint check yet"]

    liq = metrics["liquidity_usd"]
    mcap = metrics["mcap_usd"]
    txns1 = metrics["txns_1h_total"]

    if liq >= 15_000:
        score += 2
    if txns1 >= 200:
        score += 2
    if mcap > 0 and liq / mcap >= 0.10:
        score += 2
    if metrics["volume_1h_usd"] >= 20_000:
        score += 1

    return clamp(score, 0, 15), notes


def score_growth_room(metrics):
    score = 0
    notes = []

    mcap = metrics["mcap_usd"]
    pc24 = metrics["price_change_24h"]
    liq = metrics["liquidity_usd"]

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
    if current_hour in config.GOLDEN_HOURS_UTC:
        return 10, ["Golden trading window"]
    if current_hour in config.AVOID_HOURS_UTC:
        return 2, ["Avoid trading window"]
    return 6, ["Neutral trading window"]


def calculate_dynamic_adjustment(metrics):
    adjustment = 0
    notes = []

    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]
    pc5 = metrics["price_change_5m"]
    ratio = metrics["buy_sell_ratio"]
    buys5 = metrics["buys_5m"]
    sells5 = metrics["sells_5m"]

    expected_5m = vol1 / 12 if vol1 > 0 else 0

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

    breakdown["chart_strength"], notes = score_chart_strength(metrics)
    reasons.extend(notes)

    breakdown["volume_liquidity"], notes = score_volume_liquidity(metrics)
    reasons.extend(notes)

    breakdown["buyers_strength"], notes = score_buyers_strength(metrics)
    reasons.extend(notes)

    breakdown["token_safety"], notes = score_token_safety(metrics)
    reasons.extend(notes)

    breakdown["growth_room"], notes = score_growth_room(metrics)
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
# DEXSCREENER CLIENT
# ============================================================

class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def _get_token_list(self, url, label):
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
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
                return [t for t in data if t.get("chainId") == "solana" and t.get("tokenAddress")]
        except Exception as e:
            log.warning(label + " error: " + str(e))
            return []

    async def get_latest_profiles(self):
        return await self._get_token_list(config.ENDPOINT_LATEST_PROFILES, "latest_profiles")

    async def get_latest_boosts(self):
        return await self._get_token_list(config.ENDPOINT_LATEST_BOOSTS, "latest_boosts")

    async def get_top_boosts(self):
        url = getattr(config, "ENDPOINT_TOP_BOOSTS", None)
        if not url:
            return []
        return await self._get_token_list(url, "top_boosts")

    async def get_latest_community_takeovers(self):
        url = getattr(config, "ENDPOINT_LATEST_COMMUNITY_TAKEOVERS", None)
        if not url:
            return []
        return await self._get_token_list(url, "community_takeovers")

    async def get_latest_ads(self):
        url = getattr(config, "ENDPOINT_LATEST_ADS", None)
        if not url:
            return []
        return await self._get_token_list(url, "latest_ads")

    async def get_pairs_batch(self, token_addrs):
        if not token_addrs:
            return {}

        # Dexscreener supports up to 30 comma-separated token addresses per request.
        url = config.ENDPOINT_TOKEN_PAIRS + "/" + ",".join(token_addrs[:30])
        result = {addr: [] for addr in token_addrs}

        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(3)
                    return result
                if resp.status != 200:
                    return result
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
                    for addr in token_addrs:
                        if addr == base or addr == quote:
                            result.setdefault(addr, []).append(pair)
                return result
        except Exception as e:
            log.warning("get_pairs_batch error: " + str(e))
            return result

    async def get_pairs(self, token_addr):
        pairs_by_token = await self.get_pairs_batch([token_addr])
        return pairs_by_token.get(token_addr, [])


# ============================================================
# MAIN BATCH RUN
# ============================================================

async def batch_run():
    start_time = time.time()
    deadline = start_time + config.RUN_DURATION_SECONDS

    signal_logger = SignalLogger(config.SIGNALS_LOG_FILE)
    seen = SeenTokens(config.SEEN_TOKENS_FILE, config.SEEN_TOKENS_TTL_SEC)

    log.info("=" * 60)
    log.info("KHAMCX Hunter Bot V3 Expanded Discovery - Started")
    log.info("Filters: Hunter KHAMCX + Dynamic Scoring")
    log.info("Min score to ACCEPT: " + str(config.MIN_SCORE_TO_ACCEPT) + "/100")
    log.info("Loaded " + str(len(seen)) + " previously seen tokens")
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        log.info("Telegram: ENABLED")
    else:
        log.info("Telegram: DISABLED")
    log.info("=" * 60)

    processed = 0

    async with aiohttp.ClientSession(
        headers={"User-Agent": "KHAMCXHunter/3.0"}
    ) as session:
        client = DexscreenerClient(session)

        while time.time() < deadline and processed < config.MAX_TOKENS_PER_RUN:
            results = await asyncio.gather(
                client.get_latest_profiles(),
                client.get_latest_boosts(),
                client.get_top_boosts(),
                client.get_latest_community_takeovers(),
                client.get_latest_ads(),
            )

            source_names = ["profiles", "latest_boosts", "top_boosts", "community", "ads"]
            for name, items in zip(source_names, results):
                log.info(name + ": " + str(len(items)) + " solana tokens")

            all_tokens = {}
            for source_items in results:
                for t in source_items:
                    addr = t.get("tokenAddress")
                    if addr:
                        all_tokens[addr] = t

            # Do not mark tokens as seen until they are actually processed.
            candidate_tokens = [addr for addr in all_tokens if addr not in seen._seen]

            if candidate_tokens:
                log.info("Candidates before processing: " + str(len(candidate_tokens)))

            remaining_slots = config.MAX_TOKENS_PER_RUN - processed
            batch_size = min(30, remaining_slots, len(candidate_tokens))
            current_batch = candidate_tokens[:batch_size]

            if not current_batch:
                remaining = deadline - time.time()
                if remaining > config.POLL_INTERVAL_SEC:
                    await asyncio.sleep(config.POLL_INTERVAL_SEC)
                    continue
                break

            pairs_by_token = await client.get_pairs_batch(current_batch)

            for token_addr in current_batch:
                if time.time() >= deadline or processed >= config.MAX_TOKENS_PER_RUN:
                    break

                # Mark seen only when we reached the processing stage.
                seen.is_new(token_addr)
                pairs = pairs_by_token.get(token_addr, [])
                processed += 1

                if not pairs:
                    signal_logger.log("REJECT", token_addr, "No pair data", {})
                    continue

                best_pair = choose_best_pair(pairs)
                metrics = extract_metrics(best_pair)

                killed, kill_reason = check_kill_switches(metrics)
                if killed:
                    signal_logger.log("KILL", token_addr, kill_reason, metrics)
                    continue

                passed, reason = apply_filters(metrics)
                if not passed:
                    signal_logger.log("REJECT", token_addr, reason, metrics)
                    continue

                score_data = calculate_score(metrics)

                if score_data["total"] >= config.MIN_SCORE_TO_ACCEPT:
                    signal_logger.log(
                        "ACCEPT",
                        token_addr,
                        "Passed filters + dynamic score >= " + str(config.MIN_SCORE_TO_ACCEPT),
                        metrics,
                        score_data,
                    )
                    msg = format_telegram_message(token_addr, metrics, score_data)
                    await send_telegram(session, msg)
                else:
                    signal_logger.log(
                        "REJECT",
                        token_addr,
                        "Score too low: " + str(score_data["total"]),
                        metrics,
                        score_data,
                    )

                await asyncio.sleep(0.05)

            remaining = deadline - time.time()
            if remaining > config.POLL_INTERVAL_SEC:
                await asyncio.sleep(config.POLL_INTERVAL_SEC)
            else:
                break

    seen.save()

    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info("Batch complete in " + str(round(elapsed, 1)) + "s")
    log.info("Processed: " + str(processed))
    log.info("✅ Accepted: " + str(signal_logger.accepted))
    log.info("❌ Rejected: " + str(signal_logger.rejected))
    log.info("💀 Killed: " + str(signal_logger.kill_switched))
    log.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(batch_run())
    except KeyboardInterrupt:
        log.info("Interrupted")
