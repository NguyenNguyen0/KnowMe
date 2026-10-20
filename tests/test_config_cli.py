from pathlib import Path

import pydantic
import pytest

from adapters.cli.__main__ import save_note
from core import config


def test_config_example_and_typos(tmp_path):
    cfg = config.load(Path("config.example.yaml"))
    assert cfg.default_account == "personal-account" and cfg.labels["work-account"] == "học tập, làm việc"
    assert not str(cfg.inbox).startswith("~")
    default = config.load(tmp_path / "missing.yaml")
    assert default.accounts == [] and not str(default.inbox).startswith("~")

    bad = tmp_path / "config.yaml"
    bad.write_text("acounts: []\n")
    with pytest.raises(pydantic.ValidationError):
        config.load(bad)


def test_save_note_never_overwrites(tmp_path):
    a = save_note(tmp_path, "trend", "acc (giải trí)", "answer\n", "line 1\n\nline 2")
    b = save_note(tmp_path, "trend", "acc (giải trí)", "answer", "x")
    assert a != b and b.name.endswith("-trend-2.md")
    text = a.read_text()
    assert text.startswith("---\ntype: report\n") and "> line 1\n>\n> line 2" in text
