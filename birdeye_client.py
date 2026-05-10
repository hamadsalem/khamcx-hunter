"""
Birdeye client - Phase 1.

Two responsibilities:
1. BirdeyeWebSocket: persistent WS connection that receives TOKEN_NEW_LISTING_DATA events.
2. BirdeyeREST: token_overview snapshot for validation.

Implements the WebSocket protocol exactly per docs:
- URL: wss://public-api.birdeye.so/socket/<chain>?x-api-key=<KEY>
- Subprotocol: echo-protocol
- Subscription message: {"type": "SUBSCRIBE_TOKEN_NEW_LISTING", ...filters}
- Output: {"type": "TOKEN_NEW_LISTING_DATA", "data": {...}}
"""

import asyncio
import json
import logging
from typing import Callable, Optional, Awaitable

import aiohttp
import websockets

import config

log = logging.getLogger("birdeye")


# ==================== REST ====================
class BirdeyeREST:
    def __init__(self, session: aiohttp.ClientSession, api_key: str):
        self.session = session
        self.api_key = api_key

    def _headers(self) -> dict:
        return {
            "X-API-KEY": self.api_key,
            "x-chain": config.CHAIN,
            "accept": "application/json",
        }

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        url = config.BIRDEYE_REST_BASE + path
        try:
            async with self.session.get(
                url,
                headers=self._headers(),
                params=params or {},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 429:
                    log.warning("Rate limited on %s", path)
                    await asyncio.sleep(2)
                    return None
                if resp.status != 200:
                    log.warning("HTTP %s on %s", resp.status, path)
                    return None
                return await resp.json()
        except asyncio.TimeoutError:
            log.warning("Timeout on %s", path)
            return None
        except Exception as e:
            log.error("REST error on %s: %s", path, e)
            return None

    async def token_overview(self, token_address: str) -> Optional[dict]:
        data = await self._get(
            config.ENDPOINT_TOKEN_OVERVIEW,
            {"address": token_address},
        )
        if data and data.get("success"):
            return data.get("data")
        return None


# ==================== WebSocket ====================
EventHandler = Callable[[str, dict], Awaitable[None]]


class BirdeyeWebSocket:
    """
    Persistent WebSocket with auto-reconnect.

    Usage:
        ws = BirdeyeWebSocket(api_key, handler)
        ws.queue_subscription({"type": "SUBSCRIBE_TOKEN_NEW_LISTING", ...})
        await ws.run_forever()
    """

    def __init__(self, api_key: str, handler: EventHandler):
        self.api_key = api_key
        self.handler = handler
        self._stop = False
        self._subscriptions: list = []
        self._ws = None

    def queue_subscription(self, sub: dict):
        self._subscriptions.append(sub)

    def _build_url(self) -> str:
        return f"{config.BIRDEYE_WS_URL}?x-api-key={self.api_key}"

    async def _send_subscriptions(self):
        if self._ws is None:
            return
        for sub in self._subscriptions:
            await self._ws.send(json.dumps(sub))
            log.info("SUBSCRIBED type=%s", sub.get("type"))
            await asyncio.sleep(0.1)

    async def _connect_once(self):
        url = self._build_url()
        # Per Birdeye docs: subprotocol must be echo-protocol
        async with websockets.connect(
            url,
            subprotocols=["echo-protocol"],
            ping_interval=20,
            ping_timeout=20,
            max_size=2**22,
        ) as ws:
            self._ws = ws
            log.info("CONNECTED to %s", config.BIRDEYE_WS_URL)
            await self._send_subscriptions()

            async for raw in ws:
                if self._stop:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Non-JSON message ignored")
                    continue

                event_type = msg.get("type", "UNKNOWN")
                payload = msg.get("data", {})
                try:
                    await self.handler(event_type, payload)
                except Exception as e:
                    log.error("Handler error on %s: %s", event_type, e)

    async def run_forever(self):
        backoff = 2
        while not self._stop:
            try:
                await self._connect_once()
                # Clean exit means connection closed gracefully
                backoff = 2
            except websockets.exceptions.ConnectionClosed as e:
                log.warning("WS closed: %s", e)
            except Exception as e:
                log.error("WS error: %s", e)

            if self._stop:
                break

            log.info("Reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def stop(self):
        self._stop = True


# ==================== Subscription builders ====================
def build_new_listing_subscription(filters: dict) -> dict:
    """
    Build SUBSCRIBE_TOKEN_NEW_LISTING message.
    Per docs, the filters are top-level keys, not nested in 'data'.
    """
    msg = {"type": "SUBSCRIBE_TOKEN_NEW_LISTING"}
    if filters.get("meme_platform_enabled") is not None:
        msg["meme_platform_enabled"] = bool(filters["meme_platform_enabled"])
    if filters.get("min_liquidity") is not None:
        msg["min_liquidity"] = int(filters["min_liquidity"])
    if filters.get("max_liquidity") is not None:
        msg["max_liquidity"] = int(filters["max_liquidity"])
    if filters.get("sources"):
        msg["sources"] = list(filters["sources"])
    return msg
