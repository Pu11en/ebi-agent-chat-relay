# How to make this much more reliable (2026-09-26)

Today's failures were not unrelated bugs. Five structural causes produced all of
them, and each has a cheap, testable fix. 6,002 unit tests were green the whole
time the system was unusable — so more of those is not the answer.

## 1. What is running is not what was tested, and nothing notices

The single most expensive failure today. The speech-worker fix was committed in
the repo and **never deployed**; the live worker was an older copy that fed the
tag list to the decoder, so room noise came back *as* tag words and "bravo" was
inserted into the front of a real sentence. Voice was unusable for hours over a
fix that already existed. It was found by hand, with `md5sum`.

Right now the bot process started at **10:32** and the repo is **11 commits
ahead** of it. Nothing anywhere reports that.

**Fix:** `/api/health` returns the deployed commit, the repo's `HEAD`, and the
voice runtime's checksum versus the repo's. When they differ, say so once in
Discord at startup and in `/doctor`. Small, and it catches this whole class.

## 2. The safe-restart mechanism exists — and is misconfigured to death

`AutoUpgradeCog` already implements exactly the right thing: it discovers every
`DrainAware` cog and waits for all of them to reach zero in-flight work before
restarting. On this machine it cannot possibly run:

| Configured | Reality |
|---|---|
| `working_dir=~/discord-bot` | does not exist |
| `restart_command=sudo systemctl restart discord-bot.service` | no such unit |
| a *system* service | it is a **user** unit, `ebi-agent-chat-relay.service` |
| `drain_check` | **not passed at all** — so it would not wait for idle |

So the one mechanism designed to make restarts safe has never worked here. That
is *why* restarts feel dangerous, and why fixes pile up undeployed — today four
of them did, which is how a stale voice worker survived all day.

**Fix:** point it at the real directory, the real user unit, and pass the drain
check. Then "restart when idle" is one command that provably never kills work,
and deploying stops being a decision.

## 3. Failures are silent, or buried in a log nobody reads

- `Todoist fetch error` — **467** identical errors for a script that is not on
  this machine
- `429 Too Many Requests` — **6,012**, up to 120 in one minute
- `Skipping worktree removal (dirty)` — **531** for one project alone

And the quiet ones are worse: the thread rename was *skipped* rather than
attempted, so a stale tag sat there forever; audio that produces no words says
nothing, so Drew repeats himself not knowing why.

**Fix:** a repeated failure surfaces **once** in Discord with a count and then
goes quiet. A log line is not a report.

## 4. Invariants were intentions, not checks

Every one of these was written down as a rule and none was enforced:

- "a closed session holds no spoken tag" → **25 of 26** tags were held by closed
  sessions
- "a tag belongs to exactly one thread" → two threads both read `[bravo]`
- "deployed code == committed code" → false for hours
- "a closed session is not listed as live" → every closed session was

**Fix:** a `/doctor` command that asserts these against the **live** system and
prints pass/fail. Each is a few lines. This is the highest value per line of code
of anything here, because it turns "Drew notices and complains" into "the bot
says so first".

## 5. The bugs live in the seams, where unit tests cannot reach

All four of today's real bugs were in places a mock cannot represent:

- deployed artefact vs committed source
- Discord's **actual** rate limit (metered per channel, while the code paces per
  message — 15 messages in one thread is ~3.7x over)
- the recogniser's **actual** behaviour (measurable: non-speech gives
  `no_speech_prob` 0.86–0.94 — I ran it)
- **accumulated** live state (314 session rows, 26 tags, 8 stale worktrees)

**Fix:** a handful of contract checks that run against the real thing, not a
mock — one for the speech worker's confidence floors against generated
non-speech, one that counts `429`s during a long streamed answer, plus the
`/doctor` invariants above.

## Order to do them in

1. **Fix the auto-upgrade config and wire the drain check** — unblocks
   everything else, because it makes deploying safe and routine
2. **Drift reporting in `/api/health` + at startup** — would alone have prevented
   today
3. **`/doctor`** — the four invariants, asserted against live state
4. **Surface repeated failures once in Discord** — kills the 467/6,012/531 noise
5. **The rate-limit pacer** — one edit budget per thread (the pattern already
   exists in `claude_teams/pacer.py`)
6. **The two contract checks** — speech confidence, and `429`s per answer

Items 1–3 are small and would have caught or prevented every failure today.
