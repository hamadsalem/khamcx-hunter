# Solana Momentum Bot - Phase 1 MVP

Real-time Solana memecoin discovery using Birdeye Premium WebSocket + Telegram alerts.

## What this version does (Phase 1)

1. **WebSocket subscribes** to `SUBSCRIBE_TOKEN_NEW_LISTING` with these filters at the source:
   - `min_liquidity = 8000`
   - `meme_platform_enabled = true`
2. **On each new listing event**, the bot:
   - Saves the token to the `discovered` table
   - Waits 90 seconds (lets the token develop some 5m/30m history)
   - Fetches `/defi/token_overview` for full metrics
   - Applies safety + momentum filters and computes a score
   - If passes, sends a `WATCH` alert to Telegram and saves the message_id

## What it does NOT do yet (Phase 2)

- ❌ No `SUBSCRIBE_TXS` rolling window
- ❌ No second confirmation step
- ❌ No `ENTRY` alerts (only WATCH)
- ❌ No outcome tracking

This is intentional. We prove the foundation works first.

## Files

```
momentum_bot/
├── main.py              # orchestrator
├── config.py            # all settings + filters
├── birdeye_client.py    # WebSocket + REST
├── signal_engine.py     # extract_metrics + filters + score
├── database.py          # SQLite (discovered, watches, rejects)
├── telegram_notifier.py # WATCH messages with message_id capture
├── requirements.txt
├── .env.example
├── .gitignore
└── systemd/
    └── momentum-bot.service
```

## Deployment on Ubuntu 22.04 VPS

```bash
# 1. Become root, create a non-root user
adduser botuser
usermod -aG sudo botuser

# 2. Install Python
apt update && apt install -y python3 python3-venv python3-pip unzip nano

# 3. Switch to botuser, drop in the project files
su - botuser
cd ~
# (upload momentum_bot folder via scp)

# 4. Set up venv
cd momentum_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Configure secrets
cp .env.example .env
nano .env
# Fill BIRDEYE_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
chmod 600 .env

# 6. Test run
python main.py
# Watch for: "WebSocket connected" and "Subscribed: SUBSCRIBE_TOKEN_NEW_LISTING"
# Ctrl+C to stop

# 7. Install as systemd service (as root)
exit
sudo cp /home/botuser/momentum_bot/systemd/momentum-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable momentum-bot
sudo systemctl start momentum-bot
sudo systemctl status momentum-bot

# 8. Tail logs
tail -f /home/botuser/momentum_bot/momentum_bot.log
```

## What to verify in Phase 1

Before moving to Phase 2, check that all of these work for at least 24 hours:

- [ ] WebSocket stays connected (look for periodic activity in log)
- [ ] `DISCOVERED` events appear (Birdeye is sending data)
- [ ] At least some tokens make it past validation to `WATCH`
- [ ] WATCH messages arrive on Telegram
- [ ] `momentum_bot.db` grows over time
- [ ] Bot recovers automatically after `systemctl restart momentum-bot`

## Inspect the database

```bash
cd ~/momentum_bot
sqlite3 momentum_bot.db

-- Recent discoveries
SELECT datetime(first_seen_ts, 'unixepoch'), symbol, name, initial_liquidity
FROM discovered ORDER BY first_seen_ts DESC LIMIT 20;

-- Recent watches
SELECT datetime(watch_ts, 'unixepoch'), token_address, score
FROM watches ORDER BY watch_ts DESC LIMIT 20;

-- Top reject reasons
SELECT reason, COUNT(*) FROM rejects GROUP BY reason ORDER BY 2 DESC;
```

## Known limitations

- The 90-second `VALIDATION_DELAY_SEC` is a guess. Too short = no 5m data. Too long = miss the move. Tune after observing real behavior.
- `token_overview`'s 5m fields may still be empty for very new tokens. Watch the logs for repeated rejects with "Vol 5m too low".
- WebSocket only emits new listings - if a token gets liquidity later or pumps later, we miss it. Phase 2's TXS subscription on the watch list will partially address this.

## Important

This bot sends **alerts only**. It does NOT execute trades.
Verify every signal manually. Memecoins can rug at any time.
