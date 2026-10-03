"""Facebook "Download Your Information" export (JSON format, extracted folder).

Facebook as a context source: settings, sessions, reactions, comments, posts, searches.
Messages are counted only (time + sent/received); their text and participants are never stored.
"""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath

import pandas as pd

from core.ingest.youtube import COLUMNS

# (path pattern matched from the right, action_type)
RULES = [
    ("comments_and_reactions/likes_and_reactions*.json", "react"),
    ("comments_and_reactions/comments.json", "comment"),
    ("groups/your_comments_in_groups.json", "comment"),
    ("posts/your_posts__check_ins__photos_and_videos_*.json", "post"),
    ("interactions/groups_and_events_you've_visited.json", "visit"),
    ("search/your_search_history.json", "search"),
    ("preferences/video.json", "feedback"),
    ("preferences/your_daily_limit.json", "setting"),
    ("security_and_login_information/logins_and_logouts.json", "login"),
    ("security_and_login_information/account_activity.json", "session"),
]
MESSAGES = "messages/*/*/message_*.json"


def _fix(o):
    """Facebook writes UTF-8 bytes as if they were latin-1 ("Giá»\x9bi háº¡n")."""
    if isinstance(o, str):
        try:
            return o.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return o
    if isinstance(o, list):
        return [_fix(x) for x in o]
    if isinstance(o, dict):
        return {_fix(k): _fix(v) for k, v in o.items()}
    return o


def _load(f: Path):
    return _fix(json.loads(f.read_bytes()))


def _items(obj) -> list[dict]:
    """Records live in a top-level list, under a single `*_v2`-style key, or are the object itself."""
    if isinstance(obj, dict):
        lists = [v for v in obj.values() if isinstance(v, list)]
        obj = lists[0] if len(obj) == 1 and lists else [obj]
    return [r for r in obj if isinstance(r, dict) and isinstance(r.get("timestamp"), int)]


def _device(ua: str | None) -> str | None:
    if not ua:
        return None
    if m := re.search(r"Android [\d.]+; ([^;)]+?)(?: Build|\))", ua):
        return f"Android {m.group(1).strip()}"
    return next((k for k in ("iPhone", "iPad", "Windows", "Macintosh", "Linux") if k in ua), "other")


def _content(r: dict) -> str:
    texts = [d["text"] for d in r.get("data") or [] if isinstance(d, dict) and "text" in d]
    if texts:  # search history: the query, not the generic title
        return " ".join(texts)
    if "title" in r:
        return r["title"]
    if "action" in r:  # logins, account activity
        return r["action"]
    values = [lv["value"] for lv in r.get("label_values") or [] if "value" in lv and lv.get("label") != "URL"]
    return " | ".join(values)


def _metadata(r: dict) -> dict:
    reactions = [d["reaction"]["reaction"] for d in r.get("data") or [] if isinstance(d, dict) and "reaction" in d]
    labels = {lv.get("label"): lv.get("value") for lv in r.get("label_values") or []}
    meta = {
        "reaction": (reactions or [labels.get("Cảm xúc")])[0],
        "device": _device(r.get("user_agent")),  # never IP, cookie or location
    }
    return {k: v for k, v in meta.items() if v}


def _row(ts: float, account: str, action_type: str, content: str, meta: dict) -> dict:
    return {
        "timestamp": pd.Timestamp(ts, unit="s", tz="UTC"),
        "source": "facebook",
        "account": account,
        "action_type": action_type,
        "content": content,
        "metadata": json.dumps(meta, ensure_ascii=False),
    }


def _messages(files: list[Path], account: str) -> list[dict]:
    threads = [_load(f) for f in files]
    # The owner is the participant present in the most threads.
    seen = Counter(p["name"] for t in threads for p in {p["name"]: p for p in t.get("participants", [])}.values())
    owner = seen.most_common(1)[0][0] if seen else None
    rows = []
    for f, t in zip(files, threads):
        for m in t.get("messages", []):
            # content is a short hash only so the store key stays unique per message; no text kept.
            key = hashlib.sha1(f"{f.parent.name}|{m['timestamp_ms']}|{m.get('sender_name')}".encode()).hexdigest()
            meta = {"sent": m.get("sender_name") == owner, "folder": f.parent.parent.name}
            rows.append(_row(m["timestamp_ms"] / 1000, account, "message", key[:12], meta))
    return rows


def parse(paths: list[Path], account: str) -> pd.DataFrame:
    """paths: the extracted export's top-level folders (zips not supported yet)."""
    rows, message_files = [], []
    for p in paths:
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.json")):
            rel = PurePosixPath(f.as_posix())
            if rel.match(MESSAGES):
                message_files.append(f)
                continue
            action_type = next((a for pattern, a in RULES if rel.match(pattern)), None)
            if action_type:
                rows += [
                    _row(r["timestamp"], account, action_type, _content(r), _metadata(r))
                    for r in _items(_load(f))
                ]
    rows += _messages(message_files, account)
    return pd.DataFrame(rows, columns=COLUMNS)
