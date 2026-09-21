"""Tests for the approved-root ProjectLocator resolver (task 3.1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_code_core.handoffs.protocol import HandoffValidationError, ProjectLocator
from claude_discord.handoff_projects import (
    DEFAULT_LOOKUP_LOCATOR,
    ApprovedRootResolver,
    ProjectResolutionError,
    ProjectResolver,
    build_project_resolver,
    lookup_root,
    parse_owner_roots,
)


@pytest.fixture
def drew_root(tmp_path: Path) -> Path:
    root = tmp_path / "drewp"
    (root / "main-projects" / "youtube-money").mkdir(parents=True)
    (root / "notes.txt").write_text("not a folder")
    return root


class TestParseOwnerRoots:
    def test_parses_owner_to_paths(self, tmp_path: Path) -> None:
        roots = parse_owner_roots(f"drew={tmp_path / 'a'};david={tmp_path / 'b'},drew={tmp_path}")
        assert roots["drew"] == (tmp_path / "a", tmp_path)
        assert roots["david"] == (tmp_path / "b",)

    def test_rejects_ambiguous_or_malformed_owner(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            parse_owner_roots(f"my={tmp_path}")
        with pytest.raises(ValueError):
            parse_owner_roots("drew")
        assert parse_owner_roots("") == {}


class TestApprovedRootResolver:
    def test_resolves_a_folder_under_the_owners_root(self, drew_root: Path) -> None:
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        resolved = resolver.resolve(ProjectLocator(owner="drew", folder="main-projects"))
        assert resolved.path == (drew_root / "main-projects").resolve()
        assert resolved.root == drew_root.resolve()
        nested = resolver.resolve(ProjectLocator("drew", "main-projects/youtube-money"))
        assert nested.path.name == "youtube-money"

    def test_unknown_owner_is_refused(self, drew_root: Path) -> None:
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        with pytest.raises(ProjectResolutionError, match="no approved project root"):
            resolver.resolve(ProjectLocator("david", "main-projects"))

    def test_nonexistent_folder_is_refused(self, drew_root: Path) -> None:
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        with pytest.raises(ProjectResolutionError, match="does not exist"):
            resolver.resolve(ProjectLocator("drew", "missing"))

    def test_a_file_is_not_a_project(self, drew_root: Path) -> None:
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        with pytest.raises(ProjectResolutionError, match="not a directory"):
            resolver.resolve(ProjectLocator("drew", "notes.txt"))

    def test_refusals_name_labels_never_local_paths(self, drew_root: Path) -> None:
        """The refusal text travels to the peer and to Discord; local paths do not."""
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        for folder in ("missing", "notes.txt"):
            with pytest.raises(ProjectResolutionError) as excinfo:
                resolver.resolve(ProjectLocator("drew", folder))
            text = str(excinfo.value)
            assert str(drew_root) not in text
            assert drew_root.name not in text
            assert folder in text and "drew" in text

    def test_pinned_refusals_name_labels_never_local_paths(self, tmp_path: Path) -> None:
        pinned = tmp_path / "absent"
        locator = ProjectLocator("drew", "main-projects")
        resolver = ApprovedRootResolver(roots={}, pinned={locator: pinned})
        with pytest.raises(ProjectResolutionError) as excinfo:
            resolver.resolve(locator)
        assert str(tmp_path) not in str(excinfo.value)
        assert "main-projects" in str(excinfo.value)

    def test_symlink_escape_is_refused(self, drew_root: Path, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = drew_root / "escape"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are not permitted on this host")
        resolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        with pytest.raises(ProjectResolutionError, match="outside"):
            resolver.resolve(ProjectLocator("drew", "escape"))

    def test_remote_absolute_paths_never_become_locators(self) -> None:
        with pytest.raises(HandoffValidationError):
            ProjectLocator("drew", "/home/drewp/main-projects")
        with pytest.raises(HandoffValidationError):
            ProjectLocator("drew", "C:/Users/drew/projects")
        with pytest.raises(HandoffValidationError):
            ProjectLocator("drew", "../main-projects")

    def test_first_matching_root_wins_and_later_roots_are_tried(self, tmp_path: Path) -> None:
        first = tmp_path / "one"
        second = tmp_path / "two"
        (second / "proj").mkdir(parents=True)
        first.mkdir()
        resolver = ApprovedRootResolver(roots={"drew": (first, second)})
        assert resolver.resolve(ProjectLocator("drew", "proj")).root == second.resolve()

    def test_pinned_locator_resolves_to_its_exact_path(self, drew_root: Path) -> None:
        pinned = drew_root / "main-projects"
        resolver = ApprovedRootResolver(roots={}, pinned={DEFAULT_LOOKUP_LOCATOR: pinned})
        assert resolver.resolve(DEFAULT_LOOKUP_LOCATOR).path == pinned.resolve()

    def test_satisfies_the_injectable_protocol(self, drew_root: Path) -> None:
        resolver: ProjectResolver = ApprovedRootResolver(roots={"drew": (drew_root,)})
        assert resolver.resolve(ProjectLocator("drew", "main-projects")).path.is_dir()


class TestLookupRoot:
    def test_precedence_is_explicit_then_env_then_project_roots_then_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b, c, d = (tmp_path / n for n in "abcd")
        for folder in (a, b, c, d):
            folder.mkdir()
        monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(b))
        monkeypatch.setenv("CCDB_PROJECT_ROOTS", f"{c},{d}")
        assert lookup_root(configured=str(a), fallback=str(d)) == str(a)
        assert lookup_root(fallback=str(d)) == str(b)
        monkeypatch.delenv("CCDB_PROJECT_LOOKUP_ROOT")
        assert lookup_root(fallback=str(d)) == str(c)
        monkeypatch.delenv("CCDB_PROJECT_ROOTS")
        assert lookup_root(fallback=str(d)) == str(d)

    def test_missing_directory_is_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(tmp_path / "nope"))
        with pytest.raises(ValueError):
            lookup_root()

    def test_build_project_resolver_pins_the_default_lookup_locator(
        self, drew_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_PROJECT_LOOKUP_ROOT", str(drew_root / "main-projects"))
        monkeypatch.setenv("CCDB_HANDOFF_PROJECT_ROOTS", f"drew={drew_root}")
        resolver = build_project_resolver()
        expected = (drew_root / "main-projects").resolve()
        assert resolver.resolve(DEFAULT_LOOKUP_LOCATOR).path == expected
        nested = resolver.resolve(ProjectLocator("drew", "main-projects/youtube-money"))
        assert nested.path.name == "youtube-money"

    def test_build_project_resolver_without_a_lookup_root_still_serves_owner_roots(
        self, drew_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CCDB_PROJECT_LOOKUP_ROOT", raising=False)
        monkeypatch.delenv("CCDB_PROJECT_ROOTS", raising=False)
        monkeypatch.setenv("CCDB_HANDOFF_PROJECT_ROOTS", f"drew={drew_root}")
        resolver = build_project_resolver(fallback_lookup_root=str(drew_root / "does-not-exist"))
        assert resolver.resolve(ProjectLocator("drew", "main-projects")).path.is_dir()
