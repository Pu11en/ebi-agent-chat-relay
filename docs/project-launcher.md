# Computer session launcher

## A fixed place to start

Each bot publishes persistent **Favorite folders**, **New session**, and **Resume**
buttons, labelled with its display name or `CCDB_COMPUTER_NAME`. The category
already identifies the computer. Buttons always belong to the bot that posted them.
`/launcher` opens a private copy in an authorized channel.

Set `CCDB_LAUNCHER_CHANNEL_ID` to the existing control-center channel and
`CCDB_LAUNCHER_SESSION_CHANNEL_ID` to its workers channel. The workers channel is
automatically included in chat routing. Normal messages in the launcher channel
do not start chat sessions. Existing channels and threads remain available.
When unset, existing consumers keep their original primary-channel launcher and
create sessions under the invoking channel. The framework does not create channels.

The pinned anchor stays in control-center. After channel messages, a ten-second
coalescing delay adds a silent three-button shortcut at the bottom, replacing only
the previous bot-owned shortcut. Worker threads, system events and the launcher’s
own controls do not trigger it. Conversation messages and the pinned anchor are
never removed by this refresh.

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
