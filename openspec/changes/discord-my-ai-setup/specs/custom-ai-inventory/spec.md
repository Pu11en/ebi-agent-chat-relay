## Purpose

Give authorized Discord users a factual, safe, and understandable inventory of their custom AI
setup across harnesses and computers without turning the inventory into a configuration editor.

## ADDED Requirements

### Requirement: My AI Setup is available from Discord Settings
The system SHALL expose **My AI Setup** from the Discord Settings interaction for authorized users.
Opening it MUST show **Browse by kind** first, with **Where it lives** and **Compare computers** as
available sibling views.

#### Scenario: An authorized user opens My AI Setup
- **WHEN** the user chooses My AI Setup from Settings
- **THEN** Discord opens Browse by kind without starting a model session

#### Scenario: An unauthorized user attempts to open the view
- **WHEN** a user outside the bot's authorization or category policy invokes My AI Setup
- **THEN** no inventory data is shown and the interaction explains where authorized access belongs

### Requirement: The inventory covers every custom setup kind
The system SHALL inventory user-added or user-modified preferences, instructions, memory, skills,
tools, plugins, connectors, commands, hooks, and harness settings. Each item MUST have a stable kind
and identity so it can appear consistently in browse, search, comparison, and agent handoff views.

#### Scenario: Custom setup spans multiple kinds
- **WHEN** the configured sources contain a custom skill, instruction file, hook, and connector
- **THEN** Browse by kind includes each item under its corresponding kind

#### Scenario: An item is readable but unsupported by a harness
- **WHEN** a custom item exists but a selected harness cannot load it
- **THEN** the item remains inventoried and its harness availability is shown as unsupported

### Requirement: Built-in defaults are hidden unless requested
The default inventory SHALL show custom additions and overrides, not ordinary vendor or framework
built-ins. A user MUST be able to explicitly include built-ins, and those items MUST be labeled as
built-in rather than custom.

#### Scenario: Default browse opens
- **WHEN** My AI Setup opens with no built-in filter selected
- **THEN** unchanged built-in defaults are absent from the result

#### Scenario: The user requests built-ins
- **WHEN** the user enables the built-in filter
- **THEN** built-in items appear with a clear source classification and do not change the custom-item counts

### Requirement: Every item has factual provenance and scope
For each item, the system SHALL show its kind, source, effective scope, verified harness
availability, prerequisites, last change, and size or token estimate when measurable. Scope MUST
distinguish everywhere, shared Drew profile, one computer, David's profile, and one project. Unknown
or unmeasurable fields MUST be labeled unknown rather than inferred.

#### Scenario: A local item is fully measurable
- **WHEN** a local custom instruction has a readable source, modification time, byte size, and known harness loaders
- **THEN** its detail view shows those facts and identifies which harnesses were actually verified to load it

#### Scenario: Token size cannot be measured reliably
- **WHEN** no supported tokenizer or direct measurement is available
- **THEN** the item reports token size as unknown or explicitly estimated without presenting an invented exact count

#### Scenario: Source scope is project-specific
- **WHEN** an instruction exists inside one project's configuration boundary
- **THEN** its scope is shown as that project rather than everywhere or computer-wide

### Requirement: Inventory navigation is bounded and searchable
The system SHALL provide search, kind and scope filters, pagination within Discord limits, and a
Recent changes view. Search and filters MUST operate on inventory metadata without invoking a model.

#### Scenario: Search finds a custom item
- **WHEN** the user searches by an item's name, kind, harness, computer, or scope
- **THEN** matching items are returned in a bounded Discord view

#### Scenario: More items exist than one Discord component can show
- **WHEN** a result exceeds the platform's component or message limit
- **THEN** the view offers deterministic pages without silently dropping the remaining items

#### Scenario: Recent changes is selected
- **WHEN** the user opens Recent changes
- **THEN** items are ordered by known last-change time and items with unknown times are labeled separately

### Requirement: Where it lives explains scope without exposing Mega Global
The **Where it lives** view SHALL group custom items by their user-facing scopes and sources. The
internal Mega Global ownership model MUST NOT appear as a user-facing screen, label, or required
concept.

