"""The local inventory: which roots this computer declares and the collector over them.

Production reads ``~/.claude``, the Codex home (``CODEX_HOME`` or
``~/.codex``), the DSH configuration directory, the custom Cogs directory
and the bot's working directory as one project; tests hand in fixture
directories through :class:`LocalRoots` instead.  Nothing here touches the
real home unless :meth:`LocalRoots.from_env` is asked to.

Environment (all optional):

* ``CCDB_AI_SETUP_OWNER`` — the profile owner (default: the OS user name).
* ``CODEX_HOME`` / ``CUSTOM_COGS_DIR`` — as the rest of ccdb reads them.
* ``CCDB_DSH_PATCH`` / ``XDG_CONFIG_HOME`` — locate the DSH configuration.
* ``CCDB_AI_SETUP_EXCEPTIONS`` — a JSON list of deliberate exceptions.
* ``CCDB_AI_SETUP_TRUSTED_COMPUTERS`` — comma list of computers whose
  snapshots Compare computers shows (default: every stored snapshot, which
  only trusted ingestion can have written).
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_discord.ai_setup_adapters import (
    ClaudeHarnessAdapter,
    CodexHarnessAdapter,
    CommandFact,
    DeliberateException,
    DiscordExtensionAdapter,
    DshHarnessAdapter,
    HarnessProfile,
    SetupRoot,
    SharedSetupAdapter,
    claude_home_root,
    codex_home_root,
    cogs_dir_root,
    command_facts,
    dsh_config_root,
    load_exceptions,
    project_root,
)
from claude_discord.ai_setup_collector import InventoryCollector


def _dsh_config_dir(env: Mapping[str, str], home: Path) -> Path:
    override = (env.get("CCDB_DSH_PATCH") or "").strip()
    if override:
        return Path(override).parent
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / "ccdb" / "dsh"


@dataclass(frozen=True, slots=True)
class LocalRoots:
    """The directories this computer's inventory may read, declared once."""

    home: Path
    claude_home: Path
    codex_home: Path
    dsh_config: Path
    cogs_dir: Path | None
    project: Path | None
    owner: str
    exceptions_file: Path | None = None

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        home: Path | None = None,
        working_dir: str | None = None,
    ) -> LocalRoots:
        env = os.environ if env is None else env
        home = Path(home) if home is not None else Path.home()
        cogs = (env.get("CUSTOM_COGS_DIR") or "").strip()
        exceptions = (env.get("CCDB_AI_SETUP_EXCEPTIONS") or "").strip()
        owner = (env.get("CCDB_AI_SETUP_OWNER") or "").strip() or _os_user()
        return cls(
            home=home,
            claude_home=home / ".claude",
            codex_home=Path(env.get("CODEX_HOME") or home / ".codex"),
            dsh_config=_dsh_config_dir(env, home),
            cogs_dir=Path(cogs) if cogs else None,
            project=Path(working_dir) if working_dir else None,
            owner=owner,
            exceptions_file=Path(exceptions) if exceptions else None,
        )

    def setup_roots(self) -> tuple[SetupRoot, ...]:
        roots: list[SetupRoot] = [
            claude_home_root(self.claude_home, owner=self.owner, home=self.home),
            codex_home_root(self.codex_home, owner=self.owner, home=self.home),
        ]
        if self.project is not None:
            roots.append(project_root(self.project, name=self.project.name or "project"))
        return tuple(roots)

    def exceptions(self) -> tuple[DeliberateException, ...]:
        if self.exceptions_file is None:
            return ()
        return load_exceptions(self.exceptions_file)


def _os_user() -> str:
    try:
        return getpass.getuser() or "owner"
    except Exception:  # noqa: BLE001 — a headless service may have no user name
        return "owner"


def build_local_collector(
    roots: LocalRoots,
    *,
    environ: Mapping[str, str] | None = None,
    tree: Any | None = None,
    commands: Callable[[], Iterable[CommandFact]] | None = None,
    profiles: Iterable[HarnessProfile] = (),
) -> InventoryCollector:
    """One collector with every local adapter registered against ``roots``."""
    by_harness = {profile.harness: profile for profile in profiles}
    exceptions = roots.exceptions()
    if exceptions:
        for harness in ("claude", "codex", "dsh"):
            existing = by_harness.get(harness)
            by_harness[harness] = HarnessProfile(
                harness=harness,
                configured=existing.configured if existing else True,
                exceptions=(*(existing.exceptions if existing else ()), *exceptions),
            )
    env = os.environ if environ is None else environ
    collector = InventoryCollector()
    setup_roots = roots.setup_roots()
    collector.register(SharedSetupAdapter(setup_roots))
    collector.register(
        ClaudeHarnessAdapter(setup_roots[0], profile=by_harness.get("claude"), environ=env)
    )
    for root in setup_roots[2:]:
        collector.register(
            _ProjectClaudeAdapter(root, profile=by_harness.get("claude"), environ=env)
        )
    collector.register(
        CodexHarnessAdapter(setup_roots[1], profile=by_harness.get("codex"), environ=env)
    )
    collector.register(
        DshHarnessAdapter(
            dsh_config_root(roots.dsh_config, owner=roots.owner, home=roots.home),
            profile=by_harness.get("dsh"),
            environ=env,
        )
    )
    if roots.cogs_dir is not None:
        source: Callable[[], Iterable[CommandFact]] | tuple[CommandFact, ...] = ()
        if commands is not None:
            source = commands
        elif tree is not None:
            captured = tree
            source = lambda: command_facts(captured)  # noqa: E731 — read at collection time
        collector.register(
            DiscordExtensionAdapter(cogs_dir_root(roots.cogs_dir, home=roots.home), commands=source)
        )
    return collector


class _ProjectClaudeAdapter(ClaudeHarnessAdapter):
    """The Claude adapter over a project root, under its own registry name."""

    def __init__(self, root: SetupRoot, **kwargs: Any) -> None:
        super().__init__(root, **kwargs)
        self.name = f"claude-{root.key}"


__all__ = ["LocalRoots", "build_local_collector"]
