"""
SQLite storage layer.

Tables:
- users         everyone who has ever messaged the bot (+ blocked flag,
                last away-notice timestamp for throttling)
- relay         maps an admin-side message id back to the original user,
                so admin replies / button taps route to the right person
- messages      full message log (for /history), each row tagged with a
                source: user / admin / ai / canned / system / faq
- canned_replies  quick-reply presets shown as buttons under relayed messages
- settings      simple key/value store (away_mode, away_message, ai_mode,
                start_message, ai_provider, ai_api_key, ai_model, ...)
- notes         admin's private notes about a user (/note)
- faq           keyword -> auto-reply pairs (/addfaq)
- reminders     scheduled one-off messages to a user (/remind)
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
                direction TEXT NOT NULL,   -- 'in' (from user) / 'out' (to user)
                source TEXT NOT NULL,      -- 'user' / 'admin' / 'ai' / 'canned' / 'system' / 'faq'
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                answer TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                remind_at TEXT NOT NULL,
                text TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def get_user_record(user_id: int):
    """Returns (username, first_name, blocked, last_seen) or None."""
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT username, first_name, blocked, last_seen FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def get_user_message_count(user_id: int) -> int:
    with closing(_connect()) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,)
        ).fetchone()
        return count


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
# Settings (away mode, ai mode, start message, ai overrides, etc.)
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


def delete_setting(key: str):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


# --------------------------------------------------------------------------
# Notes (/note)
# --------------------------------------------------------------------------

def add_note(user_id: int, text: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO notes (user_id, text) VALUES (?, ?)", (user_id, text)
        )
        return cur.lastrowid


def get_notes(user_id: int):
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT text, created_at FROM notes WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()


# --------------------------------------------------------------------------
# FAQ auto-responder (/addfaq, /faqlist, /delfaq)
# --------------------------------------------------------------------------

def add_faq(trigger: str, answer: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO faq (trigger, answer) VALUES (?, ?)", (trigger.lower(), answer)
        )
        return cur.lastrowid


def delete_faq(faq_id: int):
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM faq WHERE id = ?", (faq_id,))


def list_faq():
    with closing(_connect()) as conn:
        return conn.execute("SELECT id, trigger, answer FROM faq ORDER BY id").fetchall()


def match_faq(text: str):
    """Case-insensitive substring match against stored triggers. Returns answer or None."""
    if not text:
        return None
    lowered = text.lower()
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT trigger, answer FROM faq").fetchall()
    for trigger, answer in rows:
        if trigger in lowered:
            return answer
    return None


# --------------------------------------------------------------------------
# Reminders (/remind)
# --------------------------------------------------------------------------

def add_reminder(user_id: int, remind_at_iso: str, text: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO reminders (user_id, remind_at, text) VALUES (?, ?, ?)",
            (user_id, remind_at_iso, text),
        )
        return cur.lastrowid


def list_pending_reminders():
    """Returns [(id, user_id, remind_at, text), ...] not yet sent."""
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT id, user_id, remind_at, text FROM reminders WHERE sent = 0 ORDER BY remind_at"
        ).fetchall()


def mark_reminder_sent(reminder_id: int):
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))


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
        faq_count = conn.execute("SELECT COUNT(*) FROM faq").fetchone()[0]
        pending_reminders = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE sent = 0"
        ).fetchone()[0]
        return {
            "total_users": total_users,
            "blocked": blocked,
            "total_messages": total_messages,
            "today_messages": today_messages,
            "faq_count": faq_count,
            "pending_reminders": pending_reminders,
        }
