# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

KnowMe: export personal data from platforms (LinkedIn, YouTube, Facebook, ...), normalize it into one schema, store it in DuckDB, aggregate with code, then have an LLM interpret the aggregates as a data analyst.

Plan, architecture and changelog live in the Obsidian vault, not in this repo: `~/Documents/Obsidian Vault/30-Projects/KnowMe/` (`knowme-plan.md`, `knowme-architecture.md`, `knowme-changelog.md`). Read them for version scope before adding features. Current version: v0.2.0.

## Rules

- **Reports go to the vault inbox.** Every report (data review, spike result, analysis, version summary) is an Obsidian note written to `~/Documents/Obsidian Vault/00-Inbox/`, following the vault's `AGENTS.md` (file name `yyyy-mm-dd-<topic>.md` kebab-case, frontmatter with `type`, `created`, `project: "[[knowme-plan]]"`, `aliases` with the Vietnamese title, content in Vietnamese with diacritics). The user reviews them and has them filed into `30-Projects/KnowMe/Reports/`. Do not edit `30-Projects/KnowMe/` notes unless asked; propose changes inside the report.
- **Personal data stays in `.raw/` (gitignored).** Never put real export content (video titles, search terms, file names, emails, account data) into code, tests, docs, commit messages or reports. Reports contain counts/aggregates only. Tests use synthetic fixtures.

## Commands

`justfile` recipes wrap the CLI adapter (`uv run python -m adapters.cli ...`):

```sh
just sync                                    # uv sync
just test                                    # all tests
just test tests/test_insight.py::test_nightly_and_prompt   # single test
just ingest youtube|google|facebook [--account <account>]   # google = My Activity products other than YouTube
just timeline --account <account> [--period day|week|month|quarter] [--since D] [--until D]  # all sources side by side
just aggregate [--action watch|search|react|message...] [--source youtube|facebook] [--period day|week|month]
just classify --account <account> [--limit 1000] [--workers 2]  # topic per channel, local LLM, resumable
just top-channels --account <account> [--since D] [--until D] [--new]  # prints channel names: local use only
just insight sleep|trend|habit|topics --account <account> [--show-prompt]   # needs llama-server on :8080
just insight around --account <account> --date YYYY-MM-DD   # cross-source changes around a date
```

`llama-server` (Qwen3.5-4B, 4 slots, ctx 8192) is started by the user, not by this repo. It crashed once under 4 parallel requests; keep `--workers 2`.

Inspect the store from Python (`uv run python`, `duckdb.connect(".raw/processed/events.duckdb", read_only=True)`): the system `duckdb` CLI lacks ICU, so `TIMESTAMPTZ` casts and `timezone()` fail there.

## Data layout

- `.raw/google-<account>/`: Google Takeout exports, as zips or extracted `Takeout*/` folders (ingest reads both; overlapping exports dedupe in the store). The account id is taken from the folder name.
- `.raw/facebook-<account>/`: Facebook "Download Your Information" JSON, extracted (zips not supported). Same account id as the Google folder of the same person, so sources join on `account`.
- `.raw/processed/events.duckdb`: Analytics Store, tables `events` and `channel_categories`.
- Google My Activity products other than YouTube (Search, Translate, Maps, Image Search, Chrome, Gemini, ...) go in as `source = google`, `action_type` = product slug (`core/ingest/google_activity.py`); Ads and Takeout are skipped, `locationInfos` never stored.
- Takeout must be exported in **JSON** (not the default HTML). Full YouTube history only comes from the **My Activity** product (`My Activity/YouTube/MyActivity.json`, all time); the "YouTube and YouTube Music" history files hold only the last few months.
- Facebook JSON stores every non-ASCII string as latin-1 mojibake; `core/ingest/facebook.py` fixes it on load. Messages are stored as count-only events (time + sent flag, `content` is a hash): never ingest message text, participant names, IPs, cookies or locations.
- Files and titles are localized to the account's UI language (one account is English, one Vietnamese). `action_type` is derived from the title prefix: see `FILES`/`ACTIVITY_DIRS`/`PREFIXES` in `core/ingest/youtube.py`.

## Architecture

Ports & adapters. `core/` is pure logic: it takes and returns DataFrames/structured data and never prints, reads argv or formats output. `adapters/cli` (later `adapters/api` FastAPI, optional `adapters/tui`) are thin layers that call `core/` and only handle I/O and display. A new UI is a new adapter, never a change to core.

Flow: `core/ingest/<source>.py` parses raw exports into the normalized schema (`timestamp` UTC, `source`, `account`, `action_type`, `content`, `metadata` JSON) → `core/store.py` writes to DuckDB idempotently (PK `timestamp, source, account, action_type, content`, `INSERT OR IGNORE`, so re-ingesting the same export adds nothing) → `core/aggregate/` computes stats in SQL (local time `Asia/Ho_Chi_Minh`, ads excluded through `metadata.is_ad`) → `core/classify.py` labels top channels with a topic via the local LLM (table `channel_categories`) → `core/insight.py` maps each research question to precomputed tables plus code-computed facts (max/min/biggest change) and builds the prompt → `core/llm/local_provider.py` (Qwen via local `llama-server`; Claude Code from v0.5.0, only when picked explicitly). The LLM gets **only aggregates, never raw events**: code computes numbers, the LLM interprets. Cut incomplete trailing periods, precompute comparisons and keep tables narrow (only the columns the question needs) in code; the 4B model misreads partial months, makes comparison errors and mixes up columns of wide tables even when the prompt warns it.
