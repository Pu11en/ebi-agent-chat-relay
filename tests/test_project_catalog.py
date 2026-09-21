"""Tests for the shared project catalog (OpenSpec tasks 1.1, 1.2 and 1.3).

Domain-model tests (task 1.1) cover identity, availability, bounded queries and
the five typed resolution results without touching disk.  The discovery tests
(task 1.2) scan real directories, always inside pytest's ``tmp_path``.  The
owner-resolution tests (task 1.3) at the end of the file cover explicit
computer-owner aliases and the local-versus-remote decision.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

import pytest

from claude_discord import project_catalog
from claude_discord.project_catalog import (
    MAX_QUERY_LIMIT,
    AmbiguousOwnerResolution,
    ApprovedRoot,
    Availability,
    CatalogProject,
    CatalogQuery,
    CatalogResolver,
    CatalogSnapshot,
    LocalProjectResolution,
    LocalUnavailableResolution,
    NoMatchResolution,
    OwnerLookupKind,
    OwnerRegistry,
    ProjectDiscovery,
    ProjectIdentity,
    RemoteTargetResolution,
    ResolutionKind,
    RootStatus,
    TrustedComputer,
    disambiguated_labels,
    parse_owner_phrase,
)


def make_root(
    key: str = "main",
    path: str = "/home/drew/main-projects",
    *,
    owner: str = "drew",
    computer: str = "drewai",
) -> ApprovedRoot:
    return ApprovedRoot(key=key, path=PurePosixPath(path), owner=owner, computer=computer)


def make_project(
    name: str = "ebi-agent-chat-relay",
    *,
    root: ApprovedRoot | None = None,
    availability: Availability = Availability.AVAILABLE,
) -> CatalogProject:
    resolved = root or make_root()
    return CatalogProject(
        identity=resolved.child_identity(name),
        path=resolved.path / name,
        availability=availability,
    )


class TestApprovedRoot:
    """An approved root is immutable configuration, not discovered state."""

    def test_normalizes_owner_computer_and_key(self) -> None:
        root = ApprovedRoot(
            key="  Main Projects ",
            path=PurePosixPath("/home/drew/main-projects"),
            owner="  Drew  ",
            computer="DrewAI",
        )
        assert root.key == "main-projects"
        assert root.owner == "drew"
        assert root.computer == "drewai"

    def test_rejects_a_relative_path(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            ApprovedRoot(
                key="main",
                path=PurePosixPath("main-projects"),
                owner="drew",
                computer="drewai",
            )

    def test_rejects_an_empty_key(self) -> None:
        with pytest.raises(ValueError, match="key"):
            ApprovedRoot(
                key="   ",
                path=PurePosixPath("/home/drew/main-projects"),
                owner="drew",
                computer="drewai",
            )

    def test_accepts_a_windows_root_path(self) -> None:
        root = ApprovedRoot(
            key="main",
            path=PureWindowsPath(r"C:\Users\david\projects"),
            owner="david",
            computer="davidpc",
        )
        assert root.path == PureWindowsPath(r"C:\Users\david\projects")

    def test_is_immutable(self) -> None:
        root = make_root()
        with pytest.raises(FrozenInstanceError):
            root.key = "other"  # type: ignore[misc]

    def test_label_defaults_to_the_key(self) -> None:
        assert make_root(key="main").label == "main"
        assert make_root(key="main").with_label("Main projects").label == "Main projects"


class TestProjectIdentity:
    """Identity is owner + computer + root key + folder name — never a path."""

    def test_same_location_yields_the_same_identity(self) -> None:
        assert make_root().child_identity("alpha") == make_root().child_identity("alpha")
        assert hash(make_root().child_identity("alpha")) == hash(
            make_root().child_identity("alpha")
        )

    def test_absolute_path_is_not_part_of_identity(self) -> None:
        moved = make_root(path="/mnt/backup/main-projects")
        assert moved.child_identity("alpha") == make_root().child_identity("alpha")
        assert "/" not in moved.child_identity("alpha").key

    def test_same_name_in_two_roots_stays_distinct(self) -> None:
        first = make_root(key="main").child_identity("alpha")
        second = make_root(key="archive", path="/home/drew/archive").child_identity("alpha")
        assert first != second
        assert first.key != second.key

    def test_same_name_on_two_computers_stays_distinct(self) -> None:
        drew = make_root(owner="drew", computer="drewai").child_identity("alpha")
        david = make_root(owner="david", computer="davidpc").child_identity("alpha")
        assert drew != david
        assert {drew, david} == {drew, david}
        assert len({drew, david}) == 2

    def test_key_round_trips_through_storage(self) -> None:
        identity = make_root().child_identity("alpha: beta")
        assert ProjectIdentity.from_key(identity.key) == identity

    def test_from_key_rejects_malformed_text(self) -> None:
        with pytest.raises(ValueError, match="identity key"):
            ProjectIdentity.from_key("drew:drewai")

    def test_folder_name_case_is_preserved(self) -> None:
        assert make_root().child_identity("Alpha").name == "Alpha"
        assert make_root().child_identity("Alpha") != make_root().child_identity("alpha")

    @pytest.mark.parametrize("name", ["", "   ", ".", "..", "a/b", "a\\b"])
    def test_rejects_names_that_are_not_direct_children(self, name: str) -> None:
        with pytest.raises(ValueError):
            make_root().child_identity(name)


class TestCatalogProject:
    """A project carries its local path as runtime data, guarded by availability."""

    def test_available_project_exposes_a_working_directory(self) -> None:
        project = make_project()
        assert project.is_available
        assert project.working_directory == PurePosixPath(
            "/home/drew/main-projects/ebi-agent-chat-relay"
        )

    def test_unavailable_project_has_no_working_directory(self) -> None:
        project = make_project(availability=Availability.MISSING)
        assert not project.is_available
        assert project.working_directory is None

    def test_with_availability_returns_a_new_value(self) -> None:
        project = make_project()
        updated = project.with_availability(Availability.UNREADABLE)
        assert project.availability is Availability.AVAILABLE
        assert updated.availability is Availability.UNREADABLE
        assert updated.identity == project.identity

    def test_path_must_sit_directly_under_its_root(self) -> None:
        root = make_root()
        with pytest.raises(ValueError, match="direct child"):
            CatalogProject(
                identity=root.child_identity("alpha"),
                path=PurePosixPath("/home/drew/main-projects/alpha/nested"),
            )

    def test_display_name_is_the_folder_name(self) -> None:
        assert make_project(name="alpha").display_name == "alpha"


class TestDisambiguatedLabels:
    """Labels add root or computer context only when a name repeats."""

    def test_unique_names_use_the_bare_folder_name(self) -> None:
        projects = [make_project(name="alpha"), make_project(name="beta")]
        labels = disambiguated_labels(projects)
        assert set(labels.values()) == {"alpha", "beta"}

    def test_duplicate_names_in_two_roots_gain_a_root_qualifier(self) -> None:
        archive = make_root(key="archive", path="/home/drew/archive")
        projects = [make_project(name="alpha"), make_project(name="alpha", root=archive)]
        labels = disambiguated_labels(projects)
        assert sorted(labels.values()) == ["alpha (archive)", "alpha (main)"]

    def test_duplicate_names_on_two_computers_gain_an_owner_qualifier(self) -> None:
        david = make_root(owner="david", computer="davidpc", path="/home/david/projects")
        projects = [make_project(name="alpha"), make_project(name="alpha", root=david)]
        labels = disambiguated_labels(projects)
        assert sorted(labels.values()) == ["alpha (david/davidpc)", "alpha (drew/drewai)"]


class TestCatalogQuery:
    """Queries are normalized and always bounded."""

    def test_limit_is_clamped_into_range(self) -> None:
        assert CatalogQuery(limit=0).limit == 1
        assert CatalogQuery(limit=10_000).limit == MAX_QUERY_LIMIT
        assert CatalogQuery().limit <= MAX_QUERY_LIMIT

    def test_text_and_owner_are_normalized(self) -> None:
        query = CatalogQuery(text="  Ebi Relay  ", owner=" Drew ")
        assert query.text == "Ebi Relay"
        assert query.owner == "drew"
        assert CatalogQuery(owner="   ").owner is None

    def test_matches_folder_name_case_insensitively(self) -> None:
        project = make_project(name="ebi-agent-chat-relay")
        assert CatalogQuery(text="EBI").matches(project)
        assert not CatalogQuery(text="realpage").matches(project)
        assert CatalogQuery().matches(project)

    def test_matches_filters_by_owner(self) -> None:
        project = make_project()
        assert CatalogQuery(owner="Drew").matches(project)
        assert not CatalogQuery(owner="david").matches(project)

    def test_unavailable_projects_are_excluded_unless_requested(self) -> None:
        project = make_project(availability=Availability.MISSING)
        assert not CatalogQuery(text="ebi").matches(project)
        assert CatalogQuery(text="ebi", include_unavailable=True).matches(project)


class TestResolutionResults:
    """Five typed outcomes; only a local available project yields a path."""

    def test_local_available_binds_a_working_directory(self) -> None:
        result = LocalProjectResolution(project=make_project())
        assert result.kind is ResolutionKind.LOCAL_AVAILABLE
        assert result.is_local_available
        assert result.working_directory == PurePosixPath(
            "/home/drew/main-projects/ebi-agent-chat-relay"
        )

    def test_local_available_rejects_an_unavailable_project(self) -> None:
        with pytest.raises(ValueError, match="available"):
            LocalProjectResolution(project=make_project(availability=Availability.MISSING))

    def test_local_unavailable_keeps_identity_without_a_path(self) -> None:
        project = make_project(availability=Availability.MISSING)
        result = LocalUnavailableResolution(project=project, reason="folder was removed")
        assert result.kind is ResolutionKind.LOCAL_UNAVAILABLE
        assert not result.is_local_available
        assert result.working_directory is None
        assert result.project.identity == project.identity

    def test_local_unavailable_rejects_an_available_project(self) -> None:
        with pytest.raises(ValueError, match="available"):
            LocalUnavailableResolution(project=make_project(), reason="none")

    def test_remote_target_names_a_computer_and_never_a_local_path(self) -> None:
        verified = datetime(2026, 9, 20, 1, 30, tzinfo=UTC)
        result = RemoteTargetResolution(
            owner="Drew",
            computer="DrewAI",
            requested_terms=("ebi", "relay"),
            verified_at=verified,
        )
        assert result.kind is ResolutionKind.REMOTE_TARGET
        assert (result.owner, result.computer) == ("drew", "drewai")
        assert result.working_directory is None
        assert not result.is_local_available
        assert not result.is_locally_verified
        assert result.verified_at == verified
        assert not hasattr(result, "path")

    def test_remote_target_requires_an_owner_and_a_computer(self) -> None:
        with pytest.raises(ValueError, match="computer"):
            RemoteTargetResolution(owner="drew", computer="  ", requested_terms=())

    def test_remote_target_can_report_a_queued_offline_computer(self) -> None:
        result = RemoteTargetResolution(
            owner="drew",
            computer="drewai",
            requested_terms=("alpha",),
            availability=Availability.UNKNOWN,
            queued=True,
        )
        assert result.queued
        assert result.availability is Availability.UNKNOWN
        assert result.verified_at is None

    def test_ambiguous_owner_asks_instead_of_choosing(self) -> None:
        result = AmbiguousOwnerResolution(
            requested_owner="  Their  ",
            candidate_owners=("David", "drew", "drew"),
        )
        assert result.kind is ResolutionKind.AMBIGUOUS_OWNER
        assert result.requested_owner == "their"
        assert result.candidate_owners == ("david", "drew")
        assert result.working_directory is None

    def test_no_match_reports_what_was_searched(self) -> None:
        result = NoMatchResolution(query=CatalogQuery(text="nope", owner="drew"))
        assert result.kind is ResolutionKind.NO_MATCH
        assert result.query.text == "nope"
        assert result.working_directory is None
        assert not result.is_local_available

    def test_every_kind_is_distinct_and_pattern_matchable(self) -> None:
        results = [
            LocalProjectResolution(project=make_project()),
            LocalUnavailableResolution(
                project=make_project(availability=Availability.MISSING), reason="gone"
            ),
            RemoteTargetResolution(owner="drew", computer="drewai", requested_terms=()),
            AmbiguousOwnerResolution(requested_owner="their", candidate_owners=("drew", "david")),
            NoMatchResolution(query=CatalogQuery(text="nope")),
        ]
        assert len({result.kind for result in results}) == len(ResolutionKind) == 5
        for result in results:
            match result:
                case LocalProjectResolution():
                    assert result.working_directory is not None
                case _:
                    assert result.working_directory is None


class TestCatalogSnapshot:
    """One unreadable root must not empty the whole catalog."""

    def test_unavailable_root_is_reported_without_dropping_other_projects(self) -> None:
        main = make_root(key="main")
        archive = make_root(key="archive", path="/home/drew/archive")
        snapshot = CatalogSnapshot(
            roots=(
                RootStatus(root=main, availability=Availability.AVAILABLE),
                RootStatus(
                    root=archive,
                    availability=Availability.UNREADABLE,
                    reason="permission denied",
                ),
            ),
            projects=(make_project(name="alpha", root=main),),
        )
        assert [status.root.key for status in snapshot.unavailable_roots] == ["archive"]
        assert [project.identity.name for project in snapshot.available_projects] == ["alpha"]
        assert not snapshot.truncated

    def test_truncation_is_explicit(self) -> None:
        snapshot = CatalogSnapshot(roots=(), projects=(), truncated=True)
        assert snapshot.truncated
        assert snapshot.available_projects == ()

    def test_is_immutable(self) -> None:
        snapshot = CatalogSnapshot(roots=(), projects=())
        with pytest.raises(FrozenInstanceError):
            snapshot.truncated = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Bounded one-level discovery (OpenSpec task 1.2).
#
# These tests do touch the filesystem, always inside pytest's ``tmp_path``.
# ---------------------------------------------------------------------------


def _symlink_dir(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:  # Windows without Developer Mode: no symlink privilege
        pytest.skip(f"symlinks not permitted here: {exc}")


def make_fs_root(
    base: Path,
    *,
    key: str = "main",
    owner: str = "drew",
    computer: str = "drewai",
) -> ApprovedRoot:
    """An approved root pointing at a real directory under ``tmp_path``."""
    return ApprovedRoot(key=key, path=PurePath(base), owner=owner, computer=computer)


def make_tree(base: Path, *names: str) -> Path:
    for name in names:
        (base / name).mkdir(parents=True, exist_ok=True)
    return base


class FakeClock:
    """A monotonic clock the cache tests can move deliberately."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestDiscoveryBoundary:
    """Approved roots are the boundary: direct child directories, nothing else."""

    def test_lists_direct_child_directories_and_ignores_files(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")
        (tmp_path / "notes.md").write_text("not a project")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha", "beta"]

    def test_nested_directories_are_not_separate_projects(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha/src/deep", "alpha/docs", "beta/.git")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha", "beta"]

    def test_project_paths_never_escape_their_approved_root(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        outside = tmp_path / "outside" / "elsewhere"
        outside.mkdir(parents=True)
        make_tree(root, "alpha")
        _symlink_dir(root / "linked", outside)

        snapshot = ProjectDiscovery([make_fs_root(root)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha", "linked"]
        for project in snapshot.projects:
            assert project.path.parent == PurePath(root)

    def test_hidden_and_noise_directories_are_skipped(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", ".cache", "node_modules", "__pycache__", "venv")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha"]

    def test_broken_symlinks_are_not_projects(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        _symlink_dir(tmp_path / "dangling", tmp_path / "gone")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha"]


class TestDiscoveryRootAvailability:
    """An unusable root is reported as data, never as a failed catalog."""

    def test_missing_root_does_not_remove_other_roots_projects(self, tmp_path: Path) -> None:
        good = make_tree(tmp_path / "good", "alpha")
        gone = tmp_path / "gone"

        snapshot = ProjectDiscovery(
            [make_fs_root(good, key="good"), make_fs_root(gone, key="gone")]
        ).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha"]
        statuses = {status.root.key: status for status in snapshot.roots}
        assert statuses["good"].availability is Availability.AVAILABLE
        assert statuses["gone"].availability is Availability.MISSING
        assert statuses["gone"].reason
        assert snapshot.unavailable_roots == (statuses["gone"],)

    def test_root_that_is_a_file_is_unreadable(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "roots.txt"
        not_a_dir.write_text("oops")

        snapshot = ProjectDiscovery([make_fs_root(not_a_dir)]).snapshot()

        assert snapshot.projects == ()
        assert snapshot.roots[0].availability is Availability.UNREADABLE

    def test_unreadable_root_is_reported_with_a_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = make_tree(tmp_path / "good", "alpha")
        locked = make_tree(tmp_path / "locked", "beta")
        real_scandir = os.scandir

        def deny_locked(path: object):
            if str(path) == str(locked):
                raise PermissionError(13, "Permission denied")
            return real_scandir(path)  # type: ignore[arg-type]

        monkeypatch.setattr(project_catalog.os, "scandir", deny_locked)

        snapshot = ProjectDiscovery(
            [make_fs_root(good, key="good"), make_fs_root(locked, key="locked")]
        ).snapshot()

        assert [project.identity.name for project in snapshot.projects] == ["alpha"]
        statuses = {status.root.key: status for status in snapshot.roots}
        assert statuses["locked"].availability is Availability.UNREADABLE
        assert statuses["locked"].reason

    def test_every_configured_root_is_reported_in_configured_order(self, tmp_path: Path) -> None:
        first = make_tree(tmp_path / "first", "alpha")
        second = make_tree(tmp_path / "second", "beta")

        snapshot = ProjectDiscovery(
            [make_fs_root(second, key="second"), make_fs_root(first, key="first")]
        ).snapshot()

        assert [status.root.key for status in snapshot.roots] == ["second", "first"]


class TestDiscoveryOrderingAndIdentity:
    """Ordering is deterministic and identities survive a rescan."""

    def test_projects_are_sorted_case_insensitively_by_name(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "Zeta", "alpha", "Beta", "gamma")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot()

        assert [project.identity.name for project in snapshot.projects] == [
            "alpha",
            "Beta",
            "gamma",
            "Zeta",
        ]

    def test_same_name_in_two_roots_keeps_distinct_identities(self, tmp_path: Path) -> None:
        work = make_tree(tmp_path / "work", "relay")
        personal = make_tree(tmp_path / "personal", "relay")

        snapshot = ProjectDiscovery(
            [make_fs_root(work, key="work"), make_fs_root(personal, key="personal")]
        ).snapshot()

        identities = [project.identity for project in snapshot.projects]
        assert len(identities) == 2
        assert len({identity.key for identity in identities}) == 2
        assert [identity.root_key for identity in identities] == ["personal", "work"]
        labels = disambiguated_labels(snapshot.projects)
        assert set(labels.values()) == {"relay (personal)", "relay (work)"}

    def test_identity_is_stable_across_repeated_discovery(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=0.0)

        first = discovery.snapshot()
        second = discovery.snapshot(refresh=True)

        assert [project.identity for project in first.projects] == [
            project.identity for project in second.projects
        ]

    def test_discovered_projects_are_available_with_a_working_directory(
        self, tmp_path: Path
    ) -> None:
        make_tree(tmp_path, "alpha")

        project = ProjectDiscovery([make_fs_root(tmp_path)]).snapshot().projects[0]

        assert project.availability is Availability.AVAILABLE
        assert project.working_directory == PurePath(tmp_path / "alpha")


class TestDiscoveryBounds:
    """A huge root is truncated deterministically instead of flooding a caller."""

    def test_results_are_capped_and_marked_truncated(self, tmp_path: Path) -> None:
        make_tree(tmp_path, *[f"p{index:03d}" for index in range(10)])

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)], max_projects=4).snapshot()

        assert snapshot.truncated is True
        assert [project.identity.name for project in snapshot.projects] == [
            "p000",
            "p001",
            "p002",
            "p003",
        ]

    def test_an_exactly_full_result_is_not_truncated(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")

        snapshot = ProjectDiscovery([make_fs_root(tmp_path)], max_projects=2).snapshot()

        assert snapshot.truncated is False
        assert len(snapshot.projects) == 2


class TestDiscoveryRefresh:
    """New and removed folders appear without any registration step."""

    def test_a_new_folder_appears_on_refresh(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=0.0)
        assert [p.identity.name for p in discovery.snapshot().projects] == ["alpha"]

        make_tree(tmp_path, "beta")

        assert [p.identity.name for p in discovery.snapshot().projects] == ["alpha", "beta"]

    def test_a_removed_folder_leaves_the_available_results(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=0.0)
        discovery.snapshot()

        (tmp_path / "beta").rmdir()

        names = [project.identity.name for project in discovery.snapshot().available_projects]
        assert names == ["alpha"]

    def test_cached_snapshot_is_reused_until_the_ttl_expires(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        clock = FakeClock()
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=30.0, time_source=clock)
        discovery.snapshot()

        make_tree(tmp_path, "beta")
        clock.advance(29.0)
        assert [p.identity.name for p in discovery.snapshot().projects] == ["alpha"]

        clock.advance(2.0)
        assert [p.identity.name for p in discovery.snapshot().projects] == ["alpha", "beta"]

    def test_refresh_bypasses_the_cache(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        clock = FakeClock()
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=3600.0, time_source=clock)
        discovery.snapshot()

        make_tree(tmp_path, "beta")

        assert [p.identity.name for p in discovery.snapshot(refresh=True).projects] == [
            "alpha",
            "beta",
        ]

    def test_invalidate_forces_the_next_query_to_rescan(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        clock = FakeClock()
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=3600.0, time_source=clock)
        discovery.snapshot()

        make_tree(tmp_path, "beta")
        discovery.invalidate()

        assert [p.identity.name for p in discovery.snapshot().projects] == ["alpha", "beta"]

    async def test_async_snapshot_matches_the_synchronous_scan(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=0.0)

        assert await discovery.snapshot_async() == discovery.snapshot()


class TestDiscoveryRevalidation:
    """Pre-launch revalidation refuses to hand out a stale working directory."""

    def test_an_existing_project_stays_available(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)])
        project = discovery.snapshot().projects[0]

        checked = discovery.revalidate(project)

        assert checked.is_available
        assert checked.identity == project.identity
        assert checked.working_directory == project.path

    def test_a_deleted_project_becomes_missing_without_a_working_directory(
        self, tmp_path: Path
    ) -> None:
        make_tree(tmp_path, "alpha")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)])
        project = discovery.snapshot().projects[0]

        (tmp_path / "alpha").rmdir()
        checked = discovery.revalidate(project)

        assert checked.availability is Availability.MISSING
        assert checked.working_directory is None
        assert checked.identity == project.identity

    def test_a_project_replaced_by_a_file_is_unreadable(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)])
        project = discovery.snapshot().projects[0]

        (tmp_path / "alpha").rmdir()
        (tmp_path / "alpha").write_text("now a file")
        checked = discovery.revalidate(project)

        assert checked.availability is Availability.UNREADABLE
        assert checked.working_directory is None

    def test_a_returning_project_keeps_its_original_identity(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        discovery = ProjectDiscovery([make_fs_root(tmp_path)], cache_ttl=0.0)
        original = discovery.snapshot().projects[0]

        (tmp_path / "alpha").rmdir()
        assert discovery.snapshot().projects == ()
        make_tree(tmp_path, "alpha")

        returned = discovery.snapshot().projects[0]
        assert returned.identity == original.identity
        assert returned.identity.key == original.identity.key


# ---------------------------------------------------------------------------
# Task 1.3 — explicit computer-owner aliases and local/remote resolution.
# Owner language is only honoured when the request says it outright; a pronoun
# is never turned into a computer, and a remote owner never yields a local path.
# ---------------------------------------------------------------------------


def make_registry(*, local: str = "david") -> OwnerRegistry:
    """Two trusted computers — David's is local here, Drew's is remote."""
    return OwnerRegistry(
        [
            TrustedComputer(
                owner="david",
                computer="davidpc",
                aliases=("dave-pc",),
                is_local=local == "david",
            ),
            TrustedComputer(
                owner="drew",
                computer="drewai",
                aliases=("drew-ai",),
                is_local=local == "drew",
            ),
        ]
    )


def make_resolver(
    base: Path,
    *,
    registry: OwnerRegistry | None = None,
    owner: str = "david",
    computer: str = "davidpc",
    extra_roots: tuple[ApprovedRoot, ...] = (),
    cache_ttl: float = 0.0,
) -> CatalogResolver:
    root = make_fs_root(base, owner=owner, computer=computer)
    discovery = ProjectDiscovery((root, *extra_roots), cache_ttl=cache_ttl)
    return CatalogResolver(discovery, registry or make_registry())


class TestTrustedComputer:
    """A trusted computer is explicit configuration: aliases are declared, never inferred."""

    def test_normalizes_owner_computer_and_aliases(self) -> None:
        computer = TrustedComputer(owner="  Drew ", computer="Drew AI", aliases=("Drew's Mac",))

        assert computer.owner == "drew"
        assert computer.computer == "drew-ai"
        assert "drew-s-mac" in computer.aliases

    def test_owner_and_computer_are_implicit_aliases(self) -> None:
        computer = TrustedComputer(owner="david", computer="davidpc")

        assert computer.aliases == ("david", "davidpc")

    def test_duplicate_aliases_collapse_without_reordering(self) -> None:
        computer = TrustedComputer(owner="david", computer="davidpc", aliases=("David", "dave"))

        assert computer.aliases == ("david", "davidpc", "dave")

    def test_qualifier_names_owner_and_computer(self) -> None:
        assert TrustedComputer(owner="drew", computer="imac").qualifier == "drew/imac"

    def test_rejects_an_alias_with_no_letters_or_digits(self) -> None:
        with pytest.raises(ValueError):
            TrustedComputer(owner="drew", computer="drewai", aliases=("---",))


class TestOwnerRegistry:
    """Alias lookup maps a phrase to at most one trusted computer, or refuses."""

    def test_an_explicit_alias_matches_one_computer(self) -> None:
        lookup = make_registry().lookup("drew-ai")

        assert lookup.kind is OwnerLookupKind.MATCHED
        assert lookup.computer is not None
        assert lookup.computer.qualifier == "drew/drewai"

    def test_the_owner_name_matches_its_computer(self) -> None:
        assert make_registry().lookup("Drew").computer == TrustedComputer(
            owner="drew", computer="drewai", aliases=("drew-ai",)
        )

    def test_an_unknown_phrase_is_not_guessed(self) -> None:
        lookup = make_registry().lookup("sam")

        assert lookup.kind is OwnerLookupKind.UNKNOWN
        assert lookup.computer is None
        assert len(lookup.candidates) == 2

    def test_a_pronoun_is_refused_rather_than_resolved(self) -> None:
        for pronoun in ("my", "his", "her", "their", "your"):
            assert make_registry().lookup(pronoun).kind is OwnerLookupKind.PRONOUN

    def test_one_owner_on_two_remote_computers_is_ambiguous(self) -> None:
        registry = OwnerRegistry(
            [
                TrustedComputer(owner="david", computer="davidpc", is_local=True),
                TrustedComputer(owner="drew", computer="drewai"),
                TrustedComputer(owner="drew", computer="imac"),
            ]
        )

        lookup = registry.lookup("drew")

        assert lookup.kind is OwnerLookupKind.AMBIGUOUS
        assert {entry.computer for entry in lookup.candidates} == {"drewai", "imac"}

    def test_the_local_computer_wins_over_the_same_owner_elsewhere(self) -> None:
        registry = OwnerRegistry(
            [
                TrustedComputer(owner="drew", computer="drewai", is_local=True),
                TrustedComputer(owner="drew", computer="imac"),
            ]
        )

        lookup = registry.lookup("drew")

        assert lookup.kind is OwnerLookupKind.MATCHED
        assert lookup.computer is not None
        assert lookup.computer.computer == "drewai"

    def test_rejects_two_local_computers(self) -> None:
        with pytest.raises(ValueError):
            OwnerRegistry(
                [
                    TrustedComputer(owner="drew", computer="drewai", is_local=True),
                    TrustedComputer(owner="david", computer="davidpc", is_local=True),
                ]
            )

    def test_rejects_the_same_computer_twice(self) -> None:
        with pytest.raises(ValueError):
            OwnerRegistry(
                [
                    TrustedComputer(owner="drew", computer="drewai"),
                    TrustedComputer(owner="  Drew ", computer="DREWAI"),
                ]
            )

    def test_exposes_the_local_computer_and_known_owners(self) -> None:
        registry = make_registry()

        assert registry.local is not None
        assert registry.local.owner == "david"
        assert registry.owners == ("david", "drew")

    def test_an_empty_registry_has_no_local_computer(self) -> None:
        registry = OwnerRegistry(())

        assert registry.local is None
        assert registry.lookup("drew").kind is OwnerLookupKind.UNKNOWN


class TestParseOwnerPhrase:
    """Only an outright possessive or an ``owner:`` prefix carries owner intent."""

    def test_reads_a_possessive_owner_and_drops_generic_words(self) -> None:
        phrase = parse_owner_phrase("Drew's projects")

        assert phrase.owner_phrase == "Drew"
        assert phrase.terms == ()
        assert phrase.is_pronoun is False

    def test_keeps_the_project_terms_after_the_owner(self) -> None:
        phrase = parse_owner_phrase("David's alpha project")

        assert phrase.owner_phrase == "David"
        assert phrase.terms == ("alpha",)
        assert phrase.text == "alpha"

    def test_accepts_a_curly_apostrophe_and_a_trailing_possessive(self) -> None:
        assert parse_owner_phrase("Drew’s alpha").owner_phrase == "Drew"
        assert parse_owner_phrase("Drews' alpha").owner_phrase == "Drew"

    def test_accepts_an_explicit_owner_prefix(self) -> None:
        phrase = parse_owner_phrase("owner:david alpha")

        assert phrase.owner_phrase == "david"
        assert phrase.terms == ("alpha",)

    def test_a_possessive_pronoun_is_recorded_but_never_becomes_an_owner(self) -> None:
        phrase = parse_owner_phrase("my projects")

        assert phrase.owner_phrase is None
        assert phrase.is_pronoun is True
        assert phrase.pronoun_word == "my"

    def test_a_bare_pronoun_without_an_apostrophe_is_still_a_pronoun(self) -> None:
        assert parse_owner_phrase("his alpha").is_pronoun is True
        assert parse_owner_phrase("their projects").is_pronoun is True

    def test_a_name_without_an_apostrophe_is_a_search_term_not_an_owner(self) -> None:
        phrase = parse_owner_phrase("drews alpha")

        assert phrase.owner_phrase is None
        assert phrase.terms == ("drews", "alpha")

    def test_an_explicit_owner_outranks_a_stray_pronoun(self) -> None:
        phrase = parse_owner_phrase("show me Drew's alpha")

        assert phrase.owner_phrase == "Drew"
        assert phrase.is_pronoun is False
        assert phrase.terms == ("alpha",)

    def test_two_different_owners_conflict(self) -> None:
        phrase = parse_owner_phrase("Drew's David's alpha")

        assert phrase.conflicting is True

    def test_the_same_owner_twice_does_not_conflict(self) -> None:
        assert parse_owner_phrase("Drew's Drew's alpha").conflicting is False

    def test_plain_text_carries_no_owner(self) -> None:
        phrase = parse_owner_phrase("alpha")

        assert phrase.owner_phrase is None
        assert phrase.is_pronoun is False
        assert phrase.terms == ("alpha",)

    def test_empty_text_is_an_empty_phrase(self) -> None:
        phrase = parse_owner_phrase("   ")

        assert phrase.owner_phrase is None
        assert phrase.terms == ()
        assert phrase.text == ""


class TestRemoteOwnerResolution:
    """A remote owner produces a handoff target — never a local folder."""

    def test_davids_computer_sends_drews_project_to_drews_computer(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("Drew's alpha")

        assert isinstance(result, RemoteTargetResolution)
        assert result.kind is ResolutionKind.REMOTE_TARGET
        assert (result.owner, result.computer) == ("drew", "drewai")
        assert result.requested_terms == ("alpha",)

    def test_a_same_named_local_project_is_never_substituted(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        local = resolver.resolve("alpha")
        remote = resolver.resolve("Drew's alpha")

        assert isinstance(local, LocalProjectResolution)
        assert local.working_directory == PurePath(tmp_path / "alpha")
        assert remote.working_directory is None
        assert remote.is_local_available is False

    def test_a_remote_result_is_never_locally_verified(self, tmp_path: Path) -> None:
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("Drew's projects")

        assert isinstance(result, RemoteTargetResolution)
        assert result.is_locally_verified is False
        assert result.availability is Availability.UNKNOWN
        assert result.requested_terms == ()

    def test_a_remote_owner_yields_no_local_search_results(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta")
        resolver = make_resolver(tmp_path)

        snapshot = resolver.search("Drew's projects")

        assert snapshot.projects == ()
        assert len(snapshot.roots) == 1


class TestLocalOwnerResolution:
    """An explicit local owner stays inside that computer's approved roots."""

    def test_davids_own_project_resolves_locally(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("David's alpha")

        assert isinstance(result, LocalProjectResolution)
        assert result.working_directory == PurePath(tmp_path / "alpha")
        assert result.project.identity.owner == "david"

    def test_davids_projects_are_limited_to_davids_roots(self, tmp_path: Path) -> None:
        mine = make_tree(tmp_path / "david", "alpha", "beta")
        theirs = make_tree(tmp_path / "guest", "gamma")
        # A deliberately mixed fixture: the scope filter must exclude a root
        # that is attributed to another owner even though it is on this disk.
        resolver = make_resolver(
            mine,
            extra_roots=(make_fs_root(theirs, key="guest", owner="drew", computer="drewai"),),
        )

        snapshot = resolver.search("David's projects")

        assert [project.identity.name for project in snapshot.projects] == ["alpha", "beta"]
        assert all(project.identity.owner == "david" for project in snapshot.projects)

    def test_an_unscoped_search_still_sees_every_local_root(self, tmp_path: Path) -> None:
        mine = make_tree(tmp_path / "david", "alpha")
        theirs = make_tree(tmp_path / "guest", "gamma")
        resolver = make_resolver(
            mine,
            extra_roots=(make_fs_root(theirs, key="guest", owner="drew", computer="drewai"),),
        )

        names = [project.identity.name for project in resolver.search().projects]

        assert names == ["alpha", "gamma"]

    def test_search_clamps_its_limit(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "beta", "gamma")
        resolver = make_resolver(tmp_path)

        snapshot = resolver.search(limit=2)

        assert len(snapshot.projects) == 2
        assert snapshot.truncated is True


class TestAmbiguousOwnerHandling:
    """Anything the aliases cannot settle asks for an explicit owner."""

    def test_an_owner_on_two_computers_asks_instead_of_choosing(self, tmp_path: Path) -> None:
        registry = OwnerRegistry(
            [
                TrustedComputer(owner="david", computer="davidpc", is_local=True),
                TrustedComputer(owner="drew", computer="drewai"),
                TrustedComputer(owner="drew", computer="imac"),
            ]
        )
        resolver = make_resolver(tmp_path, registry=registry)

        result = resolver.resolve("Drew's alpha")

        assert isinstance(result, AmbiguousOwnerResolution)
        assert result.requested_owner == "drew"
        assert result.candidate_computers == ("drewai", "imac")
        assert result.working_directory is None

    def test_an_unknown_owner_lists_the_known_ones(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("Sam's alpha")

        assert isinstance(result, AmbiguousOwnerResolution)
        assert result.requested_owner == "sam"
        assert result.candidate_owners == ("david", "drew")

    def test_a_pronoun_never_selects_a_computer(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("my alpha")

        assert isinstance(result, AmbiguousOwnerResolution)
        assert result.requested_owner == "my"
        assert result.working_directory is None

    def test_two_explicit_owners_in_one_request_are_ambiguous(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("Drew's David's alpha")

        assert isinstance(result, AmbiguousOwnerResolution)

    def test_an_explicit_owner_argument_bypasses_the_phrase(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("my alpha", owner="david")

        assert isinstance(result, LocalProjectResolution)
        assert result.project.identity.name == "alpha"


class TestSameNamedLocalProjects:
    """Two local folders with one name are reported, never silently picked."""

    def test_a_duplicate_name_reports_both_identities(self, tmp_path: Path) -> None:
        first = make_tree(tmp_path / "one", "alpha")
        second = make_tree(tmp_path / "two", "alpha")
        resolver = make_resolver(
            first,
            extra_roots=(make_fs_root(second, key="extra", owner="david", computer="davidpc"),),
        )

        result = resolver.resolve("alpha")

        assert isinstance(result, AmbiguousOwnerResolution)
        assert result.working_directory is None
        assert {identity.root_key for identity in result.candidate_identities} == {"main", "extra"}

    def test_both_duplicates_stay_selectable_with_distinct_labels(self, tmp_path: Path) -> None:
        first = make_tree(tmp_path / "one", "alpha")
        second = make_tree(tmp_path / "two", "alpha")
        resolver = make_resolver(
            first,
            extra_roots=(make_fs_root(second, key="extra", owner="david", computer="davidpc"),),
        )

        projects = resolver.search("alpha").projects
        labels = disambiguated_labels(projects)

        assert len(projects) == 2
        assert len(set(labels.values())) == 2

    def test_a_duplicate_elsewhere_does_not_block_a_unique_name(self, tmp_path: Path) -> None:
        first = make_tree(tmp_path / "one", "alpha", "beta")
        second = make_tree(tmp_path / "two", "alpha")
        resolver = make_resolver(
            first,
            extra_roots=(make_fs_root(second, key="extra", owner="david", computer="davidpc"),),
        )

        result = resolver.resolve("beta")

        assert isinstance(result, LocalProjectResolution)
        assert result.working_directory == PurePath(first / "beta")


class TestLocalResolutionAvailability:
    """Local answers are revalidated, so a stale folder never becomes a path."""

    def test_an_exact_name_beats_a_longer_partial_match(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha", "alpha-two")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("alpha")

        assert isinstance(result, LocalProjectResolution)
        assert result.project.identity.name == "alpha"

    def test_nothing_matching_reports_no_match_with_the_query(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("David's zeta")

        assert isinstance(result, NoMatchResolution)
        assert result.query.text == "zeta"
        assert result.query.owner == "david"
        assert result.working_directory is None

    def test_a_cached_project_deleted_since_the_scan_is_unavailable(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path, cache_ttl=300.0)
        assert resolver.resolve("alpha").is_local_available

        (tmp_path / "alpha").rmdir()
        result = resolver.resolve("alpha")

        assert isinstance(result, LocalUnavailableResolution)
        assert result.availability is Availability.MISSING
        assert result.working_directory is None

    def test_a_refreshed_query_drops_the_deleted_project_entirely(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path, cache_ttl=300.0)
        assert resolver.resolve("alpha").is_local_available

        (tmp_path / "alpha").rmdir()
        result = resolver.resolve("alpha", refresh=True)

        assert isinstance(result, NoMatchResolution)

    def test_resolution_is_case_insensitive_on_the_name(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "Alpha")
        resolver = make_resolver(tmp_path)

        result = resolver.resolve("ALPHA")

        assert isinstance(result, LocalProjectResolution)
        assert result.project.identity.name == "Alpha"

    @pytest.mark.asyncio
    async def test_the_async_resolver_returns_the_same_answer(self, tmp_path: Path) -> None:
        make_tree(tmp_path, "alpha")
        resolver = make_resolver(tmp_path)

        result = await resolver.resolve_async("David's alpha")
        snapshot = await resolver.search_async("David's projects")

        assert isinstance(result, LocalProjectResolution)
        assert [project.identity.name for project in snapshot.projects] == ["alpha"]
