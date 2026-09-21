"""T30 — planning and execution contracts are evaluated offline from saved examples.

Each saved case is a plan (and, where the scenario calls for it, saved
answers or a plan edit) with the structural results the implemented
parser, scheduler, ledger and prompt must produce. Structural results are
checked by code; rendered prompt examples are written out for reading and
are never presented as a measure of a live model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from claude_code_core.gowork_contracts import (
    SCENARIOS,
    evaluate_cases,
    format_report,
    render_examples,
)
from claude_code_core.gowork_contracts import _main as contracts_main

CASES = Path(__file__).parent / "fixtures" / "contracts"


def _copy_cases(tmp_path: Path) -> Path:
    target = tmp_path / "contracts"
    shutil.copytree(CASES, target)
    shutil.copy(CASES.parent / "validated-plan.md", tmp_path / "validated-plan.md")
    shutil.copy(CASES.parent / "legacy-plan.md", tmp_path / "legacy-plan.md")
    return target


def test_every_saved_case_passes_and_every_scenario_has_a_case() -> None:
    evaluation = evaluate_cases(CASES)
    failures = [
        (result.case_id, check.field, check.expected, check.actual)
        for result in evaluation.results
        if not result.ok
        for check in result.checks
        if not check.ok
    ]
    assert failures == [] and evaluation.problems == ()
    assert evaluation.ok
    assert set(evaluation.coverage) == set(SCENARIOS)
    assert all(evaluation.coverage[scenario] for scenario in SCENARIOS)


def test_saved_cases_retain_scope_and_readiness() -> None:
    """The expectations in cases.json are not self-referential: key actuals are pinned here."""
    by_id = {result.case_id: result for result in evaluate_cases(CASES).results}

    multi = by_id["multiple-projects"].actual
    assert multi["projects"] == 4 and multi["tasks"] == 4
    assert multi["ready_now"] == [
        "product.catalog-api",
        "website.page-styles",
        "marketing.launch-post",
    ]  # the page waits for the product contract

    conflict = by_id["ownership-conflict"].actual
    assert conflict["problems"] == []  # a shared file is not a broken plan
    assert conflict["conflicts"] == [["settings.save-label", "settings.autosave"]]
    assert "settings.autosave" not in conflict["ready_now"]  # never dispatched together

    tiny = by_id["tiny-change"].actual
    assert tiny["edit"]["changed"] == {"settings": 2}
    assert tiny["edit"]["reworked"] == ["settings.save-label"]
    assert tiny["edit"]["statuses"]["docs.release-note"] == "accepted"  # untouched

    missing = by_id["missing-decisions"].actual
    assert missing["problems"] == ["agreed outcome 'REQ-EXPORT' is not covered by any task"]
    assert missing["decisions"] == []

    legacy = by_id["legacy-runtime"].actual
    assert legacy["problems"][0].startswith("this plan carries a gowork-plan manifest")
    assert "control (launch)" in legacy["problems"][1] and "4 projects" in legacy["problems"][1]

    resumed = by_id["resumed-answers"].actual
    assert resumed["prompt"].count("Settled already") == 1
    assert "as many as the computer can handle" in resumed["prompt"]


def test_a_wrong_expectation_names_the_case_and_the_field(tmp_path: Path) -> None:
    cases = _copy_cases(tmp_path)
    document = json.loads((cases / "cases.json").read_text(encoding="utf-8"))
    case = next(c for c in document["cases"] if c["id"] == "multiple-projects")
    case["structural"]["ready_now"] = ["product.catalog-api", "website.catalog-page"]
    (cases / "cases.json").write_text(json.dumps(document), encoding="utf-8")

    evaluation = evaluate_cases(cases)
    assert not evaluation.ok
    failed = [r for r in evaluation.results if not r.ok]
    assert [r.case_id for r in failed] == ["multiple-projects"]
    check = next(c for c in failed[0].checks if not c.ok)
    assert check.field == "ready_now"
    assert check.expected == ["product.catalog-api", "website.catalog-page"]
    assert check.actual == ["product.catalog-api", "website.page-styles", "marketing.launch-post"]
    report = format_report(evaluation)
    assert "multiple-projects: ready_now" in report
    assert "expected ['product.catalog-api', 'website.catalog-page']" in report
    assert "FAIL" in report and report.count("\n") < 40  # short


def test_rendered_examples_separate_structure_from_prompt_quality(tmp_path: Path) -> None:
    evaluation = evaluate_cases(CASES)
    written = render_examples(evaluation, tmp_path / "rendered")
    assert {p.stem for p in written} == {r.case_id for r in evaluation.results} | {"README"}
    resumed = (tmp_path / "rendered" / "resumed-answers.md").read_text(encoding="utf-8")
    assert "## Structural results (checked by code)" in resumed
    assert "## Rendered planning prompt (not a measure of a live model)" in resumed
    assert resumed.index("Structural results") < resumed.index("Rendered planning prompt")
    assert "never ask these again" in resumed  # the example itself is there to read
    readme = (tmp_path / "rendered" / "README.md").read_text(encoding="utf-8")
    assert "prompt quality" in readme and "not measured" in readme
    assert "no paid evaluation" in readme.lower()
    assert format_report(evaluation).splitlines()[-1].startswith("prompt quality: not measured")


def test_copied_content_needs_its_license_notice(tmp_path: Path) -> None:
    cases = _copy_cases(tmp_path)
    document = json.loads((cases / "cases.json").read_text(encoding="utf-8"))
    document["cases"][0]["copied_from"] = {
        "source": "https://github.com/example/upstream",
        "license": "MIT",
    }
    (cases / "cases.json").write_text(json.dumps(document), encoding="utf-8")

    evaluation = evaluate_cases(cases)
    assert not evaluation.ok
    assert any(
        "license notice" in problem and "https://github.com/example/upstream" in problem
        for problem in evaluation.problems
    )

    with (cases / "NOTICE.md").open("a", encoding="utf-8") as notice:
        notice.write("\n- https://github.com/example/upstream — MIT, notice kept in NOTICE.md\n")
    assert evaluate_cases(cases).ok


def test_the_saved_cases_copy_nothing_from_upstream_repositories() -> None:
    document = json.loads((CASES / "cases.json").read_text(encoding="utf-8"))
    notice = (CASES / "NOTICE.md").read_text(encoding="utf-8")
    for case in document["cases"]:
        assert "copied_from" not in case
        assert case["source"] in notice  # every case says where it came from
    for upstream in ("superpowers", "get-shit-done", "BMAD"):
        assert upstream.lower() in notice.lower()  # the MIT notices are named, not copied


def test_the_command_fails_on_a_regression_and_passes_on_the_saved_cases(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    assert contracts_main([str(CASES), "--render", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "7 case(s)" in out or "case(s)" in out
    assert (tmp_path / "out" / "README.md").exists()

    cases = _copy_cases(tmp_path)
    document = json.loads((cases / "cases.json").read_text(encoding="utf-8"))
    case = next(c for c in document["cases"] if c["id"] == "ownership-conflict")
    case["structural"]["conflicts"] = []
    (cases / "cases.json").write_text(json.dumps(document), encoding="utf-8")
    assert contracts_main([str(cases)]) == 1
    out = capsys.readouterr().out
    assert "ownership-conflict: conflicts" in out
