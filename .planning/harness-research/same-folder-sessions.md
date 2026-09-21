# Several agent sessions working in one folder: how existing tools handle it

Researched 2026-09-18. The question: when several AI coding agents (or people)
work on the same repository at once, how do tools stop them from overwriting
each other, and how is the work combined afterwards? Primary sources are linked.
Items marked **UNVERIFIED** come only from secondary sources, or could not be
confirmed on a primary page.

## The six patterns

1. **One git worktree per session** (a separate checkout and branch that shares
   one `.git`). Merged back through a PR, a local merge/rebase, or "apply to my
   checkout". This is the dominant pattern.
2. **One container or VM per session** (sandbox plus branch). Merged back as a
   branch or PR. Used by cloud agents and by container-use.
3. **One shared folder, with each session's changes attributed to its own
   branch** (GitButler virtual branches driven by agent hooks). There is no
   copy, so dev servers, databases and `node_modules` are shared.
4. **One shared folder, with coordination instead of isolation**: advisory file
   leases, a claimed task list, messages between agents, or file ownership
   ("each teammate owns different files"). Examples are Claude Code agent teams,
   cross-session messaging and MCP Agent Mail.
5. **Hard locks** (one editor per file): Perforce `+l` and `git lfs lock`. Humans
   use these mostly for binary files that cannot be merged.
6. **Integration-time serialization**: feature branches, then PRs, then a merge
   queue (GitHub, Mergify, Graphite), with stacked PRs for work that depends on
   other work. This is how the human world combines parallel work, whatever
   isolation came before.

## AI tools

### Conductor (conductor.build)
- **Isolation:** one git worktree per workspace, and one branch per workspace. "A
  branch can only be checked out in one workspace at a time." "Workspace
  isolation is development isolation, not a security boundary."
- **Ports:** `CONDUCTOR_PORT` is "First port in a range of 10 ports assigned to a
  local workspace". Scripts use `$((CONDUCTOR_PORT + 1))` and so on.
- **Setup:** a setup script runs after each workspace is created. For `.env`,
  either symlink it (`ln -s "$CONDUCTOR_ROOT_PATH/.env" .env`) or use the "Files
  to copy" setting for gitignored files.
- **Shared resources:** `scripts.run_mode = "nonconcurrent"`: "Starting a run
  script stops any other run script first". This is meant for workspaces that
  share fixed ports or databases.
- **Cleanup:** archiving deletes the workspace directory. An archive script
  cleans up anything outside it, such as databases or containers.
- **Merge:** the branch is "the review and PR unit".
- **Guidance:** "Use multiple workspaces when tasks should land independently.
  Use multiple agents in one workspace when the work shares the same branch,
  code state, and context."
- Sources: https://www.conductor.build/docs/concepts/workspaces-and-branches ,
  https://www.conductor.build/docs/concepts/parallel-agents ,
  https://www.conductor.build/docs/reference/scripts ,
  https://www.conductor.build/docs/reference/environment-variables

### Claude Squad (smtg-ai/claude-squad)
- **Isolation:** a tmux session plus a git worktree for each instance. "Each
  task gets its own isolated git workspace, so no conflicts."
- **Merge:** a key commits and pushes the branch to GitHub, and another commits
  and pauses. You check out the branch to review it.
- **Cleanup:** `reset` clears all instances. No port or env handling is
  documented.
- Source: https://github.com/smtg-ai/claude-squad

### Crystal / Nimbalyst (stravu)
- Crystal ran each session in its own worktree, committed after each iteration,
  and offered "rebase from main" and "squash and rebase to main" (per the
  README/changelog, as reported by search).
- **Deprecated in Feb 2026** and replaced by Nimbalyst, which also puts each
  session in its own worktree and adds "Link sessions to files and files to
  sessions".
- Nimbalyst's merge and conflict handling is **UNVERIFIED**; the README does not
  describe it.
- Sources: https://github.com/stravu/crystal , https://github.com/Nimbalyst/nimbalyst

