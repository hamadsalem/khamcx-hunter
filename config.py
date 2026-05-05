"""
KHAMCX Hunter - Configuration V6 (Free - Max Discovery)
========================================================
تعديلات V6 (مجاني 100%):
- أضفنا مصدر Pump.fun مجاني
- زدنا GMGN calls إلى 6 (توازن بين الكمية والاستقرار)
- Interval = 12 ثانية (توازن جيد)
- Accept score = 76 (أسهل شوية)
"""

# ===== Dexscreener Endpoints =====
DEXSCREENER_BASE = "https://api.dexscreener.com"
ENDPOINT_LATEST_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
ENDPOINT_LATEST_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
ENDPOINT_TOP_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
ENDPOINT_LATEST_COMMUNITY_TAKEOVERS = f"{DEXSCREENER_BASE}/community-takeovers/latest/v1"
ENDPOINT_LATEST_ADS = f"{DEXSCREENER_BASE}/ads/latest/v1"
ENDPOINT_TOKEN_PAIRS = f"{DEXSCREENER_BASE}/tokens/v1/solana"

# ===== Pump.fun (مجاني) =====
PUMPFUN_API_BASE = "https://frontend-api.pump.fun"

# ===== Batch Mode Settings V6 =====
RUN_DURATION_SECONDS = 75
POLL_INTERVAL_SEC = 12          # توازن بين الكمية والاستقرار
MAX_TOKENS_PER_RUN = 200

# ===== KHAMCX Hunter Filters V6 =====
HUNTER_FILTERS = {
    "min_age_seconds": 900,         # 15 دقيقة
    "max_age_seconds": 43200,       # 12 ساعة
    "min_mcap_usd": 12000,
    "max_mcap_usd": 350000,
    "min_liquidity_usd": 5000,
    "max_liquidity_usd": 120000,
    "min_volume_5m_usd": 1200,
    "min_volume_1h_usd": 12000,
    "min_buys_5m": 15,
    "min_txns_1h": 80,
    "min_buy_sell_ratio": 1.05,
    "max_buy_sell_ratio": 3.8,
    "min_price_change_5m": -25.0,
    "max_price_change_5m": 40.0,
}

# ===== Kill Switches V6 =====
KILL_SWITCHES = {
    "max_price_change_5m_kill": 90.0,
    "max_price_change_24h": 450.0,
    "min_liquidity_kill": 4000,
    "max_distance_from_ath_pct": 75.0,
}

# ===== Scoring =====
MIN_SCORE_TO_ACCEPT = 76
MIN_SCORE_TO_ACCEPT_EARLY = 78

# ===== Time Windows =====
GOLDEN_HOURS_UTC = list(range(0, 12))
AVOID_HOURS_UTC = list(range(18, 23))

# ===== Storage =====
SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400

# ===== Logging =====
VERBOSE_REJECTS = True
