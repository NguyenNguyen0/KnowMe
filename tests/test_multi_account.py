"""Two accounts in one store. Synthetic data only."""

import json

import duckdb
import pandas as pd

from core import classify, insight, store
from core.aggregate import common

LABELS = {"acc_a": "giải trí", "acc_b": "học tập"}


def rows(account, source, action, hour_utc, per_day, start, end, channel=None):
    out = []
    for day in pd.date_range(start, end):
        for i in range(per_day):
            t = day.tz_localize("UTC") + pd.Timedelta(hours=hour_utc, minutes=i)
            out.append(
                {"timestamp": t, "source": source, "account": account, "action_type": action,
                 "content": f"{account} {t}", "metadata": json.dumps({"channel": channel})}
            )
    return out


def make_store(tmp_path):
    con = store.connect(tmp_path / "events.duckdb")
    data = [
        # acc_a: YouTube at 20h local, 3/day; acc_b: Google search at 9h local, 1/day, 5/day in February
        *rows("acc_a", "youtube", "watch", 13, 3, "2026-01-01", "2026-03-10", "Chan A"),
        *rows("acc_b", "google", "search", 2, 1, "2026-01-01", "2026-01-31"),
        *rows("acc_b", "google", "search", 2, 5, "2026-02-01", "2026-02-28"),
        *rows("acc_b", "google", "search", 2, 1, "2026-03-01", "2026-03-10"),
        *rows("acc_a", "youtube", "watch", 14, 1, "2026-01-01", "2026-01-01", "Chan Shared"),
        *rows("acc_b", "youtube", "watch", 3, 1, "2026-01-02", "2026-01-02", "Chan Shared"),
    ]
    store.write_events(con, pd.DataFrame(data))
    return con


def test_accounts_question(tmp_path):
    con = make_store(tmp_path)
    tables, facts = insight._accounts(con)
    monthly = next(iter(tables.values()))
    assert list(monthly.columns) == ["month", "acc_a", "acc_b"] and len(monthly) == 12
    assert monthly["month"].iloc[-1] == "2026-02"  # March is incomplete for both accounts
    assert "acc_a có nhiều sự kiện hơn ở 1/12 tháng: 2026-01" in facts  # empty months lead nobody
    assert "acc_b có nhiều sự kiện hơn ở 1/12 tháng: 2026-02" in facts
    a = next(f for f in facts if f.startswith("acc_a: chiếm"))
    assert "giờ cao điểm 20h" in a and "0% trong 8h-17h" in a
    b = next(f for f in facts if f.startswith("acc_b: chiếm"))
    assert "giờ cao điểm 9h" in b and "100% trong 8h-17h" in b

    prompt = insight.build_prompt(con, "accounts", None, labels=LABELS)
    assert "acc_a = tài khoản giải trí; acc_b = tài khoản học tập" in prompt
    single = insight.build_prompt(con, "trend", "acc_a", labels=LABELS)
    assert "của một người (tài khoản giải trí)" in single


def test_accounts_stay_apart(tmp_path):
    con = make_store(tmp_path)
    wide = common.activity(con, None, "month")
    assert {"acc_a:youtube.watch", "acc_b:google.search", "acc_b:youtube.watch"} <= set(wide.columns)
    assert "youtube.watch" in common.activity(con, "acc_a", "month").columns

    # A channel watched on both accounts is classified once and shared.
    asked = []
    list(classify.classify(con, None, lambda p: asked.append(p) or "1: music\n2: learning", workers=1))
    assert len(asked) == 1
    assert classify.pending(con, "acc_b", 10).empty


def test_migrate_v02_topics(tmp_path):
    path = tmp_path / "events.duckdb"
    old = duckdb.connect(str(path))
    old.execute(
        "CREATE TABLE channel_categories (account VARCHAR, channel VARCHAR, category VARCHAR, "
        "PRIMARY KEY (account, channel))"
    )
    old.execute("INSERT INTO channel_categories VALUES ('a', 'ch', 'music'), ('b', 'ch', 'music')")
    old.close()
    con = store.connect(path)
    assert con.execute("SELECT * FROM channel_categories").fetchall() == [("ch", "music")]


def test_classify_review(tmp_path):
    con = make_store(tmp_path)
    con.execute("INSERT INTO channel_categories VALUES ('Chan A', 'music'), ('Chan Shared', 'learning')")
    s = classify.review_sample(con, 10)
    assert sorted(s["channel"]) == ["Chan A", "Chan Shared"] and s["correct"].isna().all()
    s.loc[s["channel"] == "Chan A", "correct"] = "Đúng"  # Chan Shared not reviewed yet
    acc = classify.accuracy(s)
    assert acc.loc["all"].tolist() == [1, 100.0] and list(acc.index) == ["music", "all"]
