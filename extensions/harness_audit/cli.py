"""Command line for the professional harness audit (task 4.3).

::

    python -m extensions.harness_audit.cli audit   --machine drewai --output DIR [roots…]
    python -m extensions.harness_audit.cli export  --output DIR --machine drewai --to FILE
    python -m extensions.harness_audit.cli import  --output DIR --from FILE [--force]
    python -m extensions.harness_audit.cli compare --output DIR [--overlay FILE…]
    python -m extensions.harness_audit.cli quarantine plan --output DIR --machine M
                                                           --quarantine-dir QDIR
    python -m extensions.harness_audit.cli quarantine apply    --manifest FILE [roots…]
    python -m extensions.harness_audit.cli quarantine rollback --manifest FILE [roots…]
    python -m extensions.harness_audit.cli verify  --output DIR --machine M --manifest FILE
                                                   [roots…]
    python -m extensions.harness_audit.cli rollback --manifest FILE [roots…]

``apply`` and ``rollback`` take the same root options as ``audit`` because the
manifest is data, not authority: every path it names is checked against those
roots before anything moves.

What each command may touch:

* ``audit``, ``export``, ``import``, ``compare``, ``quarantine plan`` and
  ``verify`` write only inside ``--output`` (and ``--to``).  They read the
  harness directories and never write into them.
* ``quarantine apply`` and ``rollback`` are the explicit, reversible
  exceptions: they move the files a manifest names and rewrite that manifest.
  Nothing is deleted.
* No command runs a CLI, asks a model, or has an option that selects a model,
  reasoning effort, provider route or subscription; those settings are only
  ever *read* so the report can say which path was inspected.

Every fail-closed condition (a bundle that still matches a secret rule, an
expired source manifest, a hash mismatch) exits with status 2 and changes
nothing.  Usage errors exit with status 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from extensions.harness_audit.classify import classify
from extensions.harness_audit.claude_collector import ClaudeInvocation, collect_claude
from extensions.harness_audit.codex_collector import CodexInvocation, collect_codex
from extensions.harness_audit.collection import CollectionResult
from extensions.harness_audit.discovery import DiscoveryRoots, discover
from extensions.harness_audit.models import (
    AuditTarget,
    Classification,
    CoverageGap,
    Harness,
    Machine,
    SchemaError,
)
from extensions.harness_audit.parity import MachineOverlay, ParityReport, check_parity
from extensions.harness_audit.quarantine import (
    QuarantineManifest,
    absolute_paths,
    apply_quarantine,
    plan_quarantine,
    removal_eligibility,
    rollback_quarantine,
    target_passes,
)
from extensions.harness_audit.redaction import (
    RedactedBundle,
    build_redacted_bundle,
    parse_bundle,
    serialize_bundle,
)
from extensions.harness_audit.report import build_report, render_json, render_text
from extensions.harness_audit.rules import run_rules
from extensions.harness_audit.sources import load_manifest

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_REFUSED = 2


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _today() -> date:
    return date.today()


class CliError(Exception):
    """A usage problem the operator can fix; nothing was written."""


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", type=Path, default=None, help="the home directory (default: ~)")
    parser.add_argument("--claude-home", type=Path, default=None, help="default: HOME/.claude")
    parser.add_argument("--codex-home", type=Path, default=None, help="default: HOME/.codex")
    parser.add_argument("--project", type=Path, default=None, help="one project directory")
    parser.add_argument(
        "--known-project",
        action="append",
        default=[],
        help="a project name whose mention marks content as project-specific (repeatable)",
    )
    parser.add_argument(
        "--declare",
        type=Path,
        default=None,
        help='JSON {"links": {path: target}, "modes": {path: int|null}} for hosts that cannot'
        " create symlinks or report POSIX modes",
    )
    parser.add_argument("--invocation-claude", type=Path, default=None, help="Claude argv record")
    parser.add_argument("--invocation-codex", type=Path, default=None, help="Codex argv record")
    parser.add_argument("--salt", default="", help="hash salt; use the same one on every machine")
    parser.add_argument("--sources", type=Path, default=None, help="alternate sources.json")
    parser.add_argument(
        "--harness",
        choices=("claude", "codex", "both"),
        default="both",
        help="which harness(es) to inspect",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m extensions.harness_audit.cli",
        description="Read-only audit of what Discord-launched Claude and Codex actually load.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="inventory, check and classify one machine")
    audit.add_argument("--machine", choices=[m.value for m in Machine], required=True)
    audit.add_argument("--output", type=Path, required=True, help="the only directory written")
    _add_roots(audit)

    export = commands.add_parser("export", help="copy this machine's redacted bundle to a file")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--machine", choices=[m.value for m in Machine], required=True)
    export.add_argument("--to", type=Path, required=True)

    imp = commands.add_parser("import", help="validate another machine's bundle into --output")
    imp.add_argument("--output", type=Path, required=True)
    imp.add_argument("--from", dest="source", type=Path, required=True)
    imp.add_argument(
        "--force",
        action="store_true",
        help="replace a bundle already in --output for the same machine (default: refuse)",
    )

    compare = commands.add_parser("compare", help="combine every bundle in --output")
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--overlay", type=Path, action="append", default=[])

    quarantine = commands.add_parser("quarantine", help="plan, apply or roll back a quarantine")
    steps = quarantine.add_subparsers(dest="step", required=True)
    plan = steps.add_parser("plan", help="write a manifest; move nothing")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--machine", choices=[m.value for m in Machine], required=True)
    plan.add_argument("--quarantine-dir", type=Path, required=True)
    plan.add_argument(
        "--verdict",
        action="append",
        default=[],
        choices=[c.value for c in Classification if c is not Classification.KEEP],
        help="which verdicts to include (default: remove, move-to-project, load-only-when-needed)",
    )
    apply = steps.add_parser("apply", help="move the files a manifest names (hash-verified)")
    apply.add_argument("--manifest", type=Path, required=True)
    _add_roots(apply)  # the manifest's paths are checked against these, never trusted
    rollback = steps.add_parser("rollback", help="move them back (hash-verified)")
    rollback.add_argument("--manifest", type=Path, required=True)
    _add_roots(rollback)

    verify = commands.add_parser("verify", help="re-run the checks after a quarantine")
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--machine", choices=[m.value for m in Machine], required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    _add_roots(verify)

    top_rollback = commands.add_parser("rollback", help="alias of `quarantine rollback`")
    top_rollback.add_argument("--manifest", type=Path, required=True)
    _add_roots(top_rollback)
    return parser


def iter_actions(parser: argparse.ArgumentParser) -> Iterator[argparse.Action]:
    """Every option on every (sub)command, for the tests that police them."""
    for action in parser._actions:  # noqa: SLF001 - argparse has no public walker
        yield action
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for sub in action.choices.values():
                yield from iter_actions(sub)


# --------------------------------------------------------------------------- #
# Shared steps
# --------------------------------------------------------------------------- #


def _roots(args: argparse.Namespace) -> DiscoveryRoots:
    home = Path(args.home) if args.home is not None else Path.home()
    claude_home = Path(args.claude_home) if args.claude_home is not None else home / ".claude"
    codex_home = Path(args.codex_home) if args.codex_home is not None else home / ".codex"
    links: dict[Path, Path] = {}
    modes: dict[Path, int | None] = {}
    if args.declare is not None:
        try:
            declared = json.loads(Path(args.declare).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise CliError(f"--declare {args.declare}: unreadable: {error}") from error
        if not isinstance(declared, dict):
            raise CliError("--declare: expected a JSON object")
        for link, target in (declared.get("links") or {}).items():
            links[Path(str(link))] = Path(str(target))
        for path, mode in (declared.get("modes") or {}).items():
            modes[Path(str(path))] = None if mode is None else int(mode)
    return DiscoveryRoots(
        home=home,
        claude_home=claude_home,
        codex_home=codex_home,
        project_dir=Path(args.project) if args.project is not None else None,
        known_projects=tuple(args.known_project),
        links=links,
        modes=modes,
    )


def _collect(
    args: argparse.Namespace, machine: Machine
) -> tuple[list[CollectionResult], DiscoveryRoots]:
    roots = _roots(args)
    manifest = load_manifest(args.sources, today=_today())
    discovery = discover(roots, salt=args.salt)
    now = _now()
    results: list[CollectionResult] = []
    harnesses = tuple(Harness) if args.harness == "both" else (Harness(args.harness),)
    if Harness.CLAUDE in harnesses:
        invocation = (
            ClaudeInvocation.load(args.invocation_claude, harness=Harness.CLAUDE)
            if args.invocation_claude is not None
            else None
        )
        results.append(
            collect_claude(
                discovery,
                machine=machine,
                invocation=invocation,
                manifest=manifest,
                collected_at=now,
            )
        )
    if Harness.CODEX in harnesses:
        invocation = (
            CodexInvocation.load(args.invocation_codex, harness=Harness.CODEX)
            if args.invocation_codex is not None
            else None
        )
        results.append(
            collect_codex(
                discovery,
                machine=machine,
                invocation=invocation,
                manifest=manifest,
                collected_at=now,
                salt=args.salt,
            )
        )
    return results, roots


def _bundle_path(output: Path, machine: Machine) -> Path:
    return Path(output) / f"bundle-{machine.value}.json"


def _load_bundles(output: Path) -> list[RedactedBundle]:
    bundles: list[RedactedBundle] = []
    for path in sorted(Path(output).glob("bundle-*.json")):
        bundles.append(parse_bundle(path.read_text(encoding="utf-8")))
    if not bundles:
        raise CliError(f"no bundle-*.json in {output}; run `audit` or `import` first")
    return bundles


def _write_report(
    output: Path, bundles: Sequence[RedactedBundle], parity: ParityReport | None
) -> str:
    report = build_report(bundles, parity=parity, generated_at=_now())
    text = render_text(report)
    (Path(output) / "report.txt").write_text(text, encoding="utf-8")
    (Path(output) / "report.json").write_text(render_json(report), encoding="utf-8")
    return text


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def cmd_audit(args: argparse.Namespace) -> int:
    machine = Machine(args.machine)
    results, roots = _collect(args, machine)
    manifest = load_manifest(args.sources, today=_today())
    findings = run_rules(results, manifest=manifest)
    classifications = classify(results, findings)
    now = _now()
    gaps: list[CoverageGap] = [r.coverage_gap for r in results if r.coverage_gap is not None]
    inspected = {r.inventory.target for r in results}
    for harness in Harness:
        target = AuditTarget(machine, harness)
        if target not in inspected and target not in {g.target for g in gaps}:
            gaps.append(
                CoverageGap(
                    target=target,
                    reason=f"--harness {args.harness} did not inspect {harness.value}",
                    missing_evidence=(f"{harness.value} configuration on {machine.value}",),
                    observed_at=now,
                )
            )
    bundle = build_redacted_bundle(
        machine=machine,
        created_at=now,
        inventories=[r.inventory for r in results],
        findings=findings,
        classifications=classifications,
        coverage_gaps=gaps,
        private_bodies=[body for r in results for body in r.private_bodies],
        environment={name: "" for r in results for name in r.environment},
        salt=args.salt,
    )
    serialized = serialize_bundle(bundle)  # fails closed before anything is written
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    _bundle_path(output, machine).write_text(serialized, encoding="utf-8")
    paths = {item_id: str(path) for item_id, path in absolute_paths(results, roots).items()}
    (output / f"paths-{machine.value}.json").write_text(
        json.dumps(paths, indent=2, sort_keys=True), encoding="utf-8"
    )
    for result in results:
        for note in result.notes:
            print(f"note ({result.inventory.target.key}): {note}")
    print(_write_report(output, [bundle], None), end="")
    return EXIT_OK


def cmd_export(args: argparse.Namespace) -> int:
    source = _bundle_path(args.output, Machine(args.machine))
    if not source.is_file():
        raise CliError(f"{source} does not exist; run `audit` first")
    text = serialize_bundle(parse_bundle(source.read_text(encoding="utf-8")))
    Path(args.to).parent.mkdir(parents=True, exist_ok=True)
    Path(args.to).write_text(text, encoding="utf-8")
    print(f"exported redacted bundle to {args.to}")
    return EXIT_OK


def cmd_import(args: argparse.Namespace) -> int:
    bundle = parse_bundle(Path(args.source).read_text(encoding="utf-8"))
    text = serialize_bundle(bundle)  # re-verifies: a leaky bundle never lands in --output
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    destination = _bundle_path(output, bundle.machine)
    if destination.exists() and not args.force:
        # A foreign bundle that claims this machine's name would otherwise
        # replace the one the audit wrote here, and `compare` would report on
        # someone else's inventory as if it were local.
        raise CliError(
            f"{destination} already exists for machine {bundle.machine.value};"
            " pass --force to replace it"
        )
    destination.write_text(text, encoding="utf-8")
    print(
        f"imported {bundle.machine.value} bundle with"
        f" {', '.join(inv.target.key for inv in bundle.inventories) or 'no inventories'}"
    )
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    bundles = _load_bundles(args.output)
    overlays = [MachineOverlay.load(path) for path in args.overlay]
    inventories = [inv for bundle in bundles for inv in bundle.inventories]
    parity = check_parity(inventories, overlays=overlays)
    print(_write_report(Path(args.output), bundles, parity), end="")
    return EXIT_OK


def cmd_quarantine_plan(args: argparse.Namespace) -> int:
    machine = Machine(args.machine)
    output = Path(args.output)
    bundle_path = _bundle_path(output, machine)
    paths_path = output / f"paths-{machine.value}.json"
    if not bundle_path.is_file() or not paths_path.is_file():
        raise CliError(f"run `audit --machine {machine.value} --output {output}` first")
    bundle = parse_bundle(bundle_path.read_text(encoding="utf-8"))
    paths = {
        item_id: Path(path)
        for item_id, path in json.loads(paths_path.read_text(encoding="utf-8")).items()
    }
    verdicts = (
        frozenset(Classification(v) for v in args.verdict)
        if args.verdict
        else frozenset(
            {Classification.REMOVE, Classification.MOVE_TO_PROJECT, Classification.LOAD_ON_DEMAND}
        )
    )
    manifest = plan_quarantine(
        bundle.classifications,
        paths,
        quarantine_dir=Path(args.quarantine_dir),
        machine=machine,
        created_at=_now(),
        verdicts=verdicts,
    )
    destination = output / f"quarantine-{manifest.manifest_id}.json"
    manifest.save(destination)
    print(f"manifest {destination}")
    print(f"would quarantine {len(manifest.entries)} file(s); nothing has been moved:")
    for entry in manifest.entries:
        print(f"  [{entry.classification.value}] {entry.display_path} -> {entry.disabled_location}")
    for skipped in manifest.skipped:
        print(f"  skipped [{skipped.classification.value}] {skipped.item_id}: {skipped.reason}")
    return EXIT_OK


def cmd_quarantine_apply(args: argparse.Namespace) -> int:
    manifest = QuarantineManifest.load(args.manifest)
    applied = apply_quarantine(manifest, now=_now(), roots=_roots(args))
    applied.save(args.manifest)
    print(f"quarantined {len(applied.entries)} file(s)")
    print(f"rollback: `rollback --manifest {args.manifest}`")
    return EXIT_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    manifest = QuarantineManifest.load(args.manifest)
    restored = rollback_quarantine(manifest, now=_now(), roots=_roots(args))
    restored.save(args.manifest)
    print(f"restored {len(restored.entries)} file(s) by hash")
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    machine = Machine(args.machine)
    manifest_record = QuarantineManifest.load(args.manifest)
    results, _ = _collect(args, machine)
    findings = run_rules(results, manifest=load_manifest(args.sources, today=_today()))
    targets = [target_passes(findings, r.inventory.target) for r in results]
    verified = {verdict.target: verdict.passed for verdict in targets}
    removal = removal_eligibility(manifest_record, verified)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    record = {
        "manifest_id": manifest_record.manifest_id,
        "verified_at": _now().isoformat(),
        "targets": [t.to_dict() for t in targets],
        "removal": [r.to_dict() for r in removal],
    }
    (output / f"verify-{manifest_record.manifest_id}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8"
    )
    for verdict in targets:
        state = "pass" if verdict.passed else "FAIL"
        print(f"{verdict.target.key}: {state}")
        for finding in verdict.blocking:
            print(f"  blocking: {finding.finding_id} — {finding.summary}")
    for entry in removal:
        state = "eligible for removal" if entry.eligible else "not eligible"
        print(f"{entry.item_id}: {state} — {entry.reason}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            return cmd_audit(args)
        if args.command == "export":
            return cmd_export(args)
        if args.command == "import":
            return cmd_import(args)
        if args.command == "compare":
            return cmd_compare(args)
        if args.command == "quarantine":
            if args.step == "plan":
                return cmd_quarantine_plan(args)
            if args.step == "apply":
                return cmd_quarantine_apply(args)
            return cmd_rollback(args)
        if args.command == "verify":
            return cmd_verify(args)
        if args.command == "rollback":
            return cmd_rollback(args)
    except CliError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    except SchemaError as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_USAGE
    parser.error(f"unknown command {args.command!r}")
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
