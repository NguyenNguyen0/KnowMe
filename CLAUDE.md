# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

KnowMe: export personal data from platforms (LinkedIn, YouTube, Facebook, ...), normalize it into one schema, store it in DuckDB, aggregate with code, then have an LLM interpret the aggregates as a data analyst.

Plan, architecture and changelog live in the Obsidian vault, not in this repo: `~/Documents/Obsidian Vault/30-Projects/KnowMe/` (`knowme-plan.md`, `knowme-architecture.md`, `knowme-changelog.md`). Read them for version scope before adding features. Current version: v0.3.0.

## Rules

- **Reports go to the vault inbox.** Every report (data review, spike result, analysis, version summary) is an Obsidian note written to `~/Documents/Obsidian Vault/00-Inbox/`, following the vault's `AGENTS.md` (file name `yyyy-mm-dd-<topic>.md` kebab-case, frontmatter with `type`, `created`, `project: "[[knowme-plan]]"`, `aliases` with the Vietnamese title, content in Vietnamese with diacritics). The user reviews them and has them filed into `30-Projects/KnowMe/Reports/`. Do not edit `30-Projects/KnowMe/` notes unless asked; propose changes inside the report.
- **Personal data stays in `.raw/` (gitignored).** Never put real export content (video titles, search terms, file names, emails, account data) into code, tests, docs, commit messages or reports. Reports contain counts/aggregates only. Tests use synthetic fixtures.

## Commands

`justfile` recipes wrap the CLI adapter (`uv run python -m adapters.cli ...`). `--account` defaults to the first account in `config.yaml`; `--account all` = every account (timeline, classify, top-channels).

```sh
just sync                                    # uv sync
just test                                    # all tests
just test tests/test_insight.py::test_nightly_and_prompt   # single test
just ingest youtube|google|facebook [--account <account>]   # google = My Activity products other than YouTube
just refresh                                 # ingest every source for every account (after a new export)
just timeline [--account <account>|all] [--period day|week|month|quarter] [--since D] [--until D]  # all sources side by side
just aggregate [--action watch|search|react|message...] [--source youtube|facebook] [--period day|week|month]
just classify [--account <account>|all] [--limit 1000] [--workers 2]  # topic per channel, local LLM, resumable
just classify-review                         # 1st run: .raw/review/classify-sample.csv to mark by hand; 2nd run: accuracy
just top-channels [--account <account>|all] [--since D] [--until D] [--new]  # prints channel names: local use only
just insight sleep|trend|habit|topics [--account <account>] [--show-prompt] [--save]   # needs llama-server on :8080
just insight around [--account <account>] --date YYYY-MM-DD   # cross-source changes around a date
just insight accounts                        # compares every account (needs YouTube/Google data for 2+)
just report                                  # every question with --save: notes in the vault inbox
```

`--save` writes the answer plus the full prompt (aggregates only) as an Obsidian note to `inbox` (default `~/Documents/Obsidian Vault/00-Inbox/`), never overwriting an existing note.

## Config

`config.yaml` (gitignored: account ids are personal data; template `config.example.yaml`, loaded by `core/config.py`, Pydantic, unknown keys rejected): `accounts` (`id` = the `<account>` of `.raw/<source>-<account>/`, `label` = what the account is used for, given to the LLM), paths (`raw_dir`, `db`, `inbox`), `llm` (`base_url`, `workers`, `max_tokens`). Without the file everything runs on defaults and `--account` must be passed. Timezone stays a constant (`core/aggregate/common.py`).

`llama-server` runs as the `llama-cpp` package's systemd user unit: `systemctl --user start llama-server` (not enabled at login). Its settings are env vars in `~/.config/llama/server/environment.conf` (outside this repo): `LLAMA_ARG_HF_REPO=unsloth/Qwen3.5-4B-GGUF:Q4_K_M`, `LLAMA_ARG_CTX_SIZE=16384`, `LLAMA_ARG_N_PARALLEL=2` (2 slots x 8192), `LLAMA_ARG_MMPROJ_AUTO=false`, `LLAMA_ARG_CHAT_TEMPLATE_KWARGS='{"enable_thinking":false}'`. GPU layers stay on auto: `-ngl 99` ran out of Vulkan memory. Keep `llm.workers` <= slots (4 parallel requests crashed it once).

Inspect the store from Python (`uv run python`, `duckdb.connect(".raw/processed/events.duckdb", read_only=True)`): the system `duckdb` CLI lacks ICU, so `TIMESTAMPTZ` casts and `timezone()` fail there.

## Data layout

- `.raw/google-<account>/`: Google Takeout exports, as zips or extracted `Takeout*/` folders (ingest reads both; overlapping exports dedupe in the store). The account id is taken from the folder name.
- `.raw/facebook-<account>/`: Facebook "Download Your Information" JSON, extracted (zips not supported). Same account id as the Google folder of the same person, so sources join on `account`.
- `.raw/processed/events.duckdb`: Analytics Store, tables `events` and `channel_categories`.
- Google My Activity products other than YouTube (Search, Translate, Maps, Image Search, Chrome, Gemini, ...) go in as `source = google`, `action_type` = product slug (`core/ingest/google_activity.py`); Ads and Takeout are skipped, `locationInfos` never stored. Ads are flagged from `details[].name` (EN "From Google Ads", VI "Từ Quảng cáo trên Google": `AD_MARKERS` in `core/ingest/youtube.py`).
- Takeout must be exported in **JSON** (not the default HTML). Full YouTube history only comes from the **My Activity** product (`My Activity/YouTube/MyActivity.json`, all time); the "YouTube and YouTube Music" history files hold only the last few months.
- Facebook JSON stores every non-ASCII string as latin-1 mojibake; `core/ingest/facebook.py` fixes it on load. Messages are stored as count-only events (time + sent flag, `content` is a hash): never ingest message text, participant names, IPs, cookies or locations.
- Files and titles are localized to the account's UI language (one account is English, one Vietnamese). `action_type` is derived from the title prefix: see `FILES`/`ACTIVITY_DIRS`/`PREFIXES` in `core/ingest/youtube.py`.

## Architecture

Ports & adapters. `core/` is pure logic: it takes and returns DataFrames/structured data and never prints, reads argv or formats output. `adapters/cli` (later `adapters/api` FastAPI, optional `adapters/tui`) are thin layers that call `core/` and only handle I/O and display. A new UI is a new adapter, never a change to core.

Flow: `core/ingest/<source>.py` parses raw exports into the normalized schema (`timestamp` UTC, `source`, `account`, `action_type`, `content`, `metadata` JSON) → `core/store.py` writes to DuckDB idempotently (PK `timestamp, source, account, action_type, content`, `INSERT OR IGNORE`, so re-ingesting the same export adds nothing) → `core/aggregate/` computes stats in SQL (local time `Asia/Ho_Chi_Minh`, ads excluded through `metadata.is_ad`) → `core/classify.py` labels top channels with a topic via the local LLM (table `channel_categories`, keyed by channel and shared by every account; `store.connect` migrates v0.2 stores) → `core/insight.py` maps each research question to precomputed tables plus code-computed facts (max/min/biggest change) and builds the prompt → `core/llm/local_provider.py` (Qwen via local `llama-server`; Claude Code from v0.5.0, only when picked explicitly). The LLM gets **only aggregates, never raw events**: code computes numbers, the LLM interprets. Cut incomplete trailing periods, precompute comparisons and keep tables narrow (only the columns the question needs) in code; the 4B model misreads partial months, makes comparison errors and mixes up columns of wide tables even when the prompt warns it.
