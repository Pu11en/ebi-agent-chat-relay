"""Evaluate planning and execution contracts offline from saved examples (T30).

A *case* is a saved plan (and, when the scenario needs them, saved answers
or an edited version of the plan) together with the structural results the
implemented code must produce for it: how :func:`gowork_export.check_plan`
judges it, which tasks are ready now, which pairs may never run together,
which decisions a worker would be handed, what a mid-build edit reworks,
and what the planning prompt carries. Those are checked by code and a
failure names the case, the field, what was expected and what came out.

What is *not* checked: whether a live model answers well. The planning
prompt is rendered so a person can read it, under a heading that says so.
No model is called, nothing leaves the machine, and no paid evaluation
agent is involved.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_code_core.gowork_export import RuntimeSupport, check_plan, installed_runtime
from claude_code_core.gowork_guidance import routing_block
from claude_code_core.gowork_handoff import plan_decisions
from claude_code_core.gowork_plan import PlanValidationError, parse_plan_tree
from claude_code_core.gowork_prompts import planning_prompt
from claude_code_core.gowork_schedule import ready_tasks
from claude_code_core.gowork_state import open_build_state

#: The scenarios T30 names; every one needs at least one saved case.
SCENARIOS: tuple[str, ...] = (
    "new plan",
    "resumed answers",
    "tiny changes",
    "multiple projects",
    "ownership conflict",
    "missing decisions",
    "legacy runtime",
)

CASES_FILE = "cases.json"
NOTICE_FILE = "NOTICE.md"
STRUCTURAL_HEADING = "## Structural results (checked by code)"
RENDERED_HEADING = "## Rendered planning prompt (not a measure of a live model)"
PROMPT_QUALITY_LINE = (
    "prompt quality: not measured — the rendered prompts show what the planner is given, "
    "not how a model answers; no live model and no paid evaluation agent ran"
)


#: A case id names the rendered file ``<case_id>.md``: one plain name, no separators.
_CASE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _plain_case_id(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not _CASE_ID_RE.fullmatch(value) or value in (".", ".."):
        raise ContractError(
            f"{where}: case id {value!r} must be one plain name (letters, digits, '.', '_' or '-')"
        )
    return value


class ContractError(ValueError):
    """The case set itself is unusable (missing file, bad JSON, unknown runtime)."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One expectation of one case, and what the code produced."""

    field: str
    ok: bool
    expected: Any
    actual: Any


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    scenarios: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    #: Everything the code produced for the case, checked or not.
    actual: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True, slots=True)
class Evaluation:
    results: tuple[CaseResult, ...]
    #: scenario → the case ids that cover it (every scenario is a key).
    coverage: dict[str, tuple[str, ...]]
    #: Problems with the case set as a whole: an uncovered scenario, a copied
    #: source with no license notice.
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems and all(result.ok for result in self.results)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_cases(case_dir: Path) -> list[dict[str, Any]]:
    path = Path(case_dir) / CASES_FILE
    if not path.is_file():
        raise ContractError(f"no {CASES_FILE} in {case_dir}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path} is not valid JSON: {exc}") from exc
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ContractError(f"{path} holds no cases")
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or not case_id:
            raise ContractError(f"{path}: every case needs a case id")
        _plain_case_id(case_id, where=str(path))
        if case_id in seen:
            raise ContractError(f"{path}: duplicate case id '{case_id}'")
        seen.add(case_id)
    return cases


def _runtime(name: str) -> RuntimeSupport:
    if name == "installed":
        return installed_runtime()
    if name == "legacy":
        return RuntimeSupport.LEGACY
    raise ContractError(f"unknown runtime '{name}' (installed or legacy)")


# --------------------------------------------------------------------------- #
# Evaluating one case
# --------------------------------------------------------------------------- #


def _problems_match(expected: list[str], actual: list[str]) -> bool:
    """Each expected line is a substring of the problem at the same position."""
    return len(expected) == len(actual) and all(
        want in got for want, got in zip(expected, actual, strict=True)
    )


def _structural(case: dict[str, Any], plan_path: Path, text: str) -> dict[str, Any]:
    runtime = _runtime(case.get("runtime", "installed"))
    report = check_plan(text, source_path=plan_path, runtime=runtime)
    actual: dict[str, Any] = {
        "runtime": runtime.label,
        "format": report.format,
        "projects": report.projects,
        "tasks": report.tasks,
        "problems": list(report.problems),
        "notes": list(report.notes),
        "ready_now": list(report.ready_now),
        "decisions": list(plan_decisions(text)),
    }
    try:
        tree = parse_plan_tree(text, source_path=plan_path)
    except PlanValidationError:
        actual.update(conflicts=[], conflict_files={}, coverage={}, ready_outcomes=[])
        return actual
    conflicts = tree.ownership_conflicts()
    actual["conflicts"] = [[c.first_task_id, c.second_task_id] for c in conflicts]
    actual["conflict_files"] = {
        f"{c.first_task_id}+{c.second_task_id}": list(c.files) + list(c.resources)
        for c in conflicts
    }
    actual["coverage"] = {
        req.requirement_id: [
            task.task_id for task in tree.tasks if task.source_requirement == req.requirement_id
        ]
        for req in tree.requirements
    }
    actual["ready_outcomes"] = [
        tree.task(task_id).outcome for task_id in report.ready_now if task_id in _ids(tree)
    ]
    return actual


