# Release policy

- Owner instruction: push when all planned tasks are complete because acceptance will happen through normal Discord use.
- After every approved program task is integrated and the final verification passes, push the completed integration branch to the existing GitHub remote without asking for another pre-push approval.
- Activate the completed local dev worktree through the repository's supported dev-mode flow so Discord usage exercises that exact branch.
- Do not merge a pull request, deploy elsewhere, or retire compatibility commands before the applicable acceptance gates in the approved plans.
- If final verification fails, do not push or activate; keep repairing the in-scope failure and report any genuine blocker.
