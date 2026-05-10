"""
Configuration - Phase 1 MVP.

Phase 1 covers:
- WebSocket SUBSCRIBE_TOKEN_NEW_LISTING (discovery)
- REST token_overview (validation)
- Scoring + WATCH alert
- SQLite persistence
- Telegram notifications

Phase 2 (later) will add:
- SUBSCRIBE_TXS for rolling 5m metrics
- Confirmation logic for ENTRY
- Entry alert as reply to WATCH
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ==================== Credentials ====================
BIRDEYE_API_KEY = os.getenv("BIRDEYE_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==================== Birdeye Endpoints ====================
BIRDEYE_REST_BASE = "https://public-api.birdeye.so"
BIRDEYE_WS_URL = "wss://public-api.birdeye.so/socket/solana"
CHAIN = "solana"

ENDPOINT_TOKEN_OVERVIEW = "/defi/token_overview"

# ==================== WebSocket Subscription Filter ====================
# Pre-filter applied at the source - reduces noise before it reaches us
NEW_LISTING_FILTER = {
    "meme_platform_enabled": True,
    "min_liquidity": 8000,  # idea #7 - hardcoded floor at the subscription level
    # Optional: limit to specific sources to cut noise further
    # "sources": ["pump_dot_fun", "raydium", "meteora_dynamic_bonding_curve"],
}

# ==================== Storage ====================
DB_PATH = "momentum_bot.db"
LOG_FILE = "momentum_bot.log"

# ==================== Safety Filters ====================
SAFETY_FILTERS = {
    "min_liquidity_usd": 8000,
    "max_mcap_usd": 1_000_000,
    "min_mcap_usd": 5000,
    "max_volume_to_mcap_ratio": 20,
    "min_age_seconds": 60,
    "max_age_seconds": 6 * 60 * 60,
}

# ==================== Momentum Filters (Phase 1 - based on REST snapshot) ====================
# Note: in Phase 1 we work with token_overview's m5/h1 data - which is delayed.
# Phase 2 will replace this with our own rolling window from TXS stream.
MOMENTUM_FILTERS = {
    "min_volume_5m_usd": 2500,
    "min_txns_5m": 25,
    "min_buy_sell_ratio": 1.10,
    "min_avg_trade_usd": 30,
    "min_price_change_5m": 3,
    "max_price_change_5m": 50,
    "min_volume_acceleration": 0.7,
    "max_volume_acceleration": 5.0,
    "min_score": 60,
}

# ==================== Watch List Limits ====================
# Per the plan: don't track too many at once
MAX_WATCH_TOKENS = 50
WATCH_TTL_MINUTES = 20

# ==================== Telegram ====================
PAPER_MODE_TEXT = "Phase 1: WATCH only - manual review required."

# ==================== Validation ====================
def validate_config():
    missing = []
    if not BIRDEYE_API_KEY:
        missing.append("BIRDEYE_API_KEY")
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError(
            "Missing env vars: " + ", ".join(missing) +
            ". Copy .env.example to .env and fill in values."
        )
