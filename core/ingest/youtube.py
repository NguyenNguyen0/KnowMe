"""YouTube activity from a Google Takeout export (JSON format).

Reads both "YouTube and YouTube Music/history/*.json" (only recent months) and
"My Activity/YouTube/MyActivity.json" (full history). They overlap ~98%; the
store's key dedupes the overlap.
"""

import json
import unicodedata
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pandas as pd

# Takeout localizes file names and titles to the account's UI language.
FILES = {
    "watch-history.json",
    "search-history.json",
    "nhật ký xem.json",
    "lịch sử tìm kiếm.json",
}
ACTIVITY_DIRS = {"My Activity/YouTube", "Hoạt động của tôi/YouTube"}
PREFIXES = {
    "Watched ": "watch",
    "Viewed ": "watch",
    "Searched for ": "search",
    "Liked ": "like",
    "Subscribed to ": "subscribe",
    "Đã xem ": "watch",
    "Đã tìm kiếm ": "search",
}
COLUMNS = ["timestamp", "source", "account", "action_type", "content", "metadata"]
# details[].name of an ad entry: EN "From Google Ads", VI "Từ Quảng cáo trên Google".
AD_MARKERS = ("Google Ads", "Quảng cáo trên Google")


def is_ad(item: dict) -> bool:
    details = " ".join(d.get("name", "") for d in item.get("details") or [])
    return any(m in details for m in AD_MARKERS)


def _wanted(name: str) -> bool:
    parent, _, file = name.rpartition("/")
    if file == "MyActivity.json":
        return any(parent.endswith(d) for d in ACTIVITY_DIRS)
    return "YouTube" in parent and file in FILES


def json_files(
    paths: list[Path], wanted: Callable[[str], bool]
) -> Iterator[tuple[str, bytes]]:
    """Yield (NFC path, bytes) of wanted JSON files from Takeout zips or extracted folders."""
    for p in paths:
        if p.is_dir():
            for f in p.rglob("*.json"):
                name = unicodedata.normalize("NFC", f.as_posix())
                if wanted(name):
                    yield name, f.read_bytes()
        # ponytail: silently skips corrupt/empty zips (e.g. 0-byte partial downloads)
        elif zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                for raw in z.namelist():
                    name = unicodedata.normalize("NFC", raw)
                    if wanted(name):
                        yield name, z.read(raw)


def _action(title: str) -> tuple[str, str]:
    for prefix, action in PREFIXES.items():
        if title.startswith(prefix):
            return action, title[len(prefix):]
    return "other", title


def _record(item: dict, account: str) -> dict:
    action_type, content = _action(item.get("title", ""))
    channel = (item.get("subtitles") or [{}])[0]
    return {
        "timestamp": item["time"],
        "source": "youtube",
        "account": account,
        "action_type": action_type,
        "content": content,
        "metadata": json.dumps(
            {
                "url": item.get("titleUrl"),
                "channel": channel.get("name"),
                "channel_url": channel.get("url"),
                "is_ad": is_ad(item),
            },
            ensure_ascii=False,
        ),
    }


def parse(paths: list[Path], account: str) -> pd.DataFrame:
    rows = [
        _record(item, account)
        for _, raw in json_files(paths, _wanted)
        for item in json.loads(raw)
        if "time" in item
    ]
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="ISO8601")
    return df
