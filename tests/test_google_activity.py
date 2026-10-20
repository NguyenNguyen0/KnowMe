import json
import zipfile

from core import store
from core.ingest import google_activity

# Synthetic data only.
SEARCH = [
    {"header": "Search", "title": "Searched for something", "titleUrl": "https://g/s",
     "time": "2025-03-09T02:00:00Z", "locationInfos": [{"name": "Somewhere", "url": "https://maps/x"}]},
    {"header": "Search", "title": "Visited ad", "time": "2025-03-09T02:01:00Z",
     "details": [{"name": "From Google Ads"}]},
]
TRANSLATE_VI = [{"header": "Google Dịch", "title": "Translated \"x\"", "time": "2025-03-09T03:00:00Z"},
                {"header": "Google Dịch", "title": "Đã truy cập ad", "time": "2025-03-09T03:01:00Z",
                 "details": [{"name": "Từ Quảng cáo trên Google"}]}]
OTHER = [{"header": "Hotels", "title": "Searched for hotels", "time": "2025-03-10T00:00:00Z"}]


def test_parse_products(tmp_path):
    root = tmp_path / "Takeout" / "My Activity"
    for product, items in {"Search": SEARCH, "YouTube": [{"title": "Watched v", "time": "2025-01-01T00:00:00Z"}],
                           "Ads": [{"title": "ad", "time": "2025-01-01T00:00:00Z"}], "Hotels": OTHER}.items():
        (root / product).mkdir(parents=True)
        (root / product / "MyActivity.json").write_text(json.dumps(items))
    zp = tmp_path / "t.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Takeout/Hoạt động của tôi/Google Dịch/MyActivity.json", json.dumps(TRANSLATE_VI))

    df = google_activity.parse([tmp_path / "Takeout", zp], "acc")
    assert sorted(df["action_type"]) == ["hotels", "search", "search", "translate", "translate"]  # YouTube/Ads skipped
    assert set(df["source"]) == {"google"}
    meta = [json.loads(m) for m in df["metadata"]]
    assert sum(m["is_ad"] for m in meta) == 2 and {m["verb"] for m in meta} >= {"Searched", "Translated"}
    assert "Somewhere" not in df.to_csv()  # location never stored

    con = store.connect(tmp_path / "events.duckdb")
    assert store.write_events(con, df) == 5
    assert store.write_events(con, df) == 0


# Product folders seen in an EN and a VI export: the same product must get the same slug,
# or comparing accounts splits one product into two kinds.
EN_VI = {
    "Search": "Tìm kiếm",
    "Image Search": "Tìm kiếm hình ảnh",
    "Video Search": "Tìm kiếm video",
    "Gemini Apps": "Các ứng dụng Gemini",
    "AI Mode": "Chế độ AI",
    "Google Play Store": "Cửa hàng Google Play",
    "Google Translate": "Google Dịch",
    "Hotels": "Khách sạn",
    "Help": "Trợ giúp",
    "Maps": "Maps",
    "Gmail": "Gmail",
    "Developers": "Developers",
    "Google Analytics": "Google Analytics",
}


def test_slug_same_in_en_and_vi():
    for en, vi in EN_VI.items():
        assert google_activity._slug(en) == google_activity._slug(vi), (en, vi)
