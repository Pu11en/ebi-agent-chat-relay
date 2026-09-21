Check: `uv run pytest tests/test_project_catalog.py tests/test_project_catalog_repo.py tests/test_project_launcher.py tests/test_api_projects.py -q`

Try: `make dev-on`

## 1. Catalog Core

- [x] 1.1 **Owner: catalog domain** — Add immutable root, project, availability, query, and resolution result types in `claude_discord/project_catalog.py`; verify focused unit tests distinguish local, unavailable, remote, ambiguous, and no-match results.
- [x] 1.2 **Owner: catalog discovery** — Implement bounded one-level discovery for approved roots in `claude_discord/project_catalog.py`; verify tests cover direct children, nested folders, duplicate names, unreadable roots, deterministic ordering, refresh, and stable identities.
- [x] 1.3 **Owner: owner resolver** — Add explicit computer-owner aliases and local/remote resolution without pronoun guessing; verify tests cover Drew's versus David's projects, ambiguous owners, and same-named local projects.

## 2. Persistent Personal Metadata

- [x] 2.1 **Owner: catalog persistence** — Add project Favorite, Hide, and recent metadata schema/repository in `claude_discord/database/project_catalog_repo.py` and database initialization; verify repository tests cover user/guild isolation, concurrent updates, missing projects, and identity-preserving return.
- [ ] 2.2 **Depends on 1.2 and 2.1; owner: migration adapter** — Map valid legacy launcher favorites/recents to catalog identities without deleting raw settings; verify tests keep unmappable paths available to the manual browser and preserve metadata across temporary absence.

## 3. Shared Query Surface

- [ ] 3.1 **Depends on 1.1–2.1; owner: setup interface** — Construct one catalog service in `setup_bridge`, expose it through `BridgeComponents`, and retain backward-compatible defaults; verify setup tests show built-in and custom consumers receive the same instance.
- [ ] 3.2 **Owner: REST interface** — Add authenticated bounded list/search/resolve catalog operations to `claude_discord/ext/api_server.py`; verify API tests reject malformed/untrusted requests and never return project contents.
- [ ] 3.3 **Depends on 3.2; owner: harness adapter** — Add a local structured catalog query command/helper shared by Claude, Codex, and DSH with only a concise invocation hint in session context; verify runner tests show no full catalog or folder tree in unrelated prompts.

## 4. Discord and Handoff Integration

- [ ] 4.1 **Depends on 1.2, 2.1, and 3.1; owner: launcher adapter** — Replace `ProjectLauncherCog`'s normal project suggestions with catalog list/search results while preserving explicit Browse, session binding, and pre-launch path validation; verify launcher tests cover favorite, hidden, recent, stale, duplicate-name, and no-model-call behavior.
- [ ] 4.2 **Depends on trusted-agent-handoffs interface; owner: remote adapter** — Convert remote resolution results into compact owner-qualified handoff requests and ingest timestamped remote catalog replies; verify contract tests never return a remote path as locally usable and surface offline queue state.
- [ ] 4.3 **Owner: machine profile integration** — Load shared profile aliases separately from machine roots/capabilities and filter unsupported actions; verify configuration tests show DrewAI and iMac share behavior while keeping distinct paths and exceptions.

## 5. Verification and Safe Activation

- [ ] 5.1 **Integration owner** — Run targeted tests, `uv run ruff check claude_discord/ tests/`, `uv run pyright claude_discord/`, the full test suite, and the security audit; record every command and confirm no unrelated project or session data changed.
- [ ] 5.2 **Integration owner** — Activate the isolated dev worktree only after other live turns are clear; verify New session plus Claude, Codex, and DSH resolve one local project identically and an owner-qualified remote request enters trusted handoff instead of using a local path.

## How to try it

1. Open **New session** and confirm every direct child project appears automatically, with Favorite and Hide affecting only your view.
2. In Claude, Codex, and DSH, ask each to resolve the same local project and confirm all three report the same folder without printing the whole catalog first.
3. From David's computer, ask for **Drew's projects** and confirm it targets DrewAI through a handoff rather than choosing David's same-named folder.