#### Scenario: The user opens Where it lives
- **WHEN** inventory items exist at global, computer, profile, and project scopes
- **THEN** the view groups them under understandable user-facing scope names

#### Scenario: An item is internally classified as Mega Global
- **WHEN** internal audit logic assigns that ownership class
- **THEN** the Discord inventory renders its appropriate user-facing scope and never displays "Mega Global"

### Requirement: Compare computers distinguishes parity from deliberate differences
The **Compare computers** view SHALL compare custom item identities and verified availability across
DrewAI, iMac, David, and other configured trusted computers. It MUST distinguish matching setup,
missing setup, deliberate machine or subscription exceptions, stale snapshots, and unreachable
computers.

#### Scenario: DrewAI and iMac match
- **WHEN** both computers report the same custom item identity and effective content fingerprint
- **THEN** the comparison shows the item as aligned

#### Scenario: A subscription-dependent setting differs deliberately
- **WHEN** a computer profile declares a known subscription or machine exception
- **THEN** the comparison shows a deliberate difference rather than an error

#### Scenario: A remote computer cannot be verified
- **WHEN** the remote inventory source is offline or has not refreshed
- **THEN** the view shows its last verification time and stale or unreachable state instead of claiming current parity

### Requirement: Secret material is never displayed or attached
The inventory MUST redact secret values, credentials, tokens, private keys, and sensitive connector
configuration from summaries, details, comparisons, logs, and Setup Agent context. It MAY identify
that a required secret is present or missing when that fact can be checked without revealing it.

#### Scenario: A config contains a token
- **WHEN** an inventory adapter reads a configuration source containing a token value
- **THEN** the item reports only safe metadata and no Discord message or agent context contains the token

#### Scenario: A connector requires credentials
- **WHEN** the adapter can safely determine whether required credentials are configured
- **THEN** prerequisites show configured or missing without exposing the credential value

### Requirement: Remote facts identify their verification source
Remote inventory data SHALL arrive through the trusted handoff boundary or a previously verified
snapshot. Every remote item MUST name its source computer and verification time, and cached data
MUST be labeled stale when its freshness policy expires.

#### Scenario: A live remote inventory is returned
- **WHEN** a trusted computer supplies a current inventory response
- **THEN** the comparison records that computer and verification time as the source

#### Scenario: Only a cached remote snapshot exists
- **WHEN** the remote computer is unavailable and a prior snapshot exists
- **THEN** the user can view the snapshot with an explicit stale label and cannot mistake it for live state

### Requirement: Ask Setup Agent creates a scoped management session
An item detail SHALL offer **Ask Setup Agent**. Choosing it MUST create a normal management session
scoped to the selected item and attach only safe item metadata, its source locator, and the user's
question. It MUST NOT edit configuration merely because the button was pressed.

#### Scenario: The user asks about an item
- **WHEN** an authorized user chooses Ask Setup Agent for a custom skill
- **THEN** a session opens with that skill's safe identity, source, scope, availability, and the user's request

#### Scenario: The selected item refers to a secret-bearing source
- **WHEN** Ask Setup Agent is used for an item whose underlying file contains credentials
- **THEN** the session receives a redacted locator and safe metadata, not the secret values

#### Scenario: The user requests a change in the new session
- **WHEN** the management session proposes editing, disabling, moving, or removing an item
- **THEN** the ordinary task authority, project rules, verification, and approval boundaries apply before any change

### Requirement: Inventory interactions are read-only
Browse, search, filter, comparison, and item-detail controls MUST NOT directly edit, disable,
delete, move, install, or activate AI configuration. The system MUST preserve existing setup when an
adapter fails or an item cannot be parsed.

#### Scenario: An adapter encounters malformed configuration
- **WHEN** one custom source cannot be parsed safely
- **THEN** the inventory shows an item-level error or unavailable source while continuing to show other sources unchanged

#### Scenario: A user views an item
- **WHEN** any inventory-only interaction completes
- **THEN** the source configuration and its activation state are unchanged
