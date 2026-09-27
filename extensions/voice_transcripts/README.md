# Manual voice transcripts for an existing ccdb bot

This optional instance extension uses the existing Discord bot account to transcribe
one designated voice room when its configured owner explicitly requests it. It reuses the
Hanoi Community transcription modules; `SOURCE.json` records their original hashes.
No new Discord application, token, or invite is needed.

Auto-join is off. Enter the room, then send **`!voice join`** in the configured
voice-transcripts channel to start; send **`!voice leave`** there to disconnect
immediately. Only the configured owner can use these exact text commands, and
`!voice` shows help. Joining the room or restarting the worker never starts recording.
Everyone speaking gets separate speaker attribution. Leaving the room stops
recording; returning requires another join command. Anyone in the room can use the
notice's **Pause transcription** button; old Resume buttons only explain the command.
Muting/unmuting does not start another session. Leave preserves already captured
speech and does not cancel agent work already sent from that speech.

The bot joins muted and undeafened. Recognition runs locally with faster-whisper,
using an already downloaded `base.en` model, with no transcription API calls. One
Markdown attachment in the private transcript channel is updated while recording
and as remaining audio finishes after stopping. Speaker timestamps are UTC.

## Integration

ccdb's chat service remains its own process. A small Node voice companion connects
using the **same identity**, subscribing to guild, voice-state and message events.
It only handles owner `!voice` commands in the configured transcript channel and
never registers/overwrites slash commands or routes those commands to an AI. The
DAVE-capable `@discordjs/voice` stack is the one used by the Hanoi implementation.
Run exactly one voice companion for a given bot and guild; the service uses `flock`
to prevent duplicate local processes. No Python framework changes are required.

The normal launcher reads only `DISCORD_BOT_TOKEN` and `DISCORD_OWNER_ID` from the
existing bridge env file. The Python recognition subprocess receives an allowlist
of non-secret environment variables; it does not receive the bot token. Voice room
IDs and the separate transcript output are validated at startup and before use.
The owner and room checks are repeated during capture. Disclosure failure blocks
recording. Everyone mentions and mentions from transcript text are disabled.

## Setup

1. Use Node 22.12+ and run `npm ci --omit=dev` here. Install `requirements.txt` into
   a Python environment and point `VOICE_PYTHON` at its absolute executable path.
2. Make the faster-whisper model available in that user's Hugging Face cache before
   starting. Runtime model loading uses `HF_HUB_OFFLINE=1`.
3. Create a clearly labeled voice room and a private text transcript channel. The
   bot needs View Channel, Connect, and Send Messages in the voice room; and View
   Channel, Send Messages, Read Message History, and Attach Files in the output.
   The owner needs access to the output. Discord administrators can still access
   private channels through their administrator permission. Enable the application's
   Message Content intent (also used by ccdb chat) for the plain-text commands.
4. Copy `.env.example` to a private file and fill the IDs and absolute data path.
   Set `VOICE_BRIDGE_ENV_FILE` to the existing bridge env file and
   `VOICE_CONFIG_FILE` to the new private voice config. Both must be absolute paths.
5. Deploy the code to a permanent checkout, such as
   `~/main-projects/drew-ai-voice-runtime` on `deploy/drew-ai-voice-transcripts`.
   Never run the service from a `session/<thread-id>` worktree: ccdb removes clean
   session worktrees at turn completion, including their ignored runtime files.
   Keep the config, SQLite, transcripts, and lock under
   `~/.local/share/drew-ai-voice-transcripts/`, outside the source checkout.
   `ops/voice-transcripts.service` provides the matching supervised launcher;
   adjust the Node and bridge paths for a different installation. Use a private
   data directory, umask 0077, and an automatic failure restart.

