"""
SQLite persistence for Phase 1.

Tables:
- discovered: every token that came from WebSocket NEW_LISTING
- watches: tokens that passed REST validation and got a WATCH alert
- watches.telegram_message_id is saved so Phase 2 can reply to the WATCH with ENTRY
"""

import sqlite3
import json
import time
from pathlib import Path
from contextlib import contextmanager


SCHEMA = """
CREATE TABLE IF NOT EXISTS discovered (
    token_address TEXT PRIMARY KEY,
    first_seen_ts REAL NOT NULL,
    name TEXT,
    symbol TEXT,
    initial_liquidity REAL,
    raw_event_json TEXT
);

CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL UNIQUE,
    watch_ts REAL NOT NULL,
    score INTEGER,
    metrics_json TEXT,
    telegram_message_id INTEGER,
    expired BOOLEAN DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_watches_ts ON watches(watch_ts);

CREATE TABLE IF NOT EXISTS rejects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    reject_ts REAL NOT NULL,
    reason TEXT,
    score INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rejects_token ON rejects(token_address);
"""


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self._init()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- Discovered ----
    def add_discovered(self, token: str, name: str, symbol: str,
                       liquidity: float, raw: dict) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO discovered "
                    "(token_address, first_seen_ts, name, symbol, initial_liquidity, raw_event_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (token, time.time(), name, symbol, liquidity, json.dumps(raw)),
                )
                return conn.total_changes > 0
        except sqlite3.Error:
            return False

    def is_discovered(self, token: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM discovered WHERE token_address = ?", (token,)
            ).fetchone()
        return row is not None

    # ---- Watches ----
    def has_watch(self, token: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM watches WHERE token_address = ?", (token,)
            ).fetchone()
        return row is not None

    def add_watch(self, token: str, score: int, metrics: dict,
                  telegram_message_id: int = None) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO watches (token_address, watch_ts, score, metrics_json, telegram_message_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (token, time.time(), score, json.dumps(metrics), telegram_message_id),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def active_watches(self, ttl_minutes: int):
        cutoff = time.time() - (ttl_minutes * 60)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT token_address, watch_ts, score, metrics_json, telegram_message_id "
                "FROM watches WHERE expired = 0 AND watch_ts >= ?",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def expire_old_watches(self, ttl_minutes: int) -> int:
        cutoff = time.time() - (ttl_minutes * 60)
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE watches SET expired = 1 WHERE expired = 0 AND watch_ts < ?",
                (cutoff,),
            )
            return cur.rowcount

    def count_active_watches(self) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM watches WHERE expired = 0"
            ).fetchone()
        return row[0]

    # ---- Rejects ----
    def add_reject(self, token: str, reason: str, score: int = 0):
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO rejects (token_address, reject_ts, reason, score) VALUES (?, ?, ?, ?)",
                    (token, time.time(), reason, score),
                )
        except sqlite3.Error:
            pass

    # ---- Stats ----
    def stats(self) -> dict:
        with self._conn() as conn:
            discovered = conn.execute("SELECT COUNT(*) FROM discovered").fetchone()[0]
            watches = conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM watches WHERE expired = 0").fetchone()[0]
            rejects = conn.execute("SELECT COUNT(*) FROM rejects").fetchone()[0]
        return {
            "discovered": discovered,
            "total_watches": watches,
            "active_watches": active,
            "rejects": rejects,
        }
