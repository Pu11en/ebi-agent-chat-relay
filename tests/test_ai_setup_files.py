"""The shared file helpers never read a whole file before knowing its size.

A harness directory can hold anything — a multi-gigabyte log dropped into
``~/.claude`` by mistake — and an inventory pass must not load it into memory
to discover that it is too big.  The size comes from ``stat`` first; an
oversize file is fingerprinted in bounded chunks (facts) or refused (text).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from claude_discord.ai_setup_adapters import _files
from claude_discord.ai_setup_adapters._files import (
    SourceError,
    claude_home_root,
    read_file_facts,
    read_text_bounded,
)


@pytest.fixture
def no_whole_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: Path) -> bytes:
        raise AssertionError(f"read_bytes() on {self.name}: whole-file read before the size check")

    monkeypatch.setattr(Path, "read_bytes", refuse)


def test_read_file_facts_fingerprints_an_oversize_file_without_reading_it_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_whole_read: None
) -> None:
    monkeypatch.setattr(_files, "MAX_DECODED_BYTES", 16)
    root = claude_home_root(tmp_path, owner="drew")
    big = root.path / "CLAUDE.md"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"x" * 64)

    facts = read_file_facts(root, big)

    assert facts.fingerprint.digest == hashlib.sha256(b"x" * 64).hexdigest()
    assert facts.measurement.byte_size == 64
    assert facts.measurement.character_count is None  # never decoded


def test_read_file_facts_still_measures_a_small_file(tmp_path: Path) -> None:
    root = claude_home_root(tmp_path, owner="drew")
    small = root.path / "CLAUDE.md"
    small.parent.mkdir(parents=True, exist_ok=True)
    small.write_text("hello", encoding="utf-8")

    facts = read_file_facts(root, small)

    assert facts.measurement.byte_size == 5
    assert facts.measurement.character_count == 5


def test_read_text_bounded_refuses_an_oversize_file_before_reading(
    tmp_path: Path, no_whole_read: None
) -> None:
    root = claude_home_root(tmp_path, owner="drew")
    big = root.path / "settings.json"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(b"{" * 64)

    with pytest.raises(SourceError, match="too large"):
        read_text_bounded(root, big, limit=16)
