# Repo study: one git worktree per AI session (for ccdb)

Date: 2026-09-19. Method: shallow-cloned each repo and read its source code; nothing was run.
Paths are relative to each repo's root. "Not implemented" means the code was searched and it
isn't there. **[unverified]** marks claims taken from reading only, which a test run could
disprove.

ccdb context: each Discord thread is one Claude Code or Codex CLI session with a `working_dir`.
Today ccdb only *asks* the model to create `.worktrees/wt-<thread_id>` on `session/<thread_id>`.
Nothing enforces it, nothing merges the work back, and cleanup is weak.

Host fact that affects porting: this machine's repo sits on **ext4 under WSL2**
(`stat -f` reports ext2/ext3). Reflink/copy-on-write cloning (FICLONE) is **not available**, so
`cp --reflink=auto` and worktrunk-style reflink fall back to full copies here.

## Repos read

| Repo | Lang | License | Commit read | Notes |
|---|---|---|---|---|
| https://github.com/smtg-ai/claude-squad | Go | **AGPL-3.0** (`LICENSE.md`) | ce1ffb4 (2026-08-20) | Do not copy code. Ideas only |
| https://github.com/devflowinc/uzi | Go | MIT (`LICENSE`); `uzi.go:1-2` has a leftover BSD-3-Clause header | 421685f (2025-06-04) | Small and stale |
| https://github.com/BloopAI/vibe-kanban | Rust+TS | Apache-2.0 | d5cbb53 (2026-09-19) | Most complete lifecycle |
| https://github.com/stravu/crystal | TS/Electron | MIT | 1e18e0b (2026-02-26) | README says it is deprecated and replaced by Nimbalyst |
| https://github.com/Nimbalyst/nimbalyst | TS/Electron | MIT (`LICENSING.md`: sync server is separate, deps licensed separately) | d3b862e (2026-09-18) | |
| https://github.com/kbwo/ccmanager | TS | MIT | 2026-09-13 | |
| https://github.com/johannesjo/parallel-code | TS/Electron | MIT | 2026-09-18 | Many careful guards |
| https://github.com/max-sixty/worktrunk | Rust | MIT OR Apache-2.0 | a6e2bed (2026-09-18) | Best merge and cleanup safety |
| https://github.com/coderabbitai/git-worktree-runner | bash | Apache-2.0 | d576398 (2026-09-14) | |
| https://github.com/d-kuro/gwq | Go | Apache-2.0 | c424773 (2026-05-02) | |
| https://github.com/raine/workmux | Rust | MIT | b070893 (2026-09-19) | Found via search |
| https://github.com/dagger/container-use | Go | Apache-2.0 | 2e43e62 (2026-08-12) | Found via search |
| https://github.com/mraza007/baton | **Python** | MIT | 7bb5fb7 (2026-03-27) | Found via search; the only Python one |
| https://github.com/jayminwest/overstory | TS/Bun | MIT | ff38f3f (2026-05-28) | Found via search; merge queue |

All except claude-squad have permissive licenses (MIT/Apache), so logic can be ported with
attribution. Apache-2.0 ports should keep a NOTICE/attribution line. For claude-squad (AGPL),
reimplement ideas from scratch and don't copy code.

---

## 1. Worktree creation

### Path schemes seen
- **Inside the repo:** `<repo>/.worktrees/<branch>` (parallel-code `electron/ipc/git.ts:915`), `<repo>/.symphony/worktrees/<n>` (baton), `.overstory/worktrees/<agent>` (overstory), `<project>/worktrees/<name>` (crystal).
- **Sibling of the repo:** `<repo>/../<repo>.<branch|sanitize>` (worktrunk default, `src/config/user/accessors.rs:21-22`), `<parent>/<project>__worktrees/<handle>` (workmux `src/workflow/create.rs:389-408`), `<repo>/../<project>_worktrees/` (nimbalyst `GitWorktreeService.ts:288-289`), `$(dirname repo)/<repo>-worktrees` (gtr `lib/core.sh:51-117`).
- **Central dir:** `~/.claude-squad/worktrees/<branch>_<hexnanotime>` (claude-squad), `~/worktrees/{{Host}}/{{Owner}}/{{Repo}}/{{Branch}}` (gwq), `/var/tmp/vibe-kanban/worktrees/<4hex>-<slug>/<repo>` (vibe-kanban `crates/utils/src/path.rs:108-125`, uses /var/tmp "to avoid RAM usage"), `~/.config/container-use/worktrees/<env-id>` (container-use).
- vibe-kanban puts a user-configured base dir under an app-owned subfolder `.vibe-kanban-workspaces` (`crates/local-deployment/src/lib.rs:122-124`), so orphan cleanup can never touch the user's own folders. Worth copying.
- For an in-repo `.worktrees/`, parallel-code writes `/.worktrees/` into **`.git/info/exclude`** (found via `git rev-parse --git-common-dir`), never into `.gitignore`. It writes this *before* `worktree add` so a racing `git status` never sees it (`electron/ipc/git-exclude.ts`). ccdb's current `.worktrees/` scheme needs exactly this.

