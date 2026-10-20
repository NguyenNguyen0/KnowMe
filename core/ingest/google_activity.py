"""Google My Activity products other than YouTube (Search, Translate, Maps, Image Search, ...).

Same JSON schema as YouTube's MyActivity.json. source = "google", action_type = product slug,
so the timeline shows google.search, google.translate, google.maps, ...
YouTube has its own parser; Ads and Takeout are skipped. Location fields are never stored.
"""

import json
import unicodedata
from pathlib import Path

import pandas as pd

from core.ingest.youtube import COLUMNS, json_files

ACTIVITY_ROOTS = ("My Activity/", "Hoạt động của tôi/")
SKIP = {"YouTube", "Ads", "Quảng cáo", "Takeout"}
# Product folder (localized) -> slug; anything else falls back to an ASCII slug of the folder name.
SLUGS = {
    "Search": "search",
    "Tìm kiếm": "search",
    "Google Translate": "translate",
    "Google Dịch": "translate",
    "Maps": "maps",
    "Image Search": "image_search",
    "Tìm kiếm hình ảnh": "image_search",
    "Video Search": "video_search",
    "Tìm kiếm video": "video_search",
    "Gemini Apps": "gemini",
    "Các ứng dụng Gemini": "gemini",
    "AI Mode": "ai_mode",
    "Chế độ AI": "ai_mode",
    "Google Play Store": "play_store",
    "Cửa hàng Google Play": "play_store",
    # VI names whose ASCII fallback would differ from the EN slug (Hotels -> hotels, Help -> help).
    "Khách sạn": "hotels",
    "Trợ giúp": "help",
}


def _product(name: str) -> str | None:
    """'…/My Activity/<product>/MyActivity.json' -> product folder, or None if not wanted."""
    if not name.endswith("/MyActivity.json"):
        return None
    for root in ACTIVITY_ROOTS:
        if root in name:
            product = name.split(root, 1)[1].rsplit("/", 1)[0]
            return None if product in SKIP or "/" in product else product
    return None


def _slug(product: str) -> str:
    if product in SLUGS:
        return SLUGS[product]
    ascii_ = unicodedata.normalize("NFKD", product).encode("ascii", "ignore").decode()
    return "_".join(ascii_.lower().split())


def parse(paths: list[Path], account: str) -> pd.DataFrame:
    rows = []
    for name, raw in json_files(paths, lambda n: _product(n) is not None):
        action_type = _slug(_product(name))
        for item in json.loads(raw):
            if "time" not in item:
                continue
            title = item.get("title", "")
            details = " ".join(d.get("name", "") for d in item.get("details") or [])
            meta = {
                "verb": title.split(" ", 1)[0],
                "url": item.get("titleUrl"),
                "is_ad": "Google Ads" in details,
            }
            rows.append(
                {
                    "timestamp": item["time"],
                    "source": "google",
                    "account": account,
                    "action_type": action_type,
                    "content": title,
                    "metadata": json.dumps(meta, ensure_ascii=False),
                }
            )
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    return df
