"""SQLite database for API usage logging."""
import sqlite3
import os
import json
from datetime import datetime
from contextlib import contextmanager

# 云平台持久化存储路径（Render 等），或本地目录
DB_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "usage.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if not exist."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_key     TEXT UNIQUE NOT NULL,
                name        TEXT NOT NULL,
                max_calls   INTEGER DEFAULT 0,       -- 0 = unlimited
                is_active   INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS api_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                endpoint        TEXT NOT NULL,
                method          TEXT NOT NULL,
                request_body    TEXT,
                response_body   TEXT,
                status_code     INTEGER,
                model           TEXT,
                prompt_tokens   INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens    INTEGER DEFAULT 0,
                latency_ms      INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_logs_user ON api_logs(user_id);
            CREATE INDEX IF NOT EXISTS idx_logs_time ON api_logs(created_at);
            CREATE INDEX IF NOT EXISTS idx_logs_model ON api_logs(model);
        """)


# ---- User CRUD ----

def create_user(sub_key: str, name: str, max_calls: int = 0) -> dict:
    with db() as conn:
        conn.execute(
            "INSERT INTO users (sub_key, name, max_calls) VALUES (?, ?, ?)",
            (sub_key, name, max_calls)
        )
        row = conn.execute("SELECT * FROM users WHERE sub_key=?", (sub_key,)).fetchone()
        return dict(row)


def get_user_by_key(sub_key: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE sub_key=? AND is_active=1", (sub_key,)).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with db() as conn:
        rows = conn.execute("""
            SELECT u.*,
                   COUNT(l.id) as total_calls,
                   COALESCE(SUM(l.total_tokens), 0) as total_tokens_used
            FROM users u
            LEFT JOIN api_logs l ON l.user_id = u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def update_user(user_id: int, **kwargs):
    allowed = {"name", "max_calls", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [user_id]
    with db() as conn:
        conn.execute(f"UPDATE users SET {sets} WHERE id=?", vals)


def delete_user(user_id: int):
    with db() as conn:
        conn.execute("DELETE FROM api_logs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))


# ---- Log CRUD ----

def log_request(user_id: int, endpoint: str, method: str,
                request_body: str, response_body: str,
                status_code: int, model: str,
                prompt_tokens: int, completion_tokens: int,
                total_tokens: int, latency_ms: int) -> int:
    with db() as conn:
        cur = conn.execute("""
            INSERT INTO api_logs
            (user_id, endpoint, method, request_body, response_body,
             status_code, model, prompt_tokens, completion_tokens,
             total_tokens, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, endpoint, method, request_body, response_body,
              status_code, model, prompt_tokens, completion_tokens,
              total_tokens, latency_ms))
        return cur.lastrowid


def get_user_call_count(user_id: int) -> int:
    with db() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM api_logs WHERE user_id=?", (user_id,)).fetchone()
        return row["c"]


def get_logs(user_id: int = None, limit: int = 100, offset: int = 0,
             start_date: str = None, end_date: str = None) -> list[dict]:
    conditions = []
    params = []
    if user_id:
        conditions.append("l.user_id=?")
        params.append(user_id)
    if start_date:
        conditions.append("l.created_at>=?")
        params.append(start_date)
    if end_date:
        conditions.append("l.created_at<=?")
        params.append(end_date + " 23:59:59")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.extend([limit, offset])

    with db() as conn:
        rows = conn.execute(f"""
            SELECT l.*, u.name as user_name, u.sub_key
            FROM api_logs l
            JOIN users u ON u.id = l.user_id
            {where}
            ORDER BY l.created_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        return [dict(r) for r in rows]


def get_logs_count(user_id: int = None, start_date: str = None, end_date: str = None) -> int:
    conditions = []
    params = []
    if user_id:
        conditions.append("l.user_id=?")
        params.append(user_id)
    if start_date:
        conditions.append("l.created_at>=?")
        params.append(start_date)
    if end_date:
        conditions.append("l.created_at<=?")
        params.append(end_date + " 23:59:59")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    with db() as conn:
        row = conn.execute(f"""
            SELECT COUNT(*) as c FROM api_logs l {where}
        """, params).fetchone()
        return row["c"]


def get_stats() -> dict:
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) as c FROM api_logs").fetchone()["c"]
        tokens = conn.execute("SELECT COALESCE(SUM(total_tokens),0) as t FROM api_logs").fetchone()["t"]
        users = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_active=1").fetchone()["c"]
        today = conn.execute(
            "SELECT COUNT(*) as c FROM api_logs WHERE date(created_at)=date('now','localtime')"
        ).fetchone()["c"]

        # Per-user stats (last 7 days)
        per_user = conn.execute("""
            SELECT u.name, u.sub_key, COUNT(l.id) as calls,
                   COALESCE(SUM(l.total_tokens),0) as tokens
            FROM users u
            LEFT JOIN api_logs l ON l.user_id = u.id
                AND l.created_at >= datetime('now', '-7 days', 'localtime')
            WHERE u.is_active = 1
            GROUP BY u.id
            ORDER BY calls DESC
        """).fetchall()

        # Per-model stats
        per_model = conn.execute("""
            SELECT model, COUNT(*) as calls, SUM(total_tokens) as tokens
            FROM api_logs
            WHERE model IS NOT NULL
            GROUP BY model
            ORDER BY calls DESC
            LIMIT 10
        """).fetchall()

        # Daily stats (last 30 days)
        daily = conn.execute("""
            SELECT date(created_at) as day, COUNT(*) as calls,
                   COALESCE(SUM(total_tokens),0) as tokens
            FROM api_logs
            WHERE created_at >= datetime('now', '-30 days', 'localtime')
            GROUP BY date(created_at)
            ORDER BY day
        """).fetchall()

        return {
            "total_calls": total,
            "total_tokens": tokens,
            "active_users": users,
            "today_calls": today,
            "per_user": [dict(r) for r in per_user],
            "per_model": [dict(r) for r in per_model],
            "daily": [dict(r) for r in daily],
        }


# Init on import
init_db()
