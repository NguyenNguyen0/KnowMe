"""Research questions: pick precomputed tables + facts, build a prompt for the LLM.

One question per call; the LLM only sees aggregates (never raw events).
Done in code, not by prompt rules, because the 4B model gets them wrong otherwise:
incomplete trailing periods are cut, and comparisons (max/min/biggest change) are precomputed.
"""

from collections.abc import Callable

import duckdb
import pandas as pd

from core.aggregate import common
from core.aggregate import youtube as yt

Context = tuple[dict[str, pd.DataFrame], list[str]]  # ({description: table}, facts)


def _last_day(con: duckdb.DuckDBPyConnection, account: str | None) -> pd.Timestamp:
    day = con.execute(
        f"SELECT max({common.LOCAL})::DATE FROM events WHERE $a::VARCHAR IS NULL OR account = $a",
        {"a": account},
    ).fetchone()[0]
    return pd.Timestamp(day)


def _window(con: duckdb.DuckDBPyConnection, account: str, months: int, unit: str) -> tuple[str, str]:
    """(since, until): `months` back from the start of the latest, still incomplete, month/quarter."""
    until = _last_day(con, account).to_period(unit).start_time
    return (until - pd.DateOffset(months=months)).strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d")


def facts(df: pd.DataFrame, x: str, cols: list[str]) -> list[str]:
    """Max, min and biggest change between consecutive periods, per column."""
    out = []
    for c in cols:
        s = df.set_index(x)[c].dropna()
        if len(s) < 2:
            continue
        j = s.diff().abs().idxmax()
        prev = s.index[s.index.get_loc(j) - 1]
        out.append(
            f"{c}: cao nhất {s.idxmax()} ({s.max():g}), thấp nhất {s.idxmin()} ({s.min():g}); "
            f"thay đổi lớn nhất giữa hai kỳ liền nhau: {prev} → {j} ({s[prev]:g} → {s[j]:g})"
        )
    return out


def _sleep(con: duckdb.DuckDBPyConnection, a: str, date: str | None = None) -> Context:
    since, until = _window(con, a, 12, "M")
    nightly = yt.nightly(con, a, since, until).rename(
        columns={
            "pct_nights_past_midnight": "pct_of_nights_past_midnight",
            "pct_watch_22h_1h": "pct_of_watches_22h_1h",
        }
    )
    tables = {
        "Theo tháng, 12 tháng trọn gần nhất. median_last_watch: trung vị giờ của lượt xem cuối "
        "cùng mỗi đêm (một đêm tính đến 05:00 sáng hôm sau). pct_of_nights_past_midnight: % SỐ ĐÊM "
        "có lượt xem sau 00:00. pct_of_watches_22h_1h: % SỐ LƯỢT XEM nằm trong khung 22h-1h": nightly,
        "Tổng số lượt xem theo giờ trong ngày, cùng 12 tháng": common.by_hour(
            con, account=a, since=since, until=until
        )[["hour", "n"]],
    }
    return tables, facts(nightly, "month", ["pct_of_nights_past_midnight", "pct_of_watches_22h_1h"])


def _trend(con: duckdb.DuckDBPyConnection, a: str, date: str | None = None) -> Context:
    since, until = _window(con, a, 24, "Q")
    yearly = yt.yearly(con, a)
    quarterly = yt.quarterly(con, a, since, until)
    cmp = yearly["weekend_per_day"] - yearly["weekday_per_day"]
    years = lambda mask: ", ".join(str(y) for y in yearly.loc[mask, "year"]) or "không có"  # noqa: E731
    tables = {
        "Theo năm (năm cuối có thể chưa hết năm, nên so sánh cột trung bình/ngày, không so tổng). "
        "active_days: số ngày có ít nhất 1 lượt xem. per_active_day: lượt xem trung bình mỗi ngày có "
        "xem; weekday_per_day / weekend_per_day: tương tự, tách ngày thường và cuối tuần": yearly,
        "Theo quý, 8 quý trọn gần nhất": quarterly,
    }
    return tables, [
        *facts(yearly, "year", ["per_active_day"]),
        *facts(quarterly, "quarter", ["per_active_day"]),
        f"Năm cuối tuần xem nhiều hơn ngày thường: {years(cmp > 0)}; bằng nhau: {years(cmp == 0)}; "
        f"ít hơn: {years(cmp < 0)}",
    ]


