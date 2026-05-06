"""
KHAMCX Hunter - Fixed Birdeye + DexScreener Discovery Engine
=============================================================
Purpose:
- Finds Solana meme/new tokens.
- Uses DexScreener for discovery + market metrics.
- Uses Birdeye safely as an optional validator.
- Applies KHAMCX-style filters and score system.
- Sends ACCEPT signals to Telegram.
- Logs ACCEPT/REJECT decisions to JSONL.

Important:
- Put BIRDEYE_API_KEY in GitHub Secrets / Replit Secrets.
- Do NOT hard-code your API key in this file.
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

try:
    import config  # optional local config.py
except Exception:
    config = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("khamcx")

# ==================== CONFIG ====================
BIRDEYE_API_KEY = (
    os.getenv("BIRDEYE_API_KEY")
    or getattr(config, "BIRDEYE_API_KEY", None)
    or ""
)
BIRDEYE_BASE = "https://public-api.birdeye.so"

RUN_DURATION_SECONDS = int(os.getenv("RUN_DURATION_SECONDS", "90"))
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "18"))
MAX_TOKENS_PER_RUN = int(os.getenv("MAX_TOKENS_PER_RUN", "180"))
ACCEPT_SCORE = int(os.getenv("ACCEPT_SCORE", "70"))  # مؤقتاً 70 بدل 85 للاختبار

SIGNALS_LOG_FILE = os.getenv("SIGNALS_LOG_FILE", "khamcx_signals.jsonl")
SEEN_TOKENS_FILE = os.getenv("SEEN_TOKENS_FILE", "khamcx_seen.json")
SEEN_TOKENS_TTL_SEC = int(os.getenv("SEEN_TOKENS_TTL_SEC", "14400"))

# KHAMCX PDF filter baseline
MIN_MCAP = float(os.getenv("MIN_MCAP", "25000"))
MAX_MCAP = float(os.getenv("MAX_MCAP", "220000"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "8000"))
MAX_LIQUIDITY = float(os.getenv("MAX_LIQUIDITY", "80000"))
MIN_VOL_5M = float(os.getenv("MIN_VOL_5M", "2000"))
MIN_VOL_1H = float(os.getenv("MIN_VOL_1H", "20000"))
MIN_TXNS_5M = int(os.getenv("MIN_TXNS_5M", "25"))
MIN_TXNS_1H = int(os.getenv("MIN_TXNS_1H", "120"))
MIN_BUY_SELL_RATIO = float(os.getenv("MIN_BUY_SELL_RATIO", "1.2"))
MAX_BUY_SELL_RATIO = float(os.getenv("MAX_BUY_SELL_RATIO", "3.0"))
MIN_PRICE_CHANGE_5M = float(os.getenv("MIN_PRICE_CHANGE_5M", "12"))
MAX_PRICE_CHANGE_5M = float(os.getenv("MAX_PRICE_CHANGE_5M", "40"))
MIN_PAIR_AGE_MIN = float(os.getenv("MIN_PAIR_AGE_MIN", "30"))
MAX_PAIR_AGE_HOURS = float(os.getenv("MAX_PAIR_AGE_HOURS", "8"))

SOL_ADDRESS = "So11111111111111111111111111111111111111112"

# ==================== HELPERS ====================
def now_iso():
    return datetime.now(timezone.utc).isoformat()


def utc_ts():
    return int(time.time())


def safe_float(v, default=0.0):
    try:
        if v in (None, "", "null"):
            return default
        return float(v)
    except Exception:
        return default


def safe_int(v, default=0):
    try:
        if v in (None, "", "null"):
            return default
        return int(float(v))
    except Exception:
        return default


def normalize_address(item):
    if not isinstance(item, dict):
        return None
    for k in [
        "address",
        "tokenAddress",
        "token_address",
        "mint",
        "mintAddress",
        "baseTokenAddress",
    ]:
        if item.get(k):
            return str(item[k]).strip()
    token = item.get("token")
    if isinstance(token, dict):
        for k in ["address", "tokenAddress", "mint"]:
            if token.get(k):
                return str(token[k]).strip()
    return None


def extract_items(data):
    """Birdeye returns shapes like data.items, data.tokens, data as list, etc."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    d = data.get("data", data)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for key in ["items", "tokens", "list", "result"]:
            if isinstance(d.get(key), list):
                return d[key]
    return []


