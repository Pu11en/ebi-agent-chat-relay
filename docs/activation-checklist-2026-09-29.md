# Activation dry run and rollback checklist — September 29, 2026

Nothing in this document has been applied. It is the reviewable plan that task
7.1 asks Drew to approve (or change) before any restart, deployment or live-row
change. Values were read read-only at about 01:40–01:50 CDT on September 29.

## Live facts (read-only)

- Services: `ebi-agent-chat-relay.service` and `jester-voice.service` active.
  Claims empty; lounge quiet since the September 28 evening handoff.
- Live EBI code is main (`48f67d5` + handoff-only commits). Its health has no
  runtime identity, so the running revision can only be inferred.
- Session store: 363 rows, 24 not closed; no duplicate tags; all 10 words held,
  16 open sessions untagged. Two **closed** sessions still hold words (below).
- Saved Go Work loops (`~/.local/state/ccdb/gowork-loops.json`): two legacy
  records, neither with a saved wait or the `checkpoints` marker.

## Restart hazard on the CURRENT live code — do not restart main as-is

Main's `resume_all` has no closed-session check. On any restart it would resume
build `thread-1554146845415055445` — its session was closed by Drew
(`direct_interaction`, 2026-09-28 14:46:45) and its manifest plan has 21 open
tasks — and dispatch paid workers (the REL-03 pattern). It would also re-enter
`thread-1554145503506333736` (plain plan, 5/5 ticked), whose wrap-up re-runs a
checker session and a lessons AI call. If a restart of main is ever needed before
activation, first move `gowork-loops.json` aside (keep the copy).

## Predicted candidate startup for the saved loops (no manual change needed)

| Record | Evidence | Candidate behavior |
| --- | --- | --- |
| `thread-1554146845415055445` | session closed by a person; manifest plan, 21 open | Not resumed; record removed from the loop store; copy and branch kept. No message, no worker. |
| `thread-1554145503506333736` | session open; plain plan 5/5 ticked; legacy record | Restored as a verdict wait: "finished and waiting for your **looks good** (its checks were not run again)". Zero model/check calls until Drew replies. |

## Proposed record changes (dry run — each needs explicit approval)

| # | Target | Prior value | Proposed | Evidence / authority | Idle check at apply time |
| --- | --- | --- | --- | --- | --- |
| 1 | setting `voice_label:1553779983158349925` | `zoro` | delete | row closed by `direct_interaction` 2026-09-28 19:52:46 | row still closed; no active turn |
| 2 | setting `voice_label:1553899450227757156` | `nami` | delete | row closed by `direct_interaction` 2026-09-28 19:54:37 | row still closed; no active turn |
| 3 | session `1553779983158349925` (REL-01) | `session_id=01a0e5ff-…`, `backend=claude` | none now | closed by Drew; do not reopen or rebind without his decision | — |

Items 1–2 are optional: the candidate allocator releases closed holders itself on
its next allocation, so activation alone should free both words; the live check
will show whether it did. Item 3 would only matter if Drew reopens that thread; the
verified Claude conversation is `70615534-f45b-420b-b2f7-4ec8e56fa9d5`.

Excluded (ambiguous, not cleanup targets): the open Go Work worker rows from
REL-02. Only rows with ledger acceptance (commit + checks) may be closed, and the
candidate does that itself for builds it runs; historical rows need per-row
evidence first. No close-by-directory or bulk close.

## Decisions Drew should make explicitly before activation

- Accept `17c65de`'s restart/upgrade resume wording: resumed turns continue the
  already-authorized task without asking the user to reconfirm (the old wording
  asked for reconfirmation before any code change, commit or PR).
- Whether to release the two closed holders' tags by hand (items 1–2) or let the
  candidate allocator do it.

## Activation steps (for an approved idle window)

1. Recheck idle: `/api/claims` empty, lounge quiet, no running turns
   (`/api/jester/turns`), Drew not in the voice room. Never use `/api/sessions`.
2. Back up, with the bot stopped: `data/sessions.db` (+ `-wal`/`-shm`),
   `~/.local/state/ccdb/gowork-loops.json`, `~/.local/state/ccdb/builds/`,
   `gowork-blockers.json`. Record checksums.
