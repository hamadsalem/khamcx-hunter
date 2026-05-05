"""
KHAMCX Hunter - Configuration
==============================
يطبق فلاتر "Hunter KHAMCX 40%" من ملف PDF الخطة.
نظام تقييم 100 نقطة - لا قبول تحت 85.
"""

# ===== Dexscreener Endpoints =====
DEXSCREENER_BASE = "https://api.dexscreener.com"
ENDPOINT_LATEST_PROFILES = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
ENDPOINT_LATEST_BOOSTS = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
ENDPOINT_TOKEN_PAIRS = f"{DEXSCREENER_BASE}/tokens/v1/solana"

# ===== Batch Mode Settings =====
RUN_DURATION_SECONDS = 80
POLL_INTERVAL_SEC = 5
MAX_TOKENS_PER_RUN = 30

# ===== KHAMCX Hunter Filters (from PDF) =====
# الإعداد المعتمد حسب الـ PDF
HUNTER_FILTERS = {
    # العمر: 30 دقيقة → 8 ساعات (من PDF)
    "min_age_seconds": 1800,        # 30 دقيقة
    "max_age_seconds": 28800,       # 8 ساعات

    # Market Cap: $25K → $220K (من PDF)
    "min_mcap_usd": 25_000,
    "max_mcap_usd": 220_000,

    # Liquidity: $8K → $80K (من PDF)
    "min_liquidity_usd": 8_000,
    "max_liquidity_usd": 80_000,

    # Volume 5M: > $2K (من PDF)
    "min_volume_5m_usd": 2_000,

    # Volume 1H: > $20K (من PDF)
    "min_volume_1h_usd": 20_000,

    # Transactions: 25+ (5M) / 120+ (1H) (من PDF)
    "min_buys_5m": 25,
    "min_txns_1h": 120,

    # Buys vs Sells: 1.2x → 3x (من PDF)
    "min_buy_sell_ratio": 1.2,
    "max_buy_sell_ratio": 3.0,

    # Price Change 5M: -12% to +20% (من PDF)
    "min_price_change_5m": -12.0,
    "max_price_change_5m": 20.0,
}

# ===== Kill Switches (من PDF صفحة 8) =====
KILL_SWITCHES = {
    # شمعة خضراء عمودية طويلة
    "max_price_change_5m_kill": 50.0,

    # صعود أكثر من 250% قبل الدخول
    "max_price_change_24h": 250.0,

    # سيولة ضعيفة رغم الصعود
    "min_liquidity_kill": 6_000,

    # مساحة الصعود 40% - لو السعر مرتفع جداً
    # نتحقق من المسافة من ATH
    "max_distance_from_ath_pct": 60.0,  # السعر لازم يكون قريب من 40% الحالية
}

# ===== Scoring System (من PDF صفحة 6) =====
# 100 نقطة موزعة على 6 معايير
SCORING_WEIGHTS = {
    "chart_strength": 25,       # قوة الشارت والارتداد (يدوي - نقدر 20 افتراضي)
    "volume_liquidity": 20,     # الفوليوم والسيولة
    "buyers_strength": 15,      # قوة المشترين والمعاملات
    "token_safety": 15,         # سلامة العملة والمحافظ (تقديري)
    "growth_room": 15,          # وجود مساحة صعود 40%
    "trade_timing": 10,         # مناسبة وقت التداول
}

# الحد الأدنى للقبول (من PDF)
MIN_SCORE_TO_ACCEPT = 85

# ===== Time Windows (من PDF صفحة 4 - بتوقيت الرياض) =====
# نحول لـ UTC لأن GitHub Actions يستخدم UTC
# KSA = UTC+3
# Golden hours: 3 AM - 3 PM KSA = 12 AM - 12 PM UTC
# Avoid hours: 9 PM - 1:45 AM KSA = 6 PM - 10:45 PM UTC
GOLDEN_HOURS_UTC = list(range(0, 12))   # 12 AM - 12 PM UTC = 3 AM - 3 PM KSA
AVOID_HOURS_UTC = list(range(18, 23))   # 6 PM - 11 PM UTC = 9 PM - 2 AM KSA

# ===== Storage =====
SIGNALS_LOG_FILE = "khamcx_signals.jsonl"
SEEN_TOKENS_FILE = "khamcx_seen.json"
SEEN_TOKENS_TTL_SEC = 14400             # 4 ساعات

# ===== Logging =====
VERBOSE_REJECTS = False
