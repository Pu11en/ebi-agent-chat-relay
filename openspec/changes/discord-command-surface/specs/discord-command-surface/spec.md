## Purpose

Provide a small, location-aware Discord control surface that keeps common computer and session
actions easy to reach while preserving advanced behavior behind focused menus.

## ADDED Requirements

### Requirement: Bottom control-center controls stay current
The system SHALL maintain one current control message near the bottom of each configured control
center. The message SHALL show the computer's current status above buttons for **New session**,
**Sessions**, and **Settings**. When control-center activity would bury the controls, the system
SHALL publish a replacement and remove only the previous bot-owned control message.

#### Scenario: Human activity moves the controls down
- **WHEN** a normal message is posted after the current control message
- **THEN** the system publishes one replacement control message after a short debounce
- **AND** deletes the previous bot-owned control message without deleting channel history

#### Scenario: The control message does not refresh itself
- **WHEN** the bot publishes or updates the current control message
- **THEN** that bot-authored activity does not schedule another replacement

#### Scenario: The bot restarts
- **WHEN** the bot reconnects to a configured control center
- **THEN** it restores or replaces the saved control message so exactly one current control row is
  active

### Requirement: New session offers focused folder choices
The **New session** button and `/new` command SHALL open the same user-specific flow with
**Favorites**, **Recent**, **Browse**, **Create**, and **Clone** choices. A successful choice SHALL
create a clean, idle session thread bound to the chosen folder and the computer's default model;
the model SHALL not start until the user supplies a task.

#### Scenario: Start from a favorite or recent folder
- **WHEN** an authorized user chooses an available favorite or recent folder
- **THEN** the system creates an idle thread bound to that folder
- **AND** records the folder as most recently used

#### Scenario: Browse to an existing folder
- **WHEN** an authorized user browses to an allowed local folder and chooses **Start here**
- **THEN** the system creates an idle thread bound to that folder

#### Scenario: Create a project folder
- **WHEN** an authorized user supplies a valid new folder name beneath an approved project root
- **THEN** the system creates the folder and an idle session bound to it
- **AND** refuses traversal, absolute-path escape, collision, or creation outside approved roots

#### Scenario: Clone a project
- **WHEN** an authorized user supplies a supported repository location and a valid destination
  beneath an approved project root
- **THEN** the system clones into that destination and creates an idle session bound to it
- **AND** reports a clone failure without leaving a usable half-configured session

#### Scenario: No task has been supplied yet
- **WHEN** the new thread is created from any New session choice
- **THEN** the system shows the bound folder and default model
- **AND** does not spend a model turn until the user sends the first task

### Requirement: Sessions provides one place to find and act on work
The **Sessions** button and `/sessions` command SHALL show accessible sessions newest first, support
text search, and offer **Open**, **New in same folder**, and **Close** for a selected session.

#### Scenario: List sessions
- **WHEN** an authorized user opens Sessions without a query
- **THEN** the system lists the newest accessible sessions first
- **AND** excludes sessions the user cannot view or that belong to another configured computer

#### Scenario: Search sessions
- **WHEN** the user searches by a word present in a session title, summary, or folder
- **THEN** matching accessible sessions are returned newest first

#### Scenario: Open a session
- **WHEN** the user chooses **Open**
- **THEN** the original thread is opened, including by unarchiving it when necessary
- **AND** the existing conversation remains available to continue

#### Scenario: Start another session in the same folder
- **WHEN** the user chooses **New in same folder**
- **THEN** the system creates a clean idle thread bound to the selected session's folder
- **AND** does not copy or alter the selected conversation

#### Scenario: Close from Sessions
- **WHEN** the user chooses **Close** for a session
- **THEN** the shared close lifecycle is used rather than deleting the session mapping

### Requirement: Settings opens the computer's supported configuration
The **Settings** button and `/settings` command SHALL open the same settings entry point and SHALL
show only settings supported by that computer's configured harnesses and subscription profile.

#### Scenario: Open Settings
- **WHEN** an authorized user chooses Settings in the control center
- **THEN** the system presents that computer's supported settings without starting a model turn