def _ids(tree: Any) -> set[str]:
    return {task.task_id for task in tree.tasks}


def _edit(case: dict[str, Any], case_dir: Path, plan_path: Path) -> dict[str, Any]:
    """Accept some tasks, then take the edited plan on board: what is reworked?"""
    spec = case["edit"]
    edited_path = (case_dir / spec["plan"]).resolve()
    before = parse_plan_tree(plan_path.read_text(encoding="utf-8"), source_path=plan_path)
    edited_text = edited_path.read_text(encoding="utf-8")
    after = parse_plan_tree(edited_text, source_path=plan_path)
    with tempfile.TemporaryDirectory() as folder:
        state = open_build_state(Path(folder) / "build.json", before, build_id="contract")
        for task_id in spec.get("accepted_before", ()):
            attempt = state.begin(task_id).attempt_id
            state.submit_result(task_id, attempt, commit=f"c-{task_id}", checks=("ok",))
            state.accept(task_id, attempt)
        sync = state.sync_tree(after)
        return {
            "changed": dict(sync.changed_plans),
            "reworked": list(sync.reworked_tasks),
            "added": list(sync.added_tasks),
            "statuses": {record.task_id: record.status.value for record in state.records},
            "ready_after": [task.task_id for task in ready_tasks(state)],
            "decisions_after": list(plan_decisions(edited_text)),
        }


def _prompt(case: dict[str, Any], plan_path: Path, text: str) -> str:
    spec = case["planning"]
    return planning_prompt(
        text,
        plan_path=plan_path,
        known_answers=[tuple(pair) for pair in spec.get("known_answers", ())],  # type: ignore[misc]
        project_facts=list(spec.get("project_facts", ())),
        unclear=bool(spec.get("unclear", False)),
    )


