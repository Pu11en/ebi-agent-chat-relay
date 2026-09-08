# Lockin AI concurrency review and deployment handoff

Drew requested review, corrections, and implementation, then plans to switch
models for guidance. This checkout holds the reviewed changes. Check its Git
status and latest commit before deployment. The running service was not
restarted during this review; code verification is separate from live rollout.

## Corrected decision

Use one shared pool of 10 executing turns for this personal instance. A fixed
6-chat / 4-worker partition would unnecessarily queue a seventh conversation
while four worker slots sat unused. Ten is an initial operating choice, not a
measured upper bound for the machine or the Codex subscription. Work above ten
still waits. Actual feature prerequisites still serialize dependent work.

The existing asyncio semaphore provides a shared admission queue. Keep managers
finite: submit eligible work, start a durable external watcher, post the links,
and end the idle model turn. The watcher wakes the manager at integration.
No model process should spend its turn polling for available worker slots.

The previous model's citation to an OpenAI API model rate-limit table did not
establish ChatGPT-authenticated Codex subscription concurrency. Account limits
remain unverified. The observed three-turn restriction comes from local Ebi
configuration and source. Neither a fork switch nor extra bot instances is
required to remove it.

## Changes in this checkout

- Adopted setup session commit 986d1ec, including its live trial handoff, before
  extending it. Default coordinator admission now submits to Ebi's queue.
- A run's outstanding-worker window includes dispatched, spawning and ambiguous
  tasks, so repeated ticks do not flood the shared queue. The default remains
  three per run; `--max-running` is not the bot's global capacity.
- `GET /api/sessions` distinguishes `running`, `queued`, and `history`, accepts
  the corresponding filters, and includes `capacity.limit/running/queued`.
- Discord cards say Preparing, Queued, Running, then Turn finished. Existing
  historical cards are not retroactively rewritten.
- Queue time does not start the stalled-model warning timer. The red Stop
  button cancels admission before a process exists. Queue cancellation removes
  its registry entry, does not release another turn's slot, and does not remove
  a worktree for a turn that never started.
- Reject nonpositive/bool capacities instead of silently accepting a zero cap
  that prevents all execution.

No role classification, reserved pools, adaptive resizing, priority scheduler,
or automatic per-project fairness beyond Ebi's common admission queue is being
claimed. Normal conversations remain responsive while capacity is available;
ten simultaneous long turns can still delay an eleventh request.

## Deployment boundary

The active service remains `ebi-agent-chat-relay.service`, with working directory
`/home/drewp/main-projects/ebi-agent-chat-relay`. Preserve that directory because
the instance's data paths and extensions depend on it. Its environment file is
there too. The staged service override sets `MAX_CONCURRENT_SESSIONS=10`;
this inherited environment value takes precedence over the existing .env's 3.
The original environment file and upstream framework default remain unchanged.

The reviewed checkout is `/home/drewp/main-projects/lockin-concurrency-review`,
branch `fix/lockin-concurrency-review`. It includes the feature workflow runtime
at 986d1ec. The other setup session may subsequently add trial integration
commits; inspect and merge those before making this a combined runtime. Retain
the current main checkout, both branches, saved plans, and live worktrees.

Activate the reviewed relay code in one controlled service restart after
in-flight and queued turns have drained, or after deliberately preserving and
resuming their exact pending work. A saved conversation ID alone does not
preserve an as-yet unstarted prompt. Do not silently restart a busy instance or
claim editing the environment file changed the already-running semaphore.

An isolated startup can keep the canonical working directory and existing venv
while selecting this source explicitly (service ExecStart override):

```ini
[Service]
Environment=MAX_CONCURRENT_SESSIONS=10
ExecStart=
ExecStart=/home/drewp/main-projects/ebi-agent-chat-relay/.venv/bin/python -P -c "import sys; sys.path.insert(0, '/home/drewp/main-projects/lockin-concurrency-review'); from claude_discord.cli import main; main()" start --env /home/drewp/main-projects/ebi-agent-chat-relay/.env
```

The exact staged file is `docs/setup-evidence/concurrency-review/service-override.conf`.
It is not installed. The source import and CLI help were checked from the real
canonical working directory using the existing venv. Configuration loading
with the service environment and real .env yielded an effective limit of 10.
Install the override at the deployment boundary. Once active, read
`/api/health` and `/api/sessions`: capacity.limit must be 10. Verify new running,
queued and finished cards and a bounded actual multi-thread workload. Inspect
CPU/RAM and account-side errors during that trial. Automated fake-runner tests
verify the admission behavior, not subscription throughput at ten live turns.

The installed `lockin-workflow` launcher currently targets
`/home/drewp/main-projects/lockin-workflow-runtime`. To deploy the coordinator
default and updated guidance, merge this reviewed branch into the retained
workflow runtime after incorporating the setup session's latest commits. The
current live trial already explicitly uses `queue_ready=True` from 986d1ec and
does not need to be recreated or reapproved.

Rollback: remove the installed concurrency override, reload systemd and restart
at the same deliberate boundary. This restores the original ExecStart and
.env capacity of three. Keep the review branch and
trial results; no source or state deletion is required.

## Verification on 2026-09-07

The full suite finished with **2,897 passed and one failed**, 84% coverage.
The sole failure is the pre-existing Teams oversized-activity HTTP status test
at `tests/test_teams_relay.py:140`: expected 200, observed 400. A separate run
on unchanged canonical main 7fc303c reproduced it. Twelve warnings include
existing async-mock cleanup warnings; this is not a clean-suite claim.

Ruff lint, Ruff formatting, Pyright on the core and coordinator, and
`git diff --check` passed. Added regressions cover ten simultaneous fake turns
plus an eleventh queued turn, queued Stop cancellation without process launch
or another slot's release, suppression of queue-time stall detection,
finished-card text, invalid capacity, queued API filtering/counts, default
worker admission while ten other sessions are busy, and ambiguous-worker
window accounting. Existing same-thread serialization checks still pass.

Repository-required security review: the patch adds no shell execution,
provider calls, credential reads/logging, unauthenticated write API, or
dependency. The read-only sessions endpoint retains existing middleware.
Model credentials and subprocess construction are unchanged. The staged
systemd command uses a fixed local source path and no user-supplied input.

Live activation and subscription throughput measurement remain deployment
work for the next model. Drew asked to switch models for that guidance after
the reviewed changes were prepared; no further design research is needed.
