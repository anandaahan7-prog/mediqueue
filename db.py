"""Database helpers. Every request gets its own connection (Flask runs requests in threads)."""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

# MEDIQUEUE_DB lets the tests point the app at a throwaway database.
# On Vercel only /tmp is writable, so the demo database lives there (one copy per server instance).
_default_db = Path("/tmp/mediqueue.db") if os.environ.get("VERCEL") else Path(__file__).resolve().parent / "mediqueue.db"
DB_PATH = Path(os.environ.get("MEDIQUEUE_DB", _default_db))
SCHEMA = Path(__file__).resolve().parent / "schema.sql"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def connect(path=None):
    # isolation_level=None -> we write BEGIN / COMMIT ourselves, so the critical sections are explicit
    conn = sqlite3.connect(path or DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db(path=None):
    conn = connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
    return conn


def now():
    return datetime.now()


def stamp(dt=None):
    return (dt or now()).strftime(TIME_FORMAT)


def parse(text):
    return datetime.strptime(text, TIME_FORMAT) if text else None


def minutes_between(start, end=None):
    if start is None:
        return 0.0
    start = parse(start) if isinstance(start, str) else start
    return ((end or now()) - start).total_seconds() / 60


def log_event(conn, kind, detail, visit_id=None, user_id=None):
    conn.execute("INSERT INTO events (visit_id, user_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
                 (visit_id, user_id, stamp(), kind, detail))


def setting(conn, key, default):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return type(default)(row["value"]) if row else default
