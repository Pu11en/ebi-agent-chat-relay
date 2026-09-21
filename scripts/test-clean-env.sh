#!/usr/bin/env bash
# Run the test suite without this machine's live Discord/ccdb settings.
# A shell started by a ccdb bot session inherits DISCORD_* and CCDB_* variables that
# change how some cogs behave, so tests that pass in CI can fail there. Extra
# arguments go to pytest (default: `tests/ -q`, the whole suite).
set -euo pipefail
cd "$(dirname "$0")/.."
unset_args=()
while IFS= read -r name; do
  unset_args+=("-u" "$name")
done < <(env | grep -oE '^(DISCORD|CCDB)[A-Z0-9_]*' || true)
if [ "$#" -eq 0 ]; then set -- tests/ -q; fi
exec env "${unset_args[@]}" uv run pytest "$@"
