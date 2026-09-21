"""Inventory adapters for My AI Setup: each reads one declared boundary.

* :mod:`shared` — instructions, memory and skills, the kinds every harness
  keeps in the same places.
* :mod:`harness` — what is specific to Claude Code, Codex and DSH: commands,
  hooks, plugins, connectors and harness settings.
* :mod:`discord_ext` — custom Cogs and user-added Discord commands, from the
  loader's own evidence.

Every adapter takes the roots it may read as arguments, so tests hand it
fixture homes and production hands it the real ones; none of them writes.
"""

from __future__ import annotations

from ._files import (
    FileFacts,
    HarnessLayout,
    SetupRoot,
    SourceError,
    claude_home_root,
    codex_home_root,
    dsh_config_root,
    project_root,
    read_file_facts,
)
from .harness import (
    ALL_HARNESSES,
    DSH_ROUTES,
    HARNESS_KINDS,
    ClaudeHarnessAdapter,
    CodexHarnessAdapter,
    DeliberateException,
    DshHarnessAdapter,
    HarnessProfile,
    LoaderEvidence,
    load_exceptions,
)
from .shared import KNOWN_HARNESSES, SharedSetupAdapter, availability_for

__all__ = [
    "ALL_HARNESSES",
    "DSH_ROUTES",
    "HARNESS_KINDS",
    "KNOWN_HARNESSES",
    "ClaudeHarnessAdapter",
    "CodexHarnessAdapter",
    "DeliberateException",
    "DshHarnessAdapter",
    "FileFacts",
    "HarnessLayout",
    "HarnessProfile",
    "LoaderEvidence",
    "SetupRoot",
    "SharedSetupAdapter",
    "SourceError",
    "availability_for",
    "claude_home_root",
    "codex_home_root",
    "dsh_config_root",
    "load_exceptions",
    "project_root",
    "read_file_facts",
]
