"""T28 — grouping and worker prompts see the whole task, not its title.

A checkbox plan's task block (the indented ``Depends on`` / ``Inputs`` /
``Files`` / ``Result`` / ``Verify`` lines T27 exports) travels with the
label into the grouping prompt and the parallel worker's briefing. What the
quick AI answers is advisory: declared ownership and prerequisites are
enforced in code before a group runs, and a malformed or incomplete answer
falls back to one step at a time without dropping any step.
"""

from __future__ import annotations

from pathlib import Path

from claude_code_core import task_loop as tl
from claude_code_core.task_loop import (
    TaskContext,
    constrain_groups,
    group_prompt,
    open_task_contexts,
    parallel_prompt,
    parse_groups,
)
from tests.test_task_loop import _Fake, _done, _git

PLAN = """# Rename the service

Goal: Every surface calls the service by its new name.
Check: python -c pass

- [ ] Update the config
  Task: `api.config`
  Inputs: the new service name from the goal
  Files: api/config.py
  Result: the API reads the new name
  Verify: uv run pytest tests/test_api_config.py -q
- [ ] Update the config
  Task: `web.config`
  Inputs: the new service name from the goal
  Files: web/config.js
  Result: the web app reads the new name
  Verify: npm test -- config
- [ ] Rename it in every page
  Task: `web.pages`
  Depends on: Update the config (`web.config`)
  Files: web/
  Result: no page shows the old name
  Verify: npm test -- pages
- [ ] Write the release note
  Files: docs/release.md
  Result: a note explaining the rename
  Verify: uv run python scripts/check_docs.py
"""

API, WEB, PAGES, NOTE = (
    "Update the config",
    "Update the config",
    "Rename it in every page",
    "Write the release note",
)


def test_same_title_different_ownership_keeps_its_own_context(tmp_path: Path) -> None:
    contexts = open_task_contexts(PLAN)

    assert [c.label for c in contexts] == [API, WEB, PAGES, NOTE]
    assert contexts[0].task_id == "api.config" and contexts[1].task_id == "web.config"
    assert contexts[0].files == ("api/config.py",) and contexts[1].files == ("web/config.js",)
    assert contexts[2].depends_on == ("Update the config (`web.config`)",)
    assert contexts[2].depends_on_labels(contexts) == (1,)  # the web one, not the api one
    assert contexts[3].depends_on == () and contexts[3].task_id is None
    assert contexts[1].verify == "npm test -- config"

    prompt = group_prompt([c.label for c in contexts], contexts)
    assert "1. Update the config" in prompt and "2. Update the config" in prompt
    assert "owns: api/config.py" in prompt and "owns: web/config.js" in prompt
    assert "depends on: Update the config (`web.config`)" in prompt
    assert "same file" in prompt and "depends on" in prompt

    plan_path = tmp_path / "PLAN.md"
    plan_path.write_text(PLAN, encoding="utf-8")
    api_prompt = parallel_prompt(plan_path, API, context=contexts[0])
    web_prompt = parallel_prompt(plan_path, WEB, context=contexts[1])
    assert "api/config.py" in api_prompt and "web/config.js" not in api_prompt
    assert "web/config.js" in web_prompt and "api/config.py" not in web_prompt
    assert "the API reads the new name" in api_prompt
    assert "uv run pytest tests/test_api_config.py -q" in api_prompt
    assert "Every surface calls the service by its new name" in api_prompt


def test_declared_ownership_and_prerequisites_split_a_suggested_group() -> None:
    contexts = open_task_contexts(PLAN)
    labels = [c.label for c in contexts]

    # The quick AI says "all four at once"; code keeps the two configs and the note
    # together, holds the pages behind their prerequisite.
    groups = constrain_groups([labels], contexts)
    assert groups == [[API, WEB, NOTE], [PAGES]]

    # Ownership: web/ contains web/config.js, so pages and the web config never share.
    groups = constrain_groups([[WEB, PAGES], [API], [NOTE]], contexts)
    assert groups == [[WEB], [PAGES], [API], [NOTE]]

    # A suggestion may never bypass a declared prerequisite, whatever the order.
    groups = constrain_groups([[PAGES, NOTE], [API, WEB]], contexts)
    assert groups == [[NOTE], [PAGES], [API, WEB]] or groups == [[NOTE], [API, WEB], [PAGES]]
    flat = [step for group in groups for step in group]
    assert sorted(flat) == sorted(labels)