def _habit(con: duckdb.DuckDBPyConnection, a: str, date: str | None = None) -> Context:
    since, until = _window(con, a, 24, "M")
    profile = yt.monthly_profile(con, a, since, until)
    cols = ["median_gap_s", "pct_fast_swipe", "pct_shorts_tag", "channels", "est_hours_per_active_day"]
    tables = {
        "Theo tháng, 24 tháng trọn gần nhất. median_gap_s: trung vị số giây giữa hai lượt xem liên "
        f"tiếp (trong cùng phiên, khoảng cách < {yt.SESSION_GAP_S // 60} phút); pct_fast_swipe: % lượt "
        f"xem cách lượt trước dưới {yt.FAST_SWIPE_S} giây (dấu hiệu lướt video ngắn); pct_shorts_tag: "
        "% tiêu đề có #shorts; channels: số kênh khác nhau; est_hours_per_active_day: ước tính số giờ "
        "trên YouTube mỗi ngày có xem (cộng các khoảng cách < 30 phút, chỉ là ước lượng)": profile,
    }
    return tables, facts(profile, "month", cols)


def _topics(con: duckdb.DuckDBPyConnection, a: str, date: str | None = None) -> Context:
    since, until = _window(con, a, 36, "Q")
    s = yt.by_category(con, a, "quarter", since, until)
    col = lambda c: s[c] if c in s else 0  # noqa: E731 — a category may be absent
    fun = col("entertainment") + col("gaming") + col("music")
    # Narrow table on purpose: with all 9 categories the 4B model mixed up columns.
    narrow = pd.DataFrame(
        {
            "period": s["period"],
            "entertainment_pct": fun.round(1),
            "learning_pct": col("learning"),
            "other_topics_pct": (100 - fun - col("learning") - col("unclassified")).round(1),
            "unclassified_pct": col("unclassified"),
            "entertainment_per_learning": (fun / col("learning")).round(1),
        }
    )
    tables = {
        "% lượt xem theo nhóm chủ đề của kênh, theo quý (12 quý trọn gần nhất). Nhóm do model local "
        "phân loại theo tên kênh, chỉ cho các kênh xem nhiều nhất. entertainment_pct: giải trí + game "
        "+ âm nhạc; learning_pct: học tập, lập trình, công nghệ, khoa học; other_topics_pct: tin tức, "
        "đời sống, thể thao, khác; unclassified_pct: chưa phân loại (chiếm khoảng một nửa, nên chỉ so "
        "sánh xu hướng, không coi là tỷ lệ tuyệt đối); entertainment_per_learning: số lần giải trí so "
        "với học tập": narrow,
    }
    cols = ["entertainment_pct", "learning_pct", "entertainment_per_learning"]
    return tables, facts(narrow, "period", cols)


AROUND_DAYS = 14
RARE_EVER = 50  # a kind seen at most this many times in the whole history counts as rare
# Readable names for the most common timeline columns (source.action_type).
KINDS = {
    "youtube.watch": "lượt xem YouTube",
    "youtube.search": "lượt tìm kiếm YouTube",
    "youtube.like": "lượt thích video YouTube",
    "google.search": "lượt tìm kiếm Google",
    "google.translate": "lượt dùng Google Dịch",
    "google.maps": "lượt dùng Google Maps",
    "google.image_search": "lượt tìm ảnh Google",
    "google.chrome": "lượt truy cập ghi qua Chrome",
    "facebook.react": "lượt thích/cảm xúc Facebook",
    "facebook.message": "tin nhắn Messenger",
    "facebook.session": "phiên đăng nhập Facebook",
    "facebook.setting": "thay đổi cài đặt Facebook",
}


