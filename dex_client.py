"""
Free DEX data client (no Birdeye / no paid API key).

Discovery:  GeckoTerminal  GET /api/v2/networks/solana/new_pools
Validation: DexScreener    GET /latest/dex/tokens/{address}
Fallback:   GeckoTerminal  GET /api/v2/networks/solana/pools/{pool}
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiohttp

import config

log = logging.getLogger("dex")

WSOL = "So11111111111111111111111111111111111111112"


@dataclass
class NewListing:
    token: str
    name: str
    symbol: str
    liquidity: float
    pool_address: str
    dex_id: str
    created_at: Optional[float]
    raw: dict


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _parse_iso_ts(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        # 2026-10-03T16:10:01Z
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.timestamp()
    except (TypeError, ValueError):
        return None


class DexClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def _get_json(self, url: str, *, params: Optional[dict] = None) -> Optional[Any]:
        try:
            async with self.session.get(
                url,
                params=params or {},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    log.warning("Rate limited: %s", url)
                    await asyncio.sleep(2)
                    return None
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("HTTP %s on %s: %s", resp.status, url, body[:160])
                    return None
                return await resp.json()
        except asyncio.TimeoutError:
            log.warning("Timeout on %s", url)
            return None
        except Exception as e:
            log.error("Request error on %s: %s", url, e)
            return None

    async def fetch_new_listings(self) -> list[NewListing]:
        """Poll GeckoTerminal new pools for Solana."""
        url = f"{config.GECKO_API_BASE}/networks/{config.CHAIN}/new_pools"
        data = await self._get_json(url, params={"page": 1})
        if not data or not isinstance(data, dict):
            return []

        out: list[NewListing] = []
        for item in data.get("data") or []:
            listing = self._parse_gecko_pool(item)
            if listing is None:
                continue
            if listing.liquidity < config.NEW_LISTING_FILTER["min_liquidity"]:
                continue
            if config.NEW_LISTING_FILTER.get("meme_dex_only"):
                allowed = set(config.NEW_LISTING_FILTER.get("allowed_dexes") or [])
                if allowed and listing.dex_id not in allowed:
                    continue
            out.append(listing)
        return out

    def _parse_gecko_pool(self, item: dict) -> Optional[NewListing]:
        try:
            attrs = item.get("attributes") or {}
            rel = item.get("relationships") or {}
            base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
            quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id") or ""
            dex_id = ((rel.get("dex") or {}).get("data") or {}).get("id") or ""

            base = base_id.split("_", 1)[-1] if base_id else ""
            quote = quote_id.split("_", 1)[-1] if quote_id else ""
            if not base:
                return None

            # Prefer the non-SOL side as the memecoin mint
            if base == WSOL and quote:
                token = quote
            else:
                token = base

            name_full = attrs.get("name") or ""
            # "TICKER / SOL" → symbol guess
            symbol = name_full.split("/")[0].strip() if "/" in name_full else name_full
            liquidity = _f(attrs.get("reserve_in_usd"))
            pool_address = attrs.get("address") or ""
            created_at = _parse_iso_ts(attrs.get("pool_created_at"))

            return NewListing(
                token=token,
                name=name_full,
                symbol=symbol,
                liquidity=liquidity,
                pool_address=pool_address,
                dex_id=dex_id,
                created_at=created_at,
                raw=item,
            )
        except Exception as e:
            log.debug("Skip malformed pool: %s", e)
            return None

    async def token_overview(
        self,
        token_address: str,
        pool_address: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Return a normalized overview dict for signal_engine.extract_metrics.

        Prefer DexScreener for 5m volume/txns, but merge GeckoTerminal when
        DexScreener omits liquidity (common on early pump.fun pairs).
        """
        ds = await self._overview_from_dexscreener(token_address)
        gt = None
        if pool_address:
            gt = await self._overview_from_gecko_pool(pool_address)

        if ds and gt:
            return self._merge_overviews(ds, gt)
        return ds or gt

    @staticmethod
    def _merge_overviews(primary: dict, fallback: dict) -> dict:
        merged = dict(fallback)
        merged.update({k: v for k, v in primary.items() if v not in (None, "", 0, 0.0)})
        # Always keep a positive liquidity if either source has it
        liq = max(_f(primary.get("liquidity")), _f(fallback.get("liquidity")))
        if liq > 0:
            merged["liquidity"] = liq
        mcap = max(_f(primary.get("mc")), _f(fallback.get("mc")))
        if mcap > 0:
            merged["mc"] = mcap
        merged["source"] = f"{primary.get('source')}+{fallback.get('source')}"
        # Prefer DexScreener URL when present
        if primary.get("url"):
            merged["url"] = primary["url"]
        return merged

    async def _overview_from_dexscreener(self, token_address: str) -> Optional[dict]:
        url = f"{config.DEXSCREENER_API_BASE}/latest/dex/tokens/{token_address}"
        data = await self._get_json(url)
        if not data or not isinstance(data, dict):
            return None
        pairs = data.get("pairs") or []
        # Keep Solana pairs only, pick highest liquidity
        sol_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == config.CHAIN]
        if not sol_pairs:
            return None
        sol_pairs.sort(
            key=lambda p: _f((p.get("liquidity") or {}).get("usd")),
            reverse=True,
        )
        # Prefer a pair that actually reports liquidity when available
        with_liq = [p for p in sol_pairs if _f((p.get("liquidity") or {}).get("usd")) > 0]
        p = with_liq[0] if with_liq else sol_pairs[0]

        liq = p.get("liquidity") or {}
        vol = p.get("volume") or {}
        txns = p.get("txns") or {}
        m5 = txns.get("m5") or {}
        h1 = txns.get("h1") or {}
        pc = p.get("priceChange") or {}
        base = p.get("baseToken") or {}

        buys5 = int(_f(m5.get("buys")))
        sells5 = int(_f(m5.get("sells")))
        buys1h = int(_f(h1.get("buys")))
        sells1h = int(_f(h1.get("sells")))

        return {
            "price": _f(p.get("priceUsd")),
            "liquidity": _f(liq.get("usd")),
            "mc": _f(p.get("marketCap") or p.get("fdv")),
            "fdv": _f(p.get("fdv")),
            "v5m": _f(vol.get("m5")),
            "v30m": _f(vol.get("h1")) / 2.0 if vol.get("h1") else 0.0,
            "v1h": _f(vol.get("h1")),
            "v24h": _f(vol.get("h24")),
            "trade5m": buys5 + sells5,
            "trade1h": buys1h + sells1h,
            "buy5m": buys5,
            "sell5m": sells5,
            "priceChange5mPercent": _f(pc.get("m5")),
            "priceChange30mPercent": _f(pc.get("h1")) / 2.0 if pc.get("h1") is not None else 0.0,
            "priceChange1hPercent": _f(pc.get("h1")),
            "priceChange24hPercent": _f(pc.get("h24")),
            "createTime": p.get("pairCreatedAt"),
            "name": base.get("name") or "",
            "symbol": base.get("symbol") or "",
            "pairAddress": p.get("pairAddress"),
            "dexId": p.get("dexId"),
            "url": p.get("url"),
            "source": "dexscreener",
        }

    async def _overview_from_gecko_pool(self, pool_address: str) -> Optional[dict]:
        url = f"{config.GECKO_API_BASE}/networks/{config.CHAIN}/pools/{pool_address}"
        data = await self._get_json(url)
        if not data or not isinstance(data, dict):
            return None
        item = data.get("data") or {}
        attrs = item.get("attributes") or {}
        if not attrs:
            return None

        # Bonding-curve pools often migrate; follow destination when reserves are gone
        launchpad = attrs.get("launchpad_details") or {}
        migrated = launchpad.get("migrated_destination_pool_address")
        if migrated and _f(attrs.get("reserve_in_usd")) <= 0 and migrated != pool_address:
            log.info("Following migrated pool %s -> %s", pool_address[:10], migrated[:10])
            return await self._overview_from_gecko_pool(migrated)

        vol = attrs.get("volume_usd") or {}
        tx = attrs.get("transactions") or {}
        m5 = tx.get("m5") or {}
        h1 = tx.get("h1") or {}
        pc = attrs.get("price_change_percentage") or {}
        created = _parse_iso_ts(attrs.get("pool_created_at"))

        buys5 = int(_f(m5.get("buys")))
        sells5 = int(_f(m5.get("sells")))
        buys1h = int(_f(h1.get("buys")))
        sells1h = int(_f(h1.get("sells")))
        name_full = attrs.get("name") or ""
        symbol = name_full.split("/")[0].strip() if "/" in name_full else name_full

        return {
            "price": _f(attrs.get("base_token_price_usd")),
            "liquidity": _f(attrs.get("reserve_in_usd")),
            "mc": _f(attrs.get("market_cap_usd") or attrs.get("fdv_usd")),
            "fdv": _f(attrs.get("fdv_usd")),
            "v5m": _f(vol.get("m5")),
            "v30m": _f(vol.get("m30")),
            "v1h": _f(vol.get("h1")),
            "v24h": _f(vol.get("h24")),
            "trade5m": buys5 + sells5,
            "trade1h": buys1h + sells1h,
            "buy5m": buys5,
            "sell5m": sells5,
            "priceChange5mPercent": _f(pc.get("m5")),
            "priceChange30mPercent": _f(pc.get("m30")),
            "priceChange1hPercent": _f(pc.get("h1")),
            "priceChange24hPercent": _f(pc.get("h24")),
            "createTime": int(created * 1000) if created else None,
            "name": name_full,
            "symbol": symbol,
            "pairAddress": attrs.get("address"),
            "dexId": ((item.get("relationships") or {}).get("dex") or {}).get("data", {}).get("id"),
            "url": f"https://dexscreener.com/solana/{attrs.get('address')}",
            "source": "geckoterminal",
        }
