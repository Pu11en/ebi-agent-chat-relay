# Findings: what the stopped agents left (2026-09-10, ~20:28)

All three Discord sessions were stopped by Drew's request. Sources: four read-only search agents plus direct checks.

## DSH thread "📂 ebi-agent-chat-relay" (1547744459104583771)
- **What it built:** five commits straight on `main`, none pushed:
  - 3de8ee2: extra authorized users
  - 03b79bb: auto-join operators
  - 27377b8: DSH thread-id binding fix
  - 7b19d85: retry failed member adds
  - 24d945d: mute a member's reply-needed ping
- **`.env`:** sets `CCDB_ALLOWED_USER_IDS` and `CCDB_THREAD_MUTE_USER_IDS` to the second operator.
- **Forced restart:** it armed `data/restart_after_turn.sh`, hooked into `scripts/health-check.sh`, to force a restart within 240 s. This session disarmed it as `data/restart_after_turn.sh.disarmed`, so the mute feature is not live yet.

## Thread "📂 audio-content" (1547448027776811038)
- **What it built:** auto-transcribe every voice recording with speaker labels (faster-whisper + ffmpeg, both present).
- **Already on GitHub:** merged as PR #17, which is `origin/main` 9d9c336.
- **The local edits are duplicates:** the uncommitted copies in the main tree are byte-identical to 9d9c336, so discarding them is safe.
- **Not live yet:** it hasn't run on a new recording.

## Thread "📂 realpage" (1547708946704506920): out of scope, Drew owns it
- **QA results:** 148 findings, mostly table overflow at tablet and phone widths, plus two dead links.
- **Uncommitted fixes in `/home/drewp/main-projects/realpage`:** URL fixes, rebuilt JSON, and a small CSS change.

## Repo state
- **`main` vs GitHub:** 9 commits ahead of `origin/main`, 1 behind.
- **Branches:** `feat/plans-as-markdown` ⊂ `feat/task-loop` (the worktree at `/home/drewp/main-projects/wt-task-loop`) holds:
  - 7bb740f: plans as markdown
  - b3e4d9e, 68e8b93: task loop, `/gowork`
  - 2f818dc: start-fresh nudge
- **Untracked from an earlier session (04:47):** `examples/ebibot/cogs/workdir_command.py` and `extensions/project_picker/`. They break `test_example_cogs_load`.
- **Worktrees:** 8 in total, several of them stale (see Task 9).

## Context usage per harness (for the nudge)
- **Claude:** `modelUsage.contextWindow` on the result event. Works today.
- **Codex:** the `token_count` event carries `model_context_window` (e.g. 258400), but `parse_codex_line` ignores it (`codex_runner.py:109-117`).
- **DSH:** the SDK exposes no usage fields at all (`dsh_backend.py:756-806`), so usage must be estimated.

## Outside the repo
- **Hermes gateway:** PID 537 is still running.
- **crontab line 1:** runs `~/.hermes/profiles/asset/cron/audio-cache-cleaner.sh`, which is missing.
- **crontab line 2:** runs `/home/drewp/discord-control/idle-nudge.sh`, which is the wrong path.
