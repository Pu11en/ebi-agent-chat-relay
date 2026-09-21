# Repo study: coordinating several AI coding sessions on one repo

Date: 2026-09-19. All repos shallow-cloned to /tmp/repo-study2 and read directly
(deleted afterwards). Commit hashes are listed so every quote can be re-checked.
"VERIFIED" means I read the code or ran the command myself. "UNVERIFIED" means
the claim comes from a search result or article only.

Question: how can ccdb (one Discord thread = one Claude Code or Codex CLI
session) know what the other sessions are doing, avoid two sessions editing the
same file, and hand finished work between them? And how can it see *every* file
a session changed, including Codex patches and shell edits (`sed`, `python -c`,
code generators), not only Claude's Edit/Write tool calls?

---

## 1. Summary table

| Repo | License | Mechanism | Sees shell/Codex edits? | Portable to Python? |
|---|---|---|---|---|
| gitbutlerapp/gitbutler (`but-claude` crate, removed 2026-05-19) | FSL-1.1-MIT (non-compete; each version becomes MIT after 2 years) | Pre-tool file lock (30 s wait), post-tool hunk assignment to a per-session virtual branch, stop hook commits the session's hunks | No for the lock (only `tool_input.file_path`). Partly for the stop hook: it reads the *whole* worktree diff | The ideas port easily. The code relies on GitButler's virtual branches and cannot be lifted |
| Dicklesworthstone/mcp_agent_mail | MIT **plus a rider that forbids any use by or for OpenAI or Anthropic** | Advisory file reservations (glob leases with TTL), staleness sweeper, pre-commit and pre-push guard | The guard checks `git diff --cached` at commit time, so yes, but only when the agent commits | Already Python. **Do not copy the code** (see licence) |
| dagger/container-use | Apache-2.0 | Full isolation: one container plus one git branch per environment, auto-commit after every tool call, `merge` or `apply` (squash) | Yes, via `git status --porcelain` after each tool call | Easy (all git CLI) |
| openai/codex ghost commits (removed 2026-04-28) | Apache-2.0 | Snapshot the worktree into a detached commit using a **temporary `GIT_INDEX_FILE`** | Yes, any edit by any means | Easy (git plumbing) |
| sst/opencode `Snapshot` | MIT | **Separate shadow git-dir** plus `--work-tree`; `write-tree` at step start; `diff --name-only <hash>` at step end gives the files changed in that step | Yes, any edit by any means | Easy (git plumbing) |
| clash-sh/clash | MIT | PreToolUse hook that runs a `git merge-tree` simulation between worktree pairs and asks the user before writing | HEAD-to-HEAD only, plus a disk-vs-HEAD check on the one target file | Easy (`git merge-tree --write-tree`) |
| garniergeorges/claude-presence | MIT | Presence and resource claims with waiting queue; inbox injected into the next prompt through the UserPromptSubmit hook | No (declared) | Easy |
| swoofer/mcp-coordinator | MIT | `working_files` rows held between PreToolUse and PostToolUse (TTL), "hot file" overlap from recent activity, co-change map built from git log | No (hook paths only) | Easy |

---

## 2. GitButler: the `but claude` hooks integration

- URL: https://github.com/gitbutlerapp/gitbutler
- Licence: `LICENSE.md` = **FSL-1.1-MIT**. You may use and modify it for any
  "Permitted Purpose". That means anything except a "Competing Use", which is
  offering it commercially as a substitute for GitButler. Each version turns into
  MIT two years after release. Porting the *ideas* into ccdb is fine. Copying the
  code into an OSS framework is a grey area, and the code would not be usable
  anyway.
- **Current main no longer has it.** Commit `1dbe2bdb` (2026-05-19), "Remove
  agent/codegen integration", says: "Deleted the but-claude and but-cursor Rust
  crates". I read the parent state, commit
  `96151cb1bf237dfc0a31ac27b957950158309c94`. Main (`7926e242`) now has only a
  `but agent setup` wizard that writes policy text into agent instruction files,
  and its help points to docs for "Claude Code hooks, Cursor hooks, MCP".

### Files (at 96151cb)
- `crates/but-claude/src/hooks/mod.rs`: `handle_pre_tool_call`,
  `handle_post_tool_call`, `handle_stop`
- `crates/but-claude/src/hooks/file_lock.rs`: SQLite `file_write_locks` table
- `crates/but-action/src/simple.rs`: `handle_changes`, which commits assigned hunks per stack
- `crates/but-hunk-assignment/src/reconcile.rs`: carries assignments over as hunks move and merge
- `crates/but-cursor/src/lib.rs`: the same idea for Cursor (`handle_after_edit`, `handle_stop`)