3. Deploy the reviewed candidate revision (inspect `scripts/pre-start.sh` and the
   deploy scripts first; do not assume `make dev-on` targets this host's unit).
4. Start; confirm `/api/health` `runtime.commit` equals the deployed revision and
   `running_matches_disk` is true.
5. Run `CCDB_LIVE_CHECK_PROFILE=jester JESTER_UNIT=jester-voice.service
   CCDB_BOT_UNIT=ebi-agent-chat-relay.service CCDB_LIVE_CHECK_DB=<live db>
   uv run python scripts/live-check.py`; expect the two closed holders released
   after the first allocation, and no FAIL.
6. Watch the log for the two loop decisions above and for any unexpected launch.
   Jester's candidate (`e44cedd`) is deployed separately, after its own review.

## Corrections from the 2026-09-29 dry run (step A4) — these amend the steps above

Read these with the steps; where they differ, the correction wins.

### The restart window

- **Stop the health timer first, restart it last.**
  `ebi-agent-chat-relay-health.timer` runs `scripts/health-check.sh` every two
  minutes; the script retries `/api/health` three times five seconds apart and
  then runs `systemctl --user restart ebi-agent-chat-relay.service`. So the bot
  comes back ~15 s into any stop, on the old code, in the middle of the merge.
  Before the window: `systemctl --user stop ebi-agent-chat-relay-health.timer`.
  After the new bot is healthy: `systemctl --user start
  ebi-agent-chat-relay-health.timer`, and confirm with `systemctl --user
  list-timers ebi-agent-chat-relay-health.timer`.
- **Merge with the bot stopped.** The live checkout is an editable install: the
  running process imports from the working tree, so a merge under a running bot
  changes the code a live turn is executing.
- **Run pre-start's import gate by hand in the live checkout before starting**,
  so a bad merge is found while the bot is still stopped and never reaches the
  unit's rollback path:
  `~/.local/bin/uv sync --extra voice --extra deepseek && .venv/bin/python -c
  "from claude_discord.main import main"`.
- Rollback steps are unchanged (see the section below); the health timer stays
  stopped until the rollback bot is healthy, too.

### The idle gate (all four must hold before stopping)

1. No agent process under the bot: `pid=$(systemctl --user show -p MainPID
   --value ebi-agent-chat-relay.service); pgrep -P "$pid"` prints nothing (an
   agent turn is a child CLI process of the bot).
2. `GET /api/claims` returns `{"claims": []}`.
3. No running rows in the read-only snapshot: every row of
   `GET /api/jester/sessions` has a `state` other than `running`.
4. Lounge quiet: `GET /api/lounge` shows nothing newer than the last handoff.

`/api/jester/turns` needs a zoned cursor: `GET /api/jester/turns?since=$(date -u
-d '-15 minutes' +%FT%TZ)` (a bare call answers 400 "invalid turn cursor"). It
is a cross-check of the turn journal, not a substitute for 1–3.

### Baseline for "the restart posted nothing into an archived thread"

Before stopping, record every open-row thread's last message and archived
flag; after the new bot is up, record them again and diff. An archived thread
whose `last_message_id` changed was posted into; the restart must not do that.

```bash
cd /home/drewp/main-projects/ebi-agent-chat-relay
token=$(grep -E '^DISCORD_BOT_TOKEN=' .env | cut -d'=' -f2- | tr -d '"')   # never echo it
ids=$(curl -s -H "Authorization: Bearer $CCDB_API_SECRET" http://127.0.0.1:9876/api/jester/sessions \
  | python3 -c 'import json,sys; print(" ".join(r["thread_id"] for r in json.load(sys.stdin)["sessions"]))')
for id in $ids; do
  # printf is a shell builtin and -H @- reads the header from stdin, so the
  # token never appears in curl's argv (visible to anything listing processes).
  printf 'Authorization: Bot %s\n' "$token" | curl -s -H @- "https://discord.com/api/v10/channels/$id" \
    | python3 -c 'import json,sys; c=json.load(sys.stdin); print(c.get("id"), c.get("last_message_id"), c.get("thread_metadata",{}).get("archived"))'
done | sort > "$HOME/.local/state/ccdb/thread-baseline-before.txt"
```

Run the same loop into `thread-baseline-after.txt` once the new bot answers
`/api/health`, over the SAME ids — `ids=$(cut -d' ' -f1
"$HOME/.local/state/ccdb/thread-baseline-before.txt")` instead of a fresh
snapshot, because the new bot's sweep closes the rows of archived threads and
they would drop out of a new list, hiding exactly the posts this looks for —
then `diff` the two files. Expect: no change in `last_message_id` on any line
whose third column is `True`; the only other differences allowed are threads
the owner typed in.

### The new proof and check tooling

- `scripts/live-check.py` now fails when an open row is not visible in Discord,
  when `open_count` differs from `discord_active_threads`, when any script under
  `scripts/` names the allocating session listing, and (jester profile) when
  Jester opened a turn with a backchannel since its unit started
  (`JESTER_TURN_LOG`, default `~/main-projects/jester-voice/logs/turns.jsonl`).
  Against the pre-activation bot the two Discord rules show SKIP (older bot);
  after activation they must show PASS.
- `scripts/discord-thread-proof.py` proves archive → closed + tag released,
  unarchive → open, delete → closed, with one scratch thread it always deletes.
  It refuses to run without `CCDB_PROOF_CONFIRM=yes`, reads the bot token from
  the live `.env` and never prints it. Run it only after activation (the old
  bot does not follow Discord, so its archived stage would just time out):
  `cd /home/drewp/main-projects/ebi-agent-chat-relay && CCDB_PROOF_CONFIRM=yes
  CCDB_API_SECRET=… .venv/bin/python scripts/discord-thread-proof.py`.
- `scripts/deferred-restart.sh` now waits on `/api/jester/sessions` (read-only)
  instead of the allocating listing; its behaviour is otherwise unchanged.

## Rollback (tested offline on a temporary copy)

The candidate adds `sessions.lifecycle_version` and `sessions.archive_pending`.
Main's code reads rows with `SessionRecord(**dict(row))`, so **a code-only
rollback fails every session read** (`unexpected keyword argument
'lifecycle_version'` — reproduced against a temporary database). Rollback is:

1. Stop the bot. Keep a copy of the current database.
2. Either restore the pre-activation backup (loses changes since activation), or
   drop the two columns with Python's sqlite3 (SQLite 3.45.1 here):
   `ALTER TABLE sessions DROP COLUMN archive_pending;`
   `ALTER TABLE sessions DROP COLUMN lifecycle_version;` — verified offline that
   main's repository then reads the row again.
3. Restore `gowork-loops.json` from the backup: older code treats records with
   `waiting_*`, `landed_workers` or `checkpoints` as malformed and drops them on
   its next save. Then apply the move-aside rule from the restart hazard above.
4. Check out the previous revision and start; confirm health and the live check.
