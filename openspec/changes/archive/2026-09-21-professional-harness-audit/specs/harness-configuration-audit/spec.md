## Purpose

Provides a repeatable, evidence-backed audit of the AI configuration actually loaded by DrewAI
and iMac so Claude and Codex stay consistent, efficient, secure, and easy to maintain.

## ADDED Requirements

### Requirement: Audit the approved machines and harnesses
The audit SHALL inspect Discord-launched Claude and Codex on DrewAI and iMac, SHALL identify the
machine and harness for every observation, and SHALL report an unavailable machine or harness as
an explicit coverage gap rather than silently omitting it.

#### Scenario: All four targets are reachable
- **WHEN** the audit runs while Claude and Codex configuration is readable on DrewAI and iMac
- **THEN** the result contains a separately identified inventory and verdict set for all four machine-and-harness targets

#### Scenario: One target is unavailable
- **WHEN** the audit cannot inspect one approved machine or harness
- **THEN** the result names the missing target and the evidence that could not be collected without claiming full coverage

### Requirement: Inventory effective custom and built-in inputs
The audit SHALL inventory custom preferences, instructions, memory, skills, tools, plugins,
connectors, commands, hooks, bot-supplied additions, and harness settings that can affect an
approved target. It SHALL include built-in material only when evidence shows that the material is
actually loaded, and SHALL distinguish effective inputs from files that merely exist.

#### Scenario: Installed item is not loaded
- **WHEN** a skill, hook, command, or configuration file exists but no approved target loads it
- **THEN** the inventory marks it as installed-but-not-loaded and does not count it toward effective context

#### Scenario: Input comes from more than one source
- **WHEN** equivalent instructions reach a target from global, project, bot, or harness-specific sources
- **THEN** the inventory lists every source, its scope, its precedence when known, and the effective target behavior

### Requirement: Keep sensitive values out of audit evidence
The audit MUST NOT copy authentication tokens, API keys, cookies, private prompt contents, or other
secret values into reports or cross-computer transfers. It SHALL record safe metadata such as
source type, path or identifier, permissions, size, hash, and redacted setting names when those are
needed to support a finding.

#### Scenario: Configuration contains a secret
- **WHEN** an inspected file or environment setting contains a credential value
- **THEN** the report redacts the value while retaining enough non-secret metadata to diagnose scope, loading, and permission problems

### Requirement: Base findings on reproducible evidence
Every finding SHALL cite deterministic evidence and, when the finding depends on intended vendor
behavior, the applicable official Claude Code or Codex guidance with its retrieval date or pinned
version. The audit MUST NOT use subjective model-answer quality as an answer key.

#### Scenario: Duplicate instructions are reported
- **WHEN** normalized content or a declared canonical relationship proves that two loaded sources repeat the same rule
- **THEN** the finding identifies both sources, the comparison method, and the duplicated size without asking a model to judge whether the answers feel similar

#### Scenario: Vendor behavior informs a finding
- **WHEN** a verdict depends on documented load order, scope, or configuration behavior
- **THEN** the finding cites an official vendor source and records the version or date against which the local evidence was checked

### Requirement: Apply the professional audit checks
For each effective item the audit SHALL evaluate scope, duplication, measured size, permissions,
load behavior, source precedence, dead configuration, and whether project-specific content is
incorrectly global. A check that cannot be determined SHALL be reported as unknown with the
missing evidence, not converted into a pass or failure.

#### Scenario: Project knowledge is global
- **WHEN** an instruction or memory item applies to one project but is loaded into unrelated projects
- **THEN** the audit reports the scope mismatch and recommends moving it to that project's folder

#### Scenario: Dead configuration is found
- **WHEN** a setting, command, plugin, hook, or adapter is shadowed, unreachable, or unsupported by its target
- **THEN** the audit reports why it is ineffective and the evidence that establishes that state

#### Scenario: Token-impacting size is measured
- **WHEN** content is injected into every turn or session start
- **THEN** the audit reports its exact byte or character size and a clearly labeled token estimate or measured token count without presenting the estimate as provider billing data

### Requirement: Classify every audited item
Every audited item SHALL receive exactly one actionable classification: Keep, Fix, Move to
project, Load only when needed, or Remove. Each classification SHALL include the reason, evidence,
affected targets, proposed scope, risk, and a reversible next action.

#### Scenario: Item is safe but unnecessarily global
- **WHEN** an item is useful only during a particular workflow and otherwise adds repeated context
- **THEN** it is classified Load only when needed with a concrete activation boundary

#### Scenario: Evidence is incomplete
- **WHEN** available evidence cannot justify changing an item
- **THEN** the item is classified Keep or Fix with the uncertainty recorded and is not classified Remove

### Requirement: Preserve equivalent portable behavior across harnesses
Portable preferences, safety rules, project knowledge, memory, and skills SHALL have one canonical
shared source wherever both harnesses can support the behavior. Claude- and Codex-specific adapters
SHALL remain limited to differences in tool wiring, permissions, models, hooks, or native format,
and the audit SHALL compare behavioral parity rather than requiring byte-identical configuration.

#### Scenario: Shared rule has two maintained copies
- **WHEN** Claude and Codex load separately maintained copies of the same portable rule
- **THEN** the audit recommends one canonical source plus the smallest necessary adapters and names any migration risk

#### Scenario: A harness requires a native exception
- **WHEN** one harness cannot consume the shared representation or needs different permission wiring
- **THEN** the audit records the exception as an adapter while preserving the same user-visible outcome where the harness supports it

### Requirement: Preserve deliberate machine differences
DrewAI and iMac SHALL use the same portable configuration profile by default, while deliberate
differences caused by computer, subscription, available models, operating system, or installed
tools SHALL be recorded as named exceptions. Unsupported features or models MUST NOT be treated as
parity failures solely because another machine supports them.

#### Scenario: iMac lacks an available model
- **WHEN** DrewAI can use a model or feature that the iMac subscription or installation cannot use
- **THEN** the audit records the supported machine-specific exception and does not recommend fabricating an unavailable option

### Requirement: Cleanup is reversible and verified
Questionable items SHALL first be quarantined or disabled with a rollback record. An item MUST NOT
be permanently removed until Claude and Codex on both available machines pass the applicable
deterministic configuration and parity checks after quarantine.

#### Scenario: Quarantined item proves necessary
- **WHEN** a post-quarantine check shows a required command, rule, skill, or tool is missing
- **THEN** the rollback restores the item and the audit updates its classification without deleting it

#### Scenario: Removal is justified
- **WHEN** an item remains unnecessary through quarantine and all affected target checks pass
- **THEN** the report marks it eligible for removal and preserves the evidence and rollback reference

### Requirement: Exclude model quality tuning
The audit MUST NOT change reasoning effort, model intelligence, model quality, or model-selection
preferences. Observations about those settings SHALL be reported only when necessary to identify
which configuration path was inspected.

#### Scenario: Reasoning settings differ
- **WHEN** Claude and Codex use different reasoning or effort settings
- **THEN** the audit leaves them unchanged and excludes them from cleanup recommendations