def _text_checks(prefix: str, spec: dict[str, Any], text: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    for needle in spec.get("must_contain", ()):
        checks.append(CheckResult(f"{prefix} contains", needle in text, needle, needle in text))
    for needle in spec.get("must_not_contain", ()):
        checks.append(
            CheckResult(f"{prefix} does not contain", needle not in text, needle, needle in text)
        )
    for needle in spec.get("contains_once", ()):
        count = text.count(needle)
        checks.append(CheckResult(f"{prefix} contains once", count == 1, needle, count))
    return checks


def evaluate_case(case: dict[str, Any], case_dir: Path) -> CaseResult:
    case_dir = Path(case_dir)
    plan_path = (case_dir / case["plan"]).resolve()
    if not plan_path.is_file():
        raise ContractError(f"case '{case['id']}': plan {plan_path} is missing")
    text = plan_path.read_text(encoding="utf-8")
    actual = _structural(case, plan_path, text)
    checks: list[CheckResult] = []
    for name, expected in case.get("structural", {}).items():
        got = actual.get(name)
        ok = _problems_match(expected, got or []) if name == "problems" else got == expected
        checks.append(CheckResult(name, ok, expected, got))
    if "edit" in case:
        actual["edit"] = _edit(case, case_dir, plan_path)
        for name, expected in case["edit"].items():
            if name in ("plan", "accepted_before"):
                continue
            got = actual["edit"].get(name)
            checks.append(CheckResult(f"edit.{name}", got == expected, expected, got))
    if "planning" in case:
        actual["prompt"] = _prompt(case, plan_path, text)
        checks += _text_checks("prompt", case["planning"], actual["prompt"])
    if "routing" in case:
        actual["routing"] = routing_block(Path("~/.agents/skills/gowork-planning/SKILL.md"))
        checks += _text_checks("routing", case["routing"], actual["routing"])
    return CaseResult(case["id"], tuple(case.get("scenarios", ())), tuple(checks), actual)


# --------------------------------------------------------------------------- #
# The whole set
# --------------------------------------------------------------------------- #


def _notice_problems(cases: list[dict[str, Any]], case_dir: Path) -> list[str]:
    """A case that copies upstream text must be named, with its license, in NOTICE.md."""
    notice_path = Path(case_dir) / NOTICE_FILE
    notice = notice_path.read_text(encoding="utf-8") if notice_path.is_file() else ""
    problems: list[str] = []
    for case in cases:
        copied = case.get("copied_from")
        if not copied:
            continue
        source = copied.get("source", "") if isinstance(copied, dict) else ""
        license_name = copied.get("license", "") if isinstance(copied, dict) else ""
        if not source or not license_name:
            problems.append(
                f"case '{case['id']}': copied_from needs 'source' and 'license' (license notice)"
            )
        elif source not in notice or license_name not in notice:
            problems.append(
                f"case '{case['id']}': copied content from {source} ({license_name}) has no "
                f"license notice in {NOTICE_FILE}"
            )
    return problems


def evaluate_cases(case_dir: Path) -> Evaluation:
    case_dir = Path(case_dir)
    cases = load_cases(case_dir)
    results = tuple(evaluate_case(case, case_dir) for case in cases)
    coverage = {
        scenario: tuple(r.case_id for r in results if scenario in r.scenarios)
        for scenario in SCENARIOS
    }
    problems = [
        f"no saved case covers the scenario '{s}'" for s, ids in coverage.items() if not ids
    ]
    for result in results:
        for scenario in result.scenarios:
            if scenario not in SCENARIOS:
                problems.append(f"case '{result.case_id}' names an unknown scenario '{scenario}'")
    problems += _notice_problems(cases, case_dir)
    return Evaluation(results, coverage, tuple(problems))


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def format_report(evaluation: Evaluation) -> str:
    """A short report: one line per case, one line per failure, the coverage, the boundary."""
    lines: list[str] = []
    for result in evaluation.results:
        mark = "ok  " if result.ok else "FAIL"
        lines.append(f"{mark} {result.case_id} ({', '.join(result.scenarios)})")
        for check in result.failures:
            lines.append(
                f"     {result.case_id}: {check.field} — expected {check.expected!r}, "
                f"got {check.actual!r}"
            )
    for problem in evaluation.problems:
        lines.append(f"FAIL {problem}")
    failed = sum(1 for r in evaluation.results if not r.ok)
    lines.append(
        f"{len(evaluation.results)} case(s), {failed} failed; scenarios covered: "
        + ", ".join(f"{s} ({len(ids)})" for s, ids in evaluation.coverage.items())
    )
    lines.append(PROMPT_QUALITY_LINE)
    return "\n".join(lines)


def _render_case(result: CaseResult) -> str:
    parts = [f"# {result.case_id}", "", f"Scenarios: {', '.join(result.scenarios)}", ""]
    parts += [STRUCTURAL_HEADING, ""]
    for check in result.checks:
        mark = "ok" if check.ok else "FAIL"
        parts.append(
            f"- {mark} `{check.field}`: expected `{check.expected!r}`, got `{check.actual!r}`"
        )
    shown = {k: v for k, v in result.actual.items() if k not in ("prompt", "routing")}
    parts += ["", "Everything the code produced:", "", "```json"]
    parts.append(json.dumps(shown, indent=2, ensure_ascii=False, default=str))
    parts += ["```", ""]
    if "prompt" in result.actual:
        parts += [
            RENDERED_HEADING,
            "",
            "This is the text the planner would be given for this case. It shows what the "
            "prompt preserves and asks for; it does not show, and must not be read as, how a "
            "live model answers. No model was called to produce this file.",
            "",
            "```text",
            result.actual["prompt"],
            "```",
            "",
        ]
    if "routing" in result.actual:
        parts += [
            "## Routing rule (repo text, T29)",
            "",
            "```text",
            result.actual["routing"],
            "```",
            "",
        ]
    return "\n".join(parts)


def render_examples(evaluation: Evaluation, out_dir: Path) -> list[Path]:
    """Write one readable file per case plus a README that states the boundary."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for result in evaluation.results:
        path = out_dir / f"{_plain_case_id(result.case_id, where='render_examples')}.md"
        path.write_text(_render_case(result), encoding="utf-8")
        written.append(path)
    readme = out_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# Rendered contract cases (T30)",
                "",
                "One file per saved case. In each file the *structural results* were checked "
                "by code (parser, scheduler, ledger, prompt text); the *rendered planning "
                "prompt* is there to read.",
                "",
                "Prompt quality is not measured here: nothing in these files shows how a "
                "live model answers, and no paid evaluation agent was used. Judging answers "
                "is a separate, live activity.",
                "",
                format_report(evaluation),
                "",
            ]
        ),
        encoding="utf-8",
    )
    written.append(readme)
    return written


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m claude_code_core.gowork_contracts",
        description="Check the saved planning/execution contract cases against the installed code.",
    )
    parser.add_argument("cases", type=Path, help="folder holding cases.json and the plans")
    parser.add_argument("--render", type=Path, help="write one readable file per case here")
    args = parser.parse_args(argv)
    try:
        evaluation = evaluate_cases(args.cases)
    except ContractError as exc:
        print(f"cannot evaluate: {exc}")
        return 2
    if args.render is not None:
        render_examples(evaluation, args.render)
    print(format_report(evaluation))
    if args.render is not None:
        print(f"rendered examples: {args.render}")
    return 0 if evaluation.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
