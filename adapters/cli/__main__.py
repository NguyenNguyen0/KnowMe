"""CLI adapter: `uv run python -m adapters.cli <command>`."""

import argparse
from pathlib import Path

from core import classify, insight, store
from core.aggregate import common
from core.aggregate import youtube as yt
from core.ingest import facebook, google_activity, youtube
from core.llm import local_provider

RAW = Path(".raw")
DB = RAW / "processed" / "events.duckdb"
# source -> (raw folder prefix, parser); folders are .raw/<prefix>-<account>/
SOURCES = {
    "youtube": ("google", youtube.parse),
    "google": ("google", google_activity.parse),  # My Activity products other than YouTube
    "facebook": ("facebook", facebook.parse),
}


def ingest(args):
    prefix, parse = SOURCES[args.source]
    dirs = [RAW / f"{prefix}-{args.account}"] if args.account else sorted(RAW.glob(f"{prefix}-*"))
    con = store.connect(args.db)
    for d in dirs:
        account = d.name.removeprefix(f"{prefix}-")
        df = parse(sorted(d.iterdir()), account)  # zips and/or extracted folders
        print(f"{account}: parsed {len(df)}, inserted {store.write_events(con, df)}")


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
    prompt = insight.build_prompt(con, args.question, args.account, args.date)
    if args.show_prompt:
        print(prompt, end="\n\n---\n\n")
    print(local_provider.generate(prompt))


def classify_channels(args):
    con = store.connect(args.db)
    for done, total in classify.classify(
        con, args.account, local_provider.generate, args.limit, workers=args.workers
    ):
        print(f"\r{done}/{total} channels", end="", flush=True)
    n = con.execute("SELECT count(*) FROM channel_categories WHERE account = ?", [args.account])
    print(f"\nclassified in store: {n.fetchone()[0]}")


def top_channels(args):
    con = store.connect(args.db)
    df = yt.top_channels(con, args.account, args.since, args.until, args.new, args.limit)
    print(df.to_string(index=False))


def main():
    p = argparse.ArgumentParser(prog="knowme")
    p.add_argument("--db", type=Path, default=DB)
    sub = p.add_subparsers(required=True)

    i = sub.add_parser("ingest", help="parse raw exports into DuckDB")
    i.add_argument("source", choices=list(SOURCES))
    i.add_argument("--account", help="default: every .raw/<prefix>-* folder of that source")
    i.set_defaults(func=ingest)

    a = sub.add_parser("aggregate", help="print basic stats")
    a.add_argument("--action", default="watch", help="watch, search, react, message, ...")
    a.add_argument("--source", choices=list(SOURCES))
    a.add_argument("--period", default="week", choices=["day", "week", "month"])
    a.set_defaults(func=aggregate)

    tl = sub.add_parser("timeline", help="event counts per period across all sources")
    tl.add_argument("--account", required=True)
    tl.add_argument("--period", default="day", choices=["day", "week", "month", "quarter"])
    tl.add_argument("--since", help="YYYY-MM-DD")
    tl.add_argument("--until", help="YYYY-MM-DD, exclusive")
    tl.set_defaults(func=timeline)

    q = sub.add_parser("insight", help="ask the local LLM one research question")
    q.add_argument("question", choices=list(insight.QUESTIONS))
    q.add_argument("--account", required=True)
    q.add_argument("--date", help="YYYY-MM-DD, for the 'around' question")
    q.add_argument("--show-prompt", action="store_true")
    q.set_defaults(func=ask)

    c = sub.add_parser("classify", help="topic per channel via the local LLM (resumable)")
    c.add_argument("--account", required=True)
    c.add_argument("--limit", type=int, default=1000, help="top N channels by watches")
    c.add_argument("--workers", type=int, default=2, help="parallel requests (server crashed at 4)")
    c.set_defaults(func=classify_channels)

    t = sub.add_parser("top-channels", help="most watched channels in a window (local view only)")
    t.add_argument("--account", required=True)
    t.add_argument("--since", help="YYYY-MM-DD")
    t.add_argument("--until", help="YYYY-MM-DD, exclusive")
    t.add_argument("--new", action="store_true", help="only channels first seen in the window")
    t.add_argument("--limit", type=int, default=20)
    t.set_defaults(func=top_channels)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