### Vibe Kanban (BloopAI)
- **Isolation:** each task attempt gets its own worktree. "each workspace gives
  an agent a branch, a terminal, and a dev server."
- **Setup and cleanup:** a list of files is copied into each worktree before the
  setup script runs (issue #444 asked for this, and #1947 asked for
  `.worktreeinclude`). A cleanup script runs after each agent turn.
- **Housekeeping:** orphaned and expired worktrees are cleaned up automatically
  (`DISABLE_WORKTREE_CLEANUP` turns this off for debugging).
- **Merge:** rebase onto main, then merge and clean up, or open a PR.
- **The project is sunsetting** (per its README banner). The announcement date is
  **UNVERIFIED**.
- Sources: https://github.com/BloopAI/vibe-kanban ,
  https://github.com/BloopAI/vibe-kanban/issues/444 ,
  https://github.com/BloopAI/vibe-kanban/issues/1947 (rebase and merge details
  come from secondary sources: https://virtuslab.com/blog/ai/vibe-kanban ,
  https://deepwiki.com/BloopAI/vibe-kanban/2.4-github-integration-and-pr-workflow)

### Sculptor (Imbue)
- **At launch:** Sculptor put every agent in its own container, "without the
  hassle of git worktrees".
- **Pairing Mode:** synced an agent's container files with your local checkout in
  both directions. Sculptor would "flag potential merge conflicts automatically"
  and could hand conflicts back to the agent.
- **Current product page:** now says "Each workspace is an isolated worktree with
  its own branch, terminal, and diff view", and that it is MIT open source. So
  the design appears to have moved from containers to worktrees. **UNVERIFIED**
  when that changed.
- Sources: https://imbue.com/blog/sculptor-announce , https://imbue.com/product/sculptor ,
  https://docs.imbue.com/changelog

### container-use (Dagger)
- **Isolation:** "Each agent gets a fresh container in its own git branch."
- **Review and merge:** `container-use list`, `log`, `diff`, `terminal`,
  `checkout`, `merge` (keeps the agent's commits), `apply` (stages the changes so
  you write the commit) and `delete` (environments are "disposable by design").
- Sources: https://github.com/dagger/container-use ,
  https://container-use.com/environment-workflow

### Cursor (parallel agents, /worktree, /best-of-n, background/cloud agents)
- **Isolation:** each parallel agent gets its own worktree. `/best-of-n` runs
  several models, each in a separate worktree.
- **Setup:** `.cursor/worktrees.json` holds the setup commands
  (`setup-worktree-unix`, `setup-worktree-windows`, `setup-worktree`), and
  `$ROOT_WORKTREE_PATH` points at the original checkout (for copying `.env`).
- **Merge:** `/apply-worktree` brings the changes into your main checkout, or you
  commit and push from the worktree.
- **Cleanup:** automatic. `cursor.worktreeCleanupIntervalHours` (every 6 hours)
  and `cursor.worktreeMaxCount` (25 per machine).
- **Ports:** Cursor has no port mechanism.
- Source: https://cursor.com/docs/configuration/worktrees

