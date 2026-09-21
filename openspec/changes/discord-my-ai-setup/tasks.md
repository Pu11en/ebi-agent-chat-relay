Check: `uv run pytest tests/test_ai_setup_inventory.py tests/test_ai_setup_repo.py tests/test_ai_setup_cog.py -q`

Try: `make dev-on`

## 1. Safe Inventory Domain

- [x] 1.1 **Owner: inventory domain** — Add immutable item, source, scope, availability, prerequisite, measurement, diagnostic, and snapshot types in `claude_discord/ai_setup_inventory.py`; verify unit tests cover stable identities and every approved kind and scope.
- [x] 1.2 **Owner: redaction boundary** — Add safe metadata construction and fail-closed secret redaction in `claude_discord/ai_setup_redaction.py`; verify adversarial tests exclude tokens, passwords, private keys, environment values, connector secrets, and raw unsafe config from items and diagnostics.
- [x] 1.3 **Depends on 1.1–1.2; owner: collector** — Add an adapter registry and collector that merges items, classifications, availability evidence, and per-source errors without aborting; verify tests distinguish custom, overridden built-in, unchanged built-in, discovered, configured, and verified-loaded states.

## 2. Harness and Setup Adapters

- [x] 2.1 **Owner: shared setup adapter** — Inventory global/project instructions, shared memory, and skills only from declared source boundaries; verify fixture tests assign correct source and everywhere/profile/project scope without reading unrelated files.
- [x] 2.2 **Owner: harness adapters** — Add isolated Claude, Codex, and DSH adapters for custom tools, commands, hooks, plugins/connectors, and harness settings; verify fixtures report actual loader availability, prerequisites, unsupported states, and deliberate computer/subscription exceptions.
- [x] 2.3 **Owner: Discord extension adapter** — Inventory loaded custom Cogs and user-added Discord commands through loader/runtime evidence; verify a failed or unloaded Cog is not labeled verified and built-in framework commands stay hidden by default.

## 3. Snapshots and Remote Comparison

- [x] 3.1 **Depends on 1.1–1.3; owner: snapshot persistence** — Add a repository/schema for safe fingerprints, measurements, diagnostics, exceptions, and verification timestamps in `claude_discord/database/ai_setup_repo.py`; verify persistence tests prove no raw values or secrets are stored.
- [x] 3.2 **Depends on trusted-agent-handoffs interface and 3.1; owner: remote snapshots** — Define a bounded inventory request/reply packet and ingest trusted remote snapshots with freshness state; verify tests distinguish live, stale, unreachable, matching, missing, and deliberate-difference results.
- [x] 3.3 **Owner: comparison service** — Implement Browse by kind, Where it lives, Compare computers, search/filter, recent-change, and explicit built-in queries over snapshots; verify deterministic unit tests cover unknown measurements and modification times.

## 4. Discord My AI Setup Experience

- [x] 4.1 **Depends on 3.3; owner: Discord view** — Build owner-bound paginated views in `claude_discord/discord_ui/my_ai_setup.py` with Browse by kind as default and sibling Where it lives/Compare computers tabs; verify component-limit and authorization tests using the approved prototype as the visual reference.
- [x] 4.2 **Owner: detail view** — Render safe source, user-facing scope, availability evidence, prerequisites, measurements, and last-change facts while hiding Mega Global and unknown guesses; verify snapshots of item details contain no internal ownership label or secret value.
- [ ] 4.3 **Depends on 4.1 and command-surface Settings interface; owner: Settings integration** — Register My AI Setup as a Settings entry without owning or duplicating the parent Settings command; verify interaction tests open Browse by kind and spend no model tokens.
- [ ] 4.4 **Depends on 4.2; owner: Setup Agent handoff** — Add Ask Setup Agent using the existing session creation contract and a bounded safe item packet; verify tests create one management thread, attach no raw secret content, and perform no edit before the user's request is processed normally.

## 5. Verification and Safe Activation

- [ ] 5.1 **Integration owner** — Run targeted tests, `uv run ruff check claude_discord/ tests/`, `uv run pyright claude_discord/`, the full test suite, and the security audit; inspect logs/database fixtures to confirm no secret or source content leaks.
- [ ] 5.2 **Integration owner** — Activate the isolated dev worktree only after other live turns are clear; verify DrewAI local facts, a trusted iMac snapshot, a deliberate David difference, offline/stale labeling, and Setup Agent session creation without changing configuration.

## How to try it

1. Open **Settings → My AI Setup** and confirm it begins on Browse by kind with only custom additions and overrides visible.
2. Search for a skill, switch to **Where it lives**, then **Compare computers**; confirm sources, scopes, deliberate differences, and stale remote facts are clearly labeled without showing secrets or Mega Global.
3. Choose one item and press **Ask Setup Agent**; confirm a new session opens with safe details and that simply browsing or pressing the button changes no configuration.