def test_a_step_without_ownership_runs_alone_when_others_declare_theirs() -> None:
    text = PLAN + "- [ ] Tidy up\n"
    contexts = open_task_contexts(text)
    groups = constrain_groups([[c.label for c in contexts]], contexts)
    assert groups[0] == [API, WEB, NOTE]
    assert ["Tidy up"] in groups
    # …but a plan with no metadata at all keeps the model's grouping, as before.
    bare = open_task_contexts("- [ ] a\n- [ ] b\n- [ ] c\n")
    assert constrain_groups([["a", "b"], ["c"]], bare) == [["a", "b"], ["c"]]


def test_malformed_grouping_output_falls_back_without_losing_tasks() -> None:
    contexts = open_task_contexts(PLAN)
    labels = [c.label for c in contexts]
    assert parse_groups("1,2 | 2,3", 4) == [[0], [1], [2], [3]]
    assert parse_groups("1, 4 | 2", 4) == [[0], [1], [2], [3]]  # incomplete: every step alone
    assert parse_groups("1,2,3,4 | ", 4) == [[0], [1], [2], [3]]

    # Duplicates, unknown steps and missing steps: same steps out as in, once each.
    groups = constrain_groups([[NOTE, NOTE, "not a step"], [API]], contexts)
    flat = [step for group in groups for step in group]
    assert sorted(flat) == sorted(labels) and "not a step" not in flat
    assert groups[0] == [NOTE] and len(flat) == 4
    # Two mentions of a shared title are the two same-titled tasks, by position.
    groups = constrain_groups([[API, WEB], [PAGES], [NOTE]], contexts)
    assert groups == [[API, WEB], [PAGES], [NOTE]]


