## Context

The relay constructs Claude and Codex runners through shared backend selection, but the two CLIs
also load their own global files, project files, skills, native settings, and session metadata. The
global memory currently uses a canonical `~/AGENTS.md` with harness-facing links, while the relay
can add a system prompt and harness-specific arguments. File presence alone therefore does not
prove that content reaches a Discord-launched session. This change is local operational tooling and
belongs under `extensions/`, not in the reusable relay core. See `proposal.md` for motivation and
`specs/harness-configuration-audit/spec.md` for required behavior.

The same audit must run on DrewAI and iMac without copying secrets or full private content between
computers. The evidence needs to survive review, support rollback, and distinguish facts from token
estimates. Existing model, effort, and quality choices are intentionally outside the audit.

## Goals / Non-Goals

**Goals:**

- Produce a normalized, machine-readable inventory and a plain-language report from deterministic
  collectors and rules.
- Explain what is effectively loaded into Discord Claude and Codex, where it comes from, and what
  it costs in repeated context size.
- Make portable behavior share one canonical source while representing real harness and machine
  exceptions explicitly.
- Make cleanup reversible, evidence-backed, and independently verifiable on each target.

**Non-Goals:**

- Grading the intelligence, writing quality, or reasoning style of a model response.
- Changing models, reasoning effort, subscriptions, or provider routing.
- Displaying built-in platform internals that are not loaded or actionable.
- Automatically deleting, rewriting, or publishing configuration during an audit run.
- Treating a token estimate as an invoice or claiming cost savings the providers do not expose.

## Decisions

### Use a local extension with a stable evidence schema

Add an `extensions/harness_audit/` package whose collectors emit versioned records for machine,
harness, source, scope, load evidence, precedence, permissions, measured size, safe hash, and
redaction status. Rules consume only that schema and emit findings/classifications. This separates
collection from policy and lets fixtures prove the rules without launching a paid model.

Alternative considered: put audit logic in a Discord Cog. Rejected because discovery and rules
must also run headlessly on iMac, and personal filesystem policy does not belong in ccdb core.

### Prove effective loading from invocation and native metadata

Collectors combine static discovery with the arguments, environment names, resolved symlinks, CLI
configuration, skill manifests, hook registration, bot-provided prompt inputs, and already-produced
session/rollout metadata available for a Discord-launched target. Each record carries an evidence
level: `loaded`, `configured`, `installed-only`, or `unknown`. The report never upgrades
`configured` or `installed-only` to `loaded` merely because a file exists.

Alternative considered: ask each model to recite its prompt. Rejected because it spends tokens,
has no reliable answer key, can omit hidden inputs, and turns a deterministic configuration audit
into subjective model behavior.

### Keep raw evidence local and aggregate redacted summaries

Each machine writes its own inventory and finding bundle. Cross-computer collection transfers only
the versioned redacted bundle: secret values and private prompt bodies remain local, while safe
identifiers, hashes, sizes, permissions, and source relationships permit comparison. The report
records missing machines as gaps. The trusted handoff feature can transport bundles when present;
the audit CLI also supports explicit import/export so this change remains testable independently.

Alternative considered: read both machines through a shared filesystem. Rejected because paths,
permissions, and availability differ and it would blur the security boundary.

### Pin official guidance in a source manifest

Rules that encode vendor behavior reference a source manifest containing official URL, retrieval
date, applicable CLI version range, and content hash or quoted proposition. A source refresh is a
reviewed data change, not an untracked web lookup during every audit. Pure filesystem checks do not
need vendor citations but still include direct local evidence.

Alternative considered: live web research on every run. Rejected because results can drift, makes
offline audits fail, and weakens reproducibility.

### Classify by deterministic rules with explicit unknowns

Rules cover duplicate normalized content, invalid global scope, size and repeated-load impact,
unsafe permissions, shadowed or dead configuration, unsupported entries, and canonical-source
drift. Every item receives one of the five approved classifications. Competing rules use a fixed
severity and specificity order, and unknown evidence prevents destructive classifications.

Alternative considered: let an agent freely decide labels. Rejected because two runs could disagree
and the cleanup would not have an answer key.

### Model parity as outcomes plus named adapters

The shared profile owns portable preferences, safety rules, memory index, project guidance, and
skills. A harness adapter records only native settings needed to make that profile effective.
Machine overlays record subscription, operating-system, installed-tool, or availability exceptions.
Parity checks compare canonical identity, required outcomes, and declared exceptions—not the text
format of Claude and Codex files.

Alternative considered: force identical files. Rejected because native configuration formats and
supported features legitimately differ.

### Separate audit, quarantine, verification, and removal

The default command is read-only. A generated remediation bundle names proposed moves and creates a
quarantine manifest with original path, safe hash, disabled location or switch, time, and rollback
action. Applying quarantine is a distinct explicit operation. Verification reruns targeted rules on
all affected targets. Permanent removal is only a later eligible action after successful evidence;
it is never part of the initial audit.

Alternative considered: auto-fix obvious findings. Rejected because global configuration affects
every project and both harnesses, so a false positive has a large blast radius.

## Risks / Trade-offs

- **[CLI formats change]** → Version collectors and source rules, preserve unknown fields, and fail
  a target to `unknown` rather than inferring loaded behavior from an unfamiliar version.
- **[Hashes still reveal equality across machines]** → Hash only approved normalized fields, salt
  sensitive comparisons locally, and transfer content-free relationship identifiers when equality
  itself is sensitive.
- **[Token estimates are mistaken for spend]** → Label the method and unit on every estimate and
  keep exact bytes/characters as the authoritative measurement.
- **[Quarantine changes global behavior]** → Require a manifest and rollback operation, change one
  finding group at a time, and rerun both harness checks before further cleanup.
- **[A machine is offline]** → Produce a partial report with an explicit coverage gap; never infer
  parity or authorize removal for the missing target.
- **[Canonical sharing hides necessary differences]** → Require named harness and machine overlays
  and test the supported user-visible outcome rather than file identity.

## Migration Plan

1. Add the read-only schema, collectors, redaction, deterministic rules, source manifest, and
   fixture-based tests without changing any existing configuration.
2. Run local inventories on DrewAI and iMac and combine only redacted bundles into the first report.
3. Review classifications; generate a quarantine manifest for selected Fix, Move, Load-on-demand,
   or Remove candidates without applying it by default.
4. Quarantine the selected items in a reversible batch and run the deterministic Claude/Codex
   checks on each available machine.
5. Restore failures immediately. Mark proven-unnecessary items eligible for a later removal pass.
6. Roll back at any time by applying the quarantine manifest in reverse; inventories and reports
   remain read-only evidence.