### Branch naming
- parallel-code `tasks.ts:33-49`: `${prefix}/${slug(name)}-${id.slice(0,6)}` (prefix `task`, slug up to 72 chars). The random suffix makes collisions practically impossible.
- vibe-kanban `crates/services/src/services/container.rs:786-795`: `vk/<4hex-uuid>-<slug16>`.
- overstory `src/worktree/manager.ts:63-64`: `overstory/<agent>/<taskId>`.
- workmux, nimbalyst (`worktree/<adj-noun>`), container-use (`<petname>`, which lives only in a private bare fork).
- Name sanitizers:
  - worktrunk `sanitize_hash` (`src/path.rs:272`): replace unsafe characters, then append a 3-character base36 hash of the original when anything changed, so `a/b` and `a-b` don't collide.
  - baton `symphony/workspace.py:42-48` is a ready-made Python `slugify`.
- For ccdb, `session/<thread_id>` is already unique (Discord snowflake), so no hash is needed.

### Base commit
- Most use the main checkout's current HEAD. The better ones record the base:
  - claude-squad stores `baseCommitSHA` (`session/git/worktree_ops.go:92-108`), and all diffs are computed against it.
  - crystal stores `base_commit` and `base_branch` (`worktreeManager.ts:156-158`).
  - workmux stores the base in git config `branch.<b>.workmux-base` (`src/git/branch.rs:382-391`), so merge and remove always know the target. It is stored alongside the repo, with no DB needed.
  - ccmanager uses `git config --worktree ccmanager.parentBranch` (needs `extensions.worktreeConfig`).
- gtr resolves the base to a SHA and fetches first by default (`lib/commands/create.sh`), so git's own remote-guessing can't override `-b`.

### Reuse on resume
- **Best: vibe-kanban `ensure_container_exists`** (`crates/local-deployment/src/container.rs:1243-1287` → `crates/workspace-manager/src/workspace_manager.rs:374-425`). It runs before every operation:
  - If the branch exists, ensure the worktree exists.
  - If the branch is gone, recreate it from the target branch.
  - Validity is checked with `is_worktree_properly_set_up` (`crates/worktree-manager/src/worktree_manager.rs:156-222`): the directory exists **and** some `.git/worktrees/*/gitdir` points back at it. Otherwise it runs `comprehensive_worktree_cleanup` (`remove --force`, delete the `.git/worktrees/<name>` metadata, `rm -rf`, `prune`) and recreates.
- worktrunk `plan_switch` (`src/commands/worktree/switch.rs:921-990`): resolve first. If any worktree already holds the branch, switch to it (`SwitchPlan::Existing`). A missing directory produces `WorktreeMissing`. A path taken by another worktree produces `WorktreePathOccupied`.
- claude-squad Resume refuses to rebuild a worktree that still exists on disk, because that would throw away uncommitted work after a tmux crash (`session/instance.go:526-539`).
- baton only does `os.path.isdir(path)` (`symphony/workspace.py`). That is too weak, and its state is never reloaded after a restart.

### Branch exists or is checked out elsewhere
- overstory refuses up front by parsing `git worktree list --porcelain` (`src/worktree/manager.ts:66-73`). After creation, `validateWorktreeCreation` (93-119) confirms git has the worktree registered and `ls-files` is non-empty, and rolls back (`worktree remove --force`, `branch -D`) otherwise.
- workmux checks `git worktree list` for the branch (including the main worktree) and tells you to `open` the existing one (`create.rs:228-245`). It refuses to delete an orphan directory that contains `.git` ("to prevent data loss", `create.rs:410-449`).
- ccmanager `worktreeService.ts:1110-1140`: a local branch is attached (`worktree add path branch`). A remote-only branch gets `-b branch remote/branch`. It checks `refs/heads/` explicitly so a remote-tracking ref doesn't produce a detached HEAD.
- parallel-code rejects `task/foo` when a local branch `task` exists (`findLocalBranchPrefixConflict`, `git.ts:300-310`). This is git's ref directory/file clash; ccdb's `session/<id>` would hit it if a branch named `session` ever existed.

### Locking concurrent creation
- **Best in-process lock: nimbalyst `GitOperationLock.ts:31-170`.**
  - A FIFO promise chain per repo, keyed by `realpathSync.native(path)`, so trailing slashes and symlinks share one lock.
  - Wait timeout of 30 s. A waiter that times out still waits for its predecessor before releasing ("without creating a gap").
  - Create and delete lock the source repo; commit and rebase lock the worktree; merge locks the main repo.
- **Cross-process locks:**
  - workmux takes an flock on `<git-common-dir>/.workmux.lock` around `worktree add` and git-config writes (`src/git/config_lock.rs`). This fixes "could not lock config file" races.
  - container-use uses flock files per repo, in shared or exclusive mode (`repository/flock.go`).
- vibe-kanban keeps a per-path `tokio::sync::Mutex` map (`worktree_manager.rs:15-17, 95-105`), shared by creation and cleanup.
- Crystal's mutex is keyed per worktree name, so two creates on the same repo are *not* serialized. Don't copy that.

## 2. Setup of a fresh worktree

