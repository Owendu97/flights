"""SQLite schema and connection helpers.

Schema follows PROJECT_BRIEF §6 (price_calendar + flight_snapshot), plus a
small meta_routes table that tracks which (orig, dest) pairs are seeded.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta_routes (
    route TEXT PRIMARY KEY,
    orig TEXT NOT NULL,
    dest TEXT NOT NULL,
    note TEXT,
    depdate TEXT NOT NULL,
    is_real INTEGER DEFAULT 1,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route TEXT NOT NULL,
    flight_date TEXT NOT NULL,           -- ISO 'YYYY-MM-DD'
    depdate_anchor TEXT NOT NULL,
    days_before_departure INTEGER NOT NULL,
    price INTEGER,
    is_lowest INTEGER DEFAULT 0,
    raw_text TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(route, flight_date, depdate_anchor)
);
CREATE INDEX IF NOT EXISTS idx_cal_route_date_anchor
  ON price_calendar(route, flight_date, depdate_anchor);

CREATE TABLE IF NOT EXISTS flight_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route TEXT NOT NULL,
    flight_date TEXT NOT NULL,
    flight_no TEXT NOT NULL,
    airline TEXT,
    aircraft TEXT,
    dep_airport TEXT,
    arr_airport TEXT,
    dep_time TEXT,
    arr_time TEXT,
    stops INTEGER DEFAULT 0,
    time_bucket TEXT,
    price INTEGER NOT NULL,
    discount_rate REAL,
    inferred_full_price REAL,
    is_main_flight INTEGER DEFAULT 1,
    is_shared INTEGER DEFAULT 0,
    actual_operator TEXT,
    bucket_rank INTEGER DEFAULT 1,           -- 1=default per-bucket pick, 2=extra slot for the super-cheap bucket
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(route, flight_date, flight_no)
);
CREATE INDEX IF NOT EXISTS idx_flight_route_date_bucket
  ON flight_snapshot(route, flight_date, time_bucket, price);
CREATE INDEX IF NOT EXISTS idx_flight_shared
  ON flight_snapshot(route, flight_date, is_shared);
CREATE INDEX IF NOT EXISTS idx_flight_rank
  ON flight_snapshot(route, flight_date, bucket_rank);

CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    route TEXT NOT NULL,
    depdate_anchor TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    flights_count INTEGER,
    calendar_count INTEGER,
    status TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    """Open a new SQLite connection with row factory set."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if not exist + run lightweight migrations.

    Order matters: MIGRATIONS FIRST (add columns to legacy DBs) → THEN
    CREATE TABLE IF NOT EXISTS (no-op for legacy, fresh for new) →
    THEN CREATE INDEX (which references the new columns).
    """
    conn = get_conn()

    # 1) Forward-compat migrations: add new columns to existing tables.
    migrations = [
        ("flight_snapshot", "is_shared",     "INTEGER DEFAULT 0"),
        ("flight_snapshot", "actual_operator", "TEXT"),
        ("flight_snapshot", "bucket_rank",   "INTEGER DEFAULT 1"),
    ]
    for table, col, decl in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            conn.commit()
        except Exception:
            # Column already exists — safe to ignore.
            pass

    # 2) Create tables + indexes from the canonical schema.
    for stmt in SCHEMA.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
    conn.close()


def table_counts() -> dict:
    """Return row counts per table — handy for /api/health and smoke checks."""
    conn = get_conn()
    out = {}
    for tbl in ("meta_routes", "price_calendar", "flight_snapshot", "scrape_log"):
        out[tbl] = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()["n"]
    conn.close()
    return out


if __name__ == "__main__":
    init_db()
    print(f"[db] initialized at {DB_PATH}")
    print(f"[db] counts: {table_counts()}")
