# folder-session-launcher Specification

## Purpose
Give each computer's control channel a pinned, computer-labelled launcher so a person can start or resume a Claude session in a known project folder from Discord alone — favourite folders, a new-session shortcut and resume — without typing paths or remembering commands.

## Requirements

### Requirement: Computer-specific shortcuts
The bot SHALL provide a persistent computer-labelled panel with Favorite folders, New session, and Resume buttons.

#### Scenario: Reconnect
- **WHEN** the bot reconnects
- **THEN** the same panel is updated and its buttons remain usable

### Requirement: Personal favorites
Favorites SHALL persist per user and guild in the local instance, and unrelated users SHALL NOT operate another user's ephemeral menu.

#### Scenario: Shared operators
- **WHEN** two authorized operators save different folders
- **THEN** each sees their own saved favorites

### Requirement: Folder-bound new threads
New session SHALL persist the chosen working directory before telling the requester to send a task, and SHALL NOT launch a model itself.

#### Scenario: Folder disappears
- **WHEN** a selected folder no longer exists
- **THEN** no thread is created and the requester sees an error

### Requirement: Resume existing conversations
Resume SHALL list accessible existing threads for this bot's configured channels and reopen their original conversation without changing the saved session.

#### Scenario: Deleted thread
- **WHEN** Discord reports a saved thread missing
- **THEN** it is excluded from the resume menu

### Requirement: Category-local favorites and recents
The launcher SHALL use its configured computer channel without asking the operator to choose a computer. New session SHALL show personal favorites and recent launcher folders, deduplicated, and offer button-driven browsing.

#### Scenario: Favorite was used recently
- **WHEN** an operator opens New session after using a favorite and another folder
- **THEN** the favorite appears in Favorites and only the other folder appears in Recent folders

#### Scenario: Another project
- **WHEN** an operator presses Browse folders
- **THEN** project-folder suggestions appear without a text-entry modal

### Requirement: Navigate before starting
Folder selection SHALL show the absolute current folder and allow child, parent,
home and filesystem-root navigation, including pagination, without starting a model.
Only Start here SHALL create a folder-bound thread in the configured workers channel.

#### Scenario: Folder beyond the projects directory
An authorized operator navigates Up from projects, opens another directory and
starts there. The saved working directory is that selected absolute folder.

### Requirement: Fixed launcher and category boundary
A configured launcher home SHALL remain separate from worker thread creation.
Optional category scope SHALL reject foreign-category commands/autocomplete and
ignore normal framework chat there, including for administrators.

#### Scenario: An administrator chooses the wrong bot
The bot responds privately that it belongs to another category and performs no
command action. Its native slash entry may remain visible due to Discord rules.

### Requirement: Keep control-center controls reachable
The user’s existing control-center SHALL remain the entry point. The pinned
anchor SHALL remain in place and a silent bottom shortcut SHALL be refreshed
after channel activity, coalesced across a ten-second window. Only the saved
bot-owned previous shortcut may be removed; other messages and threads remain.

#### Scenario: Channel activity
- **WHEN** several messages arrive in the control-center within ten seconds
- **THEN** one silent shortcut is posted at the bottom, only the bot's previous shortcut is deleted, and the pinned anchor and every other message stay
