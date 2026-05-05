"""
KHAMCX Hunter - Configuration Stable V5
========================================
نسخة مستقرة ومُجربة:
- بدون Pump.fun (كان يعطي 0)
- GMGN calls = 4 فقط (الأكثر استقراراً)
- Accept score = 78
- Interval = 12 ثانية
"""

# ===== Dexscreener Endpoints =====
DEXSCREENER_BASE = "https://api.dexscreener.com"
ENDPOINT_LATEST_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
ENDPOINT_LATEST_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
ENDPOINT_TOP_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
ENDPOINT_LATEST_COMMUNITY_TAKEOVERS = f"{DEXSCREENER_BASE}/community-takeovers/latest/v1"
ENDPOINT_LATEST_ADS = f"{DEXSCREENER_BASE}/ads/latest/v1"
ENDPOINT_TOKEN_PAIRS = f"{DEXSCREENER_BASE}/tokens/v1/solana"

# ===== Batch Mode Settings =====
RUN_DURATION_SECONDS = 75
POLL_INTERVAL_SEC = 12
MAX_TOKENS_PER_RUN = 150

# ===== KHAMCX Hunter Filters (مخففة بشكل معتدل) =====
HUNTER_FILTERS = {
    "min_age_seconds": 1200,        # 20 دقيقة
    "max_age_seconds": 43200,       # 12 ساعة
    "min_mcap_usd": 15000,
    "max_mcap_usd": 300000,
    "min_liquidity_usd": 6000,
    "max_liquidity_usd": 100000,
    "min_volume_5m_usd": 1500,
    "min_volume_1h_usd": 15000,
    "min_buys_5m": 18,
    "min_txns_1h": 90,
    "min_buy_sell_ratio": 1.1,
    "max_buy_sell_ratio": 3.5,
    "min_price_change_5m": -20.0,
    "max_price_change_5m": 35.0,
}

# ===== Kill Switches =====
KILL_SWITCHES = {
    "max_price_change_5m_kill": 80.0,
    "max_price_change_24h": 400.0,
    "min_liquidity_kill": 5000,
    "max_distance_from_ath_pct": 70.0,
}

# ===== Scoring =====
MIN_SCORE_TO_ACCEPT = 78
MIN_SCORE_TO_ACCEPT_EARLY = 80

# ===== Time Windows =====
GOLDEN_HOURS_UTC = list(range(0, 12))
AVOID_HOURS_UTC = list(range(18, 23))

# ===== Storage =====
SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400

# ===== Logging =====
VERBOSE_REJECTS = True
