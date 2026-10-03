"""YouTube-specific derived metrics, precomputed so the LLM only interprets."""

import duckdb
import pandas as pd

from core.aggregate.common import LOCAL, TZ, WHERE, filters

CAVEAT = (
    "Số lượt xem là số mục trong lịch sử YouTube, không phải thời gian xem: "
    "Takeout không có thời lượng, một mục có thể là Shorts, autoplay hoặc mở rồi bỏ."
)
# A "night" runs until 05:00, so a 00:30 watch counts toward the previous evening.
NIGHT_CUTOFF_HOURS = 5


def nightly(
    con: duckdb.DuckDBPyConnection, account: str, since: str | None = None, until: str | None = None
) -> pd.DataFrame:
    """Per month: median time of the last watch each night, share of nights past midnight,
    share of watches in 22h-1h."""
    return con.execute(
        f"""
        WITH w AS (SELECT {LOCAL} AS t FROM events WHERE {WHERE}),
        nights AS (
            SELECT (t - INTERVAL {NIGHT_CUTOFF_HOURS} HOUR)::DATE AS night,
                   epoch(max(t) - (t - INTERVAL {NIGHT_CUTOFF_HOURS} HOUR)::DATE::TIMESTAMP) AS last_s,
                   avg((hour(t) IN (22, 23, 0, 1))::INT) AS late_share, count(*) AS n
            FROM w GROUP BY night
        )
        SELECT strftime(night, '%Y-%m') AS month,
               strftime(TIMESTAMP '2000-01-01' + to_seconds(median(last_s)::BIGINT), '%H:%M')
                   AS median_last_watch,
               round(100 * avg((last_s >= 86400)::INT)) AS pct_nights_past_midnight,
               round(100 * sum(late_share * n) / sum(n), 1) AS pct_watch_22h_1h
        FROM nights GROUP BY month ORDER BY month
        """,
        filters("watch", account, since, until, "youtube"),
    ).df()


def yearly(
    con: duckdb.DuckDBPyConnection, account: str, since: str | None = None, until: str | None = None
) -> pd.DataFrame:
    """Per year: volume, active days, and per-active-day rate on weekdays vs weekends."""
    return con.execute(
        f"""
        WITH w AS (SELECT {LOCAL} AS t, dayofweek({LOCAL}) IN (0, 6) AS weekend
                   FROM events WHERE {WHERE})
        SELECT year(t) AS year, count(*) AS watches, count(DISTINCT t::DATE) AS active_days,
               round(count(*) / count(DISTINCT t::DATE)) AS per_active_day,
               round(count(*) FILTER (NOT weekend)
                     / count(DISTINCT t::DATE) FILTER (NOT weekend)) AS weekday_per_day,
               round(count(*) FILTER (weekend)
                     / count(DISTINCT t::DATE) FILTER (weekend)) AS weekend_per_day
        FROM w GROUP BY year ORDER BY year
        """,
        filters("watch", account, since, until, "youtube"),
    ).df()


def quarterly(
    con: duckdb.DuckDBPyConnection, account: str, since: str | None = None, until: str | None = None
) -> pd.DataFrame:
    """Per quarter: volume and per-active-day rate."""
    return con.execute(
        f"""
        WITH w AS (SELECT {LOCAL} AS t FROM events WHERE {WHERE})
        SELECT year(t) || '-Q' || quarter(t) AS quarter, count(*) AS watches,
               round(count(*) / count(DISTINCT t::DATE)) AS per_active_day
        FROM w GROUP BY ALL ORDER BY quarter
        """,
        filters("watch", account, since, until, "youtube"),
    ).df()


CHANNEL = "(metadata->>'channel')"
# Gap since the previous watch. < 60s = fast swiping (Shorts-like). Gaps under 30 min, summed,
# approximate time on YouTube (each gap ~ how long the previous item played).
FAST_SWIPE_S = 60
SESSION_GAP_S = 1800


def monthly_profile(
    con: duckdb.DuckDBPyConnection, account: str, since: str | None = None, until: str | None = None
) -> pd.DataFrame:
    """Per month: how the account watches (long videos vs fast swiping), not just how much."""
    return con.execute(
        f"""
        WITH w AS (SELECT {LOCAL} AS t, content, {CHANNEL} AS ch FROM events WHERE {WHERE}),
        g AS (SELECT *, epoch(t - lag(t) OVER (ORDER BY t)) AS gap FROM w)
        SELECT strftime(t, '%Y-%m') AS month, count(*) AS watches,
               (median(gap) FILTER (gap < {SESSION_GAP_S}))::INT AS median_gap_s,
               round(100 * avg((gap < {FAST_SWIPE_S})::INT)) AS pct_fast_swipe,
               round(100 * avg((lower(content) LIKE '%#short%')::INT), 1) AS pct_shorts_tag,
               count(DISTINCT ch) AS channels,
               round(sum(gap) FILTER (gap < {SESSION_GAP_S}) / 3600 / count(DISTINCT t::DATE), 1)
                   AS est_hours_per_active_day
        FROM g GROUP BY month ORDER BY month
        """,
        filters("watch", account, since, until, "youtube"),
    ).df()


def top_channels(
    con: duckdb.DuckDBPyConnection,
    account: str,
    since: str | None = None,
    until: str | None = None,
    new_only: bool = False,
    limit: int = 20,
) -> pd.DataFrame:
    """Most watched channels in a window; new_only keeps channels first seen inside it."""
    return con.execute(
        f"""
        WITH first AS (
            SELECT {CHANNEL} AS ch, min(timestamp) AS first_seen FROM events
            WHERE account = $account AND action_type = 'watch' GROUP BY ch
        )
        SELECT {CHANNEL} AS channel, count(*) AS watches,
               strftime(timezone('{TZ}', any_value(f.first_seen)), '%Y-%m-%d') AS first_seen,
               coalesce(any_value(c.category), '-') AS category
        FROM events JOIN first f ON f.ch = {CHANNEL}
        LEFT JOIN channel_categories c ON c.account = events.account AND c.channel = {CHANNEL}
        WHERE {WHERE.replace("account =", "events.account =")}
          AND (NOT $new_only OR $since::DATE IS NULL OR f.first_seen >= $since::DATE)
        GROUP BY 1 ORDER BY watches DESC, channel LIMIT $limit
        """,
        {**filters("watch", account, since, until, "youtube"), "new_only": new_only, "limit": limit},
    ).df()


def by_category(
    con: duckdb.DuckDBPyConnection,
    account: str,
    period: str = "quarter",
    since: str | None = None,
    until: str | None = None,
) -> pd.DataFrame:
    """% of watches per topic per period (wide). Channels not classified -> 'unclassified'."""
    df = con.execute(
        f"""
        SELECT strftime(date_trunc($period, {LOCAL}), '%Y-%m') AS period,
               coalesce(c.category, 'unclassified') AS category, count(*) AS n
        FROM events LEFT JOIN channel_categories c
          ON c.account = events.account AND c.channel = {CHANNEL}
        WHERE {WHERE.replace("account =", "events.account =")}
        GROUP BY ALL
        """,
        {"period": period, **filters("watch", account, since, until, "youtube")},
    ).df()
    wide = df.pivot_table(index="period", columns="category", values="n", fill_value=0)
    return (wide.div(wide.sum(axis=1), axis=0) * 100).round(1).reset_index().rename_axis(None, axis=1)
