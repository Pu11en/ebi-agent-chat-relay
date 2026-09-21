## Why

Discord Codex and Claude load overlapping global, project, skill, tool, plugin, hook, and bot
context, creating suspected token waste and inconsistent behavior when switching harnesses. Drew
needs a professional, vendor-backed audit and reversible cleanup rather than subjective AI grading.

## What Changes

- Inventory all custom additions plus built-in material that is actually loaded on DrewAI and
  iMac for both Claude and Codex.
- Use official vendor guidance and deterministic checks for duplication, scope, size, permissions,
  loading behavior, dead configuration, and misplaced project-specific content.
- Classify each item Keep, Fix, Move to project, Load only when needed, or Remove.
- Preserve one shared source for portable preferences, safety rules, project knowledge, memory,
  and skills, with thin harness-specific adapters for tools, permissions, models, and hooks.
- Quarantine or disable questionable items first, verify both harnesses, and remove only after the
  item proves unnecessary.
- Exclude reasoning effort, model intelligence, and model quality changes from this audit.

## Capabilities

### New Capabilities
- `harness-configuration-audit`: Deterministic cross-harness configuration inspection,
  classification, parity checks, and reversible cleanup.

### Modified Capabilities

None.

## Impact

This affects local audit tooling/skills, configuration discovery, reports, shared global files, and
harness-specific adapters. Cleanup must preserve secrets, ownership, project rules, and rollback.
