"""
Configuration - Phase 1 MVP (free DEX sources).

Phase 1 covers:
- GeckoTerminal new_pools polling (discovery)
- DexScreener / GeckoTerminal snapshots (validation)
- Scoring + WATCH alert
- SQLite persistence
- Telegram notifications

No Birdeye / paid API keys required for market data.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ==================== Credentials ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ==================== Free DEX Endpoints ====================
GECKO_API_BASE = "https://api.geckoterminal.com/api/v2"
DEXSCREENER_API_BASE = "https://api.dexscreener.com"
CHAIN = "solana"

# ==================== Discovery Filter ====================
# Applied when polling GeckoTerminal new_pools
NEW_LISTING_FILTER = {
    "min_liquidity": 8000,
    # Focus on memecoin launch venues (GeckoTerminal dex ids)
    "meme_dex_only": True,
    "allowed_dexes": [
        "pump-fun",
        "pumpswap",
        "raydium",
        "raydium-launchlab",
        "meteora",
        "meteora-dbc",
        "meteora-dyn",
        "orca",
    ],
}

# Polling
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "30"))
# If set (e.g. GitHub Actions), stop after N seconds
RUN_DURATION_SECONDS = int(os.getenv("RUN_DURATION_SECONDS", "0") or "0")
# How long to wait after discovery before validation snapshot
VALIDATION_DELAY_SEC = int(os.getenv("VALIDATION_DELAY_SEC", "90"))

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

# ==================== Momentum Filters ====================
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
MAX_WATCH_TOKENS = 50
WATCH_TTL_MINUTES = 20

# ==================== Telegram ====================
PAPER_MODE_TEXT = "Phase 1: WATCH only - manual review required."
TELEGRAM_STARTUP_PING = os.getenv("TELEGRAM_STARTUP_PING", "true").lower() in (
    "1",
    "true",
    "yes",
)

# ==================== Validation ====================
def validate_config():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise RuntimeError(
            "Missing env vars: " + ", ".join(missing) +
            ". Copy .env.example to .env and fill in values."
        )
