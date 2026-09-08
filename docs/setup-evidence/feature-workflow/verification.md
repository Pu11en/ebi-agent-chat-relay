# Verification checkpoint

Coordinator: 32 targeted tests, Ruff lint/format, and Pyright pass. OpenSpec
strict validation passes both changes. Integration acceptance checks were
written FIRST and failed because the lead CLI did not exist; the pending lead
CLI and tests are preserved in `lead-integration.patch` for application after
worker commits arrive.

Full suite at the first checkpoint: 2,879 passed and one failed; 84% package
coverage. The one failure is the existing Teams oversized-body test at
`tests/test_teams_relay.py:140`, which returns 400 instead of expected 200.
It was reproduced individually on unchanged main 7fc303c with the same installed
environment (aiohttp 3.14.3). No Teams source or test was changed. Core Ruff
lint/format and Pyright passed. The final full run after the cleanup/approval
fixes had 2,886 passing tests and the same one baseline failure, recorded in
`full-tests-final.log`.

Live observations so far: two actual Discord planning sessions ran, each
created only its named OpenSpec plan and preserved its distinct decisions.
Their planning commits are 462c09c and e663639; both are merged into the agreed
foundation. No worker was created by the actual unapproved tick. Trial approval
covers exactly greeting and word-count. Worker builds and integrated proof
remain pending at this checkpoint.

Ebi PID remained 36020. No core bot code, shared database, existing project
files, existing plan content, or unrelated live thread was modified. The
coordinator is a separate process using the existing localhost APIs. Normal Ebi
cleanup removed the clean greeting planning worktree; the branch survived,
which prompted a tested collector fix for normal worker cleanup.
