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
            answer=("Push to GitHub?", "yes do it"),
            retry_reason="no new commit",
        )
        assert "Push to GitHub?" in p and "yes do it" in p
        assert "no new commit" in p


class _Fake:
    """Drives TaskLoop with scripted worker replies that act on the repo."""

    def __init__(self, repo: Path, script: list, answers: list[str | None] | None = None):
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

    async def ask(self, question: str) -> str | None:
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
            answers=["yes"],
        )
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert fake.questions == ["Deploy now?"]
        assert "Deploy now?" in fake.prompts[1] and '"yes"' in fake.prompts[1]

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
        fake = _Fake(repo, [lambda r: ("x\nASK: q?", None)] * 3, answers=["yes", "yes", "yes"])
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


class TestPlanCheck:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Check: `uv run pytest -q`\n- [ ] a", ["uv", "run", "pytest", "-q"]),
            ("**Check:** python3 build.py\n", ["python3", "build.py"]),
            ("no check line\n- [ ] a", None),
        ],
    )
    def test_reads_the_check_line(self, text: str, expected: list[str] | None) -> None:
        assert tl.plan_check_command(text) == expected

    async def test_run_check_reports_pass_and_fail(self, tmp_path: Path) -> None:
        ok, _ = await tl.run_check(tmp_path, ["python3", "-c", "print('fine')"])
        assert ok
        ok, tail = await tl.run_check(
            tmp_path, ["python3", "-c", "import sys; print('boom'); sys.exit(1)"]
        )
        assert not ok and "boom" in tail

    async def test_missing_program_fails_cleanly(self, tmp_path: Path) -> None:
        ok, tail = await tl.run_check(tmp_path, ["definitely-not-a-program-xyz"])
        assert not ok and tail