### OpenAI Codex
- **App worktrees:**
  - Worktrees live in `$CODEX_HOME/worktrees`. They start in **detached HEAD**
    "to prevent polluting your branch list", because "Git prevents the same
    branch from being checked out in more than one worktree at a time."
  - **Handoff** moves a thread between Local and Worktree, and Codex does the git
    steps. "any files that are part of your .gitignore file won't move with the
    thread."
  - Setup scripts come from `.codex` local environments, and `.worktreeinclude`
    copies gitignored files.
  - Cleanup: "By default, Codex keeps your most recent 15 Codex-managed
    worktrees." It snapshots before deleting, and skips pinned, in-progress and
    permanent worktrees.
  - Open bugs: setup scripts are not run for tool-created worktrees or over
    remote SSH (openai/codex #23648, #27584).
- **Cloud tasks:** an isolated container per task (`codex-universal` image plus a
  setup script). Secrets are removed before the agent phase. Results come back as
  a diff and summary, and then a PR. The exact per-task container mechanics are
  **UNVERIFIED** (the docs page is vague).
- **Codex CLI:** has no built-in worktree feature. You create worktrees yourself.
- Sources: https://learn.chatgpt.com/docs/environments/git-worktrees
  (redirected from developers.openai.com/codex/app/worktrees),
  https://developers.openai.com/codex/cloud/environments ,
  https://learn.chatgpt.com/docs/cloud , https://github.com/openai/codex/issues/23648 ,
  https://github.com/openai/codex/issues/27584

### Claude Code
- **Starting in a worktree:** `claude --worktree <name>` (`-w`) creates
  `.claude/worktrees/<name>/` on the branch `worktree-<name>`, based on the remote
  default branch. Setting `worktree.baseRef: "head"` bases it on the current
  HEAD instead. `--worktree "#1234"` starts from a PR.
- **Enforced isolation:** while a session is in a worktree, Claude Code
  **blocks** Edit/Write calls into the main checkout, Bash whose working
  directory resolves to the main checkout, git redirects (`-C`, `GIT_DIR`), and
  commands whose git use it cannot verify. Isolation is enforced by tool checks,
  not by instruction.
- **Subagents:** `isolation: worktree` in a subagent's frontmatter gives it a
  temporary worktree. The worktree is removed if unchanged. If it has changes, a
  periodic sweep removes it after `cleanupPeriodDays`, but only when nothing is
  unpushed. A running agent holds `git worktree lock` on its worktree.
- **Background sessions (agent view):** they "start in your working directory.
  Before editing files, Claude moves the session into an isolated git worktree
  ... so parallel sessions can read the same checkout but each writes to its
  own." That is, **isolation happens lazily, on the first write**. The session
  commits and pushes, may open a draft PR, and never merges into main.
- **Outside git:** "sessions write to the working directory directly and aren't
  isolated from each other, so avoid dispatching parallel sessions that edit the
  same files." A `WorktreeCreate`/`WorktreeRemove` hook can supply isolation for
  SVN, Perforce and similar systems.
- **`.worktreeinclude`:** copies gitignored files such as `.env` (gitignore
  syntax).
- **Exit behavior:** interactive exit removes a clean worktree and prompts if
  there is work. `-p` runs never auto-clean.
- **Coordination features:**
  - **Cross-session messaging** (`ListAgents`/`SendMessage`, a per-session Unix
    socket) lets sessions in parallel worktrees tell each other "what landed".
  - **Agent teams** do *not* isolate teammates in worktrees: "Two teammates
    editing the same file leads to overwrites. Break the work so each teammate
    owns a different set of files."
  - The agent-teams task list uses **file locking for task claims**.
  - `/batch` splits a big change into 5 to 30 worktree-isolated subagents, each
    opening its own PR.
- Sources: https://code.claude.com/docs/en/worktrees ,
  https://code.claude.com/docs/en/agent-view , https://code.claude.com/docs/en/agents ,
  https://code.claude.com/docs/en/agent-teams ,
  https://code.claude.com/docs/en/cross-session-messaging

### GitHub Copilot coding agent (cloud agent)
- **Isolation:** runs in an ephemeral GitHub Actions environment. "can only work
  on one branch at a time and can open exactly one pull request to address each
  task."
- **Permissions:** it can only push to `copilot/` branches, and branch
  protection and required checks still apply.
- **Limits:** 59 minutes per session.
- **Merge:** a human reviews and merges the PR, and the person who requested the
  PR cannot approve it.
- Sources: https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent ,
  https://docs.github.com/en/enterprise-cloud@latest/copilot/responsible-use-of-github-copilot-features/responsible-use-of-copilot-coding-agent-on-githubcom

### Devin (Cognition)
- **Isolation:** one VM per session, and child sessions each get their own VM.
- **Setup:** machine snapshots and startup commands replace per-session setup.
- **Output:** a PR per session, and archiving a parent closes its children's PRs
  (release notes, 2026-09-09).
- "Manager Devin merges the successful workers' changes into one branch/PR" is
  **UNVERIFIED** (secondary source only).
- Sources: https://docs.devin.ai/release-notes , https://docs.devin.ai/onboard-devin/repo-setup

### Jules (Google)
- **Isolation:** clones the repo into an isolated cloud VM for each task, with an
  optional environment snapshot.
- **Output:** publishes a branch and opens a PR.
- **Concurrency limits** by plan: 3, 15 or 60 concurrent tasks. These numbers are
  **UNVERIFIED** (from secondary sources; the docs site was not fetched).
- Sources: https://jules.google/ (plus secondary:
  https://www.morphllm.com/comparisons/jules-google-coding-agent)

## Version-control-level building blocks

- **git worktree:** separate working directories that share one object store and
  refs. A branch can be checked out in only one worktree at a time.
  `git worktree lock` protects a worktree from pruning.
  https://git-scm.com/docs/git-worktree
- **Jujutsu workspaces:** `jj workspace add` creates several working copies
  backed by one repo.
  - If a commit is rewritten from another workspace, this one becomes *stale*;
    `jj workspace update-stale` fixes it.
  - Conflicts are first-class: they are recorded in commits and can be resolved
    partially. The working copy is auto-committed.
  - https://docs.jj-vcs.dev/latest/working-copy/
- **GitButler parallel (virtual) branches:** several branches are applied to
  **one** working directory, and each change or hunk is assigned to a lane.
  - For agents, Claude Code hooks call `but claude pre-tool` / `post-tool`
    (PreToolUse/PostToolUse on Edit|MultiEdit|Write) and `but claude stop`. The
    result is "one commit per chat round, and one branch per Claude Code
    session", all in the same folder.
  - **Limitation:** GitButler's maintainers say "overlapping areas can definitely
    lead to problems related to races and interference". A session is hardwired
    to one branch.
  - https://docs.gitbutler.com/features/branch-management/virtual-branches ,
    https://blog.gitbutler.com/parallel-claude-code/ ,
    https://trigger.dev/blog/parallel-agents-gitbutler
  - The hooks doc page returned 404 when fetched; the hook names come from search
    snippets of the gitbutler-docs repo and issue #13316.
- **Sapling:** stacks of commits, not branches. `sl pr submit --stack` creates one
  PR per commit, and ReviewStack reviews stacks.
  https://sapling-scm.com/docs/overview/stacks/ ,
  https://sapling-scm.com/docs/addons/reviewstack/

## How human teams do it

- **Feature branches, PRs and a merge queue:**
  - GitHub merge queue tests each PR on a temporary branch that holds the latest
    base plus every PR ahead of it in the queue (the `merge_group` event).
  - A PR that fails is removed, and the temporary branch is rebuilt.
  - https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- **Mergify:** speculative (parallel) checks and batching.
  https://docs.mergify.com/merge-queue/parallel-checks/ ,
  https://docs.mergify.com/merge-queue/batches/
- **Stacked PRs:**
  - **Graphite** has a stack-aware merge queue.
    https://graphite.com/docs/graphite-merge-queue
  - **GitHub gh-stack** entered private preview on 2026-04-13. It ships an agent
    skill ("teaches compatible AI coding agents how to create and manage
    stacks"). https://www.infoq.com/news/2026/04/github-stacked-prs/
- **Code ownership:** CODEOWNERS automatically requests review from the owners of
  the files a PR touches, and can be made a required review.
  https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- **Hard locks:**
  - **Perforce** `+l` (exclusive open, which a typemap can apply site-wide):
    https://www.perforce.com/manuals/p4sag/Content/P4SAG/superuser.basic.typemap_locking.html
  - **git-lfs** `--lockable`: files are made read-only until you run
    `git lfs lock`, and pushes that touch another user's locked file are
    refused.
    https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-lock.adoc ,
    https://github.com/git-lfs/git-lfs/wiki/File-Locking
  - Both are recommended mainly for files that cannot be merged, such as
    binaries.
- **Advisory leases for agents:** MCP Agent Mail's
  `file_reservation_paths(paths[], ttl_seconds, exclusive, reason)`.
  - Leases are advisory and time-limited (TTL), and use glob patterns.
  - An optional pre-commit guard blocks commits that touch files another agent
    has reserved; `AGENT_MAIL_BYPASS=1` overrides it.
  - https://github.com/Dicklesworthstone/mcp_agent_mail

## Known problems and lessons

- **Ports:** every worktree's dev server tries to use the same port.
  - Conductor gives each workspace 10 ports (`CONDUCTOR_PORT`).
  - Vibe Kanban gives each workspace its own dev server.
  - Conductor's "nonconcurrent" mode stops the others' servers instead.
  - Cursor, Claude Code and Claude Squad leave this to the user.
- **Databases and services:** Trigger.dev dropped worktrees because agents shared
  one Postgres, Redis, ClickHouse and S3 and collided on schema and test data.
  Conductor's answer is an archive script that tears down per-workspace
  resources (keyed by `CONDUCTOR_WORKSPACE_NAME`).
- **Disk and install time:** Trigger.dev reported "9.82 GB across two worktrees"
  for a codebase of about 2 GB, because `node_modules` and build output are
  duplicated. Mitigations:
  - setup scripts;
  - a symlink to the root `.env`;
  - snapshots (Devin, Jules, Codex cloud caching);
  - max-count cleanup (Cursor 25, Codex 15).
- **Gitignored files don't follow:** `.env` and local config files are missing
  from a new worktree. The convergent fix is a `.worktreeinclude` file (Claude
  Code and Codex; requested in Vibe Kanban), or Cursor's `$ROOT_WORKTREE_PATH`
  and Conductor's `CONDUCTOR_ROOT_PATH`.
- **Same branch twice:** git refuses to check out one branch in two worktrees.
  Codex uses detached HEAD, and Conductor maps exactly one branch to one
  workspace.
- **Two agents editing one file:**
  - Every tool's answer is either "isolate, then merge later" or "partition file
    ownership".
  - **No primary source was found for a tool that detects, in real time, two
    live sessions editing the same file in the same folder.** The closest are:
    GitButler hooks (attribution only, with races acknowledged), MCP Agent Mail
    leases (advisory plus a commit guard), and Sculptor's merge-conflict
    flagging at sync time.
- **Coordinate vs isolate:**
  - Anthropic: isolate with worktrees "when tasks touch the same files". For same-file
    or sequential work "a single session or subagents are more effective".
  - Conductor: several agents share one workspace when they share "the same
    branch, code state, and context".
- **Cleanup must never lose work:** Claude Code keeps any worktree that has
  uncommitted, untracked or unpushed changes. It locks worktrees while an agent
  runs, and only sweeps worktrees it created (a marker in git metadata). Codex
  snapshots before deleting. Headless `-p` runs are never auto-cleaned.
- **Isolation should be enforced, not requested:** Claude Code went from telling
  the agent to blocking tool calls that escape the worktree. Background sessions
  isolate lazily, only when they first write.

## Relevance to ccdb (Discord thread = session)

`claude_discord/concurrency.py` currently tells a session in its prompt to create
`.worktrees/wt-{thread_id}` on `session/{thread_id}` when another active session
shares the project. Isolation today is therefore procedural: the prompt asks for
it, nothing enforces it. The sources above point toward:

- enforced or lazy isolation, such as Claude Code's own background-session
  behavior or `--worktree`;
- per-session ports;
- `.worktreeinclude`-style copying;
- cleanup that never deletes unmerged work;
- cross-session messaging or leases for related threads that should cooperate
  instead of splitting.