### How it works (VERIFIED by reading)
1. **PreToolUse** acquires a per-file lock owned by the session ID. If another
   session holds it, the hook polls every second for up to 30 s, then fails:
   ```rust
   // file_lock.rs
   let max_wait_time = std::time::Duration::from_secs(30);
   ...
   if let Some(lock) = locks.into_iter().find(|l| l.path == file_path) {
       if lock.owner == owner { return Ok(()); }
       else { if start.elapsed() > max_wait_time {
               return Err(anyhow!("Failed to obtain lock for {file_path} after waiting for {max_wait_time:?}")); }
             std::thread::sleep(std::time::Duration::from_secs(1)); }
   ```
   The lock lasts only for the length of one tool call. **PostToolUse** releases
   it through a drop guard (`ClearLocksGuard`). So this lock serialises writes;
   it does not reserve ownership.
2. **PostToolUse** takes the `structured_patch` hunk headers that Claude's
   Edit tool returns and assigns every *unassigned* worktree hunk in that file
   whose new-line range intersects them to the session's own stack (virtual
   branch). A session with no stack gets one, and a rule `session_id -> stack_id`
   is stored:
   ```rust
   .filter(|a| a.stack_id.is_none())
   ...
   hook_headers.iter().any(|h| h.new_range().intersects(a.new_range()))
   ...
   target: Some(HunkAssignmentTarget::Stack { stack_id }),
   ```
   Note: this is **hunk-level attribution in one shared folder**. Two sessions
   can edit different parts of the same file, and each hunk goes to its own
   session's branch.
3. **Stop** reads `worktree_changes`. If there are none, it returns "No changes
   detected". Otherwise it runs `handle_changes(..., exclusive_stack=Some(stack_id))`,
   which commits only the hunks assigned to that session's stack. When an
   exclusive stack is given, unassigned hunks are deliberately left alone:
   ```rust
   } else if exclusive_stack.is_none() {
       // If there is an exclusive stack. We don't want to do anything with
       // the unassigned changes.
   ```
   It takes oplog snapshots before and after (undo points), then uses an LLM to
   reword the commit and rename the branch.
4. **Two sessions touching the same hunk**: in `reconcile.rs`, when a new hunk
   intersects several old assignments, it takes the biggest one. With
   `MultipleOverlapping::SetNone` it resets the hunk to *unassigned* if the
   overlapping hunks belonged to different branches:
   ```rust
   if multiple_overlapping_resolution == MultipleOverlapping::SetNone
       && unique_branch_refs.count() > 1 { new_assignment.branch_ref_bytes = None; }
   ```
   Separately, `but-hunk-dependency` "locks" a hunk to a stack when it depends
   on a commit already in that stack.
- **Blind spot**: shell edits. `ToolInput` requires `file_path`, so Bash never
  gets a lock or a hunk assignment. Its changes stay "unassigned" and, with the
  exclusive stack, are never committed by the stop hook.

---

## 3. mcp_agent_mail: file reservations (leases) and the commit guard

- URL: https://github.com/Dicklesworthstone/mcp_agent_mail (commit `ac4966c6`)
- **Licence warning:** "MIT License (with OpenAI/Anthropic Rider)". The rider
  says: "no rights are granted to any Restricted Party" (OpenAI, Anthropic, and
  anyone acting "on behalf of, for the benefit of, or under the direction of"
  them). It also defines "use" to include "incorporating the Software ... into
  any ... pipeline for machine learning or other automated systems".
  - **Do not copy any of this code into ccdb.** ccdb exists to drive
    Anthropic's and OpenAI's CLIs, so a derivative could plausibly fall under the
    rider.
  - The quotes below are for design reference only. ccdb's implementation must
    be written from scratch. The concepts (leases, TTL, glob overlap, pre-commit
    check) are generic and not protected.

### Mechanism (VERIFIED by reading `src/mcp_agent_mail/`)
- `models.py` `FileReservation`: `path_pattern`, `exclusive`, `reason`,
  `created_ts`, `expires_ts`, `released_ts`. The agent is nullable, so an orphaned
  lease can still be swept.
- `app.py` tool `file_reservation_paths(paths, ttl_seconds=3600, exclusive=True)`
  is **advisory**: "Advisory model: still grant the file_reservation but
  surface conflicts". It returns `{granted, conflicts:[{path, holders}]}`, and
  the TTL must be at least 60 s.
- Overlap test `_file_reservations_conflict`:
  - Two shared (non-exclusive) leases never conflict, and neither do two leases
    held by the same agent.
  - Globs use gitignore-style `PathSpec`, cross-matched in both directions
    (`a_spec.match_file(b) or b_spec.match_file(a)`).
