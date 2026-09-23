"""Regenerate the sanitized DrewAI/iMac fixture trees from the test builders.

Run from the repository root::

    uv run python tests/fixtures/harness_audit/generate.py

The trees contain no real paths: every absolute home path is written as the
``__HOME__`` token, which :func:`tests.harness_audit_fixtures.materialize_fixture`
substitutes when it copies a tree into a temporary directory.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from extensions.harness_audit.discovery import DiscoveryRoots
from tests.harness_audit_fixtures import (
    claude_invocation,
    codex_invocation,
    fake_home,
    write,
)

ROOT = Path(__file__).resolve().parent
TOKEN = "__HOME__"


def build(name: str, tweak: Callable[[DiscoveryRoots], None]) -> None:
    base = ROOT / name
    if base.exists():
        shutil.rmtree(base)
    roots = fake_home(base, with_transcripts=True)
    home = roots.home
    tweak(roots)

    def rel(path: Path) -> str:
        return TOKEN + "/" + path.relative_to(home).as_posix()

    write(
        base / "declare.json",
        json.dumps(
            {
                "links": {rel(k): rel(v) for k, v in roots.links.items()},
                "modes": {rel(k): v for k, v in roots.modes.items()},
            },
            indent=2,
        )
        + "\n",
    )
    assert roots.project_dir is not None
    for harness, builder in (("claude", claude_invocation), ("codex", codex_invocation)):
        record = builder(roots.project_dir)
        record["cwd"] = TOKEN + "/projects/relay"
        write(base / f"invocation-{harness}.json", json.dumps(record, indent=2) + "\n")
    for path in home.rglob("*.jsonl"):
        text = path.read_text(encoding="utf-8").replace(home.as_posix(), TOKEN)
        path.write_text(text, encoding="utf-8", newline="\n")
    projects = home / ".claude" / "projects"
    for slug in list(projects.iterdir()):
        slug.rename(projects / "-__HOME__-projects-relay")


def drewai(roots: DiscoveryRoots) -> None:
    del roots


def imac(roots: DiscoveryRoots) -> None:
    write(
        roots.codex_home / "config.toml",
        'model = "gpt-5-mini"\nmodel_reasoning_effort = "medium"\n'
        '[mcp_servers.github]\ncommand = "gh-mcp"\n',
    )
    write(roots.home / ".claude.json", json.dumps({"mcpServers": {}}))
    write(
        roots.home.parent / "overlay.json",
        json.dumps(
            {
                "machine": "imac",
                "exceptions": [
                    {
                        "exception_id": "imac-subscription",
                        "kind": "subscription",
                        "description": "the iMac subscription cannot use the larger models",
                        "preserved_outcome": "the same rules, skills and project guidance load",
                        "covers": ["setting:model", "setting:model_reasoning_effort"],
                    },
                    {
                        "exception_id": "imac-no-github-mcp",
                        "kind": "installed-tool",
                        "harness": "claude",
                        "description": "gh-mcp is not configured for Claude on the iMac",
                        "preserved_outcome": "GitHub work is done through the gh CLI there",
                        "covers": ["connector:github"],
                    },
                ],
            },
            indent=2,
        )
        + "\n",
    )


if __name__ == "__main__":
    build("drewai", drewai)
    build("imac", imac)
    print("fixtures regenerated under", ROOT)
