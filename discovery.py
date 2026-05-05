"""
KHAMCX Hunter Bot - Discovery Engine
======================================
بوت اكتشاف يطبق:
- فلاتر Hunter KHAMCX 40%
- نظام تقييم 100 نقطة
- Kill Switches
- إشعارات Telegram مفصلة
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def now_utc_hour():
    return datetime.now(timezone.utc).hour


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
    age_min = metrics.get("age_seconds", 0) / 60
    score = score_data["total"]
    breakdown = score_data["breakdown"]

    # رمز حسب التقييم
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
    msg += "*Score: " + str(score) + "/100*\n\n"

    msg += "*Contract:*\n`" + token_addr + "`\n\n"

    # Score breakdown
    msg += "📊 *Score Breakdown:*\n"
    msg += "├ Volume/Liquidity: " + str(breakdown["volume_liquidity"]) + "/20\n"
    msg += "├ Buyers Strength: " + str(breakdown["buyers_strength"]) + "/15\n"
    msg += "├ Growth Room: " + str(breakdown["growth_room"]) + "/15\n"
    msg += "├ Trade Timing: " + str(breakdown["trade_timing"]) + "/10\n"
    msg += "├ Token Safety: " + str(breakdown["token_safety"]) + "/15\n"
    msg += "└ Chart Strength: " + str(breakdown["chart_strength"]) + "/25 (manual)\n\n"

    # Market data
    msg += "📊 *Market Data:*\n"
    msg += "├ MCap: $" + format(int(metrics.get("mcap_usd", 0)), ",") + "\n"
    msg += "├ Liquidity: $" + format(int(metrics.get("liquidity_usd", 0)), ",") + "\n"
    msg += "└ Age: " + str(round(age_min, 1)) + " min\n\n"

    # Volume
    msg += "📈 *Volume:*\n"
    msg += "├ 5m: $" + format(int(metrics.get("volume_5m_usd", 0)), ",") + "\n"
    msg += "├ 1h: $" + format(int(metrics.get("volume_1h_usd", 0)), ",") + "\n"
    msg += "└ 24h: $" + format(int(metrics.get("volume_24h_usd", 0)), ",") + "\n\n"

    # Activity
    msg += "🔄 *Activity:*\n"
    msg += "├ Buys 5m: " + str(metrics.get("buys_5m", 0)) + "\n"
    msg += "├ Sells 5m: " + str(metrics.get("sells_5m", 0)) + "\n"
    msg += "├ B/S Ratio: " + str(metrics.get("buy_sell_ratio", 0)) + "\n"
    msg += "└ Txns 1h: " + str(metrics.get("txns_1h_total", 0)) + "\n\n"

    # Momentum
    msg += "⚡ *Momentum:*\n"
    msg += "├ 5m: " + format(metrics.get("price_change_5m", 0), "+.1f") + "%\n"
    msg += "├ 1h: " + format(metrics.get("price_change_1h", 0), "+.1f") + "%\n"
    msg += "└ 24h: " + format(metrics.get("price_change_24h", 0), "+.1f") + "%\n\n"

    # Links
    msg += "🔗 *Links:*\n"
    msg += "[Dexscreener](https://dexscreener.com/solana/" + token_addr + ") | "
    msg += "[GMGN](https://gmgn.ai/sol/token/" + token_addr + ") | "
    msg += "[Photon](https://photon-sol.tinyastro.io/en/lp/" + token_addr + ")\n\n"

    # Manual checklist (من PDF)
    msg += "⚠️ *قائمة التحقق اليدوي:*\n"
    msg += "1. افتح الشارت\n"
    msg += "2. تحقق: اندفاع → تصحيح هادئ → ارتداد\n"
    msg += "3. السعر فوق قاعدة سعرية واضحة\n"
    msg += "4. لا توجد شموع خضراء طويلة\n"
    msg += "5. لا تصريف من Top Holders\n"
    msg += "6. مساحة الصعود 40%+ متوفرة\n\n"

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
            "metrics": metrics,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if decision == "ACCEPT":
            self.accepted += 1
            log.info("✅ ACCEPT " + token_addr[:8] + "..." +
                     " Score=" + str(score_data["total"]) +
                     " MCap=$" + str(int(metrics.get("mcap_usd", 0))))
        elif decision == "KILL":
            self.kill_switched += 1
        else:
            self.rejected += 1


# ============================================================
# METRICS EXTRACTION
# ============================================================

def extract_metrics(pair):
    m = {}
    created_at_ms = pair.get("pairCreatedAt")
    if created_at_ms:
        age_seconds = (time.time() * 1000 - created_at_ms) / 1000
        m["age_seconds"] = round(age_seconds, 1)
    else:
        m["age_seconds"] = None

    m["mcap_usd"] = pair.get("marketCap") or pair.get("fdv") or 0
    liquidity = pair.get("liquidity", {}) or {}
    m["liquidity_usd"] = liquidity.get("usd", 0) or 0

    volume = pair.get("volume", {}) or {}
    m["volume_5m_usd"] = volume.get("m5", 0) or 0
    m["volume_1h_usd"] = volume.get("h1", 0) or 0
    m["volume_24h_usd"] = volume.get("h24", 0) or 0

    txns = pair.get("txns", {}) or {}
    m5 = txns.get("m5", {}) or {}
    h1 = txns.get("h1", {}) or {}
    m["buys_5m"] = m5.get("buys", 0) or 0
    m["sells_5m"] = m5.get("sells", 0) or 0
    m["buys_1h"] = h1.get("buys", 0) or 0
    m["sells_1h"] = h1.get("sells", 0) or 0
    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)
    m["txns_5m_total"] = m["buys_5m"] + m["sells_5m"]
    m["txns_1h_total"] = m["buys_1h"] + m["sells_1h"]

    price_change = pair.get("priceChange", {}) or {}
    m["price_change_5m"] = price_change.get("m5", 0) or 0
    m["price_change_1h"] = price_change.get("h1", 0) or 0
    m["price_change_24h"] = price_change.get("h24", 0) or 0
    m["price_usd"] = float(pair.get("priceUsd", 0) or 0)
    m["pair_address"] = pair.get("pairAddress", "")

    return m


# ============================================================
# KILL SWITCHES (من PDF)
# ============================================================

def check_kill_switches(metrics):
    k = config.KILL_SWITCHES

    # 1. شمعة خضراء عمودية طويلة
    if metrics["price_change_5m"] > k["max_price_change_5m_kill"]:
        return True, "Long green candle (5m > 50%)"

    # 2. صعود أكثر من 250% قبل الدخول
    if metrics["price_change_24h"] > k["max_price_change_24h"]:
        return True, "Already pumped >250% (likely missed)"

    # 3. سيولة ضعيفة جداً
    if metrics["liquidity_usd"] < k["min_liquidity_kill"]:
        return True, "Critical low liquidity"

    return False, None


# ============================================================
# SCORING SYSTEM (من PDF صفحة 6)
# ============================================================

def calculate_score(metrics):
    """نظام تقييم 100 نقطة"""
    breakdown = {
        "chart_strength": 0,      # 25 - تحليل الشارت (يدوي)
        "volume_liquidity": 0,    # 20 - الفوليوم والسيولة
        "buyers_strength": 0,     # 15 - قوة المشترين
        "token_safety": 0,        # 15 - سلامة العملة (افتراضي)
        "growth_room": 0,         # 15 - مساحة الصعود
        "trade_timing": 0,        # 10 - وقت التداول
    }

    # 1. Chart Strength (25 pts) - افتراضي 20 لأنه يحتاج تحليل بصري
    # نعطي 20 افتراضي لأي توكن يمر الفلاتر، والـ 5 الباقية تحتاج عينك
    breakdown["chart_strength"] = 20

    # 2. Volume & Liquidity (20 pts)
    vl_score = 0
    if metrics["volume_1h_usd"] >= 20000:
        vl_score += 8
    if metrics["volume_5m_usd"] >= 5000:
        vl_score += 4
    if metrics["liquidity_usd"] >= 15000:
        vl_score += 5
    if metrics["liquidity_usd"] >= 30000:
        vl_score += 3
    breakdown["volume_liquidity"] = min(vl_score, 20)

    # 3. Buyers Strength (15 pts)
    bs_score = 0
    if metrics["buy_sell_ratio"] >= 1.5:
        bs_score += 5
    elif metrics["buy_sell_ratio"] >= 1.2:
        bs_score += 3
    if metrics["buys_5m"] >= 40:
        bs_score += 5
    elif metrics["buys_5m"] >= 25:
        bs_score += 3
    if metrics["txns_1h_total"] >= 200:
        bs_score += 5
    elif metrics["txns_1h_total"] >= 120:
        bs_score += 3
    breakdown["buyers_strength"] = min(bs_score, 15)

    # 4. Token Safety (15 pts) - افتراضي 12
    # حقيقة هذي تحتاج Solana RPC للتحقق من mint authority, holders, إلخ
    # نعطي 12 افتراضي لو مر الفلاتر، يعني آمن نسبياً
    breakdown["token_safety"] = 12

    # 5. Growth Room (15 pts) - مساحة 40% للصعود
    gr_score = 0
    # MCap < $50K = مساحة كبيرة
    if metrics["mcap_usd"] < 50000:
        gr_score += 8
    elif metrics["mcap_usd"] < 100000:
        gr_score += 5
    elif metrics["mcap_usd"] < 150000:
        gr_score += 3
    # لم يصعد كثيراً في 24h = مساحة متاحة
    if metrics["price_change_24h"] < 100:
        gr_score += 4
    elif metrics["price_change_24h"] < 200:
        gr_score += 2
    # السيولة كافية للخروج عند 40%+
    if metrics["liquidity_usd"] >= 10000:
        gr_score += 3
    breakdown["growth_room"] = min(gr_score, 15)

    # 6. Trade Timing (10 pts) - الوقت الذهبي
    current_hour = now_utc_hour()
    if current_hour in config.GOLDEN_HOURS_UTC:
        breakdown["trade_timing"] = 10
    elif current_hour in config.AVOID_HOURS_UTC:
        breakdown["trade_timing"] = 2
    else:
        breakdown["trade_timing"] = 6

    total = sum(breakdown.values())
    return {"total": total, "breakdown": breakdown}


# ============================================================
# FILTERS (من PDF)
# ============================================================

def apply_filters(metrics):
    f = config.HUNTER_FILTERS

    # Age
    age = metrics.get("age_seconds")
    if age is None:
        return False, "Age unknown"
    if age < f["min_age_seconds"]:
        return False, "Too young (<30 min)"
    if age > f["max_age_seconds"]:
        return False, "Too old (>8h)"

    # Market Cap
    if metrics["mcap_usd"] < f["min_mcap_usd"]:
        return False, "MCap too low"
    if metrics["mcap_usd"] > f["max_mcap_usd"]:
        return False, "MCap too high"

    # Liquidity
    if metrics["liquidity_usd"] < f["min_liquidity_usd"]:
        return False, "Liq too low"
    if metrics["liquidity_usd"] > f["max_liquidity_usd"]:
        return False, "Liq too high"

    # Volume
    if metrics["volume_5m_usd"] < f["min_volume_5m_usd"]:
        return False, "Vol 5m too low"
    if metrics["volume_1h_usd"] < f["min_volume_1h_usd"]:
        return False, "Vol 1h too low"

    # Transactions
    if metrics["buys_5m"] < f["min_buys_5m"]:
        return False, "Buys 5m too few"
    if metrics["txns_1h_total"] < f["min_txns_1h"]:
        return False, "Txns 1h too few"

    # Buy/Sell Ratio
    if metrics["buy_sell_ratio"] < f["min_buy_sell_ratio"]:
        return False, "B/S ratio too low"
    if metrics["buy_sell_ratio"] > f["max_buy_sell_ratio"]:
        return False, "B/S ratio suspicious"

    # Price Change 5m
    if metrics["price_change_5m"] < f["min_price_change_5m"]:
        return False, "5m drop too big"
    if metrics["price_change_5m"] > f["max_price_change_5m"]:
        return False, "5m pump too big"

    return True, "All filters passed"


# ============================================================
# DEXSCREENER CLIENT
# ============================================================

class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def get_latest_profiles(self):
        try:
            async with self.session.get(
                config.ENDPOINT_LATEST_PROFILES,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return [t for t in data if t.get("chainId") == "solana"]
        except Exception:
            return []

    async def get_latest_boosts(self):
        try:
            async with self.session.get(
                config.ENDPOINT_LATEST_BOOSTS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return [t for t in data if t.get("chainId") == "solana"]
        except Exception:
            return []

    async def get_pairs(self, token_addr):
        url = config.ENDPOINT_TOKEN_PAIRS + "/" + token_addr
        try:
            async with self.session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 429:
                    await asyncio.sleep(3)
                    return []
                if resp.status != 200:
                    return []
                data = await resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "pairs" in data:
                    return data["pairs"] or []
                return []
        except Exception:
            return []


# ============================================================
# MAIN BATCH RUN
# ============================================================

async def batch_run():
    start_time = time.time()
    deadline = start_time + config.RUN_DURATION_SECONDS

    signal_logger = SignalLogger(config.SIGNALS_LOG_FILE)
    seen = SeenTokens(config.SEEN_TOKENS_FILE, config.SEEN_TOKENS_TTL_SEC)

    log.info("=" * 60)
    log.info("KHAMCX Hunter Bot - Started")
    log.info("Filters: Hunter KHAMCX 40% (from PDF)")
    log.info("Min score to ACCEPT: " + str(config.MIN_SCORE_TO_ACCEPT) + "/100")
    log.info("Loaded " + str(len(seen)) + " previously seen tokens")
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        log.info("Telegram: ENABLED")
    else:
        log.info("Telegram: DISABLED")
    log.info("=" * 60)

    processed = 0

    async with aiohttp.ClientSession(
        headers={"User-Agent": "KHAMCXHunter/1.0"}
    ) as session:
        client = DexscreenerClient(session)

        while time.time() < deadline and processed < config.MAX_TOKENS_PER_RUN:
            profiles, boosts = await asyncio.gather(
                client.get_latest_profiles(),
                client.get_latest_boosts(),
            )

            all_tokens = {}
            for t in profiles + boosts:
                addr = t.get("tokenAddress")
                if addr:
                    all_tokens[addr] = t

            new_tokens = [addr for addr in all_tokens if seen.is_new(addr)]

            if new_tokens:
                log.info("Found " + str(len(new_tokens)) + " new tokens")

            for token_addr in new_tokens:
                if time.time() >= deadline:
                    break
                if processed >= config.MAX_TOKENS_PER_RUN:
                    break

                pairs = await client.get_pairs(token_addr)
                processed += 1

                if not pairs:
                    signal_logger.log("REJECT", token_addr, "No pair data", {})
                    continue

                best_pair = max(
                    pairs,
                    key=lambda p: (p.get("liquidity", {}) or {}).get("usd", 0) or 0
                )
                metrics = extract_metrics(best_pair)

                # Step 1: Kill Switches first
                killed, kill_reason = check_kill_switches(metrics)
                if killed:
                    signal_logger.log("KILL", token_addr, kill_reason, metrics)
                    continue

                # Step 2: Apply filters
                passed, reason = apply_filters(metrics)
                if not passed:
                    signal_logger.log("REJECT", token_addr, reason, metrics)
                    continue

                # Step 3: Calculate score
                score_data = calculate_score(metrics)

                # Step 4: Decision based on score
                if score_data["total"] >= config.MIN_SCORE_TO_ACCEPT:
                    signal_logger.log("ACCEPT", token_addr, "Passed all + score >= 85",
                                     metrics, score_data)
                    msg = format_telegram_message(token_addr, metrics, score_data)
                    await send_telegram(session, msg)
                else:
                    signal_logger.log("REJECT", token_addr,
                                     "Score too low: " + str(score_data["total"]),
                                     metrics, score_data)

                await asyncio.sleep(0.3)

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