async def test_the_loop_enforces_readiness_whatever_the_suggestion_says(tmp_path: Path) -> None:
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "PLAN.md").write_text(PLAN, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    ran: list[list[str]] = []

    async def next_group(open_steps: list[str]) -> list[str]:
        return open_steps  # "everything at once", including the pages behind their prerequisite

    async def run_group(group: list[str]) -> list[tuple[str, bool, str]]:
        ran.append(list(group))
        for step in group:
            tl.tick_task(repo / "PLAN.md", step)
        _git(repo, "commit", "-qam", "group")
        return [(step, True, "ok") for step in group]

    fake = _Fake(repo, [_done, _done])
    outcome = await fake.loop(next_group=next_group, run_group=run_group).run()

    assert outcome.status == tl.Status.COMPLETE
    assert ran == [[API, WEB, NOTE]]  # pages waited for the web config
    assert len(fake.prompts) == 1  # then ran on its own
    assert tl.first_unchecked((repo / "PLAN.md").read_text()) is None


async def test_a_suggestion_without_the_first_step_runs_it_alone(tmp_path: Path) -> None:
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "PLAN.md").write_text("- [ ] a\n- [ ] b\n- [ ] c\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    ran: list[list[str]] = []

    async def next_group(open_steps: list[str]) -> list[str]:
        return ["b", "c"] if len(open_steps) == 3 else open_steps[:1]

    async def run_group(group: list[str]) -> list[tuple[str, bool, str]]:
        ran.append(list(group))
        return [(step, False, "never") for step in group]

    fake = _Fake(repo, [_done, _done, _done])
    outcome = await fake.loop(next_group=next_group, run_group=run_group).run()

    assert outcome.status == tl.Status.COMPLETE
    assert ran == []  # a group that skips the first open step is not a group
    assert len(fake.prompts) == 3


def test_context_round_trips_through_the_plan_contract_lines() -> None:
    context = TaskContext(
        label="Ship it",
        task_id="ship",
        depends_on=("Build it (`build`)",),
        inputs=("the built artefact",),
        files=("dist/",),
        resources=("release-channel",),
        result="a tagged release",
        verify="uv run python scripts/verify_release.py",
    )
    assert "dist/" in context.summary() and "release-channel" in context.summary()
    assert context.owns_anything


async def test_the_cog_briefs_each_parallel_worker_with_its_own_context(tmp_path: Path) -> None:
    """Two same-titled steps with different ownership: each worker and the quick AI see
    the right files; the suggestion is constrained before the group runs."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from tests.test_task_loop_cog import _cog_with_chat, _type_when_asked

    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "PLAN.md").write_text(
        "- [ ] Update the config\n  Files: api/config.py\n  Result: the API reads the new name\n"
        "- [ ] Update the config\n  Files: web/config.js\n  Result: the web app reads it\n"
        "- [ ] Rename it in every page\n  Depends on: Update the config\n  Files: web/\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    cog, chat, thread = _cog_with_chat()
    thread.delete = AsyncMock()
    thread.parent = MagicMock()
    side_threads: list[MagicMock] = []

    async def spawn(channel, text, *, thread_name, auto_start, working_dir):  # noqa: ANN001
        if not side_threads and "Task loop" in thread_name:
            side_threads.append(thread)
            return thread
        t = MagicMock(spec=discord.Thread)
        t.id = 600 + len(side_threads)
        t.send = AsyncMock(return_value=MagicMock())
        t.delete = AsyncMock()
        side_threads.append(t)
        return t

    chat.spawn_session = AsyncMock(side_effect=spawn)
    worker_prompts: list[str] = []
    grouped: list[list[str]] = []

    async def turn(seed, thread_, prompt, *, working_dir, result_sink, **_slot):  # noqa: ANN001
        if "checking finished work" in prompt:
            await result_sink("PASS: ok — ok\nDONE", None)
            return
        plan = Path(working_dir) / "PLAN.md"
        if "Do exactly this one step" in prompt:
            worker_prompts.append(prompt)
            name = "api.txt" if "api/config.py" in prompt else "web.txt"
            (Path(working_dir) / name).write_text("made\n")
            _git(Path(working_dir), "add", ".")
            _git(Path(working_dir), "commit", "-qm", name)
            await result_sink("Made it.\nDONE", None)
            return
        plan.write_text(plan.read_text().replace("- [ ]", "- [x]", 1))
        _git(Path(working_dir), "commit", "-qam", "tick")
        await result_sink("recap\nDONE", None)

    chat.run_fresh_turn = AsyncMock(side_effect=turn)
    cog._quick_ai = AsyncMock(return_value="1,2,3")  # "all three at once" — pages must wait
    original_run_group = cog._run_group

    async def run_group(running, steps):  # noqa: ANN001
        grouped.append(list(steps))
        return await original_run_group(running, steps)

    cog._run_group = run_group  # type: ignore[method-assign]
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 1
    channel.send = AsyncMock()
    await cog.start_loop(channel, str(repo / "PLAN.md"))
    await _type_when_asked(cog, 1, "looks good")
    await asyncio.wait_for(cog.running[0].task, 20) if cog.running else None

    asked = cog._quick_ai.await_args_list[0].args[0]
    assert "owns: api/config.py" in asked and "owns: web/config.js" in asked
    assert grouped == [["Update the config", "Update the config"]]
    assert len(worker_prompts) == 2
    api = next(p for p in worker_prompts if "api/config.py" in p)
    web = next(p for p in worker_prompts if "web/config.js" in p)
    assert "web/config.js" not in api and "api/config.py" not in web
    assert "the API reads the new name" in api and "the web app reads it" in web
    plan = (repo / "PLAN.md").read_text()
    assert plan.count("- [x]") == 3
