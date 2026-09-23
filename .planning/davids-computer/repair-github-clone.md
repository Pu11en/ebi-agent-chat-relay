# David: repair GitHub cloning from Discord

## 1. Your task and the known state
- Task ID: `david-gh-clone-20260919-01`; check your recent work for this ID and resume rather than installing twice.
- Drew requests execution on David's Windows computer. Do the installation and verification yourself; do not send this instruction back to Drew or ask him to type terminal commands.
- The observed `/new` failure is: “GitHub CLI is not visible to the bot. Its existing installation needs to be connected.” That text does not prove an installation exists.
- Your last receipt reported GitHub CLI absent. `/new` is already active: instance commit `106acaf`, bridge `e72a851`, ProjectCreationCog loaded, 59 instance tests passed. Inspect current state before changing it.
- Success: `/new` → Create project → Clone GitHub repo creates a new project directory and an idle workers thread; its first human message starts the agent.
- Keep this task small: fix GitHub CLI, its environment and authentication. Do not redesign the menu or build agent-to-agent messaging here.

## 2. Find or install GitHub CLI
- Work as Windows user `david`. Bridge: `C:/Users/david/ccdb/bridge`; instance: `C:/Users/david/ccdb/instance`; runtime Python: `C:/Users/david/ccdb/.venv/Scripts/python.exe`; projects: `C:/Users/david/projects`.
- Read the existing startup/supervisor scripts and determine the bot's actual Windows identity and environment. Check `Get-Command gh -ErrorAction SilentlyContinue`, `where.exe gh`, and existing GitHub CLI installation directories. Do not dump the environment or credentials.
- If an existing `gh.exe` works, reuse it. If absent, install GitHub's official CLI: `winget install --id GitHub.cli --exact --source winget --accept-source-agreements --accept-package-agreements`.
- Inspect the command's exit status. If winget is unavailable or installation requires an unavailable elevated desktop, use the official `cli/cli` Windows ZIP matching this computer's architecture, verify its published checksum, and extract into an instance-owned tools directory. Do not download an executable from an unofficial mirror or overwrite unrelated files.
- Execute the located binary by absolute path and verify `--version`. Confirm `git --version` too; diagnose a missing Git separately rather than claiming GitHub CLI alone fixed cloning.

## 3. Make the background bot find it
- The installed picker uses `shutil.which("gh")` in the BOT process. A PATH change in your Claude subprocess alone cannot fix the parent bot.
- Back up the narrow startup file you change. Add the discovered executable's directory to the bot's effective PATH before Python starts, preserving the rest of PATH. Use the existing launcher configuration where possible; do not invent an unsupported environment setting.
- Ensure this applies to normal logon startup, supervisor recovery and the idle-aware restart path, not just today's terminal. Avoid a system-wide PATH rewrite.
- In a fresh process launched through that same environment, run the runtime Python with `-c "import shutil; print(shutil.which('gh')); print(shutil.which('git'))"`. Both must resolve to working executables.
- Preserve personal Claude settings, credentials, subscription, instructions, memory, permissions, projects, session history and other running agents. Do not change the Claude authentication method.

## 4. Reuse authentication or request only browser authorization
- Under the bot's actual Windows user/environment, run `gh auth status --hostname github.com` and `gh api user --jq .login`, using the located executable. Never use `gh auth token`, `--show-token`, or print credential files/token values.
- A working existing login should be reused. If multiple accounts make the intended account ambiguous, ask Drew only which username to use. Do not log out an existing account or copy credentials from another computer/profile.
- If authentication is absent, start `gh auth login --hostname github.com --git-protocol https --web` yourself for Drew's EXISTING account; this is not account creation. The current repair request permits this necessary connection step, superseding the older handoff's instruction to stop before starting login.
- Keep the login process alive while presenting its actual GitHub verification URL and one-time user code to Drew in this authorized setup thread. Do not publish any token, password or OAuth device_code. Tell Drew the only required action is to authorize the existing account in his browser. Handle the CLI's routine prompts yourself; do not tell him to run a terminal command.
- If browser authorization cannot be completed during the turn, report `WAITING_FOR_GITHUB_AUTH`, keep a usable continuation, and finish independent installation checks. Do not say cloning is ready. Do not repeatedly initiate new logins or claim approval happened.
- After authentication succeeds, run `gh auth setup-git --hostname github.com`, then verify the account username again from the bot environment. Preserve unrelated Git configuration; do not invent Git author name/email.

## 5. Verify the actual clone path and activate
- Keep changes local. If a code fix is necessary, write a regression test first; use the existing dev venv and instance test suite, preserving the platform-aware test fix. Do not reinstall the already working project picker merely to configure PATH.
- Check the actual picker's subprocess environment, not only an interactive `gh` command. Use its `create_project` function with the same bot environment and root. Inspect its current signature first.
- If the failed interaction retained Drew's requested repository and folder, reuse that request only if the destination remains absent. Otherwise use the small public `https://github.com/octocat/Hello-World` as a labeled temporary clone check with a unique new folder name. Never overwrite an existing folder; delete only the temporary test directory you created, after checking its identity.
- Check the clone return status and `.git` directory. A public clone does not prove private-repository access; report that distinction. Do not push, open PRs, run cloned code, or invoke a model for this check.
- Control-center: `1550644904617582662`; workers: `1550644907100741783`; category: `1550644903384580167`; guild: `1546639912848199742`. Preserve access for Drew `488763953397235712` and David `718234548139196476`.
- Use the existing independent idle-aware restart when needed. Check active sessions and announce before restarting; do not kill active turns. Report activation as pending until the restarted process verifies executable discovery and command loading.
- After activation, verify the real bot environment can execute `gh` and access the authenticated account. Do not call a simulated interaction a real Discord button test. Leave the final `/new` clone click for Drew if no real click was observed.

## 6. Report a concise receipt, not another plan
- Post progress and the final receipt in this David setup thread `1550681218717192263`; Drew's agent can read it without the human forwarding it.
- Include task ID, status (`READY_TO_TRY`, `WAITING_FOR_GITHUB_AUTH`, `ACTIVATION_PENDING`, or `BLOCKED`), installed version/path, Windows identity, GitHub username if authenticated, bot environment checks, clone check, tests, restart result, and any exact remaining owner action.
- `READY_TO_TRY` requires a working CLI from the bot environment, verified authentication and clone check, plus completed activation if needed. Say explicitly if a human button click/private clone remains untested.
- Commit only your own local changes. No GitHub publishing. If blocked, give the specific failed command/error with secrets removed and continue any independent work.
- Starter instruction for this task: **Run task david-gh-clone-20260919-01 from DrewAI's latest repair instructions and report the verified result here.**
- Official references: [Windows installation](https://github.com/cli/cli#installation), [browser login](https://cli.github.com/manual/gh_auth_login), [Git credential setup](https://cli.github.com/manual/gh_auth_setup-git).
