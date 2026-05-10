"""
Telegram notifier - Phase 1.

Sends WATCH alerts and captures the returned message_id so Phase 2 can
reply to the WATCH with an ENTRY message later (using reply_to_message_id).
"""

import logging
from typing import Optional

import aiohttp

import config

log = logging.getLogger("telegram")


async def send_watch(session: aiohttp.ClientSession,
                     token: str, m: dict, score: int) -> Optional[int]:
    """
    Send WATCH message. Returns message_id on success, None on failure.
    """
    text = _format_watch(token, m, score)
    return await _send(session, text)


async def send_text(session: aiohttp.ClientSession, text: str,
                    reply_to_message_id: Optional[int] = None) -> Optional[int]:
    return await _send(session, text, reply_to_message_id)


async def _send(session: aiohttp.ClientSession, text: str,
                reply_to_message_id: Optional[int] = None) -> Optional[int]:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured")
        return None

    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("Telegram %s: %s", resp.status, body[:200])
                return None
            data = await resp.json()
            if data.get("ok"):
                return data.get("result", {}).get("message_id")
            return None
    except Exception as e:
        log.error("Telegram error: %s", e)
        return None


def _format_watch(token: str, m: dict, score: int) -> str:
    return (
        "🟡 *WATCH SIGNAL*\n"
        "_Phase 1 - REST snapshot validated_\n\n"
        f"`{token}`\n\n"
        f"*Score:* {score}\n"
        f"*MCap:* ${int(m['mcap_usd']):,}\n"
        f"*Liquidity:* ${int(m['liquidity_usd']):,}\n"
        f"*Vol 5m:* ${int(m['volume_5m_usd']):,}\n"
        f"*Vol Accel:* {m['volume_acceleration']}x\n"
        f"*B/S:* {m['buy_sell_ratio']} ({m['buys_5m']}/{m['sells_5m']})\n"
        f"*Txns 5m:* {m['txns_5m']}\n"
        f"*Avg Trade:* ${m['avg_trade_usd']}\n"
        f"*Price 5m:* {m['price_change_5m']}%\n"
        f"*Price 30m:* {m['price_change_30m']}%\n"
        f"*Price 1h:* {m['price_change_1h']}%\n\n"
        f"[Birdeye](https://birdeye.so/token/{token}?chain=solana) · "
        f"[GMGN](https://gmgn.ai/sol/token/{token}) · "
        f"[Photon](https://photon-sol.tinyastro.io/en/lp/{token})\n\n"
        f"_{config.PAPER_MODE_TEXT}_"
    )
