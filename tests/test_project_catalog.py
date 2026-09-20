"""Domain model tests for the shared project catalog (OpenSpec task 1.1).

These cover identity, availability, bounded queries, and the five typed
resolution results.  Directory scanning belongs to task 1.2 and is absent here:
nothing in this module touches the filesystem.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from claude_discord.project_catalog import (
    MAX_QUERY_LIMIT,
    AmbiguousOwnerResolution,
    ApprovedRoot,
    Availability,
    CatalogProject,
    CatalogQuery,
    CatalogSnapshot,
    LocalProjectResolution,
    LocalUnavailableResolution,
    NoMatchResolution,
    ProjectIdentity,
    RemoteTargetResolution,
    ResolutionKind,
    RootStatus,
    disambiguated_labels,
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
