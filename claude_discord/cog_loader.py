"""Dynamic loader for custom Cogs from external directories.

Consumers place Cog files in a directory and point ccdb at it via
``CUSTOM_COGS_DIR`` env or ``--cogs-dir`` CLI flag.  Each file must
expose an ``async def setup(bot, runner, components)`` entry point.

The loader is also the one witness to what actually happened to each file:
whether ``setup()`` ran and which Cogs it added, whether it raised, or whether
the file had no ``setup()`` at all.  That record is a :class:`CogLoadReport`,
kept in :func:`last_load_report` so My AI Setup can call a Cog *verified
loaded* only when this loader says so.  The report carries paths, statuses,
Cog names and exception *types* — never file content or exception text.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from claude_code_core.backend import SessionBackend

    from .setup import BridgeComponents

logger = logging.getLogger(__name__)


class CogLoadStatus(StrEnum):
    """What the loader did with one file."""

    LOADED = "loaded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class CogLoadEntry:
    """One file's fate: its status, the Cogs it added, the error type if any."""

    path: Path
    status: CogLoadStatus
    cogs: tuple[str, ...]
    loaded_at: datetime
    error: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self.status is CogLoadStatus.LOADED


@dataclass(slots=True)
class CogLoadReport:
    """Everything one run of :func:`load_custom_cogs` observed."""

    directory: Path
    finished_at: datetime
    entries: tuple[CogLoadEntry, ...] = ()

    @classmethod
    def empty(cls, directory: Path) -> CogLoadReport:
        return cls(directory=Path(directory), finished_at=datetime.now(UTC))

    def record(self, entry: CogLoadEntry) -> None:
        self.entries = (*self.entries, entry)

    def entry_for(self, path: Path) -> CogLoadEntry | None:
        wanted = Path(path)
        for entry in self.entries:
            if entry.path == wanted:
                return entry
        return None

    @property
    def loaded(self) -> tuple[CogLoadEntry, ...]:
        return tuple(entry for entry in self.entries if entry.is_loaded)


_LAST_REPORT: CogLoadReport | None = None


def last_load_report() -> CogLoadReport | None:
    """The most recent loader run in this process, or ``None`` before any."""
    return _LAST_REPORT


def _cog_names(bot: object) -> frozenset[str]:
    """The Cog names a bot currently holds; a mock or odd bot counts as none."""
    cogs = getattr(bot, "cogs", None)
    try:
        return frozenset(str(name) for name in cogs) if cogs is not None else frozenset()
    except TypeError:
        return frozenset()


async def load_custom_cogs(
    cogs_dir: Path,
    bot: Bot,
    runner: SessionBackend | None,
    components: BridgeComponents,
    *,
    report: CogLoadReport | None = None,
) -> int:
    """Load custom Cog files from *cogs_dir*.

    Each ``.py`` file (excluding ``_``-prefixed files) must define::

        async def setup(bot, runner, components):
            await bot.add_cog(MyCog(bot, ...))

    A single Cog's failure is logged and skipped — it never prevents
    other Cogs from loading.

    Args:
        cogs_dir: Directory containing ``.py`` Cog files.
        bot: Discord bot instance.
        runner: ClaudeRunner (may be ``None`` if Claude chat is disabled).
        components: BridgeComponents from ``setup_bridge()``.
        report: Where to record each file's fate; a fresh report is made (and
            published through :func:`last_load_report`) when omitted.

    Returns:
        Number of successfully loaded Cog files.
    """
    global _LAST_REPORT
    if report is None:
        report = CogLoadReport.empty(cogs_dir)
    _LAST_REPORT = report

    if not cogs_dir.is_dir():
        logger.warning("Custom cogs directory does not exist: %s", cogs_dir)
        return 0

    files = sorted(
        p for p in cogs_dir.iterdir() if p.suffix == ".py" and not p.name.startswith("_")
    )

    if not files:
        logger.info("No custom cog files found in %s", cogs_dir)
        return 0

    loaded = 0
    for path in files:
        module_name = f"_ccdb_custom_cog_{path.stem}"
        before = _cog_names(bot)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                logger.error("Failed to create module spec for %s", path)
                report.record(
                    CogLoadEntry(path, CogLoadStatus.FAILED, (), _now(), error="ImportError")
                )
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            setup_fn = getattr(module, "setup", None)
            if setup_fn is None:
                logger.warning("No setup() function in %s — skipped", path.name)
                report.record(CogLoadEntry(path, CogLoadStatus.SKIPPED, (), _now()))
                continue

            await setup_fn(bot, runner, components)
            loaded += 1
            added = tuple(sorted(_cog_names(bot) - before))
            report.record(CogLoadEntry(path, CogLoadStatus.LOADED, added, _now()))
            logger.info("Loaded custom cog: %s", path.name)

        except Exception as error:
            logger.exception("Failed to load custom cog: %s", path.name)
            report.record(
                CogLoadEntry(path, CogLoadStatus.FAILED, (), _now(), error=type(error).__name__)
            )
            # Clean up partial module registration
            sys.modules.pop(module_name, None)

    report.finished_at = _now()
    logger.info("Custom cogs loaded: %d/%d from %s", loaded, len(files), cogs_dir)
    return loaded


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "CogLoadEntry",
    "CogLoadReport",
    "CogLoadStatus",
    "last_load_report",
    "load_custom_cogs",
]
