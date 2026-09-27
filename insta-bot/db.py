"""Message/reply history in data/bot.db. The bot writes, the panel reads."""

import sqlite3
import time

from config import DATA

DB = DATA / "bot.db"


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        sender TEXT NOT NULL,
        text TEXT NOT NULL,
        decision TEXT NOT NULL,     -- reply / cooldown / ignored:* / paused
        reply TEXT,                 -- what was (or would have been) sent
        dry_run INTEGER NOT NULL DEFAULT 0)""")
    return con


def log_event(sender: str, text: str, decision: str, reply: str | None = None,
              dry_run: bool = False) -> None:
    with connect() as con:
        con.execute("INSERT INTO events (ts, sender, text, decision, reply, dry_run) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), sender, text, decision, reply, int(dry_run)))


def recent(limit: int = 100) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def recent_replies(limit: int = 20) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM events WHERE reply IS NOT NULL "
                           "ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
