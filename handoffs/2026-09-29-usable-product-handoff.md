# Handoff — make EBI + Jester a usable product (September 29, 2026)

Written by the Claude Code session that stabilized and activated both services.
The next session runs in `~/main-projects/ebi-agent-chat-relay` with bypass
permissions. Drew has approved building the fixes; he has NOT yet approved the
specific "Go?" and "Jester brain" choices below — ask him those two first, in one
multiple-choice question, then work without asking for routine steps.

## What Drew wants (his words, condensed)

- **Jester has the same access as Drew** to everything he can do in Discord: see,
  search, read, send, create, stop, close sessions. It must "know everything".
- **Open = visible in Discord.** If he closed/archived a thread, it is closed.
  Right now he sees 2 threads but EBI says 24 are open. He raised this yesterday
  and expected it fixed. This is the main issue.
- **Jester must not cut out, say "Mm-hmm", or stop mid-sentence.**
- He is frustrated: "we are not going in the right direction". Lead with fixes he
  can feel, prove each with his own checks, keep messages short and plain.

## Current live state (verified this morning)

- EBI service `ebi-agent-chat-relay.service` runs main **`1656b6d`** (merge of the
  stabilization candidate). Later commits on main are docs only (handoffs).
  Main is ahead of origin and NOT pushed. Health: `curl localhost:9876/api/health`
  shows `runtime.commit`.
- Jester service `jester-voice.service` runs `~/main-projects/jester-voice` main
  **`1348470`** (fast-forwarded from `f804d99`). `jester-review.service` up.
- Backups before activation: `~/.local/state/ccdb/activation-backup-20260929-083125/`
  (EBI db, loop store, blockers, builds, SHA256SUMS, previous main `8e021d2`;
  `jester/` subfolder with Jester state + previous main `f804d99`).
- Rollback steps (a code-only EBI rollback breaks every session read):
  `docs/activation-checklist-2026-09-29.md` in the EBI repo.
- **Drew's Codex account hit its usage limit until Oct 4, 2026.** Jester's brain
  (Luna, `src/brain.mjs`) and intent proposer run on it, so open conversation and
  natural-wording commands are effectively down. (The limit was hit partly by an
  attempted Codex review — do not use Codex for reviews.)

## Evidence of the problems (read-only, today)

- EBI rows not closed: 24. In Discord: 2 active (📂 podlox — created by Drew
  today, no tag; 🔁 Task loop · jester-voice), 19 archived ⚡ worker threads from
  Sept 28 builds (old code archived them without closing the EBI row), 3 deleted.
  All 10 tag words are held (8 by those ghosts, 2 by closed sessions zoro/nami),
  so podlox got no tag.
- The Task loop thread reappeared because this morning's restart restored its
  finished legacy build as a "looks good" wait and posted "Back after a
  restart…" into the archived thread.
- Jester room transcript
  `~/.local/share/drew-ai-voice-transcripts/runtime/transcripts/a4ee1105-fa9e-47c1-ac2f-4988bb3684c1.md`
  and `~/main-projects/jester-voice/logs/turns.jsonl`: every session question
  ("what threads do we have open?", "do you see podlox?") went to Luna with no
  EBI data → "I can't see that from here". Barge-in fires on the worker's first
  VAD frame (`worker/speech.py:337-343`); 6 barge-ins had empty heard text.
  "Mm-hmm" attributed to Drew appears even before Jester spoke (STT
  hallucination on noise); Jester also opens replies with "Mm-hmm".

## The plan (independently reviewed; claims spot-checked)

Full text: `docs/usable-product-plan-2026-09-29.md` on branch
`fix/harness-round-2-20260928` (worktree
`~/.local/state/ccdb/session-wt/harness-round-2-20260928`, commit `a47fecf`).

1. **Jester (no EBI restart needed)**
   - Model-free answers from `/api/jester/sessions` for "what's open", "do you
     see X", "why no tag", "search for …"; one honest "Luna is out until Oct 4"
     line instead of "say the final task once more".
   - Barge-in only after ~250–300 ms of sustained voice (or STT-confirmed
     non-backchannel words); backchannel-only text (mm-hmm/yeah/ok) is neither a
     stop nor a turn; Jester never opens with "Mm-hmm"; don't inject backchannels
     into brain context (`src/conversation.mjs:206-229,216,305`, `src/brain.mjs:22-29`).
   - Refresh known tags when EBI's list changes (`src/conversation.mjs:120,291`).
