"""Settings from `config.yaml` (gitignored: account ids are personal data). See config.example.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator


class Strict(BaseModel):
    # extra=forbid: a typo in config.yaml fails loudly; validate_default: defaults get ~ expanded too.
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Account(Strict):
    id: str  # same as the .raw/<source>-<id>/ folder name
    label: str  # how the LLM prompt names this account, e.g. "giải trí"


class LLM(Strict):
    base_url: str = "http://localhost:8080/v1"
    workers: int = 2  # parallel requests; must not exceed llama-server slots
    max_tokens: int = 1024


class Config(Strict):
    raw_dir: Path = Path(".raw")
    db: Path = Path(".raw/processed/events.duckdb")
    inbox: Path = Path("~/Documents/Obsidian Vault/00-Inbox")  # where `insight --save` writes
    accounts: list[Account] = []  # the first one is the default --account
    llm: LLM = LLM()

    @field_validator("raw_dir", "db", "inbox")
    @classmethod
    def _expand(cls, p: Path) -> Path:
        return p.expanduser()

    @property
    def labels(self) -> dict[str, str]:
        return {a.id: a.label for a in self.accounts}

    @property
    def default_account(self) -> str | None:
        return self.accounts[0].id if self.accounts else None


def load(path: Path = Path("config.yaml")) -> Config:
    """Missing file -> defaults (no accounts), so tests and a fresh clone still run."""
    if not path.exists():
        return Config()
    return Config.model_validate(yaml.safe_load(path.read_text()) or {})