def _offset(delta: pd.Timedelta) -> str:
    n = delta.days
    return "đúng ngày mốc" if n == 0 else f"{abs(n)} ngày {'trước' if n < 0 else 'sau'} mốc"


def _around(con: duckdb.DuckDBPyConnection, a: str, date: str | None = None) -> Context:
    if not date:
        raise ValueError("question 'around' needs a date (YYYY-MM-DD)")
    d = pd.Timestamp(date)
    day = pd.Timedelta(days=1)
    lo, hi = d - AROUND_DAYS * day, d + AROUND_DAYS * day
    days = pd.date_range(lo, hi - day).strftime("%Y-%m-%d")
    win = (
        common.activity(con, a, "day", lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d"))
        .set_index("period")
        .reindex(days, fill_value=0)
    )
    totals = win.sum().sort_values(ascending=False)
    # Narrow on purpose: only the 6 busiest kinds go into tables; rare ones become facts.
    cols = [k for k in totals.index if totals[k] > 3][:6]
    before, after = win.iloc[:AROUND_DAYS], win.iloc[AROUND_DAYS:]
    fact_lines, change = [], {}
    for k in cols:
        b, f = before[k].mean(), after[k].mean()
        change[k] = (f + 1) / (b + 1)  # +1 smoothing: 0 -> 1.4/day must not outrank 34 -> 170/day
        ratio = f"×{f / b:.1f}" if b else "từ 0"
        fact_lines.append(
            f"{k}: trung bình/ngày {AROUND_DAYS} ngày trước {date} là {b:.1f}, "
            f"{AROUND_DAYS} ngày từ {date} là {f:.1f} ({ratio})"
        )
    if cols:
        # Biggest change in either direction (×5 and ×0.2 are equally strong).
        top = max(cols, key=lambda k: max(change[k], 1 / change[k]))
        fact_lines.insert(0, f"Thay đổi rõ nhất: {top}")
        b = before[top].mean()
        high = win[top] >= 2 * b if b else win[top] > 0
        start = next((win.index[i] for i in range(len(win) - 2) if high.iloc[i : i + 3].all()), None)
        if start:
            fact_lines.insert(1, f"{top} bắt đầu vượt 2 lần mức trước đó và giữ 3 ngày liền từ ngày {start}")
    # Rare = rare over the whole history, not just in this window (a like is not news).
    ever = dict(
        con.execute(
            "SELECT source || '.' || action_type, count(*) FROM events WHERE account = ? GROUP BY 1", [a]
        ).fetchall()
    )
    for k in totals.index:
        if 0 < totals[k] and ever.get(k, 0) <= RARE_EVER:
            when = ", ".join(
                f"{x} ({_offset(pd.Timestamp(x) - d)})" for x in win.index[win[k] > 0]
            )
            fact_lines.append(
                f"Sự kiện hiếm {KINDS.get(k, k)} ({k}, cả lịch sử chỉ {ever[k]} lần): ngày {when}"
            )

    near = win.loc[(d - 7 * day).strftime("%Y-%m-%d") : (d + 7 * day).strftime("%Y-%m-%d"), cols]
    # Whole weeks only (Monday to Monday), so no partial week at either end.
    week = lambda t: t.to_period("W").start_time.strftime("%Y-%m-%d")  # noqa: E731
    weekly = common.activity(con, a, "week", week(d - 6 * 7 * day), week(d + 6 * 7 * day))
    weekly = weekly[["period", *[c for c in cols if c in weekly]]]
    names = "; ".join(f"{k} = {KINDS.get(k, k)}" for k in cols)
    tables = {
        f"Số sự kiện theo ngày, 7 ngày trước và sau {date}. Cột: {names}": near.reset_index(names="day"),
        f"Số sự kiện theo tuần (ngày đầu tuần), 6 tuần trước và sau {date}": weekly,
    }
    return tables, fact_lines


COMPARE_SOURCES = ("youtube", "google")  # every Google account has these; Facebook belongs to one


def _accounts(con: duckdb.DuckDBPyConnection, a: str | None = None, date: str | None = None) -> Context:
    """Every account side by side (`a` is ignored): how much each is used, per month and per hour."""
    ends = con.execute(
        f"""
        SELECT account, min({common.LOCAL})::DATE, max({common.LOCAL})::DATE FROM events
        WHERE source IN {COMPARE_SOURCES} GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    if len(ends) < 2:
        raise ValueError("question 'accounts' needs YouTube/Google data for at least 2 accounts")
    # Stop at the earliest export end: after it, one account would look idle when it is just not exported.
    until = pd.Timestamp(min(e for _, _, e in ends)).to_period("M").start_time
    since = until - pd.DateOffset(months=12)
    df = con.execute(
        f"""
        SELECT strftime(date_trunc('month', {common.LOCAL}), '%Y-%m') AS month, account,
               hour({common.LOCAL}) AS hour
        FROM events
        WHERE source IN {COMPARE_SOURCES} AND (metadata->>'is_ad') IS DISTINCT FROM 'true'
          AND timestamp >= $since::DATE AND timestamp < $until::DATE
        """,
        {"since": since.strftime("%Y-%m-%d"), "until": until.strftime("%Y-%m-%d")},
    ).df()
    accounts = [acc for acc, _, _ in ends]
    monthly = (
        df.pivot_table(index="month", columns="account", values="hour", aggfunc="size", fill_value=0)
        .reindex(columns=accounts, fill_value=0)
        .reindex(pd.period_range(since, until - pd.Timedelta(days=1), freq="M").strftime("%Y-%m"), fill_value=0)
        .astype(int)
    )
    by_hour = df.pivot_table(index="hour", columns="account", values="month", aggfunc="size", fill_value=0)
    by_hour = by_hour.reindex(index=range(24), columns=accounts, fill_value=0)
    pct_hour = (by_hour / by_hour.sum().where(lambda t: t > 0) * 100).fillna(0).round(1)

    # A month leads to one account only if it alone has the most events (all-zero months lead nobody).
    alone = monthly.eq(monthly.max(axis=1), axis=0).sum(axis=1) == 1
    lead = monthly.idxmax(axis=1).where(alone)
    fact_lines = [f"Dữ liệu của {acc}: từ {lo} đến {hi}" for acc, lo, hi in ends]
    fact_lines += facts(monthly.reset_index(names="month"), "month", accounts)
    for acc in accounts:
        months = list(monthly.index[lead == acc])
        h = pct_hour[acc]
        fact_lines += [
            f"{acc} có nhiều sự kiện hơn ở {len(months)}/{len(monthly)} tháng"
            + (f": {', '.join(months)}" if months else ""),
            f"{acc}: chiếm {monthly[acc].sum() / max(monthly.values.sum(), 1) * 100:.0f}% tổng sự kiện; "
            f"giờ cao điểm {h.idxmax()}h ({h.max():g}%); {h[8:17].sum():.0f}% trong 8h-17h; "
            f"{h[[22, 23, 0]].sum():.0f}% trong 22h-1h",
        ]
    tables = {
        "Số sự kiện YouTube + Google (xem, tìm kiếm, dịch, bản đồ...) theo tháng, mỗi cột một "
        "tài khoản, 12 tháng trọn mà mọi tài khoản đều có dữ liệu": monthly.reset_index(names="month"),
        "% sự kiện của mỗi tài khoản theo giờ trong ngày (mỗi cột cộng lại 100%), cùng 12 tháng":
            pct_hour.reset_index(names="hour"),
    }
    return tables, fact_lines


# question id -> (question, context builder, subject)
Builder = Callable[[duckdb.DuckDBPyConnection, str, str | None], Context]
QUESTIONS: dict[str, tuple[str, Builder, str]] = {
    "sleep": (
        "Thói quen xem YouTube buổi tối có đang lấn vào giờ ngủ không? Giờ đi ngủ thực tế "
        "khoảng mấy giờ, và thay đổi thế nào qua các tháng?",
        _sleep,
        "YouTube",
    ),
    "trend": (
        "Mức độ xem YouTube thay đổi thế nào theo thời gian? Có giai đoạn nào thay đổi đột ngột "
        "không, và ngày thường khác cuối tuần ra sao?",
        _trend,
        "YouTube",
    ),
    "habit": (
        "Cách xem YouTube thay đổi thế nào: xem video dài hay lướt video ngắn? Thay đổi bắt đầu "
        "từ khi nào và kéo theo những gì (số kênh, thời gian ước tính)?",
        _habit,
        "YouTube",
    ),
    "topics": (
        "Chủ đề quan tâm thay đổi thế nào theo quý? Tỷ lệ giải trí so với học tập/công việc "
        "thay đổi ra sao?",
        _topics,
        "YouTube",
    ),
    "around": (
        "Hoạt động trực tuyến thay đổi thế nào quanh ngày {date}? Nguồn nào thay đổi rõ nhất, "
        "thay đổi bắt đầu đúng ngày nào, và có sự kiện hiếm nào xảy ra gần mốc (cách bao nhiêu ngày)?",
        _around,
        "YouTube, Google và Facebook",
    ),
    "accounts": (
        "Các tài khoản được dùng khác nhau thế nào: tài khoản nào hoạt động nhiều hơn, có giai đoạn "
        "nào đảo chiều không, và mỗi tài khoản thường dùng vào khung giờ nào?",
        _accounts,
        "YouTube và Google",
    ),
}

TEMPLATE = """Bạn là data analyst. Dưới đây là số liệu ĐÃ TÍNH SẴN về lịch sử hoạt động trên {subject} của một người{who} \
(giờ Việt Nam, đã loại quảng cáo, dữ liệu đến hết ngày {last_day}).

Quy tắc:
- Chỉ dùng số liệu bên dưới, không tự tính thêm, không bịa số.
- Mỗi nhận định phải trích con số cụ thể làm bằng chứng, và gọi đúng tên chỉ số theo mô tả bảng.
- Khi nói "cao nhất", "thấp nhất", "thay đổi lớn nhất", "luôn", chỉ dựa vào mục Fact, không tự so sánh.
- Không suy ra quy luật từ 1-2 điểm dữ liệu. Nếu số liệu không đủ để kết luận, nói rõ.
- {caveat}

{tables}

### Fact đã tính sẵn
{facts}

Câu hỏi: {question}

Trả lời bằng tiếng Việt, tối đa 6 gạch đầu dòng, kết thúc bằng một dòng "Kết luận: ..."."""


def build_prompt(
    con: duckdb.DuckDBPyConnection,
    question_id: str,
    account: str | None,
    date: str | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """labels: account id -> what the account is used for (config.yaml), so the model knows."""
    question, context, subject = QUESTIONS[question_id]
    labels = labels or {}
    if question_id == "accounts":
        who = "; ".join(f"{k} = tài khoản {v}" for k, v in labels.items())
        who = f", có nhiều tài khoản ({who})" if who else ", có nhiều tài khoản"
    else:
        who = f" (tài khoản {labels[account]})" if account in labels else ""
    tables, fact_lines = context(con, account, date)
    body = "\n\n".join(f"### {desc}\n{df.to_csv(index=False).strip()}" for desc, df in tables.items())
    return TEMPLATE.format(
        last_day=_last_day(con, account).date(),
        caveat=yt.CAVEAT,
        tables=body,
        facts="\n".join(f"- {f}" for f in fact_lines),
        question=question.format(date=date),
        subject=subject,
        who=who,
    )
