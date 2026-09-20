## Why

Drew needs one factual Discord view of custom AI setup across computers and harnesses so misplaced,
duplicated, or unnecessary customization is visible without running an AI audit every day. The
view must remain understandable and must not become a dangerous configuration editor.

## What Changes

- Add Settings → My AI Setup as a normal Discord interaction.
- Inventory custom preferences, instructions, memory, skills, tools, plugins/connectors, commands,
  hooks, and harness settings while hiding built-in defaults unless requested.
- Open on Browse by kind and provide Where it lives, Compare computers, Search, filters, and Recent
  changes.
- Show every item's source, scope, verified harness availability, prerequisites, size/token
  estimate when measurable, and last change.
- Add Ask Setup Agent to open a scoped management session with the selected item attached.
- Keep the technical Mega Global ownership model internal for audit logic rather than exposing it
  as a user-facing screen.

## Capabilities

### New Capabilities
- `custom-ai-inventory`: Read-only Discord inventory and comparison of user-added AI configuration
  with a safe agent-management handoff.

### Modified Capabilities

None.

## Impact

This affects Settings UI, inventory adapters for each harness/computer, redaction, paging/search,
and session creation. It must never display secrets or claim unavailable remote state is verified.
