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

Follow-up after the plan handoff: the server remained busy, and waiting for
two free slots delayed the trial. Added opt-in --queue-ready admission with
a failing-first regression test. It bounds this run's outstanding workers,
retains the actual Ebi execution semaphore, approval and dependency gates,
and sends no stop/interrupt to existing conversations.

## Completed trial

Both actual builders completed, committed and pushed their assigned files.
Their Codex processes overlapped at 2026-09-08T01:44:07Z (20:44 Chicago); see
worker-process-overlap.json. Both used foundation 7d46035 and separate session
branches/worktrees. Greeting commit ad094786 and counter commit 8cc12dbb were
reviewed and merged with ancestry preserved. Integrated commit 13de390 passed
all 14 unittest cases, Ruff lint/format, and Pyright. Mixed-case/trimmed name
and punctuation-count behavior worked together; blank inputs produced friend/0.
Both OpenSpec changes still pass strict validation.

The coordinator recorded both tasks integrated and repeated collect/tick
kept the same two worker IDs with no additional dispatch. The real completion
relay is marked sent. See completed-state.json.

The final coordinator has 33 passing focused tests, including tested queue
admission. The full-suite baseline exception described above remains unchanged.
The human try-it step is available in the usage guide; no claim is made that
Drew has already performed it.
