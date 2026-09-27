# Handoff — voice control for ccdb (2026-09-26)

Thread tag: `bravo` · repo: `/home/drewp/main-projects/ebi-agent-chat-relay` · Drew's user id: `488763953397235712`

## Goal

Drew wants to run his coding agents **by talking**, not typing. He sits in a Discord
voice room and speaks; sessions get created, steered, reconfigured and closed from
his voice alone. He reads the replies as text — **he has explicitly and repeatedly
ruled out spoken output (TTS). Never build it.**

The longer-term thing he was describing when this session ended: plan a large build
out loud, then leave it running on a loop for days while he is away, coming back to
finished work. That is not built and not designed yet.

## Where things stand

**Working and live** (bot `ebi-agent-chat-relay.service` + companion
`drew-ai-voice-transcripts.service`, both `active`):

- Every visible thread gets a NATO-alphabet tag (`alpha`, `bravo`…) shown in its
  Discord title as `[bravo] 📂 repo`. Tags are stored, stable, reclaimed only when
  all 26 are in use (oldest absent thread first). A thread spawned by voice is
  tagged immediately, not on the next poll.
- **Talk to a thread:** "bravo, run make verify". Tag anywhere near the front of the
  sentence; everything after it is the instruction.
- **Conversation mode:** after a delivered instruction that thread keeps receiving
  what you say for 90s, each sentence resetting the clock. "stop listening" ends it;
  naming another tag switches thread. Owner-only; acknowledgement noise ("okay",
  "thank you") refreshes the window without being forwarded.
- **Open a session:** "make a new session in the aldus folder and do the design
  work" — no tag needed. Folder matched phonetically (see Decisions). No match →
  opens in `/home/drewp/main-projects` anyway and tells the new session what was
  heard.
- **Close:** "hotel, close this session" — works via ccdb's existing close hint; the
  session closes itself.
- **Model/backend:** "bravo, switch to opus" · "use codex" · "move this to ollama" ·
  "what model are you on?" Handled in the voice layer without waking the session.
- Speech: `small.en` faster-whisper, local, offline. 4s of silence ends an utterance,
  30s ceiling, both env-tunable (`VOICE_SILENCE_MS`, `VOICE_MAX_UTTERANCE_SECONDS`).

**Merged:** PR #28 on `main` (squashed, `b2cd51d`). CI green on 3.12 and 3.13.

**Committed locally but NOT yet pushed** (four commits on `main` after the merge —
they need a follow-up PR; `main` is a protected branch, direct push is rejected):

1. looser tag-address rule (noun-maker blocklist)
2. conversation mode (the 90s window)
3. short-clip transcription fix
4. (plus the README updates that went with them)

**Nothing is broken.** Every "it's not working" today turned out to be one of:
the bot not restarted yet, the tag absent from that utterance, or the bug in item 3.

**Known red test, pre-existing, unrelated:**
`tests/test_deploy_recovery.py::test_runtime_hook_loads_fallback_then_returns_to_main`
(fails locally, passes in CI — documented in CLAUDE.md).

**Open question Drew was answering when the session ended.** I analysed all 8,501
stored utterances and recommended a setup; he had not yet picked. The evidence:

- He says a tag in **22 of 8,501** utterances (0.3%). Requiring one per utterance
  fights how he talks.
- Median utterance **4.5s / 11 words**; median gap between utterances **3.2s**; 76%
  of gaps under 10s; 80th percentile 12.7s. So "he has finished" is ~**12s**, not 4.
- Only 6 utterances ever hit the 30s ceiling, so that cap is fine.

Recommended (option A, unanswered): **sticky thread + collect-before-sending** — a
tag sets the target indefinitely, utterances accumulate, and the whole thing is sent
as one prompt ~12s after he genuinely stops. One thought, one message, one turn.
The trade-off he must accept: everything said in that room goes to the thread until
he says "stop listening" or names another. Variants offered were B) sticky but send
each burst immediately, C) collect but keep the 90s window, D) sticky with a
10-minute idle timeout.

## Decisions

- **A Discord thread title is not a usable handle.** "📂 ebi-agent-chat-relay" is
  five words aloud and one of them ("chat") is a word the grammar uses to end a
  name. Hence tags, matched exactly, winning outright over any name matching.
- **Tags must never change meaning under him.** A tag is a word he learned; it is
  held while its thread is out of view and only reclaimed when the pool is
  exhausted.
- **Tags live in the thread title, not only a roster message.** A roster answers
  "which tag is that thread?"; looking at the sidebar he needs "what do I say to
  *this* one?".
- **Never require a sentence template.** "Okay and alpha say that we need a repo"
  must work. The tag is a wake word; the guard is on the word *immediately before*
  it (block articles, prepositions, naming verbs — "the delta", "call it bravo"),
  not on an allowlist of filler, which failed against real speech.
- **Folders are matched on sound, not spelling.** The recogniser writes "oldest" for
  "aldus" and always will. Drop vowels, fold confusable consonants, compare with a
  small edit allowance. Deliberately not soundex (its first letter is what went
  wrong). Two guards came from testing against the real catalog: strip articles
  first, and shrink the allowance with word length.
- **`/api/threads/{id}/spoken`, not the agent relay endpoint.** The relay stamps
  every message "NOT from your human" and rate-limits one per thread pair per
  minute — correct between agents, wrong for a person mid-sentence.
