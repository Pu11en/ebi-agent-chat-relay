"""Query tests (task 3.3): deterministic views over snapshots, no model, no Discord.

Browse by kind, Where it lives, Compare computers, search and filters, Recent
changes and the explicit built-in filter are all pure functions of one or
more snapshots.  The tests pin the ordering, the paging arithmetic, and the
honesty rules for what is not known: an unmeasured item says *unknown*, an
undated item is listed apart rather than ranked, and nothing ever renders
the internal ownership vocabulary.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from claude_discord.ai_setup_adapters import DeliberateException
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    ContentFingerprint,
    EffectiveScope,
    Freshness,
    HarnessAvailability,
    InventoryItem,
    InventorySnapshot,
    InventorySource,
    ItemIdentity,
    Measurement,
    OwnershipClass,
    Prerequisite,
    PrerequisiteKind,
    PrerequisiteState,
    ScopeKind,
    SetupKind,
)
from claude_discord.ai_setup_queries import (
    InventoryFilter,
    browse_by_kind,
    compare_computers,
    detail_facts,
    paginate,
    recent_changes,
    search,
    where_it_lives,
)
from claude_discord.ai_setup_remote import ComparisonStatus
from claude_discord.database.ai_setup_repo import ObservedChange

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)


def item(
    name: str,
    kind: SetupKind = SetupKind.SKILL,
    *,
    computer: str = "drewai",
    scope: EffectiveScope | None = None,
    source_key: str = "claude-home",
    source_label: str = "Claude home",
    classification: Classification = Classification.CUSTOM,
    changed: datetime | None = None,
    measurement: Measurement | None = None,
    digest: str = "ab" * 32,
    harness: str = "claude",
    state: AvailabilityState = AvailabilityState.DISCOVERED,
    summary: str | None = None,
) -> InventoryItem:
    return InventoryItem(
        identity=ItemIdentity(kind=kind, source_key=source_key, name=name),
        display_name=name,
        source=InventorySource(
            key=source_key,
            computer=computer,
            label=source_label,
            locator=f"~/{source_key}/{name}",
            harness=harness,
            modified_at=changed,
        ),
        scope=scope or EffectiveScope.shared_profile("drew"),
        classification=classification,
        ownership=OwnershipClass.MEGA_GLOBAL,
        availability=(HarnessAvailability(harness=harness, state=state, computer=computer),),
        measurement=measurement or Measurement.unknown(),
        fingerprint=ContentFingerprint(digest=digest),
        last_changed_at=changed,
        summary=summary,
    )


def local_snapshot() -> InventorySnapshot:
    return InventorySnapshot(
        computer="drewai",
        owner="drew",
        collected_at=NOW,
        items=(
            item("grilling", changed=NOW - timedelta(days=1)),
            item("Baking", changed=NOW - timedelta(days=3), summary="Bread and cake"),
            item("zesting"),
            item(
                "CLAUDE.md",
                SetupKind.INSTRUCTION,
                scope=EffectiveScope.everywhere(),
                changed=NOW - timedelta(hours=2),
                measurement=Measurement.estimated(
                    byte_size=4000, character_count=3900, token_count=975
                ),
            ),
            item(
                "verify",
                SetupKind.COMMAND,
                scope=EffectiveScope.one_computer("drewai"),
                source_key="custom-cogs",
                source_label="Custom Cogs",
                harness="ccdb",
            ),
            item(
                "ship",
                SetupKind.COMMAND,
                scope=EffectiveScope.one_project("ccdb"),
                source_key="project-ccdb",
                source_label="Project ccdb",
            ),
            item(
                "docs@official",
                SetupKind.PLUGIN,
                classification=Classification.OVERRIDDEN_BUILTIN,
                changed=NOW - timedelta(days=10),
            ),
            item("x@official", SetupKind.PLUGIN, classification=Classification.BUILTIN),
            item(
                "remote",
                SetupKind.CONNECTOR,
                harness="codex",
                state=AvailabilityState.MISSING_PREREQUISITE,
                scope=EffectiveScope.other_profile("david"),
            ),
        ),
    )


class TestBrowseByKind:
    def test_groups_custom_items_by_kind_in_kind_order_sorted_by_name(self):
        groups = browse_by_kind(local_snapshot(), InventoryFilter())
        assert [group.kind for group in groups] == [
            SetupKind.INSTRUCTION,
            SetupKind.SKILL,
            SetupKind.PLUGIN,
            SetupKind.CONNECTOR,
            SetupKind.COMMAND,
        ]
        skills = next(group for group in groups if group.kind is SetupKind.SKILL)
        assert [entry.display_name for entry in skills.items] == ["Baking", "grilling", "zesting"]
        assert skills.label == "Skills"
        assert skills.count == 3
        plugins = next(group for group in groups if group.kind is SetupKind.PLUGIN)
        assert [entry.display_name for entry in plugins.items] == ["docs@official"]
        assert plugins.builtin_count == 0

    def test_builtins_appear_only_when_asked_and_do_not_change_custom_counts(self):
        groups = browse_by_kind(local_snapshot(), InventoryFilter(include_builtins=True))
        plugins = next(group for group in groups if group.kind is SetupKind.PLUGIN)
        assert [entry.display_name for entry in plugins.items] == ["docs@official", "x@official"]
        assert plugins.custom_count == 1
        assert plugins.builtin_count == 1
        assert plugins.count == 2

    def test_total_counts_are_split_by_classification(self):
        groups = browse_by_kind(local_snapshot(), InventoryFilter())
        assert sum(group.count for group in groups) == 8


class TestWhereItLives:
    def test_groups_by_user_facing_scope_then_source_without_internal_vocabulary(self):
        groups = where_it_lives(local_snapshot(), InventoryFilter())
        labels = [group.label for group in groups]
        assert labels == [
            "Everywhere",
            "Shared Drew profile",
            "One computer (drewai)",
            "David's profile",
            "One project (ccdb)",
        ]
        shared = groups[1]
        assert [source.label for source in shared.sources] == ["Claude home"]
        assert [entry.display_name for entry in shared.sources[0].items] == [
            "Baking",
            "docs@official",
            "grilling",
            "zesting",
        ]
        rendered = [group.label for group in groups] + [
            source.label for group in groups for source in group.sources
        ]
        assert all("mega" not in label.lower() for label in rendered)


class TestSearchAndFilters:
    def test_search_matches_name_kind_harness_computer_and_scope(self):
        snapshot = local_snapshot()
        assert [e.display_name for e in search(snapshot, InventoryFilter(query="grill"))] == [
            "grilling"
        ]
        assert {e.display_name for e in search(snapshot, InventoryFilter(query="command"))} == {
            "ship",
            "verify",
        }
        assert [e.display_name for e in search(snapshot, InventoryFilter(query="codex"))] == [
            "remote"
        ]
        assert [e.display_name for e in search(snapshot, InventoryFilter(query="ccdb"))] == [
            "ship",
            "verify",
        ]
        assert {e.display_name for e in search(snapshot, InventoryFilter(query="drewai"))} == {
            entry.display_name for entry in snapshot.custom_items
        }
        assert [e.display_name for e in search(snapshot, InventoryFilter(query="cake"))] == [
            "Baking"
        ]
        assert search(snapshot, InventoryFilter(query="nothing-like-this")) == ()

    def test_kind_scope_harness_and_state_filters_compose(self):
        snapshot = local_snapshot()
        only_commands = InventoryFilter(kinds=frozenset({SetupKind.COMMAND}))
        assert {e.display_name for e in search(snapshot, only_commands)} == {"ship", "verify"}
        project_only = replace(only_commands, scopes=frozenset({ScopeKind.PROJECT}))
        assert [e.display_name for e in search(snapshot, project_only)] == ["ship"]
        codex = InventoryFilter(harness="codex")
        assert [e.display_name for e in search(snapshot, codex)] == ["remote"]
        problems = InventoryFilter(states=frozenset({AvailabilityState.MISSING_PREREQUISITE}))
        assert [e.display_name for e in search(snapshot, problems)] == ["remote"]

    def test_the_filter_describes_itself(self):
        assert InventoryFilter().describe() == "custom additions and overrides"
        described = InventoryFilter(
            query="grill", kinds=frozenset({SetupKind.SKILL}), include_builtins=True
        ).describe()
        assert "grill" in described and "Skills" in described and "built-ins" in described


class TestRecentChanges:
    def test_dated_items_newest_first_and_undated_listed_apart(self):
        result = recent_changes(local_snapshot(), InventoryFilter())
        assert [entry.display_name for entry in result.dated] == [
            "CLAUDE.md",
            "grilling",
            "Baking",
            "docs@official",
        ]
        assert {entry.display_name for entry in result.undated} == {
            "zesting",
            "verify",
            "ship",
            "remote",
        }
        assert result.dated[0].last_changed_at == NOW - timedelta(hours=2)
        assert result.observed == ()

    def test_observed_changes_ride_along_newest_first(self):
        observed = (
            ObservedChange(
                "drewai",
                ItemIdentity.from_key("skill:claude-home:grilling"),
                "changed",
                NOW - timedelta(hours=1),
            ),
            ObservedChange(
                "drewai",
                ItemIdentity.from_key("skill:claude-home:old"),
                "removed",
                NOW - timedelta(days=2),
            ),
        )
        result = recent_changes(local_snapshot(), InventoryFilter(), observed=observed, limit=2)
        assert len(result.dated) == 2
        assert [entry.kind for entry in result.observed] == ["changed", "removed"]


class TestPagination:
    def test_pages_are_deterministic_and_nothing_is_dropped(self):
        entries = tuple(range(23))
        first = paginate(entries, page=0, page_size=10)
        assert first.items == tuple(range(10))
        assert (first.page, first.page_count, first.total) == (0, 3, 23)
        assert first.has_next and not first.has_previous
        last = paginate(entries, page=2, page_size=10)
        assert last.items == (20, 21, 22)
        assert not last.has_next and last.has_previous
        assert last.label == "Page 3 of 3 (23 items)"
        clamped = paginate(entries, page=9, page_size=10)
        assert clamped.page == 2
        empty = paginate((), page=0, page_size=10)
        assert (empty.page_count, empty.items) == (1, ())


class TestCompareComputers:
    def test_wraps_the_remote_comparison_per_computer_with_the_filter_applied(self):
        local = local_snapshot()
        imac = InventorySnapshot(
            computer="imac",
            owner="drew",
            collected_at=NOW - timedelta(days=2),
            items=(
                item("grilling", computer="imac"),
                item("Baking", computer="imac", digest="cd" * 32),
            ),
        )
        comparisons = compare_computers(
            local,
            {"imac": imac, "david": None},
            now=NOW,
            exceptions={"imac": (DeliberateException("plugin:", "Plugins need Max"),)},
            inventory_filter=InventoryFilter(kinds=frozenset({SetupKind.SKILL, SetupKind.PLUGIN})),
        )
        assert [entry.computer for entry in comparisons] == ["david", "imac"]
        david = comparisons[0]
        assert david.freshness is Freshness.UNREACHABLE
        assert {entry.status for entry in david.results} == {ComparisonStatus.UNREACHABLE}
        imac_result = comparisons[1]
        assert imac_result.freshness is Freshness.STALE
        by_name = {entry.display_name: entry for entry in imac_result.results}
        assert set(by_name) == {"grilling", "Baking", "zesting", "docs@official"}
        assert by_name["grilling"].status is ComparisonStatus.MATCHING
        assert by_name["Baking"].status is ComparisonStatus.DIFFERENT
        assert by_name["zesting"].status is ComparisonStatus.MISSING_REMOTE
        assert by_name["docs@official"].status is ComparisonStatus.DELIBERATE
        assert all(entry.stale for entry in imac_result.results)


class TestDetailFacts:
    def test_unknown_measurements_and_times_say_unknown(self):
        facts = detail_facts(item("zesting"))
        as_dict = dict(facts)
        assert as_dict["Kind"] == "Skills"
        assert as_dict["Source"] == "Claude home — ~/claude-home/zesting"
        assert as_dict["Scope"] == "Shared Drew profile"
        assert as_dict["Size"] == "unknown"
        assert as_dict["Tokens"] == "unknown"
        assert as_dict["Last change"] == "unknown (no source modification time)"
        assert as_dict["Availability"] == "claude: Discovered"
        assert as_dict["Prerequisites"] == "none recorded"
        assert "Mega" not in repr(facts) and "mega" not in repr(facts)

    def test_measured_facts_are_labelled_by_method_and_verified_harnesses_named(self):
        measured = replace(
            item(
                "CLAUDE.md",
                SetupKind.INSTRUCTION,
                changed=NOW - timedelta(hours=2),
                measurement=Measurement.estimated(
                    byte_size=4000, character_count=3900, token_count=975
                ),
            ),
            availability=(
                HarnessAvailability(
                    harness="claude",
                    state=AvailabilityState.VERIFIED_LOADED,
                    computer="drewai",
                    evidence="claude --print listed it",
                    verified_at=NOW,
                ),
                HarnessAvailability(
                    harness="codex",
                    state=AvailabilityState.UNSUPPORTED,
                    computer="drewai",
                    detail="Codex reads AGENTS.md",
                ),
            ),
            prerequisites=(
                Prerequisite(
                    name="DOCS_TOKEN",
                    state=PrerequisiteState.MISSING,
                    kind=PrerequisiteKind.CREDENTIAL,
                ),
            ),
        )
        as_dict = dict(detail_facts(measured))
        assert as_dict["Size"] == "4,000 bytes"
        assert as_dict["Tokens"] == "~975 tokens (estimated)"
        assert as_dict["Last change"].startswith("2026-09-21 07:00 UTC (source modification time)")
        assert as_dict["Availability"] == (
            "claude: Verified loaded — claude --print listed it (2026-09-21 09:00 UTC); "
            "codex: Unsupported — Codex reads AGENTS.md"
        )
        assert as_dict["Prerequisites"] == "DOCS_TOKEN: missing (credential)"
        assert as_dict["Content"] == "sha256:abababababab"
