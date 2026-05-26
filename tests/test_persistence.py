from __future__ import annotations

import json
from pathlib import Path

import pytest

from minicc.core.persistence import HISTORY_FILE, MessageStore


class TestMessageStore:
    def test_empty_load_when_no_file(self, tmp_path: Path):
        store = MessageStore(path=tmp_path / "nonexistent.json")
        assert store.load() == []

    def test_save_and_load_round_trip(self, tmp_path: Path):
        store = MessageStore(path=tmp_path / "history.json")

        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )

        messages: list = [
            ModelRequest(parts=[UserPromptPart(content="hello")]),
            ModelResponse(parts=[TextPart(content="hi there")]),
        ]

        store.save(messages)
        restored = store.load()
        assert len(restored) == 2
        assert isinstance(restored[0], ModelRequest)
        assert isinstance(restored[1], ModelResponse)

    def test_corrupt_file_returns_empty(self, tmp_path: Path):
        path = tmp_path / "corrupt.json"
        path.write_text("not valid json {{{}", encoding="utf-8")

        store = MessageStore(path=path)
        assert store.load() == []

    def test_save_creates_parent_dir(self, tmp_path: Path):
        store = MessageStore(path=tmp_path / "subdir" / "history.json")
        store.save([])
        assert (tmp_path / "subdir" / "history.json").exists()

    def test_save_is_atomic_via_temp(self, tmp_path: Path):
        path = tmp_path / "history.json"
        tmp_path_str = str(tmp_path / "history.tmp")

        store = MessageStore(path=path)
        store.save([])

        assert path.exists()
        assert not Path(tmp_path_str).exists()

    def test_default_path_is_in_config_dir(self):
        store = MessageStore()
        assert store._path == HISTORY_FILE

    def test_load_handles_non_list_json(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

        store = MessageStore(path=path)
        assert store.load() == []

    def test_load_handles_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")

        store = MessageStore(path=path)
        assert store.load() == []
