"""Shared fixture builders for the professional harness audit tests.

Everything here writes into a ``tmp_path`` handed in by the test; nothing
ever points at the real ``~/.claude`` or ``~/.codex``.  The sanitized
DrewAI-style home built by :func:`fake_home` is the same shape as the checked-in
fixture trees under ``tests/fixtures/harness_audit/``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from extensions.harness_audit.discovery import DiscoveryRoots
from extensions.harness_audit.sources import SourceManifest, load_manifest

FAKE_KEY = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE0000"
FAKE_NUMERIC_PASSWORD = "48213907"  # noqa: S105 - fixture, not a credential
FAKE_SYSTEM_PROMPT = "You are EbiBot. Private operator prompt that must stay local."
MANIFEST_TODAY = date(2026, 9, 21)

GLOBAL_RULES = "# Global rules\n\nAlways run the tests.\n"
PROJECT_RULES = "# Relay project\n\nUse uv.\n"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def manifest() -> SourceManifest:
    """The shipped manifest, pinned to the day it was reviewed so it never expires in tests."""
    return load_manifest(today=MANIFEST_TODAY)


def fake_home(tmp_path: Path, *, with_transcripts: bool = False) -> DiscoveryRoots:
    """A sanitized DrewAI-style home: canonical ~/AGENTS.md plus harness links."""
    home = tmp_path / "home"
    write(home / "AGENTS.md", GLOBAL_RULES)
    write(
        home / ".claude" / "settings.json",
        json.dumps(
            {
                "permissions": {"allow": ["Bash(git:*)"], "deny": ["Read(.env)"]},
                "env": {"ANTHROPIC_API_KEY": FAKE_KEY, "EDITOR": "vim"},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}
                    ]
                },
                "model": "opus",
            }
        ),
    )
    write(
        home / ".claude" / "skills" / "deploy" / "SKILL.md",
        "---\nname: deploy\ndescription: Deploy the bot\n---\n\nSteps for ebi-agent-chat-relay.\n",
    )
    write(home / ".claude" / "commands" / "verify.md", "Run the full verification.\n")
    write(
        home / ".claude.json",
        json.dumps({"mcpServers": {"github": {"command": "gh-mcp"}}, "numStartups": 3}),
    )
    write(
        home / ".codex" / "config.toml",
        'model = "gpt-5-codex"\nmodel_reasoning_effort = "high"\n'
        f'db_password = "{FAKE_NUMERIC_PASSWORD}"\n[mcp_servers.github]\ncommand = "gh-mcp"\n',
    )
    write(
        home / ".codex" / "skills" / "release" / "SKILL.md",
        "---\nname: release\ndescription: Cut a release\n---\n\nRelease steps.\n",
    )
    project = home / "projects" / "relay"
    write(project / "CLAUDE.md", PROJECT_RULES)
    write(project / "AGENTS.md", PROJECT_RULES)
    write(project / ".claude" / "commands" / "verify.md", "Project verify.\n")
    if with_transcripts:
        add_claude_transcript(home, project, version="2.0.5")
        add_codex_rollout(home, project, version="0.147.0", instructions=GLOBAL_RULES)
    return DiscoveryRoots(
        home=home,
        claude_home=home / ".claude",
        codex_home=home / ".codex",
        project_dir=project,
        known_projects=("relay", "ebi-agent-chat-relay"),
        links={
            home / ".claude" / "CLAUDE.md": home / "AGENTS.md",
            home / ".codex" / "AGENTS.md": home / "AGENTS.md",
        },
        modes={
            home / ".claude" / "settings.json": 0o600,
            home / ".codex" / "config.toml": 0o600,
            home / "AGENTS.md": 0o644,
        },
    )


def add_claude_transcript(home: Path, cwd: Path, *, version: str, session: str = "s1") -> Path:
    """A Claude Code session transcript: the metadata lines a real one carries."""
    slug = "-" + cwd.as_posix().replace("/", "-").replace(":", "")
    lines = [
        {
            "type": "user",
            "cwd": cwd.as_posix(),
            "sessionId": session,
            "version": version,
            "message": {"role": "user", "content": "private prompt text"},
        },
        {
            "type": "assistant",
            "cwd": cwd.as_posix(),
            "sessionId": session,
            "version": version,
            "message": {"role": "assistant", "content": "private answer text"},
        },
    ]
    return write(
        home / ".claude" / "projects" / slug / f"{session}.jsonl",
        "".join(json.dumps(line) + "\n" for line in lines),
    )


def add_codex_rollout(
    home: Path,
    cwd: Path,
    *,
    version: str,
    instructions: str,
    skill_names: tuple[str, ...] = ("release", "deploy"),
    name: str = "rollout-2026-09-20T10-00-00-abc.jsonl",
) -> Path:
    """A Codex rollout: session_meta plus the developer turn that names skills."""
    lines = [
        {
            "timestamp": "2026-09-20T10:00:00.000Z",
            "type": "session_meta",
            "payload": {
                "id": "abc",
                "cwd": cwd.as_posix(),
                "cli_version": version,
                "originator": "codex_cli_rs",
                "instructions": instructions,
            },
        },
        {
            "timestamp": "2026-09-20T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Skills available: "
                        + ", ".join(f"<skill name='{skill}'/>" for skill in skill_names),
                    }
                ],
            },
        },
    ]
    return write(
        home / ".codex" / "sessions" / "2026" / "09" / "20" / name,
        "".join(json.dumps(line) + "\n" for line in lines),
    )


def claude_invocation(project: Path, *extra: str) -> dict[str, object]:
    """The Discord-launched Claude argv the relay builds, as an invocation record."""
    return {
        "harness": "claude",
        "cwd": project.as_posix(),
        "argv": [
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            "opus",
            "--permission-mode",
            "acceptEdits",
            "--append-system-prompt",
            FAKE_SYSTEM_PROMPT,
            *extra,
            "--",
            "hello",
        ],
        "environment_names": ["ANTHROPIC_API_KEY", "CCDB_API_URL", "PATH"],
        "cli_version": "2.0.5",
    }


def codex_invocation(project: Path, *extra: str) -> dict[str, object]:
    """The Discord-launched Codex argv the relay builds, as an invocation record."""
    return {
        "harness": "codex",
        "cwd": project.as_posix(),
        "argv": [
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-c",
            "model_reasoning_effort=medium",
            "-c",
            "sandbox_workspace_write.network_access=true",
            *extra,
            "--",
            "hello",
        ],
        "environment_names": ["CODEX_HOME", "OPENAI_API_KEY", "PATH"],
        "cli_version": "0.147.0",
    }
