"""Machine profile integration (OpenSpec shared-project-catalog task 4.3).

A shared profile carries the owner aliases and trusted computers every
computer agrees on; a machine config carries what only this computer knows —
its approved roots, its identity and its deliberate capability exceptions.
DrewAI and the iMac share the first and differ in the second.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_discord.catalog_config import (
    CATALOG_ACTIONS,
    CatalogConfig,
    MachineConfig,
    SharedProfile,
    derive_root_keys,
)
from claude_discord.project_catalog import ProjectDiscovery


def profile_file(tmp_path: Path) -> Path:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "computers": [
                    {"owner": "drew", "computer": "drewai", "aliases": ["drew's pc"]},
                    {"owner": "drew", "computer": "imac", "aliases": ["the imac"]},
                    {"owner": "david", "computer": "davidpc"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def test_shared_profile_parses_inline_and_file_forms(tmp_path):
    inline = SharedProfile.parse("drew/drewai:drew's pc|drewbox, david/davidpc")
    assert [c.qualifier for c in inline.computers] == ["drew/drewai", "david/davidpc"]
    assert "drewbox" in inline.computers[0].aliases and "drew-s-pc" in inline.computers[0].aliases
    from_file = SharedProfile.from_file(profile_file(tmp_path))
    assert [c.qualifier for c in from_file.computers] == [
        "drew/drewai",
        "drew/imac",
        "david/davidpc",
    ]


@pytest.mark.parametrize("bad", ["drew", "drew/", "/drewai", "drew/drewai/extra"])
def test_malformed_profile_entries_raise(bad):
    with pytest.raises(ValueError):
        SharedProfile.parse(bad)


def test_profile_file_that_is_not_an_object_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        SharedProfile.from_file(path)


def test_root_keys_come_from_folder_names_and_never_collide():
    keys = derive_root_keys(
        [Path("/home/drew/main-projects"), Path("/mnt/work/Projects"), Path("/tmp/projects")]
    )
    assert keys == ("main-projects", "projects", "projects-2")


def test_machine_config_reads_roots_identity_and_exceptions(tmp_path, monkeypatch):
    (tmp_path / "main").mkdir()
    env = {
        "CCDB_PROJECT_ROOTS": f"{tmp_path / 'main'},{tmp_path / 'missing'}",
        "CCDB_CATALOG_OWNER": "Drew",
        "CCDB_CATALOG_COMPUTER": "DrewAI",
        "CCDB_CATALOG_DISABLED": "clone=git is not installed here; handoff=no peers",
    }
    machine = MachineConfig.from_env(env)
    assert machine.owner == "drew" and machine.computer == "drewai"
    # A missing root is kept so discovery can report it as unavailable, not silently dropped.
    assert [r.key for r in machine.roots] == ["main", "missing"]
    assert all(r.owner == "drew" and r.computer == "drewai" for r in machine.roots)
    assert machine.unavailable_reason("clone") == "git is not installed here"
    assert machine.unavailable_reason("session") is None
    assert machine.available_actions(CATALOG_ACTIONS) == ("session", "create")


def test_machine_config_falls_back_to_the_working_directory_and_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr("claude_discord.catalog_config._hostname", lambda: "My Laptop")
    machine = MachineConfig.from_env({}, fallback_root=str(tmp_path))
    assert [str(r.path) for r in machine.roots] == [str(tmp_path)]
    assert machine.computer == "my-laptop" and machine.owner == "local"
    assert machine.available_actions(CATALOG_ACTIONS) == ("session", "create", "clone")


def test_handoff_capability_follows_the_handoff_configuration():
    without = MachineConfig.from_env({}, fallback_root="/tmp")
    assert "handoff" not in without.capabilities
    with_peers = MachineConfig.from_env({"CCDB_HANDOFF_AGENTS": "drewai=1"}, fallback_root="/tmp")
    assert "handoff" in with_peers.capabilities


def test_drewai_and_imac_share_aliases_but_expose_only_their_own_projects(tmp_path):
    drew_root = tmp_path / "drewai-projects"
    imac_root = tmp_path / "imac-projects"
    (drew_root / "alpha").mkdir(parents=True)
    (imac_root / "beta").mkdir(parents=True)
    profile = str(profile_file(tmp_path))

    drewai = CatalogConfig.from_env(
        {
            "CCDB_CATALOG_PROFILE": profile,
            "CCDB_PROJECT_ROOTS": str(drew_root),
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
        }
    )
    imac = CatalogConfig.from_env(
        {
            "CCDB_CATALOG_PROFILE": profile,
            "CCDB_PROJECT_ROOTS": str(imac_root),
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "imac",
            "CCDB_CATALOG_DISABLED": "clone=the iMac has no git credentials",
        }
    )

    # Shared behaviour: the same three trusted computers, the same aliases...
    for config in (drewai, imac):
        registry = config.registry()
        assert {c.qualifier for c in registry.computers} == {
            "drew/drewai",
            "drew/imac",
            "david/davidpc",
        }
        assert registry.lookup("David").computer is not None
        assert registry.lookup("David").computer.computer == "davidpc"
    # ...but each computer is local only to itself.
    assert drewai.registry().local is not None and drewai.registry().local.computer == "drewai"
    assert imac.registry().local is not None and imac.registry().local.computer == "imac"
    # "Drew" (the owner of both) resolves to the local computer on each, never the other.
    assert drewai.registry().lookup("drew").computer.computer == "drewai"
    assert imac.registry().lookup("drew").computer.computer == "imac"

    # Distinct roots: each exposes only its own direct children.
    def names(config: CatalogConfig) -> list[str]:
        return [p.identity.name for p in ProjectDiscovery(config.roots).snapshot().projects]

    drew_names, imac_names = names(drewai), names(imac)
    assert drew_names == ["alpha"] and imac_names == ["beta"]

    # Deliberate exception: the iMac cannot clone, and says why.
    assert "clone" in drewai.available_actions()
    assert "clone" not in imac.available_actions()
    assert imac.unavailable_reason("clone") == "the iMac has no git credentials"


def test_profile_without_the_local_computer_still_gets_a_local_entry(tmp_path):
    config = CatalogConfig.from_env(
        {
            "CCDB_CATALOG_COMPUTERS": "david/davidpc",
            "CCDB_CATALOG_OWNER": "drew",
            "CCDB_CATALOG_COMPUTER": "drewai",
        },
        fallback_root=str(tmp_path),
    )
    registry = config.registry()
    assert registry.local is not None and registry.local.qualifier == "drew/drewai"
    assert {c.qualifier for c in registry.computers} == {"drew/drewai", "david/davidpc"}


def test_malformed_profile_file_raises_rather_than_degrading_to_no_peers(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        CatalogConfig.from_env({"CCDB_CATALOG_PROFILE": str(path)}, fallback_root=str(tmp_path))
