"""CLI adapter: `uv run python -m adapters.cli <command>`."""

import argparse
import itertools
from datetime import date
from functools import partial
from pathlib import Path

import pandas as pd

from core import classify, config, insight, store
from core.aggregate import common
from core.aggregate import youtube as yt
from core.ingest import facebook, google_activity, youtube
from core.llm import local_provider

# source -> (raw folder prefix, parser); folders are .raw/<prefix>-<account>/
SOURCES = {
    "youtube": ("google", youtube.parse),
    "google": ("google", google_activity.parse),  # My Activity products other than YouTube
    "facebook": ("facebook", facebook.parse),
}


NOTE = """---
type: report
created: {today}
project: "[[knowme-plan]]"
status: active
aliases: [KnowMe insight {question} {today}]
tags: [knowme, insight]
---

# KnowMe insight: {question} ({today})

> Sinh tự động bởi `just insight {question} --save` (model local). Tài khoản: {who}. Chỉ chứa số liệu tổng hợp.

## Câu trả lời của model

{answer}

## Số liệu đưa vào model

> [!note]- Prompt đầy đủ
{prompt}
"""


def save_note(inbox: Path, question: str, who: str, answer: str, prompt: str) -> Path:
    """Write an Obsidian note to the inbox; never overwrites (a note may already be edited)."""
    today = date.today().isoformat()
    quoted = "\n".join(f"> {line}".rstrip() for line in prompt.splitlines())
    body = NOTE.format(today=today, question=question, who=who, answer=answer.strip(), prompt=quoted)
    for i in itertools.count(1):
        path = inbox / f"{today}-knowme-insight-{question}{f'-{i}' if i > 1 else ''}.md"
        try:
            with path.open("x") as f:
                f.write(body)
            return path
        except FileExistsError:
            continue


def ingest(args):
    prefix, parse = SOURCES[args.source]
    raw = args.cfg.raw_dir
    dirs = [raw / f"{prefix}-{args.account}"] if args.account else sorted(raw.glob(f"{prefix}-*"))
    con = store.connect(args.db)
    for d in dirs:
        account = d.name.removeprefix(f"{prefix}-")
        df = parse(sorted(d.iterdir()), account)  # zips and/or extracted folders
        print(f"{account}: parsed {len(df)}, inserted {store.write_events(con, df)}")
        if df.empty and any(d.rglob("*.html")):
            print(f"  {d} has only HTML for this source: re-export it as JSON")


def aggregate(args):
    con = store.connect(args.db)
    kw = {"source": args.source}
    print(common.by_period(con, args.action, args.period, **kw).to_string(index=False))
    print()
    print(common.by_hour(con, args.action, **kw).to_string(index=False))


def timeline(args):
    con = store.connect(args.db)
    df = common.activity(con, args.account, args.period, args.since, args.until)
    print(df.to_string(index=False))


def ask(args):
    con = store.connect(args.db)
    account = None if args.question == "accounts" else need(args)
    labels = args.cfg.labels
    prompt = insight.build_prompt(con, args.question, account, args.date, labels)
    if args.show_prompt:
        print(prompt, end="\n\n---\n\n")
    answer = llm(args)(prompt)
    print(answer)
    if args.save:
        who = "tất cả" if account is None else f"{account} ({labels.get(account, 'chưa có nhãn')})"
        print(f"\nsaved: {save_note(args.cfg.inbox, args.question, who, answer, prompt)}")


def classify_channels(args):
    con = store.connect(args.db)
    for done, total in classify.classify(
        con, args.account, llm(args), args.limit, workers=args.workers
    ):
        print(f"\r{done}/{total} channels", end="", flush=True)
    n = con.execute("SELECT count(*) FROM channel_categories")  # shared by every account
    print(f"\nclassified in store: {n.fetchone()[0]}")


