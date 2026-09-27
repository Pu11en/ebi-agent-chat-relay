"""Auto Upgrade — ccdb AutoUpgradeCog configuration for a systemd **user** deployment.

``/upgrade`` (and the ``ebibot-upgrade`` webhook) is the drain-aware safe restart:
it snapshots the threads with a Claude session in flight, waits for them to
finish, marks them to be resumed, and only then restarts the service. That is the
whole point of it — a restart that provably does not throw away someone's work.

Two facts about this deployment shape the configuration, and getting either wrong
silently disables the mechanism rather than reporting an error:

**The service is a systemd user unit.** So no ``sudo``: a password prompt in a
subprocess with no tty does not fail, it hangs until the step timeout. And the
restart cannot be a plain ``systemctl restart`` child — that child lives inside
the cgroup of the unit being stopped, so systemd kills it while its own request
is still in flight. ``systemd-run`` moves it into a transient unit of its own,
outside that cgroup, which is what makes the restart survive its own cause.

**The bot runs *from* its checkout**, not from an installed wheel, and
``scripts/pre-start.sh`` already pulls, syncs (with the optional extras) and rolls
back on a failed import on every start. So there is nothing here for
``uv lock --upgrade-package`` to upgrade — the package *is* this project. The two
steps before the restart are a pre-flight instead: report the commit that is about
to be replaced, then refuse to restart a checkout that cannot even import. A
consumer that installs ccdb as a dependency gets the ordinary upgrade pair.

This file is config-only. Execution logic lives in ccdb's AutoUpgradeCog.

Usage:
    CUSTOM_COGS_DIR=examples/ebibot/cogs ccdb start
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from claude_discord.cogs.auto_upgrade import AutoUpgradeCog, UpgradeConfig

_PACKAGE = "claude-code-discord-bridge"

# The checkout this file ships in: examples/ebibot/cogs/auto_upgrade.py -> repo root.
# A deployment points CUSTOM_COGS_DIR at that directory, so this is the checkout the
# running bot was launched from — unlike the old ``~/discord-bot`` default, which
# named a directory that has never existed here.
_DEFAULT_WORKING_DIR = Path(__file__).resolve().parents[3]
_WORKING_DIR = Path(os.getenv("EBIBOT_WORKING_DIR") or _DEFAULT_WORKING_DIR)

_UV = os.getenv("UV_PATH") or shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
_SERVICE_NAME = os.getenv("EBIBOT_SERVICE_NAME", "ebi-agent-chat-relay.service")
# "user" (default) or "system". A system unit is the only case that needs sudo, and
# a deployment that wants it has to say so rather than inherit it.
_SERVICE_SCOPE = os.getenv("EBIBOT_SERVICE_SCOPE", "user")

# Wait longer than one Claude turn. 300s (the ccdb default) expires mid-turn on
# real work; 480s is what the hand-rolled restart script on this machine uses, and
# marking sessions for resume covers the overrun either way.
UPGRADE_DRAIN_TIMEOUT = int(os.getenv("EBIBOT_DRAIN_TIMEOUT", "480"))


def _restart_command() -> list[str]:
    """Restart the service from *outside* the cgroup that is being torn down."""
    systemd_run = shutil.which("systemd-run") or "/usr/bin/systemd-run"
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    scope = "--user" if _SERVICE_SCOPE == "user" else "--system"
    # --collect reaps the transient unit when it exits, so the fixed name is reusable.
    launcher = [systemd_run, scope, "--collect", "--unit=ccdb-upgrade-restart"]
    inner = [systemctl, scope, "restart", _SERVICE_NAME]
    if scope == "--system":
        inner = ["/usr/bin/sudo", *inner]
    return [*launcher, *inner]


def _is_self_hosted_checkout(working_dir: Path) -> bool:
    """True when *working_dir* is a checkout of ccdb itself rather than a consumer."""
    pyproject = working_dir / "pyproject.toml"
    if not pyproject.is_file():
        return False
    return f'name = "{_PACKAGE}"' in pyproject.read_text(encoding="utf-8")


def _preflight_commands(working_dir: Path) -> tuple[list[str], list[str]]:
    """The two steps that run before the restart, for a self-hosted checkout.

    Neither can damage the running bot and neither can fail spuriously — a failing
    step aborts the pipeline *before* the restart, which would turn a transient
    network error into "the safe restart does not work" all over again.
    """
    git = shutil.which("git") or "/usr/bin/git"
    report = [git, "-C", str(working_dir), "rev-parse", "--short", "HEAD"]
    python = working_dir / ".venv" / "bin" / "python"
    validate = [str(python), "-c", "from claude_discord.main import main"]
    return report, validate


if _is_self_hosted_checkout(_WORKING_DIR):
    _UPGRADE_COMMAND, _SYNC_COMMAND = _preflight_commands(_WORKING_DIR)
else:
    _UPGRADE_COMMAND = [_UV, "lock", "--upgrade-package", _PACKAGE]
    # Without the extras this prunes the voice and DeepSeek dependencies that
    # pre-start.sh installs, and every dsh thread fails on the next start.
    _SYNC_COMMAND = [_UV, "sync", "--extra", "voice", "--extra", "deepseek"]

EBIBOT_UPGRADE_CONFIG = UpgradeConfig(
    package_name=_PACKAGE,
    trigger_prefix="\U0001f504 ebibot-upgrade",
    working_dir=str(_WORKING_DIR),
    upgrade_command=_UPGRADE_COMMAND,
    sync_command=_SYNC_COMMAND,
    restart_command=_restart_command(),
    # The approval gate is answerable only by a ✅ reaction or a button click, so an
    # operator who replies in text waits forever. Running /upgrade is the approval.
    upgrade_approval=False,
    restart_approval=False,
    slash_command_enabled=True,
)


async def setup(bot: object, runner: object, components: object) -> None:
    """Entry point for the custom Cog loader."""
    cog = AutoUpgradeCog(
        bot=bot,  # type: ignore[arg-type]
        config=EBIBOT_UPGRADE_CONFIG,
        # drain_check is left unset on purpose: AutoUpgradeCog auto-discovers every
        # DrainAware Cog on the bot, so a new Cog with in-flight work is waited for
        # without this file having to learn about it.
        drain_timeout=UPGRADE_DRAIN_TIMEOUT,
    )
    await bot.add_cog(cog)  # type: ignore[union-attr]