- **Voice may do local work freely but not publish.** Plan/read/edit/run/test/commit
  on his word alone; push, deploy, delete-remote or spend money wait for a typed OK.
  Drew once said "it can push on its own" but was unsure; the restriction was kept
  because his own standing rule (`~/AGENTS.md`) is that nothing reaches GitHub until
  he has tried it. **He can overrule this; ask before removing it.**
- **Model/backend changes never wake the session.** The moment this matters most is
  when a thread's plan has run out, and such a thread cannot be asked anything.
- **Model names are free text; only aliases are recognised by voice.** Key Design
  Decision 11 — model names are discovered, not hardcoded.
- **The leaked bot token is NOT being rotated.** Drew pasted the live
  `DISCORD_BOT_TOKEN` into Discord, was told once, declined. Do not raise it again.
- **A planning question is not permission to build.** He asked what a "jester"
  keyword *could* do and got a spawned session; it had to be closed. Answer
  questions; build on imperatives.

## Update — the context handoff was losing the tag (2026-09-26, later)

Cleaning up context opened a new thread and left the spoken tag on the dead one,
so saying `bravo` reached a finished session while the live one answered to a tag
nobody had been told (`zulu`). It also left the old session open, to be closed by
hand. Both are fixed in `5cb4749` — `ContextNudger.hand_off` now moves the tag to
the continuation thread, retitles it immediately, strips the tag off the finished
thread and calls `close_session` on it. A handoff whose file was never written
still changes nothing. The settings key (`voice_label:<id>`) moved to
`voice_labels.py` as `label_key`/`thread_id_from_key`, since two modules now use it.

**Not live yet** — this is under `claude_discord/`, so it needs a bot restart, which
is Drew's call. As a stopgap the `bravo` row was moved in `data/sessions.db` by hand,
so this thread answers to `bravo` right now. The old archived thread still *shows*
`[bravo]` in its title because Discord will not rename an archived thread; it is
closed and no longer routes.

## Next steps

1. **Ask Drew to pick the end-of-speech setup** (A–D above; A is recommended). This
   is the one open decision and everything else is stable without it.
2. Implement whichever he picks, in
   `extensions/voice_transcripts/src/control/controller.mjs` (sticky target replaces
   the `attached` window) and, for collect-before-send, a buffer keyed on silence
   duration. Node-only → **companion restart only, no bot restart.**
3. Open a follow-up PR for the four unpushed commits (`main` is protected; push a
   branch and `gh pr create`).
4. Still unbuilt and asked for twice: **"close everything I'm not using"** — one
   sentence archiving every idle thread, leaving running ones alone.
5. Also unbuilt: telling him when audio arrived but produced no words, so silence is
   never ambiguous.
6. Then: the loop/planning project he was describing (plan a big build out loud,
   leave it running for days). Undesigned. Ask him before touching it.

**Before changing any voice grammar, read what the recogniser actually produced** —
`sqlite3 ~/.local/share/drew-ai-voice-transcripts/runtime/transcripts.sqlite
"SELECT captured_at, text FROM segments ORDER BY id DESC LIMIT 20"`. Every real bug
today was found that way; none by a test.

**Restarts:** a Node-only change needs only
`systemctl --user restart drew-ai-voice-transcripts.service`. Anything under
`claude_discord/` needs the bot restarted too, which **kills the current session
mid-turn** — use `/home/drewp/.local/state/ccdb/voice-golive-restart.sh` via
`systemd-run --user` after `POST /api/mark-resume`, and announce in the AI Lounge
first. Bot restarts are Drew's call.

## Key files

- `/home/drewp/main-projects/ebi-agent-chat-relay/extensions/voice_transcripts/README.md`
  — the whole feature in prose; read this first.
- `.../extensions/voice_transcripts/src/control/` — `command.mjs` (grammar and the
  tag wake word), `targets.mjs` (tag/name resolution), `folders.mjs` (phonetic
  folder matching), `controller.mjs` (the whole decision flow), `api.mjs`,
  `runtime.mjs`, `roster.mjs`
- `.../extensions/voice_transcripts/src/config.mjs` — the timing numbers
- `.../extensions/voice_transcripts/src/voice/faster_whisper_worker.py` — the
  short-clip VAD fix
- `/home/drewp/main-projects/ebi-agent-chat-relay/claude_discord/spoken.py` — the
  prompt an utterance arrives as, including the bounded-authority paragraph
- `/home/drewp/main-projects/ebi-agent-chat-relay/claude_discord/voice_labels.py` —
  tag assignment and the title prefix
- `/home/drewp/main-projects/ebi-agent-chat-relay/claude_discord/ext/api_server.py` —
  `deliver_spoken_message`, `set_thread_runtime`, `_apply_voice_labels`
- `/home/drewp/.local/share/drew-ai-voice-transcripts/voice.env` — live config
  (guild/channel ids, `VOICE_CONTROL_ENABLED`, `CCDB_API_URL`, model, timings)
- `/home/drewp/main-projects/drew-ai-voice-runtime` — the checkout the companion
  actually runs from, on branch `deploy/drew-ai-voice-transcripts`. Deploy with
  `git checkout main -- extensions/voice_transcripts` there, then commit.
- `/home/drewp/.claude/projects/-home-drewp-main-projects-ebi-agent-chat-relay/memory/MEMORY.md`
  — standing preferences, including no-TTS and not-rotating-the-token.
