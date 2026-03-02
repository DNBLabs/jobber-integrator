"""
SQLite database and token storage for connected Jobber accounts.
Step 1: shell only; OAuth will populate this in step 2.
"""
import sqlite3
from pathlib import Path
from typing import Any

from app.config import DATABASE_URL, PROJECT_ROOT

# SQLite path from DATABASE_URL (e.g. sqlite:///./app.db)
_db_path: str | None = None
if DATABASE_URL.startswith("sqlite:///"):
    path_part = DATABASE_URL.replace("sqlite:///", "").strip("/")
    if path_part.startswith(".") or (path_part != "" and ":" not in path_part and not path_part.startswith("/")):
        _db_path = str(PROJECT_ROOT / path_part.replace("./", ""))
    else:
        _db_path = path_part


def _get_connection() -> sqlite3.Connection:
    if not _db_path:
        raise RuntimeError("Only SQLite is supported for now. Set DATABASE_URL to sqlite:///./app.db")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    return conn


def check_db() -> bool:
    """Phase 2.3: Return True if DB is reachable (e.g. for health check)."""
    try:
        conn = _get_connection()
        try:
            conn.execute("SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        return False


def init_db() -> None:
    """Create tables if they don't exist. Step 3: add expires_at column if missing."""
    conn = _get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobber_connections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                jobber_account_id TEXT NOT NULL UNIQUE,
                jobber_account_name TEXT,
                access_token TEXT NOT NULL,
                refresh_token TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        # Step 3: add optional expires_at for proactive refresh (migration)
        try:
            conn.execute("ALTER TABLE jobber_connections ADD COLUMN access_token_expires_at TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
    finally:
        conn.close()


def get_connection_by_account_id(account_id: str) -> dict[str, Any] | None:
    """Return stored connection row for a Jobber account id, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM jobber_connections WHERE jobber_account_id = ?",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_connection(
    jobber_account_id: str,
    jobber_account_name: str,
    access_token: str,
    refresh_token: str,
    expires_at: str | None = None,
) -> None:
    """Insert or replace connection for this account. Step 3: optional expires_at."""
    import datetime
    now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO jobber_connections
            (jobber_account_id, jobber_account_name, access_token, refresh_token, created_at, updated_at, access_token_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(jobber_account_id) DO UPDATE SET
                jobber_account_name = excluded.jobber_account_name,
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                updated_at = excluded.updated_at,
                access_token_expires_at = excluded.access_token_expires_at
            """,
            (jobber_account_id, jobber_account_name, access_token, refresh_token, now, now, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def update_tokens(
    jobber_account_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: str | None = None,
) -> None:
    """Step 3: Update only tokens (and optionally expires_at) for an existing connection."""
    import datetime
    now = datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    conn = _get_connection()
    try:
        conn.execute(
            """
            UPDATE jobber_connections
            SET access_token = ?, refresh_token = ?, updated_at = ?, access_token_expires_at = ?
            WHERE jobber_account_id = ?
            """,
            (access_token, refresh_token, now, expires_at, jobber_account_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_connection(jobber_account_id: str) -> None:
    """Remove stored connection (e.g. on disconnect)."""
    conn = _get_connection()
    try:
        conn.execute("DELETE FROM jobber_connections WHERE jobber_account_id = ?", (jobber_account_id,))
        conn.commit()
    finally:
        conn.close()