### Copying .env and other gitignored files
- **Best matcher: ccmanager `src/utils/worktreeInclude.ts:30-117`** (MIT, easy to port to Python). A file is copied only if it is listed in `.worktreeinclude` **and** git already ignores it, so tracked files can never be copied:
  ```ts
  git ls-files --others --ignored --exclude-from=".worktreeinclude" -z
  // then keep only paths the real ignore rules also ignore:
  git check-ignore --stdin -z
  ```
  It copies with `cpSync(..., {recursive: true, preserveTimestamps: true})` and never overwrites an existing destination.
- worktrunk `wt step copy-ignored` (`src/commands/step/shared.rs:99-235`):
  - Candidates come from `git ls-files -z --ignored --exclude-standard -o --directory` (`--directory` makes `node_modules/` one entry). They are filtered by `.worktreeinclude` compiled with gitignore semantics.
  - Built-in excludes (`.worktrees/`, `.jj/`, and others), plus entries that contain other worktrees, are dropped.
  - Copying uses `reflink_copy::reflink_or_copy` in parallel (`src/copy.rs:170`) and skips existing files.
  - `--require-include` copies nothing unless `.worktreeinclude` exists, which matches Claude Code desktop's behavior.
- gtr: `gtr.copy.include` plus `.worktreeinclude` as **bash globs**, not gitignore semantics (`lib/copy.sh:58-80`). Directories are copied with `cp --reflink=auto -RP` (`lib/copy.sh:85-113`).
- workmux `files.copy` / `files.symlink` globs (`src/config.rs:18-28`, `src/workflow/file_ops.rs:57-190`):
  - Paths are checked to stay within the repo.
  - Symlinks are **relative** (`pathdiff`).
  - `CLAUDE.local.md` is symlinked automatically if it is gitignored.
- vibe-kanban `copy_files` (`crates/local-deployment/src/copy.rs:18-115`): comma-separated globs. Anything outside the repo is rejected, existing files are skipped, there is a 30 s timeout, and it re-runs on every `ensure_container_exists`.
- gwq `copy_files` globs (`internal/worktree/copy_files.go`) copy regular files only.
- Not implemented: claude-squad, uzi, crystal, nimbalyst, baton, overstory. container-use deliberately excludes `.env` and dependency directories from environments and from commits back (`repository/git.go:642-760`).

### Dependencies (node_modules, .venv)
- **Best node_modules handling: parallel-code `electron/ipc/worktree-node-modules.ts`.**
  - The worktree gets a *real* `node_modules/` directory containing one relative symlink per top-level package.
  - Package-manager metadata dot-entries are linked (`.bin .pnpm .package-lock.json .modules.yaml .yarn-integrity .yarn-state.yml`). Tool caches (`.vite`, `.vite-temp`, `.cache`) stay local, because Vite writes through a whole-directory symlink into the main checkout, which sandboxes mount read-only (EROFS).
  - `refreshWorktreeNodeModules` resyncs on every agent spawn (`git.ts:1244-1256`) and only touches trees it manages (`isManagedNodeModules`).
- **.venv: nobody symlinks or copies it successfully.** worktrunk's docs say it plainly: "Virtual environments contain absolute paths and can't be copied. Use `uv sync` instead." gtr's `includeDirs` could copy `.venv`, but copied venvs break for the reason worktrunk gives. With uv's global cache, `uv sync` in a fresh worktree is fast because it hardlinks from the cache. **[unverified on this machine]**
- Everyone else leaves dependency install to a user hook: crystal `build_script`, ccmanager post-create hook, vibe-kanban `setup_script`, baton `after_create`, uzi `devCommand`, container-use `install_commands`.
- workmux's default `pre_remove` hook moves `node_modules` into a mktemp directory and `nohup rm -rf`s it (`src/scripts/cleanup_node_modules.sh`).

### Setup hooks and their failure semantics
- ccmanager (`src/utils/hookExecutor.ts:159-218`):
  - The pre-creation hook runs in the git root; if it fails, the worktree is not created.
  - The post-creation hook runs in the new worktree; failure is reported but doesn't undo creation.
  - Env: `CCMANAGER_WORKTREE_PATH`, `CCMANAGER_WORKTREE_BRANCH`, `CCMANAGER_GIT_ROOT`, `CCMANAGER_BASE_BRANCH`.
- worktrunk (`src/cli/mod.rs:1609-1700`):
  - `pre-*` hooks block the operation; `post-*` hooks run in the background with logs.
  - A table value runs its commands concurrently; `[[post-start]]` blocks form a pipeline.
  - Template values are POSIX-quoted before reaching the shell (`src/shell_exec.rs:739`).
- vibe-kanban (`crates/services/src/services/container.rs:1047-1131, 611-630`):
  - Setup scripts either chain before the agent (sequential) or run beside it (parallel).
  - The cleanup script runs as the agent's `next_action`, only if the agent changed something.
  - A worktree recreated after expiry does **not** re-run setup, which is a gap.
