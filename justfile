# KnowMe entry points. Each recipe only calls the CLI adapter.

cli := "uv run python -m adapters.cli"

default:
    @just --list

sync:
    uv sync

test *args:
    uv run pytest {{args}}

# just ingest youtube [--account ID]
ingest source="youtube" *args:
    {{cli}} ingest {{source}} {{args}}

# just aggregate [--action watch|search] [--period day|week|month]
aggregate *args:
    {{cli}} aggregate {{args}}

# just insight sleep|trend|habit|topics|around --account ID [--date D] [--show-prompt]
insight question *args:
    {{cli}} insight {{question}} {{args}}

# just classify --account ID [--limit 1000] [--workers 2]   (local LLM, resumable)
classify *args:
    {{cli}} classify {{args}}

# just top-channels --account ID [--since D] [--until D] [--new] [--limit N]
top-channels *args:
    {{cli}} top-channels {{args}}

# just timeline --account ID [--period day|week|month|quarter] [--since D] [--until D]
timeline *args:
    {{cli}} timeline {{args}}
