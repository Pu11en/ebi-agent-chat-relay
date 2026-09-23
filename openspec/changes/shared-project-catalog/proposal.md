## Why

Discord favorites/recents, configured roots, and each AI harness currently discover projects
differently, causing inconsistent folder choices and repeated token-heavy explanations. Each
computer needs one local, query-on-demand project catalog shared by its bot and harness adapters.

## What Changes

- Discover every direct child of each computer's approved main-projects roots automatically.
- Expose the same project identities to New session and local Claude, Codex, and DSH sessions.
- Keep folder trees and contents out of always-loaded prompts; resolve and inspect them only for a
  relevant request.
- Support Favorite and Hide metadata without making either action the source of truth for whether
  a project exists.
- Resolve explicit owner phrases such as “Drew's projects” and use trusted handoffs for remote
  computers rather than pretending remote paths are local.
- Keep DrewAI and iMac behavior aligned while allowing deliberate machine-specific paths and
  availability.

## Capabilities

### New Capabilities
- `shared-project-catalog`: Local project discovery and identity resolution shared across the
  Discord surface and installed harnesses without prompt-wide folder injection.

### Modified Capabilities

None.

## Impact

This affects project launcher discovery, configuration profiles, context/path resolution, harness
adapters, persistence, and cross-computer handoff integration.
