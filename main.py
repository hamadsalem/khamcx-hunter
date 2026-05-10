"""
Solana Momentum Bot - Phase 1 MVP.

Pipeline:
  1. WebSocket subscribes to TOKEN_NEW_LISTING with min_liquidity=8000 filter
  2. On each TOKEN_NEW_LISTING_DATA event:
     a. Save to discovered table
     b. Wait 60-90s for token to develop some history
     c. Call REST token_overview to get full snapshot
     d. Apply filters + score
     e. If WATCH passes, send Telegram and save to watches table
  3. Background task expires watches older than WATCH_TTL_MINUTES

Phase 2 will add:
  - SUBSCRIBE_TXS for tokens in active_watches
  - Rolling 5m metrics from TXS stream
  - ENTRY confirmation as Telegram reply_to_message_id
"""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

import config
from birdeye_client import (
    BirdeyeREST,
    BirdeyeWebSocket,
    build_new_listing_subscription,
)
from database import Database
from signal_engine import extract_metrics, apply_filters
from telegram_notifier import send_watch


# ==================== Logging ====================
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE),
    ],
)
log = logging.getLogger("main")


# ==================== Config tunables ====================
# How long to wait after first listing before fetching token_overview.
# Too short = token has no 5m/30m history yet. Too long = miss the move.
VALIDATION_DELAY_SEC = 90


class Pipeline:
    def __init__(self, db: Database, rest: BirdeyeREST, session: aiohttp.ClientSession):
        self.db = db
        self.rest = rest
        self.session = session

    async def handle_new_listing(self, event_type: str, payload: dict):
        """Called by BirdeyeWebSocket on every event."""
        if event_type != "TOKEN_NEW_LISTING_DATA":
            log.debug("Ignored event type: %s", event_type)
            return

        token = payload.get("address")
        if not token:
            log.warning("New listing event missing address")
            return

        if self.db.is_discovered(token):
            return  # Already processed

        name = payload.get("name", "")
        symbol = payload.get("symbol", "")
        try:
            liquidity = float(payload.get("liquidity") or 0)
        except (TypeError, ValueError):
            liquidity = 0.0

        self.db.add_discovered(token, name, symbol, liquidity, payload)
        log.info("DISCOVERED %s (%s) liq=$%.0f", symbol or token[:8], name[:20], liquidity)

        # Don't block the WebSocket reader - schedule validation as a separate task
        asyncio.create_task(self._validate_after_delay(token))

    async def _validate_after_delay(self, token: str):
        """Wait, then fetch token_overview and decide WATCH/REJECT."""
        await asyncio.sleep(VALIDATION_DELAY_SEC)

        if self.db.has_watch(token):
            return  # Already watched (race condition guard)

        # Capacity check - don't accumulate too many watches
        if self.db.count_active_watches() >= config.MAX_WATCH_TOKENS:
            self.db.add_reject(token, "Watch list full")
            return

        overview = await self.rest.token_overview(token)
        if not overview:
            self.db.add_reject(token, "No overview from REST")
            log.info("NO_OVERVIEW %s (REST returned empty)", token[:10])
            return

        metrics = extract_metrics(overview)
        log.info("VALIDATED %s liq=$%.0f vol5m=$%.0f txns5m=%d accel=%.2f",
                 token[:10], metrics["liquidity_usd"], metrics["volume_5m_usd"],
                 metrics["txns_5m"], metrics["volume_acceleration"])

        passed, reason, score = apply_filters(metrics)

        if not passed:
            self.db.add_reject(token, reason, score)
            log.info("REJECT %s: %s", token[:10], reason)
            return

        # Send Telegram first to capture message_id
        msg_id = await send_watch(self.session, token, metrics, score)
        if self.db.add_watch(token, score, metrics, msg_id):
            log.info("WATCH SENT %s score=%d msg_id=%s", token[:10], score, msg_id)
        else:
            log.warning("Could not record watch for %s", token[:10])


async def expire_loop(db: Database):
    """Periodically expire old watches."""
    while True:
        await asyncio.sleep(60)
        try:
            n = db.expire_old_watches(config.WATCH_TTL_MINUTES)
            if n > 0:
                log.info("Expired %d watches", n)
        except Exception as e:
            log.error("Expire loop error: %s", e)


async def stats_loop(db: Database):
    """Log stats every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        try:
            s = db.stats()
            log.info(
                "STATS discovered=%d watches=%d active=%d rejects=%d",
                s["discovered"], s["total_watches"], s["active_watches"], s["rejects"],
            )
        except Exception as e:
            log.error("Stats loop error: %s", e)


async def main():
    config.validate_config()
    log.info("=" * 60)
    log.info("Solana Momentum Bot - Phase 1 MVP")
    log.info("Filter: min_liq=$%d meme=%s",
             config.NEW_LISTING_FILTER["min_liquidity"],
             config.NEW_LISTING_FILTER.get("meme_platform_enabled", False))
    log.info("=" * 60)

    db = Database(config.DB_PATH)

    async with aiohttp.ClientSession(headers={"User-Agent": "MomentumBot/2.0"}) as session:
        rest = BirdeyeREST(session, config.BIRDEYE_API_KEY)
        pipeline = Pipeline(db, rest, session)

        ws = BirdeyeWebSocket(config.BIRDEYE_API_KEY, pipeline.handle_new_listing)
        ws.queue_subscription(build_new_listing_subscription(config.NEW_LISTING_FILTER))

        # Run WebSocket + background loops concurrently
        await asyncio.gather(
            ws.run_forever(),
            expire_loop(db),
            stats_loop(db),
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