- Staleness (`_collect_file_reservation_statuses`): a lease is stale when its
  agent has been inactive longer than the threshold **and** there is no recent
  mail, no recent filesystem mtime on the matched files, and no recent git
  commit touching them:
  ```python
  stale = bool(reservation.released_ts is None
      and (agent_orphaned or (agent_inactive and not (recent_mail or recent_fs or recent_git))))
  ```
  Git activity comes from one rev walk (`repo.iter_commits(paths=pathspec, max_count=1)`).
  Stale leases are auto-released, and `force_release_file_reservation` exists
  for manual release.
- **Pre-commit guard** (`guard.py` `render_precommit_script`):
  - The generated Python hook collects staged paths, including both sides of a
    rename, with `git diff --cached --name-only -z --diff-filter=ACMRDTU` and
    `--name-status -M`.
  - It reads the lease JSON files, skips its own leases (`AGENT_NAME` env) and
    released or expired ones, and matches the rest with a union PathSpec.
  - It exits 1, or 0 when `AGENT_MAIL_GUARD_MODE=warn`. `AGENT_MAIL_BYPASS=1`
    skips the check.
  - A pre-push variant checks `git rev-list local --not --remotes=` and
    `diff-tree` for each commit.
  - This is the one place it sees shell edits, but only once they are committed.

---

## 4. dagger/container-use: isolation, then merge or apply

- URL: https://github.com/dagger/container-use (commit `2e43e625`). Licence: **Apache-2.0**.
- Each environment is a container plus a git worktree and branch in a fork
  repo. The user's repo gets a `container-use` remote.
- After each file write or command, `propagateToWorktree` exports the container
  filesystem to the worktree. `commitWorktreeChanges` then runs
  `git status --porcelain`, stages non-binary files, and commits with the tool's
  explanation as the message (`repository/git.go`). So every change, including
  shell edits, is attributed, because nothing else writes to that worktree.
- Hand-off is through git (`repository/repository.go`):
  ```go
  // Merge
  "merge", "--no-ff", "--autostash", "-m", "Merge environment "+envInfo.ID, "--", "container-use/"+envInfo.ID
  // Apply (squash, leaves changes staged for the user)
  "merge", "--autostash", "--squash", "--", "container-use/"+envInfo.ID
  ```
  `Diff` uses `merge-base(currentBranch, container-use/<id>)..container-use/<id>`.
  Per-env logs and state are kept in git notes (`notes --ref ... append`), and a
  flock-based `RepositoryLockManager` serialises git operations.
- It does no cross-environment overlap detection. Conflicts surface only at
  merge time.

---

## 5. Detecting changed files regardless of how they were changed

