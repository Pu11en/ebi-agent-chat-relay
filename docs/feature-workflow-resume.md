# Resume the authorized Lockin AI setup trial

This is continuation of the setup request in Discord thread
1546685954167672912. The task is NOT ready yet. Do not ask Drew to authorize
this trial again. The two small feature builds were explicitly authorized.
Other projects/conversations must remain intact; no bot restart.

## Durable locations

- Retained installed worktree: `/home/drewp/main-projects/lockin-workflow-runtime`,
  branch `lockin/feature-workflow-runtime`. The command `lockin-workflow` points
  to its coordinator module. It is deliberately not a disposable session branch.
- Lead development worktree: `/home/drewp/main-projects/wt-1546685954167672912`,
  branch `session/1546685954167672912`. If Ebi removed this clean worktree,
  recreate it from that existing branch through git worktree add; do not create
  a fresh branch from main or overwrite any other worktree.
- Run state: `/home/drewp/.local/state/lockin-ai/feature-workflow/trial-1546685954167672912/`.
  `state.json`, `approval.json`, `threads/`, `results/`, `watch.log`.
- Manifest: `docs/setup-evidence/feature-workflow/trial-manifest.json` in the
  installed worktree. The external watcher uses that retained worktree as repo.
- Worker parent: 1546658184091934740; planning threads: greeting
  1546687948588589086, word-count 1546687950375493752. Guild 1546639912848199742.

## Complete the trial

1. Read the installed skill source in extensions/feature_workflow/skill and
   coordinator status/results. Use the existing exact approval. Never respawn
   a completed task. On a partial frontier, integrate what is ready and restart
   the watcher for any approved pending tasks before ending an idle turn again.
2. Review each actual worker branch diff, tests, and Discord final message.
   Merge both verified worker commits into the lead's isolated worktree
   preserving ancestry. The frozen OpenSpec plan files stay unchanged.
3. Apply `docs/setup-evidence/feature-workflow/lead-integration.patch` in the
   lead worktree; this is the saved lead CLI and its three acceptance tests.
   Run standard-library unittest discovery across the entire
   examples/feature_workflow_trial directory, then Ruff and Pyright on the
   demo. Verify JSON for name `  dReW  ` and text `Lockin AI, works!` gives
   `Hello, dReW!` and 3, and blank inputs give friend/0.
4. Record the live two-worker process-overlap evidence from the run state;
   worktrees and branch/commit evidence; unapproved gate and repeated collection
   without creating any additional threads; integration output and focused
   checks. If actual parallel execution wasn't observed, do not claim it was.
5. Commit the integrated lead change. Fast-forward the retained runtime
   worktree to that commit, then run coordinator `integrate` for each task
   using runtime `--repo` and its actual HEAD (the CLI enforces ancestry and
   a clean tracked integration checkout). Repeat tick/collect to prove no
   completed work is duplicated. Save final state/evidence in docs.
6. Finish installing the local lockin-feature-workflow skill into shared
   CODEX_HOME/skills (the two upstream coordination skills and six OpenSpec
   skills already installed). Replace only the interim planning pointer in
   shared CODEX_HOME/AGENTS.md after checking its current contents and claiming
   that file. Preserve every unrelated instruction and all old plans. User
   authorized this setup; no additional installation permission required.
7. Update LOCKIN-AI.md and docs/feature-workflow-setup.md in our isolated
   worktree, preserving references. Update the copied cheat sheet and publish
   a concise verified addition in the existing Discord cheat-sheet channel
   1546670029422985226 through /api/notify (authorized setup guide update).
   Do not edit canonical main worktree directly. Keep maintained guides in the
   retained installed worktree and link there from the shared workflow pointer.
8. Commit and push session branch to existing voice-backup remote (personal
   fork already configured; no new GitHub repository and no main push/merge).
   Fast-forward runtime to final commit. Verify bot PID still 36020/health okay.
   Release claims and post a short closing lounge note. Final to Drew: verified
   result, actual two worker links, and just one simple Discord try-it action.
   Distinguish the known pre-existing Teams test failure from workflow checks.

The full suite had one existing failure on unchanged main; details in
`docs/setup-evidence/feature-workflow/verification.md`. No upstream PR has
been submitted and no claim that the whole repository is test-clean was made.
