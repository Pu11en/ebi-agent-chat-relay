# Using the new Lockin AI planning workflow

The two-feature Discord trial passed. OpenSpec, the two selected coordination
skills, and the Ebi coordinator are installed. The workflow applies to new
feature requests from the next Codex turn after activation; active turns and
existing plans are preserved. No bot restart or new Discord command is needed.

## What you do

1. New feature: `/cdnew` in #control-center, choose the project, and describe
   what you want. For example: “Plan a saved-search feature. Don't build yet.”
2. Discuss the plan in that feature's thread. Each feature keeps its own plan.
3. When satisfied: “Build this plan.” The lead queues approved tasks in real
   worker threads and handles combining the results.
4. Continue or change that feature by replying in its existing thread.
5. Try the combined result when the lead hands it back; report what happened
   in that same feature thread.

You do not need OpenSpec commands, terminal commands, or to coordinate workers
by hand. Ordinary questions and small direct fixes stay direct. Existing
projects and older plans were not moved, deleted, or migrated.

## What the trial proved

The greeting and word counter were disposable test features, not additions to
Discord. Separate planning threads retained separate decisions. The two
approved builder processes overlapped at 20:44 Chicago time on September 7,
2026, from the same committed foundation in different worktrees. Both were
reviewed, merged, and verified together: 14 demo tests and 33 coordinator tests
pass. Repeated result collection created no new workers. The completion
handoff reached the lead through Ebi's actual thread-message API.

- [Greeting worker](https://discord.com/channels/1546639912848199742/1546696547390197793)
- [Word-count worker](https://discord.com/channels/1546639912848199742/1546696552670830672)
- [Saved verification evidence](setup-evidence/feature-workflow/verification.md)

For a quick try in this setup thread, say:
“Try the demo with name Drew and text Lockin AI works.”
The integrated result should be `Hello, Drew!` and a word count of `3`.
You can also choose your own name and text. This human try-it step remains
available; automated verification does not claim Drew has already tried it.

## Where this is maintained

The installed source and current guides live in the retained Git worktree
`/home/drewp/main-projects/lockin-workflow-runtime`. `lockin-workflow` invokes
that source. Shared Codex instructions route new feature work to the installed
`lockin-feature-workflow` skill. Runtime records live separately under
`~/.local/state/lockin-ai/feature-workflow/`, one directory per build run.
The canonical relay checkout and its local research notes remain intact.

The full relay suite has one pre-existing Teams oversized-body test failure,
reproduced on unchanged main. It is separate from the passing workflow/demo
checks; this setup is not a claim that every upstream repository test passes.
