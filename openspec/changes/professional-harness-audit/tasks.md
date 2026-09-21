Check: `uv run pytest tests/test_harness_audit_*.py -q`

Try: `uv run python -m extensions.harness_audit.cli audit --machine drewai --harness claude --output /tmp/harness-audit`

## 1. Evidence contracts

- [x] 1.1 **Depends on: none; owns: `extensions/harness_audit/models.py`, `tests/test_harness_audit_models.py`.** Define versioned inventory, evidence-level, finding, classification, machine-exception, and coverage-gap records; verify schema round trips and rejects unknown required fields.
- [x] 1.2 **Depends on: 1.1; owns: `extensions/harness_audit/redaction.py`, `tests/test_harness_audit_redaction.py`.** Implement safe metadata and secret redaction rules; verify fixture credentials and private bodies never appear in serialized bundles.
- [x] 1.3 **Depends on: 1.1; owns: `extensions/harness_audit/sources.json`, `extensions/harness_audit/sources.py`, `tests/test_harness_audit_sources.py`.** Add pinned official Claude Code and Codex guidance records with version/date validation; verify expired, malformed, or non-official entries fail closed.

## 2. Read-only collection

- [x] 2.1 **Depends on: 1.1, 1.2; owns: `extensions/harness_audit/discovery.py`, `tests/test_harness_audit_discovery.py`.** Discover global/project instructions, memory, skills, tools, plugins, connectors, commands, hooks, bot additions, and native settings with source/scope/permissions/size/hash metadata; verify files outside approved roots and secret values are excluded.
- [x] 2.2 **Depends on: 2.1; owns: `extensions/harness_audit/claude_collector.py`, `tests/test_harness_audit_claude.py`.** Resolve Claude effective inputs from configuration, invocation, symlinks, and existing session metadata; verify loaded, configured, installed-only, and unknown fixtures are distinguished without a model call.
- [x] 2.3 **Depends on: 2.1; owns: `extensions/harness_audit/codex_collector.py`, `tests/test_harness_audit_codex.py`.** Resolve Codex effective inputs from native config, invocation, symlinks, skill discovery, and existing rollout metadata; verify evidence levels without changing model or reasoning settings.

## 3. Deterministic findings

- [x] 3.1 **Depends on: 1.1, 1.3, 2.2, 2.3; owns: `extensions/harness_audit/rules.py`, `tests/test_harness_audit_rules.py`.** Implement checks for duplicate content, wrong scope, exact size/token-estimate labels, permissions, precedence/loading, dead configuration, and project-specific global content; verify every fixture finding cites local evidence and official guidance when required.
- [x] 3.2 **Depends on: 3.1; owns: `extensions/harness_audit/classify.py`, `tests/test_harness_audit_classify.py`.** Assign exactly one Keep/Fix/Move to project/Load only when needed/Remove verdict with fixed precedence and safe unknown handling; verify incomplete evidence can never produce Remove.
- [x] 3.3 **Depends on: 2.2, 2.3, 3.2; owns: `extensions/harness_audit/parity.py`, `tests/test_harness_audit_parity.py`.** Compare canonical portable behavior and named harness/machine adapters across DrewAI and iMac; verify unsupported subscription/model/tool differences are exceptions rather than false parity failures.

## 4. Reports and reversible cleanup

- [ ] 4.1 **Depends on: 3.2, 3.3; owns: `extensions/harness_audit/report.py`, `tests/test_harness_audit_report.py`.** Render machine-readable and plain-language inventories, coverage gaps, evidence, verdicts, and token-impact measurements; verify a four-target fixture is complete and a missing target is plainly marked partial.
- [ ] 4.2 **Depends on: 3.2; owns: `extensions/harness_audit/quarantine.py`, `tests/test_harness_audit_quarantine.py`.** Generate/apply/rollback quarantine manifests without permanent deletion; verify hash mismatch aborts, rollback restores fixtures, and Remove remains ineligible until affected target checks pass.
- [ ] 4.3 **Depends on: 4.1, 4.2; owns: `extensions/harness_audit/cli.py`, `extensions/harness_audit/__init__.py`, `tests/test_harness_audit_cli.py`.** Wire local audit, redacted export/import, compare, quarantine, verify, and rollback commands; verify default audit is read-only and no command changes effort, model, routing, or subscription settings.

## 5. Integrated verification

- [ ] 5.1 **Depends on: 4.3; integration owner only; owns: `tests/fixtures/harness_audit/`, `docs/harness-configuration-audit.md`.** Add sanitized DrewAI/iMac-style fixtures and operator instructions, run `uv run pytest tests/test_harness_audit_*.py -q`, and verify the documented dry run produces no filesystem changes outside its output directory.
- [ ] 5.2 **Depends on: 5.1; integration owner only; owns: no source files.** Run `uv run ruff check extensions/harness_audit tests/test_harness_audit_*.py`, `uv run pyright extensions/harness_audit`, and the project security checklist; record passing commands before any real quarantine is proposed.

## How to try it

1. Run the audit on one DrewAI harness and confirm the report separates loaded items from installed-only items without exposing secret values.
2. Import the redacted iMac bundle and confirm all four machine/harness targets appear or the unavailable target is clearly marked as a coverage gap.
3. Generate a quarantine plan, apply it only to disposable fixtures, then roll it back and confirm the original hashes and files are restored.