- baton `symphony/hooks.py:10-44`: `bash -lc` with `asyncio.wait_for` timeout, killed on timeout. This is directly reusable asyncio code.
- **Hook trust** (hooks committed in the repo run arbitrary code):
  - worktrunk stores approved command *templates* per project in `~/.config/worktrunk/approvals.toml`. It re-asks whenever the text changes, and fails without a TTY (`src/config/approvals.rs`, `src/commands/command_approval.rs:184`).
  - gtr trusts by `sha256(repo root + hook content)` and **skips** untrusted hooks non-interactively, reporting `hook_status=skipped-untrusted` (`lib/hooks.sh:183-215`). This non-interactive "skip and report" fits a Discord bot.
  - gwq trusts by `(abs path, sha256)` and skips when non-interactive.
  - Security note: gwq's `setup_commands` templates are **not** shell-escaped, so a branch name containing `$(…)` can inject shell commands (`internal/worktree/setup_commands.go`).

### Claude-specific setup
- ccmanager copies `~/.claude/projects/<encoded source path>` to `<encoded target path>` so `claude --resume` in the new worktree sees the parent's conversations (`worktreeService.ts:319-357`; encoding `path.replace(/[/\\.]/g,'-')` in `utils/claudeDir.ts:79-85`, respects `CLAUDE_CONFIG_DIR`).
- parallel-code `ensureClaudeSandboxFiles` (`git.ts:1023-1113`):
  - Makes `.claude/` a real directory, never a symlink, because Claude's bwrap sandbox can't bind-mount at a symlink path.
  - Copies entries with dereference, except `plans` and `steps.json`.
  - Creates placeholder `settings.json` and `settings.local.json`.
- nimbalyst resolves settings, permissions and MCP config to the **parent project's** `.claude/` (`ClaudeSettingsManager.ts:12-13`).

## 3. Port and shared-resource isolation

Nobody allocates ports robustly. What exists:
- **worktrunk `hash_port`** (`src/config/expansion.rs:506-510`):
  ```rust
  fn string_to_port(s: &str) -> u16 {
      let mut h = std::collections::hash_map::DefaultHasher::new();
      s.hash(&mut h);
      10000 + (h.finish() % 10000) as u16
  }
  ```
  - Deterministic per string; recommended as `{{ (repo ~ '-' ~ branch) | hash_port }}`.
  - No collision detection.
  - A Python port must use `hashlib` (e.g. blake2b), not `hash()`, which is salted per process.
  - worktrunk also has `sanitize_db` for per-branch database names, and per-branch vars in git config `worktrunk.state.<branch>.vars.<key>`.
- **uzi** (`cmd/prompt/prompt.go:77-109`):
  - Scans a configured `portRange` and probes each port with `net.Listen` then close.
  - Substitutes `$PORT` into the dev command and runs it in a separate tmux window.
  - Race window; tracked within a single call only.
- **container-use**: each environment is its own container, and a service port is tunneled to a random host port (`Frontend: 0`, `environment/service.go:70-100`).
- **workmux**: docs only (`docs/.../guide/monorepos.md:64-115`). A post-create script hashes `$WM_HANDLE` into an offset, probes with `lsof`, and writes `.env.local`.
- **parallel-code** exports `PARALLEL_CODE_TASK_ID`, `PARALLEL_CODE_BRANCH` and `PARALLEL_CODE_WORKTREE` to its verify command "to namespace shared resources such as a database name or a port" (`verify.ts:60-72`).
- **crystal** avoids clashes by allowing only one run script at a time (`sessionManager.ts:1190`, `stopRunningScript()` first).
- **vibe-kanban** reads the port from dev-server log output with a regex (`packages/web-core/src/shared/hooks/usePreviewUrl.ts:10-15`) and proxies `{port}.localhost`.

For ccdb:
1. Take a deterministic hash-based port for the thread (worktrunk).
2. Bind-probe it before use (uzi).
3. Store it in the thread's DB row to detect collisions, then linear-probe to the next free port.
4. Export it as `CCDB_PORT` (plus the other `CCDB_*` vars) to the session and hooks.

## 4. Guardrails

| What | Where | Detail |
|---|---|---|
| Hard instance cap | claude-squad `app/app.go:24` | `const GlobalInstanceLimit = 10` |
| Max concurrent, per-task lock, stagger | overstory `src/commands/sling.ts:775-826`, `src/config.ts:55-62` | `maxConcurrent: 25`, `staggerDelayMs: 2000`, `maxAgentsPerLead: 5`, and one agent per task ID |
| Controllable cap plus a lifetime backstop | nimbalyst `MetaAgentService.ts:594-634` | `MAX_IN_FLIGHT = 4`, `LIFETIME_BACKSTOP = 50`. Bug: the worktree is created before the cap check, so hitting the cap leaves an orphan **[read only]** |
| Cap enforced in the backend, not just the prompt | parallel-code `electron/mcp/coordinator.ts:928-942` | "the preamble instructs the model to stay under the limit, but instructions drift; this enforces it" |
| Slot counting (Python) | baton `symphony/state.py:38-40`, `orchestrator.py:269-280` | `available_slots = max(max_concurrent - running_count, 0)`, hot-reloaded |
| Creation queue concurrency | crystal `taskQueue.ts:80` | `isLinux ? 1 : 5` |
| Verify runs | parallel-code `verify.ts:9-11` | `VERIFY_MAX_CONCURRENT = 2`, FIFO queue, 10-min timeout |
| Docker limits | parallel-code `pty.ts:487-494` | `--memory 8g --pids-limit 512 --user uid:gid` |
| Git status fan-out | nimbalyst `WorktreeHandlers.ts:676`, `GitWorktreeService.ts:2329` | limiter of 2; `GIT_OPTIONAL_LOCKS=0` so read-only status doesn't fight over `.git/index` |
| Min-age before prune | worktrunk `src/cli/step.rs:622-629` | default 1 day, so a fresh worktree still at main isn't treated as "merged" |
| Watchdog | overstory `src/watchdog/daemon.ts:1-20, 909-930`, `health.ts` | warn → nudge → AI triage → terminate, one level per `nudgeIntervalMs` of stall. tmux/pid liveness wins over stored state |
| Process-group kill | vibe-kanban `crates/utils/src/process.rs:5-31` | SIGINT → SIGTERM → SIGKILL, 2 s apart |
| Kill tied to the worktree's life | worktrunk `src/commands/step/tether.rs` | run CMD in its own process group; poll every 250 ms; kill the group when the worktree directory disappears |
| **Disk space checks** | **none** | Not implemented anywhere for worktrees. Nimbalyst uses `statfs` only for DB migration. ccdb would build its own (`shutil.disk_usage`) |
| **Memory caps** | none, except Docker `--memory` (parallel-code) | |

