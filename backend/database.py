"""
Database access.

- On your own computer: SQLite file (backend/payments.db). Nothing to set up.
- On Render (or anywhere): set the DATABASE_URL environment variable to a Postgres
  connection string (for example from Neon) and the app uses Postgres instead.
  Render's free plan wipes local files on every restart, so SQLite there would lose data.

main.py talks to both through the same few calls: execute, executemany, commit, close.
"""
import os
import sqlite3
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USING_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = Path(os.environ.get("SQLITE_PATH") or Path(__file__).parent / "payments.db")

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    amount   REAL    NOT NULL,
    date     TEXT    NOT NULL,           -- YYYY-MM-DD
    status   TEXT    NOT NULL DEFAULT 'pending',
    new_date TEXT                         -- set when status is 'delayed'
)
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id       SERIAL PRIMARY KEY,
    name     TEXT             NOT NULL,
    amount   DOUBLE PRECISION NOT NULL,
    date     TEXT             NOT NULL,   -- YYYY-MM-DD
    status   TEXT             NOT NULL DEFAULT 'pending',
    new_date TEXT                          -- set when status is 'delayed'
)
"""


class _Result:
    """What execute() returns for Postgres: the same few attributes sqlite3 gives."""

    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class _PostgresConnection:
    """Lets main.py's sqlite-style SQL ('?' placeholders, lastrowid) run on Postgres."""

    def __init__(self, raw):
        self._raw = raw

    @staticmethod
    def _convert(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql, params=()):
        sql = self._convert(sql)
        is_insert = sql.lstrip().upper().startswith("INSERT")
        if is_insert and "RETURNING" not in sql.upper():
            sql += " RETURNING id"
        cursor = self._raw.execute(sql, params)
        lastrowid = cursor.fetchone()["id"] if is_insert else None
        return _Result(cursor, lastrowid)

    def executemany(self, sql, rows):
        with self._raw.cursor() as cursor:
            cursor.executemany(self._convert(sql), rows)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()


def _connect_postgres():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db() -> None:
    """Create the payments table if it does not exist yet."""
    if USING_POSTGRES:
        raw = _connect_postgres()
        raw.execute(POSTGRES_SCHEMA)
        raw.commit()
        raw.close()
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SQLITE_SCHEMA)
    conn.commit()
    conn.close()


def get_db():
    """FastAPI dependency: one database connection per request."""
    if USING_POSTGRES:
        conn = _PostgresConnection(_connect_postgres())
    else:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()