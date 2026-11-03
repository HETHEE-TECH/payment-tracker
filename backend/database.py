import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "payments.db"


def init_db() -> None:
    """Create the payments table if it does not exist yet."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT    NOT NULL,
            amount   REAL    NOT NULL,
            date     TEXT    NOT NULL,           -- YYYY-MM-DD
            status   TEXT    NOT NULL DEFAULT 'pending',
            new_date TEXT                         -- set when status is 'delayed'
        )
        """
    )
    conn.commit()
    conn.close()


def get_db():
    """FastAPI dependency: one database connection per request."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()