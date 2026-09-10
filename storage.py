"""
SQLite storage layer.

Tables:
- users          everyone who has ever messaged the bot (+ blocked flag,
                  last away-notice timestamp for throttling)
- relay          maps an admin-side message id back to the original user,
                  so admin replies / button taps route to the right person
- messages       full message log (for /history), each row tagged with a
                  source: user / admin / ai / canned / system
- canned_replies quick-reply presets shown as buttons under relayed messages
- settings       simple key/value store (away_mode, away_message, ai_mode)
"""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bot.db"


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                blocked INTEGER DEFAULT 0,
                last_away_notice TEXT,
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS relay (
                admin_msg_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                direction TEXT NOT NULL,       -- 'in' (from user) / 'out' (to user)
                source TEXT NOT NULL,          -- 'user' / 'admin' / 'ai' / 'canned' / 'system'
                text TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canned_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def upsert_user(user_id: int, username: str | None, first_name: str | None):
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_seen)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen=CURRENT_TIMESTAMP
            """,
            (user_id, username, first_name),
        )


def all_user_ids() -> list[int]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE blocked = 0").fetchall()
    return [r[0] for r in rows]


def count_users() -> int:
    with closing(_connect()) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
    return count


def set_blocked(user_id: int, blocked: bool):
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (1 if blocked else 0, user_id))


def is_blocked(user_id: int) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row[0])


def should_send_away_notice(user_id: int, cooldown_minutes: int = 30) -> bool:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT last_away_notice FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row or not row[0]:
        return True
    last = datetime.fromisoformat(row[0])
    return (datetime.utcnow() - last) > timedelta(minutes=cooldown_minutes)


def mark_away_notice_sent(user_id: int):
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE users SET last_away_notice = ? WHERE user_id = ?",
            (datetime.utcnow().isoformat(), user_id),
        )


# --------------------------------------------------------------------------
# Relay mapping (admin message id -> user)
# --------------------------------------------------------------------------

def save_relay(admin_msg_id: int, user_id: int, user_chat_id: int):
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO relay (admin_msg_id, user_id, user_chat_id) VALUES (?, ?, ?)",
            (admin_msg_id, user_id, user_chat_id),
        )


def get_relay(admin_msg_id: int):
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT user_id, user_chat_id FROM relay WHERE admin_msg_id = ?",
            (admin_msg_id,),
        ).fetchone()
    return row  # (user_id, user_chat_id) or None


# --------------------------------------------------------------------------
# Message history
# --------------------------------------------------------------------------

def log_message(user_id: int, direction: str, source: str, text: str):
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO messages (user_id, direction, source, text) VALUES (?, ?, ?, ?)",
            (user_id, direction, source, text or ""),
        )


def get_recent_messages(user_id: int, limit: int = 10):
    """Returns [(direction, text), ...] oldest first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT direction, text FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return list(reversed(rows))


# --------------------------------------------------------------------------
# Canned replies
# --------------------------------------------------------------------------

def add_canned(label: str, text: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO canned_replies (label, text) VALUES (?, ?)", (label, text)
        )
        return cur.lastrowid


def delete_canned(canned_id: int):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM canned_replies WHERE id = ?", (canned_id,))


def list_canned():
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT id, label, text FROM canned_replies ORDER BY id"
        ).fetchall()


def get_canned(canned_id: int):
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT label, text FROM canned_replies WHERE id = ?", (canned_id,)
        ).fetchone()


# --------------------------------------------------------------------------
# Settings (away mode, ai mode, etc.)
# --------------------------------------------------------------------------

def get_setting(key: str, default=None):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value):
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------

def get_stats() -> dict:
    with closing(_connect()) as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM users WHERE blocked = 1").fetchone()[0]
        total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        today_messages = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE date(created_at) = date('now')"
        ).fetchone()[0]
    return {
        "total_users": total_users,
        "blocked": blocked,
        "total_messages": total_messages,
        "today_messages": today_messages,
    }