`ops/create-channels.mjs` is an explicit provisioning helper, never invoked by the
runtime. It creates the room, a transcript channel accessible to the owner and bot,
and a visible room disclosure. It writes a receipt after each channel creation,
and refuses to create duplicate named rooms. Set `VOICE_GUILD_ID`,
`VOICE_CATEGORY_ID`, `VOICE_SETUP_OUTPUT` (absolute receipt path), and
`VOICE_BRIDGE_ENV_FILE` before invoking it. Inspect the receipt if provisioning
stops partway through. Discord voice-channel messages cannot be pinned.

## Spoken commands (optional, off by default)

Transcription only listens. With `VOICE_CONTROL_ENABLED=true`, `CCDB_API_URL`
and (if the control plane has one) `CCDB_API_SECRET` in the voice config file,
the owner can also steer a session without leaving the room:

- **"Make a new session in the aldus folder and <what to do>."** No tag — there
  is no thread yet, and nobody says that phrase in conversation. The folder is
  matched on how it *sounds*, because the recogniser writes "oldest" for "aldus"
  and no amount of clearer speech fixes a word outside its vocabulary. If nothing
  matches, a session is opened in the projects root anyway and told what was
  heard, so it can be corrected by talking to its tag instead of repeating the
  whole request.
- **Just say the tag, then talk.** "Alpha, run the tests" · "okay and bravo,
  check DKIM" · "hey charlie can you push that". The tag names the thread and
  everything after it is the instruction — no sentence template to remember. A
  tag only counts as an address when nothing but throat-clearing precedes it
  ("okay", "and", "hey", "uh"…), so "the delta between the two runs was small"
  is left alone even though `delta` is a tag.
- **"Bravo, switch to opus" · "use codex" · "move this to ollama" · "what model
  are you on?"** Choosing the agent is not asking the thread to do anything, so
  it happens without waking the session — which matters precisely when it
  matters: a thread whose plan has run out cannot be asked to change its own
  model. Model *aliases* (`opus`, `sonnet`, `haiku`, `fable`) and backend names
  are recognised, with the spellings a transcript actually produces ("oh pus",
  "code x", "deep seek"); a version string is never guessed into a setting. The
  change applies from the thread's next turn and is written through the same
  store `/backend` and `/model` use, so voice and Discord cannot disagree.
- **Say the tag.** Every visible thread gets one word — a One Piece character
  (`luffy`, `zoro`, `nami`…) — assigned by ccdb, shown at the front of the
  Discord title and listed in a single self-updating message in the transcript
  channel. "Zoro, check DKIM" is exact: a tag is a handle, compared letter for
  letter, so two similar folder names can never be confused for one another. Tags are stable for as long as the thread
  stays visible and are kept when it scrolls out of view and handed back if it
  returns; only when all ten are spoken for does the oldest absent thread give
  one up. Ten, not twenty-six: twenty-six matched an alphabet rather than the
  number of conversations open at once, and the words in play should be familiar
  ones. Past ten live threads the rest go untagged, which only became affordable
  once closed sessions stopped holding tags. A tag is a word you learned, so it must not change meaning underneath
  you — and when a conversation continues in a fresh thread (the context
  handoff), the tag follows it there rather than staying on the finished one.

  Two constraints decide which words may be in the pool, and they are enforced
  by tests rather than by care: no two tags share their first two letters, and
  no tag is a word that turns up in ordinary speech. `law`, `ace`, `brook` and
  `smoker` are all One Piece characters and all disqualified for the second
  reason — a tag that occurs in conversation addresses a thread by accident.

  The recogniser writes what it knows, so a character name comes back as an
  English one ("Luffy" → "Lucy"). Each tag therefore carries the substitutions
  actually seen for it, ccdb ships them with the session as
  `voice_label_aliases`, and they are compared **exactly**. Not fuzzily: the
  consonant skeleton of a four-letter name is two characters long, so a fuzzy
  tag match would route `nami` and `kaido` to each other. The list grows from
  what the transcript log shows, not from guesses — read it with the query at
  the bottom of this file and add the word that actually came out.

  The tag list is **never offered to the decoder as a hint.** It was, and the
  bias was severe enough that half a second of room tone came back as `yankee
  zulu` — an invented tag addresses a real thread. Helping it recognise a word
  is not worth teaching it to invent one; low-confidence segments are dropped
  instead (`no_speech_prob` / `avg_logprob` floors in
  `src/voice/faster_whisper_worker.py`).
