import json

from core import store
from core.ingest import facebook

# Synthetic data only. Facebook stores UTF-8 text as latin-1 mojibake; reproduce that.
moji = lambda s: s.encode("utf-8").decode("latin-1")  # noqa: E731


def write(root, rel, obj):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(obj))


def make_export(tmp_path):
    root = tmp_path / "facebook-acc"
    act = root / "your_facebook_activity"
    write(act, "comments_and_reactions/likes_and_reactions.json", [  # new label_values format
        {"timestamp": 1743000000, "label_values": [
            {"label": moji("Cảm xúc"), "value": "LIKE"},
            {"label": "URL", "value": "https://example.com/x"},
            {"label": moji("Tiêu đề"), "value": moji("Bài viết A")},
        ]},
    ])
    write(act, "comments_and_reactions/likes_and_reactions_1.json", [  # old format
        {"timestamp": 1743000100, "title": moji("Đã thích B"),
         "data": [{"reaction": {"reaction": "HAHA", "actor": "Owner"}}]},
    ])
    write(act, "comments_and_reactions/comments.json",
          {"comments_v2": [{"timestamp": 1743000200, "title": "commented"}]})
    write(root, "logged_information/search/your_search_history.json",
          {"searches_v2": [{"timestamp": 1743000300, "title": "generic", "data": [{"text": moji("từ khoá")}]}]})
    write(root, "security_and_login_information/account_activity.json",
          {"account_activity_v2": [{"action": "Login", "timestamp": 1743000400, "ip_address": "10.1.2.3",
                                    "user_agent": "Mozilla/5.0 (Linux; Android 10; SM-X100 Build/QP1A)"}]})
    write(root, "preferences/preferences/your_daily_limit.json",  # single record, not a list
          {"timestamp": 1742947200, "label_values": [{"label": "x", "value": moji("Nhắc đóng Facebook")}]})
    write(root, "ads_information/ads_viewed.json", [{"timestamp": 1743000500, "title": "ignored"}])
    thread = lambda msgs, people: {"participants": [{"name": p} for p in people], "messages": msgs}  # noqa: E731
    write(act, "messages/inbox/t1/message_1.json", thread(
        [{"sender_name": "Owner", "timestamp_ms": 1743000000000, "content": "secret 1"},
         {"sender_name": "Friend", "timestamp_ms": 1743000000000, "content": "secret 2"}],
        ["Owner", "Friend"]))
    write(act, "messages/archived_threads/t2/message_1.json", thread(
        [{"sender_name": "Other", "timestamp_ms": 1743000001000, "content": "secret 3"}], ["Owner", "Other"]))
    return sorted(root.iterdir())


def test_parse_and_store(tmp_path):
    df = facebook.parse(make_export(tmp_path), "acc")
    by = dict(zip(df["action_type"], df["content"]))
    assert sorted(df["action_type"]) == (
        ["comment", "message", "message", "message", "react", "react", "search", "session", "setting"]
    )
    assert set(df[df.action_type == "react"]["content"]) == {"LIKE | Bài viết A", "Đã thích B"}  # fixed + URL dropped
    assert by["search"] == "từ khoá" and by["setting"] == "Nhắc đóng Facebook" and by["session"] == "Login"
    assert str(df["timestamp"].dt.tz) == "UTC"

    meta = [json.loads(m) for m in df["metadata"]]
    assert {m.get("reaction") for m in meta} >= {"LIKE", "HAHA"}
    assert any(m.get("device") == "Android SM-X100" for m in meta)
    msgs = df[df.action_type == "message"]
    assert sorted(json.loads(m)["sent"] for m in msgs["metadata"]) == [False, False, True]
    raw = df.to_csv()
    assert "10.1.2.3" not in raw and "secret" not in raw and "Friend" not in raw  # no IP, text or names

    con = store.connect(tmp_path / "events.duckdb")
    assert store.write_events(con, df) == 9  # two messages in the same ms stay distinct
    assert store.write_events(con, df) == 0


def test_cross_source_timeline(tmp_path):
    from core.aggregate import common

    con = store.connect(tmp_path / "events.duckdb")
    store.write_events(con, facebook.parse(make_export(tmp_path), "acc"))
    t = common.activity(con, "acc", "month").set_index("period")
    assert t.loc["2025-03-01", "facebook.setting"] == 1
    assert t.loc["2025-03-01", "facebook.message"] == 3
    assert t.loc["2025-03-01", "facebook.react"] == 2
