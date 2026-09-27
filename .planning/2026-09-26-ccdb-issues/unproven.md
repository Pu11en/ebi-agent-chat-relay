# What I have actually proven, and what I have only claimed (2026-09-26)

Drew's instruction: *"you might not be solving it, and you think you are, but
you're not."* He is right, and today gives three worked examples of exactly that:

1. I said voice control was working. The deployed speech worker was an old copy.
2. I said the tags were fixed. The mishearing list had been dropped by my own
   refactor, so saying "Zorro" reached the wrong thread.
3. I said tagging on thread creation was fixed. It worked **by coincidence** —
   the guard used a channel list that happens to contain the control center on
   this machine. Point the control center at its own channel and every thread it
   opens is silently untagged. Found by looking, not by any test. Now fixed
   (`on_thread_create` guards on the category boundary instead).

So this file separates the two. It is the honest list, not the flattering one.

## Proven — checked against the running system

| Claim | How |
|---|---|
| Every live thread has a One Piece tag | `/api/sessions`, live |
| No two threads share a tag | computed over the live list |
| Each tag carries its mishearings (`zoro` → `zorro`, `luffy` → `lucy`) | live field |
| No closed session holds a tag (was 25 of 26) | SQL against `data/sessions.db` |
| The deployed voice code equals the committed voice code | SHA over both trees |
| The running bot is not behind the committed code | service start vs commit time |
| Nothing is failing repeatedly | timestamp-aware log scan |
| No message-edit rate-limit rejections | log scan since restart |
| Non-speech audio is rejected by the confidence floor | measured: `no_speech_prob` 0.86–0.94 on synthetic silence, hiss and hum |

All sixteen run as `make live-check`.

## NOT proven — claimed, implied, or assumed

| Claim | Why it is not proven | What would prove it |
|---|---|---|
| "Answers are faster now" | Almost nothing has streamed since the restart. Zero rejections with zero load is not evidence. | One long streamed answer, then count `429`s. |
| The tool counter's 10s delay looks right | Never seen it in Discord. It might read as "nothing is happening". | Run a slow command and look. |
| A control-center thread is tagged at creation | Unit-tested only. Never observed live, and the guard was wrong until ten minutes ago. | Open one from the control center, then `make live-check`. |
| "Close everything I'm not using" works by voice | Never said out loud. Only the parser and the controller were tested. | Say it. |
| Saying a tag reaches its thread | `live-check` drives `parseByTag` — **one function in the middle**. The real path is audio → whisper → transcript → controller → API → thread. Whisper and the transport are untested. | Speak a tag, then read the transcript log. |
| The 90-second window behaves after the alias fix | Untested since the fix. | Talk to one thread, then name another immediately. |
| One Piece names survive the recogniser at all | The alias list is a *guess* at what Whisper writes. Only "Zorro" is confirmed, from Drew's own transcript. | Say each tag once; read `transcripts.sqlite`; add what actually came out. |

## The structural gap behind most of that row

There is no way to inject a transcript into the running voice companion. It reads
audio from Discord and nothing else, so the only way to exercise the real path is
for a person to speak. Until the companion accepts a test utterance over its own
loopback, "voice works" can only ever be proven by Drew talking — which is the
thing this whole effort is trying to stop.

That is the next piece of work worth doing, and it is worth more than any further
unit test.
