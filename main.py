"""
Solana Momentum Bot - Phase 1 MVP (free DEX).

Pipeline:
  1. Poll GeckoTerminal new_pools (Solana) every POLL_INTERVAL_SEC
  2. On each new listing:
     a. Save to discovered table
     b. Wait VALIDATION_DELAY_SEC
     c. Fetch DexScreener (fallback GeckoTerminal) snapshot
     d. Apply filters + score
     e. If WATCH passes, send Telegram and save to watches table
  3. Background task expires watches older than WATCH_TTL_MINUTES
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

import config
from database import Database
from dex_client import DexClient, NewListing
from signal_engine import extract_metrics, apply_filters
from telegram_notifier import verify_telegram, send_startup_ping, send_watch


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


class Pipeline:
    def __init__(self, db: Database, dex: DexClient, session: aiohttp.ClientSession):
        self.db = db
        self.dex = dex
        self.session = session
        # token -> pool_address for validation fallback
        self._pools: dict[str, str] = {}

    async def handle_listing(self, listing: NewListing):
        token = listing.token
        if not token:
            return
        if self.db.is_discovered(token):
            return

        self._pools[token] = listing.pool_address
        self.db.add_discovered(
            token, listing.name, listing.symbol, listing.liquidity, listing.raw
        )
        log.info(
            "DISCOVERED %s (%s) liq=$%.0f dex=%s",
            listing.symbol or token[:8],
            (listing.name or "")[:24],
            listing.liquidity,
            listing.dex_id,
        )
        asyncio.create_task(self._validate_after_delay(token))

    async def _validate_after_delay(self, token: str):
        await asyncio.sleep(config.VALIDATION_DELAY_SEC)

        if self.db.has_watch(token):
            return

        if self.db.count_active_watches() >= config.MAX_WATCH_TOKENS:
            self.db.add_reject(token, "Watch list full")
            return

        overview = await self.dex.token_overview(token, self._pools.get(token))
        if not overview:
            self.db.add_reject(token, "No overview from free DEX")
            log.info("NO_OVERVIEW %s", token[:10])
            return

        metrics = extract_metrics(overview)
        # Keep display fields from overview
        metrics["name"] = overview.get("name") or metrics.get("name") or ""
        metrics["symbol"] = overview.get("symbol") or metrics.get("symbol") or ""
        metrics["url"] = overview.get("url")

        log.info(
            "VALIDATED %s liq=$%.0f vol5m=$%.0f txns5m=%d accel=%.2f src=%s",
            token[:10],
            metrics["liquidity_usd"],
            metrics["volume_5m_usd"],
            metrics["txns_5m"],
            metrics["volume_acceleration"],
            overview.get("source"),
        )

        passed, reason, score = apply_filters(metrics)
        if not passed:
            self.db.add_reject(token, reason, score)
            log.info("REJECT %s: %s", token[:10], reason)
            return

        msg_id = await send_watch(self.session, token, metrics, score)
        if msg_id is None:
            self.db.add_reject(token, "Telegram send failed", score)
            log.error("WATCH Telegram failed for %s", token[:10])
            return

        if self.db.add_watch(token, score, metrics, msg_id):
            log.info("WATCH SENT %s score=%d msg_id=%s", token[:10], score, msg_id)
        else:
            log.warning("Could not record watch for %s", token[:10])


async def discovery_loop(pipeline: Pipeline, stop_at: Optional[float]):
    while True:
        if stop_at and time.time() >= stop_at:
            log.info("RUN_DURATION reached — stopping discovery loop")
            return
        try:
            listings = await pipeline.dex.fetch_new_listings()
            log.info("Poll: %d new-pool candidates after filters", len(listings))
            for listing in listings:
                await pipeline.handle_listing(listing)
        except Exception as e:
            log.error("Discovery poll error: %s", e)
        await asyncio.sleep(config.POLL_INTERVAL_SEC)


async def expire_loop(db: Database, stop_at: Optional[float]):
    while True:
        if stop_at and time.time() >= stop_at:
            return
        await asyncio.sleep(60)
        try:
            n = db.expire_old_watches(config.WATCH_TTL_MINUTES)
            if n > 0:
                log.info("Expired %d watches", n)
        except Exception as e:
            log.error("Expire loop error: %s", e)


async def stats_loop(db: Database, stop_at: Optional[float]):
    while True:
        if stop_at and time.time() >= stop_at:
            return
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
    stop_at = (
        time.time() + config.RUN_DURATION_SECONDS
        if config.RUN_DURATION_SECONDS > 0
        else None
    )

    log.info("=" * 60)
    log.info("Solana Momentum Bot - Phase 1 (free DEX)")
    log.info(
        "Discovery=GeckoTerminal new_pools | Validation=DexScreener"
    )
    log.info(
        "Filter: min_liq=$%d meme_dex_only=%s poll=%ss delay=%ss",
        config.NEW_LISTING_FILTER["min_liquidity"],
        config.NEW_LISTING_FILTER.get("meme_dex_only", False),
        config.POLL_INTERVAL_SEC,
        config.VALIDATION_DELAY_SEC,
    )
    if stop_at:
        log.info("Timed run: %ss", config.RUN_DURATION_SECONDS)
    log.info("=" * 60)

    db = Database(config.DB_PATH)

    headers = {
        "User-Agent": "MomentumBot/2.0 (+https://github.com/momentum-bot)",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        ok = await verify_telegram(session)
        if not ok:
            raise RuntimeError("Telegram verification failed — check TELEGRAM_TOKEN / TELEGRAM_CHAT_ID")

        if config.TELEGRAM_STARTUP_PING:
            ping_id = await send_startup_ping(session)
            if ping_id:
                log.info("Startup ping sent (msg_id=%s)", ping_id)
            else:
                log.warning("Startup ping failed (bot will continue)")

        dex = DexClient(session)
        pipeline = Pipeline(db, dex, session)

        tasks = [
            asyncio.create_task(discovery_loop(pipeline, stop_at)),
            asyncio.create_task(expire_loop(db, stop_at)),
            asyncio.create_task(stats_loop(db, stop_at)),
        ]

        if stop_at:
            # Wait until duration ends, then cancel background loops
            await asyncio.sleep(max(0, stop_at - time.time()))
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log.info("Timed run complete")
        else:
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
