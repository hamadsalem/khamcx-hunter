"""
KHAMCX Hunter - Configuration V5 (Fixed for rate limits + more signals)
========================================================================
تعديلات V5:
- خفض عتبة القبول إلى 78 لزيادة عدد الإشارات
- تخفيف معتدل للفلاتر والـ kill switches
- تقليل ضغط GMGN (POLL_INTERVAL أطول)
- تفعيل VERBOSE_REJECTS لتشخيص أفضل
"""

# ===== Dexscreener Endpoints =====
DEXSCREENER_BASE = "https://api.dexscreener.com"
ENDPOINT_LATEST_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
ENDPOINT_LATEST_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
ENDPOINT_TOP_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/top/v1"
ENDPOINT_LATEST_COMMUNITY_TAKEOVERS = f"{DEXSCREENER_BASE}/community-takeovers/latest/v1"
ENDPOINT_LATEST_ADS = f"{DEXSCREENER_BASE}/ads/latest/v1"
ENDPOINT_TOKEN_PAIRS = f"{DEXSCREENER_BASE}/tokens/v1/solana"

# ===== Batch Mode Settings (V5 Fixed) =====
RUN_DURATION_SECONDS = 75
POLL_INTERVAL_SEC = 15          # زيادة من 8 إلى 15 لتقليل الضغط على GMGN
MAX_TOKENS_PER_RUN = 150

# ===== KHAMCX Hunter Filters V5 (مخففة معتدل) =====
HUNTER_FILTERS = {
    # العمر: 20 دقيقة → 12 ساعة
    "min_age_seconds": 1200,        # 20 دقيقة (مخفف)
    "max_age_seconds": 43200,       # 12 ساعة

    # Market Cap: $15K → $300K
    "min_mcap_usd": 15000,
    "max_mcap_usd": 300000,

    # Liquidity: $6K → $100K
    "min_liquidity_usd": 6000,
    "max_liquidity_usd": 100000,

    # Volume 5M: > $1.5K
    "min_volume_5m_usd": 1500,

    # Volume 1H: > $15K
    "min_volume_1h_usd": 15000,

    # Transactions: 18+ (5M) / 90+ (1H)
    "min_buys_5m": 18,
    "min_txns_1h": 90,

    # Buys vs Sells: 1.1x → 3.5x
    "min_buy_sell_ratio": 1.1,
    "max_buy_sell_ratio": 3.5,

    # Price Change 5M: -20% to +35%
    "min_price_change_5m": -20.0,
    "max_price_change_5m": 35.0,
}

# ===== Kill Switches V5 (مخففة) =====
KILL_SWITCHES = {
    "max_price_change_5m_kill": 80.0,      # بدل 50 - يسمح بـ pumps أقوى
    "max_price_change_24h": 400.0,         # بدل 250
    "min_liquidity_kill": 5000,
    "max_distance_from_ath_pct": 70.0,
}

# ===== Scoring System =====
SCORING_WEIGHTS = {
    "chart_strength": 25,
    "volume_liquidity": 20,
    "buyers_strength": 15,
    "token_safety": 15,
    "growth_room": 15,
    "trade_timing": 10,
}

# الحد الأدنى للقبول V5 (مخفض لزيادة الإشارات)
MIN_SCORE_TO_ACCEPT = 78
MIN_SCORE_TO_ACCEPT_EARLY = 80

# ===== Time Windows (UTC) =====
GOLDEN_HOURS_UTC = list(range(0, 12))
AVOID_HOURS_UTC = list(range(18, 23))

# ===== Storage =====
SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400

# ===== Logging =====
VERBOSE_REJECTS = True   # مفعل لتشخيص أسباب الرفض
