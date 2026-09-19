## ADDED Requirements

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
