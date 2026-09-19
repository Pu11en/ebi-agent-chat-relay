# Computer session launcher

## Start work with buttons

Each configured bot publishes a pinned session launcher in its primary channel on
startup. The heading uses the bot's display name, or `CCDB_COMPUTER_NAME` when set.
The persistent **Favorite folders**, **New session**, and **Resume** buttons route
to that bot, so operators do not need to identify it in a duplicated slash-command
list. `/launcher` also opens a private copy inside that bot's configured channels.

## Favorite folders

Select an existing project folder to save it, or enter another absolute folder
path using **Add by path**. Suggestions come from `CCDB_PROJECT_ROOTS`
(comma-separated) or the backend's default working directory. Folder discovery
only examines immediate children and offers up to 25 entries.

Favorites persist in the instance's settings database, separately for each guild
and user. Sharing operator access does not replace anyone's personal favorites.
Select a saved favorite in the management menu to remove it. Up to 25 favorites
are supported. Missing folders remain removable but cannot start a session.

## New session and Resume

**New session** shows separate **Favorite folders** and **Recent folders** lists,
with favorites taking precedence over duplicates. Recent folders are personal
and persist when you start or resume work through the launcher, newest first.
When both lists are empty it offers project folders. **Browse folders** opens
project-folder suggestions without typing, with a **Favorites + recent** button
to return. Selecting a folder creates a public thread bound to that absolute path,
joins the requester and configured shared thread members, and shows the folder
in its welcome message. The first human message starts the model through the
normal chat pipeline. Opening menus and creating threads make no model calls.

**Resume** offers accessible existing threads from the 25 most recently used
session records. It links to the original conversation, including archived
threads; it does not clone or overwrite a running session. Deleted, forbidden,
other-guild and other-computer-channel threads are excluded. The existing
`/resume` command keeps its separate behavior of resuming into a new thread.

## Access and operation

All interactions check the bot's configured operator list and channel list.
Personal menus only accept the user who opened them, and expire after five
minutes. Open a fresh menu from the persistent panel after expiry or restart.
Folder paths are checked again before creating a session. Resume checks thread
access again when selected. Private threads additionally require membership or
thread-management permissions.

The panel message ID is stored in the settings database. Reconnects edit it;
deleting the panel causes recreation on the next reconnect/startup. Pinning is
best effort, so missing pin permission does not prevent the panel being usable.
No credentials, Claude settings, model preferences or personal instruction files
are copied between computers. Linux automated tests do not constitute Windows
runtime verification.