## 5. Merge-back

### Best overall: worktrunk `wt merge` (`src/commands/merge.rs:340-470`)
Defaults: squash, rebase, remove, verify and fast-forward are all on.
1. Commit dirty changes. An LLM can write the message.
2. **Squash**:
   - First take a backup: `git stash create --include-untracked` stored in `refs/wt-backup/<branch>` (`src/git/repository/working_tree.rs:1073-1100`). It creates a commit object without touching the stash list.
   - Then `git reset --soft <merge-base>` and a single commit.
3. **Rebase** (`src/commands/step/rebase.rs:19-80`):
   - Refuses if a rebase is already in progress; skips if already linear.
   - Runs `git rebase --no-update-refs --end-of-options <target>`.
   - On conflict it returns `RebaseConflict` and leaves the rebase open, with the hint `git rebase --abort`.
4. **`pre-merge` hooks** (tests/lint, "local CI") run on the final rebased state and stop the merge if they fail.
5. **Advance the target without push or checkout** (`src/commands/worktree/push.rs:353-470`):
   ```
   git update-ref -m <msg> refs/heads/<target> <new_sha> <old_sha>   # compare-and-swap
   git update-index -q --refresh                                     # in target's worktree
   git -c submodule.recurse=false read-tree -m -u <old_sha> <new_sha>
   ```
   It refuses first if uncommitted changes in the target worktree touch the same files. If the sync fails, the ref is rolled back with a second compare-and-swap.
6. `pre-remove` hooks, then remove the worktree and branch. `post-merge` / `post-remove` run in the background.

It is a sequence of git plumbing calls, so it ports to Python via `asyncio.create_subprocess_exec` with no library needed.

### Other strong pieces
- **Merge preflight without touching files:**
  - parallel-code `checkMergeStatus` (`git.ts:1885-1935`): `git merge-tree --write-tree HEAD main` lists conflicting files. `rev-list --count --cherry-pick --right-only HEAD...main` gives an accurate "main is ahead" count.
  - overstory `merge/predict.ts` uses the same `merge-tree` dry run.
- **Guard against the agent switching branch:** parallel-code `mergeTask` (`git.ts:1935-2056`) refuses if the worktree's current branch isn't the task branch: "AI agents sometimes check out a different branch… merging the original branch would silently discard their work". It also:
  - runs under a lock per repo;
  - requires a clean target;
  - uses `merge --squash` + commit, or a plain merge;
  - on failure runs `merge --abort` or `reset --hard` and restores the original branch;
  - merges inside the worktree that has the target checked out, if one exists;
  - retries once on `index.lock`.
- **Tests before merge:**
  - worktrunk `pre-merge` hooks.
  - workmux `pre_merge` hooks with `WM_BRANCH_NAME`/`WM_TARGET_BRANCH` (`src/workflow/merge.rs:212-247`).
  - parallel-code `verifyBeforeLanding` (`electron/mcp/coordinator.ts:1563-1597`) blocks landing on a failing verify command.
  - Not implemented in vibe-kanban, crystal, nimbalyst, ccmanager, container-use, or overstory's resolver (overstory only tells its merger agent in the prompt).
- **vibe-kanban squash** (`crates/git/src/lib.rs:575-673, 1082-1126`):
  - Refuses if the base branch has moved (`BranchesDiverged`), so you must rebase first.
  - If the base isn't checked out, it does an **in-memory libgit2 merge** with `fail_on_conflict(true)`, so no checkout and no half-conflicted index.
  - Afterwards it resets the task branch to the squash commit so the session can keep going.
  - Python equivalent: `git merge-tree --write-tree` + `git commit-tree` + `update-ref`, which is also checkout-free.
- **vibe-kanban auto-commit:**
  - `try_commit_changes` runs `git add -A && git commit` after every *successful* run, using the agent's final message (`container.rs:1554-1578`).
  - A **Claude Stop hook** blocks the agent from stopping while there are uncommitted changes (`crates/executors/src/executors/claude.rs:208-217`, `claude/client.rs:330-347`): `{"decision":"block","reason": commit_reminder_prompt + status}`, with a `stop_hook_active` guard against loops.
