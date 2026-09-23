"""Discord extension adapter tests (task 2.3): Cogs and commands from loader evidence.

The Cog loader is the only thing that knows whether a custom Cog file was
loaded, failed, or had no ``setup()``; the command tree is the only thing that
knows which slash commands are registered and by which module.  The adapter
turns those two facts into items and refuses to call anything *verified* that
the loader did not actually add.  Framework commands are inventoried as
built-ins so they stay out of the default view.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.ai_setup_adapters import (
    FRAMEWORK_SOURCE_KEY,
    CommandFact,
    DiscordExtensionAdapter,
    cogs_dir_root,
    command_facts,
)
from claude_discord.ai_setup_collector import CollectionContext, InventoryCollector, SetupAdapter
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    InventoryItem,
    ScopeKind,
    SetupKind,
)
from claude_discord.cog_loader import (
    CogLoadEntry,
    CogLoadReport,
    CogLoadStatus,
    last_load_report,
    load_custom_cogs,
)

NOW = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
LEAKED = "ghp_abcdefghijklmnopqrstuv0123456789"  # noqa: S105 — a fake, proves redaction


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def context() -> CollectionContext:
    return CollectionContext(computer="drewai", owner="drew", collected_at=NOW)


def collect(adapter: SetupAdapter):
    collector = InventoryCollector()
    collector.register(adapter)
    return collector.collect(context())


def by_name(items: tuple[InventoryItem, ...], kind: SetupKind, name: str) -> InventoryItem:
    for item in items:
        if item.kind is kind and item.identity.name == name:
            return item
    raise AssertionError(f"no {kind.value} named {name!r} in {[i.identity.key for i in items]}")


def all_text(result) -> str:
    return repr(result.snapshot.items) + repr(result.snapshot.diagnostics)


@pytest.fixture
def cogs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "home" / "ebibot" / "cogs"
    write(
        directory / "reminder.py",
        f'''
        TOKEN = "{LEAKED}"


        async def setup(bot, runner, components):
            bot.cogs["ReminderCog"] = object()
        ''',
    )
    write(
        directory / "broken.py",
        """
        async def setup(bot, runner, components):
            raise RuntimeError("boom")
        """,
    )
    write(directory / "helper_only.py", "x = 1\n")
    write(directory / "_private.py", "async def setup(bot, runner, components): pass\n")
    write(directory / "never_loaded.py", "async def setup(bot, runner, components): pass\n")
    return directory


class TestLoaderReport:
    async def test_the_loader_records_what_happened_to_each_file(self, cogs_dir: Path):
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        bot.cogs = {}
        loaded = await load_custom_cogs(cogs_dir, bot, None, MagicMock())
        assert loaded == 2  # reminder and never_loaded; `_private` is skipped silently
        report = last_load_report()
        assert report is not None
        assert report.directory == cogs_dir
        by_stem = {entry.path.stem: entry for entry in report.entries}
        assert by_stem["reminder"].status is CogLoadStatus.LOADED
        assert by_stem["reminder"].cogs == ("ReminderCog",)
        assert by_stem["broken"].status is CogLoadStatus.FAILED
        assert by_stem["broken"].error == "RuntimeError"
        assert by_stem["helper_only"].status is CogLoadStatus.SKIPPED
        assert "_private" not in by_stem
        assert all(entry.loaded_at.tzinfo is not None for entry in report.entries)
        assert LEAKED not in repr(report)

    async def test_a_report_can_be_requested_explicitly(self, cogs_dir: Path):
        bot = MagicMock()
        bot.add_cog = AsyncMock()
        bot.cogs = {}
        report = CogLoadReport.empty(cogs_dir)
        await load_custom_cogs(cogs_dir, bot, None, MagicMock(), report=report)
        assert {entry.path.name for entry in report.entries} >= {"reminder.py", "broken.py"}


def report_for(cogs_dir: Path) -> CogLoadReport:
    return CogLoadReport(
        directory=cogs_dir,
        finished_at=NOW,
        entries=(
            CogLoadEntry(cogs_dir / "reminder.py", CogLoadStatus.LOADED, ("ReminderCog",), NOW),
            CogLoadEntry(
                cogs_dir / "broken.py", CogLoadStatus.FAILED, (), NOW, error="RuntimeError"
            ),
            CogLoadEntry(cogs_dir / "helper_only.py", CogLoadStatus.SKIPPED, (), NOW),
        ),
    )


def adapter_for(cogs_dir: Path, tmp_path: Path, **kwargs: object) -> DiscordExtensionAdapter:
    root = cogs_dir_root(cogs_dir, home=tmp_path / "home")
    return DiscordExtensionAdapter(root, **kwargs)  # pyright: ignore[reportArgumentType]


class TestCogItems:
    def test_declares_its_boundary(self, cogs_dir: Path, tmp_path: Path):
        adapter = adapter_for(cogs_dir, tmp_path)
        assert adapter.name == "discord-extensions"
        assert set(adapter.kinds) == {SetupKind.PLUGIN, SetupKind.COMMAND}
        assert list(adapter.source_keys) == ["custom-cogs", FRAMEWORK_SOURCE_KEY]
        assert isinstance(adapter, SetupAdapter)

    def test_a_loaded_cog_is_verified_by_the_loader(self, cogs_dir: Path, tmp_path: Path):
        result = collect(adapter_for(cogs_dir, tmp_path, report=report_for(cogs_dir)))
        assert result.failed_adapters == ()
        cog = by_name(result.snapshot.items, SetupKind.PLUGIN, "reminder")
        assert cog.classification is Classification.CUSTOM
        assert cog.scope.kind is ScopeKind.COMPUTER
        assert cog.source.locator == "~/ebibot/cogs/reminder.py"
        assert cog.is_verified_on("ccdb")
        entry = cog.availability_for("ccdb")
        assert entry is not None and entry.verified_at == NOW
        assert "ReminderCog" in (entry.evidence or "")
        assert cog.summary == "loaded: ReminderCog"
        assert cog.fingerprint is not None
        assert cog.measurement.has_size
        assert LEAKED not in all_text(result)

    def test_a_failed_cog_is_discovered_not_verified_and_diagnosed(
        self, cogs_dir: Path, tmp_path: Path
    ):
        result = collect(adapter_for(cogs_dir, tmp_path, report=report_for(cogs_dir)))
        broken = by_name(result.snapshot.items, SetupKind.PLUGIN, "broken")
        assert not broken.has_verified_availability
        assert broken.state_for("ccdb") is AvailabilityState.DISCOVERED
        assert "RuntimeError" in (broken.availability_for("ccdb").detail or "")  # type: ignore[union-attr]
        problems = [d for d in result.snapshot.diagnostics if d.identity == broken.identity]
        assert problems and problems[0].is_error

    def test_a_skipped_and_an_unloaded_file_are_not_verified(self, cogs_dir: Path, tmp_path: Path):
        result = collect(adapter_for(cogs_dir, tmp_path, report=report_for(cogs_dir)))
        skipped = by_name(result.snapshot.items, SetupKind.PLUGIN, "helper_only")
        assert not skipped.has_verified_availability
        assert "setup()" in (skipped.availability_for("ccdb").detail or "")  # type: ignore[union-attr]
        unloaded = by_name(result.snapshot.items, SetupKind.PLUGIN, "never_loaded")
        assert unloaded.state_for("ccdb") is AvailabilityState.DISCOVERED
        assert "not loaded" in (unloaded.availability_for("ccdb").detail or "")  # type: ignore[union-attr]
        names = {item.identity.name for item in result.snapshot.items}
        assert "_private" not in names

    def test_without_a_report_every_file_is_only_discovered(self, cogs_dir: Path, tmp_path: Path):
        result = collect(adapter_for(cogs_dir, tmp_path, report=None))
        assert not any(item.has_verified_availability for item in result.snapshot.items)
        assert by_name(result.snapshot.items, SetupKind.PLUGIN, "reminder")

    def test_a_report_provider_is_read_at_collection_time(self, cogs_dir: Path, tmp_path: Path):
        calls: list[int] = []

        def provider() -> CogLoadReport:
            calls.append(1)
            return report_for(cogs_dir)

        adapter = adapter_for(cogs_dir, tmp_path, report=provider)
        assert calls == []
        result = collect(adapter)
        assert calls == [1]
        assert by_name(result.snapshot.items, SetupKind.PLUGIN, "reminder").is_verified_on("ccdb")

    def test_a_missing_directory_is_a_diagnostic(self, tmp_path: Path):
        adapter = DiscordExtensionAdapter(cogs_dir_root(tmp_path / "none"))
        result = collect(adapter)
        assert result.failed_adapters == ()
        assert result.snapshot.items == ()
        assert result.snapshot.diagnostics_for("custom-cogs")


class TestCommands:
    def test_custom_commands_are_verified_and_framework_commands_are_builtin(
        self, cogs_dir: Path, tmp_path: Path
    ):
        facts = (
            CommandFact("remind", module="_ccdb_custom_cog_reminder", description="Set one"),
            CommandFact("new", module="claude_discord.cogs.surface_commands"),
            CommandFact("gowork", module="ebibot.cogs.gowork", description="Instance command"),
        )
        result = collect(
            adapter_for(cogs_dir, tmp_path, report=report_for(cogs_dir), commands=facts)
        )
        items = result.snapshot.items

        remind = by_name(items, SetupKind.COMMAND, "/remind")
        assert remind.classification is Classification.CUSTOM
        assert remind.source.locator == "~/ebibot/cogs/reminder.py"
        assert remind.source.key == "custom-cogs"
        assert remind.is_verified_on("ccdb")
        assert remind.summary == "Set one"

        new = by_name(items, SetupKind.COMMAND, "/new")
        assert new.classification is Classification.BUILTIN
        assert new.source.key == FRAMEWORK_SOURCE_KEY
        assert new not in result.snapshot.custom_items
        assert new in result.snapshot.visible_items(include_builtins=True)

        gowork = by_name(items, SetupKind.COMMAND, "/gowork")
        assert gowork.classification is Classification.CUSTOM
        assert gowork.source.locator == "ebibot.cogs.gowork"

    def test_commands_can_be_read_from_a_tree(self):
        tree = SimpleNamespace(
            get_commands=lambda: [
                SimpleNamespace(name="remind", module="_ccdb_custom_cog_reminder", description="x"),
                SimpleNamespace(
                    name="help", module="claude_discord.cogs.claude_chat", description=""
                ),
                SimpleNamespace(name="odd", module=None, description=None),
            ]
        )
        facts = command_facts(tree)
        assert [fact.name for fact in facts] == ["remind", "help", "odd"]
        assert facts[0].is_custom and not facts[1].is_custom
        assert facts[2].module is None and facts[2].is_custom

    def test_a_command_provider_is_read_at_collection_time(self, cogs_dir: Path, tmp_path: Path):
        adapter = adapter_for(
            cogs_dir,
            tmp_path,
            commands=lambda: (CommandFact("late", module="_ccdb_custom_cog_reminder"),),
        )
        result = collect(adapter)
        assert by_name(result.snapshot.items, SetupKind.COMMAND, "/late")
