# Automatic voice transcripts for an existing ccdb bot

This optional instance extension uses the existing Discord bot account to transcribe
one designated voice room whenever its configured owner is present. It reuses the
Hanoi Community transcription modules; `SOURCE.json` records their original hashes.
No new Discord application, token, or invite is needed.

Join the room to start. Everyone speaking there gets separate speaker attribution.
Leave to stop. The recording notice has a **Pause transcription** button that anyone
in the room can use; only the owner can resume. A pause also clears when the owner
leaves and rejoins. Muting/unmuting does not start another session.

The bot joins muted and undeafened. Recognition runs locally with faster-whisper,
using an already downloaded `base.en` model, with no transcription API calls. One
Markdown attachment in the private transcript channel is updated while recording
and as remaining audio finishes after stopping. Speaker timestamps are UTC.

## Integration

ccdb's chat service remains its own process. A small Node voice companion connects
using the **same identity**, subscribing only to guild and voice-state events. It
neither consumes chat messages nor registers/overwrites slash commands. The
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
   private channels through their administrator permission.
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

## Persistence and limits

- SQLite stores sessions, speaker segments, pending audio jobs, pause state, and
  publication message IDs. Captured utterances are saved before recognition;
  successful jobs delete their temporary WAV. Failed jobs retain audio for review
  until their session's local retention cleanup.
- Continuous speech is split into bounded 20-second chunks, without tearing down
  the speaker stream. Disconnect flushes speech already in the current buffer.
  A hard process crash can lose at most the current in-memory chunk per speaker.
- A five-second health loop retries connections and queued work. After a restart,
  an active session resumes only if the owner is still in the designated room.
  Otherwise it is stopped and its saved transcript published.
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
