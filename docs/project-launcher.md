# Computer session launcher

## A fixed place to start

Each bot publishes persistent **Favorite folders**, **New session**, and **Resume**
buttons, labelled with its display name or `CCDB_COMPUTER_NAME`. The category
already identifies the computer. Buttons always belong to the bot that posted them.
`/launcher` opens a private copy in an authorized channel.

Set `CCDB_LAUNCHER_CHANNEL_ID` to the existing control-center channel and
`CCDB_LAUNCHER_SESSION_CHANNEL_ID` to its workers channel. The workers channel is
automatically included in chat routing. Existing channels and threads remain available.
When unset, existing consumers keep their original primary-channel launcher and
create sessions under the invoking channel. The framework does not create channels.

The pinned anchor stays in control-center. After channel messages, a ten-second
coalescing delay adds a silent three-button shortcut at the bottom, replacing only
the previous bot-owned shortcut. Worker threads, system events and the launcher’s
own controls do not trigger it. Conversation messages and the pinned anchor are
never removed by this refresh.

## No panel at all

`CCDB_CONTROL_CENTER_PANEL=off` makes the control center a place you type in and
nothing else: no pinned panel, no republished control row, no buttons. Everything
those buttons opened is already a slash command — `/cd` and `/new` for a
folder-bound session, `/sessions` for old ones, `/settings` for what this computer
supports — and a quick chat needs none of them.

The switch removes what is already posted, not just future publishing: on the
next start the pinned panel is unpinned and deleted, the saved control row is
deleted, and both stored ids are forgotten. Only the two messages ccdb tracked
by id and still owns are touched. Default is on, because dropping a consumer's
only visible entry point on upgrade is not a change they asked for.

Turning it back on republishes both on the next start.

## Type nothing but the question

A normal message in the launcher channel starts a **quick chat**: a thread off
that message, running the message as its first task, bound to no folder — it
works where this instance's runner works by default, like any other unbound
thread. It is the shortest path there is to the agent, and it costs the control
center nothing: the bot's own control row is still shut out, so the row it
republishes after every message never starts a session. Replies inside the
thread continue that session, exactly as they do under any chat channel.

Use **New session** or `/cd` when the work belongs to a folder; use a quick chat
for the questions that do not.

## Type the folder instead of walking the menus

`/cd <folder>` starts a session in a folder without opening a single menu; the
`folder` argument is autocompleted. Before anything is typed the list is the
folders this operator used most recently, then their favorites, then what is
under `CCDB_PROJECT_ROOTS` (projects and one level below them, scanned at most
every 30 seconds and never on the event loop). Typing filters it: case and
separators are ignored and scattered letters match, so `echat` reaches
`ebi-agent-chat-relay`. A full path that no scan ever saw is still accepted,
because the typed text is submitted as-is.

`/cdnew` is the same command under its older name, and `/new` takes the same
optional `folder` argument — with it, `/new` skips its menu entirely. An
unauthorized user, a wrong category or an unreadable root produces an empty
list, never an error and never a folder listing.

## Find any accessible folder

**New session → Favorites / Recent / Browse folders → Start here**.
Selecting a favorite or recent folder opens the browser at that location.
Browse opens the default working folder. Open child folders, use **Up**, **Home**,
or **Drives**, and use **Previous / Next** for directories with more than 25 children.
Windows uses its available drive list; macOS and Linux start at the filesystem root.
Hidden folders are included. Browsing is limited by the bot account's filesystem
permissions, not by the configured projects directory. There is no live keyword
search or folder-creation button in this revision.

Only **Start here** creates a new public thread bound to the displayed absolute
folder. It joins the requester and configured shared members and shows the folder
in the welcome message. The first human reply starts the model normally. Menus
and thread creation make no model calls. Filesystem reads run off the event loop.

## Favorites and existing work

**Save favorite** saves the currently displayed folder without typing.
**Favorite folders** manages up to 25 saved entries; optional **Add by path**
accepts an absolute path. Suggestions come from `CCDB_PROJECT_ROOTS`
(comma-separated) or the default working directory. Favorites and recent folders
persist separately for each guild and user in the instance settings database.

**Resume** offers accessible original threads among the 25 most recent session
records. It links to the existing conversation, including archived threads, without
cloning it. Deleted, forbidden, other-guild and other-computer threads are omitted.
Private threads require membership or thread-management permission. The separate
legacy `/resume` command retains its existing behavior.

## Keep bots in their categories

Optional `CCDB_ALLOWED_CATEGORY_IDS` is a comma-separated category boundary for
application commands, autocomplete, launcher buttons and normal framework chat.
Threads inherit their parent's category. Outside the boundary commands get a
private explanation, autocomplete returns no suggestions, and chat is ignored.
The tree check composes with existing consumer authorization; it does not replace
operator checks. With the variable unset, existing consumers are unchanged.
Custom message listeners, scheduled jobs and API-triggered work retain their own
policies; this setting does not remove bots from guild membership or change roles.

This is an execution boundary, not a slash-menu visibility promise. Discord
Administrators can use all application commands regardless of command permissions:
https://docs.discord.com/developers/interactions/application-commands#permissions
Category buttons avoid choosing among duplicate slash commands. Changing native
command permissions requires a suitably scoped user OAuth token, not a bot token.

## Access and recovery

Every launcher action checks the instance operator/channel/category policies.
Private menus belong to the requesting user and expire after five minutes; reopen
the persistent panel after expiry or restart. Paths are rechecked before starting.
Reconnects update the saved panel; a deleted panel is recreated. Pinning is best
effort. No credentials, model preferences, permission defaults or personal
instructions are copied between computers. Linux tests do not verify Windows boot.
