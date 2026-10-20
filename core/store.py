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
-- Enrichment from core/classify.py: one topic per channel, shared by every account.
CREATE TABLE IF NOT EXISTS channel_categories (
    channel VARCHAR PRIMARY KEY,
    category VARCHAR NOT NULL
);
"""
# ponytail: one-off migration of v0.2 stores (topics keyed by account, channel); drop after v0.3.
MIGRATE_V02 = """
CREATE TEMP TABLE cc AS SELECT channel, min(category) AS category FROM channel_categories GROUP BY channel;
DROP TABLE channel_categories;
"""


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    old = con.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'channel_categories' AND column_name = 'account'"
    ).fetchone()
    if old:
        con.execute(MIGRATE_V02)
    con.execute(SCHEMA)
    if old:
        con.execute("INSERT INTO channel_categories SELECT * FROM cc; DROP TABLE cc")
    return con


def write_events(con: duckdb.DuckDBPyConnection, df: pd.DataFrame) -> int:
    """Idempotent insert; returns the number of new rows."""
    df = df.drop_duplicates(subset=KEY)  # noqa: F841 — referenced by name in SQL
    before = con.execute("SELECT count(*) FROM events").fetchone()[0]
    con.execute("INSERT OR IGNORE INTO events SELECT * FROM df")
    return con.execute("SELECT count(*) FROM events").fetchone()[0] - before
