# Project creation menu

An optional, standalone custom Cog for a computer's control-center channel.
It works with the existing `ClaudeChatCog.spawn_session` interface, including
the interface at bridge revision `9d9c336`; no framework replacement is needed.

`/new` takes no arguments and opens **Recent / All projects / Create project**.
Create project offers **Empty folder / Clone GitHub repo**. For cloning, paste
a GitHub HTTPS link or `owner/repo`, with an optional new folder name.
The default folder name is the repository name. GitHub account browsing is not
included in this first version.

Projects are immediate child directories of the configured root. Recent
projects are personal to each Discord user and persist in the state file.
Lists have 25-item pages. Hidden staging directories and links outside the root
are excluded. Existing arbitrary-folder launchers can remain available.

Every selection creates a fresh, idle session in the configured workers
channel. The existing bridge retains folder binding and adds configured thread
members. No model runs until a human sends the first task. Menus belong only to
the user who opened them and are accepted only in the configured control-center.
The workers channel must be in that same guild and category.

## Installation

Copy `project_creation.py` into the instance's existing `CUSTOM_COGS_DIR`.
Configure these values in its existing environment file:

```dotenv
PROJECT_HOME_CHANNEL_ID=<control-center channel ID>
PROJECT_WORKERS_CHANNEL_ID=<workers channel ID>
PROJECT_ROOT=<absolute projects directory>
PROJECT_STATE_FILE=<absolute path outside projects for recent-project state.json>
```

The Cog uses `CCDB_ALLOWED_USER_IDS` (comma/whitespace-separated IDs) and
`DISCORD_OWNER_ID`; it refuses to load without an operator list. Preserve the
instance's existing thread-member list and allowed operators. Include workers
in the bridge's `CCDB_CHANNEL_IDS` so plain replies there start the session.
Check existing command registrations for `/new` before loading; do not remove
another Cog's commands without inspecting ownership. Use the normal instance
command-sync and idle-aware activation procedure.

GitHub CLI and Git must already be installed and usable by the bot's OS user.
The Cog reuses that user's existing GitHub CLI credentials and does not start
a login or install tools. It retains GitHub credential environment variables,
strips Discord/bridge variables from the clone child, and disables interactive
credential prompts. No credentials are included in this package.

## Clone lifecycle

- Validate Windows-compatible new-folder names on every platform, including
  reserved names and case-insensitive collisions.
- Accept only GitHub HTTPS URLs or `owner/repo`, never user-supplied commands,
  flags, other protocols or credential-bearing URLs.
- Clone with `gh repo clone` using an argument array, into a private staging
  directory under the project root. Never invoke a shell or an AI model.
- After successful cloning, reserve the destination exclusively and move the
  cloned files into it. Existing destination folders are never reused.
- A failed clone removes only its private staging directory and starts no
  session. A publishing or Discord failure may leave a newly created project;
  the error tells the user to check All projects before retrying.
- Clone timeout is ten minutes, within the interaction's lifetime. Timeout or
  cancellation terminates that clone's process tree. Windows uses `taskkill /T`
  on the spawned process ID; Unix uses a dedicated process group.
- Progress is a cloning/creating notice followed by success or failure, not a
  byte-level progress bar. Retry with the same still-open creation menu or /new.

## Verification

```sh
python -m pytest examples/project_creation/tests -q
ruff check examples/project_creation
ruff format --check examples/project_creation
pyright examples/project_creation/project_creation.py
ruff check examples/project_creation/project_creation.py --select S
```

When this directory is delivered by itself, use `python -m pytest tests -q`.
The tests use temporary directories and mocked GitHub/Discord boundaries; no
real repository is cloned and no model call or network authentication is made.

Local checks: 37 focused tests pass; lint, format, type and security checks pass.
The full clean repository run passed 3150 tests with 36 of these focused tests;
the additional category-isolation test was then added and all 37 focused tests
passed. One known aggregate-hanging framework test was run separately and passed.
Native Windows activation and real Discord button clicks still require checking
on the receiving instance; Linux tests do not establish those results.

## How to try it

1. Run `/new` in the configured control-center; select Create project, then
   Empty folder, and check the new workers thread waits for your first task.
2. Select Clone GitHub repo with a repo you can access; check the cloned project
   appears beside the others and its thread is bound to that exact directory.
3. Run `/new` again and check Recent; try the same folder name and verify the
   existing project stays intact.
