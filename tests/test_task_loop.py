"""Tests for claude_code_core.task_loop — the sequential, fresh-session task loop."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_code_core import task_loop as tl


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "PLAN.md").write_text("# Plan\n\n- [ ] Task 1: a\n- [ ] Task 2: b\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _tick_and_commit(repo: Path, n: int = 1) -> None:
    plan = repo / "PLAN.md"
    text = plan.read_text()
    for _ in range(n):
        text = text.replace("- [ ]", "- [x]", 1)
    plan.write_text(text)
    _git(repo, "commit", "-qam", "tick")


class TestParseStatus:
    @pytest.mark.parametrize(
        "text,status,detail",
        [
            ("recap\n\nDONE", tl.Status.DONE, ""),
            ("recap\nASK: Push to GitHub?", tl.Status.ASK, "Push to GitHub?"),
            ("recap\nSTUCK: tests fail", tl.Status.STUCK, "tests fail"),
            ("all done\nCOMPLETE\n", tl.Status.COMPLETE, ""),
            ("**DONE**", tl.Status.DONE, ""),
            ("no status here", tl.Status.NONE, ""),
            ("", tl.Status.NONE, ""),
        ],
    )
    def test_reads_the_last_line(self, text: str, status: tl.Status, detail: str) -> None:
        assert tl.parse_status(text) == (status, detail)

    def test_status_word_mid_text_does_not_count(self) -> None:
        assert tl.parse_status("DONE\nthen I kept going") == (tl.Status.NONE, "")


class TestPlanTasks:
    def test_counts_checked_and_unchecked(self) -> None:
        text = "- [x] one\n- [ ] two\n  - [ ] three\n* [X] four\nnot a task [ ]"
        assert tl.count_tasks(text) == (2, 2)

    def test_first_unchecked(self) -> None:
        assert tl.first_unchecked("- [x] a\n- [ ] Task 2: deploy\n- [ ] c") == "Task 2: deploy"
        assert tl.first_unchecked("- [x] a") is None


class TestSnapshotAndCheck:
    async def test_real_progress_passes(self, repo: Path) -> None:
        before = await tl.take_snapshot(repo, repo / "PLAN.md")
        _tick_and_commit(repo)
        after = await tl.take_snapshot(repo, repo / "PLAN.md")
        assert tl.check_done(before, after) == []

    async def test_claimed_done_without_commit_is_caught(self, repo: Path) -> None:
        before = await tl.take_snapshot(repo, repo / "PLAN.md")
        after = await tl.take_snapshot(repo, repo / "PLAN.md")
        problems = tl.check_done(before, after)
        assert any("commit" in p for p in problems)
        assert any("ticked" in p for p in problems)

    async def test_uncommitted_leftovers_are_caught(self, repo: Path) -> None:
        before = await tl.take_snapshot(repo, repo / "PLAN.md")
        _tick_and_commit(repo)
        (repo / "PLAN.md").write_text((repo / "PLAN.md").read_text() + "edited\n")
        after = await tl.take_snapshot(repo, repo / "PLAN.md")
        assert any("uncommitted" in p for p in tl.check_done(before, after))

    async def test_stray_untracked_files_do_not_block(self, repo: Path) -> None:
        """Junk the worker didn't create (build caches, someone's draft folder)
        must not make every round look unfinished."""
        (repo / "stray-dir").mkdir()
        (repo / "stray-dir" / "x.txt").write_text("x")
        before = await tl.take_snapshot(repo, repo / "PLAN.md")
        _tick_and_commit(repo)
        after = await tl.take_snapshot(repo, repo / "PLAN.md")
        assert tl.check_done(before, after) == []


class TestWorkerPrompt:
    def test_names_files_and_rules(self, tmp_path: Path) -> None:
        p = tl.worker_prompt(tmp_path / "PLAN.md", tmp_path / "PLAN.progress.md")
        assert str(tmp_path / "PLAN.md") in p
        assert "PLAN.progress.md" in p
        assert "first unchecked" in p
        assert "ASK:" in p and "DONE" in p and "STUCK:" in p and "COMPLETE" in p

    def test_carries_the_answer_and_retry_reason(self, tmp_path: Path) -> None:
        p = tl.worker_prompt(
            tmp_path / "PLAN.md",
            tmp_path / "p.md",
            answer=("Push to GitHub?", True),
            retry_reason="no new commit",
        )
        assert "Push to GitHub?" in p and "YES" in p
        assert "no new commit" in p


class _Fake:
    """Drives TaskLoop with scripted worker replies that act on the repo."""

    def __init__(self, repo: Path, script: list, answers: list[bool | None] | None = None):
        self.repo = repo
        self.script = list(script)
        self.answers = list(answers or [])
        self.prompts: list[str] = []
        self.questions: list[str] = []
        self.reports: list[str] = []

    async def run_round(self, prompt: str) -> tuple[str | None, str | None]:
        self.prompts.append(prompt)
        action = self.script.pop(0)
        return action(self.repo)

    async def ask(self, question: str) -> bool | None:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else None

    async def report(self, text: str) -> None:
        self.reports.append(text)

    def loop(self, **kw: object) -> tl.TaskLoop:
        return tl.TaskLoop(
            plan_path=self.repo / "PLAN.md",
            repo_dir=self.repo,
            run_round=self.run_round,
            ask=self.ask,
            report=self.report,
            **kw,  # type: ignore[arg-type]
        )


def _done(repo: Path) -> tuple[str, None]:
    _tick_and_commit(repo)
    return ("did it\nDONE", None)


def _lie(_repo: Path) -> tuple[str, None]:
    return ("did it (not really)\nDONE", None)


class TestTaskLoop:
    async def test_runs_every_task_in_order_then_completes(self, repo: Path) -> None:
        fake = _Fake(repo, [_done, _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert len(fake.prompts) == 2
        assert any("1 of 2" in r for r in fake.reports)
        assert any("2 of 2" in r for r in fake.reports)

    async def test_a_false_done_is_retried_then_stops(self, repo: Path) -> None:
        fake = _Fake(repo, [_lie, _lie])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.STUCK
        assert len(fake.prompts) == 2
        assert "commit" in fake.prompts[1]  # the retry says what was missing

    async def test_a_false_done_then_real_done_continues(self, repo: Path) -> None:
        fake = _Fake(repo, [_lie, _done, _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE

    async def test_ask_yes_is_passed_to_the_next_round(self, repo: Path) -> None:
        fake = _Fake(
            repo,
            [lambda r: ("need ok\nASK: Deploy now?", None), _done, _done],
            answers=[True],
        )
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert fake.questions == ["Deploy now?"]
        assert "Deploy now?" in fake.prompts[1] and "YES" in fake.prompts[1]

    async def test_unanswered_ask_pauses_the_loop(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: ("x\nASK: Deploy now?", None)])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.ASK

    async def test_worker_stuck_stops_with_its_reason(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: ("x\nSTUCK: need an API key", None)])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.STUCK
        assert "need an API key" in outcome.detail

    async def test_run_error_counts_as_a_failed_round(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: (None, "boom"), lambda r: (None, "boom")])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.STUCK

    async def test_plan_already_finished_runs_nothing(self, repo: Path) -> None:
        _tick_and_commit(repo, 2)
        fake = _Fake(repo, [])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert fake.prompts == []

    async def test_stop_request_ends_after_the_current_round(self, repo: Path) -> None:
        fake = _Fake(repo, [_done, _done])
        loop = fake.loop()

        async def run_then_stop(prompt: str) -> tuple[str | None, str | None]:
            loop.request_stop()
            return await fake.run_round(prompt)

        loop._run_round = run_then_stop  # type: ignore[method-assign]
        outcome = await loop.run()
        assert outcome.status == tl.Status.NONE
        assert len(fake.prompts) == 1

    async def test_round_cap(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: ("x\nASK: q?", None)] * 3, answers=[True, True, True])
        outcome = await fake.loop(max_rounds=2).run()
        assert outcome.status == tl.Status.STUCK
        assert len(fake.prompts) == 2


class TestFindPlan:
    def test_picks_the_plan_with_open_tasks(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN-old.md").write_text("- [x] done\n")
        (tmp_path / "PLAN-v1.md").write_text("- [ ] Task 10: ship\n")
        (tmp_path / "README.md").write_text("- [ ] not a plan\n")
        assert tl.find_plan(tmp_path) == tmp_path / "PLAN-v1.md"

    def test_newest_wins_when_several_are_open(self, tmp_path: Path) -> None:
        import os

        a, b = tmp_path / "plan.md", tmp_path / "PLAN-v2.md"
        a.write_text("- [ ] a\n")
        b.write_text("- [ ] b\n")
        os.utime(a, (1, 1))
        assert tl.find_plan(tmp_path) == b

    def test_none_when_nothing_open(self, tmp_path: Path) -> None:
        (tmp_path / "PLAN.md").write_text("no boxes\n")
        assert tl.find_plan(tmp_path) is None
