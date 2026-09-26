# Findings — measured, not guessed (2026-09-26)

## Discord rate-limit storm

- `grep -c 429 discord-bot.log` → **6,012**
- Worst minute: **120** rejections (2026-09-26 05:32)
- In that minute: **1** channel, **15** distinct message ids being `PATCH`ed
- `STREAM_EDIT_INTERVAL = 1.5` (streaming_manager.py), `TOOL_TIMER_INTERVAL = 5`
  (tool_timer.py) — both per message
- Discord's edit bucket is ~5 per 5s **per channel**. 15 timers/5s plus the
  stream ≈ 18.5 edits per 5s → ~3.7x over
- Each rejection retries in ~0.9s, so the backoff serialises the whole thread
- `claude_teams/pacer.py` already solves exactly this ("Coalescing per target,
  one update per interval"); the Discord frontend does not use it

## Todoist watchdog

- `ERROR _ccdb_custom_cog_watchdog: Todoist fetch error: [Errno 2] No such file
  or directory: '/home/drewp/.claude/skills/todoist/scripts/todoist.sh'` → **467**
- `ls ~/.claude/skills/todoist` → does not exist
- `examples/ebibot/cogs/watchdog.py` runs a 30-minute loop with
  `_FETCH_ATTEMPTS = 2` and logs an error per attempt, forever

## Worktrees

- `Skipping worktree removal (dirty)` → **531** for `Lockin AI`, **380** for
  `ebi-agent-chat-relay`, **283** for `realpage`, plus others
- Also `cannot remove a locked working tree` (195) and `not a git repository` (187)
- 8 leftover `.worktrees` directories; `GOMER` holds 110 MB of the ~119 MB total

## Not a problem after all

- **Bot log growth** — `RotatingFileHandler` at 10 MB is already configured; the
  9.4 MB file is one rotation away from turning over
- **"Local speech worker exited"** (6 times) — every occurrence is immediately
  followed by a systemd stop/start, so these are the restarts done today, not
  crashes. The one utterance in flight is lost; nothing else
- **"Failed to decrypt a packet"** — Discord's DAVE end-to-end encryption
  resyncing; recovers on its own, no audio loss observed in the transcript

## Fixed today, waiting only on a bot restart

- Closed sessions were still listed in Sessions, and still held a spoken tag —
  **25 of 26 tags** are currently held by closed sessions (`4e6e95d`)
- Context cleanup left the tag on the finished thread and left its session open
  (`5cb4749`)
- Tags are One Piece names, and each answers to the words it is misheard as
  (`9f63acf`)
- "Close everything I'm not using" — already live, voice-side only (`c574e7f`)

## Root cause of today's "voice is broken"

The deployed speech worker was an **older copy** that still passed the NATO tag
list to the decoder as `initial_prompt`. The bias was strong enough that room
tone came back *as* tag words, and it also inserted "bravo" into the front of a
real sentence — which is why an instruction reached an abandoned thread and why
"yankee zulu" appears 12 times in the transcript. Drew never said either. The fix
existed in the repo, unshipped. Deployed and restarted; 18 noise clips since with
0 invented tags.
