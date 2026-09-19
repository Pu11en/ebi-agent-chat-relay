# David: use the existing GitHub login

## Task to perform now
- Work on David’s Windows computer and connect the bot’s Git operations to the GitHub CLI login already present there.
- Drew confirms the GitHub login already exists and authorizes using his GitHub account for David’s bot.
- Do the work and report verified results; do not stop after an audit or ask the user to type terminal commands.
- No new account, signup, subscription, fresh login flow, or credential transfer is requested.

## Find the existing login
- This installation was last verified as native Windows: bridge `C:/Users/david/ccdb/bridge`, instance `C:/Users/david/ccdb/instance`, runtime `C:/Users/david/ccdb/.venv`, projects `C:/Users/david/projects`.
- Inspect the current bot process and startup task to determine its Windows user and environment; preserve the existing setup.
- Locate GitHub CLI using `Get-Command gh -ErrorAction SilentlyContinue`; if it is not on the bot’s PATH, look for the existing installation before considering installation.
- In the same Windows user context used by the bot, run `gh auth status` and `gh api user --jq .login`.
- Inspect credential-related environment variable names and whether they are set, without printing their values; environment tokens can override stored authentication.
- Reuse the account already authorized by Drew; do not log it out or replace it with a newly created account.
- If multiple existing accounts make Drew’s identity ambiguous, report their usernames and ask which to use rather than overwriting authentication.
- If this process cannot see the existing login, diagnose its Windows user, HOME/profile and GH_CONFIG_DIR first; a login in another user profile is not automatically available to the bot.
- Never output tokens, passwords, credential files, or commands that reveal them, including `gh auth token` or `gh auth status --show-token`.

## Connect Git and verify
- Once the existing account is confirmed usable, run `gh auth setup-git --hostname github.com` so Git HTTPS operations can use GitHub CLI as the credential helper.
- Verify GitHub CLI access from the bot/session execution environment, not only a separate interactive terminal.
- Read repository metadata with `gh repo list --limit 100 --json nameWithOwner,isPrivate,url`; show only the account identity, count and access status in the public receipt, not a dump of private repository names.
- Verify private-repository access without changing it, when a private repository is available to the account; if none exists, say that private access was not exercised.
- Do not clone a random repository, push, create a PR, launch a paid model test, or modify Git author identity merely to test authentication.
- Do not restart healthy sessions for this configuration change; if the bot needs an environment refresh, use its existing idle-aware restart mechanism and report whether activation is pending or verified.
- If authentication truly is unavailable, report the exact missing account/context and the minimum required owner action; do not start a signup or login flow automatically.

## Keep David’s setup and the agreed project flow
- Preserve David’s own Claude subscription, login, instructions, skills, memory, permission mode, projects and session history.
- Preserve the coordination-header fix and requester-only completion notification behavior.
- Both Drew (`488763953397235712`) and David (`718234548139196476`) retain access.
- David’s category is `1550644903384580167`, control-center `1550644904617582662`, workers `1550644907100741783`, bot `1550644558176460961`, guild `1546639912848199742`.
- The separately planned `/new` flow is Recent / All projects / Create project, with Empty folder or Clone GitHub repo creating a new child folder inside `C:/Users/david/projects`.
- Successful project creation should open a clean idle workers session bound to its folder; only the first user task starts the model.
- Cloning into an existing folder remains a normal-language request inside a thread.
- This file configures and verifies existing GitHub access only: it does not claim the new `/new` UI is already implemented, and does not require a framework upgrade or copying Drew’s home configuration.

## Report the result in Discord
- Post a concise receipt in the current David session: actual GitHub username, whether the bot can reuse its login, whether Git credential setup succeeded, repository/private-access checks, and any genuinely unresolved condition.
- Clearly separate completed GitHub setup from the separately planned project picker.
- If files were changed, save only those changes in a local commit; do not publish or push.
- Final success wording must reflect actual checks; do not say installed, connected or verified based only on receiving this handoff.
