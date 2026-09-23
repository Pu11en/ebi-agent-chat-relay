# Harness configuration audit

`extensions/harness_audit/` answers one question with evidence: **what do Discord-launched
Claude and Codex actually load on DrewAI and the iMac, and what should change?** It reads
configuration, never runs a model or a CLI, and — except for the explicit quarantine step —
never writes into a harness directory. Every claim in its report is either a measured local fact
(a path, a hash, a size, a mode, an invocation flag, a transcript's metadata) or a citation to
pinned official vendor guidance in `extensions/harness_audit/sources.json`.

What it does not do: change models, reasoning effort, provider routing or subscriptions. Those
settings are read so the report can say which configuration path was inspected; every such item
is marked *excluded from cleanup* and always classified Keep.

## The pipeline

```
discover → collect_claude / collect_codex → run_rules → classify → build_redacted_bundle
        → (export / import) → check_parity → build_report
        → plan_quarantine → apply_quarantine → verify → rollback
```

| Stage | Module | What it produces |
|---|---|---|
| discovery | `discovery.py` | every file a harness *can* read under the approved roots: kind, scope, permissions, exact size, salted hash, secret-match status, value-free settings facts. Links outside the roots are excluded, not followed. |
| collection | `claude_collector.py`, `codex_collector.py` | one `HarnessInventory` per target with an evidence level per item: `loaded` (proven by invocation + session transcript/rollout on a covered CLI version), `configured` (in a read location, no session proof), `installed-only` (read only on invocation, excluded by the invocation, disabled), `unknown` (unparseable, unreadable, uncovered version). |
| rules | `rules.py` | findings for duplication, scope, size, permissions, load behaviour, precedence, dead configuration and project-content-is-global. Failures and unknowns are per item; passes are aggregated. |
| classify | `classify.py` | exactly one verdict per item: Keep, Fix, Move to project, Load only when needed, Remove — by fixed precedence; incomplete evidence can never produce Remove. |
| redaction | `redaction.py` | the bundle that may cross machines: identifiers, hashes, sizes, modes and verdicts; never a secret value or a prompt body. Serialization re-scans its own output and refuses to emit a leak. |
| parity | `parity.py` | canonical identity compared across harnesses and machines; adapters and declared machine differences are named exceptions, a missing target is a gap. |
| report | `report.py` | `report.txt` (plain language) and `report.json` (machine-readable), coverage first. |
| quarantine | `quarantine.py` | a manifest, a hash-verified move into a quarantine directory, a hash-verified rollback, and the removal-eligibility gate. |

## Before you start: the invocation record

Nothing can be proven *loaded* without knowing how the relay launches the CLI. Write one JSON
record per harness describing the argv ccdb builds for a Discord thread (the same flags
`claude_discord/claude/runner.py` and the Codex backend pass), the working directory and the
**names** of the environment variables the subprocess receives:

```json
{
  "harness": "claude",
  "cwd": "/home/drewp/projects/ebi-agent-chat-relay",
  "argv": ["-p", "--output-format", "stream-json", "--verbose", "--model", "opus",
           "--permission-mode", "acceptEdits", "--append-system-prompt", "<the bot prompt>",
           "--", "<prompt>"],
  "environment_names": ["ANTHROPIC_API_KEY", "CCDB_API_URL", "PATH"],
  "cli_version": "2.0.5"
}
```

The `--append-system-prompt` text (Codex: `-c developer_instructions=…`) is treated as a private
body: its size and hash are recorded, the text never leaves the machine. Environment *values* are
never read. If you skip the record the audit still runs; the report says so and every evidence
level stops at `configured`.

## Step 1 — audit each machine (read-only)

On DrewAI:

```bash
uv run python -m extensions.harness_audit.cli audit \
    --machine drewai \
    --project ~/projects/ebi-agent-chat-relay \
    --known-project ebi-agent-chat-relay \
    --invocation-claude ~/harness-audit/invocation-claude.json \
    --invocation-codex  ~/harness-audit/invocation-codex.json \
    --salt "<a shared phrase>" \
    --output ~/harness-audit
```

On the iMac, the same command with `--machine imac` **and the same `--salt`** — hashes are
salted so that content equality is only visible to someone who knows the salt, and two machines
can only prove two files are the same file if they used the same one.

`audit` writes only inside `--output`:

- `bundle-<machine>.json` — the redacted bundle (the only thing that should ever leave the machine),
- `paths-<machine>.json` — item id → absolute path (local; used by `quarantine plan`),
- `report.txt`, `report.json` — the single-machine report (marked PARTIAL: two of four targets).

Defaults: `--home` is `~`, `--claude-home` is `~/.claude`, `--codex-home` is `~/.codex`. If the
relay runs Codex from a ccdb-owned `CODEX_HOME`, pass it explicitly. `--harness claude|codex`
audits one harness and records the other as a coverage gap.

On Windows, or anywhere symlinks and POSIX modes cannot be inspected, pass `--declare FILE`:

```json
{"links": {"C:/Users/me/.claude/CLAUDE.md": "C:/Users/me/AGENTS.md"},
 "modes": {"C:/Users/me/.claude/settings.json": 384}}
```

Undeclared modes are reported `unknown` there (the permission check neither passes nor fails).

## Step 2 — bring the iMac bundle to DrewAI

```bash
# iMac
uv run python -m extensions.harness_audit.cli export --output ~/harness-audit --machine imac --to ~/imac-bundle.json
# copy ~/imac-bundle.json to DrewAI by any means; it contains no secret and no prompt body
# DrewAI
uv run python -m extensions.harness_audit.cli import --output ~/harness-audit --from ~/imac-bundle.json
```

`import` re-verifies the bundle through the same fail-closed redactor; a file that still matches a
secret rule is refused and nothing is written. It also refuses to replace a bundle already in
`--output` for the same machine name — a foreign file claiming to be `drewai` must not overwrite the
one this machine's `audit` wrote — unless you pass `--force`.

## Step 3 — compare and read the report

```bash
uv run python -m extensions.harness_audit.cli compare --output ~/harness-audit --overlay ~/harness-audit/imac-overlay.json
```

An **overlay** declares the differences a machine has on purpose so parity names them instead
of failing them:

```json
{"machine": "imac",
 "exceptions": [
   {"exception_id": "imac-subscription", "kind": "subscription",
    "description": "the iMac subscription cannot use the larger models",
    "preserved_outcome": "the same rules, skills and project guidance load",
    "covers": ["setting:model", "setting:model_reasoning_effort"]},
   {"exception_id": "imac-no-github-mcp", "kind": "installed-tool", "harness": "claude",
    "description": "gh-mcp is not installed on the iMac",
    "preserved_outcome": "GitHub work is done through the gh CLI there",
    "covers": ["connector:github"]}]}
```

Model and reasoning-effort differences are exceptions even without an overlay: the audit never
recommends fabricating an option one subscription cannot use.

Read `report.txt` top to bottom:

1. **Coverage** — `COMPLETE (4/4 targets)` or `PARTIAL` with each missing target named. Nothing
   is inferred for a missing target.
2. **Inventory per target** — `[loaded]` items count toward context; `[installed-only]` items are
   listed under "not counted toward effective context"; `[unknown]` items say what evidence is
   missing.
3. **Token impact** — exact bytes and characters proven loaded at every session start, plus a
   token *estimate* labeled with its method. It is not provider billing data.
4. **Findings** — failures and unknowns first, each with its local evidence and, when the verdict
   rests on documented vendor behaviour, the official source and its retrieval date.
5. **Verdicts** — one per item, with reason, proposed scope, risk and the reversible next action.
6. **Parity** and **Machine exceptions**.

## Step 4 — quarantine (the only step that writes into a harness directory)

```bash
uv run python -m extensions.harness_audit.cli quarantine plan --output ~/harness-audit --machine drewai --quarantine-dir ~/harness-audit/quarantine
```

The plan writes a manifest (`quarantine-<id>.json`) and moves nothing. It names whole files whose
verdict is Remove, Move to project or Load only when needed (add `--verdict fix` to include Fix
items); hooks, MCP servers and setting keys are listed as *skipped* — edit those under version
control. Review the manifest, then:

```bash
uv run python -m extensions.harness_audit.cli quarantine apply --manifest ~/harness-audit/quarantine-<id>.json [same root flags as audit]
uv run python -m extensions.harness_audit.cli verify --output ~/harness-audit --machine drewai --manifest ~/harness-audit/quarantine-<id>.json [same root flags as audit]
```

`apply` verifies every file's hash first and aborts before moving anything on a mismatch; it moves
files into the quarantine directory and never deletes. The manifest is data, not authority: `apply`
and `rollback` take the same root flags as `audit` and refuse any entry whose original path is
outside those roots or whose disabled location is outside `<quarantine-dir>/<manifest-id>`, so an
edited manifest cannot move `~/.ssh/id_rsa` or plant a file elsewhere. `verify` re-collects, re-runs the checks and
writes `verify-<id>.json`: a target passes when nothing HIGH fails and nothing is unknown on it.
Run it on every affected machine. If something a session needs is missing:

```bash
uv run python -m extensions.harness_audit.cli rollback --manifest ~/harness-audit/quarantine-<id>.json [same root flags as audit]
```

restores every file by hash. An item is **eligible for removal** only when its verdict is Remove,
the quarantine is applied and not rolled back, and every affected target has been verified passing.
Removal itself is a later, reviewed action; the CLI never performs it.

## Keeping the vendor guidance current

`sources.json` pins the official Claude Code and Codex pages each vendor-backed rule relies on,
with retrieval date, review date and CLI version range. The audit does no web request; when a
source or the manifest passes its review date the run refuses to start (`refused: … past its review
date`). Refreshing the file is a reviewed data change, not something done mid-audit.

## Try it on the fixtures

The sanitized DrewAI/iMac trees under `tests/fixtures/harness_audit/` (regenerate with
`uv run python tests/fixtures/harness_audit/generate.py`) drive the same commands end to end:

```bash
bash scripts/test-clean-env.sh tests/test_harness_audit_dry_run.py -q
```

That test follows steps 1–4 exactly and asserts the run leaves every file outside its output
directories byte-identical.