- **"Close everything I'm not using."** One sentence archives every finished
  session, because every spoken instruction opened a thread and nothing ever
  closed one. Two things are never touched, and they are the two a sentence
  cannot know: a thread with a turn in flight, and the thread currently being
  talked to. One unreachable thread does not stop the sweep — a thread Discord
  has already lost needs no closing. This is the only spoken command that acts
  on threads it was not addressed from, so the phrase is anchored to the start of
  the sentence and needs both halves: talking *about* the idea ("I should close
  everything I'm not using at some point") must not carry it out.
- **One tag, one run, one message.** Say a tag and it starts collecting. Keep
  talking: every pause under **ten seconds** resets the clock and nothing is sent.
  Ten seconds of silence and the whole thing arrives as **one** message on that
  thread. Then the aim is released — the next run needs its own tag, and **with no
  tag nothing is sent at all.**

  That last part is the design, not a limitation. The recogniser drops words: on
  2026-09-27 "Luffy" never reached the transcript, and under the old
  keep-listening window every sentence after it flowed silently into the previous
  thread — `nami` swallowed the lot and `luffy` got nothing. With an unreliable
  recogniser, "nothing happened" is information he can act on and "it went
  somewhere you did not choose" is not, so silence is the failure mode.

  Two details carry it. A **later tag inside a run is just a word**: the first tag
  owns the run, so naming another thread mid-sentence can never redirect what is
  being said. And the silence is measured from **when he stopped speaking**
  (`captured_at + duration_ms`), never from when the text arrived — transcription
  lags up to 30s here, and timing off arrival would count that lag as a pause and
  cut him off mid-thought.

  Recogniser noise ("Thank you.", "Okay.") is dropped rather than added, and does
  not reset the clock — counting it as speech would hold a run open forever.

  There is deliberately **no way to cancel a run**. "Stop listening" existed for
  the ninety-second window, where being stuck on the wrong thread was expensive; a
  run lasts ten seconds, so letting it send and correcting in the next one is
  fewer things to remember than a phrase which has to be *recognised correctly*
  to work at all.

  A run is **one line** in the transcript channel. It posts "Listening for X" the
  moment the tag lands, so the tag is confirmed without waiting ten seconds, and
  rewrites that same line into what was actually sent. A surface that cannot edit
  gets two lines instead — the confirmation matters more than the tidiness.

- **Say the tag once, then just keep talking.** A delivered instruction leaves
  that thread listening for 90 seconds, and every further sentence resets the
  clock — so thinking out loud reaches one thread instead of needing the name in
  every breath. Naming another tag switches thread, "stop listening" ends it, and
  going quiet closes it on its own. Acknowledgement noise ("okay", "thank you" —
  what the recogniser emits for near-silence) is not forwarded but does keep the
  conversation open, and nobody else in the room can be forwarded at all.
- **If you pause mid-sentence, the thread is held.** Speech is captured per
  pause, so naming a thread and then saying what to do arrives as two
  utterances. Naming one on its own simply starts a run with nothing in it yet;
  the next thing you say joins the same message.
- **There is no second form.** "Put this in the ebi agent chat relay thread, run
  make verify" used to work, and with it came a parser that offered every place
  the name might end and a resolver that scored the readings against the sessions
  that exist — matching folder names by sound, breaking ties on recency, and
  reporting "that was ambiguous, say its tag instead". All of it is gone
  (2026-09-27), about 500 lines with the tests.

  It was removed for simplicity, and it cost nothing: every thread is tagged the
  moment it is created, and a tag is compared **exactly**. The scoring could only
  ever add ways to be misunderstood. "Where does the name end and the instruction
  begin" is not a question anyone has to answer now, and "nothing matched" became
  unreachable — a word only parses as a tag if it already belongs to a live
  session, so the lookup that follows cannot fail.
- Every send is confirmed in the transcript channel with the thread it went to
  and the instruction as transcribed, so a misheard prompt is visible
  immediately. Failures are reported there too.

**A spoken instruction carries the same authority as a typed one** — including
push, deploy, publish and release. It did not until 2026-09-27: those waited for a
typed confirmation, on the reasoning that a transcript is lossy and such actions
cannot be recalled. Drew removed it, and he was right twice over. The rule made
saying a thing clearly count for *less* than typing the same words, which is the
opposite of what a voice surface is for; and it was enforced by asking the model
nicely in a prompt, which is not a control — it stopped the obedient case and
nothing else.

The mishearing risk is real and is answered where it can actually be answered:
the transcript channel shows the exact text that was delivered, on the same line
that confirmed the tag, so a wrong instruction is visible immediately rather than
prevented unreliably. Authority lives where it always did — the control-plane
secret plus the owner check on the endpoint.

Only the configured owner is obeyed — everyone in the room is transcribed, but
being present is not authorisation. Anything that is not a command leaves no
trace at all; a controller that answered ordinary conversation would make the
room unusable.

The utterance is delivered through `POST /api/threads/{id}/spoken`, which is
deliberately not the agent-to-agent relay endpoint: that one stamps every
message "NOT from your human" and allows one message per thread pair per
minute, both correct between sessions and both wrong for a person mid-sentence.
The receiving session is told the words arrived through speech recognition, so
it reads a mangled path or flag for intent and says what it reinterpreted
instead of stopping to ask.

Enabling this is a second decision, not a consequence of the first: hearing the
room is passive, acting on it starts agent turns. A half-filled control config
fails at startup rather than the first time the owner speaks.

## Persistence and limits

- SQLite stores sessions, speaker segments, pending audio jobs, pause state, and
  publication message IDs. Captured utterances are saved before recognition;
  successful jobs delete their temporary WAV. Failed jobs retain audio for review
  until their session's local retention cleanup.
- Continuous speech is split into bounded chunks (`VOICE_MAX_UTTERANCE_SECONDS`,
  30s by default), without tearing down
  the speaker stream. Disconnect flushes speech already in the current buffer.
  A hard process crash can lose at most the current in-memory chunk per speaker.
- A five-second health loop processes queued work but never joins or reconnects
  voice. After a restart, any recovered recording is stopped and its saved
  transcript published, even if the owner is present. A new join command is required.
- Pending work finishing after a stop edits the existing transcript attachment.
  Failed and pending counts remain visible; a failed transcription is not reported
  as a complete transcript.
- Local stopped sessions and local Markdown exports expire after 30 days by
  default. **Discord attachments remain until deleted in Discord.** A live session
  is never deleted by retention cleanup.
- Audio receive is best effort; shared microphones, packet loss, and local model
  recognition errors can affect attribution or words. Recognition is in utterance
  batches, not word-by-word live captions. Very long transcripts may exceed
  Discord's attachment limit; their full local Markdown remains available.
- No summaries, spoken answers, voice playback, or following the owner into other
  rooms are enabled.

The upstream Opus install chain originally included vulnerable `tar` 6.2.1.
This extension pins the compatible patched `tar` 7.5.22 override; fresh installation,
native decoder loading, and `npm audit` were verified.

References: [Discord voice protocol](https://docs.discord.com/developers/topics/voice-connections),
[discord.js voice package](https://github.com/discordjs/discord.js/tree/main/packages/voice),
[faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Verification

Run `npm test`. The tests cover presence transitions, concurrent events, pause
persistence, control authorization, disclosure before capture, separate speakers,
continuous speech, stop flushing, SQLite recovery, late transcript updates, silence,
WAV framing, and credential isolation. `npm audit --omit=dev` checks dependencies.

See `VERIFICATION.md` for the recorded build checks. Live acceptance needs an actual
person to join, speak, and leave; a synthesized audio test does not prove microphone
capture through Discord.
