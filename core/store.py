"""Analytics Store: one DuckDB `events` table in the normalized schema."""

from pathlib import Path

import duckdb
import pandas as pd

# content is part of the key: YouTube logs distinct videos with identical ms timestamps.
KEY = ["timestamp", "source", "account", "action_type", "content"]
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS events (
    timestamp TIMESTAMPTZ NOT NULL,
    source VARCHAR NOT NULL,
    account VARCHAR NOT NULL,
    action_type VARCHAR NOT NULL,
    content VARCHAR,
    metadata JSON,
    PRIMARY KEY ({", ".join(KEY)})
);
-- Enrichment from core/classify.py: one topic per channel.
CREATE TABLE IF NOT EXISTS channel_categories (
    account VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    PRIMARY KEY (account, channel)
);
"""


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA)
    return con


def write_events(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Idempotent insert; returns the number of new rows."""
    df = df.drop_duplicates(subset=KEY)  # noqa: F841 — referenced by name in SQL
    before = con.execute("SELECT count(*) FROM events").fetchone()[0]
    con.execute("INSERT OR IGNORE INTO events SELECT * FROM df")
    return con.execute("SELECT count(*) FROM events").fetchone()[0] - before