def load_seen(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        if isinstance(raw, dict):
            return {k: int(v) for k, v in raw.items()}
        if isinstance(raw, list):
            now = utc_ts()
            return {str(x): now for x in raw}
    except Exception:
        pass
    return {}


def save_seen(path, seen):
    now = utc_ts()
    clean = {k: v for k, v in seen.items() if now - int(v) <= SEEN_TOKENS_TTL_SEC}
    Path(path).write_text(json.dumps(clean, ensure_ascii=False, indent=2))


def pair_age_minutes(pair):
    created_ms = safe_float(pair.get("pairCreatedAt"))
    if not created_ms:
        return 0.0
    created_sec = created_ms / 1000 if created_ms > 10_000_000_000 else created_ms
    return max(0.0, (time.time() - created_sec) / 60.0)


def txns(pair, window):
    obj = (pair.get("txns") or {}).get(window) or {}
    buys = safe_int(obj.get("buys"))
    sells = safe_int(obj.get("sells"))
    return buys, sells, buys + sells


# ==================== BIRDEYE CLIENT ====================
class BirdeyeClient:
    def __init__(self, session):
        self.session = session
        self.enabled = bool(BIRDEYE_API_KEY)
        self.headers = {
            "X-API-KEY": BIRDEYE_API_KEY,
            "x-chain": "solana",  # هذا هو سبب شائع لخطأ 400/401/403 عند غيابه
            "accept": "application/json",
            "User-Agent": "KHAMCXHunter/1.0",
        }
        if not self.enabled:
            log.warning("BIRDEYE_API_KEY is missing. Birdeye will be skipped; DexScreener still works.")

    async def get_json(self, path, params=None, timeout=12):
        if not self.enabled:
            return None
        url = f"{BIRDEYE_BASE}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    log.warning(f"Birdeye error {resp.status} {path}: {text[:180]}")
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return None
        except Exception as e:
            log.warning(f"Birdeye exception {path}: {e}")
            return None

    async def get_new_tokens(self, limit=50):
        """
        Uses Birdeye new listing endpoint first.
        Falls back to v3 token/list with valid market sorting if new_listing is unavailable.
        """
        # Endpoint commonly used for fresh listings.
        data = await self.get_json(
            "/defi/v2/tokens/new_listing",
            {
                "limit": min(limit, 100),
                "meme_platform_enabled": "true",
            },
        )
        items = extract_items(data)
        if items:
            return [{"address": normalize_address(i), "source": "birdeye_new_listing", "raw": i} for i in items if normalize_address(i)]

        # Safer fallback: do NOT use invalid sort_by=creation_time; that causes 400 on v3 token/list.
        data = await self.get_json(
            "/defi/v3/token/list",
            {
                "sort_by": "volume_1h_usd",
                "sort_type": "desc",
                "min_liquidity": int(MIN_LIQUIDITY),
                "limit": min(limit, 100),
                "offset": 0,
            },
        )
        items = extract_items(data)
        return [{"address": normalize_address(i), "source": "birdeye_v3_list", "raw": i} for i in items if normalize_address(i)]

    async def get_token_overview(self, address):
        data = await self.get_json("/defi/token_overview", {"address": address}, timeout=8)
        if isinstance(data, dict):
            return data.get("data") if isinstance(data.get("data"), dict) else None
        return None

    async def get_price(self, address):
        # This endpoint requires address and x-chain header.
        data = await self.get_json("/defi/price", {"address": address}, timeout=8)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return None


# ==================== DEXSCREENER ====================
class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def get_json(self, url, timeout=10):
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception:
            return None

    async def get_new_tokens(self):
        urls = [
            "https://api.dexscreener.com/token-profiles/latest/v1",
            "https://api.dexscreener.com/token-boosts/latest/v1",
            "https://api.dexscreener.com/token-boosts/top/v1",
        ]
        results = []
        for url in urls:
            data = await self.get_json(url, timeout=8)
            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") not in (None, "solana"):
                        continue
                    addr = normalize_address(item)
                    if addr:
                        results.append({"address": addr, "source": url.split("/")[-2], "raw": item})
        return results

    async def get_pairs(self, addresses):
        result = {}
        clean_addresses = [a for a in addresses if a and a != SOL_ADDRESS]
        for i in range(0, len(clean_addresses), 30):
            batch = clean_addresses[i : i + 30]
            if not batch:
                continue
            url = f"https://api.dexscreener.com/tokens/v1/solana/{','.join(batch)}"
            data = await self.get_json(url, timeout=10)
            if isinstance(data, list):
                for p in data:
                    base = ((p.get("baseToken") or {}).get("address") or "").strip()
                    quote = ((p.get("quoteToken") or {}).get("address") or "").strip()
                    # Prefer base token; if SOL is base, use quote token.
                    key = quote if base == SOL_ADDRESS else base
                    if key:
                        result.setdefault(key, []).append(p)
        return result


# ==================== SCORING ====================
def score_pair(pair, overview=None):
    mcap = safe_float(pair.get("marketCap") or pair.get("fdv"))
    liq = safe_float((pair.get("liquidity") or {}).get("usd"))
    vol5m = safe_float((pair.get("volume") or {}).get("m5"))
    vol1h = safe_float((pair.get("volume") or {}).get("h1"))
    pc5m = safe_float((pair.get("priceChange") or {}).get("m5"))
    age_min = pair_age_minutes(pair)
    buys5, sells5, tx5 = txns(pair, "m5")
    buys1h, sells1h, tx1h = txns(pair, "h1")
    buy_sell_ratio = buys5 / max(sells5, 1)

    reject = []
    score = 0

    # Hard filters first
    if not (MIN_PAIR_AGE_MIN <= age_min <= MAX_PAIR_AGE_HOURS * 60):
        reject.append(f"age {age_min:.1f}m outside {MIN_PAIR_AGE_MIN:.0f}m-{MAX_PAIR_AGE_HOURS:.0f}h")
    if not (MIN_MCAP <= mcap <= MAX_MCAP):
        reject.append(f"mcap ${mcap:,.0f} outside ${MIN_MCAP:,.0f}-${MAX_MCAP:,.0f}")
    if not (MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY):
        reject.append(f"liquidity ${liq:,.0f} outside ${MIN_LIQUIDITY:,.0f}-${MAX_LIQUIDITY:,.0f}")
    if vol5m < MIN_VOL_5M:
        reject.append(f"vol5m ${vol5m:,.0f} < ${MIN_VOL_5M:,.0f}")
    if vol1h < MIN_VOL_1H:
        reject.append(f"vol1h ${vol1h:,.0f} < ${MIN_VOL_1H:,.0f}")
    if tx5 < MIN_TXNS_5M:
        reject.append(f"tx5m {tx5} < {MIN_TXNS_5M}")
    if tx1h < MIN_TXNS_1H:
        reject.append(f"tx1h {tx1h} < {MIN_TXNS_1H}")
    if not (MIN_BUY_SELL_RATIO <= buy_sell_ratio <= MAX_BUY_SELL_RATIO):
        reject.append(f"B/S {buy_sell_ratio:.2f} outside {MIN_BUY_SELL_RATIO}-{MAX_BUY_SELL_RATIO}")
    if not (MIN_PRICE_CHANGE_5M <= pc5m <= MAX_PRICE_CHANGE_5M):
        reject.append(f"5m change {pc5m:.1f}% outside {MIN_PRICE_CHANGE_5M:.0f}-{MAX_PRICE_CHANGE_5M:.0f}%")

    # KHAMCX 100-point model approximation
    # 25 قوة الشارت / الارتداد
    if MIN_PRICE_CHANGE_5M <= pc5m <= 25:
        score += 25
    elif 25 < pc5m <= MAX_PRICE_CHANGE_5M:
        score += 16
    elif pc5m > 0:
        score += 8

    # 20 الفوليوم والسيولة
    if MIN_LIQUIDITY <= liq <= MAX_LIQUIDITY and vol1h >= MIN_VOL_1H and vol5m >= MIN_VOL_5M:
        score += 20
    elif liq >= MIN_LIQUIDITY and vol5m >= MIN_VOL_5M:
        score += 12

    # 15 قوة المشترين والمعاملات
    if tx5 >= MIN_TXNS_5M and tx1h >= MIN_TXNS_1H and buys5 > sells5 and buy_sell_ratio >= MIN_BUY_SELL_RATIO:
        score += 15
    elif buys5 > sells5:
        score += 8

    # 15 سلامة العملة / المحافظ - Birdeye overview إذا توفر
    safety_points = 8
    if overview:
        holder = safe_int(overview.get("holder") or overview.get("holder_count"))
        buy_vol = safe_float(overview.get("buyVolume") or overview.get("buy_volume"))
        sell_vol = safe_float(overview.get("sellVolume") or overview.get("sell_volume"))
        if holder >= 50:
            safety_points += 3
        if buy_vol > sell_vol:
            safety_points += 4
    score += min(safety_points, 15)

    # 15 مسافة صعود آمنة
    if pc5m <= 25 and mcap <= MAX_MCAP:
        score += 15
    elif pc5m <= MAX_PRICE_CHANGE_5M:
        score += 8

    # 10 مناسبة الوقت - GitHub Actions usually runs UTC; use broad golden window KSA/UAE compatible.
    # خطة PDF: 3AM-3PM KSA. UAE = 4AM-4PM. هنا نحسب UTC: KSA 00:00-12:00 UTC.
    hour_utc = datetime.now(timezone.utc).hour
    if 0 <= hour_utc < 12:
        score += 10
    else:
        score += 4

    metrics = {
        "score": int(score),
        "mcap": mcap,
        "liquidity": liq,
        "vol5m": vol5m,
        "vol1h": vol1h,
        "price_change_5m": pc5m,
        "age_min": age_min,
        "buys5m": buys5,
        "sells5m": sells5,
        "tx5m": tx5,
        "tx1h": tx1h,
        "buy_sell_ratio": buy_sell_ratio,
        "reject_reasons": reject,
    }
    return metrics


# ==================== LOGGER & TELEGRAM ====================
class SignalLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.accepted = 0
        self.rejected = 0

    def log(self, decision, token, reason, metrics=None):
        if decision == "ACCEPT":
            self.accepted += 1
            log.info(f"✅ ACCEPT {token[:8]}... {reason}")
        else:
            self.rejected += 1
            log.info(f"❌ REJECT {token[:8]}... {reason}")

        entry = {
            "time": now_iso(),
            "decision": decision,
            "token": token,
            "reason": reason,
            "metrics": metrics or {},
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def send_telegram(session, text):
    token = os.getenv("TELEGRAM_TOKEN") or getattr(config, "TELEGRAM_TOKEN", None)
    chat = os.getenv("TELEGRAM_CHAT_ID") or getattr(config, "TELEGRAM_CHAT_ID", None)
    if not token or not chat:
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                txt = await resp.text()
                log.warning(f"Telegram error {resp.status}: {txt[:160]}")
    except Exception as e:
        log.warning(f"Telegram exception: {e}")


def format_signal(addr, pair, metrics):
    base = pair.get("baseToken") or {}
    symbol = base.get("symbol") or "UNKNOWN"
    url = pair.get("url") or f"https://dexscreener.com/solana/{addr}"
    return (
        "🎯 *KHAMCX ACCEPT*\n"
        f"Token: *{symbol}*\n"
        f"Address: `{addr}`\n"
        f"Score: *{metrics['score']}* / 100\n"
        f"Age: {metrics['age_min']:.1f}m\n"
        f"MC: ${metrics['mcap']:,.0f}\n"
        f"Liquidity: ${metrics['liquidity']:,.0f}\n"
        f"Vol 5M: ${metrics['vol5m']:,.0f}\n"
        f"Vol 1H: ${metrics['vol1h']:,.0f}\n"
        f"Tx 5M/1H: {metrics['tx5m']} / {metrics['tx1h']}\n"
        f"B/S 5M: {metrics['buy_sell_ratio']:.2f}x\n"
        f"5M Change: {metrics['price_change_5m']:.1f}%\n"
        f"[DexScreener]({url}) | [Birdeye](https://birdeye.so/token/{addr}?chain=solana)"
    )


# ==================== MAIN ====================
async def run():
    seen = load_seen(SEEN_TOKENS_FILE)
    logger = SignalLogger(SIGNALS_LOG_FILE)
    connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300)

    async with aiohttp.ClientSession(connector=connector) as session:
        birdeye = BirdeyeClient(session)
        dex = DexscreenerClient(session)

        start = time.time()
        while time.time() - start < RUN_DURATION_SECONDS:
            birdeye_tokens, dex_tokens = await asyncio.gather(
                birdeye.get_new_tokens(60),
                dex.get_new_tokens(),
            )

            candidates = {}
            now = utc_ts()
            for t in birdeye_tokens + dex_tokens:
                addr = t.get("address")
                if not addr or addr == SOL_ADDRESS:
                    continue
                if addr in seen and now - int(seen[addr]) <= SEEN_TOKENS_TTL_SEC:
                    continue
                candidates[addr] = t
                if len(candidates) >= MAX_TOKENS_PER_RUN:
                    break

            if not candidates:
                log.info("No new tokens in this cycle.")
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            log.info(f"New tokens found: {len(candidates)}")
            pairs_by_token = await dex.get_pairs(list(candidates.keys()))

            for addr in list(candidates.keys()):
                seen[addr] = utc_ts()
                pair_list = pairs_by_token.get(addr, [])
                if not pair_list:
                    logger.log("REJECT", addr, "no DexScreener pair", {})
                    continue

                # Best pair = highest USD liquidity
                best_pair = max(pair_list, key=lambda p: safe_float((p.get("liquidity") or {}).get("usd")))
                overview = await birdeye.get_token_overview(addr)
                metrics = score_pair(best_pair, overview)

                if metrics["reject_reasons"]:
                    logger.log("REJECT", addr, "; ".join(metrics["reject_reasons"][:3]), metrics)
                    continue

                if metrics["score"] >= ACCEPT_SCORE:
                    logger.log("ACCEPT", addr, f"Score {metrics['score']}", metrics)
                    await send_telegram(session, format_signal(addr, best_pair, metrics))
                else:
                    logger.log("REJECT", addr, f"Score {metrics['score']} < {ACCEPT_SCORE}", metrics)

            save_seen(SEEN_TOKENS_FILE, seen)
            await asyncio.sleep(POLL_INTERVAL_SEC)

    save_seen(SEEN_TOKENS_FILE, seen)
    log.info(f"Done. Accepted={logger.accepted}, Rejected={logger.rejected}")


if __name__ == "__main__":
    asyncio.run(run())
