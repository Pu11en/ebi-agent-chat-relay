"""Create and Clone stay beneath approved roots and never leave half a project behind.

No git runs here: the clone command is a fake runner that records its argv, so
the tests show what would be executed (an argument vector, no shell) and how a
failed attempt cleans up only what it created.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from claude_discord import project_creation
from claude_discord.project_creation import (
    ProjectCreationError,
    ProjectRoots,
    clone_project,
    create_project,
    parse_repository,
    resolve_destination,
)


@pytest.fixture
def roots(tmp_path: Path) -> ProjectRoots:
    (tmp_path / "projects").mkdir()
    (tmp_path / "work").mkdir()
    return ProjectRoots.from_paths([tmp_path / "projects", tmp_path / "work"])


class TestRoots:
    def test_roots_come_from_env_and_skip_missing_folders(self, tmp_path, monkeypatch):
        (tmp_path / "a").mkdir()
        monkeypatch.setenv(
            "CCDB_PROJECT_ROOTS", f"{tmp_path / 'a'}{os.pathsep}{tmp_path / 'missing'}"
        )
        roots = ProjectRoots.from_env()
        assert roots.paths == ((tmp_path / "a").resolve(),)

    def test_comma_separated_roots_are_accepted_too(self, tmp_path, monkeypatch):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        monkeypatch.setenv("CCDB_PROJECT_ROOTS", f"{tmp_path / 'a'},{tmp_path / 'b'}")
        assert len(ProjectRoots.from_env().paths) == 2

    def test_fallback_root_is_used_when_nothing_is_configured(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CCDB_PROJECT_ROOTS", raising=False)
        roots = ProjectRoots.from_env(fallback=str(tmp_path))
        assert roots.paths == (tmp_path.resolve(),)

    def test_no_roots_means_no_creation_anywhere(self, monkeypatch):
        monkeypatch.delenv("CCDB_PROJECT_ROOTS", raising=False)
        roots = ProjectRoots.from_env()
        with pytest.raises(ProjectCreationError, match="No approved project root"):
            roots.choose(None)

    def test_choose_refuses_a_root_that_is_not_approved(self, roots, tmp_path):
        with pytest.raises(ProjectCreationError, match="approved"):
            roots.choose(str(tmp_path))
        assert roots.choose(None) == roots.paths[0]
        assert roots.choose(str(roots.paths[1])) == roots.paths[1]


class TestDestination:
    @pytest.mark.parametrize(
        "name",
        ["../escape", "..", "a/b", "a\\b", "/abs", "C:\\abs", ".hidden", "", "con", "x" * 81, "-x"],
    )
    def test_traversal_absolute_hidden_and_reserved_names_are_refused(self, roots, name):
        with pytest.raises(ProjectCreationError):
            resolve_destination(roots, roots.paths[0], name)
        assert not any(roots.paths[0].iterdir())

    def test_collision_is_refused_case_insensitively(self, roots):
        (roots.paths[0] / "Existing").mkdir()
        with pytest.raises(ProjectCreationError, match="already exists"):
            resolve_destination(roots, roots.paths[0], "existing")

    def test_destination_must_be_beneath_an_approved_root(self, roots, tmp_path):
        with pytest.raises(ProjectCreationError, match="approved"):
            resolve_destination(roots, tmp_path, "fine-name")

    def test_valid_name_resolves_inside_the_root(self, roots):
        dest = resolve_destination(roots, roots.paths[0], "my-project.v2")
        assert dest == roots.paths[0] / "my-project.v2"
        assert not dest.exists()


class TestRepository:
    def test_owner_repo_becomes_a_github_https_url(self):
        assert parse_repository("octo/hello") == ("https://github.com/octo/hello.git", "hello")

    def test_github_urls_are_normalised(self):
        url, name = parse_repository("https://github.com/octo/hello.git")
        assert (url, name) == ("https://github.com/octo/hello.git", "hello")

    def test_other_https_hosts_are_allowed(self):
        url, name = parse_repository("https://gitlab.example.com/group/sub/thing.git")
        assert url == "https://gitlab.example.com/group/sub/thing.git"
        assert name == "thing"

    @pytest.mark.parametrize(
        "value",
        [
            "git@github.com:octo/hello.git",
            "ssh://git@github.com/octo/hello",
            "file:///tmp/repo",
            "/tmp/repo",
            "-oProxyCommand=evil",
            "--upload-pack=evil",
            "https://github.com/octo/hello --bad",
            "",
        ],
    )
    def test_unsupported_locations_are_refused(self, value):
        with pytest.raises(ProjectCreationError):
            parse_repository(value)


class TestCreate:
    async def test_create_makes_the_folder_and_returns_it(self, roots):
        dest = await create_project(roots, None, "fresh")
        assert dest == roots.paths[0] / "fresh"
        assert dest.is_dir()

    async def test_create_never_reuses_an_existing_folder(self, roots):
        (roots.paths[0] / "fresh").mkdir()
        with pytest.raises(ProjectCreationError):
            await create_project(roots, None, "fresh")


class TestClone:
    async def test_clone_runs_git_as_an_argument_vector_into_the_destination(self, roots):
        calls: list[tuple[list[str], Path]] = []

        async def fake_runner(argv: list[str], cwd: Path) -> int:
            calls.append((list(argv), cwd))
            Path(argv[-1]).joinpath("README.md").write_text("hi", encoding="utf-8")
            return 0

        dest = await clone_project(roots, None, "octo/hello", name=None, runner=fake_runner)
        assert dest == roots.paths[0] / "hello"
        assert (dest / "README.md").exists()
        argv, cwd = calls[0]
        assert argv == ["git", "clone", "--", "https://github.com/octo/hello.git", str(dest)]
        assert cwd == roots.paths[0]

    async def test_failed_clone_removes_only_the_folder_it_created(self, roots):
        sibling = roots.paths[0] / "keep-me"
        sibling.mkdir()
        (sibling / "important.txt").write_text("x", encoding="utf-8")

        async def failing_runner(argv: list[str], cwd: Path) -> int:
            Path(argv[-1]).joinpath("partial").write_text("x", encoding="utf-8")
            return 128

        with pytest.raises(ProjectCreationError, match="Clon"):
            await clone_project(roots, None, "octo/hello", name="hello", runner=failing_runner)
        assert not (roots.paths[0] / "hello").exists()
        assert (sibling / "important.txt").exists()

    async def test_runner_exception_is_cleaned_up_and_reported(self, roots):
        async def exploding_runner(argv: list[str], cwd: Path) -> int:
            raise TimeoutError

        with pytest.raises(ProjectCreationError, match="timed out"):
            await clone_project(roots, None, "octo/hello", name="hello", runner=exploding_runner)
        assert not (roots.paths[0] / "hello").exists()

    async def test_explicit_name_wins_and_is_validated(self, roots):
        async def fake_runner(argv: list[str], cwd: Path) -> int:
            return 0

        with pytest.raises(ProjectCreationError):
            await clone_project(roots, None, "octo/hello", name="../x", runner=fake_runner)
        dest = await clone_project(roots, None, "octo/hello", name="renamed", runner=fake_runner)
        assert dest.name == "renamed"

    async def test_default_runner_uses_exec_without_a_shell(self, roots, monkeypatch):
        seen: dict[str, object] = {}

        class FakeProcess:
            returncode = 0

            async def wait(self) -> int:
                return 0

        async def fake_exec(*argv: str, **kwargs: object) -> FakeProcess:
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(project_creation.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "secret")
        code = await project_creation.run_git(["git", "clone", "--", "u", "d"], roots.paths[0])
        assert code == 0
        assert seen["argv"] == ("git", "clone", "--", "u", "d")
        kwargs = seen["kwargs"]
        assert isinstance(kwargs, dict)
        assert "shell" not in kwargs
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert "DISCORD_BOT_TOKEN" not in env
        assert env["GIT_TERMINAL_PROMPT"] == "0"