def classify_review(args):
    """First run writes a sample to mark by hand; later runs score it. The CSV stays in .raw/."""
    path = args.cfg.raw_dir / "review" / "classify-sample.csv"
    if path.exists():
        print(classify.accuracy(pd.read_csv(path)).to_string())
        return
    con = store.connect(args.db)
    df = classify.review_sample(con, args.n)
    df["samples"] = df["samples"].map(" | ".join)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"wrote {len(df)} channels to {path}\nfill `correct` (y/n) and, if wrong, `should_be`; then run again")


def top_channels(args):
    con = store.connect(args.db)
    df = yt.top_channels(con, args.account, args.since, args.until, args.new, args.limit)
    print(df.to_string(index=False))


def need(args) -> str:
    if args.account is None:
        raise SystemExit("this question needs one account: pass --account or list accounts in config.yaml")
    return args.account


def llm(args):
    return partial(local_provider.generate, base_url=args.cfg.llm.base_url, max_tokens=args.cfg.llm.max_tokens)


ACCOUNT = "default: first account in config.yaml; 'all' = every account"


def main():
    cfg = config.load()
    p = argparse.ArgumentParser(prog="knowme")
    p.add_argument("--db", type=Path, default=cfg.db)
    sub = p.add_subparsers(required=True)

    i = sub.add_parser("ingest", help="parse raw exports into DuckDB")
    i.add_argument("source", choices=list(SOURCES))
    i.add_argument("--account", help="default: every .raw/<prefix>-* folder of that source")
    i.set_defaults(all_by_default=True)
    i.set_defaults(func=ingest)

    a = sub.add_parser("aggregate", help="print basic stats")
    a.add_argument("--action", default="watch", help="watch, search, react, message, ...")
    a.add_argument("--source", choices=list(SOURCES))
    a.add_argument("--period", default="week", choices=["day", "week", "month"])
    a.set_defaults(func=aggregate)

    tl = sub.add_parser("timeline", help="event counts per period across all sources")
    tl.add_argument("--account", help=ACCOUNT)
    tl.add_argument("--period", default="day", choices=["day", "week", "month", "quarter"])
    tl.add_argument("--since", help="YYYY-MM-DD")
    tl.add_argument("--until", help="YYYY-MM-DD, exclusive")
    tl.set_defaults(func=timeline)

    q = sub.add_parser("insight", help="ask the local LLM one research question")
    q.add_argument("question", choices=list(insight.QUESTIONS))
    q.add_argument("--account", help="default: first account in config.yaml (ignored by 'accounts')")
    q.add_argument("--date", help="YYYY-MM-DD, for the 'around' question")
    q.add_argument("--show-prompt", action="store_true")
    q.add_argument("--save", action="store_true", help="also write the answer as a note to the vault inbox")
    q.set_defaults(func=ask)

    c = sub.add_parser("classify", help="topic per channel via the local LLM (resumable)")
    c.add_argument("--account", help=ACCOUNT)
    c.add_argument("--limit", type=int, default=1000, help="top N channels by watches")
    c.add_argument("--workers", type=int, default=cfg.llm.workers, help="parallel requests, <= server slots")
    c.set_defaults(func=classify_channels)

    r = sub.add_parser("classify-review", help="sample topics to check by hand, then score them")
    r.add_argument("--n", type=int, default=100)
    r.set_defaults(func=classify_review)

    t = sub.add_parser("top-channels", help="most watched channels in a window (local view only)")
    t.add_argument("--account", help=ACCOUNT)
    t.add_argument("--since", help="YYYY-MM-DD")
    t.add_argument("--until", help="YYYY-MM-DD, exclusive")
    t.add_argument("--new", action="store_true", help="only channels first seen in the window")
    t.add_argument("--limit", type=int, default=20)
    t.set_defaults(func=top_channels)

    args = p.parse_args()
    args.cfg = cfg
    if getattr(args, "account", None) == "all":
        args.account = None
    elif hasattr(args, "account") and args.account is None and not getattr(args, "all_by_default", False):
        args.account = cfg.default_account
    try:
        args.func(args)
    except ValueError as e:  # core rejects a request it cannot answer (missing date, too few accounts)
        raise SystemExit(f"error: {e}")


if __name__ == "__main__":
    main()