### 5a. Codex "ghost commits": temporary index (VERIFIED)
- File: `codex-rs/git-utils/src/ghost_commits.rs` at openai/codex commit
  `61dfe0b86c75bb4e6c173a70ca9fb2f2daac2f67`. It used to live under
  `codex-rs/utils/git/`, and was **removed** in `4e05f305` (2026-04-28, "Remove
  ghost snapshots"). Licence: Apache-2.0.
  ```rust
  let index_tempdir = Builder::new().prefix("codex-git-index-").tempdir()?;
  let index_path = index_tempdir.path().join("index");
  let base_env = vec![(OsString::from("GIT_INDEX_FILE"), OsString::from(index_path.as_os_str()))];
  // Use a temporary index so snapshotting does not disturb the user's index state.
  // Example plumbing sequence:
  //   GIT_INDEX_FILE=/tmp/index git read-tree HEAD
  //   GIT_INDEX_FILE=/tmp/index git add --all -- <paths>
  //   GIT_INDEX_FILE=/tmp/index git write-tree
  //   GIT_INDEX_FILE=/tmp/index git commit-tree <tree> -p <parent> -m "codex snapshot"
  ...
  // `git commit-tree` writes a detached commit object without updating refs,
  // which keeps snapshots out of the user's branch history.
  ```
  It also skipped large untracked files and directories
  (`ignore_large_untracked_files/dirs`) and reported what it skipped.
- Why it was pulled (UNVERIFIED, from GitHub issues found by search):
  - openai/codex#19588: the snapshot ran `git add -A` on a trusted *home
    directory* and filled the disk with `tmp_pack` files.
  - #7395: ghost_commit made sessions huge.
  - **Lesson for ccdb:** never snapshot `$HOME` or a non-repo directory, and cap
    file size and file count.
- Codex's current `core/src/turn_diff_tracker.rs` says it "Tracks the net text
  diff for the current turn from committed apply_patch mutations, without
  rereading the workspace filesystem". It tracks **apply_patch only**, so Codex
  itself no longer sees shell edits.

### 5b. opencode: shadow git-dir snapshot per step (VERIFIED; best fit)
- File: `packages/opencode/src/snapshot/index.ts` in sst/opencode (commit
  `4e1c4963`). Licence: **MIT**.
- It keeps a separate git dir in opencode's data folder, keyed by project and
  worktree, and never touches the user's `.git` or index:
  ```ts
  gitdir: path.join(Global.Path.data, "snapshot", ctx.project.id, Hash.fast(ctx.worktree)),
  const args = (cmd: string[]) => ["--git-dir", state.gitdir, "--work-tree", state.worktree, ...cmd]
  // track():
  yield* add()                                   // git add --all (incremental: diff-files + ls-files --others)
  const result = yield* git(args(["write-tree"]), { cwd: state.directory })
  // patch(hash):
  yield* add()
  git([...quote, ...args(["diff", "--cached", "--no-ext-diff", "--name-only", hash, "--", "."])])
  ```
- Configuration: `core.fsmonitor false`, `feature.manyFiles`, `index.version 4`,
  `core.untrackedCache true`. It also blocks large untracked files.
- The session loop (`packages/opencode/src/session/processor.ts`) calls
  `track()` on every model `step-start` and `patch(ctx.snapshot)` on
  `step-finish`, then stores a `{type:"patch", hash, files}` part:
  ```ts
  case "step-start":
    if (!ctx.snapshot) ctx.snapshot = yield* snapshot.track()
  ...
  case "step-finish": { ...
    if (ctx.snapshot) {
      const patch = yield* snapshot.patch(ctx.snapshot)
      if (patch.files.length) { yield* session.updatePart({ ..., type: "patch", hash: patch.hash, files: patch.files }) }
  ```
- This is exactly "which files did this step change, however they changed".

### 5c. Reproduced locally (VERIFIED, git 2.43, in /tmp)
- A shadow `--git-dir` plus `--work-tree` snapshot, then `sed -i`, a Python
  append and a new file, followed by `git diff --name-status T0 T1`, reported
  `M f.txt`, `M g.txt`, `A h.txt`. `git status` of the real repo was unaffected.
- The temp-`GIT_INDEX_FILE` variant also left the real index untouched.
- **Overlap check between two sessions' uncommitted states:**
  `git merge-tree --write-tree --name-only --merge-base=<base> <A> <B>`
  printed `f.txt` plus `CONFLICT (content)`, with exit code 1. On git 2.43,
  `merge-tree` refuses bare trees ("expected commit type"), so each snapshot tree
  must first be wrapped with `git commit-tree <tree> -p HEAD`, as Codex does.

### 5d. Other approaches considered
- **mtime scan**: mcp_agent_mail uses file mtimes only for staleness. It is
  cheap, but content-blind and cannot tell which process wrote the file.
- **inotify / watchdog**: it shows *that* a file changed, but not *who* changed
  it. Inotify events carry no PID. **UNVERIFIED:** Linux fanotify can report the
  PID but needs `CAP_SYS_ADMIN`, and I found no agent tool using it. With two
  sessions in one folder, a watcher cannot attribute a write on its own.
- **Cline and Gemini CLI checkpoints** (shadow git repos). UNVERIFIED: known from
  memory and search, code not read in this study. The pattern is the same as
  opencode's.
- **Codex `--json` `file_change` item.** VERIFIED in openai/codex main,
  `codex-rs/exec/src/exec_events.rs`:
  - The item is `{"type":"file_change","changes":[{"path":..., "kind":"add|delete|update"}],"status":...}`.
  - **Possible ccdb bug:** `claude_code_core/codex_runner.py` matches
    `item_type == "file_changes"` (plural) and reads `item["text"]`.
  - UNVERIFIED against codex-cli 0.155.0's real output. Run one `codex exec
    --json` that edits a file to confirm.
  - If confirmed, Codex edits never reach the collision watcher, even when they
    come from `apply_patch`.

---

## 6. Other coordination tools found

- **clash-sh/clash** (MIT, commit `2ac931c`), https://github.com/clash-sh/clash
  - `plugins/clash/hooks/hooks.json` registers the `PreToolUse` matcher
    `Write|Edit|MultiEdit` to run `clash check`.
  - `src/check.rs` flags a conflict when either of two things holds:
    - an in-memory 3-way merge (`gix` `merge_trees(base, head1, head2)`) of this
      worktree's HEAD with each other worktree's HEAD conflicts on the target
      file;
    - the other worktree's copy on disk differs from its HEAD
      (`file_has_active_changes`).
  - On a hit it returns `permission_decision: "ask"` with a reason, so Claude
    Code asks the human.
  - Limitation: the merge uses HEAD commits, so uncommitted work only counts
    through the single-file disk-vs-HEAD check.
- **garniergeorges/claude-presence** (MIT, commit `b428a1c`)
  - `src/tools/locks.ts` `resource_claim`: advisory named locks with TTL,
    optional capacity (semaphore), and a **waiting queue**. When a lock is
    released or expires, the next waiter gets an inbox message ("a slot is now
    free... claim it") (`src/db/repository.ts` `notifyWaiters`).
  - `hooks/user-prompt-submit.sh` injects the unread inbox and a presence summary
    as `hookSpecificOutput.additionalContext` on every prompt, and warns when
    another session is on the same branch.
  - This is a clean hand-off pattern.
- **swoofer/mcp-coordinator** (MIT, commit `b341906`)
  - `src/working-files-tracker.ts`: "Tracks files an agent is currently editing
    (between PreToolUse and PostToolUse hooks)", with a TTL sweeper.
  - `src/conflict-detector.ts` reports `file_overlap` ("Hot file: X recently
    edited by Y") from a 60-minute activity window, plus module-level and
    declared-plan overlap.
  - `src/git-cochange-builder.ts` builds "files that change together" from
    `git log` to widen the risk estimate.
- Articles (UNVERIFIED):
  - dev.to "Coordinate Multiple Claude Code Sessions on a Shared Repo" (points
    at claude-presence and mcp-coordinator);
  - readysolutions.ai "Seven Claude Code sessions on one repo";
  - anthropics/claude-code#19364, a request for a session lock file.

---

## 7. Recommendation for ccdb

1. **Per-thread shadow snapshots (the opencode pattern, MIT, reimplemented in
   Python).**
   - Keep one shadow git-dir per repo root, under ccdb's data directory.
   - Take a snapshot when a turn starts. Also snapshot around each shell or
     command tool: Claude `Bash` tool_use/tool_result, and Codex
     `command_execution` item.started/completed.
   - Take a final snapshot when the turn ends.
   - `git diff --name-status T_before T_after` then gives the files changed in
     that window, whether by Edit, Codex patch or `sed`.
   - Run it with `asyncio.create_subprocess_exec`, and never `shell=True`.
   - Guards:
     - Only run inside a git work tree (`git rev-parse --show-toplevel`).
     - Refuse `$HOME`.
     - Skip untracked files over a size cap, and stop past a file-count cap (the
       Codex disk-fill lesson).
     - Use a per-repo asyncio lock, because git index writes serialise anyway.
2. **Attribution in a shared folder.**
   - Paths named by the tool itself (Edit/Write `file_path`, Codex `file_change`
     `changes[].path`) are *certain*.
   - Other changed paths are attributed to the thread whose command window was
     open.
   - If windows of two threads overlapped in time, mark the path *ambiguous* and
     warn both threads rather than guessing. This is GitButler's "set none on
     multiple owners" rule.
   - Store `path -> {thread_id: (blob_sha_after, time)}` per repo root.
3. **Overlap warning.**
   - When a thread's window diff contains a path that another *live* thread
     changed within the activity window, post the existing collision notice to
     both threads and the lounge. Add "unannounced" when neither thread held a
     claim.
   - To say whether the edits actually clash, not just share a file, wrap both
     snapshot trees with `commit-tree` and run
     `git merge-tree --write-tree --name-only --merge-base=<HEAD> A B`
     (VERIFIED on git 2.43). This gives "same file, different parts,
     auto-merges" versus "real conflict". It works the same for threads in
     separate worktrees.
4. **Prevention, optional.**
   - Automatically turn a thread's first write to a path into a short claim
     (a lease that auto-renews while the thread is active).
   - Before the next write, a Claude PreToolUse hook can return `"ask"` with the
     holder's name (clash pattern) instead of GitButler's blocking 30 s wait.
   - For Codex and shell there is no pre-hook, so ccdb can only warn afterwards.
5. **Hand-off.** When a thread's turn ends, release its claims and queue a note
   for the waiting threads. ccdb injects that note into the waiting thread's next
   prompt, which is claude-presence's queue plus UserPromptSubmit-injection
   pattern, done with ccdb's lounge and claims.
6. **Verify first:** check the `file_changes` vs `file_change` item type in
   `codex_runner.py` against a real codex-cli 0.155 run.
