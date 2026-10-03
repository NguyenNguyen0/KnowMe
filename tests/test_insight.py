import json

import pandas as pd

from core import insight, store
from core.aggregate import youtube as yt


def events(rows):
    """rows: (utc time, content, is_ad[, channel]). Synthetic data only."""
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(t, tz="UTC"),
                "source": "youtube",
                "account": "acc",
                "action_type": "watch",
                "content": c,
                "metadata": json.dumps({"is_ad": ad, "channel": (ch or [None])[0]}),
            }
            for t, c, ad, *ch in rows
        ]
    )


def test_nightly_and_prompt(tmp_path):
    con = store.connect(tmp_path / "events.duckdb")
    store.write_events(
        con,
        events(
            [
                # night of 2026-09-01 (local = UTC+7): 23:00, then 00:30 -> last watch 00:30
                ("2026-09-01T16:00:00", "secret title 1", False),
                ("2026-09-01T17:30:00", "secret title 2", False),
                # night of 2026-09-02: ends 22:00; the 23:59 ad is ignored
                ("2026-09-02T15:00:00", "secret title 3", False),
                ("2026-09-02T16:59:00", "ad", True),
                # night of 2026-09-03 (Thursday): ends 23:00
                ("2026-09-03T16:00:00", "secret title 4", False),
                # latest data in October -> October is incomplete and cut from the prompt
                ("2026-10-02T16:00:00", "secret title 5", False),
            ]
        ),
    )

    n = yt.nightly(con, "acc", until="2026-10-01").iloc[0]
    assert n["month"] == "2026-09"
    assert n["median_last_watch"] == "23:00"  # median of 00:30(+24h), 22:00, 23:00
    assert n["pct_nights_past_midnight"] == 33
    assert n["pct_watch_22h_1h"] == 100.0

    y = yt.yearly(con, "acc", until="2026-10-01").iloc[0]
    # 00:30 on 09-02 is a separate calendar day: 3 active days (09-01, 09-02 x2 watches, 09-03)
    assert (y["watches"], y["active_days"]) == (4, 3)

    prompt = insight.build_prompt(con, "sleep", "acc")
    assert "\n2026-09,23:00," in prompt and yt.CAVEAT in prompt
    assert "\n2026-10," not in prompt  # incomplete month cut
    assert "secret title" not in prompt  # aggregates only


def test_classify_categories_and_facts(tmp_path):
    con = store.connect(tmp_path / "events.duckdb")
    store.write_events(
        con,
        events(
            [
                ("2026-01-05T10:00:00", "title 0", False, "Chan Music"),
                ("2026-01-05T10:00:30", "title 1 #shorts", False, "Chan Music"),  # 30s gap
                ("2026-01-05T10:10:30", "title 2", False, "Chan Code"),  # 600s gap
                ("2026-04-05T10:00:00", "title 3", False, "Chan New"),  # first seen in Q2
            ]
        ),
    )

    from core import classify

    # fake LLM: answers by position; garbage line ignored, unknown code -> other, line 3 missing
    def fake(prompt):
        assert "Chan Music" in prompt and "title" in prompt
        return "1: music\nnoise\n2: cooking"

    progress = list(classify.classify(con, "acc", fake, batch_size=10, workers=1))
    assert progress == [(3, 3)]
    got = dict(con.execute("SELECT channel, category FROM channel_categories").fetchall())
    assert got == {"Chan Music": "music", "Chan Code": "other"}  # Chan New stays pending
    assert list(classify.pending(con, "acc", 10)["channel"]) == ["Chan New"]

    shares = yt.by_category(con, "acc")
    assert shares.set_index("period").loc["2026-01", "music"] == 66.7
    assert shares.set_index("period").loc["2026-04", "unclassified"] == 100.0

    p = yt.monthly_profile(con, "acc", until="2026-02-01").iloc[0]
    assert (p["watches"], p["median_gap_s"], p["pct_shorts_tag"], p["channels"]) == (3, 315, 33.3, 2)

    new = yt.top_channels(con, "acc", since="2026-04-01", new_only=True)
    assert new[["channel", "category"]].values.tolist() == [["Chan New", "-"]]

    f = insight.facts(pd.DataFrame({"q": ["a", "b", "c"], "v": [1, 10, 8]}), "q", ["v"])
    assert f == ["v: cao nhất b (10), thấp nhất a (1); thay đổi lớn nhất giữa hai kỳ liền nhau: a → b (1 → 10)"]


def test_around_date(tmp_path):
    con = store.connect(tmp_path / "events.duckdb")
    day = pd.Timedelta(days=1)
    rows = []
    # youtube.watch: 1/day for 14 days before 2025-04-02, 5/day for 14 days after (UTC noon = 19h local)
    for i in range(-14, 14):
        t = pd.Timestamp("2025-04-02T05:00:00", tz="UTC") + i * day
        for j in range(1 if i < 0 else 5):
            rows.append((t + j * pd.Timedelta(minutes=1), f"secret {i} {j}", "youtube", "watch"))
    rows.append((pd.Timestamp("2025-03-26T05:00:00", tz="UTC"), "secret setting", "facebook", "setting"))
    df = pd.DataFrame(
        [
            {"timestamp": t, "source": s, "account": "acc", "action_type": a, "content": c,
             "metadata": json.dumps({})}
            for t, c, s, a in rows
        ]
    )
    store.write_events(con, df)

    tables, fact_lines = insight._around(con, "acc", "2025-04-02")
    assert fact_lines[0] == "Thay đổi rõ nhất: youtube.watch"
    assert "từ ngày 2025-04-02" in fact_lines[1]
    assert fact_lines[2].endswith("là 1.0, 14 ngày từ 2025-04-02 là 5.0 (×5.0)")
    assert any("(facebook.setting, cả lịch sử chỉ 1 lần): ngày 2025-03-26 (7 ngày trước mốc)" in f for f in fact_lines)
    daily = next(iter(tables.values()))
    assert list(daily.columns) == ["day", "youtube.watch"] and len(daily) == 15

    prompt = insight.build_prompt(con, "around", "acc", "2025-04-02")
    assert "quanh ngày 2025-04-02" in prompt and "YouTube, Google và Facebook" in prompt
    assert "secret" not in prompt
