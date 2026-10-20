# KnowMe entry points. Each recipe only calls the CLI adapter.
# --account defaults to the first account in config.yaml; 'all' = every account.

cli := "uv run python -m adapters.cli"

default:
    @just --list

sync:
    uv sync

test *args:
    uv run pytest {{args}}

# just ingest youtube|google|facebook [--account ID]   (default: every .raw/<prefix>-* folder)
ingest source="youtube" *args:
    {{cli}} ingest {{source}} {{args}}

# Ingest every source for every account; run after dropping a new export into .raw/
refresh:
    {{cli}} ingest youtube
    {{cli}} ingest google
    {{cli}} ingest facebook

# just aggregate [--action watch|search] [--period day|week|month]
aggregate *args:
    {{cli}} aggregate {{args}}

# just insight sleep|trend|habit|topics|accounts|around [--account ID] [--date D] [--show-prompt] [--save]
insight question *args:
    {{cli}} insight {{question}} {{args}}

# Every research question as an Obsidian note in the vault inbox (needs llama-server)
report *args:
    for q in sleep trend habit topics; do {{cli}} insight $q --save {{args}}; done
    -{{cli}} insight accounts --save

# just classify [--account ID|all] [--limit 1000] [--workers N]   (local LLM, resumable)
classify *args:
    {{cli}} classify {{args}}

# just top-channels [--account ID|all] [--since D] [--until D] [--new] [--limit N]
top-channels *args:
    {{cli}} top-channels {{args}}

# just timeline [--account ID|all] [--period day|week|month|quarter] [--since D] [--until D]
timeline *args:
    {{cli}} timeline {{args}}

# Topic accuracy check: first run writes .raw/review/classify-sample.csv to mark by hand, next run scores it
classify-review *args:
    {{cli}} classify-review {{args}}
