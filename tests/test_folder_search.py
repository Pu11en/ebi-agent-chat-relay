"""Typing a few letters must land on the folder the user meant, first."""

from __future__ import annotations

from pathlib import Path

from claude_discord.folder_search import (
    choice_label,
    rank_folders,
    scan_project_folders,
)


def make(root: Path, *names: str) -> list[str]:
    for name in names:
        (root / name).mkdir(parents=True)
    return [str(root / name) for name in names]


def test_scan_lists_projects_and_their_subfolders(tmp_path: Path):
    make(tmp_path, "alpha", "alpha/docs", "beta", ".hidden", "beta/node_modules")
    found = scan_project_folders([str(tmp_path)])
    names = [Path(path).name for path in found]
    assert names[:2] == ["alpha", "beta"], "top-level projects come first"
    assert "docs" in names, "one level below a project is still reachable"
    assert ".hidden" not in names
    assert "node_modules" not in names


def test_scan_is_bounded_and_survives_a_missing_root(tmp_path: Path):
    make(tmp_path, *[f"p{index:03d}" for index in range(30)])
    found = scan_project_folders([str(tmp_path), str(tmp_path / "gone")], cap=10)
    assert len(found) == 10


def test_empty_query_offers_recents_first_then_the_rest(tmp_path: Path):
    alpha, beta, gamma = make(tmp_path, "alpha", "beta", "gamma")
    ranked = rank_folders("", candidates=[alpha, beta, gamma], recents=[gamma])
    assert ranked[0] == gamma
    assert set(ranked) == {alpha, beta, gamma}


def test_empty_query_keeps_favorites_ahead_of_unused_folders(tmp_path: Path):
    alpha, beta, gamma = make(tmp_path, "alpha", "beta", "gamma")
    ranked = rank_folders("", candidates=[alpha, beta, gamma], recents=[gamma], favorites=[beta])
    assert ranked[:2] == [gamma, beta]


def test_query_matches_the_folder_name_before_the_path(tmp_path: Path):
    lockin, other = make(tmp_path, "lockin-ai", "notes/lockin-archive")
    ranked = rank_folders("lockin", candidates=[other, lockin])
    assert ranked[0] == lockin


def test_query_matches_scattered_letters(tmp_path: Path):
    (target,) = make(tmp_path, "ebi-agent-chat-relay")
    assert rank_folders("echat", candidates=[target]) == [target]
    assert rank_folders("relay", candidates=[target]) == [target]
    assert rank_folders("zzz", candidates=[target]) == []


def test_query_is_case_and_separator_insensitive(tmp_path: Path):
    (target,) = make(tmp_path, "Lockin AI")
    assert rank_folders("lockin-ai", candidates=[target]) == [target]
    assert rank_folders("LOCKINAI", candidates=[target]) == [target]


def test_a_recent_folder_is_offered_even_when_it_is_outside_every_root(tmp_path: Path):
    (inside,) = make(tmp_path, "roots/alpha")
    outside = tmp_path / "elsewhere" / "alpha-tools"
    outside.mkdir(parents=True)
    ranked = rank_folders("alpha", candidates=[inside], recents=[str(outside)])
    assert str(outside) in ranked


def test_limit_and_deduplication(tmp_path: Path):
    folders = make(tmp_path, *[f"proj{index}" for index in range(30)])
    ranked = rank_folders("proj", candidates=folders + folders, recents=[folders[5]], limit=25)
    assert len(ranked) == 25
    assert len(set(ranked)) == 25
    assert ranked[0] == folders[5]


def test_label_shows_the_folder_name_and_where_it_lives():
    label = choice_label("/home/drewp/main-projects/lockin-ai")
    assert label.startswith("lockin-ai")
    assert "main-projects" in label
    assert len(choice_label("/" + "x" * 300)) <= 100


def test_scattered_letters_prefer_the_folder_they_land_in_fewest_pieces(tmp_path: Path):
    relay, health = make(tmp_path, "ebi-agent-chat-relay", "epic-health")
    assert rank_folders("echat", candidates=[health, relay])[0] == relay
