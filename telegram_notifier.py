"""
Telegram notifier - Phase 1.

Uses HTML parse mode (safer than Markdown for Solana addresses).
Sends WATCH alerts and captures message_id for future ENTRY replies.
"""

from __future__ import annotations

import html
import logging
from typing import Optional

import aiohttp

import config

log = logging.getLogger("telegram")


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


async def verify_telegram(session: aiohttp.ClientSession) -> bool:
    """
    Confirm token works via getMe, and that chat_id is reachable via getChat.
    Returns True if Telegram looks ready.
    """
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("Telegram not configured (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)")
        return False

    me = await _api(session, "getMe")
    if not me or not me.get("ok"):
        log.error("Telegram getMe failed: %s", me)
        return False
    bot = me.get("result") or {}
    log.info("Telegram bot OK: @%s (%s)", bot.get("username"), bot.get("id"))

    chat = await _api(session, "getChat", {"chat_id": config.TELEGRAM_CHAT_ID})
    if not chat or not chat.get("ok"):
        log.error(
            "Telegram getChat failed for chat_id=%s: %s",
            config.TELEGRAM_CHAT_ID,
            chat,
        )
        return False
    info = chat.get("result") or {}
    log.info(
        "Telegram chat OK: type=%s title=%s",
        info.get("type"),
        info.get("title") or info.get("username") or info.get("id"),
    )
    return True


async def send_startup_ping(session: aiohttp.ClientSession) -> Optional[int]:
    text = (
        "✅ <b>Momentum Bot online</b>\n"
        "Source: free DEX (GeckoTerminal + DexScreener)\n"
        f"<i>{_esc(config.PAPER_MODE_TEXT)}</i>"
    )
    return await _send(session, text)


async def send_watch(session: aiohttp.ClientSession,
                     token: str, m: dict, score: int) -> Optional[int]:
    text = _format_watch(token, m, score)
    return await _send(session, text)


async def send_text(session: aiohttp.ClientSession, text: str,
                    reply_to_message_id: Optional[int] = None) -> Optional[int]:
    return await _send(session, text, reply_to_message_id)


async def _api(session: aiohttp.ClientSession, method: str,
               payload: Optional[dict] = None) -> Optional[dict]:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{method}"
    try:
        async with session.post(
            url,
            json=payload or {},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                log.warning("Telegram %s HTTP %s: %s", method, resp.status, str(data)[:200])
            return data
    except Exception as e:
        log.error("Telegram %s error: %s", method, e)
        return None


async def _send(session: aiohttp.ClientSession, text: str,
                reply_to_message_id: Optional[int] = None) -> Optional[int]:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured")
        return None

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id

    data = await _api(session, "sendMessage", payload)
    if data and data.get("ok"):
        return data.get("result", {}).get("message_id")
    if data:
        log.warning("Telegram sendMessage failed: %s", str(data)[:240])
    return None


def _format_watch(token: str, m: dict, score: int) -> str:
    symbol = _esc(m.get("symbol") or "")
    name = _esc(m.get("name") or "")
    title = f"{symbol}" + (f" ({name})" if name and name != symbol else "")
    dex_url = m.get("url") or f"https://dexscreener.com/solana/{token}"

    return (
        "🟡 <b>WATCH SIGNAL</b>\n"
        "Phase 1 — free DEX snapshot validated\n\n"
        f"<b>{title}</b>\n"
        f"<code>{_esc(token)}</code>\n\n"
        f"<b>Score:</b> {int(score)}\n"
        f"<b>MCap:</b> ${int(m['mcap_usd']):,}\n"
        f"<b>Liquidity:</b> ${int(m['liquidity_usd']):,}\n"
        f"<b>Vol 5m:</b> ${int(m['volume_5m_usd']):,}\n"
        f"<b>Vol Accel:</b> {m['volume_acceleration']}x\n"
        f"<b>B/S:</b> {m['buy_sell_ratio']} ({m['buys_5m']}/{m['sells_5m']})\n"
        f"<b>Txns 5m:</b> {m['txns_5m']}\n"
        f"<b>Avg Trade:</b> ${m['avg_trade_usd']}\n"
        f"<b>Price 5m:</b> {m['price_change_5m']}%\n"
        f"<b>Price 30m:</b> {m['price_change_30m']}%\n"
        f"<b>Price 1h:</b> {m['price_change_1h']}%\n\n"
        f'<a href="{_esc(dex_url)}">DexScreener</a> · '
        f'<a href="https://gmgn.ai/sol/token/{_esc(token)}">GMGN</a> · '
        f'<a href="https://photon-sol.tinyastro.io/en/lp/{_esc(token)}">Photon</a>\n\n'
        f"<i>{_esc(config.PAPER_MODE_TEXT)}</i>"
    )