- **crystal** auto-commit modes (`commitManager.ts:36-54`):
  - `checkpoint` commits after every prompt with `--no-verify`;
  - `structured` appends a Conventional-Commits instruction to the prompt.
  - Its squash is `reset --soft <merge-base>` + commit, then `merge --ff-only` into main (`worktreeManager.ts:557-715`), so main is never rewritten.
- **nimbalyst stash safety** (`GitWorktreeService.ts:1013-1029, 1990-2302`):
  - The auto-stash message is unique (`Auto-stash before rebase ${Date.now()}`), and it checks the top stash has that message before popping.
  - It never pops a stash that isn't its own.
  - It makes a `backup-before-squash-<ts>` branch before squashing (2456-2562).
  - Bug: its rebase-in-progress check does `path.join(repo, '.git')`, which is a *file* in a linked worktree, so the check never fires **[read only]**.
- **Conflicts handed back to an agent:**
  - crystal `sessions:abort-rebase-and-use-claude` (`ipc/git.ts:794-900`): "Please rebase the local main branch (not origin/main) into this branch and resolve all conflicts".
  - vibe-kanban's conflict prompt includes "set `GIT_EDITOR=true`" so the rebase doesn't hang (`packages/web-core/src/shared/lib/conflicts.ts:37-58`).
  - nimbalyst gives paths, files and commits, and warns "rebase onto the local base … NOT origin".
- **Merge queue: overstory** (`src/merge/queue.ts:45-136`, `src/merge/resolver.ts`, `src/merge/lock.ts`):
  - SQLite `merge_queue` (WAL, `busy_timeout=5000`) with statuses `pending/merging/merged/conflict/failed`, FIFO by `ORDER BY id ASC`.
  - A lock file per target, `merge-{target}.lock`, created with `wx` and storing the PID; a dead holder's lock is taken over.
  - Tiers:
    1. `git merge --no-edit`.
    2. Keep the agent's side, but only when the main side of the conflict is empty (`hasContentfulCanonical`), and union for `merge=union` files.
    3. `claude --print` per conflicted file, rejecting prose-looking output (`looksLikeProse`).
    4. "Reimagine": abort, then give the model both full versions to reimplement (off by default).
  - Caveat: it merges in the **project root checkout**: checkout main there, stash, and delete untracked files that overlap the agent's changes. That is unsafe if a person or another thread uses that checkout. The `merging` status is never actually set **[read only]**.
- **container-use** merge is `git -c core.hooksPath=/dev/null merge --no-ff --autostash -- container-use/<id>`; `apply` is `merge --autostash --squash` (`repository/repository.go:511-636`). It keeps agent branches in a private bare fork, so the user's repo only sees `container-use/<id>` remote-tracking refs.
- **baton** doesn't merge. The prompt tells the agent to push and open a PR, and `tracker.py:157-178` detects the PR by branch prefix or `#N` in the title/body.
- **claude-squad** "push" commits with `--no-verify` and runs `gh repo sync` / `git push -u` (`session/git/worktree_git.go:71-126`); no merge.
- **uzi checkpoint** runs `git rebase <agentBranch>` from the user's branch (`cmd/checkpoint/checkpoint.go:110-152`), with no conflict handling. Don't copy it.

## 6. Cleanup and safety

### Best safety chain: worktrunk `src/git/remove.rs`
1. Refuse if the worktree is locked; `--force` does not override this.
2. `ensure_clean`: `git status --porcelain --untracked-files=normal`, so untracked files count as dirty.
3. Stop the fsmonitor daemon.
4. **Rename the directory into `<git-common-dir>/wt/trash/<name>-<timestamp>/`**, then `rm -rf` it in a detached background process.
5. Delete the branch only if it is **integrated** (`delete_branch_if_safe`, 557-630). It re-checks that the branch hasn't been checked out again, then deletes with a compare-and-swap, `git update-ref -d <ref> <expected_sha>`. If the branch moved, it is kept (`RetainedRaced`).
6. **Integration detection** (`src/git/mod.rs:131-324`, `src/git/repository/integration.rs:361-440`), cheapest check first:
   ```
   SameCommit → Ancestor → NoAddedChanges (diff-tree --quiet merge_base branch)
   → TreesMatch → MergeAddsNothing (merge-tree --write-tree target branch == target tree)
   → PatchIdMatch (patch-id of the branch diff vs each commit in merge_base..target, capped at 500)
   ```
   This catches **squash-merged** branches, which plain `git branch -d` / `--merged` miss.
- `wt step prune` does bulk removal of integrated worktrees, with the min-age guard (default 1 day).

### Other good pieces
- **workmux cleanup** (`src/command/remove.rs:101-200`, `src/workflow/cleanup.rs:323-431`):
  - Uncommitted changes block removal.
  - Unmerged commits (`git branch --no-merged <stored base>`) prompt `[y/N]`.
  - Then, in order: inode and repo identity check, rename to `.workmux_trash_<name>_<pid>_<nanos>` (a process still using it as its cwd keeps working), `worktree prune`, `branch -d`, delete the per-worktree config keys, `rm` the trash.
