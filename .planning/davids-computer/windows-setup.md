# Connect David’s Windows computer to Discord

## 1. Ready now and still needed

✅ **DAVID’S COMPUTER** exists, with **control-center** and **workers** channels.

✅ David’s Discord application ID is recorded: `1550644558176460961`.

⏳ The application has not joined the server, and no bridge has been installed or tested on David’s computer from this session.

The intended connection is **David’s Discord bot → bridge running on David’s Windows computer → David’s own Claude Code installation and login**.

The public key is not needed for this bridge’s Discord connection; it uses a bot token and Discord’s persistent connection instead.

## 2. Reset the token and invite the bot

⚠️ A secret bot token was pasted into this thread. Reset it on [David’s application Bot page](https://discord.com/developers/applications/1550644558176460961/bot); enter the replacement only in the local setup on David’s computer, never in Discord chat or the prompt below.

On that same Bot page, enable **Message Content Intent**, which this bridge requests to read messages. See [Discord’s gateway documentation](https://github.com/discord/discord-api-docs/blob/main/developers/events/gateway.mdx).

[Invite David’s bot to this server](https://discord.com/oauth2/authorize?client_id=1550644558176460961&scope=bot%20applications.commands&permissions=0&guild_id=1546639912848199742&disable_guild_select=true).

The invite requests no extra server-wide permissions. After the bot joins, grant its role access to David’s category and both channels: view channels, send messages, read history, embed links, attach files, add reactions, create public threads, and send messages in threads. Check inherited server permissions too; the invite alone does not establish isolation. Discord installation and permission concepts are described in [its bot setup guide](https://docs.discord.com/developers/quick-start/getting-started).

## 3. Paste this into Claude Code on David’s computer

The prompt contains no secrets. It tells the local agent what to install and which Discord section to use.

```text
Set up a separate claude-code-discord-bridge instance on this Windows computer so Discord talks to this computer’s Claude Code, using its own existing login.

First check whether this Claude Code session runs in native Windows or WSL. Keep the bridge and Claude CLI in the same environment. Inspect any existing installation before changing it; do not overwrite credentials or another bridge instance.

Source repository: https://github.com/Pu11en/ebi-agent-chat-relay.git
Published main commit verified on 2026-09-18: 9d9c3369bbc54bcd91c60d3aa891b682bbe513ef
Use that revision as the starting point, read its local instructions, and use a dedicated installation directory and Python 3.12 or 3.13 environment. This is a published baseline; do not assume it includes Drew’s unpublished development features.

Discord application/bot ID: 1550644558176460961
Guild ID: 1546639912848199742
David category ID: 1550644903384580167
control-center ID: 1550644904617582662
workers ID: 1550644907100741783

David’s Discord user ID is 718234548139196476; Drew’s is 488763953397235712. Both must be allowed to operate agents and auto-joined to session threads. Ask David which project directory agents should work in. The application ID is not a human user ID.

Have David enter the newly reset bot token through a masked local prompt or a local configuration editor. Never request it in chat, print it, put it in shell history, or commit it. Validate the token belongs to application 1550644558176460961 without revealing it.

Configure the instance with:
CCDB_BACKEND=claude
DISCORD_CHANNEL_ID=1550644904617582662
CCDB_CHANNEL_IDS=1550644904617582662,1550644907100741783
CCDB_MONITOR_ALL_CHANNELS=false
CCDB_MENTION_ANYWHERE=false
CLAUDE_PERMISSION_MODE=acceptEdits
CCDB_DANGEROUSLY_SKIP_PERMISSIONS=false
MAX_CONCURRENT_SESSIONS=3
DISCORD_OWNER_ID=718234548139196476
CCDB_ALLOWED_USER_IDS=488763953397235712,718234548139196476
CCDB_THREAD_MEMBER_IDS=488763953397235712,718234548139196476

Use the owner, allowed-user, and thread-member IDs above. Create shared public session threads rather than a separate private copy per person. Allow separate session threads to run concurrently within the configured limit. Do not mirror or synchronize personal Leave Thread actions into destructive deletion. Set CCDB_WORKING_DIR to the agreed project directory and CCDB_DATA_ROOT to a dedicated absolute local data directory. Store DISCORD_BOT_TOKEN only in the local ignored .env file. Verify the installed revision recognizes each setting.

Check the Claude CLI executable and login availability without issuing a paid model request. Verify Discord membership, Message Content Intent, and both channels’ permissions. Check slash-command authorization as well as ordinary message handling. Do not alter Drew’s existing bots, channels, credentials, or services.

Start with ccdb start from the instance directory and verify the bot connects. Keep initial startup visible so errors can be read; do not claim it will survive sign-out or reboot yet. Do not launch additional agents or paid model tests automatically. Report the exact installed revision, environment, command to start it again, and any remaining owner action. Ask before the first end-to-end model test.
```

## 4. What proves it is connected

- **Online:** David’s bot appears online while the bridge runs on his computer.
- **Correct computer:** after approving a small live test, a reply in David’s control-center reports the expected project directory on David’s machine.
- **Correct routing:** an authorized test in another computer’s ordinary channel does not start David’s agent; also verify unauthorized users cannot invoke it through slash commands.
- **Honest operating status:** until a Windows startup task and recovery behavior are configured and tested, the bridge needs its terminal process and computer to remain running.

Claude Code supports both native Windows and WSL; follow the environment he already uses, as described in [Anthropic’s Windows setup instructions](https://code.claude.com/docs/en/setup#set-up-on-windows).

⚠️ These are setup instructions, not a claim that Windows installation, remote access, isolation, or an end-to-end agent run has already been verified.
