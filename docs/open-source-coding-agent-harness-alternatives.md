# Open-source alternatives to Better Harness

**Research date:** 2026-09-07 (America/Chicago)  
**Scope:** tools directly usable by or around Codex: Codex plugins/skills/MCP, `AGENTS.md` and project bootstrap, Codex-supported lifecycle controls, session evaluation, and orchestration that invokes Codex. Standalone replacement agents are excluded. Sources are project-owned repositories, documentation, and GitHub's repository API.

## Bottom line

There is no proven drop-in, strictly better replacement for Better Harness. Better Harness is primarily a **read-only diagnostic and continuous-improvement layer**: it collects bounded session, project, and configured-agent evidence; runs three independent review lanes; reconciles evidence-backed findings; and renders repair-oriented reports. Its public description explicitly separates configured capability from evidence that a mechanism was actually used or improved an outcome ([README](https://github.com/QoderAI/better-harness#what-is-open), [workflow](https://github.com/QoderAI/better-harness/blob/main/skills/better-harness/SKILL.md), [architecture](https://github.com/QoderAI/better-harness/blob/main/docs/ARCHITECTURE.md)). Most alternatives below instead **operate** the loop or **observe instrumented executions**.

For Drew's Codex/Lockin setup, the best practical direction is:

1. **Pilot HAR first** as the operational harness for isolated worktrees, deterministic validation, evidence artifacts, and multi-agent runs. It explicitly supports Codex and exposes an MCP server Codex can call, has an Apache-2.0 license, and is the strongest current match for “outer harness that controls and verifies work” ([repository](https://github.com/os-factory/har), [license](https://github.com/os-factory/har/blob/main/LICENSE), [v1.14.1 release](https://github.com/os-factory/har/releases/tag/v1.14.1)).
2. **Keep Better Harness during the pilot** as the independent retrospective auditor. HAR does not appear to reproduce Better Harness's cross-session diagnosis, evidence-state semantics, five-dimension scoring, asset-demand discovery, or finding-bound repair workflow; replacing it immediately would remove those controls ([HAR README](https://github.com/os-factory/har), [Better Harness work-loop model](https://github.com/QoderAI/better-harness/blob/main/models/agent-work-loop.md)).
3. If the main pain is duplicated Codex/Claude/Cursor configuration, evaluate **madebywild/agent-harness** alongside HAR. It is a configuration compiler/registry for prompts, skills, MCP, subagents, commands, and hooks—not an execution verifier or retrospective evaluator ([README](https://github.com/madebywild/agent-harness)).
4. Only adopt **aharness** if Lockin wants a Codex-specific, explicit finite-state-machine workflow. It offers unusually deep Codex App Server integration, but it is young, more invasive, and shifts workflow ownership into TypeScript/XState machines ([architecture](https://github.com/Alfredvc/aharness/blob/main/docs/architecture.md), [repository API](https://api.github.com/repos/Alfredvc/aharness)).

## What Better Harness actually covers

The installed package is `@qoder-ai/better-harness` version `0.7.0-alpha1`, MIT-licensed, with adapters for Codex, Claude Code, Qoder, Cursor, GitHub Copilot CLI, Qwen Code, Pi, Kimi Code, WorkBuddy, and Grok ([package metadata](https://github.com/QoderAI/better-harness/blob/main/package.json), [adapter matrix](https://github.com/QoderAI/better-harness/blob/main/docs/adapters/README.md)). Its distinguishing scope is:

- Collection of a versioned evidence bundle across session evidence, project harness evidence, configured agent assets, and a lead-analysis envelope ([skill workflow](https://github.com/QoderAI/better-harness/blob/main/skills/better-harness/SKILL.md)).
- Independent specialist review followed by lead reconciliation, explicit evidence states, conservative scoring, and durable findings with repair boundaries and verifiers ([work-loop model](https://github.com/QoderAI/better-harness/blob/main/models/agent-work-loop.md)).
- Inspection of rules, skills, MCP, memory metadata, agents, hooks, commands, workflows, plugins, tests, CI, sessions, and project history, while treating inventory as capability—not proof of use ([README](https://github.com/QoderAI/better-harness#how-better-harness-works)).
- Host-adapted reports and finding-bound repair, rather than owning the coding agent's runtime state machine or worktree fleet ([architecture](https://github.com/QoderAI/better-harness/blob/main/docs/ARCHITECTURE.md)).

That makes “alternative” ambiguous. The comparison below distinguishes **direct Codex operational substitutes** from **Codex-callable complements**.

## Codex extension boundary

Official OpenAI documentation currently describes plugins as bundles that extend Codex with **skills, MCP servers, and optional UI** ([OpenAI Developers](https://developers.openai.com/)). A plugin manifest can make a skill discoverable—the installed Better Harness Codex manifest itself points `skills` at `./skills/`—but a plugin label does not automatically grant a portable lifecycle runtime.

Treat the surfaces separately:

- `AGENTS.md` and skills guide model behavior; they do not mechanically enforce a state machine.
- MCP servers provide callable tools and external state; HAR's MCP endpoint is useful through this supported route ([HAR README](https://github.com/os-factory/har)).
- Codex project configuration can materialize MCP, subagent, and hook settings, as madebywild/agent-harness documents in its generated-output matrix ([README](https://github.com/madebywild/agent-harness#generated-outputs)). Those are provider-specific generated files, not generic plugin-manifest capabilities.
- Deep lifecycle ownership requires an external controller over Codex App Server, as aharness and Harness CLI implement ([aharness architecture](https://github.com/Alfredvc/aharness/blob/main/docs/architecture.md), [Harness CLI](https://github.com/hyspacex/harness-cli#codex-app-server)).
- I did not find an official OpenAI claim that a Codex plugin manifest has portable first-class `commands`, `agents`, or arbitrary lifecycle-hook fields. Do not infer those capabilities from Claude/Cursor plugin layouts or from a repository containing similarly named folders.

### If Codex-native discovery is the requirement

Two additional projects package their workflows as installable Codex plugins, so Codex can discover them as skills and/or MCP tools rather than relying on a separately launched controller:

- [Intense-Visions/harness-engineering](https://github.com/Intense-Visions/harness-engineering) publishes a `harness-codex` manifest containing Codex skills plus a pinned `@harness-engineering/cli` MCP server. Its broader project includes validation, architecture checks, project initialization, knowledge graphs, and policy-oriented workflows. This is the closest candidate to a broad **native Better Harness alternative**, but its own documentation says Codex receives skills + MCP while richer commands, personas, and lifecycle hooks are available on other hosts; adoption telemetry is enabled by default unless disabled ([Codex manifest](https://github.com/Intense-Visions/harness-engineering/blob/main/.codex-plugin/plugin.json), [installation and capability matrix](https://github.com/Intense-Visions/harness-engineering#quick-start)).
- [Harnessworks Harness Agent Skills](https://github.com/harnessworks/harness-starter-kit/tree/main/agent-skills) is a smaller MIT-licensed Codex plugin exposing `doctor`, `adopt`, `review`, `update`, and `refresh` as portable skills. It is the safer lightweight choice when the desired result is repository guidance and audit/adoption workflows, but it is prompt-first and does not provide Better Harness's bounded session-evidence collection or controlled experiments ([Codex installation](https://github.com/harnessworks/harness-starter-kit#install-agent-skills), [plugin manifest](https://github.com/harnessworks/harness-starter-kit/blob/main/agent-skills/.codex-plugin/plugin.json)).

Therefore, choose **Intense-Visions `harness-codex`** if “appears directly in Codex and offers broad harness controls” is the primary requirement; choose **Harnessworks** for a smaller review/adoption skill set. HAR remains the stronger operational pilot when MCP-callable worktree isolation and deterministic proof matter more than appearing as a broad skill bundle.

## Shortlist

| Project | Category | Best fit | Codex fit | License / maturity snapshot | Adoption cost | Verdict |
|---|---|---|---|---|---|---|
| [HAR](https://github.com/os-factory/har) | Closest operational substitute | Isolated agent worktrees, repeatable launch/verify/teardown, proof artifacts, fleet dashboard | Explicit Codex support and MCP server | Apache-2.0; created 2026-06-28; 85 stars; pushed 2026-09-07; v1.14.1 ([API](https://api.github.com/repos/os-factory/har), [release](https://github.com/os-factory/har/releases/tag/v1.14.1)) | Medium | **Best first pilot** |
| [madebywild/agent-harness](https://github.com/madebywild/agent-harness) | Complement / partial substitute | One canonical source for prompts, skills, MCP, subagents, hooks, and commands across providers | Generates `AGENTS.md`, `.codex/skills/`, `.codex/config.toml`, agents, and hooks | MIT; created 2026-02-06; 13 stars; pushed 2026-09-01; v2.1.0 ([API](https://api.github.com/repos/madebywild/agent-harness), [release](https://github.com/madebywild/agent-harness/releases/tag/v2.1.0)) | Low–medium, but migration can rewrite provider config | **Best asset/config layer** |
| [aharness](https://github.com/Alfredvc/aharness) | Direct Codex workflow engine | Typed FSMs, gates, approvals, durable events/artifacts, sidecar Codex threads | Native `codex app-server` WebSocket client; validates Codex skills before starting | Apache-2.0; created 2026-05-28; 16 stars; latest release v0.1.3 in June ([API](https://api.github.com/repos/Alfredvc/aharness), [release](https://github.com/Alfredvc/aharness/releases/tag/v0.1.3)) | High | **Powerful but experimental** |
| [Harness CLI](https://github.com/hyspacex/harness-cli) | Opinionated execution substitute | Long-running application builds with research/plan/generate/evaluate/repair roles and persisted state | Uses Codex App Server via JSON-RPC; manages auth, resume, approvals | Apache-2.0; created 2026-04-04; 15 stars; latest release v0.4.0 ([API](https://api.github.com/repos/hyspacex/harness-cli), [release](https://github.com/hyspacex/harness-cli/releases/tag/v0.4.0)) | Medium–high | **Interesting, but too narrow/young for default** |
| [fusengine/harness](https://github.com/fusengine/harness) | Lifecycle-policy complement | Cross-agent pre/post-tool gates, protected paths, policy enforcement, receipts and lessons | Codex adapter gates shell and `apply_patch`; Codex lifecycle coverage is narrower than Claude's | MIT; created 2026-06-22; 1 star; no GitHub release at research time; pushed 2026-09-08 ([API](https://api.github.com/repos/fusengine/harness)) | Medium | **Watch; do not standardize yet** |
| [OpenAI Codex SDK / App Server](https://github.com/openai/codex) | Foundation for a custom harness | Programmatic control over Codex threads/events or a native app-server client | First-party | Apache-2.0; very active and broadly adopted, but it is a substrate rather than an outer-harness product ([repository](https://github.com/openai/codex), [TypeScript SDK](https://github.com/openai/codex/blob/main/sdk/typescript/README.md)) | High engineering cost | **Best build-your-own base** |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix) | Observability/evaluation complement | OpenTelemetry/OpenInference traces, datasets, experiments, prompt evaluation, replay, MCP access | Coding agents including Codex can use its CLI/MCP surfaces; it does not control Codex lifecycle | Elastic License 2.0 in the current repository, not OSI open source; self-hostable ([license](https://github.com/Arize-ai/phoenix/blob/main/LICENSE), [README](https://github.com/Arize-ai/phoenix)) | Medium instrumentation/ops cost | **Useful complement, license caveat** |

Repository counts and dates are volatile snapshots, not quality scores. Small/new projects can be technically strong; here they raise maintenance and bus-factor risk.

## Detailed assessment

### 1. HAR — strongest operational alternative

HAR gives each agent a separate worktree, ports, and database slot; defines a repository-local `.har/` contract; runs deterministic verification; records logs, artifacts, and a validated tree hash; and exposes both a CLI/MCP server and a local Mission Control dashboard ([README](https://github.com/os-factory/har)). This directly addresses concurrency collisions, reproducibility, verification, and fleet observability.

Its advantage over Better Harness is **enforcement at execution time**. Its disadvantage is that it is not an equivalent retrospective learning system: its published feature set does not claim independent session/project/asset review, diagnosis of repeated workflow friction, conservative evidence-state grading, or longitudinal proof that a repair improved later episodes ([HAR README](https://github.com/os-factory/har), [Better Harness model](https://github.com/QoderAI/better-harness/blob/main/models/agent-work-loop.md)).

Migration risk is manageable because HAR is repository-local and agent-agnostic, but its environment contract, worktree conventions, services, ports, and validation commands must be reconciled with Lockin's existing OpenSpec/Ebi workflow. Start with one non-critical repository and one validation profile.

### 2. madebywild/agent-harness — strongest configuration complement

This project centralizes agent assets under `.harness/src/` and generates provider-native artifacts for Codex, Claude, Copilot, and Cursor. It supports registries, presets, schema validation/migration, dry-run planning, monorepo targets, and import of legacy provider files ([README and command reference](https://github.com/madebywild/agent-harness)).

It could reduce duplicated `AGENTS.md`, skill, MCP, subagent, command, and hook maintenance. It does **not** claim to launch isolated coding environments, enforce independent test evidence, analyze sessions, score delivery quality, or establish learning across episodes. Also, its U-Haul import says it removes imported legacy files after materializing canonical entities; that route should only be tested on a disposable branch after backups and diff review ([U-Haul documentation](https://github.com/madebywild/agent-harness/blob/main/docs/toolkit.u-haul.md)).

### 3. aharness — deepest Codex-native control, highest workflow commitment

aharness makes a typed FSM—not the model—own states, exits, gates, approvals, and final artifacts. It launches one Codex App Server, preflights required skills, records canonical JSONL events, supports replay/view mode, and can create keyed Codex sidecar threads over the same connection ([architecture](https://github.com/Alfredvc/aharness/blob/main/docs/architecture.md)). This is the most direct option for making a Lockin process mechanically executable rather than prompt-guided.

The tradeoff is architectural commitment: workflows become TypeScript/XState FSMs, and the project had only one small release line with no commit after 2026-06-09 at the snapshot date ([repository API](https://api.github.com/repos/Alfredvc/aharness), [commit history](https://github.com/Alfredvc/aharness/commits/main/)). Treat it as a prototype dependency, not yet a foundational standard.

### 4. Harness CLI — validated long-running build pipeline

Harness CLI persists decisions, artifacts, and evaluation state and can resume interrupted runs. Its configurable ceremony ladder ranges from separated researcher/planner/generator/evaluator roles to a minimal mode, while verification remains mandatory. It integrates with Codex App Server for auth, thread resume, and approval handling ([README](https://github.com/hyspacex/harness-cli)).

This is useful if the target is “take a one-line product prompt through a whole app build.” It is less suitable as a general harness layer for arbitrary existing engineering workflows, and its activity/release footprint is still small ([repository API](https://api.github.com/repos/hyspacex/harness-cli)).

### 5. fusengine/harness — promising hook/policy kernel

fusengine/harness detects multiple coding-agent hosts and runs a pure policy core. For Codex it wires pre/post-tool hooks and specifically claims gating for shell and `apply_patch`; it stores enforcement state outside the project and caches/lessons inside `.harness/` ([README](https://github.com/fusengine/harness)). This is closer to a cross-host guardrail runtime than an analysis product.

Its own compatibility table makes an important limitation explicit: the richer 14-event lifecycle is currently a Claude Code path, while Codex coverage is concentrated around tool-use gates ([compatibility section](https://github.com/fusengine/harness#compatibility)). With a one-star, no-release repository at the snapshot, it merits source review and a sandbox experiment—not immediate adoption ([repository API](https://api.github.com/repos/fusengine/harness), [releases API](https://api.github.com/repos/fusengine/harness/releases/latest)).

### 6. Codex SDK / App Server — substrate, not packaged answer

OpenAI's Apache-2.0 Codex repository includes a TypeScript SDK and the App Server protocol used by several projects above ([repository](https://github.com/openai/codex), [SDK README](https://github.com/openai/codex/blob/main/sdk/typescript/README.md), [App Server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)). This is the safest compatibility base for custom orchestration, but adopting it means Lockin owns lifecycle design, persistence, retries, approvals, evidence schemas, UI/observability, and upgrades. Prefer extending a tested framework unless Lockin's workflow requirements are genuinely unique.

### 7. Evaluation and observability complements

Phoenix provides OpenTelemetry/OpenInference tracing, evaluations, versioned datasets, experiments, prompt management, replay, CLI access, and an MCP server that coding agents can query ([README](https://github.com/Arize-ai/phoenix)). It is strong for instrumented LLM applications, but Codex's own internal run stream is not automatically equivalent to an OpenInference-instrumented application. Integration work would be required, and the current Elastic License 2.0 means it should not be described as OSI-open-source even though the project calls itself open-source/self-hosted ([license](https://github.com/Arize-ai/phoenix/blob/main/LICENSE)).

## Recommended proof of concept

Run a two-week, non-destructive HAR pilot in one representative Lockin repository while retaining Better Harness:

1. Encode only existing start, test, lint, and verification behavior in `.har/`; do not redesign the workflow during onboarding.
2. Execute the same three task shapes twice: one small fix, one cross-package feature, and one failure/repair scenario.
3. Measure setup time, successful resume rate, parallel-worktree collision rate, verification reproducibility, artifact usefulness, token/runtime overhead, and manual interventions.
4. Run Better Harness before and after the pilot to see whether HAR closes concrete findings; do not interpret a green HAR verification as proof that the broader workflow improved.
5. Adopt HAR if it materially improves isolation and reproducible verification without fighting OpenSpec/Ebi ownership. Then separately prototype madebywild/agent-harness only if cross-provider asset duplication remains costly.

### Decision rule

- Choose **HAR** when the problem is unsafe parallelism, inconsistent environments, weak verification, or missing execution proof.
- Choose **madebywild/agent-harness** when the problem is duplicated or drifting agent configuration across hosts.
- Choose **aharness** when the problem is that a Codex workflow must follow explicit states, approvals, and typed exits.
- Keep **Better Harness** when the problem is diagnosing the overall coding-agent system and tracking whether workflow repairs produce better later outcomes.
- Combine tools only when each owns a distinct layer; avoid two systems both generating the same Codex config or lifecycle hooks.

## Confidence and caveats

Confidence is **moderate**. Feature and license claims come from primary project sources, but most harness-specific candidates were created in 2026 and have limited independent adoption evidence. GitHub stars, releases, and last-push dates were captured on 2026-09-07/08 UTC through the official repository API and will change. A source audit and disposable-repository trial should precede any installation into the shared Codex home or a production workflow.
