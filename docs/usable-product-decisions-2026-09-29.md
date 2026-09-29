# Owner decisions — September 29, 2026

Drew's settled decisions for the usable-product change. These replace the older
"Discord archive is not closure" rule everywhere it was written down.

## Sessions follow Discord

- **Open means visible in Discord.** A thread you can see is an open session. A
  thread that is archived (by you, by EBI, or by Discord) or deleted is a closed
  session, and its spoken tag word goes back into the pool.
- **Seven quiet days closes a session.** EBI asks Discord to keep every thread
  visible for 7 days, including threads you make by hand. After a quiet week
  Discord archives the thread, which closes the session. Typing wakes it up.
- **Archiving stops the work at once.** If an agent is still working when a
  thread is archived, the run is stopped first, then the session closes. Nothing
  keeps running out of sight, and nothing is posted into the archived thread.
- **A quiet close.** When Discord archives a thread, EBI posts nothing, renames
  nothing and locks nothing. It writes its own short wrap-up without asking a model.
- **Typing brings it back.** Typing in an archived thread (or speaking to it)
  reopens the session with its memory: same conversation, same backend. The
  title gets a tag again.
- **Only you reopen a thread.** Restarts, reminders, reports, follow-ups and
  notifications never post into an archived thread.
- **On for everyone.** Following Discord is on by default. An instance that does
  not want it sets `CCDB_FOLLOW_DISCORD_THREADS=false`.

## Tag words

- **Your threads get tags first.** Helper threads made by workflows (Go Work
  workers, build threads, handoff jobs, lookup workers) only get words nobody
  else needs. When the words run out and you make a thread, a helper gives its
  word up and its title loses the tag.

## Go Work stays the same

Builds start, run, wait for "looks good", keep, fix and throw away exactly as
before. Only three protective fixes:

- It never posts into an archived thread.
- A finished build thread is closed properly instead of just archived.
- After a restart, a stored build is dropped quietly if you (or Discord) closed
  its thread.

## Jester

- **Session questions need no model.** Jester answers "what's open?" and
  similar questions straight from EBI's session list.
- **Claude is the backup brain.** While Codex is out, Jester uses Claude instead.
