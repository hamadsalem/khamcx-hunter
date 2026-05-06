"""
KHAMCX Hunter Bot - Birdeye Fixed Version
=========================================
تم إصلاح مشكلة Birdeye 404
الآن يستخدم Endpoint صحيح: /defi/v3/token/list مع ترتيب حسب وقت الإنشاء
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
BIRDEYE_API_KEY = "6a62af5ac9e54e94bb9deb343645480e"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

RUN_DURATION_SECONDS = 90
POLL_INTERVAL_SEC = 18
MAX_TOKENS_PER_RUN = 180

ACCEPT_SCORE_POST = 78
ACCEPT_SCORE_EARLY = 80

DEXSCREENER_BASE = "https://api.dexscreener.com"
ENDPOINT_TOKEN_PAIRS = f"{DEXSCREENER_BASE}/tokens/v1/solana"

SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400
VERBOSE_REJECTS = True


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
        return float(value) if value is not None else default
    except:
        return default

def as_int(value, default=0):
    try:
        return int(float(value or 0))
    except:
        return default

def normalize_token_address(item):
    if not isinstance(item, dict):
        return None
    for key in ["tokenAddress", "token_address", "address", "mint", "ca"]:
        val = item.get(key)
        if isinstance(val, str) and len(val) >= 32:
            return val
    base = item.get("baseToken") or {}
    if isinstance(base, dict):
        val = base.get("address")
        if isinstance(val, str) and len(val) >= 32:
            return val
    return None

def source_record(address, source, raw=None):
    return {"tokenAddress": address, "source": source, "raw": raw or {}}


# ============================================================
# TELEGRAM + STORAGE + METRICS (مختصرة للتوفير)
# ============================================================

async def send_telegram(session, message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            return resp.status == 200
    except:
        return False


class SeenTokens:
    def __init__(self, path, ttl_sec):
        self.path = Path(path)
        self.ttl_sec = ttl_sec
        self._seen = {}
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                now = time.time()
                self._seen = {k: t for k, t in data.items() if now - t < self.ttl_sec}
            except:
                pass

    def save(self):
        try:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self._seen, f)
        except:
            pass

    def is_known(self, addr):
        return addr in self._seen

    def mark_seen(self, addr):
        self._seen[addr] = time.time()


class SignalLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.accepted = self.watched = self.rejected = self.killed = 0

    def log(self, decision, token, reason, metrics=None, score=None):
        entry = {"ts": now_iso(), "decision": decision, "token": token, "reason": reason, "score": score}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if decision == "ACCEPT":
            self.accepted += 1
            log.info(f"✅ ACCEPT {token[:8]}... Score={score}")
        elif decision == "KILL":
            self.killed += 1
        else:
            self.rejected += 1
            if VERBOSE_REJECTS:
                log.info(f"❌ REJECT {token[:8]}... {reason}")


def extract_metrics(pair):
    m = {}
    created = pair.get("pairCreatedAt")
    m["age_seconds"] = round((time.time() * 1000 - safe_number(created)) / 1000, 1) if created else None
    m["mcap_usd"] = safe_number(pair.get("marketCap") or pair.get("fdv"))
    liq = pair.get("liquidity", {}) or {}
    m["liquidity_usd"] = safe_number(liq.get("usd") if isinstance(liq, dict) else pair.get("liquidity"))
    vol = pair.get("volume", {}) or {}
    m["volume_5m_usd"] = safe_number(vol.get("m5") if isinstance(vol, dict) else pair.get("volume_5m"))
    m["volume_1h_usd"] = safe_number(vol.get("h1") if isinstance(vol, dict) else pair.get("volume_1h"))
    txns = pair.get("txns", {}) or {}
    m5 = txns.get("m5", {}) if isinstance(txns, dict) else {}
    m["buys_5m"] = as_int(m5.get("buys"))
    m["sells_5m"] = as_int(m5.get("sells"))
    m["buy_sell_ratio"] = round(m["buys_5m"] / max(m["sells_5m"], 1), 2)
    pc = pair.get("priceChange", {}) or {}
    m["price_change_5m"] = safe_number(pc.get("m5") if isinstance(pc, dict) else pair.get("price_change_percent5m"))
    m["dex_id"] = str(pair.get("dexId", "")).lower()
    return m


def determine_mode(metrics):
    age = metrics.get("age_seconds")
    if age and age < 1800:
        return "EARLY"
    return "POST"


def check_kill_switches(metrics):
    if metrics["price_change_5m"] > 90:
        return True, "Big green candle"
    if metrics["liquidity_usd"] < 5000:
        return True, "Low liquidity"
    return False, None


def apply_filters(metrics):
    age = metrics.get("age_seconds")
    if age is None or age < 900 or age > 43200:
        return False, "Age out of range"
    if metrics["mcap_usd"] < 15000 or metrics["mcap_usd"] > 300000:
        return False, "MCap out of range"
    if metrics["liquidity_usd"] < 6000:
        return False, "Low liquidity"
    if metrics["volume_5m_usd"] < 1500:
        return False, "Low volume"
    if metrics["buy_sell_ratio"] < 1.1:
        return False, "Low buy pressure"
    return True, "Passed"


def calculate_score(metrics):
    score = 50
    if 1.2 <= metrics["buy_sell_ratio"] <= 2.5:
        score += 15
    if metrics["volume_5m_usd"] >= 8000:
        score += 10
    if 15000 <= metrics["mcap_usd"] <= 120000:
        score += 10
    if -10 <= metrics["price_change_5m"] <= 15:
        score += 10
    return min(score, 100)


# ============================================================
# BIRDEYE CLIENT - FIXED
# ============================================================

class BirdeyeClient:
    BASE = "https://public-api.birdeye.so"

    def __init__(self, session, api_key):
        self.session = session
        self.headers = {
            "X-API-KEY": api_key,
            "accept": "application/json",
            "User-Agent": "KHAMCXHunter/6.0"
        }

    async def get_new_tokens(self, limit=50):
        """
        Fixed endpoint: استخدام /defi/v3/token/list مع ترتيب حسب وقت الإنشاء
        """
        params = {
            "sort_by": "creation_time",
            "sort_type": "desc",
            "limit": limit,
            "offset": 0
        }
        url = f"{self.BASE}/defi/v3/token/list?{urlencode(params)}"
        try:
            async with self.session.get(url, headers=self.headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status != 200:
                    log.warning(f"Birdeye token/list error: {resp.status}")
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
            log.warning(f"Birdeye error: {e}")
            return []

    async def get_sources(self):
        tokens = await self.get_new_tokens(50)
        log.info(f"birdeye_new: {len(tokens)} tokens")
        return [tokens]


class DexscreenerClient:
    def __init__(self, session):
        self.session = session

    async def get_sources(self):
        endpoints = [
            (DEXSCREENER_BASE + "/token-profiles/latest/v1", "dex_profiles"),
            (DEXSCREENER_BASE + "/token-boosts/latest/v1", "dex_latest_boosts"),
            (DEXSCREENER_BASE + "/token-boosts/top/v1", "dex_top_boosts"),
        ]
        results = []
        for url, label in endpoints:
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data if isinstance(data, list) else []
                        out = [source_record(normalize_token_address(i), label, i) for i in items if normalize_token_address(i)]
                        results.append(out)
            except:
                pass
        return results

    async def get_pairs_batch(self, addrs):
        result = {}
        for i in range(0, len(addrs), 25):
            batch = addrs[i:i+25]
            url = ENDPOINT_TOKEN_PAIRS + "/" + ",".join(batch)
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


# ============================================================
# MAIN
# ============================================================

async def collect_candidates(dex_client, birdeye_client):
    dex_res, birdeye_res = await asyncio.gather(
        dex_client.get_sources(),
        birdeye_client.get_sources()
    )
    merged = {}
    for group in dex_res + birdeye_res:
        for item in group:
            addr = item["tokenAddress"]
            if addr not in merged:
                merged[addr] = {"tokenAddress": addr, "sources": set()}
            merged[addr]["sources"].add(item["source"])
    return list(merged.values())


async def batch_run():
    start = time.time()
    deadline = start + RUN_DURATION_SECONDS
    logger = SignalLogger(SIGNALS_LOG_FILE)
    seen = SeenTokens(SEEN_TOKENS_FILE, SEEN_TOKENS_TTL_SEC)

    log.info("="*55)
    log.info("KHAMCX Hunter - Birdeye Fixed Version")
    log.info(f"Birdeye Endpoint: /defi/v3/token/list (sorted by creation_time)")
    log.info("="*55)

    async with aiohttp.ClientSession() as session:
        dex = DexscreenerClient(session)
        birdeye = BirdeyeClient(session, BIRDEYE_API_KEY)

        while time.time() < deadline:
            candidates = await collect_candidates(dex, birdeye)
            new_candidates = [c for c in candidates if not seen.is_known(c["tokenAddress"])]

            if new_candidates:
                log.info(f"New candidates: {len(new_candidates)}")
            else:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            pairs = await dex.get_pairs_batch([c["tokenAddress"] for c in new_candidates[:80]])

            for c in new_candidates:
                if time.time() > deadline:
                    break
                addr = c["tokenAddress"]
                seen.mark_seen(addr)

                pair_list = pairs.get(addr, [])
                if not pair_list:
                    logger.log("REJECT", addr, "No pair data")
                    continue

                best = max(pair_list, key=lambda p: safe_number((p.get("liquidity") or {}).get("usd")))
                m = extract_metrics(best)
                m["sources"] = list(c["sources"])

                killed, reason = check_kill_switches(m)
                if killed:
                    logger.log("KILL", addr, reason)
                    continue

                passed, reason = apply_filters(m)
                if not passed:
                    logger.log("REJECT", addr, reason)
                    continue

                score = calculate_score(m)
                if score >= ACCEPT_SCORE_POST:
                    logger.log("ACCEPT", addr, "Passed filters", score=score)
                    msg = f"🎯 *ACCEPT* `{addr}`\nScore: {score}\nMCap: ${int(m['mcap_usd']):,}\n[Birdeye](https://birdeye.so/token/{addr})"
                    await send_telegram(session, msg)
                else:
                    logger.log("REJECT", addr, f"Score too low: {score}")

            await asyncio.sleep(POLL_INTERVAL_SEC)

    seen.save()
    log.info(f"Done. Accepted: {logger.accepted} | Rejected: {logger.rejected} | Killed: {logger.killed}")


if __name__ == "__main__":
    asyncio.run(batch_run())
