"""Tests for workdir discovery helpers (/cd, /cdnew autocomplete)."""

from __future__ import annotations

from pathlib import Path

from workdir_command import (
    display_label,
    find_directories,
    resolve_project_roots,
)


class TestResolveProjectRoots:
    def test_default_is_developer(self) -> None:
        roots = resolve_project_roots({})
        assert roots == [Path("~/Developer").expanduser()]

    def test_parses_comma_list_and_expands(self) -> None:
        roots = resolve_project_roots({"CCDB_PROJECT_ROOTS": "~/Developer, ~/work"})
        assert roots == [Path("~/Developer").expanduser(), Path("~/work").expanduser()]

    def test_dedupes(self) -> None:
        roots = resolve_project_roots({"CCDB_PROJECT_ROOTS": "~/a,~/a"})
        assert roots == [Path("~/a").expanduser()]

    def test_blank_falls_back_to_default(self) -> None:
        assert resolve_project_roots({"CCDB_PROJECT_ROOTS": "   "}) == [
            Path("~/Developer").expanduser()
        ]


class TestFindDirectories:
    def _make_tree(self, base: Path) -> None:
        (base / "quiet-ink").mkdir()
        (base / "quiet-ink" / "prototype").mkdir()
        (base / "plex-stack").mkdir()
        (base / "tap-tap-spell").mkdir()
        (base / "node_modules").mkdir()  # should be skipped
        (base / ".hidden").mkdir()  # should be skipped
        (base / "notes.txt").write_text("x")  # file, not a dir

    def test_empty_query_lists_top_level_projects(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        names = {p.name for p in find_directories([tmp_path], "")}
        assert names == {"quiet-ink", "plex-stack", "tap-tap-spell"}

    def test_skips_noise_and_hidden_and_files(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        names = {p.name for p in find_directories([tmp_path], "")}
        assert "node_modules" not in names
        assert ".hidden" not in names
        assert "notes.txt" not in names

    def test_substring_query_matches(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        results = find_directories([tmp_path], "tap")
        assert [p.name for p in results] == ["tap-tap-spell"]

    def test_query_searches_nested(self, tmp_path: Path) -> None:
        self._make_tree(tmp_path)
        results = find_directories([tmp_path], "prototype", max_depth=2)
        assert any(p.name == "prototype" for p in results)

    def test_prefix_matches_rank_first(self, tmp_path: Path) -> None:
        (tmp_path / "ink-shop").mkdir()
        (tmp_path / "quiet-ink").mkdir()
        results = find_directories([tmp_path], "ink")
        # "ink-shop" starts with the query, so it ranks ahead of "quiet-ink".
        assert results[0].name == "ink-shop"

    def test_limit_is_respected(self, tmp_path: Path) -> None:
        for i in range(30):
            (tmp_path / f"proj{i:02d}").mkdir()
        assert len(find_directories([tmp_path], "", limit=25)) == 25

    def test_missing_root_is_ignored(self, tmp_path: Path) -> None:
        assert find_directories([tmp_path / "does-not-exist"], "") == []


class TestDisplayLabel:
    def test_relative_to_home(self) -> None:
        path = Path.home() / "Developer" / "quiet-ink"
        assert display_label(path) == "~/Developer/quiet-ink"

    def test_outside_home_is_absolute(self) -> None:
        assert display_label(Path("/opt/thing")) == "/opt/thing"