- **nimbalyst archive** (`ipc/WorktreeHandlers.ts:120-327`, `renderer/hooks/useArchiveWorktreeDialog.ts`):
  - Kills terminals and sessions first.
  - The DB row is marked archived **only after** the directory is confirmed gone. If deletion fails, the sessions are un-archived.
  - Auto-archive happens only when the worktree is clean **and** merged (`git branch -a --merged` after a fetch). Otherwise it shows the uncommitted-file count and unique commits via `git cherry`.
- **vibe-kanban expiry** (`crates/db/src/models/workspace.rs:255-309`, `container.rs:299-320`):
  - Every 30 min, workspaces with no running process whose last activity is older than **72 h (1 h if archived)** lose their worktree **but keep their branch**. `ensure_container_exists` recreates the worktree on the next use.
  - Activity `touch()` is debounced to once per 2 min.
  - Orphan directories not in the DB are removed, only under the app-owned base dir.
  - Gap: a run that failed or was killed isn't auto-committed, so its dirty work is destroyed on expiry (`container.rs:570`, the commit happens only when `success || cleanup_done`).
- **claude-squad pause/resume** (`session/instance.go:422-572`): pause auto-commits, detaches, runs `git worktree remove` (keeping the branch) and prunes; resume re-adds the worktree from the branch. Good for idle Discord threads. (AGPL: reimplement, don't copy.)
- **gtr clean** (`lib/commands/clean.sh`, `lib/provider.sh:174-224`):
  - `--merged` asks GitHub whether the PR is merged **and** `headRefOid == local tip`, so commits added after the merge are kept.
  - It skips dirty and untracked worktrees.
  - `_clean_locked_phantoms` handles registry entries left behind when a session crashed after its directory was deleted.
- **overstory `ov worktree clean`** (`src/commands/worktree.ts:156-260`): only cleans agents in the `completed`/`zombie` state; skips unmerged branches unless `--force`; refuses when live child agents exist.
- **parallel-code** removal (`git.ts:841-904, 1258-1290`, `worktree-cleanup.ts`):
  - Kills agents first; rmSync retries at 0/500/1500/3000 ms.
  - Reclaims root-owned files with a throwaway container running `chown` (deliberately not `rm`).
  - Imported worktrees are never deleted.
- **Unpushed-commit check:** not implemented in any repo. gtr's `headRefOid` match is the closest.
- **No safety at all:** claude-squad (`worktree remove -f` + `branch -D` behind a confirm dialog), crystal archive (`--force`, branches never deleted), container-use delete, baton, vibe-kanban user delete (`branch -D`).
- uzi `kill`'s worktree removal checks the wrong path (`cmd/kill/kill.go:30-106`), so it probably leaves worktrees and branches behind **[unverified, read only]**.

## 7. Keeping the agent in its worktree

In ascending strength:
1. **Prompt only.** nimbalyst `packages/runtime/src/ai/prompt.ts:256`: "IMPORTANT: You are working in a git worktree at ${worktreePath} … Stay in this directory; do not modify files in the main checkout unless explicitly asked…". overstory `templates/overlay.md.tmpl:32-37` names the violation (`PATH_BOUNDARY_VIOLATION`). ccdb today is at this level, minus even the cwd.
2. **cwd set by the tool.** This is what everyone does: tmux `-c` (claude-squad `session/tmux/tmux.go:102`, workmux, overstory, gwq), a PTY with `cwd` (crystal, ccmanager, parallel-code), or `current_dir` (vibe-kanban `claude.rs:636`), or `subprocess cwd=` (baton `worker.py:100-105`). ccdb can do this directly: spawn the CLI with `cwd=<worktree>`.
3. **Env scrubbing and identifying vars.**
   - parallel-code `pty.ts:231-285, 409-411` blocks `PATH/HOME/LD_PRELOAD/GIT_CONFIG_*/…` from task env, and strips `CLAUDECODE`, `CLAUDE_CODE_SESSION` and `CLAUDE_CODE_ENTRYPOINT` so nested sessions start clean.
   - overstory does the same `unset` and exports `OVERSTORY_WORKTREE_PATH`.
   - vibe-kanban exports `VK_WORKSPACE_ID` and `VK_WORKSPACE_BRANCH`.
4. **Claude Code PreToolUse hooks that block writes outside the worktree. Best fit for ccdb: overstory `src/agents/hooks-deployer.ts:78-125, 496-528`**, deployed into the worktree's `.claude/settings.local.json` and merged with existing hooks.
   - `ENV_GUARD='[ -z "$OVERSTORY_AGENT_NAME" ] && exit 0;'` means the hook is inert in the user's own sessions.
   - The Write/Edit/NotebookEdit guard makes the path absolute and blocks it unless it is under `$OVERSTORY_WORKTREE_PATH`:
     `{"decision":"block","reason":"Path boundary violation: file is outside your assigned worktree…"}`.
   - A Bash guard checks absolute paths in file-modifying commands (allows `/tmp`, `/dev`), and blocks `git push`, `git reset --hard` and `git checkout -b` outside the agent's prefix. It documents its own blind spots: `$VAR` paths, `cd` + relative path, subshells.
   - ccdb could inject the same hook with `--settings` rather than writing into the repo. Nimbalyst passes a PreToolUse hook via `--settings`, but check how that combines with the CLI's other settings files **[unverified]**.
   - The overstory hook extracts `file_path` with `sed`. A Python hook script using `json.load(sys.stdin)` would be sturdier.
5. **Codex sandbox.** vibe-kanban uses `SandboxMode::WorkspaceWrite` (`crates/executors/src/executors/codex.rs:462-470`), and overstory uses `--full-auto` with `--add-dir` for shared directories. Codex confines writes to cwd plus the added directories itself. For ccdb's Codex/`local` backends, cwd=worktree plus workspace-write is already a real boundary.
6. **Containers.**
   - workmux `src/sandbox/container.rs:618-997`: mounts the worktree read-write at the same path, and the main worktree and git common dir **read-only**. Only `objects/`, `refs/`, `logs/` and the worktree's admin dir are writable. Secrets are masked by mounting `/dev/null` over them; `SYS_ADMIN` and a writable docker socket are refused.
   - container-use: all file and shell operations go through MCP tools into a Dagger container, auto-committed per call. This is enforced only by rules text plus `permissions.allow` (`rules/agent.md`, `cmd/container-use/agent/configure_claude.go`); nothing in code denies the built-in tools.
   - parallel-code Docker mode: `-v cwd:cwd -w cwd`, per-agent `HOME`.

Also relevant:
- Agents *do* switch branches or `cd` out. parallel-code's pre-merge branch check (section 5) and nimbalyst's `worktreeInference.ts`, which only accepts adoption of paths under `<workspace>_worktrees/<name>`, handle that after the fact.
- For resume: per-session `--session-id <uuid>` instead of `--continue` when a worktree can hold more than one agent (parallel-code `electron/shared/session-resume.ts`).

---

## What ccdb should take (design sketch, not decided)

1. **Enforce the worktree in code.** Create `.worktrees/wt-<thread_id>` on `session/<thread_id>` in Python before spawning, and pass it as the subprocess `cwd`. Put `/.worktrees/` in `.git/info/exclude` (parallel-code). Record the base SHA and base branch in the thread's DB row (claude-squad/crystal) or git config (workmux).
2. **Idempotent `ensure_worktree()`** before every run, with a per-repo `asyncio.Lock` keyed by `os.path.realpath` (nimbalyst) plus an flock on `<git-common-dir>/ccdb.lock` for cross-process safety (workmux). Validate via the `gitdir` back-pointer; clean up fully and recreate if broken, keeping the branch (vibe-kanban). Refuse if the branch is checked out elsewhere (overstory); never rebuild an existing directory (claude-squad).
3. **Setup:** `.worktreeinclude` using ccmanager's two git commands; `.claude/` as a real copied directory (parallel-code); a post-create hook for `uv sync` / `npm ci`; `CCDB_*` env vars. Run repo-committed hooks only after trust by content hash, and skip-and-report when untrusted (gtr). Re-run setup when a worktree is recreated (vibe-kanban's gap).
4. **Ports:** blake2b hash to 10000–19999, bind-probe, DB registry, `CCDB_PORT`.
5. **Guardrails:** a global and per-repo cap on live worktrees (claude-squad/overstory); a `shutil.disk_usage` floor before creation (nobody has one); a process-group kill with escalation (vibe-kanban); kill dev servers when the worktree goes (worktrunk tether).
6. **Merge back** (explicit command, e.g. `/land`):
   - Branch check (parallel-code).
   - `merge-tree` preflight (parallel-code/overstory).
   - `stash create` backup ref, `reset --soft` squash, rebase, then a test/verify hook.
   - Compare-and-swap `update-ref` + `read-tree -m -u` into the main checkout, with rollback (worktrunk).
   - Serialized per target branch (overstory lock).
   - On conflict, leave it or abort and hand a canned "rebase onto local main, `GIT_EDITOR=true`" prompt back to the same thread (crystal/vibe-kanban).
7. **Keep work safe:** a Stop hook that blocks stopping with uncommitted changes (vibe-kanban), or an auto-commit checkpoint after each turn (crystal). Then an idle worktree can be removed while its branch is kept, and recreated when the thread wakes (vibe-kanban expiry, claude-squad pause).
8. **Cleanup:**
   - Refuse on dirty, including untracked (worktrunk).
   - Delete the branch only if integrated, using worktrunk's six checks including `merge-tree` and patch-id for squash merges, with a compare-and-swap `update-ref -d`.
   - Rename to trash, then delete in the background (worktrunk/workmux).
   - Add an unpushed-commit check, which no repo has.
   - Mark the DB row closed only after the directory is verified gone (nimbalyst).
9. **Boundary:** cwd plus an env-gated PreToolUse path guard for Claude (overstory pattern, rewritten in Python with a JSON parser); workspace-write sandbox for Codex; strip `CLAUDECODE*` from the child env; a system-prompt reminder (nimbalyst wording).

Search sources for the extra repos:
- https://github.com/topics/parallel-agents?l=python
- https://www.augmentcode.com/tools/open-source-agent-orchestrators
- https://github.com/raine/workmux
- https://github.com/dagger/container-use
