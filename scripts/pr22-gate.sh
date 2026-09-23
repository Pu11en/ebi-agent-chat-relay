#!/usr/bin/env bash
# Full local gate for the PR #22 green-checks build, as one command.
# Exists because the build runner executes the plan's Check: line as a single
# command invocation, so a `cmd1 && cmd2` chain there gets its tail fed to
# pytest as arguments. Keep this script self-contained and fast (<2 min).
set -euo pipefail
cd "$(dirname "$0")/.."

# Sandboxed runners may mount the default uv cache (~/.cache/uv) read-only;
# fall back to a cache inside the repo so the gate still runs there.
if [ -z "${UV_CACHE_DIR:-}" ] && ! mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/uv/.writable-probe" 2>/dev/null; then
  export UV_CACHE_DIR="$PWD/.uvcache"
  mkdir -p "$UV_CACHE_DIR"
fi
rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/uv/.writable-probe" 2>/dev/null || true

scripts/test-clean-env.sh tests/test_work_copy.py tests/test_task_loop_cog.py tests/gowork_upgrade/ -q -p no:randomly
uv run ruff check claude_discord/ claude_code_core/ tests/
uv run ruff format --check claude_discord/ claude_code_core/ tests/
