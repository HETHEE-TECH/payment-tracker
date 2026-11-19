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

SQLITE_SCHEMAS = ["""
CREATE TABLE IF NOT EXISTS payments (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT    NOT NULL,
    amount   REAL    NOT NULL,
    date     TEXT    NOT NULL,           -- YYYY-MM-DD
    status   TEXT    NOT NULL DEFAULT 'pending',
    new_date TEXT,                        -- set when status is 'delayed'
    description TEXT,                     -- comment written by the user (also goes into the Excel sheet)
    extra_amount REAL,                    -- the extra paid, when the status is 'extra_paid'
    paid_amount REAL,                     -- no longer used (older version)
    awaiting_amount REAL,                 -- Awaiting income: amount still to come in (not part of the totals)
    we_paid_amount REAL,                  -- We paid: amount we paid (not part of the totals)
    part_pending_amount REAL              -- Part paid pending: the pending part of a part payment (not part of the totals)
)
""", """
-- Every file you import (CSV, Excel, PDF) is kept here, so it is not lost.
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT    NOT NULL,
    content_type TEXT   NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT    NOT NULL,            -- same file twice is stored once
    uploaded_at TEXT    NOT NULL,
    data        BLOB    NOT NULL
)
""", """
-- One image per payment (for example a payment screenshot).
CREATE TABLE IF NOT EXISTS payment_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id   INTEGER NOT NULL,
    content_type TEXT    NOT NULL,
    data         BLOB    NOT NULL
)
"""]

# Payments saved with the statuses of an earlier version become a normal status plus an amount.
LEGACY_STATUS_MIGRATION = [
    # Delayed is now a mark that can sit next to Paid or Cancel: it is the delayed date (new_date) being set.
    "UPDATE payments SET status = 'pending' WHERE status = 'delayed'",
    "UPDATE payments SET awaiting_amount = amount, status = 'pending' WHERE status = 'need_to_come'",
    "UPDATE payments SET we_paid_amount = extra_amount, status = 'paid' WHERE status = 'extra_paid'",
    "UPDATE payments SET part_pending_amount = amount - COALESCE(paid_amount, 0), status = 'pending' WHERE status = 'part_paid'",
]

POSTGRES_SCHEMAS = ["""
CREATE TABLE IF NOT EXISTS payments (
    id       SERIAL PRIMARY KEY,
    name     TEXT             NOT NULL,
    amount   DOUBLE PRECISION NOT NULL,
    date     TEXT             NOT NULL,   -- YYYY-MM-DD
    status   TEXT             NOT NULL DEFAULT 'pending',
    new_date TEXT,                         -- set when status is 'delayed'
    description TEXT,                      -- comment written by the user (also goes into the Excel sheet)
    extra_amount DOUBLE PRECISION,         -- the extra paid, when the status is 'extra_paid'
    paid_amount DOUBLE PRECISION,          -- no longer used (older version)
    awaiting_amount DOUBLE PRECISION,      -- Awaiting income: amount still to come in (not part of the totals)
    we_paid_amount DOUBLE PRECISION,       -- We paid: amount we paid (not part of the totals)
    part_pending_amount DOUBLE PRECISION   -- Part paid pending: the pending part of a part payment (not part of the totals)
)
""", """
CREATE TABLE IF NOT EXISTS files (
    id           SERIAL PRIMARY KEY,
    filename     TEXT    NOT NULL,
    content_type TEXT    NOT NULL,
    size         INTEGER NOT NULL,
    sha256       TEXT    NOT NULL,
    uploaded_at  TEXT    NOT NULL,
    data         BYTEA   NOT NULL
)
""", """
CREATE TABLE IF NOT EXISTS payment_images (
    id           SERIAL PRIMARY KEY,
    payment_id   INTEGER NOT NULL,
    content_type TEXT    NOT NULL,
    data         BYTEA   NOT NULL
)
"""]


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
        for statement in POSTGRES_SCHEMAS:
            raw.execute(statement)
        # Databases made before the description column existed get it added here.
        raw.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS description TEXT")
        raw.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS extra_amount DOUBLE PRECISION")
        raw.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS paid_amount DOUBLE PRECISION")
        for column in ("awaiting_amount", "we_paid_amount", "part_pending_amount"):
            raw.execute(f"ALTER TABLE payments ADD COLUMN IF NOT EXISTS {column} DOUBLE PRECISION")
        for statement in LEGACY_STATUS_MIGRATION:
            raw.execute(statement)
        raw.commit()
        raw.close()
        return
    conn = sqlite3.connect(DB_PATH)
    for statement in SQLITE_SCHEMAS:
        conn.execute(statement)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
    if "description" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN description TEXT")  # database from before this column
    if "extra_amount" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN extra_amount REAL")
    if "paid_amount" not in columns:
        conn.execute("ALTER TABLE payments ADD COLUMN paid_amount REAL")
    for column in ("awaiting_amount", "we_paid_amount", "part_pending_amount"):
        if column not in columns:
            conn.execute(f"ALTER TABLE payments ADD COLUMN {column} REAL")
    for statement in LEGACY_STATUS_MIGRATION:
        conn.execute(statement)
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