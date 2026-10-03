"""Offline smoke checks (no Telegram credentials required)."""

from __future__ import annotations

import asyncio
import sys

import aiohttp

import config
from dex_client import DexClient
from signal_engine import extract_metrics, apply_filters
from telegram_notifier import _format_watch


async def run() -> int:
    headers = {
        "User-Agent": "MomentumBot/2.0-smoke",
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        dex = DexClient(session)
        listings = await dex.fetch_new_listings()
        print(f"OK discovery candidates={len(listings)}")
        if not listings:
            # Still prove API reachability with an unfiltered page
            raw = await dex._get_json(
                f"{config.GECKO_API_BASE}/networks/{config.CHAIN}/new_pools",
                params={"page": 1},
            )
            if not raw or not raw.get("data"):
                print("FAIL gecko new_pools empty")
                return 1
            listings = [dex._parse_gecko_pool(raw["data"][0])]
            listings = [x for x in listings if x]
            print(f"OK unfiltered sample={len(listings)}")

        sample = listings[0]
        overview = await dex.token_overview(sample.token, sample.pool_address)
        if not overview:
            print("FAIL overview empty")
            return 1
        metrics = extract_metrics(overview)
        if metrics["liquidity_usd"] <= 0:
            # Migration follow should usually fill this; still accept overview if other fields exist
            print(f"WARN liquidity=0 source={overview.get('source')} (may be pre-migration)")
        passed, reason, score = apply_filters(metrics)
        text = _format_watch(sample.token, metrics, score or 1)
        assert "<b>WATCH SIGNAL</b>" in text
        assert sample.token in text
        print(
            f"OK overview source={overview.get('source')} "
            f"liq=${metrics['liquidity_usd']:.0f} filter={passed}/{reason}"
        )
        print("OK telegram HTML formatter")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        raise SystemExit(130)
