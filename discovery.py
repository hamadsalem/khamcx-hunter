"""
KHAMCX Hunter Bot - Discovery Engine with Birdeye (Free Tier)
==============================================================
نسخة معدلة تضيف Birdeye API كمصدر رئيسي
- يستخدم المفتاح المجاني اللي أرسلته
- Rate Limit: 60 طلب/دقيقة
- يركز على New Listings + Token Overview
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

# ====================== BIRDEYE API KEY ======================
# المفتاح اللي أرسلته (Free Tier - 60 طلب/دقيقة)
BIRDEYE_API_KEY = "6a62af5ac9e54e94bb9deb343645480e"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DEFAULT_SAFE_RUN_SECONDS = 75
RUN_DURATION_SECONDS = min(getattr(config, "RUN_DURATION_SECONDS", DEFAULT_SAFE_RUN_SECONDS), DEFAULT_SAFE_RUN_SECONDS)
POLL_INTERVAL_SEC = getattr(config, "POLL_INTERVAL_SEC", 20)  # زدته لـ 20 ثانية عشان الـ Free Tier
MAX_TOKENS_PER_RUN = getattr(config, "MAX_TOKENS_PER_RUN", 150)

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
    keys = ["tokenAddress", "token_address", "address", "base_address", "baseTokenAddress", "mint", "ca", "contract_address"]
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
            return resp.status == 200
    except:
        return False


def format_telegram_message(token_addr, metrics, score_data, decision="ACCEPT"):
    age_min = (metrics.get("age_seconds") or 0) / 60
    score = score_data["total"]
    breakdown = score_data["breakdown"]
    reasons = score_data.get("reasons", [])
    mode = metrics.get("mode", "UNKNOWN")

    if decision == "ACCEPT":
        title = "🎯 *KHAMCX Hunter - ACCEPT*"
        status_emoji = "🟢" if score >= 90 else "🟡"
        status_text = "EXCELLENT - فرصة قوية" if score >= 90 else "GOOD - فرصة مقبولة"
    else:
        title = "👀 *KHAMCX Hunter - WATCH*"
        status_emoji = "🟠"
        status_text = "WATCH - قريبة من القبول، راقب فقط"

    msg = f"{title}\n{status_emoji} *{status_text}*\n*Mode: {mode}*\n*Score: {score}/100*\n\n"
    msg += f"*Contract:*\n`{token_addr}`\n\n"
    msg += "📊 *Score Breakdown:*\n"
    msg += f"├ Chart Strength: {breakdown['chart_strength']}/25\n"
    msg += f"├ Volume/Liquidity: {breakdown['volume_liquidity']}/20\n"
    msg += f"├ Buyers Strength: {breakdown['buyers_strength']}/15\n"
    msg += f"├ Token Safety: {breakdown['token_safety']}/15\n"
    msg += f"├ Growth Room: {breakdown['growth_room']}/15\n"
    msg += f"└ Trade Timing: {breakdown['trade_timing']}/10\n\n"

    if reasons:
        msg += "🧠 *Scoring Notes:*\n"
        for r in reasons[:6]:
            msg += f"• {r}\n"
        msg += "\n"

    msg += "📊 *Market Data:*\n"
    msg += f"├ MCap: ${int(metrics.get('mcap_usd', 0)):,}\n"
    msg += f"├ Liquidity: ${int(metrics.get('liquidity_usd', 0)):,}\n"
    msg += f"├ DEX: {metrics.get('dex_id', '')}\n"
    msg += f"└ Age: {round(age_min, 1)} min\n\n"
    msg += "📈 *Volume:*\n"
    msg += f"├ 5m: ${int(metrics.get('volume_5m_usd', 0)):,}\n"
    msg += f"├ 1h: ${int(metrics.get('volume_1h_usd', 0)):,}\n"
    msg += f"└ 24h: ${int(metrics.get('volume_24h_usd', 0)):,}\n\n"
    msg += "🔗 *Links:*\n"
    msg += f"[Dexscreener](https://dexscreener.com/solana/{token_addr}) | [Birdeye](https://birdeye.so/token/{token_addr}) | [GMGN](https://gmgn.ai/sol/token/{token_addr})\n\n"
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
            except:
                self._seen = {}

    def save(self):
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._seen, f)
        except:
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
            log.info(f"✅ ACCEPT {token_addr[:8]}... Score={score_data['total']}")
        elif decision == "WATCH":
            self.watched += 1
            log.info(f"👀 WATCH {token_addr[:8]}... Score={score_data['total']}")
        elif decision == "KILL":
            self.kill_switched += 1
        else:
            self.rejected += 1
            if VERBOSE_REJECTS:
                log.info(f"❌ REJECT {token_addr[:8]}... {reason}")


# ============================================================
# METRICS + FILTERS + SCORING (نفس السابق)
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
    m["liquidity_usd"] = safe_number(liquidity.get("usd", 0) if isinstance(liquidity, dict) else pair.get("liquidity", 0))

    volume = pair.get("volume", {}) or {}
    if isinstance(volume, dict):
        m["volume_5m_usd"] = safe_number(volume.get("m5", 0))
        m["volume_1h_usd"] = safe_number(volume.get("h1", 0))
        m["volume_24h_usd"] = safe_number(volume.get("h24", 0))
    else:
        m["volume_5m_usd"] = safe_number(pair.get("volume_5m", 0))
        m["volume_1h_usd"] = safe_number(pair.get("volume_1h", 0))
        m["volume_24h_usd"] = safe_number(pair.get("volume_24h", 0))

    txns = pair.get("txns", {}) or {}
    m5 = txns.get("m5", {}) if isinstance(txns, dict) else {}
    h1 = txns.get("h1", {}) if isinstance(txns, dict) else {}
    m["buys_5m"] = as_int(m5.get("buys", pair.get("buys_5m", 0)))
    m["sells_5m"] = as_int(m5.get("sells", pair.get("sells_5m", 0)))
    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)
    m["txns_5m_total"] = m["buys_5m"] + m["sells_5m"]
    m["txns_1h_total"] = as_int(h1.get("buys", 0)) + as_int(h1.get("sells", 0))

    price_change = pair.get("priceChange", {}) or {}
    m["price_change_5m"] = safe_number(price_change.get("m5", 0) if isinstance(price_change, dict) else pair.get("price_change_percent5m", 0))
    m["price_change_1h"] = safe_number(price_change.get("h1", 0) if isinstance(price_change, dict) else pair.get("price_change_percent1h", 0))
    m["price_change_24h"] = safe_number(price_change.get("h24", 0) if isinstance(price_change, dict) else pair.get("price_change_percent", 0))

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

def choose_best_pair(pairs):
    valid_pairs = [p for p in pairs if (p.get("chainId") == "solana" or not p.get("chainId"))]
    if not valid_pairs:
        valid_pairs = pairs
    return max(valid_pairs, key=lambda p: safe_number(p.get("liquidity", {}).get("usd", 0) if isinstance(p.get("liquidity"), dict) else 0))

def check_kill_switches(metrics):
    mode = metrics.get("mode", "POST_GRADUATION")
    pc5 = metrics["price_change_5m"]
    pc24 = metrics["price_change_24h"]
    liq = metrics["liquidity_usd"]
    buys5 = metrics.get("buys_5m", 0)
    sells5 = metrics.get("sells_5m", 0)
    txns5 = metrics.get("txns_5m_total", 0)

    if mode == "EARLY_PUMPFUN":
        if pc5 > 120: return True, "Early pump vertical candle"
        if pc24 > 800: return True, "Extremely pumped"
        if txns5 < 20: return True, "Dead early activity"
        if sells5 >= 2.2 * max(buys5, 1): return True, "Sell pressure too high"
        return False, None

    k = getattr(config, "KILL_SWITCHES", {})
    if pc5 > k.get("max_price_change_5m_kill", 80.0):
        return True, "Long green candle"
    if pc24 > k.get("max_price_change_24h", 400.0):
        return True, "Already pumped too much"
    if liq < k.get("min_liquidity_kill", 5000):
        return True, "Critical low liquidity"
    if txns5 < 20: return True, "Dead recent activity"
    if sells5 >= 2 * max(buys5, 1): return True, "Sell pressure too high"
    return False, None

def apply_filters(metrics):
    mode = metrics.get("mode", "POST_GRADUATION")
    age = metrics.get("age_seconds")
    if age is None: return False, "Age unknown"

    mcap = metrics["mcap_usd"]
    liq = metrics["liquidity_usd"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]
    buys5 = metrics["buys_5m"]
    txns5 = metrics["txns_5m_total"]
    txns1 = metrics["txns_1h_total"]
    ratio = metrics["buy_sell_ratio"]
    pc5 = metrics["price_change_5m"]

    f = getattr(config, "HUNTER_FILTERS", {})

    if mode == "EARLY_PUMPFUN":
        if age < 120: return False, "Too fresh"
        if age > 1800: return False, "Early pumpfun too old"
        if mcap < 7000 or mcap > 70000: return False, "MCap out of range"
        if vol5 < 5000: return False, "Vol too low"
        if buys5 < 50: return False, "Buys too few"
        if ratio < 1.05 or ratio > 2.8: return False, "B/S out of range"
        return True, "Early pumpfun passed"

    if mode == "EARLY_LIQUID":
        if age < 300: return False, "Too fresh"
        if age >= 1800: return False, "Not early liquid"
        if mcap < 12000 or mcap > 120000: return False, "MCap out of range"
        if liq < 8000: return False, "Liq too low"
        if vol5 < 8000: return False, "Vol too low"
        if ratio < 1.05 or ratio > 2.7: return False, "B/S out of range"
        return True, "Early liquid passed"

    # POST_GRADUATION
    if age < f.get("min_age_seconds", 1200): return False, "Too young"
    if age > f.get("max_age_seconds", 43200): return False, "Too old"
    if mcap < f.get("min_mcap_usd", 15000): return False, "MCap too low"
    if mcap > f.get("max_mcap_usd", 300000): return False, "MCap too high"
    if liq < f.get("min_liquidity_usd", 6000): return False, "Liq too low"
    if vol5 < f.get("min_volume_5m_usd", 1500): return False, "Vol 5m too low"
    if vol1 < f.get("min_volume_1h_usd", 15000): return False, "Vol 1h too low"
    if buys5 < f.get("min_buys_5m", 18): return False, "Buys too few"
    if txns1 < f.get("min_txns_1h", 90): return False, "Txns 1h too few"
    if ratio < f.get("min_buy_sell_ratio", 1.1): return False, "B/S too low"
    if ratio > f.get("max_buy_sell_ratio", 3.5): return False, "B/S suspicious"
    if pc5 < f.get("min_price_change_5m", -20.0): return False, "Big drop"
    if pc5 > f.get("max_price_change_5m", 35.0): return False, "Big pump"
    return True, "Post-graduation passed"


def calculate_score(metrics):
    breakdown = {"chart_strength": 0, "volume_liquidity": 0, "buyers_strength": 0, "token_safety": 0, "growth_room": 0, "trade_timing": 0}
    reasons = []
    mode = metrics.get("mode", "POST_GRADUATION")
    pc5 = metrics["price_change_5m"]
    vol5 = metrics["volume_5m_usd"]
    vol1 = metrics["volume_1h_usd"]
    ratio = metrics["buy_sell_ratio"]
    buys5 = metrics["buys_5m"]
    liq = metrics["liquidity_usd"]
    mcap = metrics["mcap_usd"]

    if mode.startswith("EARLY"):
        if -15 <= pc5 <= 25: breakdown["chart_strength"] += 10
        elif -35 <= pc5 < -15: breakdown["chart_strength"] += 6
        if vol5 >= 5000: breakdown["chart_strength"] += 5
    else:
        if -5 <= pc5 <= 10: breakdown["chart_strength"] += 10
        elif -12 <= pc5 < -5: breakdown["chart_strength"] += 5
        if vol5 >= 5000: breakdown["chart_strength"] += 3

    if vol5 >= 15000: breakdown["volume_liquidity"] += 8
    elif vol5 >= 5000: breakdown["volume_liquidity"] += 5
    if liq >= 8000: breakdown["volume_liquidity"] += 5

    if 1.2 <= ratio <= 2.5: breakdown["buyers_strength"] += 6
    if buys5 >= 40: breakdown["buyers_strength"] += 5

    if liq >= 8000: breakdown["token_safety"] += 4
    if mcap > 0 and liq / mcap >= 0.08: breakdown["token_safety"] += 3

    if 15000 <= mcap <= 120000: breakdown["growth_room"] += 6
    if liq >= 8000: breakdown["growth_room"] += 3

    hour = now_utc_hour()
    if hour in getattr(config, "GOLDEN_HOURS_UTC", list(range(0,12))):
        breakdown["trade_timing"] = 10
    else:
        breakdown["trade_timing"] = 6

    base_total = sum(breakdown.values())
    total = clamp(base_total, 0, 100)
    return {"total": int(total), "base_total": int(base_total), "adjustment": 0, "breakdown": breakdown, "reasons": reasons}


# ============================================================
# DISCOVERY CLIENTS
# ============================================================

class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def _get_token_list(self, url, label):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200: return []
                data = await resp.json()
                if not isinstance(data, list): return []
                out = []
                for item in data:
                    if item.get("chainId") == "solana":
                        addr = normalize_token_address(item)
                        if addr: out.append(source_record(addr, label, item))
                return out
        except:
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
        if not token_addrs: return {}
        result = {addr: [] for addr in token_addrs}
        for i in range(0, len(token_addrs), 30):
            batch = token_addrs[i:i+30]
            url = ENDPOINT_TOKEN_PAIRS + "/" + ",".join(batch)
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                    if resp.status != 200: continue
                    data = await resp.json()
                    pairs = data if isinstance(data, list) else data.get("pairs", [])
                    for pair in pairs:
                        base = (pair.get("baseToken") or {}).get("address")
                        for addr in batch:
                            if addr == base:
                                result.setdefault(addr, []).append(pair)
            except:
                continue
        return result


class BirdeyeClient:
    """Birdeye API Client (Free Tier)"""
    BASE_URL = "https://public-api.birdeye.so"

    def __init__(self, session, api_key):
        self.session = session
        self.api_key = api_key
        self.headers = {
            "X-API-KEY": api_key,
            "accept": "application/json",
            "User-Agent": "KHAMCXHunter/6.0"
        }

    async def get_new_listings(self, limit=50):
        """جلب التوكنات الجديدة"""
        url = f"{self.BASE_URL}/defi/v3/token/new_listing?limit={limit}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    log.warning(f"Birdeye new_listing error: {resp.status}")
                    return []
                data = await resp.json()
                items = data.get("data", []) if isinstance(data, dict) else []
                out = []
                for item in items:
                    addr = normalize_token_address(item)
                    if addr:
                        out.append(source_record(addr, "birdeye_new", item))
                return out
        except Exception as e:
            log.warning(f"Birdeye new_listing exception: {e}")
            return []

    async def get_token_overview(self, token_address):
        """جلب تفاصيل التوكن (Market Cap, Liquidity, Volume...)"""
        url = f"{self.BASE_URL}/defi/token_overview?address={token_address}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("data") if isinstance(data, dict) else None
        except:
            return None

    async def get_sources(self):
        """المصدر الرئيسي من Birdeye"""
        tokens = await self.get_new_listings(60)
        log.info(f"birdeye_new: {len(tokens)} tokens")
        return [tokens]


class GmgnPublicClient:
    """GMGN مع 3 calls فقط (للتوازن مع Birdeye)"""
    BASE = "https://gmgn.ai/defi/quotation/v1/rank/sol/swaps"

    def __init__(self, session):
        self.session = session

    async def get_rank(self, period, orderby, direction="desc"):
        params = {"orderby": orderby, "direction": direction, "filters[]": ["not_honeypot"]}
        url = self.BASE + "/" + period + "?" + urlencode(params, doseq=True)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://gmgn.ai/",
            "User-Agent": "Mozilla/5.0 KHAMCXHunter/6.0",
        }
        label = f"gmgn_{period}_{orderby}"
        try:
            async with self.session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status in (403, 429):
                    log.warning(f"{label} blocked: {resp.status}")
                    return []
                if resp.status != 200: return []
                data = await resp.json(content_type=None)
                rank = []
                if isinstance(data, dict):
                    payload = data.get("data") or {}
                    rank = payload.get("rank") or payload.get("list") or []
                if not isinstance(rank, list): return []
                out = []
                for item in rank:
                    addr = normalize_token_address(item)
                    if addr: out.append(source_record(addr, label, item))
                return out
        except:
            return []

    async def get_sources(self):
        calls = [
            self.get_rank("5m", "open_timestamp"),
            self.get_rank("1h", "volume"),
            self.get_rank("1h", "smartmoney"),
        ]
        return await asyncio.gather(*calls)


# ============================================================
# MAIN
# ============================================================

async def collect_candidates(dex_client, birdeye_client, gmgn_client):
    dex_results, birdeye_results, gmgn_results = await asyncio.gather(
        dex_client.get_sources(),
        birdeye_client.get_sources(),
        gmgn_client.get_sources(),
    )

    all_lists = []
    for group in dex_results + birdeye_results + gmgn_results:
        all_lists.append(group)

    merged = {}
    source_counts = {}
    for source_items in all_lists:
        for item in source_items:
            addr = item.get("tokenAddress")
            source = item.get("source", "unknown")
            if not addr: continue
            source_counts[source] = source_counts.get(source, 0) + 1
            if addr not in merged:
                merged[addr] = {"tokenAddress": addr, "sources": set(), "raw": []}
            merged[addr]["sources"].add(source)
            if item.get("raw"):
                merged[addr]["raw"].append(item.get("raw"))

    for source, count in sorted(source_counts.items()):
        log.info(f"{source}: {count} tokens")

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
    log.info("KHAMCX Hunter Bot - Birdeye Free Tier + Dexscreener + GMGN")
    log.info(f"Birdeye Rate Limit: 60 requests/min")
    log.info(f"Accept score: {ACCEPT_SCORE_POST}/100")
    log.info(f"Runtime: {RUN_DURATION_SECONDS}s | Interval: {POLL_INTERVAL_SEC}s")
    log.info(f"Loaded {len(seen)} previously seen tokens")
    log.info("=" * 60)

    processed = 0
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers={"User-Agent": "KHAMCXHunter/6.0"}) as session:
        dex_client = DexscreenerClient(session)
        birdeye_client = BirdeyeClient(session, BIRDEYE_API_KEY)
        gmgn_client = GmgnPublicClient(session)

        while time.time() < deadline and processed < MAX_TOKENS_PER_RUN:
            records = await collect_candidates(dex_client, birdeye_client, gmgn_client)
            candidate_records = [r for r in records if not seen.is_known(r["tokenAddress"])]

            if candidate_records:
                log.info(f"Candidates before processing: {len(candidate_records)}")
            else:
                if deadline - time.time() > POLL_INTERVAL_SEC:
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    continue
                break

            current_records = candidate_records[:MAX_TOKENS_PER_RUN - processed]
            current_addrs = [r["tokenAddress"] for r in current_records]

            pairs_by_token = await dex_client.get_pairs_batch(current_addrs)

            for rec in current_records:
                if time.time() >= deadline or processed >= MAX_TOKENS_PER_RUN: break

                token_addr = rec["tokenAddress"]
                sources = rec.get("sources", [])
                pairs = pairs_by_token.get(token_addr, [])
                processed += 1
                seen.mark_seen(token_addr)

                if not pairs:
                    signal_logger.log("REJECT", token_addr, "No pair data", {"sources": sources})
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

                if score_data["total"] >= accept_score:
                    signal_logger.log("ACCEPT", token_addr, f"Passed {mode}", metrics, score_data)
                    msg = format_telegram_message(token_addr, metrics, score_data, "ACCEPT")
                    await send_telegram(session, msg)
                else:
                    signal_logger.log("REJECT", token_addr, f"Score too low: {score_data['total']}", metrics, score_data)

                await asyncio.sleep(0.03)

            if deadline - time.time() > POLL_INTERVAL_SEC:
                await asyncio.sleep(POLL_INTERVAL_SEC)
            else:
                break

    seen.save()
    elapsed = time.time() - start_time
    log.info("=" * 60)
    log.info(f"Batch complete in {round(elapsed, 1)}s")
    log.info(f"Processed: {processed}")
    log.info(f"✅ Accepted: {signal_logger.accepted}")
    log.info(f"👀 Watched: {signal_logger.watched}")
    log.info(f"❌ Rejected: {signal_logger.rejected}")
    log.info(f"💀 Killed: {signal_logger.kill_switched}")
    log.info("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(batch_run())
    except KeyboardInterrupt:
        log.info("Interrupted")
