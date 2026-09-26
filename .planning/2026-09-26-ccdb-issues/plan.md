# Plan — the things actually going wrong in ccdb (2026-09-26)

Check: `make verify`
Try: `systemctl --user status ebi-agent-chat-relay.service drew-ai-voice-transcripts.service`
Open: (no local web UI — this is a Discord bot)

Every task below is one outcome one fresh session can finish and prove.
Evidence for each is in `findings.md` next to this file.

## Tasks

- [ ] **Restart the bot to land the three finished fixes.** They are committed and
      the suite passes, but the bot only reads code on restart. After it: closed
      sessions leave the Sessions list, closed sessions give up their spoken tag
      (25 of the 26 are currently held by dead sessions), context cleanup hands
      the tag to the new thread and closes the old session, and every tag becomes
      a One Piece name. Verify by opening Sessions and reading one thread title.

- [ ] **Confirm the duplicate tag is gone after the restart.** Two threads showed
      "[bravo]" because a title is a second copy of the tag and nothing removed it:
      the updater only ever *added* a tag, and Discord refuses to rename an
      archived thread, so a closed thread's tag could not be cleaned at all. Fixed
      in `2c11ca8` — the tag comes off the title before the archive lands, a
      thread that lost its word has it stripped, and an uncached thread is fetched
      instead of skipped. Verify: no two threads show the same word.

- [ ] **Stop the Discord rate-limit storm.** 6,012 `429`s in the log, up to 120 in
      a single minute, all `PATCH .../messages/...` in one thread. Cause: each
      message paces its own edits (stream 1.5s, each tool timer 5s) but Discord
      meters edits *per channel*. Fifteen messages editing in one thread is about
      3.7x over budget, and every rejection costs ~0.9s of backoff, which is why
      long answers crawl. Fix: one shared edit budget per thread that coalesces
      all pending edits — the pattern already exists in `claude_teams/pacer.py`
      and Discord does not use it. Verify: a long streaming answer produces no
      `429` lines in the log.

- [ ] **Make the Todoist watchdog disable itself when its script is missing.**
      467 `ERROR Todoist fetch error: No such file or directory`, because
      `~/.claude/skills/todoist/scripts/todoist.sh` does not exist on this
      machine. A cog whose dependency is absent should say so once and stop, not
      log an error every 30 minutes forever. Verify: restart and see exactly one
      line about it.

- [ ] **Stop the worktree warnings, and add a way to tidy them.** 531+ `Skipping
      worktree removal (dirty)` warnings and 8 leftover `.worktrees` directories
      (~119 MB, mostly `GOMER`). Refusing to delete dirty work is correct; saying
      so on every single turn is not. Fix: report an un-removable worktree once
      per worktree, and add a command that lists them with their size so they can
      be cleared deliberately. Verify: the count stops growing, and the command
      lists all 8.

- [ ] **Tell Drew when audio arrived but produced no words.** Right now silence
      and "heard nothing" look identical, so he repeats himself not knowing why.
      Fix: when a clip is captured but the transcript is empty or fails the
      confidence floor, post one quiet line in the transcript channel. Verify:
      cough into the mic and see the line.

- [ ] **Decide and build the end-of-speech behaviour.** Needs Drew's answer
      first (see the open question below). Measured from 8,501 real utterances:
      he names a tag in 0.3% of them, median gap between utterances is 3.2s, and
      80% of gaps are under 12.7s — so "he has finished" is about 12 seconds, not
      the 4 currently used, and one thought is being chopped into several turns.
      Node-only, so companion restart only. Verify: one long spoken thought
      arrives as one Discord message.

- [ ] **Put today's work on GitHub as one pull request.** Nine local commits on
      `main`, which is protected — so push a branch and open a PR. Only after
      Drew has tried the restarted bot himself.

## Open question that blocks the last-but-one task

How should the system decide he has finished talking? Recommended: a tag sets the
target indefinitely, utterances accumulate, and the whole thing is sent as one
prompt about 12 seconds after he genuinely stops. The cost he must accept is that
everything said in that room goes to that thread until he says "stop listening"
or names another tag.

## How to try it

1. Open **Sessions** in Discord — the list should show only live sessions, not
   every session ever closed.
2. Look at any thread title in the sidebar — it should read `[luffy] 📂 repo`
   rather than `[alpha]`.
3. Say "close everything I'm not using" in the voice room — the sidebar should
   empty out except for whatever is running.
