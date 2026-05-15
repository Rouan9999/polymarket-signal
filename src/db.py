"""SQLite schema and connection helpers.

All tables for the full pipeline are defined here up front so we don't churn
the schema as later steps land. Only `traders` is written in step 1.
"""
import sqlite3
from contextlib import contextmanager

from .config import DB_PATH


SCHEMA = """
-- Daily snapshot of the top-N leaderboard. One row per (date, wallet).
CREATE TABLE IF NOT EXISTS traders (
    snapshot_date TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    proxy_wallet  TEXT NOT NULL,
    user_name     TEXT,
    vol           REAL,
    pnl           REAL,
    verified      INTEGER,
    fetched_at    TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, proxy_wallet)
);
CREATE INDEX IF NOT EXISTS idx_traders_wallet ON traders(proxy_wallet);

-- Active positions per wallet per snapshot. One row per (date, wallet, token).
CREATE TABLE IF NOT EXISTS positions (
    snapshot_date     TEXT NOT NULL,
    proxy_wallet      TEXT NOT NULL,
    condition_id      TEXT NOT NULL,
    token_id          TEXT NOT NULL,
    market_question   TEXT,
    outcome           TEXT,        -- 'Yes' or 'No' (side held)
    size              REAL,        -- token quantity held
    avg_price         REAL,        -- trader's average entry price
    current_price     REAL,
    current_value     REAL,
    initial_value     REAL,
    cash_pnl          REAL,
    percent_pnl       REAL,
    days_to_resolution INTEGER,
    end_date          TEXT,
    category          TEXT,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, proxy_wallet, token_id)
);
CREATE INDEX IF NOT EXISTS idx_positions_condition ON positions(condition_id);
CREATE INDEX IF NOT EXISTS idx_positions_token     ON positions(token_id);

-- Generated signals. One row per (market_side, generation_day).
CREATE TABLE IF NOT EXISTS signals (
    signal_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT NOT NULL,
    condition_id       TEXT NOT NULL,
    token_id           TEXT NOT NULL,
    market_question    TEXT,
    side               TEXT NOT NULL,    -- 'Yes' | 'No'
    entry_price        REAL NOT NULL,
    consensus_count    INTEGER NOT NULL,
    consensus_size_usd REAL NOT NULL,
    days_to_resolution INTEGER,
    bucket             TEXT,             -- '2-7d' | '8-21d'
    score              REAL,
    category           TEXT,
    market_url         TEXT,
    UNIQUE (condition_id, token_id, created_at)
);
CREATE INDEX IF NOT EXISTS idx_signals_token ON signals(token_id);

-- Paper trade book. One row per opened paper position.
CREATE TABLE IF NOT EXISTS paper_positions (
    position_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id    INTEGER NOT NULL,
    opened_at    TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    size_usd     REAL NOT NULL,
    status       TEXT NOT NULL,          -- 'OPEN' | 'CLOSED'
    closed_at    TEXT,
    exit_price   REAL,
    pnl_usd      REAL,
    outcome      TEXT,                    -- 'WIN' | 'LOSE' | NULL while open
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id)
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_positions(status);

-- Pipeline funnel telemetry. One row per run.
CREATE TABLE IF NOT EXISTS funnel_runs (
    run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                  TEXT NOT NULL,
    snapshot_date           TEXT NOT NULL,
    starting_candidates     INTEGER NOT NULL,
    after_price_floor       INTEGER NOT NULL,
    after_price_ceiling     INTEGER NOT NULL,
    after_resolution_window INTEGER NOT NULL,
    after_latency           INTEGER NOT NULL,
    after_category          INTEGER NOT NULL,
    after_liquidity         INTEGER NOT NULL,
    final_signals           INTEGER NOT NULL,
    notes                   TEXT
);
"""


@contextmanager
def get_conn():
    """Yield a SQLite connection with row factory; commit on success."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    print(f"Initialized database at {DB_PATH}")


if __name__ == "__main__":
    init_db()