class TestLoopRunsTheCheck:
    async def test_failed_check_means_not_done(self, repo: Path) -> None:
        plan = repo / "PLAN.md"
        plan.write_text("Check: python3 -c 'import sys; sys.exit(1)'\n" + plan.read_text())
        _git(repo, "commit", "-qam", "add check")
        fake = _Fake(repo, [_done, _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.STUCK
        assert "check" in outcome.detail

    async def test_passing_check_lets_it_continue(self, repo: Path) -> None:
        plan = repo / "PLAN.md"
        plan.write_text("Check: python3 -c 'print(1)'\n" + plan.read_text())
        _git(repo, "commit", "-qam", "add check")
        fake = _Fake(repo, [_done, _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE


class TestListPlans:
    def test_lists_open_plans_newest_first_across_the_usual_places(self, tmp_path: Path) -> None:
        import os

        (tmp_path / "docs" / "plans").mkdir(parents=True)
        (tmp_path / ".planning" / "x").mkdir(parents=True)
        a = tmp_path / "PLAN-v1.md"
        b = tmp_path / "docs" / "plans" / "cleanup-plan.md"
        c = tmp_path / ".planning" / "x" / "task_plan.md"
        done = tmp_path / "old-plan.md"
        for f, body in ((a, "- [ ] a"), (b, "- [ ] b"), (c, "- [ ] c"), (done, "- [x] d")):
            f.write_text(body + "\n")
        os.utime(a, (1, 1))
        os.utime(b, (2, 2))
        assert tl.list_plans(tmp_path) == [c, b, a]
        assert tl.find_plan(tmp_path) == c


class TestTypedReplies:
    def test_unclear_reply_asks_the_worker_to_explain_and_ask_again(self, tmp_path: Path) -> None:
        p = tl.worker_prompt(
            tmp_path / "PLAN.md", tmp_path / "p.md", answer=("Round?", "what that mean")
        )
        assert "what that mean" in p
        assert "explain" in p.lower() and "ASK" in p

    def test_notes_typed_during_a_task_reach_the_next_round(self, tmp_path: Path) -> None:
        p = tl.worker_prompt(tmp_path / "PLAN.md", tmp_path / "p.md", notes=["use blue"])
        assert "use blue" in p

    def test_ask_lines_must_carry_a_plain_example(self, tmp_path: Path) -> None:
        p = tl.worker_prompt(tmp_path / "PLAN.md", tmp_path / "p.md")
        assert "example" in p.lower()

    async def test_notes_are_drained_into_the_next_round(self, repo: Path) -> None:
        fake = _Fake(repo, [_done, _done])
        loop = fake.loop()
        loop.add_note("make it friendlier")
        await loop.run()
        assert "make it friendlier" in fake.prompts[0]
        assert "make it friendlier" not in fake.prompts[1]


class TestFindingTheProject:
    def test_a_session_copy_points_back_to_its_project(self, tmp_path: Path) -> None:
        proj = tmp_path / "realpage"
        wt = proj / ".worktrees" / "wt-123"
        wt.mkdir(parents=True)
        assert tl.project_of(wt) == proj
        assert tl.project_of(proj) == proj

    def test_thread_copy_is_found_by_thread_id(self, tmp_path: Path) -> None:
        (tmp_path / "realpage" / ".worktrees" / "wt-555").mkdir(parents=True)
        (tmp_path / "other").mkdir()
        assert tl.project_for_thread(tmp_path, 555) == tmp_path / "realpage"
        assert tl.project_for_thread(tmp_path, 999) is None

    def test_open_plans_across_every_project_newest_first(self, tmp_path: Path) -> None:
        import os

        (tmp_path / "realpage").mkdir()
        (tmp_path / "gomer").mkdir()
        (tmp_path / "empty").mkdir()
        a = tmp_path / "realpage" / "PLAN-v5.md"
        b = tmp_path / "gomer" / "plan.md"
        a.write_text("- [ ] x\n")
        b.write_text("- [ ] y\n")
        os.utime(b, (1, 1))
        assert tl.list_plans_across(tmp_path) == [a, b]


_LIMIT = "You've hit your session limit · resets 7pm (America/Chicago)"


class TestUsageLimit:
    @pytest.mark.parametrize(
        "text,error",
        [
            (_LIMIT, None),
            (None, "Claude AI usage limit reached|1757800000"),
            (None, "You exceeded your current quota, please check your plan"),
            ("x", "429 Too Many Requests: rate limit exceeded"),
        ],
    )
    def test_limit_messages_are_recognised(self, text: str | None, error: str | None) -> None:
        assert tl.usage_limit_message(text, error)

    @pytest.mark.parametrize(
        "text,error", [("did it\nDONE", None), (None, "boom"), ("added a rate limit\nDONE", None)]
    )
    def test_ordinary_results_are_not_limits(self, text: str | None, error: str | None) -> None:
        assert tl.usage_limit_message(text, error) is None

    async def test_a_limit_asks_and_never_uses_up_a_try(self, repo: Path) -> None:
        limited = lambda r: (_LIMIT, None)  # noqa: E731
        fake = _Fake(repo, [limited, limited, _done, _done])
        seen: list[str] = []

        async def on_limit(message: str) -> bool:
            seen.append(message)
            return True  # switched model or waited: try the same step again

        outcome = await fake.loop(on_limit=on_limit).run()
        assert outcome.status == tl.Status.COMPLETE
        assert seen == [_LIMIT, _LIMIT]
        assert not any("Stuck" in r for r in fake.reports)

    async def test_notes_survive_a_limit(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: (_LIMIT, None), _done, _done])
        loop = fake.loop(on_limit=lambda m: _true())
        loop.add_note("use sqlite")
        await loop.run()
        assert "use sqlite" in fake.prompts[1]

    async def test_no_answer_to_a_limit_pauses(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: (_LIMIT, None)])
        outcome = await fake.loop(on_limit=lambda m: _false()).run()
        assert outcome.status == tl.Status.ASK
        assert "limit" in outcome.detail


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


class TestTheWorkerFollowsThePerson:
    def test_new_endings_parse(self) -> None:
        assert tl.parse_status("x\nPAUSE: Drew is planning the bot first") == (
            tl.Status.PAUSE,
            "Drew is planning the bot first",
        )
        assert tl.parse_status("x\nSKIP: Drew said not yet")[0] == tl.Status.SKIP
        assert tl.parse_status("x\nPLAN: moved R4 to the end")[0] == tl.Status.PLAN

    def test_prompt_puts_the_persons_words_first_and_offers_the_new_endings(
        self, tmp_path: Path
    ) -> None:
        p = tl.worker_prompt(tmp_path / "PLAN.md", tmp_path / "p.md", notes=["hold off"])
        assert p.index("hold off") < p.index("Read the plan")
        assert "PAUSE:" in p and "SKIP:" in p and "PLAN:" in p

    async def test_pause_stops_the_loop_and_does_not_count_as_a_try(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: ("ok\nPAUSE: Drew wants to plan first", None)])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.PAUSE
        assert "plan first" in outcome.detail
        assert len(fake.prompts) == 1

    async def test_skip_ticks_the_step_and_moves_on(self, repo: Path) -> None:
        fake = _Fake(repo, [lambda r: ("ok\nSKIP: not yet", None), _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert "_(skipped)_" in (repo / "PLAN.md").read_text()

    async def test_plan_change_is_accepted_and_the_loop_carries_on(self, repo: Path) -> None:
        def reorder(r: Path) -> tuple[str, None]:
            plan = r / "PLAN.md"
            plan.write_text(plan.read_text() + "- [ ] Task 3: added by the person\n")
            return ("changed\nPLAN: added task 3", None)

        fake = _Fake(repo, [reorder, _done, _done, _done])
        outcome = await fake.loop().run()
        assert outcome.status == tl.Status.COMPLETE
        assert len(fake.prompts) == 4
        assert any("Plan changed" in r for r in fake.reports)


class TestPlanChangesFromThePlanner:
    COPY = (
        "# Plan\n\n- [x] T1 done\n- [ ] T2 old\n  more about T2\n- [ ] T3 old\n"
        "\n## How to try it\n- open it\n"
    )

    def test_open_steps_are_replaced_and_finished_ones_kept(self) -> None:
        real = (
            "# Plan\n\n- [ ] T1 done\n- [ ] T2 new\n- [ ] T4 added\n\n## How to try it\n- open it\n"
        )
        merged = tl.merge_open_tasks(self.COPY, real)
        assert merged is not None
        assert "- [x] T1 done" in merged and "- [ ] T1 done" not in merged
        assert "T2 new" in merged and "T4 added" in merged
        assert "T2 old" not in merged and "more about T2" not in merged and "T3 old" not in merged
        assert merged.index("T4 added") < merged.index("## How to try it")

    def test_no_change_returns_none(self) -> None:
        real = "# Plan\n\n- [ ] T1 done\n- [ ] T2 old\n  more about T2\n- [ ] T3 old\n"
        assert tl.merge_open_tasks(self.COPY, real) is None

    async def test_the_loop_asks_for_changes_before_every_step(self, repo: Path) -> None:
        calls = 0

        async def before_round() -> None:
            nonlocal calls
            calls += 1

        fake = _Fake(repo, [_done, _done])
        await fake.loop(before_round=before_round).run()
        assert calls >= 2  # before every step, and once more before finishing
