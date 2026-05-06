"""
KHAMCX Hunter - Birdeye v2 (Fixed & Improved)
=============================================
- تم إصلاح مشكلة 404 في Birdeye
- يستخدم Endpoint صحيح: /defi/v3/token/list
- أضفت دعم token_overview لجلب بيانات أفضل
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("khamcx")

# ==================== BIRDEYE CONFIG ====================
BIRDEYE_API_KEY = "6a62af5ac9e54e94bb9deb343645480e"
BIRDEYE_BASE = "https://public-api.birdeye.so"

# ==================== GENERAL CONFIG ====================
RUN_DURATION_SECONDS = 90
POLL_INTERVAL_SEC = 18
MAX_TOKENS_PER_RUN = 180
ACCEPT_SCORE = 78

SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400


# ==================== HELPERS ====================
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except:
        return default

def normalize_address(item):
    if isinstance(item, dict):
        for k in ["tokenAddress", "address", "mint"]:
            if item.get(k):
                return item[k]
    return None


# ==================== BIRDEYE CLIENT ====================
class BirdeyeClient:
    def __init__(self, session):
        self.session = session
        self.headers = {
            "X-API-KEY": BIRDEYE_API_KEY,
            "accept": "application/json",
            "User-Agent": "KHAMCXHunter"
        }

    async def get_new_tokens(self, limit=50):
        """جلب توكنات جديدة مرتبة حسب وقت الإنشاء"""
        params = {
            "sort_by": "creation_time",
            "sort_type": "desc",
            "limit": limit,
            "offset": 0
        }
        url = f"{BIRDEYE_BASE}/defi/v3/token/list?{urlencode(params)}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    log.warning(f"Birdeye list error: {resp.status}")
                    return []
                data = await resp.json()
                items = data.get("data", []) if isinstance(data, dict) else []
                return [{"address": normalize_address(i), "raw": i} for i in items if normalize_address(i)]
        except Exception as e:
            log.warning(f"Birdeye exception: {e}")
            return []

    async def get_token_overview(self, address):
        """جلب بيانات التوكن (MCap, Liquidity, Volume...)"""
        url = f"{BIRDEYE_BASE}/defi/token_overview?address={address}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data")
        except:
            pass
        return None


# ==================== DEXSCREENER ====================
class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def get_new_tokens(self):
        urls = [
            "https://api.dexscreener.com/token-profiles/latest/v1",
            "https://api.dexscreener.com/token-boosts/latest/v1",
            "https://api.dexscreener.com/token-boosts/top/v1",
        ]
        results = []
        for url in urls:
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in (data if isinstance(data, list) else []):
                            addr = normalize_address(item)
                            if addr:
                                results.append({"address": addr, "source": url.split("/")[-2], "raw": item})
            except:
                pass
        return results

    async def get_pairs(self, addresses):
        result = {}
        for i in range(0, len(addresses), 30):
            batch = addresses[i:i+30]
            url = f"https://api.dexscreener.com/tokens/v1/solana/{','.join(batch)}"
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for p in (data if isinstance(data, list) else []):
                            base = (p.get("baseToken") or {}).get("address")
                            if base:
                                result.setdefault(base, []).append(p)
            except:
                continue
        return result


# ==================== MAIN LOGIC ====================
async def run():
    seen_path = Path(SEEN_TOKENS_FILE)
    seen = set()
    if seen_path.exists():
        try:
            seen = set(json.load(seen_path.open()))
        except:
            pass

    logger = SignalLogger(SIGNALS_LOG_FILE)

    async with aiohttp.ClientSession() as session:
        birdeye = BirdeyeClient(session)
        dex = DexscreenerClient(session)

        start = time.time()
        while time.time() - start < RUN_DURATION_SECONDS:
            # جلب توكنات جديدة من Birdeye + Dexscreener
            birdeye_tokens = await birdeye.get_new_tokens(40)
            dex_tokens = await dex.get_new_tokens()

            all_tokens = {t["address"]: t for t in birdeye_tokens + dex_tokens if t["address"] not in seen}

            if not all_tokens:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            log.info(f"New tokens found: {len(all_tokens)}")

            pairs = await dex.get_pairs(list(all_tokens.keys()))

            for addr, token_data in all_tokens.items():
                seen.add(addr)

                pair_list = pairs.get(addr, [])
                if not pair_list:
                    continue

                # أخذ أفضل pair
                best_pair = max(pair_list, key=lambda p: safe_float((p.get("liquidity") or {}).get("usd")))

                # جلب بيانات إضافية من Birdeye إن أمكن
                overview = await birdeye.get_token_overview(addr)

                mcap = safe_float(best_pair.get("marketCap") or best_pair.get("fdv"))
                liq = safe_float((best_pair.get("liquidity") or {}).get("usd"))
                vol5m = safe_float((best_pair.get("volume") or {}).get("m5"))

                # فلاتر بسيطة
                if mcap < 15000 or mcap > 300000:
                    continue
                if liq < 6000:
                    continue
                if vol5m < 2000:
                    continue

                score = 70
                if overview:
                    if overview.get("buyVolume", 0) > overview.get("sellVolume", 0):
                        score += 10

                if score >= ACCEPT_SCORE:
                    logger.log("ACCEPT", addr, f"Score: {score}")
                    msg = f"🎯 *KHAMCX ACCEPT*\n`{addr}`\nScore: {score}\nMCap: ${int(mcap):,}\n[Birdeye](https://birdeye.so/token/{addr})"
                    await send_telegram(session, msg)
                else:
                    logger.log("REJECT", addr, f"Score {score}")

            await asyncio.sleep(POLL_INTERVAL_SEC)

    # حفظ الـ seen tokens
    seen_path.write_text(json.dumps(list(seen)))


class SignalLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.accepted = 0

    def log(self, decision, token, reason):
        if decision == "ACCEPT":
            self.accepted += 1
            log.info(f"✅ ACCEPT {token[:8]}... {reason}")
        entry = {"time": now_iso(), "decision": decision, "token": token, "reason": reason}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


async def send_telegram(session, text):
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        await session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "Markdown"}
        )
    except:
        pass


if __name__ == "__main__":
    asyncio.run(run())