2. **EBI follows Discord, both directions**
   - Add `on_raw_thread_update` / `on_raw_thread_delete` listeners plus a startup
     and periodic sweep: archived or deleted ⇒ close with a new "discord
     archived" `CloseAuthority`, release tag; 404 ⇒ mark archive done (no endless
     retry in `reconcile_pending_closes`).
   - A message (typed, or spoken via `/api/threads/{id}/spoken`) in a closed
     thread reopens it first (`claude_chat.py` `_handle_thread_reply`,
     `_run_claude`, `_finish_turn`).
   - Sweep assigns freed tags to untagged open threads (podlox).
   - Never post into archived threads: task-loop `report()`
     (`task_loop.py` ~986-995, ~1211-1217), restart notices,
     `_archive_finished_worker_thread` (archive without close — the ghost maker),
     `handoff_discord.py:212` unarchive.
   - Snapshot: real thread names (not cache-only `_thread_names`), enumerate all
     rows not just `list_all(limit=100)`; open count must equal Drew's sidebar.
   - Tell Drew: Discord auto-archives after 7 days (`thread_policy.py:14`), which
     will now close the session.
3. **Activate**: backup, deploy (merge to main), one restart per service, the
   sweep performs the 22-row cleanup (no hand SQL). Check idle first
   (`/api/claims`, `/api/jester/sessions`, no `claude -p` processes); post a
   lounge notice (`POST /api/lounge {"label","message"}`) before and after.
4. Luna-enriched answers again when Codex resets, or per Drew's brain choice.

## Drew's acceptance checks (show him these results)

- Sidebar thread count == EBI `closed:false` count (should be 2 today).
- Archive a thread by hand → EBI closed, tag freed; unarchive → open again.
- Delete a thread → closed; no repeated retries after restart.
- Restart the bot → no messages posted into archived threads; sidebar unchanged.
- Type in an archived thread → it reappears, open, with a tag.
- podlox title shows `[tag]`.
- Voice with Luna down: "Jester, what's open?" names both; "do you see podlox?"
  answers yes/no; other questions get one "Luna is out" line.
- Long answer + cough / "mm-hmm" → keeps talking; "Jester, stop" stops within ~300 ms.
- Two silent minutes → no "Mm-hmm" in the transcript; Jester never opens with it.
- Live check: `CCDB_LIVE_CHECK_PROFILE=jester JESTER_UNIT=jester-voice.service
  CCDB_BOT_UNIT=ebi-agent-chat-relay.service
  CCDB_BOT_LOG=$HOME/.local/state/ebi-agent-chat-relay/discord-bot.log
  CCDB_LIVE_CHECK_DB=data/sessions.db .venv/bin/python scripts/live-check.py`

## How to work (project rules that matter here)

- Test-first for each defect; `make test-one f=...` while iterating; `make verify`
  (≈9 min, parallel) before each EBI commit. Jester: `node --test`,
  `bench/.venv/bin/python -m pytest -q worker/tests`, `node sim/run.mjs`.
- Develop EBI on a branch/worktree, then merge into main to deploy; Jester
  likewise. Don't push unless Drew asks. Never print secrets (`.env`).
- Two known load-only test flakes (`test_work_copy` fixed since; `TestGoalNotMet`
  fake commit) — rerun before blaming a change.
- Background: `handoffs/2026-09-28-jester-ebi-stabilization.md` (long history),
  EBI docs `docs/boundary-audit-2026-09-29.md`, Jester
  `STABILIZATION-REVIEW-2026-09-28.md`. Read only what you need.

## Two questions to ask Drew first (one multiple-choice message)

1. Start now with Jester fixes first, then EBI? (recommended: yes)
2. Jester's brain until Oct 4: wait (recommended, no cost) / switch to his Claude
   subscription / he buys Codex credits.
