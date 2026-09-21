"""Tests for claude_code_core.context_nudge — when to suggest a fresh session."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core import context_nudge as cn


class TestNudgeStep:
    @pytest.mark.parametrize(
        "used,window,already,expected",
        [
            (100, 1000, 0, None),  # 10% — quiet
            (500, 1000, 0, 50),  # crossed 50%
            (600, 1000, 50, None),  # already nudged at 50
            (760, 1000, 50, 75),  # next step
            (950, 1000, 0, 90),  # jumps straight to the highest crossed step
            (950, 1000, 90, None),
            (500, 0, 0, None),  # unknown window
            (None, 1000, 0, None),
        ],
    )
    def test_steps(self, used: int | None, window: int, already: int, expected: int | None) -> None:
        assert cn.nudge_step(used, window, already) == expected


class TestHandoff:
    def test_path_is_inside_the_project(self, tmp_path: Path) -> None:
        p = cn.handoff_path(tmp_path, "📂 realpage planning!", "2026-09-10")
        assert p.parent == tmp_path / "handoffs"
        assert p.name.startswith("2026-09-10-")
        assert p.suffix == ".md"
        assert "/" not in p.name[len("2026-09-10-") :]

    def test_prompt_names_the_file_and_sections(self, tmp_path: Path) -> None:
        text = cn.handoff_prompt(tmp_path / "handoffs" / "x.md")
        assert str(tmp_path / "handoffs" / "x.md") in text
        for section in ("Goal", "Where things stand", "Decisions", "Next steps"):
            assert section in text

    def test_starter_points_at_the_handoff(self, tmp_path: Path) -> None:
        text = cn.starter_prompt(tmp_path / "handoffs" / "x.md", "<#1>")
        assert "x.md" in text and "<#1>" in text


class TestNextThreadName:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("realpage", "realpage · part 2"),
            ("realpage · part 2", "realpage · part 3"),
            ("x" * 100, "x" * 91 + " · part 2"),
        ],
    )
    def test_numbering(self, name: str, expected: str) -> None:
        assert cn.next_thread_name(name) == expected
