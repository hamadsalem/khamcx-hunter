# Solana Momentum Bot - Phase 1 MVP (Free DEX)

Real-time Solana memecoin discovery using **free** public DEX APIs + Telegram alerts.

No Birdeye / paid market-data key required.

## Data sources

| Step | API | Cost |
|------|-----|------|
| Discovery | [GeckoTerminal](https://www.geckoterminal.com/) `GET /api/v2/networks/solana/new_pools` | Free |
| Validation | [DexScreener](https://dexscreener.com/) `GET /latest/dex/tokens/{mint}` | Free |
| Fallback | GeckoTerminal pool snapshot | Free |
| Alerts | Telegram Bot API | Free |

## What this version does (Phase 1)

1. **Polls** Solana new pools every `POLL_INTERVAL_SEC` (default 30s)
2. Pre-filters by `min_liquidity` and memecoin DEX list (pump / raydium / meteora / orca)
3. On each new listing:
   - Saves the token to the `discovered` table
   - Waits `VALIDATION_DELAY_SEC` (default 90s)
   - Fetches a DexScreener snapshot (GeckoTerminal fallback)
   - Applies safety + momentum filters and computes a score
   - If it passes, sends a `WATCH` alert to Telegram and stores `message_id`

## What it does NOT do yet (Phase 2)

- ❌ No rolling TX window / second confirmation
- ❌ No `ENTRY` alerts (only WATCH)
- ❌ No outcome tracking

## Files

```
momentum_bot/
├── main.py              # orchestrator (poll loop)
├── config.py            # settings + filters
├── dex_client.py        # GeckoTerminal + DexScreener
├── signal_engine.py     # extract_metrics + filters + score
├── database.py          # SQLite (discovered, watches, rejects)
├── telegram_notifier.py # HTML WATCH messages + startup verify
├── requirements.txt
├── .env.example
└── .gitignore
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env
# Fill TELEGRAM_TOKEN and TELEGRAM_CHAT_ID
chmod 600 .env

python main.py
```

On startup the bot:
1. Calls Telegram `getMe` + `getChat` to verify credentials
2. Sends an optional startup ping (`TELEGRAM_STARTUP_PING=true`)
3. Starts polling free DEX APIs

## GitHub Actions secrets

Only these are required now:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

`BIRDEYE_API_KEY` is no longer used — remove it if present.

## Inspect the database

```bash
sqlite3 momentum_bot.db

SELECT datetime(first_seen_ts, 'unixepoch'), symbol, name, initial_liquidity
FROM discovered ORDER BY first_seen_ts DESC LIMIT 20;

SELECT datetime(watch_ts, 'unixepoch'), token_address, score
FROM watches ORDER BY watch_ts DESC LIMIT 20;

SELECT reason, COUNT(*) FROM rejects GROUP BY reason ORDER BY 2 DESC;
```

## Important

This bot sends **alerts only**. It does NOT execute trades.
Verify every signal manually. Memecoins can rug at any time.
