"""Shared aggregations over `events`. Ads are always excluded."""

import duckdb
import pandas as pd

TZ = "Asia/Ho_Chi_Minh"
LOCAL = f"timezone('{TZ}', timestamp)"
# Optional filters: pass None to disable. Bind with filters().
WHERE = """
    action_type = $action_type
    AND (metadata->>'is_ad') IS DISTINCT FROM 'true'
    AND ($source::VARCHAR IS NULL OR source = $source)
    AND ($account::VARCHAR IS NULL OR account = $account)
    AND ($since::DATE IS NULL OR timestamp >= $since::DATE)
    AND ($until::DATE IS NULL OR timestamp < $until::DATE)
"""


def filters(
    action_type: str = "watch",
    account: str | None = None,
    since: str | None = None,
    until: str | None = None,
    source: str | None = None,
) -> dict:
    return {
        "action_type": action_type,
        "account": account,
        "since": since,
        "until": until,
        "source": source,
    }


def by_period(
    con: duckdb.DuckDBPyConnection,
    action_type: str = "watch",
    period: str = "week",
    account: str | None = None,
    since: str | None = None,
    until: str | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    """Event count per account per day/week/month/quarter/year (local time)."""
    return con.execute(
        f"""
        SELECT account, date_trunc($period, {LOCAL})::DATE AS period, count(*) AS n
        FROM events WHERE {WHERE}
        GROUP BY ALL ORDER BY account, period
        """,
        {"period": period, **filters(action_type, account, since, until, source)},
    ).df()


def by_hour(
    con: duckdb.DuckDBPyConnection,
    action_type: str = "watch",
    account: str | None = None,
    since: str | None = None,
    until: str | None = None,
    source: str | None = None,
) -> pd.DataFrame:
    """Event count per account per hour of day (local time)."""
    return con.execute(
        f"""
        SELECT account, hour({LOCAL}) AS hour, count(*) AS n
        FROM events WHERE {WHERE}
        GROUP BY ALL ORDER BY account, hour
        """,
        filters(action_type, account, since, until, source),
    ).df()


def activity(
    con: duckdb.DuckDBPyConnection,
    account: str,
    period: str = "day",
    since: str | None = None,
    until: str | None = None,
) -> pd.DataFrame:
    """Cross-source timeline: one column per source.action_type, event counts per period."""
    df = con.execute(
        f"""
        SELECT strftime(date_trunc($period, {LOCAL}), '%Y-%m-%d') AS period,
               source || '.' || action_type AS kind, count(*) AS n
        FROM events
        WHERE account = $account AND (metadata->>'is_ad') IS DISTINCT FROM 'true'
          AND ($since::DATE IS NULL OR timestamp >= $since::DATE)
          AND ($until::DATE IS NULL OR timestamp < $until::DATE)
        GROUP BY ALL
        """,
        {"period": period, "account": account, "since": since, "until": until},
    ).df()
    wide = df.pivot_table(index="period", columns="kind", values="n", fill_value=0)
    return wide.astype(int).reset_index().rename_axis(None, axis=1)
