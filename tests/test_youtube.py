import json
import zipfile

from core import store
from core.aggregate import common
from core.ingest import youtube

# Synthetic data only — never real export content.
WATCH = [
    {
        "header": "YouTube",
        "title": "Watched Video A",
        "titleUrl": "https://www.youtube.com/watch?v=a",
        "subtitles": [{"name": "Channel A", "url": "https://www.youtube.com/channel/a"}],
        "time": "2026-09-01T15:30:00.123Z",
    },
    {
        "header": "YouTube",
        "title": "Watched Ad B",
        "time": "2026-09-01T16:00:00Z",
        "details": [{"name": "From Google Ads"}],
    },
    {"header": "YouTube", "title": "Watched Video A", "time": "2026-09-01T15:30:00.123Z"},
    {"header": "YouTube", "title": "Watched Video C", "time": "2026-09-01T15:30:00.123Z"},
]
SEARCH_VI = [{"header": "YouTube", "title": "Đã tìm kiếm từ khoá", "time": "2026-09-02T01:00:00Z"}]
# My Activity overlaps the history files; Video A here must dedupe against WATCH.
ACTIVITY = [
    {"header": "YouTube", "title": "Watched Video A", "time": "2026-09-01T15:30:00.123Z"},
    {"header": "YouTube", "title": "Liked Video D", "time": "2020-01-01T00:00:00Z"},
    {"header": "YouTube", "title": "Dismissed something", "time": "2020-01-02T00:00:00Z"},
]


def make_zip(tmp_path):
    zp = tmp_path / "takeout.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Takeout/YouTube and YouTube Music/history/watch-history.json", json.dumps(WATCH))
        z.writestr("Takeout/YouTube và YouTube Music/lịch sử/lịch sử tìm kiếm.json", json.dumps(SEARCH_VI))
        z.writestr("Takeout/Drive/watch-history.json", "not youtube, must be skipped")
    (tmp_path / "empty.zip").touch()
    extracted = tmp_path / "Takeout"
    (extracted / "My Activity" / "YouTube").mkdir(parents=True)
    (extracted / "My Activity" / "YouTube" / "MyActivity.json").write_text(json.dumps(ACTIVITY))
    (extracted / "My Activity" / "Maps").mkdir()
    (extracted / "My Activity" / "Maps" / "MyActivity.json").write_text("not youtube, must be skipped")
    return [zp, tmp_path / "empty.zip", extracted]


def test_parse_and_store(tmp_path):
    df = youtube.parse(make_zip(tmp_path), "acc")
    assert list(df.columns) == youtube.COLUMNS
    assert sorted(df["action_type"]) == ["like", "other", "search"] + ["watch"] * 5
    assert set(df["content"]) == {"Video A", "Ad B", "Video C", "từ khoá", "Video D", "Dismissed something"}
    assert str(df["timestamp"].dt.tz) == "UTC"
    ads = [json.loads(m)["is_ad"] for m in df["metadata"]]
    assert ads.count(True) == 1

    con = store.connect(tmp_path / "db" / "events.duckdb")
    # duplicates of Video A collapsed; Video C kept despite same timestamp as Video A
    assert store.write_events(con, df) == 6
    assert store.write_events(con, df) == 0  # idempotent

    hours = common.by_hour(con)  # ad excluded, 15:30Z -> 22h local
    assert hours[["hour", "n"]].values.tolist() == [[22, 2]]
    weeks = common.by_period(con, "search", "day")
    assert weeks["n"].tolist() == [1]
