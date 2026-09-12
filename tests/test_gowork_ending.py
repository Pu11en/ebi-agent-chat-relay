"""Tests for the /gowork ending: try it locally, then keep it or fix it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_code_core import task_loop as tl
from claude_code_core import work_copy as wc

PLAN = """# Plan: calculator

Check: python3 -m unittest -q
Try: python3 -m http.server 8765
Open: http://localhost:8765

## How to try it
- Open the page and see the calculator
- Type 3 + 4 and get 7
- Try 3 ^ 4 and see a friendly error

## Tasks
- [x] Task 1: build it
"""


class TestPlanTryLines:
    def test_try_command_and_url(self) -> None:
        assert tl.plan_try_command(PLAN) == ["python3", "-m", "http.server", "8765"]
        assert tl.plan_open_url(PLAN) == "http://localhost:8765"

    def test_try_checks_are_the_bullets_under_how_to_try_it(self) -> None:
        assert tl.plan_try_checks(PLAN) == [
            "Open the page and see the calculator",
            "Type 3 + 4 and get 7",
            "Try 3 ^ 4 and see a friendly error",
        ]

    def test_all_optional(self) -> None:
        assert tl.plan_try_command("- [x] a") is None
        assert tl.plan_open_url("- [x] a") is None
        assert tl.plan_try_checks("- [x] a") == []


class TestVerdict:
    @pytest.mark.parametrize(
        "reply", ["looks good", "Looks good!", "yes", "keep it", "lgtm", "perfect", "ship it"]
    )
    def test_looks_good(self, reply: str) -> None:
        assert tl.is_looks_good(reply)

    @pytest.mark.parametrize(
        "reply", ["the button is red", "no", "it crashes on 3 / 0", "not good", "looks bad"]
    )
    def test_anything_else_is_a_fix(self, reply: str) -> None:
        assert not tl.is_looks_good(reply)


class TestAppendFixTask:
    def test_adds_an_unticked_task_at_the_end(self, tmp_path: Path) -> None:
        plan = tmp_path / "PLAN.md"
        plan.write_text("- [x] Task 1: a\n")
        tl.append_fix_task(plan, "the page is blank")
        text = plan.read_text()
        assert text.rstrip().endswith("- [ ] Fix: the page is blank")
        assert tl.count_tasks(text) == (1, 1)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "PLAN.md").write_text("- [ ] a\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "init")
    return r


class TestKeepTheWork:
    async def test_merges_into_the_project_and_cleans_up(self, repo: Path, tmp_path: Path) -> None:
        copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "c")
        (copy.path / "new.txt").write_text("built\n")
        _git(copy.path, "add", ".")
        _git(copy.path, "commit", "-qm", "work")

        ok, msg = await wc.keep_work(copy)

        assert ok, msg
        assert (repo / "new.txt").read_text() == "built\n"
        assert not copy.path.exists()
        assert _git(repo, "branch", "--list", copy.branch).strip() == ""

    async def test_unsaved_changes_block_it(self, repo: Path, tmp_path: Path) -> None:
        copy = await wc.create_work_copy(repo, repo / "PLAN.md", root=tmp_path / "c")
        (repo / "PLAN.md").write_text("edited by someone\n")

        ok, msg = await wc.keep_work(copy)

        assert not ok and "unsaved" in msg
        assert copy.path.exists()  # nothing lost


class TestStartCardHelpers:
    @pytest.mark.parametrize(
        "task",
        [
            "**C2 Railway.** New service, redeploy",
            "**C1 Drew tries it on localhost.** Sign in",
            "Task 13: Push the commits and open a PR on GitHub",
            "Check: 💲 one question with a lookup",
        ],
    )
    def test_steps_that_need_you(self, task: str) -> None:
        assert tl.needs_you(task)

    @pytest.mark.parametrize(
        "task", ["**A3 Brand it PropertyStack.** Name, logo", "Task 2: add a sign-in card"]
    )
    def test_steps_that_run_alone(self, task: str) -> None:
        assert not tl.needs_you(task)

    def test_short_label_is_plain(self) -> None:
        assert (
            tl.short_label("**A3 Brand it PropertyStack.** Name, logo") == "Brand it PropertyStack"
        )
        assert tl.short_label("Task 4: Merge the branch. Then more") == "Merge the branch"
        assert len(tl.short_label("x" * 300)) <= 81


class TestBotChecksItself:
    def test_prompt_lists_every_check_and_forbids_changes(self) -> None:
        prompt = tl.checker_prompt(Path("/p/PLAN.md"), ["Open the page", "Type 3 + 4"])
        assert "Open the page" in prompt and "Type 3 + 4" in prompt
        assert "Do not edit" in prompt and "PASS:" in prompt and "SKIP:" in prompt

    def test_parse_results(self) -> None:
        text = (
            "I checked things.\n"
            "PASS: Open the page — it loads\n"
            "**FAIL: Type 3 + 4 — shows 8**\n"
            "SKIP: Google sign-in — needs a real login\n"
            "DONE"
        )
        assert tl.parse_check_results(text) == [
            ("pass", "Open the page — it loads"),
            ("fail", "Type 3 + 4 — shows 8"),
            ("skip", "Google sign-in — needs a real login"),
        ]

    def test_nothing_parsed(self) -> None:
        assert tl.parse_check_results("no idea\nDONE") == []


class TestCheckLineWithComment:
    def test_only_the_command_in_backticks_runs(self) -> None:
        text = "Check: `python3 tooling/qa/sweep.py http://localhost:8765` (plus the task's own)"
        assert tl.plan_check_command(text) == [
            "python3",
            "tooling/qa/sweep.py",
            "http://localhost:8765",
        ]
