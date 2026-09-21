"""The Discord extension adapter: custom Cogs and user-added slash commands.

Two witnesses, no guessing:

* The **Cog loader's report** (:func:`claude_discord.cog_loader.last_load_report`)
  says which files in ``CUSTOM_COGS_DIR`` ran ``setup()``, which raised and
  which had no ``setup()`` at all.  Only a file the loader reports as loaded
  is *verified loaded* on the ``ccdb`` harness; a failed one is *discovered*
  with the error type as its detail and an error diagnostic, a skipped one is
  *discovered* with the reason, and a file the report never saw (a report
  from before it was added, or no report at all) is *discovered* and says so.
* The **command tree** says which slash commands are registered and by which
  module.  A command from a ``_ccdb_custom_cog_*`` module belongs to that Cog
  file; one from ``claude_discord.*`` is a framework built-in and is inventoried
  as such so it stays out of the default view; anything else is an instance's
  own package and counts as custom.

The adapter reads Cog files only to fingerprint and measure them; the token a
Cog author left in a constant never reaches an item.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from claude_discord.ai_setup_collector import AdapterResult, CollectionContext
from claude_discord.ai_setup_inventory import (
    AvailabilityState,
    Classification,
    DiagnosticSeverity,
    HarnessAvailability,
    InventoryDiagnostic,
    InventoryItem,
    InventorySource,
    ItemIdentity,
    OwnershipClass,
    SetupKind,
    safe_text,
)
from claude_discord.ai_setup_redaction import RedactionError, safe_message
from claude_discord.cog_loader import CogLoadReport, CogLoadStatus, last_load_report

from ._files import HarnessLayout, SetupRoot, SourceError, read_file_facts, sorted_children

#: The harness name Discord-side items report availability under.
DISCORD_HARNESS = "ccdb"
#: Source key for the framework's own commands (built-ins).
FRAMEWORK_SOURCE_KEY = "ccdb-framework"
CUSTOM_MODULE_PREFIX = "_ccdb_custom_cog_"
FRAMEWORK_MODULE_PREFIX = "claude_discord."


@dataclass(frozen=True, slots=True)
class CommandFact:
    """One registered slash command and the module that registered it."""

    name: str
    module: str | None = None
    description: str | None = None

    @property
    def is_framework(self) -> bool:
        return bool(self.module and self.module.startswith(FRAMEWORK_MODULE_PREFIX))

    @property
    def is_custom(self) -> bool:
        return not self.is_framework

    @property
    def cog_stem(self) -> str | None:
        """The Cog file stem when a custom Cog registered this command."""
        if self.module and self.module.startswith(CUSTOM_MODULE_PREFIX):
            return self.module[len(CUSTOM_MODULE_PREFIX) :]
        return None


class _TreeLike(Protocol):
    def get_commands(self) -> Iterable[object]: ...


def command_facts(tree: _TreeLike) -> tuple[CommandFact, ...]:
    """Read the registered top-level commands off a command tree.

    Works on ``discord.app_commands.CommandTree`` and on anything with a
    ``get_commands()`` returning objects that carry ``name``, ``module`` and
    ``description``.
    """
    facts: list[CommandFact] = []
    for command in tree.get_commands():
        name = getattr(command, "name", None)
        if not isinstance(name, str) or not name.strip():
            continue
        module = getattr(command, "module", None)
        description = getattr(command, "description", None)
        facts.append(
            CommandFact(
                name=name.strip(),
                module=module if isinstance(module, str) and module else None,
                description=description if isinstance(description, str) else None,
            )
        )
    return tuple(facts)


ReportSource = CogLoadReport | Callable[[], CogLoadReport | None] | None
CommandSource = Iterable[CommandFact] | Callable[[], Iterable[CommandFact]]


class DiscordExtensionAdapter:
    """Custom Cogs (as plugins) and slash commands, from loader and tree evidence."""

    name = "discord-extensions"
    kinds: tuple[SetupKind, ...] = (SetupKind.PLUGIN, SetupKind.COMMAND)

    def __init__(
        self,
        root: SetupRoot,
        *,
        report: ReportSource = last_load_report,
        commands: CommandSource = (),
    ) -> None:
        if root.layout is not HarnessLayout.COGS_DIR:
            raise ValueError("The Discord extension adapter needs a custom Cogs root")
        self.root = root
        self._report = report
        self._commands = commands

    @property
    def source_keys(self) -> tuple[str, ...]:
        return (self.root.key, FRAMEWORK_SOURCE_KEY)

    def _current_report(self) -> CogLoadReport | None:
        source = self._report
        if callable(source):
            return source()
        return source

    def _current_commands(self) -> tuple[CommandFact, ...]:
        source = self._commands
        return tuple(source() if callable(source) else source)

    def collect(self, context: CollectionContext) -> AdapterResult:
        items: list[InventoryItem] = []
        diagnostics: list[InventoryDiagnostic] = []
        report = self._current_report()
        if report is not None and Path(report.directory) != self.root.path:
            report = None  # A report for another directory is no evidence for this one.
        if self.root.exists:
            self._cogs(context, report, items, diagnostics)
        else:
            diagnostics.append(
                context.diagnostic(
                    self.root.key, DiagnosticSeverity.WARNING, self.root.missing_message()
                )
            )
        self._commands_into(context, items)
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))

    # -- Cogs ---------------------------------------------------------------------

    def _cogs(
        self,
        context: CollectionContext,
        report: CogLoadReport | None,
        items: list[InventoryItem],
        diagnostics: list[InventoryDiagnostic],
    ) -> None:
        for path in sorted_children(self.root.path):
            if path.suffix != ".py" or path.name.startswith("_") or not path.is_file():
                continue
            entry = report.entry_for(path) if report is not None else None
            identity = ItemIdentity(kind=SetupKind.PLUGIN, source_key=self.root.key, name=path.stem)
            try:
                facts = read_file_facts(self.root, path)
            except SourceError as problem:
                diagnostics.append(
                    context.diagnostic(self.root.key, DiagnosticSeverity.WARNING, str(problem))
                )
                continue
            if entry is None:
                availability = HarnessAvailability(
                    harness=DISCORD_HARNESS,
                    state=AvailabilityState.DISCOVERED,
                    computer=context.computer,
                    detail="File present; not loaded in this bot process",
                )
                summary = "not loaded"
            elif entry.status is CogLoadStatus.LOADED:
                names = ", ".join(entry.cogs) if entry.cogs else "setup() ran"
                availability = HarnessAvailability(
                    harness=DISCORD_HARNESS,
                    state=AvailabilityState.VERIFIED_LOADED,
                    computer=context.computer,
                    evidence=safe_text(f"Cog loader added {names}"[:120], kind="evidence"),
                    verified_at=entry.loaded_at,
                )
                summary = safe_text(f"loaded: {names}"[:120], kind="item summary")
            elif entry.status is CogLoadStatus.FAILED:
                error = entry.error or "error"
                availability = HarnessAvailability(
                    harness=DISCORD_HARNESS,
                    state=AvailabilityState.DISCOVERED,
                    computer=context.computer,
                    detail=f"Failed to load ({error})",
                )
                summary = f"failed to load ({error})"
                diagnostics.append(
                    context.diagnostic(
                        self.root.key,
                        DiagnosticSeverity.ERROR,
                        f"Custom Cog {path.name} failed to load ({error}) and is not active",
                        identity=identity,
                    )
                )
            else:
                availability = HarnessAvailability(
                    harness=DISCORD_HARNESS,
                    state=AvailabilityState.DISCOVERED,
                    computer=context.computer,
                    detail="Skipped: the file has no setup() function",
                )
                summary = "skipped: no setup() function"
            items.append(
                InventoryItem(
                    identity=identity,
                    display_name=path.stem,
                    source=self.root.source_for(path, context, modified_at=facts.modified_at),
                    scope=self.root.scope_for(context),
                    classification=Classification.CUSTOM,
                    ownership=OwnershipClass.MACHINE,
                    availability=(availability,),
                    measurement=facts.measurement,
                    fingerprint=facts.fingerprint,
                    last_changed_at=facts.modified_at,
                    summary=summary,
                )
            )

    # -- Commands -----------------------------------------------------------------

    def _commands_into(self, context: CollectionContext, items: list[InventoryItem]) -> None:
        seen: set[str] = set()
        for fact in self._current_commands():
            name = f"/{fact.name}"
            if name in seen:
                continue
            seen.add(name)
            summary = _description(fact.description)
            registered = HarnessAvailability(
                harness=DISCORD_HARNESS,
                state=AvailabilityState.VERIFIED_LOADED,
                computer=context.computer,
                evidence="Registered on the bot's command tree",
                verified_at=context.collected_at,
            )
            if fact.is_framework:
                source = InventorySource(
                    key=FRAMEWORK_SOURCE_KEY,
                    computer=context.computer,
                    label="ccdb framework",
                    locator=fact.module or "claude_discord",
                    harness=DISCORD_HARNESS,
                )
                items.append(
                    InventoryItem(
                        identity=ItemIdentity(
                            kind=SetupKind.COMMAND, source_key=FRAMEWORK_SOURCE_KEY, name=name
                        ),
                        display_name=name,
                        source=source,
                        scope=self.root.scope_for(context),
                        classification=Classification.BUILTIN,
                        ownership=OwnershipClass.MACHINE,
                        availability=(registered,),
                        summary=summary,
                    )
                )
                continue
            stem = fact.cog_stem
            cog_file = self.root.path / f"{stem}.py" if stem else None
            if cog_file is not None:
                source = self.root.source_for(cog_file, context)
            else:
                source = InventorySource(
                    key=self.root.key,
                    computer=context.computer,
                    label=self.root.label,
                    locator=fact.module or "unknown module",
                    harness=DISCORD_HARNESS,
                )
            items.append(
                InventoryItem(
                    identity=ItemIdentity(
                        kind=SetupKind.COMMAND, source_key=self.root.key, name=name
                    ),
                    display_name=name,
                    source=source,
                    scope=self.root.scope_for(context),
                    classification=Classification.CUSTOM,
                    ownership=OwnershipClass.MACHINE,
                    availability=(registered,),
                    summary=summary,
                )
            )


def _description(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    try:
        return safe_message(text, kind="item summary", limit=120)
    except (RedactionError, ValueError):
        return None


__all__ = [
    "CUSTOM_MODULE_PREFIX",
    "DISCORD_HARNESS",
    "FRAMEWORK_MODULE_PREFIX",
    "FRAMEWORK_SOURCE_KEY",
    "CommandFact",
    "DiscordExtensionAdapter",
    "command_facts",
]