#### Scenario: A feature is unsupported on this computer
- **WHEN** a configured harness or subscription does not support a setting
- **THEN** the setting is hidden or clearly unavailable rather than presented as a working choice

### Requirement: The supported command surface is location aware
The final supported command set SHALL be `/new`, `/sessions`, `/settings`, and `/help` in a control
center, and `/switch`, `/stop`, `/session`, `/close`, and `/help` in a session thread. Commands
invoked from an unsupported location SHALL make no state change and direct the user to the correct
location. `/help` SHALL list only the commands and buttons supported where it is invoked.

#### Scenario: Help in a control center
- **WHEN** a user invokes `/help` in a configured control center
- **THEN** the response describes New session, Sessions, Settings, and the four control-center
  commands

#### Scenario: Help in a session thread
- **WHEN** a user invokes `/help` in a managed session thread
- **THEN** the response describes Switch, Stop, Session, Close, and the five session commands

#### Scenario: A session-only command is used in the control center
- **WHEN** a user invokes `/switch`, `/stop`, `/session`, or `/close` outside a managed session
- **THEN** the system makes no session change and tells the user to use it inside a session thread

#### Scenario: A control-only command is used in a session
- **WHEN** a user invokes `/new`, `/sessions`, or `/settings` inside a managed session thread
- **THEN** the system makes no control-center change and directs the user to the control center

### Requirement: Switch selects a model directly
`/switch` SHALL make model selection the first and only required choice, mark the current model,
put currently supported newer models ahead of legacy choices, support typed search beyond Discord's
display limit, and resolve the selected model's harness automatically.

#### Scenario: Open the model chooser
- **WHEN** a user starts `/switch` in a managed session
- **THEN** model choices are available immediately without first choosing a backend
- **AND** the current model is visibly marked

#### Scenario: Search for a model not in the initial choices
- **WHEN** the user types part of a supported model identifier
- **THEN** matching models can be selected even if they were not in the initial Discord choices

#### Scenario: Select a model on another harness
- **WHEN** the user selects a supported model owned by a different configured harness
- **THEN** the system selects both the model and its harness for that thread
- **AND** preserves file-backed context according to the existing cross-harness handoff behavior

#### Scenario: Model is unavailable on this computer
- **WHEN** a model is known globally but unsupported by the current computer's configuration
- **THEN** it is not offered as a selectable model

### Requirement: Session actions are consolidated
`/session` SHALL open one session-management view with **Fork**, **Rewind**, **Compact**, **Clear**,
**Context**, and **Goal**. Each action SHALL reuse the same underlying operation as any other entry
point and SHALL explain destructive effects before applying them.

#### Scenario: Fork
- **WHEN** the user chooses Fork
- **THEN** the system creates a separate thread that continues from the selected conversation
- **AND** leaves the original thread and working files unchanged

#### Scenario: Rewind
- **WHEN** the user selects an earlier valid turn through Rewind
- **THEN** later conversation context is removed from the resumable session
- **AND** working files are preserved

#### Scenario: Compact
- **WHEN** the user chooses Compact while no model turn is active
- **THEN** the session requests conversation compaction and reports completion or failure

#### Scenario: Clear
- **WHEN** the user confirms Clear
- **THEN** the session conversation identity is reset while its thread and bound folder remain

#### Scenario: Context
- **WHEN** the user chooses Context
- **THEN** the system reports the current session's available context information without changing
  the session

#### Scenario: Goal
- **WHEN** the user chooses Goal
- **THEN** the user can view, set, or clear that session's completion condition

### Requirement: Superseded commands are retired only after replacement acceptance
The system MUST keep existing command entry points available during local replacement testing and
MUST remove them from registration only after replacement tests pass and the operator accepts the
Discord try-out. Removal SHALL not delete stored sessions, settings, folders, or history.

#### Scenario: Replacement is not yet accepted
- **WHEN** a replacement flow has not passed its local checks and operator try-out
- **THEN** the superseded command remains available

#### Scenario: Replacement is accepted
- **WHEN** all replacement flows pass local checks and the operator accepts the Discord try-out
- **THEN** superseded command registrations are removed in a separate retirement step
- **AND** existing stored state remains usable through the replacement surface
