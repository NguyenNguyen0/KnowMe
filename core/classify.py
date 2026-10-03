"""Topic per YouTube channel, classified by the LOCAL LLM (sees channel names + sample titles).

Raw content goes to the local model only. Results land in `channel_categories`;
re-running classifies only channels still missing, so it can be resumed.
"""

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pandas as pd

CATEGORIES = {
    "entertainment": "Giải trí (hài, phim, reaction, meme, drama)",
    "gaming": "Game",
    "music": "Âm nhạc",
    "learning": "Học tập, lập trình, công nghệ, khoa học, giáo dục",
    "news": "Tin tức, thời sự, xã hội",
    "lifestyle": "Đời sống, vlog, ẩm thực, du lịch, sức khoẻ",
    "sports": "Thể thao",
    "other": "Khác",
}
PROMPT = """Phân loại từng kênh YouTube dưới đây vào ĐÚNG MỘT nhóm, dựa vào tên kênh và vài tiêu đề video mẫu.
Các nhóm:
{categories}

Kênh:
{channels}

Trả lời mỗi kênh một dòng theo dạng "<số>: <mã nhóm>", ví dụ "1: music". Không giải thích."""
LINE = re.compile(r"^\s*(\d+)\s*[:.)\-]\s*([a-z]+)", re.MULTILINE)


def pending(con: duckdb.DuckDBPyConnection, account: str, limit: int) -> pd.DataFrame:
    """Top `limit` channels by watches that are not classified yet, with 3 sample titles each."""
    return con.execute(
        """
        WITH w AS (
            SELECT metadata->>'channel' AS channel, content FROM events
            WHERE account = $account AND action_type = 'watch'
              AND (metadata->>'is_ad') IS DISTINCT FROM 'true' AND (metadata->>'channel') IS NOT NULL
        ),
        top AS (SELECT channel, count(*) AS n FROM w GROUP BY channel ORDER BY n DESC, channel LIMIT $limit)
        SELECT top.channel, any_value(top.n) AS n, list(DISTINCT w.content)[1:3] AS samples
        FROM top JOIN w USING (channel)
        WHERE top.channel NOT IN (SELECT channel FROM channel_categories WHERE account = $account)
        GROUP BY top.channel ORDER BY n DESC, top.channel
        """,
        {"account": account, "limit": limit},
    ).df()


def build_prompt(batch: pd.DataFrame) -> str:
    channels = "\n".join(
        f"{i}. {r.channel} | ví dụ: " + "; ".join(t[:60] for t in r.samples)
        for i, r in enumerate(batch.itertuples(), 1)
    )
    categories = "\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items())
    return PROMPT.format(categories=categories, channels=channels)


def parse(answer: str, batch: pd.DataFrame) -> dict[str, str]:
    """Missing lines stay unclassified (retried next run); unknown codes become 'other'."""
    names = list(batch["channel"])
    out = {}
    for num, code in LINE.findall(answer):
        i = int(num) - 1
        if 0 <= i < len(names):
            out[names[i]] = code if code in CATEGORIES else "other"
    return out


def classify(
    con: duckdb.DuckDBPyConnection,
    account: str,
    generate: Callable[[str], str],
    limit: int = 1000,
    batch_size: int = 25,
    workers: int = 4,
) -> Iterator[tuple[int, int]]:
    """Classify pending channels; yields (done, total) after each batch for progress display."""
    todo = pending(con, account, limit)
    batches = [todo.iloc[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    done = 0
    with ThreadPoolExecutor(workers) as pool:  # llama-server serves parallel slots
        for batch, answer in zip(batches, pool.map(lambda b: generate(build_prompt(b)), batches)):
            labels = parse(answer, batch)
            if labels:
                con.executemany(
                    "INSERT OR IGNORE INTO channel_categories VALUES (?, ?, ?)",
                    [(account, ch, cat) for ch, cat in labels.items()],
                )
            done += len(batch)
            yield done, len(todo)
